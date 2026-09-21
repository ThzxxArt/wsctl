"""Security helpers: origin validation, authorization and session cookies."""

from __future__ import annotations

from urllib.parse import urlparse

from wsctl.core.session import TermSession
from wsctl.core.store import User

COOKIE_NAME = "wsctl_session"


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
