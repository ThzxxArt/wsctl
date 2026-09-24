"""Where ANSI escape sequences begin and end.

Dropping terminal bytes is now how a slow viewer is kept alive (see
``server.client``). Dropping them *arbitrarily*, though, tears a colour or
cursor sequence in half: the remaining stream then starts mid-escape and a
full-screen application -- ``vi``, ``htop``, ``less`` -- renders as garbage
until its next full repaint. The fix is not to stop dropping; it is to drop on
sequence boundaries, and this module is what decides where those are.

Only what eviction needs is parsed: enough to tell "does this buffer stop in
the middle of a sequence" and "where does the first complete sequence end".
Anything else would be a terminal emulator in the shedding path.
"""

from __future__ import annotations

ESC = 0x1B
CSI_FINAL_MIN = 0x40  # '@' .. '~'
CSI_FINAL_MAX = 0x7E
BEL = 0x07
#: Intermediates that make the sequence three bytes long: the final byte
#: after these is still outstanding (``ESC ( B``).
_SHORT_INTERMEDIATE = frozenset({0x28, 0x29, 0x2A, 0x2B, 0x23, 0x25})


def _scan_csi(buf: bytes, start: int) -> int | None:
    """Index just past a CSI sequence starting at ``start`` (``ESC [``).

    ``None`` means the buffer ends inside it.
    """
    i = start + 2  # skip ESC [
    n = len(buf)
    while i < n and not (CSI_FINAL_MIN <= buf[i] <= CSI_FINAL_MAX):
        i += 1
    return None if i >= n else i + 1


def _scan_osc(buf: bytes, start: int) -> int | None:
    """Index just past an OSC sequence (``ESC ]`` … BEL or ESC backslash)."""
    i = start + 2
    n = len(buf)
    while i < n:
        if buf[i] == BEL:
            return i + 1
        if buf[i] == ESC and i + 1 < n and buf[i + 1] == 0x5C:  # ST
            return i + 2
        i += 1
    return None


def _scan_short(buf: bytes, start: int) -> int | None:
    """Index just past the two/three-byte forms (``ESC ( B``, ``ESC c``, ...)."""
    n = len(buf)
    i = start + 1
    if i >= n:
        return None
    if buf[i] in (0x28, 0x29, 0x2A, 0x2B, 0x23, 0x25):  # ( ) * + # %
        return None if i + 1 >= n else i + 2
    return i + 1


def sequence_end(buf: bytes, start: int) -> int | None:
    """Index just past the escape sequence beginning at ``start``.

    ``None`` when the buffer ends inside the sequence.
    """
    if start >= len(buf) or buf[start] != ESC:
        return start + 1
    if start + 1 >= len(buf):
        return None
    kind = buf[start + 1]
    if kind == 0x5B:  # '['
        return _scan_csi(buf, start)
    if kind == 0x5D:  # ']'
        return _scan_osc(buf, start)
    return _scan_short(buf, start)


def ends_inside_escape(buf: bytes) -> bool:
    """Does ``buf`` stop in the middle of an ANSI escape sequence?

    A frame that does must not be the last thing kept when shedding: whatever
    follows would be read as the tail of a colour or cursor sequence.
    """
    i = 0
    n = len(buf)
    while i < n:
        if buf[i] != ESC:
            i += 1
            continue
        end = sequence_end(buf, i)
        if end is None:
            return True
        i = end
    return False


def skip_partial_prefix(buf: bytes) -> bytes:
    """Drop a *partial* leading escape sequence, if there is one.

    A complete leading sequence is kept -- there is nothing wrong with it.
    Used when re-anchoring a stream after the bytes before it were discarded.
    """
    if not buf or buf[0] != ESC:
        return buf
    end = sequence_end(buf, 0)
    # ``None`` means the buffer is entirely the head of one sequence that never
    # completes here: there is nothing after it to keep.
    return b"" if end is None else buf


class AnsiTracker:
    """Streaming "am I inside an escape sequence?" state.

    :func:`ends_inside_escape` answers that question for a buffer that *starts
    at a sequence boundary*. Terminal frames do not: a PTY read of 64 KiB cuts
    sequences wherever it likes, so frame two may begin with ``1m`` -- the tail
    of ``ESC[31m``. Scanning it standalone says "plain text, safe to cut here",
    which is exactly wrong.

    Feed frames in order and this tracks the real state. Dropping is safe at a
    frame that *leaves the state outside* a sequence; anything else would hand
    the terminal a stream that resumes mid-escape, and a full-screen program
    renders as garbage until its next repaint.
    """

    __slots__ = ("_state",)

    def __init__(self) -> None:
        #: ``None`` when between sequences; otherwise the kind we are inside.
        self._state: str | None = None

    @property
    def inside(self) -> bool:
        return self._state is not None

    @property
    def state(self) -> str | None:
        """The streaming state after the bytes fed so far (``None`` = outside)."""
        return self._state

    def resume(self, state: str | None) -> None:
        """Adopt a previously recorded state.

        Used when a buffer's head was chosen as the start of a replay and the
        bytes *before* it are only known as "left the parser inside kind X" --
        re-anchoring that head needs to continue the same sequence, not start
        a fresh scan that would read its remainder as text.
        """
        self._state = state

    def skip_to_boundary(self, buf: bytes) -> bytes:
        """Drop the leading bytes that complete an in-progress sequence.

        ``self._state`` must be the state *before* ``buf`` (see :meth:`resume`).
        A head that resumes mid-escape -- the remainder of a colour or cursor
        sequence whose first half was evicted -- is exactly what must not be
        replayed as text, so those bytes go and whatever follows is returned.
        """
        i = 0
        n = len(buf)
        while i < n and self._state is not None:
            self.feed(buf[i : i + 1])
            i += 1
        return buf[i:]

    def feed(self, buf: bytes) -> bool:
        """Consume ``buf``; return whether the stream now ends inside a sequence."""
        i = 0
        n = len(buf)
        while i < n:
            if self._state is None:
                if buf[i] != ESC:
                    i += 1
                    continue
                if i + 1 >= n:
                    self._state = "esc"
                    return True
                kind = buf[i + 1]
                if kind == 0x5B:
                    self._state = "csi"
                    i += 2
                elif kind == 0x5D:
                    self._state = "osc"
                    i += 2
                elif kind in _SHORT_INTERMEDIATE:
                    # `ESC ( B`: the intermediate needs exactly one final byte.
                    self._state = "short"
                    i += 2
                else:
                    # `ESC c` and friends are complete at two bytes. Waiting
                    # for a third here is what broke chunking invariance: the
                    # same stream split differently reached different states.
                    self._state = None
                    i += 2
                continue

            if self._state == "esc":
                # A bare ESC closed the previous buffer; *this* byte is its
                # kind and must go through the same dispatch. Resetting to
                # ``None`` instead silently ate the ``]`` of a split ``ESC ]``,
                # so an OSC opener split across two frames was never seen.
                kind = buf[i]
                if kind == 0x5B:
                    self._state = "csi"
                elif kind == 0x5D:
                    self._state = "osc"
                elif kind in _SHORT_INTERMEDIATE:
                    self._state = "short"
                else:
                    self._state = None
                i += 1
                continue

            if self._state == "csi":
                if CSI_FINAL_MIN <= buf[i] <= CSI_FINAL_MAX:
                    self._state = None
                i += 1
                continue

            if self._state == "osc":
                if buf[i] == BEL:
                    self._state = None
                    i += 1
                    continue
                if buf[i] == ESC:
                    self._state = "osc-st"
                i += 1
                continue

            if self._state == "osc-st":
                self._state = None if buf[i] == 0x5C else "osc"
                i += 1
                continue

            # ``short`` means "one final byte outstanding" (``ESC ( B``).
            self._state = None
            i += 1
        return self.inside


def is_boundary_aligned(buf: bytes) -> bool:
    """Does ``buf`` begin somewhere a terminal would consider a fresh start?

    True for plain text, or for a buffer whose first escape sequence is
    complete. This is the property shedding must preserve.
    """
    return not buf or buf[0] != ESC or sequence_end(buf, 0) is not None
