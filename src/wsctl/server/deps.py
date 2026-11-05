"""Dependency injection and helpers shared by every route module.

Routes reach application state through ``request.app.state`` rather than
closing over names bound in ``create_app``: that is what lets the endpoints
live in their own modules without a web of cross-imports.
"""

from __future__ import annotations

import asyncio
import io
import re
from typing import Any

import segno
from fastapi import Depends, HTTPException, Request, WebSocket, status

from wsctl.core.config import Settings
from wsctl.core.session import SessionManager, TermSession
from wsctl.core.store import Store, User, hash_token

from .security import COOKIE_NAME


def resolve_token_user(
    request: Request | WebSocket, token: str | None, *, allow_api_key: bool = True
) -> User | None:
    """Resolve a login token or an API key to a user.

    API keys are checked *before* auth sessions only when they carry the
    ``wsctl_`` prefix; both are hashed, so a stolen database row cannot be
    replayed as a credential. ``allow_api_key`` is false for a token that came
    from a cookie: an API key is a bearer credential and must never be accepted
    from a path where its scopes are not visible to the request middleware.
    """
    if not token:
        return None
    store: Store = request.app.state.store
    db = getattr(request.app.state, "db", None)
    if allow_api_key and db is not None and token.startswith("wsctl_"):
        record = db.api_key_resolve(hash_token(token))
        if record is not None:
            user = store.user_get_by_id(int(record["user_id"]))
            if user is not None and not user.disabled:
                return user
    return store.resolve_auth_session(token)


def token_source(request: Request | WebSocket) -> tuple[str | None, str | None]:
    """Return ``(token, source)`` where source is ``bearer`` or ``cookie``.

    Duplicate ``Authorization`` headers are ambiguous: the scope middleware and
    Starlette used to read different values (last vs first), which let a
    read-only key ride alongside a bogus second header. Treat more than one as
    no bearer credential at all.
    """
    values = request.headers.getlist("authorization")
    if len(values) > 1:
        return None, "ambiguous"
    auth = values[0] if values else None
    if auth and auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token:
            return token, "bearer"
    cookie = request.cookies.get(COOKIE_NAME)
    return (cookie, "cookie") if cookie else (None, None)


def token_from_request(request: Request | WebSocket) -> str | None:
    return token_source(request)[0]


def current_user(request: Request) -> User:
    cached = getattr(request.state, "user", None)
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    settings: Settings = request.app.state.settings
    if not settings.auth_required:
        user = User(id=0, username="anonymous", role="admin", disabled=False, created_at=0.0)
        request.state.user = user
        return user
    token, source = token_source(request)
    if source == "ambiguous":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization 头重复"
        )
    if source == "cookie" and token is not None and token.startswith("wsctl_"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key 只能通过 Authorization 头使用",
        )
    resolved: User | None = resolve_token_user(
        request, token, allow_api_key=(source == "bearer")
    )
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="需要登录"
        )
    # Idempotency keys are scoped by principal; storing the resolved user here
    # avoids a second token lookup and keeps a key from replaying another
    # account's response.
    request.state.user = resolved
    request.state.token = token
    request.state.token_source = source
    return resolved


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


def qr_svg(text: str) -> str:
    """Render ``text`` as a standalone SVG QR code (for TOTP provisioning).

    segno emits ``width="45" height="45"`` (module pixels) and **no
    viewBox**. Loaded through ``<img>`` that is harmless -- a replaced
    element scales the whole *document* to its box, which is why the share
    code always looked right. As an *inline* ``<svg>`` it is not: CSS
    ``width/height: 100%`` enlarges only the viewport, the user units stay
    1:1 with pixels, and the code keeps drawing at 45px in the corner of a
    200px card. The viewBox is what ties user units to the viewport -- add
    it, and the code finally scales with its container.
    """
    buffer = io.BytesIO()
    segno.make(text, error="m").save(buffer, kind="svg")
    svg = buffer.getvalue().decode("utf-8")
    if "viewBox" not in svg and "viewbox" not in svg:
        match = re.search(r'width="(\d+)"\s+height="(\d+)"', svg)
        if match:
            svg = re.sub(
                r"<svg\b",
                f'<svg viewBox="0 0 {match.group(1)} {match.group(2)}"',
                svg,
                count=1,
            )
    return svg


def render_setting(value: Any) -> str:
    """Render a setting for display without leaking a raw Python repr."""
    from pathlib import Path

    if value is None:
        return ""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        import json

        return json.dumps(value, ensure_ascii=False)
    return str(value)


def serialize_session(
    session: TermSession, owners: dict[int, User] | None = None
) -> dict[str, Any]:
    """JSON view of a live session.

    ``owners`` is a pre-resolved ``id -> User`` map so a list of N sessions
    costs one query instead of N. Looked up per session this was 64 synchronous
    SQLite round trips on the event loop -- the exact class of work 0.1.2 and
    0.1.4 claimed to have moved off it.
    """
    owner_name: str | None = None
    if session.owner_id is not None:
        owner = (owners or {}).get(session.owner_id)
        owner_name = owner.username if owner is not None else None
    return {
        "id": session.id,
        "name": session.spec.name,
        "pid": session.pid,
        "owner_id": session.owner_id,
        "owner": owner_name,
        "backend": session.backend,
        "shared": session.is_shared,
        "share_writable": session.share_writable,
        "recording": session.is_recording,
        "clients": session.client_count,
        "alive": session.is_alive,
        "created_at": session.created_at,
        "last_active": session.last_active,
        "bytes": session.memory_usage(),
        # Present on both the live and the history path so ``/detail`` has one
        # shape regardless of whether the session is still running. Consumers
        # should not have to branch on "is it alive" just to read argv.
        "duration": max(0.0, session.last_active - session.created_at),
        "command": session.spec.command,
        "argv": list(session.spec.argv),
        "cwd": session.spec.cwd,
        "status": "running" if session.is_alive else "stopped",
        "ended_reason": "" if session.is_alive else "已结束",
    }


async def resolve_owners(request: Request, sessions: list[TermSession]) -> dict[int, User]:
    """One batched owner lookup, off the event loop."""
    ids = {s.owner_id for s in sessions if s.owner_id is not None}
    if not ids:
        return {}
    store: Store = request.app.state.store
    return await asyncio.to_thread(store.users_by_ids, ids)


def owned_session(request: Request, sid: str, user: User) -> TermSession:
    """Fetch a session the user may act on, or raise a 404/403."""
    from typing import cast

    from .security import can_access

    manager: SessionManager = request.app.state.manager
    session = manager.get(sid)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
    if not can_access(user, session):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该会话")
    return cast(TermSession, session)
