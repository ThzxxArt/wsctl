"""Password hashing helpers built on Argon2.

Also holds the product's minimum password policy so that every write path
(storage layer, HTTP API and CLI) enforces exactly the same rule.
"""

from __future__ import annotations

from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_PASSWORD_LENGTH = 8

_hasher = PasswordHasher()


class PasswordPolicyError(ValueError):
    """A password that violates the minimum password policy."""


def validate_password(password: str) -> None:
    """Enforce the minimum password policy; raise :class:`PasswordPolicyError`."""
    if not password or not password.strip():
        raise PasswordPolicyError("密码不能为空")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"密码至少需要 {MIN_PASSWORD_LENGTH} 位")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except (VerificationError, InvalidHashError):
        return False


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """Hash verified against unknown accounts to equalize login timing.

    Computed lazily: doing this at import time made every CLI invocation pay a
    full Argon2 pass just to print ``--version``.
    """
    return hash_password("wsctl-timing-equalizer")


def burn_verification(password: str) -> None:
    """Waste one verification so a missing account costs like a wrong password."""
    verify_password(password, _dummy_hash())
