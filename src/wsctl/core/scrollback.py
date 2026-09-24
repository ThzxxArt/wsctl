"""Bounded, in-memory ring buffer for raw terminal output.

The buffer stores the *raw* byte stream (including ANSI escape sequences) so
that a reconnecting client can be replayed the exact bytes and let its local
terminal emulator rebuild the screen state.
"""

from __future__ import annotations

from collections import deque

from .ansi import AnsiTracker

DEFAULT_MAX_BYTES = 4 * 1024 * 1024


class Scrollback:
    """A bounded FIFO of byte chunks, capped at ``max_bytes``."""

    __slots__ = ("_chunks", "_inside", "_max_bytes", "_size", "_tracker")

    def __init__(self, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._max_bytes = max_bytes
        self._chunks: deque[bytes] = deque()
        # Streaming ANSI state *after* each chunk (``None`` = outside a
        # sequence). Eviction needs the real state, not a bool: a kept head
        # that resumes mid-escape must skip the *remainder of that sequence*,
        # and only the state kind can say where it ends.
        self._inside: deque[str | None] = deque()
        self._tracker = AnsiTracker()
        self._size = 0

    @property
    def size(self) -> int:
        """Current number of buffered bytes."""
        return self._size

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def append(self, data: bytes) -> None:
        """Append ``data``, evicting the oldest bytes beyond the cap."""
        if not data:
            return
        self._chunks.append(data)
        self._tracker.feed(data)
        self._inside.append(self._tracker.state)
        self._size += len(data)
        self._trim()

    def snapshot(self) -> bytes:
        """Return all buffered bytes, oldest first."""
        return b"".join(self._chunks)

    def chunks(self) -> tuple[bytes, ...]:
        """The buffered chunks as stored, oldest first.

        Replaying through :meth:`snapshot` copied the whole buffer on every
        attach -- a 4 MiB scrollback was a 4 MiB ``join`` per reconnect, which
        is pure waste when the caller is only going to push the bytes out over
        a socket one chunk at a time.

        For the wire use :meth:`replay_frames` instead: stored chunk boundaries
        are PTY-read boundaries and a TUI writes in hundreds of tiny bursts, so
        a replay of *raw* chunks easily outruns the per-client frame cap and
        gets shredded -- by the very mechanism meant to protect slow viewers.
        """
        return tuple(self._chunks)

    def replay_frames(self, target: int = 65536) -> tuple[bytes, ...]:
        """The buffer as wire frames, coalesced to about ``target`` bytes.

        Chunk count is the hidden half of the outbound bound: the frame cap
        (``MAX_PENDING``) drops *frames*, and a full-screen app's output is
        thousands of tiny PTY reads. Enqueuing those verbatim during an attach
        replay hit the frame cap long before the byte budget -- the replay then
        shed its own head, the viewer was told it had "lost sync" while sitting
        at an idle prompt, and the screen that came back was a TUI the user had
        already exited, drawn from a gapped stream.
        """
        if target <= 0:
            target = 1
        out: list[bytes] = []
        buf = bytearray()
        for chunk in self._chunks:
            buf += chunk
            while len(buf) >= target:
                out.append(bytes(buf[:target]))
                del buf[:target]
        if buf:
            out.append(bytes(buf))
        return tuple(out)

    def clear(self) -> None:
        self._chunks.clear()
        self._inside.clear()
        self._size = 0
        self._tracker = AnsiTracker()

    def _trim(self) -> None:
        """Evict from the left until within budget, cutting on safe boundaries.

        Two rules, both learned the hard way:

        1. **Only trim when over budget.** The first version ran its
           "re-anchor the head" pass on every append, so a chunk that merely
           *ended* mid-escape -- which is what every PTY read of ``ESC[3`` /
           ``1m`` does -- was dropped with zero memory pressure. The replay
           then began with literal ``1m``: the colour tail read as text.
        2. **Cut where the stream is outside a sequence**, using the same rule
           as :meth:`wsctl.server.client.WsClient._drop_oldest_binary`: keep
           evicting until the last chunk removed *ended* outside, so whatever
           is now at the head starts at a fresh boundary. A stateless scan of
           the head cannot tell -- it may legitimately begin mid-sequence.

        ``resume_state`` covers the one place that rule cannot reach: the last
        chunk is kept even when the bytes before it left the parser inside a
        sequence (we do not throw away the only copy). That head is re-anchored
        by skipping the remainder of the sequence it resumes in.
        """
        resume_state: str | None = None
        while self._size > self._max_bytes and len(self._chunks) > 1:
            while len(self._chunks) > 1:
                dropped = self._chunks.popleft()
                resume_state = self._inside.popleft()
                self._size -= len(dropped)
                if resume_state is None:
                    break
        if self._chunks and resume_state is not None:
            # The kept head resumes mid-escape. Skip the rest of that sequence
            # (its first half is gone) so the replay starts at a boundary.
            probe = AnsiTracker()
            probe.resume(resume_state)
            head = probe.skip_to_boundary(self._chunks[0])
            self._size -= len(self._chunks[0]) - len(head)
            if head:
                self._chunks[0] = head
            else:
                # The whole head was the remainder of a sequence; the next
                # chunk (if any) starts where that sequence ended.
                self._chunks.popleft()
                self._inside.popleft()
        if self._size > self._max_bytes and self._chunks:
            # A single chunk larger than the cap: keep its tail. The cut must
            # land outside a sequence as well -- feeding the dropped prefix to
            # a tracker is the only way to know, because the tail may open on
            # the remainder (``1m...``) rather than on an ESC.
            chunk = self._chunks[0]
            drop = self._size - self._max_bytes
            tail = chunk[drop:]
            probe = AnsiTracker()
            if probe.feed(chunk[:drop]):
                tail = probe.skip_to_boundary(tail)
            tail = _align_utf8(tail)
            self._chunks[0] = tail
            self._size -= len(chunk) - len(tail)


def _align_utf8(buf: bytes) -> bytes:
    """Drop leading UTF-8 continuation bytes after a mid-character cut.

    Eviction works on bytes, so the oldest few bytes may be the tail of a
    multi-byte character whose lead byte was just evicted. Replaying those
    orphan continuation bytes would render as a replacement glyph, so skip to
    the next character boundary.
    """
    index = 0
    # A UTF-8 sequence is at most 4 bytes, so at most 3 continuations can be
    # orphaned at the head.
    while index < len(buf) and index < 3 and 0x80 <= buf[index] <= 0xBF:
        index += 1
    return buf[index:]
