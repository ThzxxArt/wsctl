"""Terminal screen snapshots for protocol v2.

A byte replay cannot reconstruct a full-screen application: it draws with
cursor-addressed overwrites on top of state the stream may no longer carry
(shed frames, evicted scrollback head). The application itself holds the only
complete model, but asking every TUI to repaint is not always possible (a
detached tmux pane can be captured directly) and is expensive.

Three providers, chosen per session:

``tmux``
    ``tmux capture-pane -e -p``: exact, cheap, and already ANSI.
``pyte``
    A shadow terminal kept in sync with the output stream. This is the default
    for local sessions; ``auto`` only maintains it once an alternate screen is
    entered, so a plain shell pays almost nothing.
``raw``
    The last resort: replay the ring and let the client's emulator rebuild.
    It is honest about its limits (``snapshot_capable=false``).

A provider that cannot faithfully capture a screen (Sixel, Kitty graphics,
unknown private sequences) reports ``capable=False`` so the server can fall
back to raw replay plus the application repaint nudge instead of pretending.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger("wsctl.snapshot")

ALT_SCREEN_ON = re.compile(rb"\x1b\[\?(?:1049|1047|47)h")
ALT_SCREEN_OFF = re.compile(rb"\x1b\[\?(?:1049|1047|47)l")
#: Sequences we cannot serialise faithfully. Seeing one marks the shadow
#: screen as non-authoritative: the caller must fall back rather than paint a
#: screen that is missing graphics.
UNSUPPORTED = (
    re.compile(rb"\x1bP.*?\x1b\\", re.DOTALL),  # DCS, including Sixel
    re.compile(rb"\x1b_G"),  # Kitty graphics
)

_DEFAULT_FG = {
    "black": 30,
    "red": 31,
    "green": 32,
    "brown": 33,
    "yellow": 33,
    "blue": 34,
    "magenta": 35,
    "cyan": 36,
    "white": 37,
}
_BRIGHT_FG = {
    "black": 90,
    "red": 91,
    "green": 92,
    "brown": 93,
    "yellow": 93,
    "blue": 94,
    "magenta": 95,
    "cyan": 96,
    "white": 97,
}
_DEFAULT_BG = {name: code + 10 for name, code in _DEFAULT_FG.items()}
_BRIGHT_BG = {name: code + 10 for name, code in _BRIGHT_FG.items()}


@dataclass(frozen=True, slots=True)
class Snapshot:
    """An ANSI screen snapshot starting at output offset ``seq``."""

    seq: int
    data: bytes
    capable: bool
    source: str
    created_at: float = 0.0

    def __post_init__(self) -> None:
        if self.created_at == 0.0:
            object.__setattr__(self, "created_at", time.time())


@runtime_checkable
class SnapshotProvider(Protocol):
    name: str
    capable: bool

    def feed(self, data: bytes) -> None: ...

    async def capture(self, seq: int) -> Snapshot | None: ...


class RawSnapshot:
    """The fallback provider: no shadow screen, replay only."""

    name = "raw"
    capable = False

    def __init__(self, scrollback: Any) -> None:
        self._scrollback = scrollback

    def feed(self, data: bytes) -> None:
        return None

    async def capture(self, seq: int) -> Snapshot | None:
        payload = self._scrollback.snapshot()
        if not payload:
            return None
        return Snapshot(seq=seq, data=payload, capable=False, source="raw")


class PyteSnapshot:
    """A pyte-backed shadow terminal.

    ``auto`` starts the shadow screen only when an alternate-screen sequence
    is observed. A side benefit is that ordinary line-oriented shells -- whose
    screen can be reconstructed from the ring just fine -- never pay for a
    full terminal emulator.
    """

    name = "pyte"

    def __init__(self, cols: int, rows: int, *, mode: str = "auto") -> None:
        import pyte

        self._cols = max(1, min(int(cols), 1000))
        self._rows = max(1, min(int(rows), 1000))
        self._mode = mode
        self._screen = pyte.Screen(self._cols, self._rows)
        self._stream = pyte.Stream(self._screen)
        self._active = mode == "always"
        self.capable = True
        self._scan_tail = b""

    # -- feeding -------------------------------------------------------

    def feed(self, data: bytes) -> None:
        if not data:
            return
        if self._mode == "auto":
            window = self._scan_tail + data
            if ALT_SCREEN_ON.search(window):
                self._active = True
            elif ALT_SCREEN_OFF.search(window):
                # Leave the shadow active: after alt-screen exit the normal
                # screen model is still useful, and re-entering must not lose
                # the parser state. ``feed`` below is idempotent either way.
                self._active = True
            self._scan_tail = window[-32:]
        if not self._active:
            return
        for pattern in UNSUPPORTED:
            if pattern.search(data):
                # Graphics cannot be represented in pyte. Mark the snapshot
                # non-authoritative so the caller never promises a screen it
                # cannot paint; keep feeding the text parts for cursor state.
                self.capable = False
                break
        if self.capable is False and self._mode == "auto":
            # Still feed: if the graphics sequence is one-off, a later text
            # screen may become authoritative again. ``capable`` stays false
            # for this snapshot; the session may retry after a repaint.
            pass
        try:
            self._stream.feed(data.decode("utf-8", "replace"))
        except Exception:  # pragma: no cover - pyte is defensive itself
            log.debug("pyte feed failed", exc_info=True)
            self.capable = False

    # -- capture -------------------------------------------------------

    def resize(self, cols: int, rows: int) -> None:
        cols = max(1, min(int(cols), 1000))
        rows = max(1, min(int(rows), 1000))
        if cols == self._cols and rows == self._rows:
            return
        self._cols, self._rows = cols, rows
        self._screen.resize(rows, cols)

    async def capture(self, seq: int) -> Snapshot | None:
        if not self._active:
            return None
        # Serialising a 1000×1000 screen is millions of Python-level cells. It
        # is CPU work, and doing it on the event loop stalls every other
        # terminal for the duration. The caller holds the session lock, so the
        # screen is frozen while the worker renders it.
        data = await asyncio.to_thread(self.serialize)
        return Snapshot(
            seq=seq,
            data=data,
            capable=self.capable,
            source="pyte",
        )

    def serialize(self) -> bytes:
        """Render the shadow screen as an ANSI full-screen repaint."""
        if not self.capable:
            # Returning an empty string would clear the client's screen; that
            # is worse than letting the caller fall back to raw replay.
            return b""
        out: list[str] = ["\x1b[0m\x1b[2J"]
        buffer = self._screen.buffer
        for y in range(self._rows):
            out.append(f"\x1b[{y + 1};1H")
            current: tuple[Any, ...] | None = None
            row = buffer[y]
            for x in range(self._cols):
                char = row[x]
                attrs = (
                    char.fg,
                    char.bg,
                    char.bold,
                    char.italics,
                    char.underscore,
                    char.reverse,
                    char.strikethrough,
                )
                if attrs != current:
                    out.append(_sgr(attrs))
                    current = attrs
                out.append(char.data or " ")
            out.append("\x1b[0m")
        cursor = self._screen.cursor
        out.append(f"\x1b[{cursor.y + 1};{cursor.x + 1}H")
        return "".join(out).encode("utf-8")


def _sgr(attrs: tuple[Any, ...]) -> str:
    fg, bg, bold, italics, underscore, reverse, strike = attrs
    codes: list[str] = ["0"]
    if bold:
        codes.append("1")
    if italics:
        codes.append("3")
    if underscore:
        codes.append("4")
    if reverse:
        codes.append("7")
    if strike:
        codes.append("9")
    fg_code = _color_code(fg, background=False)
    if fg_code is not None:
        codes.append(fg_code)
    bg_code = _color_code(bg, background=True)
    if bg_code is not None:
        codes.append(bg_code)
    return "\x1b[" + ";".join(codes) + "m"


def _color_code(value: Any, *, background: bool) -> str | None:
    if value is None or value == "default":
        return None
    if isinstance(value, int):
        # pyte uses ints for 256-colour cells.
        return f"{48 if background else 38};5;{value}"
    table = (_DEFAULT_BG if background else _DEFAULT_FG)
    bright = (_BRIGHT_BG if background else _BRIGHT_FG)
    name = str(value)
    if name in table:
        return str(table[name])
    if name in bright:
        return str(bright[name])
    if name.startswith("bright") and name[6:] in bright:
        return str(bright[name[6:]])
    return None


class TmuxSnapshot:
    """Capture a tmux pane directly: exact and independent of our ring."""

    name = "tmux"
    capable = True

    def __init__(self, target: str) -> None:
        self._target = target

    def feed(self, data: bytes) -> None:
        return None

    async def capture(self, seq: int) -> Snapshot | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "tmux",
                "capture-pane",
                "-e",
                "-p",
                "-t",
                self._target,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                return None
        except (OSError, ValueError):
            return None
        if proc.returncode != 0 or not out:
            return None
        # ``capture-pane`` output has no clear-screen prefix and sets no
        # cursor; wrap it so applying the snapshot replaces the screen rather
        # than appending to whatever the client already had.
        data = b"\x1b[0m\x1b[2J\x1b[H" + out.replace(b"\n", b"\r\n")
        return Snapshot(seq=seq, data=data, capable=True, source="tmux")


def provider_for(
    *,
    mode: str,
    cols: int,
    rows: int,
    scrollback: Any,
    tmux_target: str | None = None,
) -> SnapshotProvider:
    """Build the provider a session should use.

    ``mode`` is the operator's choice: ``auto``/``always`` prefer tmux then
    pyte, ``off`` uses raw. A tmux-backed session always prefers tmux because
    it survives our process and can be captured even before we see any output.
    """
    if tmux_target:
        return TmuxSnapshot(tmux_target)
    if mode == "off":
        return RawSnapshot(scrollback)
    try:
        import pyte  # noqa: F401
    except ImportError:  # pragma: no cover - dependency is declared
        return RawSnapshot(scrollback)
    return PyteSnapshot(cols, rows, mode="always" if mode == "always" else "auto")


__all__ = [
    "PyteSnapshot",
    "RawSnapshot",
    "Snapshot",
    "SnapshotProvider",
    "TmuxSnapshot",
    "provider_for",
]
