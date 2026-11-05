"""VAPID key generation for Web Push.

Operators should not have to install Node just to get a key pair. The same
P-256 keys Web Push specifies are generated with ``cryptography`` and written
in the base64url form ``pywebpush`` expects.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def generate_vapid_keys() -> tuple[str, str]:
    """Return ``(public_key, private_key)`` as base64url strings."""
    private = ec.generate_private_key(ec.SECP256R1())
    private_der = private.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return _b64url(public_raw), _b64url(private_der)


__all__ = ["generate_vapid_keys"]
