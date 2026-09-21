from __future__ import annotations

from wsctl.server.security import is_origin_allowed


def test_missing_origin_allowed_for_non_browser() -> None:
    assert is_origin_allowed(None, "example.com", [])


def test_same_host_allowed() -> None:
    assert is_origin_allowed("http://example.com", "example.com", [])


def test_cross_host_rejected() -> None:
    assert not is_origin_allowed("http://evil.com", "example.com", [])


def test_explicit_allowlist() -> None:
    assert is_origin_allowed("https://ok.com", "example.com", ["https://ok.com"])
    assert not is_origin_allowed("https://no.com", "example.com", ["https://ok.com"])
