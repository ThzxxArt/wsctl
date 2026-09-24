from __future__ import annotations

import pyotp

from wsctl.core import totp
from wsctl.server.deps import qr_svg


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


def test_the_qr_svg_carries_a_viewbox_so_inline_use_scales() -> None:
    """The 2FA code rendered at 45px in the corner of a 200px card.

    segno emits ``width/height`` in module pixels and **no viewBox**. Loaded
    through ``<img>`` the *document* is scaled to the box (the share code
    always looked right for exactly this reason); as an inline ``<svg>`` the
    CSS ``width/height: 100%`` enlarges only the viewport and the code keeps
    drawing at its intrinsic 45px. The viewBox is the missing link between
    user units and the viewport -- without it, no amount of CSS makes the
    code bigger.
    """
    svg = qr_svg("otpauth://totp/wsctl:admin?secret=JBSWY3DPEHPK3PXP&issuer=wsctl")
    assert "viewBox" in svg or "viewbox" in svg, (
        "the SVG has no viewBox; an inline copy cannot scale past its intrinsic size"
    )
    import re

    box = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
    size = re.search(r'width="(\d+)"\s+height="(\d+)"', svg)
    assert box and size, svg[:200]
    assert box.group(1) == size.group(1) and box.group(2) == size.group(2), (
        f"viewBox {box.group(0)} must match the intrinsic size {size.group(0)}"
    )
