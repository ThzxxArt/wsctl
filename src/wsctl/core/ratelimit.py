"""Rate limiting for authentication attempts and terminal input.

The login limiter is backed by ``limits`` (a moving-window strategy with a
bounded in-memory store). The ``now=`` argument is a *test clock*: unit tests
need deterministic windows, and ``limits`` deliberately uses wall time, so the
synthetic-clock path keeps the small deque implementation. Production code
never passes ``now`` and therefore always exercises the library.

``TokenBucket`` remains custom: ``limits`` counts events, while terminal input
is measured in bytes and needs a burst capacity, which is a different shape.
"""

from __future__ import annotations

import secrets
import time
from collections import deque

from limits import RateLimitItemPerSecond
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter


class RateLimiter:
    """Tracks failures per key and blocks keys that exceed a threshold.

    Only failures are recorded, so successful logins never consume quota; a
    success clears the key. State is process-local (single-server deployment).
    """

    def __init__(self, limit: int, window: float) -> None:
        self._limit = int(limit)
        self._window = float(window)
        #: ``now=`` calls use this deque; production calls use ``limits``.
        self._events: dict[str, deque[float]] = {}
        self._storage = MemoryStorage()
        self._strategy = MovingWindowRateLimiter(self._storage)
        self._namespace = f"wsctl-{secrets.token_hex(4)}"
        self._item = self._rate_item()

    def _rate_item(self) -> RateLimitItemPerSecond:
        return RateLimitItemPerSecond(
            max(1, self._limit),
            max(1, round(self._window)),
            namespace=self._namespace,
        )

    @property
    def limit(self) -> int:
        return self._limit

    @limit.setter
    def limit(self, value: int) -> None:
        # Reload updates the derived per-IP budget by assigning to ``limit``.
        # The ``limits`` item embeds the amount in its storage key, so it must
        # be rebuilt; otherwise the attribute would say 20 while the strategy
        # kept enforcing 30.
        self._limit = int(value)
        self._item = self._rate_item()

    @property
    def window(self) -> float:
        return self._window

    @window.setter
    def window(self, value: float) -> None:
        # Same trap as ``limit``: the window is part of the storage key, and a
        # hot reload that only changed the attribute kept enforcing the old
        # window.
        self._window = float(value)
        self._item = self._rate_item()

    # -- production path (limits) --------------------------------------

    def _blocked_library(self, key: str) -> bool:
        if self.limit <= 0:
            return False
        return not self._strategy.test(self._item, key)

    def _retry_library(self, key: str) -> float:
        if self.limit <= 0:
            return 0.0
        stats = self._strategy.get_window_stats(self._item, key)
        return max(0.0, float(stats.reset_time) - time.time())

    # -- synthetic-clock path (unit tests) -----------------------------

    def _prune(self, key: str, now: float) -> deque[float]:
        events = self._events.get(key)
        if events is None:
            events = deque()
            self._events[key] = events
        cutoff = now - self.window
        while events and events[0] < cutoff:
            events.popleft()
        return events

    def is_blocked(self, key: str, *, now: float | None = None) -> bool:
        if now is None:
            return self._blocked_library(key)
        if self.limit <= 0:
            return False
        events = self._prune(key, now)
        if not events:
            self._events.pop(key, None)
            return False
        return len(events) >= self.limit

    def retry_after(self, key: str, *, now: float | None = None) -> float:
        if now is None:
            return self._retry_library(key)
        events = self._prune(key, now)
        if not events:
            self._events.pop(key, None)
            return 0.0
        return max(0.0, events[0] + self.window - now)

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        if now is None:
            if self.limit > 0:
                self._strategy.hit(self._item, key)
            return
        self._prune(key, now).append(now)

    def reset(self, key: str) -> None:
        self._events.pop(key, None)
        self._storage.clear(self._item.key_for(key))

    def sweep(self, *, now: float | None = None) -> int:
        """Drop keys with no failures left in the window (bounds memory)."""
        now = time.monotonic() if now is None else now
        cutoff = now - self.window
        removed = 0
        for key in list(self._events):
            events = self._events[key]
            while events and events[0] < cutoff:
                events.popleft()
            if not events:
                del self._events[key]
                removed += 1
        return removed


class TokenBucket:
    """Simple token bucket used to cap sustained input throughput."""

    def __init__(self, rate: float, capacity: float) -> None:
        self.rate = rate
        self.capacity = capacity
        self._tokens = capacity
        self._last = time.monotonic()

    def allow(self, amount: float, *, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        if self._tokens >= amount:
            self._tokens -= amount
            return True
        return False


__all__ = ["RateLimiter", "TokenBucket"]
