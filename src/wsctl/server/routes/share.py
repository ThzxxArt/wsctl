"""Session share links and their QR codes."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from wsctl.core.store import User

from ..deps import current_user, owned_session, qr_svg
from ..models import SessionShare
from ..security import is_origin_allowed

router = APIRouter(prefix="/api", tags=["share"])


@router.get("/sessions/{sid}/share")
async def get_share(
    sid: str, request: Request, user: User = Depends(current_user)
) -> dict[str, Any]:
    """Return the active share token so the UI can reuse an existing link."""
    session = owned_session(request, sid, user)
    token = session.peek_share()
    if token is None:
        return {"shared": False}
    return {"shared": True, "token": token, "writable": session.share_writable}


@router.post("/sessions/{sid}/share")
async def create_share(
    sid: str, body: SessionShare, request: Request, user: User = Depends(current_user)
) -> dict[str, Any]:
    session = owned_session(request, sid, user)
    token = session.create_share(
        ttl=float(body.ttl) if body.ttl else None, writable=body.writable
    )
    # Persist so the link survives a restart (the same promise the session
    # itself makes on the tmux backend).
    await asyncio.to_thread(
        request.app.state.store.term_session_set_share,
        sid,
        token=token,
        expires=session.share_expiry,
        writable=body.writable,
    )
    request.app.state.audit.enqueue(
        "session_share",
        user_id=user.id,
        term_session_id=sid,
        payload="write" if body.writable else "read",
    )
    return {
        "token": token,
        "ttl": body.ttl,
        "writable": body.writable,
        "expires_at": session.share_expiry,
    }


@router.delete("/sessions/{sid}/share")
async def revoke_share(
    sid: str, request: Request, user: User = Depends(current_user)
) -> dict[str, bool]:
    session = owned_session(request, sid, user)
    session.revoke_share()
    await asyncio.to_thread(request.app.state.store.term_session_set_share, sid, token=None)
    request.app.state.audit.enqueue("session_unshare", user_id=user.id, term_session_id=sid)
    return {"ok": True}


@router.get("/sessions/{sid}/qr.svg")
async def share_qr(
    request: Request, sid: str, origin: str = "", user: User = Depends(current_user)
) -> Response:
    session = owned_session(request, sid, user)
    token = session.peek_share()
    if token is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="该会话未分享")
    settings = request.app.state.settings
    allowed = is_origin_allowed(origin, request.headers.get("host"), settings.allowed_origins)
    base = origin if (origin and allowed) else str(request.base_url).rstrip("/")
    url = f"{base}/?session={sid}&share={token}"
    # ``qr_svg`` already emits a standalone SVG document (with xmlns), so it
    # renders both inside an ``<img src>`` and via ``innerHTML``.
    return Response(content=qr_svg(url), media_type="image/svg+xml")
