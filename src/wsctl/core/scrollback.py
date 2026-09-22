"""Bounded, in-memory ring buffer for raw terminal output.

The buffer stores the *raw* byte stream (including ANSI escape sequences) so
that a reconnecting client can be replayed the exact bytes and let its local
terminal emulator rebuild the screen state.
"""

from __future__ import annotations

from collections import deque

DEFAULT_MAX_BYTES = 4 * 1024 * 1024


class Scrollback:
    """A bounded FIFO of byte chunks, capped at ``max_bytes``."""

    __slots__ = ("_chunks", "_max_bytes", "_size")

    def __init__(self, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._max_bytes = max_bytes
        self._chunks: deque[bytes] = deque()
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
        self._size += len(data)
        self._trim()

    def snapshot(self) -> bytes:
        """Return all buffered bytes, oldest first."""
        return b"".join(self._chunks)

    def clear(self) -> None:
        self._chunks.clear()
        self._size = 0

    def _trim(self) -> None:
        while self._size > self._max_bytes and len(self._chunks) > 1:
            self._size -= len(self._chunks.popleft())
        if self._size > self._max_bytes:
            # A single chunk larger than the cap: keep its tail.
            chunk = self._chunks[0]
            drop = self._size - self._max_bytes
            tail = _align_utf8(chunk[drop:])
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
