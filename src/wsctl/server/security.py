"""Security helpers: origin validation, authorization and session cookies."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from fastapi import Request, WebSocket

from wsctl.core.config import Settings
from wsctl.core.session import TermSession
from wsctl.core.store import User

COOKIE_NAME = "wsctl_session"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        # ``img-src`` carries ``blob:`` because the file panel previews a
        # bitmap from an object URL (``URL.createObjectURL``) rather than
        # shipping it through a data: URL -- an 8 MB picture would otherwise
        # be base64-inflated by a third and held as one string. A ``blob:``
        # URL can only name bytes this origin already holds, so it widens
        # nothing that a script could not reach another way.
        "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        "connect-src 'self' blob:; worker-src 'self' blob:"
    ),
}


def _is_loopback(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _networks(entries: list[str]) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Parse CIDRs, ignoring entries that are not valid networks."""
    parsed: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for entry in entries:
        try:
            parsed.append(ipaddress.ip_network(str(entry).strip(), strict=False))
        except ValueError:
            continue
    return parsed


def _in_networks(
    value: str, networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network]
) -> bool:
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return False
    return any(addr in network for network in networks)


def _trusted_proxy_peer(peer: str, settings: Settings) -> bool:
    """Whether ``peer`` is allowed to tell us who the client was.

    Loopback is always trusted: that is the shape of "nginx on the same host",
    and listing an *additional* proxy in ``trusted_proxies`` must not silently
    revoke the local one. A non-loopback proxy has to be named, otherwise every
    host on the network that can reach us could rewrite its own source address.
    ``trust_proxy=False`` bypasses all of this (the header is not read at all).
    """
    if _is_loopback(peer):
        return True
    return _in_networks(peer, _networks(settings.trusted_proxies))


def client_ip(conn: Request | WebSocket, settings: Settings) -> str | None:
    """Resolve the client IP, honouring ``X-Forwarded-For`` behind a proxy.

    The header is only believed when *both* hold:

    * ``trust_proxy`` is on -- the operator said a proxy is in front, and
    * the socket peer is a trusted proxy (``trusted_proxies``, or loopback).

    Two bugs made this forgeable before:

    1. ``X-Forwarded-For`` is *appended* to, so the leftmost entry is whatever
       the client sent. Reading it made ``allowed_ips``, the login rate-limit
       key and every audit record forgeable: send ``X-Forwarded-For: 10.0.0.1``
       and walk straight through ``allowed_ips=["10.0.0.0/8"]``. The walk below
       goes right-to-left, so the answer is the hop our own proxy observed.
    2. ``uvicorn.run``/``uvicorn.Config`` were called with their default
       ``proxy_headers=True``, which installs a middleware that rewrites
       ``scope["client"]`` from ``X-Forwarded-For`` *before this code runs* --
       so even ``trust_proxy=False`` returned the attacker's value. The CLI now
       starts uvicorn with ``proxy_headers=False``; this function is the only
       place the header is interpreted.
    """
    peer = conn.client.host if conn.client else None
    if not settings.trust_proxy or not peer:
        return peer
    if not _trusted_proxy_peer(peer, settings):
        return peer
    forwarded = conn.headers.get("x-forwarded-for")
    if not forwarded:
        return peer
    hops = [part.strip() for part in forwarded.split(",") if part.strip()]
    trusted = _networks(settings.trusted_proxies)
    # Right-to-left: skip the hops that are themselves our own proxies. The
    # first address we did not vouch for is the client our proxy saw.
    for hop in reversed(hops):
        if not _is_loopback(hop) and not _in_networks(hop, trusted):
            try:
                ipaddress.ip_address(hop)
            except ValueError:
                # A malformed hop is not an address; keeping it would poison
                # the allowlist with a string that can never match anyway.
                continue
            return hop
    # Every hop is a trusted proxy: nothing told us who the original client
    # was, so say "the peer" rather than repeating an unverified claim.
    return peer


def ip_allowed(ip: str | None, allowed: list[str]) -> bool:
    """Whether ``ip`` falls within the allowlist (empty allowlist allows all)."""
    if not allowed:
        return True
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for entry in allowed:
        try:
            if addr in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


def can_access(user: User, session: TermSession) -> bool:
    """Admins may access every session; others only the sessions they own."""
    return user.role == "admin" or session.owner_id == user.id


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _split_host(value: str) -> tuple[str, int | None]:
    """``host[:port]`` (including ``[::1]:7681``) -> ``(hostname, port)``."""
    try:
        parsed = urlsplit(f"//{value}")
        hostname = (parsed.hostname or "").lower()
        return hostname, parsed.port
    except ValueError:
        return "", None


def is_origin_allowed(origin: str | None, host: str | None, allowed: list[str]) -> bool:
    """Validate an ``Origin`` against the request's own host or an allowlist.

    A missing ``Origin`` means a non-browser client (e.g. the CLI); such
    requests are permitted here but must still authenticate.

    The comparison ignores the *port when the Host header has none*: nginx's
    documented ``proxy_set_header Host $host`` drops the port, so a panel on
    ``https://term.example.com:8443`` sent ``Origin: https://term.example.com:8443``
    against ``Host: term.example.com`` and every write -- including login --
    was rejected as cross-site. An explicit port in ``Host`` still has to
    match, which is the case a direct (non-proxied) browser hits.

    ``allowed_origins`` is *additional*, not exclusive: it used to replace the
    same-origin check entirely, so configuring the WebSocket whitelist broke
    same-origin HTTP from any address not listed in it.
    """
    if not origin:
        return True
    if allowed and origin in allowed:
        return True
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    origin_host = (parsed.hostname or "").lower()
    if not origin_host:
        # ``Origin: null`` (sandboxed iframe, file://) has no host to compare.
        return False
    if not host:
        return False
    host_name, host_port = _split_host(host)
    if not host_name or origin_host != host_name:
        return False
    if host_port is None:
        return True
    try:
        origin_port = parsed.port or _DEFAULT_PORTS.get(parsed.scheme.lower())
    except ValueError:
        return False
    return origin_port == host_port


def forwarded_scheme(conn: Request | WebSocket, settings: Settings) -> str | None:
    """The scheme a *trusted* proxy terminated, or ``None``.

    wsctl starts uvicorn with ``proxy_headers=False`` (its default rewrote the
    client address before the application could check the peer), which also
    removed uvicorn's ``X-Forwarded-Proto`` handling. Without this, a panel
    behind a TLS-terminating proxy builds ``http://`` links -- the share QR
    code generated for a non-browser caller would point at the wrong scheme.
    """
    peer = conn.client.host if conn.client else None
    if not settings.trust_proxy or not peer or not _trusted_proxy_peer(peer, settings):
        return None
    value = conn.headers.get("x-forwarded-proto")
    if not value:
        return None
    # Rightmost hop, like X-Forwarded-For: the header is appended to, so the
    # leftmost entry is whatever the client sent. We already know the peer is a
    # proxy we trust; its own record is the last one.
    scheme = value.split(",")[-1].strip().lower()
    return scheme if scheme in {"http", "https"} else None
