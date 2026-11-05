"""Shell Integration: parse OSC 133 / OSC 633 into command blocks.

Shells emit semantic markers inside the terminal stream:

* OSC 133 ``A`` -- prompt starts
* OSC 133 ``B`` -- command input starts
* OSC 133 ``C`` -- command executed
* OSC 133 ``D;<exit>`` -- command finished
* OSC 633 ``E;<command>`` -- the command line (VS Code compatible)
* OSC 633 ``P;Cwd=<path>`` -- current working directory

The parser is streaming: a PTY read may end in the middle of an OSC sequence,
so the incomplete tail is kept and resumed on the next read. Parsing never
modifies the byte stream -- the terminal receives exactly what the application
wrote.
"""

from __future__ import annotations

import base64
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

BEL = 0x07
ESC = 0x1B
OSC_INTRODUCER = b"\x1b]"
ST = b"\x1b\\"
MAX_OSC = 64 * 1024
MAX_BLOCKS = 500


@dataclass
class CommandBlock:
    id: str
    session: str
    command: str = ""
    cwd: str | None = None
    started_at: float | None = None
    ended_at: float | None = None
    duration_ms: int | None = None
    exit_code: int | None = None
    status: str = "running"
    output_seq: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Pending:
    command: str = ""
    cwd: str | None = None
    started_at: float | None = None
    output_seq: int | None = None


class CommandBlockParser:
    """Incremental OSC 133/633 parser.

    ``on_event`` is called with plain dictionaries so the session can queue
    them as JSON control messages without importing this module's types.

    Invalid or oversized OSC payloads are ignored: a malformed shell prompt
    must never break the terminal stream. The parser is bounded (``MAX_OSC``),
    so a hostile application cannot grow the buffer without limit.
    """

    def __init__(
        self,
        session_id: str,
        *,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        clock: Callable[[], float] = time.time,
        max_blocks: int = MAX_BLOCKS,
    ) -> None:
        self.session_id = session_id
        self._on_event = on_event
        self._clock = clock
        self._max_blocks = max_blocks
        self._buffer = bytearray()
        self._pending = _Pending()
        self.blocks: list[CommandBlock] = []
        self.enabled = False

    # -- stream --------------------------------------------------------

    def feed(self, data: bytes, *, seq: int | None = None) -> list[dict[str, Any]]:
        """Feed output bytes; return the events produced by this chunk."""
        if not data:
            return []
        self._buffer.extend(data)
        if len(self._buffer) > MAX_OSC:
            # Keep the tail; an OSC that long is not a command marker.
            del self._buffer[:-MAX_OSC]
        events: list[dict[str, Any]] = []
        while True:
            start = self._buffer.find(OSC_INTRODUCER)
            if start < 0:
                # Keep one byte: an introducer can be split across reads.
                if len(self._buffer) > 1:
                    del self._buffer[:-1]
                break
            if start > 0:
                del self._buffer[:start]
            end, consumed = _find_terminator(self._buffer)
            if end < 0:
                break  # incomplete; wait for the next read
            payload = bytes(self._buffer[2:end])
            del self._buffer[:consumed]
            event = self._handle(payload, seq=seq)
            if event is not None:
                events.append(event)
                if self._on_event is not None:
                    self._on_event(event)
        # The list is the public state; trim to bound long-running shells.
        if len(self.blocks) > self._max_blocks:
            del self.blocks[: len(self.blocks) - self._max_blocks]
        return events

    # -- OSC dispatch --------------------------------------------------

    def _handle(self, payload: bytes, *, seq: int | None) -> dict[str, Any] | None:
        try:
            text = payload.decode("utf-8", "replace")
        except Exception:  # pragma: no cover - decode with replace cannot raise
            return None
        if text.startswith("133;"):
            return self._handle_133(text[4:], seq)
        if text.startswith("633;"):
            return self._handle_633(text[4:], seq)
        return None

    def _handle_133(self, body: str, seq: int | None) -> dict[str, Any] | None:
        kind, _, rest = body.partition(";")
        if kind == "A":
            self.enabled = True
            self._pending = _Pending(
                cwd=self._pending.cwd, started_at=None, output_seq=None
            )
            return self._emit_prompt("prompt_start")
        if kind == "B":
            self.enabled = True
            self._pending.command = ""
            self._pending.output_seq = seq
            return self._emit_prompt("command_start")
        if kind == "C":
            self.enabled = True
            if self._pending.started_at is None:
                self._pending.started_at = self._clock()
            if self._pending.output_seq is None:
                self._pending.output_seq = seq
            return self._emit_running()
        if kind == "D":
            self.enabled = True
            exit_code: int | None = None
            if rest.strip():
                try:
                    exit_code = int(rest.strip())
                except ValueError:
                    exit_code = None
            return self._finish(exit_code)
        if kind == "P" and "=" in rest:
            key, _, value = rest.partition("=")
            if key.lower() in {"cwd", "dir"} and value:
                self._pending.cwd = value
            return None
        return None

    def _handle_633(self, body: str, seq: int | None) -> dict[str, Any] | None:
        kind, _, rest = body.partition(";")
        if kind == "E":
            self.enabled = True
            command = rest
            # VS Code encodes non-ASCII commands as base64 with a marker.
            if command.startswith("b64:"):
                try:
                    command = base64.b64decode(command[4:]).decode("utf-8", "replace")
                except Exception:
                    command = rest
            self._pending.command = command
            return None
        if kind == "P":
            key, _, value = rest.partition("=")
            if key.lower() in {"cwd", "dir"} and value:
                self._pending.cwd = value
            return None
        if kind == "A":
            self.enabled = True
            return self._emit_prompt("prompt_start")
        if kind == "B":
            self.enabled = True
            return self._emit_prompt("command_start")
        if kind == "C":
            self.enabled = True
            if self._pending.started_at is None:
                self._pending.started_at = self._clock()
            if self._pending.output_seq is None:
                self._pending.output_seq = seq
            return self._emit_running()
        if kind == "D":
            self.enabled = True
            exit_code = None
            if rest.strip():
                try:
                    exit_code = int(rest.strip())
                except ValueError:
                    exit_code = None
            return self._finish(exit_code)
        return None

    # -- events --------------------------------------------------------

    def _emit_prompt(self, phase: str) -> dict[str, Any]:
        return {
            "type": "command",
            "phase": phase,
            "session": self.session_id,
            "cwd": self._pending.cwd,
        }

    def _emit_running(self) -> dict[str, Any]:
        return {
            "type": "command",
            "phase": "running",
            "session": self.session_id,
            "command": self._pending.command,
            "cwd": self._pending.cwd,
            "started_at": self._pending.started_at,
        }

    def _finish(self, exit_code: int | None) -> dict[str, Any]:
        now = self._clock()
        started = self._pending.started_at
        block = CommandBlock(
            id="cmd_" + uuid.uuid4().hex[:20],
            session=self.session_id,
            command=self._pending.command,
            cwd=self._pending.cwd,
            started_at=started,
            ended_at=now,
            duration_ms=int((now - started) * 1000) if started is not None else None,
            exit_code=exit_code,
            status=(
                "running"
                if exit_code is None
                else ("ok" if exit_code == 0 else "failed")
            ),
            output_seq=self._pending.output_seq,
        )
        self.blocks.append(block)
        self._pending = _Pending(cwd=self._pending.cwd)
        return {"type": "command", "phase": "finished", **block.to_dict()}

    # -- introspection -------------------------------------------------

    def snapshot(self, limit: int = 100) -> list[dict[str, Any]]:
        return [b.to_dict() for b in self.blocks[-limit:]]


def _find_terminator(buffer: bytearray) -> tuple[int, int]:
    """Find the end of the OSC payload starting at offset 0.

    Returns ``(payload_end, consumed_bytes)`` or ``(-1, 0)`` when incomplete.
    Both BEL and ST terminate; ST wins when it appears first.
    """
    bel = buffer.find(bytes([BEL]), 2)
    st = buffer.find(ST, 2)
    if bel < 0 and st < 0:
        return -1, 0
    if st >= 0 and (bel < 0 or st < bel):
        return st, st + 2
    return bel, bel + 1


__all__ = ["CommandBlock", "CommandBlockParser"]
