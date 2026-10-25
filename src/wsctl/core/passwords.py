"""Password hashing helpers built on Argon2.

Also holds the product's minimum password policy so that every write path
(storage layer, HTTP API and CLI) enforces exactly the same rule.
"""

from __future__ import annotations

import os
from functools import lru_cache
from threading import BoundedSemaphore

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_PASSWORD_LENGTH = 8

_hasher = PasswordHasher()

#: How many Argon2 passes may run at once. The parameters are deliberately
#: expensive (64 MiB, ~40 ms) and every login attempt pays one; without a cap a
#: storm pinned every core and starved the event loop's worker threads. The
#: per-IP login budget bounds how many attempts *arrive*; this bounds how many
#: run concurrently when the attempts are legitimate. Small on purpose: two to
#: four lanes already saturate a laptop, and extra lanes only add latency.
HASH_LANES = max(2, min(4, os.cpu_count() or 2))
_hash_slots = BoundedSemaphore(HASH_LANES)


class PasswordPolicyError(ValueError):
    """A password that violates the minimum password policy."""


def validate_password(password: str) -> None:
    """Enforce the minimum password policy; raise :class:`PasswordPolicyError`."""
    if not password or not password.strip():
        raise PasswordPolicyError("密码不能为空")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"密码至少需要 {MIN_PASSWORD_LENGTH} 位")


def hash_password(password: str) -> str:
    with _hash_slots:
        return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    with _hash_slots:
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
