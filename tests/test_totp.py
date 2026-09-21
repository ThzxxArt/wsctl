from __future__ import annotations

import pyotp

from wsctl.core import totp


def test_generate_and_verify() -> None:
    secret = totp.generate_secret()
    code = pyotp.TOTP(secret).now()
    assert totp.verify(secret, code)


def test_reject_wrong_code() -> None:
    secret = totp.generate_secret()
    assert not totp.verify(secret, "000000")
    assert not totp.verify(secret, "")


def test_provisioning_uri() -> None:
    secret = totp.generate_secret()
    uri = totp.provisioning_uri(secret, "alice", issuer="wsctl")
    assert uri.startswith("otpauth://totp/")
    assert "issuer=wsctl" in uri
    assert "alice" in uri
