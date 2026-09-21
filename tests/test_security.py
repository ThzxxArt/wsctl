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
