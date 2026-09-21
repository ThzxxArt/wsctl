"""Security helpers: origin validation and session cookies."""

from __future__ import annotations

from urllib.parse import urlparse

COOKIE_NAME = "wsctl_session"


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
