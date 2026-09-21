"""TOTP (RFC 6238) helpers built on ``pyotp``."""

from __future__ import annotations

import pyotp

DEFAULT_ISSUER = "wsctl"


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, account: str, *, issuer: str = DEFAULT_ISSUER) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=issuer)


def verify(secret: str, code: str) -> bool:
    if not code:
        return False
    return bool(pyotp.TOTP(secret).verify(code, valid_window=1))
