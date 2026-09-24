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
from fastapi import Depends, HTTPException, Request, status

from wsctl.core.config import Settings
from wsctl.core.session import SessionManager, TermSession
from wsctl.core.store import Store, User

from .security import COOKIE_NAME


def token_from_request(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(COOKIE_NAME)


def current_user(request: Request) -> User:
    settings: Settings = request.app.state.settings
    store: Store = request.app.state.store
    if not settings.auth_required:
        return User(id=0, username="anonymous", role="admin", disabled=False, created_at=0.0)
    token = token_from_request(request)
    user = store.resolve_auth_session(token) if token else None
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="需要登录"
        )
    return user


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
        "command": None,
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
    from .security import can_access

    manager: SessionManager = request.app.state.manager
    session = manager.get(sid)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
    if not can_access(user, session):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该会话")
    return session
