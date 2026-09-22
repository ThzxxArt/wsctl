"""The short-lived auth-session cache and its revocation guarantees."""

from __future__ import annotations

import time

from wsctl.core.authcache import MISS, AuthCache
from wsctl.core.store import User


def _user(uid: int = 1) -> User:
    return User(id=uid, username=f"u{uid}", role="user", disabled=False, created_at=0.0)


def test_miss_then_hit() -> None:
    cache = AuthCache(ttl=30.0)
    assert cache.get("t1") is MISS
    cache.put("t1", _user(), token_expiry=time.time() + 3600)
    assert cache.get("t1") is not MISS


def test_negative_result_is_cached() -> None:
    cache = AuthCache(ttl=30.0)
    cache.put("bad", None, token_expiry=time.time() + 30)
    assert cache.get("bad") is None
    assert cache.get("bad") is not MISS


def test_expired_token_is_dropped() -> None:
    cache = AuthCache(ttl=30.0)
    cache.put("t1", _user(), token_expiry=time.time() - 1)
    assert cache.get("t1") is MISS


def test_stale_entry_is_dropped() -> None:
    cache = AuthCache(ttl=0.01)
    cache.put("t1", _user(), token_expiry=time.time() + 3600)
    time.sleep(0.03)
    assert cache.get("t1") is MISS


def test_invalidate_user_drops_only_that_user() -> None:
    cache = AuthCache(ttl=30.0)
    expiry = time.time() + 3600
    cache.put("a", _user(1), token_expiry=expiry)
    cache.put("b", _user(2), token_expiry=expiry)
    cache.invalidate_user(1)
    assert cache.get("a") is MISS
    assert cache.get("b") is not MISS


def test_invalidate_token() -> None:
    cache = AuthCache(ttl=30.0)
    cache.put("a", _user(), token_expiry=time.time() + 3600)
    cache.invalidate_token("a")
    assert cache.get("a") is MISS


def test_capacity_eviction_is_bounded() -> None:
    cache = AuthCache(ttl=30.0, max_entries=8)
    expiry = time.time() + 3600
    for index in range(50):
        cache.put(f"t{index}", _user(index), token_expiry=expiry)
    assert len(cache._entries) <= 8


def test_clear() -> None:
    cache = AuthCache(ttl=30.0)
    cache.put("a", _user(), token_expiry=time.time() + 3600)
    cache.clear()
    assert cache.get("a") is MISS


def test_write_back_is_discarded_when_a_revocation_landed_mid_read() -> None:
    """The cache-aside race must not resurrect a revoked user.

    Sequence: read the epoch, read the (still unrevoked) row, revocation lands,
    write back. Without the epoch guard the stale user would be cached and the
    "revocation is immediate" guarantee would silently degrade to ``ttl``.
    """
    cache = AuthCache(ttl=30.0)
    epoch = cache.epoch()          # caller snapshots before hitting the DB
    cache.invalidate_user(1)       # ...revocation lands while it was reading
    cache.put("t1", _user(1), token_expiry=time.time() + 3600, epoch=epoch)
    assert cache.get("t1") is MISS, "a stale write-back must not be cached"


def test_write_back_is_accepted_when_nothing_changed() -> None:
    cache = AuthCache(ttl=30.0)
    epoch = cache.epoch()
    cache.put("t1", _user(1), token_expiry=time.time() + 3600, epoch=epoch)
    assert cache.get("t1") is not MISS


def test_epoch_advances_on_every_invalidation() -> None:
    cache = AuthCache(ttl=30.0)
    start = cache.epoch()
    cache.invalidate_token("x")
    cache.invalidate_user(7)
    cache.clear()
    assert cache.epoch() >= start + 3
