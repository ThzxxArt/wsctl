"""In-memory sliding-window rate limiter for authentication attempts."""

from __future__ import annotations

import time
from collections import deque


class RateLimiter:
    """Tracks failures per key and blocks keys that exceed a threshold.

    Only failures are recorded, so successful logins never consume quota; a
    success clears the key. State is process-local (single-server deployment).
    """

    def __init__(self, limit: int, window: float) -> None:
        self.limit = limit
        self.window = window
        self._events: dict[str, deque[float]] = {}

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
        now = time.monotonic() if now is None else now
        if self.limit <= 0:
            return False
        events = self._prune(key, now)
        if not events:
            self._events.pop(key, None)
            return False
        return len(events) >= self.limit

    def retry_after(self, key: str, *, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        events = self._prune(key, now)
        if not events:
            self._events.pop(key, None)
            return 0.0
        return max(0.0, events[0] + self.window - now)

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self._prune(key, now).append(now)

    def reset(self, key: str) -> None:
        self._events.pop(key, None)

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
