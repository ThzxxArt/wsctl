from __future__ import annotations

from types import SimpleNamespace

from wsctl.server.security import can_access, ip_allowed, is_origin_allowed


def test_missing_origin_allowed_for_non_browser() -> None:
    assert is_origin_allowed(None, "example.com", [])


def test_same_host_allowed() -> None:
    assert is_origin_allowed("http://example.com", "example.com", [])


def test_cross_host_rejected() -> None:
    assert not is_origin_allowed("http://evil.com", "example.com", [])


def test_explicit_allowlist() -> None:
    assert is_origin_allowed("https://ok.com", "example.com", ["https://ok.com"])
    assert not is_origin_allowed("https://no.com", "example.com", ["https://ok.com"])


def test_can_access_owner_and_admin() -> None:
    owner = SimpleNamespace(id=7, role="user")
    other = SimpleNamespace(id=8, role="user")
    admin = SimpleNamespace(id=8, role="admin")
    session = SimpleNamespace(owner_id=7)
    assert can_access(owner, session)
    assert not can_access(other, session)
    assert can_access(admin, session)


def test_ip_allowlist_empty_allows_all() -> None:
    assert ip_allowed("203.0.113.5", [])
    assert ip_allowed(None, [])


def test_ip_allowlist_cidr() -> None:
    allowed = ["10.0.0.0/8", "192.168.1.0/24"]
    assert ip_allowed("10.1.2.3", allowed)
    assert ip_allowed("192.168.1.50", allowed)
    assert not ip_allowed("192.168.2.1", allowed)
    assert not ip_allowed(None, allowed)
    assert not ip_allowed("not-an-ip", allowed)


# -- 0.1.22: X-Forwarded-For is appended to, so the LEFT end is the liar ------


def test_a_spoofed_xff_header_cannot_bypass_the_ip_allowlist() -> None:
    """``X-Forwarded-For`` is *appended* to by the proxy, not replaced.

    Under ``proxy_add_x_forwarded_for`` (the nginx example in the README) the
    leftmost entry is whatever the client sent. Reading it made the IP
    allowlist, the login rate-limit key and every audit record forgeable: send
    ``X-Forwarded-For: 10.0.0.1`` and walk straight through
    ``allowed_ips=["10.0.0.0/8"]``.
    """
    from wsctl.core.config import load_settings
    from wsctl.server.security import client_ip, ip_allowed

    class FakeRequest:
        def __init__(self, forwarded: str, peer: str) -> None:
            self.headers = {"x-forwarded-for": forwarded}
            self.client = type("C", (), {"host": peer})()

    settings = load_settings(auth_required=True, trust_proxy=True)
    # The proxy appended the real client (203.0.113.9) after the spoofed one.
    req = FakeRequest("10.0.0.1, 203.0.113.9", "127.0.0.1")
    seen = client_ip(req, settings)
    assert seen == "203.0.113.9", f"the spoofed left end won: {seen}"
    assert not ip_allowed(seen, ["10.0.0.0/8"]), (
        "the allowlist must see the address the proxy observed"
    )
    # Without a spoof the answer is unchanged.
    assert client_ip(FakeRequest("203.0.113.9", "127.0.0.1"), settings) == "203.0.113.9"
    # A local proxy appending itself must not hide the client either.
    assert client_ip(FakeRequest("203.0.113.9, 127.0.0.1", "127.0.0.1"), settings) == (
        "203.0.113.9"
    )
