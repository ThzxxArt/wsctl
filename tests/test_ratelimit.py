from __future__ import annotations

from wsctl.core.ratelimit import RateLimiter, TokenBucket


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


def test_token_bucket_depletes_and_refills() -> None:
    bucket = TokenBucket(rate=10.0, capacity=10.0)
    assert bucket.allow(10.0, now=0.0)
    assert not bucket.allow(1.0, now=0.0)
    assert bucket.allow(5.0, now=1.0)


def test_token_bucket_caps_at_capacity() -> None:
    bucket = TokenBucket(rate=10.0, capacity=10.0)
    assert bucket.allow(1.0, now=0.0)
    # after a long idle period the bucket refills to capacity, not beyond
    assert bucket.allow(10.0, now=1000.0)
    assert not bucket.allow(0.1, now=1000.0)


def test_sweep_evicts_empty_keys() -> None:
    rl = RateLimiter(limit=3, window=10)
    rl.record_failure("k", now=0.0)
    assert rl.sweep(now=5.0) == 0  # still inside the window
    assert rl.sweep(now=20.0) == 1  # expired -> evicted
    assert rl._events == {}


def test_is_blocked_evicts_expired_key() -> None:
    rl = RateLimiter(limit=1, window=10)
    rl.record_failure("k", now=0.0)
    assert not rl.is_blocked("k", now=20.0)
    assert "k" not in rl._events


def test_retry_after_evicts_expired_key() -> None:
    rl = RateLimiter(limit=1, window=10)
    rl.record_failure("k", now=0.0)
    assert rl.retry_after("k", now=20.0) == 0.0
    assert "k" not in rl._events
