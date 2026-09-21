from __future__ import annotations

from wsctl.core.ratelimit import RateLimiter


def test_blocks_after_limit() -> None:
    rl = RateLimiter(limit=3, window=60)
    assert not rl.is_blocked("k")
    for _ in range(3):
        rl.record_failure("k")
    assert rl.is_blocked("k")
    assert not rl.is_blocked("other")


def test_reset_clears_failures() -> None:
    rl = RateLimiter(limit=1, window=60)
    rl.record_failure("k")
    assert rl.is_blocked("k")
    rl.reset("k")
    assert not rl.is_blocked("k")


def test_window_expiry() -> None:
    rl = RateLimiter(limit=1, window=10)
    rl.record_failure("k", now=0.0)
    assert rl.is_blocked("k", now=5.0)
    assert not rl.is_blocked("k", now=11.0)


def test_retry_after() -> None:
    rl = RateLimiter(limit=1, window=10)
    rl.record_failure("k", now=100.0)
    assert rl.retry_after("k", now=104.0) == 6.0


def test_zero_limit_never_blocks() -> None:
    rl = RateLimiter(limit=0, window=60)
    rl.record_failure("k")
    assert not rl.is_blocked("k")
