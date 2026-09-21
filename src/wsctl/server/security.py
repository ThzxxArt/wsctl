"""Security helpers: origin validation, authorization and session cookies."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

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
        "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self' blob:; worker-src 'self' blob:"
    ),
}


def client_ip(conn: Request | WebSocket, settings: Settings) -> str | None:
    """Resolve the client IP, honouring ``X-Forwarded-For`` behind a proxy."""
    if settings.trust_proxy:
        forwarded = conn.headers.get("x-forwarded-for")
        if forwarded:
            first = forwarded.split(",")[0].strip()
            if first:
                return first
    return conn.client.host if conn.client else None


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


def is_origin_allowed(origin: str | None, host: str | None, allowed: list[str]) -> bool:
    """Validate a WebSocket/HTTP ``Origin`` against an allowlist.

    A missing ``Origin`` means a non-browser client (e.g. the CLI); such
    requests are permitted here but must still authenticate.
    """
    if not origin:
        return True
    if allowed:
        return origin in allowed
    if not host:
        return False
    return urlparse(origin).netloc == host
