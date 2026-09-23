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
        # Streaming ANSI state *after* each chunk, so eviction can tell whether
        # the new head resumes mid-escape. A replay that begins inside a colour
        # sequence is what turns a full-screen program into garbage on
        # reconnect.
        self._inside: deque[bool] = deque()
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
        self._inside.append(self._tracker.feed(data))
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
        """
        return tuple(self._chunks)

    def clear(self) -> None:
        self._chunks.clear()
        self._inside.clear()
        self._size = 0
        self._tracker = AnsiTracker()

    def _trim(self) -> None:
        while self._size > self._max_bytes and len(self._chunks) > 1:
            self._size -= len(self._chunks.popleft())
            self._inside.popleft()
        # Whatever is now at the head is the start of a replay. If the evicted
        # bytes left the stream inside an escape sequence, that head resumes
        # mid-escape and a full-screen program renders as garbage on reconnect.
        # The tracker (not a stateless scan) is what knows, because a chunk can
        # begin mid-sequence exactly the way a PTY read can.
        while self._chunks and self._inside and self._inside[0]:
            dropped = self._chunks.popleft()
            self._inside.popleft()
            self._size -= len(dropped)
        if self._size > self._max_bytes:
            # A single chunk larger than the cap: keep its tail.
            chunk = self._chunks[0]
            drop = self._size - self._max_bytes
            tail = _align_ansi(_align_utf8(chunk[drop:]))
            self._chunks[0] = tail
            self._size -= len(chunk) - len(tail)


def _align_ansi(buf: bytes) -> bytes:
    """Drop a leading partial escape sequence.

    The head of the buffer becomes the head of a replay after eviction, and a
    replay that starts mid-sequence corrupts the terminal it is replayed into.
    """
    return buf  # kept for call sites that only need UTF-8 alignment


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
