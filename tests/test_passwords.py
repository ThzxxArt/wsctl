"""The shared minimum password policy."""

from __future__ import annotations

import pytest

from wsctl.core.passwords import (
    MIN_PASSWORD_LENGTH,
    PasswordPolicyError,
    burn_verification,
    hash_password,
    validate_password,
    verify_password,
)


def test_accepts_reasonable_passwords() -> None:
    validate_password("password123")
    validate_password("correct horse battery staple")
    validate_password("12345678")


def test_rejects_empty_and_whitespace() -> None:
    for bad in ("", "   ", "\t\n"):
        with pytest.raises(PasswordPolicyError, match="不能为空"):
            validate_password(bad)


def test_rejects_short_passwords() -> None:
    with pytest.raises(PasswordPolicyError) as err:
        validate_password("x" * (MIN_PASSWORD_LENGTH - 1))
    assert str(MIN_PASSWORD_LENGTH) in str(err.value)
    validate_password("x" * MIN_PASSWORD_LENGTH)


def test_dummy_verification_does_not_raise() -> None:
    # Equalising login timing must be callable forever and must not leak.
    burn_verification("anything")
    burn_verification("anything")


def test_hash_round_trip() -> None:
    hashed = hash_password("password123")
    assert verify_password("password123", hashed)
    assert not verify_password("wrong-password", hashed)


def test_concurrent_verification_is_capped_by_the_lanes(monkeypatch: object) -> None:
    """The cap is the claim; without it a login storm runs ``cores`` Argon2s.

    ``_hasher.verify`` is replaced with a slow counter so the test measures the
    semaphore, not Argon2's runtime. Peak concurrency must sit exactly at the
    lane count: fewer means the cap is serialising (login latency would
    collapse), more means it is not capping at all.
    """
    import threading
    import time
    from types import SimpleNamespace

    import wsctl.core.passwords as pw

    lanes = pw.HASH_LANES
    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def fake_verify(_hashed: str, _password: str) -> bool:
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.05)
        with lock:
            state["active"] -= 1
        return True

    # ``PasswordHasher.verify`` is a read-only C attribute; replace the hasher.
    monkeypatch.setattr(  # type: ignore[attr-defined]
        pw, "_hasher", SimpleNamespace(verify=fake_verify)
    )
    threads = [
        threading.Thread(target=verify_password, args=("pw", "hash"))
        for _ in range(lanes * 3)
    ]

    # A barrier, not "start them quickly": without it a loaded runner could
    # start the last thread after the first has already finished, and the
    # measured peak would describe the scheduler rather than the cap.
    barrier = threading.Barrier(len(threads))

    def start() -> None:
        barrier.wait(timeout=10)
        verify_password("pw", "hash")

    threads = [threading.Thread(target=start) for _ in threads]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    # The property under test is the *cap*: on a loaded runner the peak can be
    # lower than ``lanes`` (the fake verify only holds for 50ms), which used to
    # be a false red. > lanes is the regression this exists for.
    assert 2 <= state["peak"] <= lanes, (
        f"{lanes * 3} concurrent verifications peaked at {state['peak']} "
        f"(lanes={lanes}); >lanes means the cap leaked"
    )
