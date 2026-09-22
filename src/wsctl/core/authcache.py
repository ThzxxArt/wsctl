"""Short-lived cache for authentication-session resolution.

Re-validating a live WebSocket runs every few seconds per connection. Doing
that as a synchronous SQLite query on the event loop is exactly the kind of
work this cache removes: a hit is a dict lookup.

Revocation must still land quickly, so every mutation of a user (disable,
password change, role change, delete) drops that user's entries, and dropping
a token drops its entry. The remaining staleness window is therefore only
"the cached user object is up to ``ttl`` seconds old", which never affects
permission changes because those invalidate.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance
    from .store import User

#: Returned by :meth:`AuthCache.get` when the caller must hit the database.
MISS: Any = object()


@dataclass
class _Entry:
    deadline: float  # monotonic: when the cache entry itself becomes stale
    token_expiry: float  # wall clock: when the auth session expires
    user_id: int | None
    user: User | None


class AuthCache:
    """Thread-safe, bounded cache of ``token_hash -> resolved user``.

    Besides the entries it keeps an *epoch* that every invalidation bumps. A
    caller reads the epoch before going to the database and passes it back to
    :meth:`put`; if a revocation landed in between the write-back is discarded.
    Without that guard a lookup that read the pre-revocation row would
    re-populate the cache with the stale user and defeat the "revocation is
    immediate" guarantee for up to ``ttl`` seconds.
    """

    def __init__(self, ttl: float = 30.0, max_entries: int = 4096) -> None:
        self.ttl = float(ttl)
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}
        self._epoch = 0

    def epoch(self) -> int:
        """Snapshot the invalidation generation; pass it to :meth:`put`."""
        with self._lock:
            return self._epoch

    def get(self, token_hash: str) -> Any:
        """Return the cached user, ``None`` for a known-bad token, or :data:`MISS`."""
        now_mono = time.monotonic()
        now_wall = time.time()
        with self._lock:
            entry = self._entries.get(token_hash)
            if entry is None:
                return MISS
            if now_mono >= entry.deadline or now_wall >= entry.token_expiry:
                self._entries.pop(token_hash, None)
                return MISS
            return entry.user

    def put(
        self,
        token_hash: str,
        user: User | None,
        *,
        token_expiry: float,
        epoch: int | None = None,
    ) -> None:
        """Record a resolution. ``token_expiry`` is absolute wall-clock time.

        When ``epoch`` is given and no longer current the entry is dropped: a
        revocation happened while the caller was reading.
        """
        now_mono = time.monotonic()
        entry = _Entry(
            deadline=now_mono + self.ttl,
            token_expiry=token_expiry,
            user_id=user.id if user is not None else None,
            user=user,
        )
        with self._lock:
            if epoch is not None and epoch != self._epoch:
                return
            if len(self._entries) >= self.max_entries:
                self._evict_locked(now_mono)
            self._entries[token_hash] = entry

    def invalidate_token(self, token_hash: str) -> None:
        with self._lock:
            self._epoch += 1
            self._entries.pop(token_hash, None)

    def invalidate_user(self, user_id: int | None) -> None:
        """Drop every cached entry belonging to ``user_id``."""
        with self._lock:
            self._epoch += 1
            if user_id is None:
                return
            stale = [key for key, entry in self._entries.items() if entry.user_id == user_id]
            for key in stale:
                del self._entries[key]

    def clear(self) -> None:
        with self._lock:
            self._epoch += 1
            self._entries.clear()

    def _evict_locked(self, now_mono: float) -> None:
        """Drop expired entries, then the oldest ones if still over capacity."""
        for key in [k for k, e in self._entries.items() if now_mono >= e.deadline]:
            del self._entries[key]
        while len(self._entries) >= self.max_entries:
            oldest = min(self._entries, key=lambda k: self._entries[k].deadline)
            del self._entries[oldest]
