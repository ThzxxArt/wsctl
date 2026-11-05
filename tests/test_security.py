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


# -- 0.1.30: the header is only believed from a trusted peer ------------------


class _Req:
    def __init__(self, forwarded: str | None, peer: str) -> None:
        self.headers = {} if forwarded is None else {"x-forwarded-for": forwarded}
        self.client = type("C", (), {"host": peer})()


def test_xff_is_ignored_entirely_when_trust_proxy_is_off() -> None:
    """``trust_proxy=False`` must not merely relabel the header -- it must not be read.

    uvicorn's default ``proxy_headers=True`` rewrote ``scope["client"]`` from
    the header *before* wsctl saw the request, so this function returned the
    attacker's address even here. The CLI now starts uvicorn with
    ``proxy_headers=False``.
    """
    from wsctl.core.config import load_settings
    from wsctl.server.security import client_ip

    settings = load_settings(auth_required=True)  # trust_proxy default: False
    assert client_ip(_Req("10.0.0.7", "203.0.113.50"), settings) == "203.0.113.50"
    assert client_ip(_Req("127.0.0.1", "127.0.0.1"), settings) == "127.0.0.1"


def test_a_peer_that_is_not_a_trusted_proxy_cannot_forward_anything() -> None:
    from wsctl.core.config import load_settings
    from wsctl.server.security import client_ip

    settings = load_settings(
        auth_required=True, trust_proxy=True, trusted_proxies=["10.9.0.0/16"]
    )
    # 10.1.2.3 can reach us, but it is not the proxy we named.
    assert client_ip(_Req("1.2.3.4", "10.1.2.3"), settings) == "10.1.2.3"


def test_a_named_proxy_is_trusted_but_a_loopback_peer_still_is_without_one() -> None:
    from wsctl.core.config import load_settings
    from wsctl.server.security import client_ip

    named = load_settings(
        auth_required=True, trust_proxy=True, trusted_proxies=["10.9.0.0/16"]
    )
    assert client_ip(_Req("203.0.113.9", "10.9.1.2"), named) == "203.0.113.9"
    # Adding a proxy must not revoke the local one: loopback stays trusted.
    assert client_ip(_Req("203.0.113.9", "127.0.0.1"), named) == "203.0.113.9"
    # ...but an unrelated peer gains nothing from the list.
    assert client_ip(_Req("203.0.113.9", "203.0.113.50"), named) == "203.0.113.50"

    default = load_settings(auth_required=True, trust_proxy=True)
    assert client_ip(_Req("203.0.113.9", "127.0.0.1"), default) == "203.0.113.9"


def test_all_hops_trusted_falls_back_to_the_peer_not_the_leftmost_claim() -> None:
    from wsctl.core.config import load_settings
    from wsctl.server.security import client_ip

    # A *named* trusted proxy in the last hop, loopback peer. Returning the
    # leftmost claim (or the proxy itself) would also satisfy a plain
    # ``127.0.0.1`` fixture -- this one can tell them apart.
    settings = load_settings(
        auth_required=True, trust_proxy=True, trusted_proxies=["10.9.0.0/16"]
    )
    assert client_ip(_Req("10.9.1.2", "127.0.0.1"), settings) == "127.0.0.1"
    # Every entry is loopback: nothing here identifies a client, and the
    # leftmost one is just as likely to be a claim the peer made up.
    loopback_only = load_settings(auth_required=True, trust_proxy=True)
    assert client_ip(_Req("127.0.0.1", "127.0.0.1"), loopback_only) == "127.0.0.1"


def test_malformed_hops_are_skipped_rather_than_returned() -> None:
    from wsctl.core.config import load_settings
    from wsctl.server.security import client_ip

    settings = load_settings(auth_required=True, trust_proxy=True)
    # The garbage is the *first* hop examined (right-to-left walk); placing it
    # on the left would never reach the code under test.
    assert client_ip(_Req("203.0.113.9, garbage", "127.0.0.1"), settings) == (
        "203.0.113.9"
    )


# -- 0.1.30 review: same-origin must survive a reverse proxy ------------------


def test_a_port_less_host_does_not_match_a_non_default_origin_port() -> None:
    """``Host`` without a port means the scheme's default port, not any port.

    Treating it as a wildcard let a page on ``http://host:8080`` drive an
    authenticated panel on ``http://host`` (same-site, so the Lax cookie was
    sent). A proxy must preserve the external port (``Host $http_host``) or
    the operator must list the origin explicitly.
    """
    assert not is_origin_allowed(
        "https://term.example.com:8443", "term.example.com", []
    )
    assert is_origin_allowed(
        "https://term.example.com:8443",
        "term.example.com",
        ["https://term.example.com:8443"],
    )
    # A direct connection carries the port in Host; it must still match.
    assert is_origin_allowed(
        "https://term.example.com:8443", "term.example.com:8443", []
    )
    assert not is_origin_allowed(
        "https://term.example.com:8443", "term.example.com:9443", []
    )
    assert not is_origin_allowed("https://evil.example", "term.example.com", [])
    # Case-insensitive hostnames; ``Origin: null`` is never same-origin.
    assert is_origin_allowed("https://TERM.example.com", "term.example.com", [])
    assert not is_origin_allowed("null", "term.example.com", [])


def test_the_origin_allowlist_is_additional_not_exclusive() -> None:
    """Configuring the WS whitelist used to break same-origin HTTP entirely."""
    allow = ["https://ws.example.com"]
    assert is_origin_allowed("https://ws.example.com", "term.example.com", allow)
    assert is_origin_allowed("https://term.example.com", "term.example.com", allow)
    assert not is_origin_allowed("https://evil.example", "term.example.com", allow)


def test_forwarded_proto_is_only_believed_from_a_trusted_proxy() -> None:
    """``proxy_headers=False`` also removed uvicorn's scheme handling.

    Without a replacement, a panel behind a TLS-terminating proxy built
    ``http://`` share links for every non-browser caller.
    """
    from wsctl.core.config import load_settings
    from wsctl.server.security import forwarded_scheme

    class ProtoReq(_Req):
        def __init__(self, peer: str, proto: str | None) -> None:
            super().__init__(None, peer)
            if proto is not None:
                self.headers["x-forwarded-proto"] = proto

    named = load_settings(
        auth_required=True, trust_proxy=True, trusted_proxies=["10.9.0.0/16"]
    )
    assert forwarded_scheme(ProtoReq("10.9.1.2", "https"), named) == "https"
    # Not the proxy we named: its claim is ignored, not just relabelled.
    assert forwarded_scheme(ProtoReq("203.0.113.9", "https"), named) is None
    # trust_proxy=False ignores the header entirely.
    untrusted = load_settings(auth_required=True)
    assert forwarded_scheme(ProtoReq("127.0.0.1", "https"), untrusted) is None
    # Garbage is not a scheme.
    assert forwarded_scheme(ProtoReq("10.9.1.2", "javascript"), named) is None


def test_origin_scheme_must_match_the_served_scheme() -> None:
    """A missing Host port used to make ``http://host`` match an HTTPS panel.

    ``Origin`` and ``Host`` agreed on the hostname, and with no port in Host the
    port check was skipped entirely. Another service on port 80 of the same
    hostname could then drive authenticated writes.
    """
    assert not is_origin_allowed(
        "http://term.example.com", "term.example.com", [], scheme="https"
    )
    assert is_origin_allowed(
        "https://term.example.com", "term.example.com", [], scheme="https"
    )
    # WebSocket schemes are normalised before comparison.
    assert is_origin_allowed(
        "https://term.example.com", "term.example.com", [], scheme="wss"
    )
    assert not is_origin_allowed(
        "http://term.example.com", "term.example.com", [], scheme="wss"
    )
    # The operator's explicit allowlist is still additional to the same-origin
    # rule; it does not get invalidated by a scheme mismatch.
    assert is_origin_allowed(
        "http://other.example", "term.example.com", ["http://other.example"], scheme="https"
    )
