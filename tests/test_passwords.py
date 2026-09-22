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
