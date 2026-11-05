"""API-key scope enforcement.

An API key is a bearer credential, and a credential that says ``read`` but can
still open a shell is a lie. The middleware resolves a ``wsctl_...`` token
before the route runs and maps the request to one of three scopes:

* ``read``  -- safe methods (GET/HEAD/OPTIONS);
* ``write`` -- everything else under sessions/files/shares/recordings/layouts;
* ``admin`` -- user management, config, audit, API keys and overview.

An empty scope list means "inherit the user's full permissions", which is the
same contract as a browser session cookie; this keeps existing keys useful and
is documented. A key that *does* carry scopes is enforced strictly.
"""

from __future__ import annotations

import asyncio
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from wsctl.core.store import hash_token

ADMIN_PREFIXES = (
    "/users",
    "/config",
    "/audit",
    "/api-keys",
    "/overview",
)
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _api_path(scope: Scope) -> str:
    path = str(scope.get("path", ""))
    for prefix in ("/api/v2", "/api"):
        if path == prefix or path.startswith(prefix + "/"):
            return path[len(prefix) :] or "/"
    return path


def required_scope(scope: Scope) -> str:
    path = _api_path(scope)
    if any(path == prefix or path.startswith(prefix + "/") for prefix in ADMIN_PREFIXES):
        return "admin"
    # The legacy share endpoints *return the plaintext token* (and a QR code
    # containing it). Treating them as a plain GET let a read-only API key
    # harvest a writable share link and type through it, which defeats the
    # read scope entirely.
    if path.startswith("/sessions/") and (
        path.endswith("/share") or path.endswith("/qr.svg")
    ):
        return "write"
    if str(scope.get("method", "GET")).upper() in SAFE_METHODS:
        return "read"
    return "write"


class ApiKeyScopeMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw = headers.get(b"authorization", b"").decode("latin-1").strip()
        if not raw.lower().startswith("bearer "):
            await self.app(scope, receive, send)
            return
        token = raw[7:].strip()
        if not token.startswith("wsctl_"):
            await self.app(scope, receive, send)
            return
        app = scope.get("app")
        database = getattr(getattr(app, "state", None), "db", None)
        if database is None:
            await self.app(scope, receive, send)
            return
        record = await asyncio.to_thread(database.api_key_resolve, hash_token(token))
        if record is None:
            # The route's auth dependency will produce the 401 with a body.
            await self.app(scope, receive, send)
            return
        scopes = set(record.get("scopes") or [])
        if not scopes:
            await self.app(scope, receive, send)
            return
        needed = required_scope(scope)
        allowed = needed in scopes or "admin" in scopes or (
            needed == "read" and "write" in scopes
        )
        if allowed:
            await self.app(scope, receive, send)
            return
        body: dict[str, Any] = {
            "type": "about:blank",
            "title": "API Key 权限不足",
            "status": 403,
            "detail": f"该 API Key 没有 {needed} 权限（现有：{', '.join(sorted(scopes))}）",
            "instance": scope.get("path", ""),
        }
        response = JSONResponse(body, status_code=403, media_type="application/problem+json")
        await response(scope, receive, send)


__all__ = ["ApiKeyScopeMiddleware", "required_scope"]
