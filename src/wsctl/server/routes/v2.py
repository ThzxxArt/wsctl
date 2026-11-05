"""0.2.0-only API surface.

The 0.1.x routers are mounted under both ``/api`` and ``/api/v2`` for parity;
the endpoints here have no 0.1.x equivalent:

* multi-link shares with scopes;
* API keys;
* workspace layouts;
* Server-Sent Events for state changes;
* command blocks;
* audit hash-chain verification;
* Web Push subscriptions;
* configuration schema metadata.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from wsctl.core import config_v2
from wsctl.core.database import Database
from wsctl.core.store import User, hash_token
from wsctl.server.deps import (
    current_user,
    owned_session,
    require_admin,
    resolve_token_user,
    token_source,
)
from wsctl.server.security import client_ip
from wsctl.server.services.events import format_sse
from wsctl.server.services.shares import SCOPES, ShareService

router = APIRouter(tags=["v2"])


class ShareCreateRequest(BaseModel):
    scope: str = Field(default="read", max_length=16)
    label: str = Field(default="", max_length=80)
    ttl: int | None = Field(default=None, ge=1, le=365 * 24 * 3600)


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(default="", max_length=80)
    scopes: list[str] = Field(default_factory=list, max_length=16)
    expires_in: int | None = Field(default=None, ge=60, le=10 * 365 * 24 * 3600)


class LayoutRequest(BaseModel):
    name: str = Field(default="default", max_length=80)
    layout: dict[str, Any] = Field(default_factory=dict)


class PushSubscriptionRequest(BaseModel):
    endpoint: str = Field(min_length=1, max_length=4096)
    keys: dict[str, Any] = Field(default_factory=dict)


class PushUnsubscribeRequest(BaseModel):
    endpoint: str = Field(min_length=1, max_length=4096)


def _shares(request: Request) -> ShareService:
    from typing import cast

    return cast(ShareService, request.app.state.shares)


# -- shares ------------------------------------------------------------


@router.get("/shares/resolve")
async def resolve_share(token: str, request: Request) -> dict[str, Any]:
    """Resolve a share token to its session and scope.

    This is intentionally reachable without a login: the token *is* the
    credential, and a share link opened in a fresh browser has no cookie yet.
    It returns no session name, command, or output -- only what the caller
    needs to open a WebSocket.
    """
    if not token or len(token) > 512:
        raise HTTPException(status_code=400, detail="分享令牌无效")
    record = await asyncio.to_thread(_shares(request).resolve, token)
    if record is None:
        # A 0.1.x share token lives on the live session, not in the v2 table.
        # Resolve it too so an old link still opens after the upgrade.
        for session in request.app.state.manager.list_sessions():
            scope = session.legacy_share_scope(token)
            if scope is not None:
                request.app.state.audit.enqueue(
                    "share_resolve",
                    term_session_id=session.id,
                    ip=client_ip(request, request.app.state.settings),
                    payload=scope,
                )
                return {"session": session.id, "scope": scope}
        raise HTTPException(status_code=404, detail="分享链接无效或已过期")
    request.app.state.audit.enqueue(
        "share_resolve",
        term_session_id=record.term_session_id,
        ip=client_ip(request, request.app.state.settings),
        payload=record.scope,
    )
    return {"session": record.term_session_id, "scope": record.scope}


@router.get("/sessions/{sid}/shares")
async def list_shares(
    sid: str, request: Request, user: User = Depends(current_user)
) -> dict[str, Any]:
    owned_session(request, sid, user)
    return {"shares": await asyncio.to_thread(_shares(request).list, sid)}


@router.post("/sessions/{sid}/shares", status_code=status.HTTP_201_CREATED)
async def create_share(
    sid: str,
    body: ShareCreateRequest,
    request: Request,
    user: User = Depends(current_user),
) -> dict[str, Any]:
    session = owned_session(request, sid, user)
    if body.scope not in SCOPES:
        raise HTTPException(status_code=400, detail=f"scope 必须是 {', '.join(SCOPES)}")
    existing = await asyncio.to_thread(_shares(request).list, sid)
    if len(existing) >= 64:
        raise HTTPException(
            status_code=409,
            detail="该会话的分享链接已达上限（64），请先撤销不再使用的链接",
        )
    token, record = await asyncio.to_thread(
        _shares(request).create,
        sid,
        scope=body.scope,
        label=body.label,
        ttl=body.ttl,
        created_by=user.id,
    )
    request.app.state.audit.enqueue(
        "share_create",
        user_id=user.id,
        term_session_id=session.id,
        payload=f"{body.scope}:{body.label}"[:512],
    )
    request.app.state.events.publish(
        "share_created",
        owner_id=session.owner_id,
        session=sid,
        share=record.id,
        scope=record.scope,
    )
    from ..deps import qr_svg
    from ..security import forwarded_scheme

    scheme = forwarded_scheme(request, request.app.state.settings) or request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    share_url = f"{scheme}://{host}/s/{token}"
    return {
        "id": record.id,
        "token": token,
        "url": f"/s/{token}",
        "absolute_url": share_url,
        "qr_svg": qr_svg(share_url),
        "share": record.to_dict(),
    }


@router.delete("/shares/{share_id}")
async def revoke_share(
    share_id: int, request: Request, user: User = Depends(current_user)
) -> dict[str, bool]:
    service = _shares(request)
    db: Database = request.app.state.db
    record = await asyncio.to_thread(db.share_get, share_id)
    if record is None:
        raise HTTPException(status_code=404, detail="分享不存在")
    row = await asyncio.to_thread(
        request.app.state.store.term_session_get, record.term_session_id
    )
    owner_id = row.get("owner_id") if row else None
    if user.role != "admin" and owner_id != user.id:
        raise HTTPException(status_code=403, detail="无权访问该分享")
    revoked = await asyncio.to_thread(service.revoke, share_id)
    # A 0.1.x token was also copied into the legacy column/table by migration.
    # Revoking the v2 row must not leave that older authority alive.
    session = request.app.state.manager.get(record.term_session_id)
    if session is not None:
        session.revoke_share()
    await asyncio.to_thread(
        request.app.state.store.term_session_set_share,
        record.term_session_id,
        token=None,
    )
    request.app.state.audit.enqueue(
        "share_revoke",
        user_id=user.id,
        term_session_id=record.term_session_id,
        payload=str(share_id),
    )
    request.app.state.events.publish(
        "share_revoked",
        owner_id=owner_id,
        session=record.term_session_id,
        share=share_id,
    )
    return {"revoked": revoked}


# -- API keys ----------------------------------------------------------


@router.get("/api-keys")
async def list_api_keys(
    request: Request, user: User = Depends(current_user)
) -> dict[str, Any]:
    db: Database = request.app.state.db
    rows = await asyncio.to_thread(db.api_key_list, None if user.role == "admin" else user.id)
    return {"keys": rows}


@router.post("/api-keys", status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: ApiKeyCreateRequest,
    request: Request,
    user: User = Depends(current_user),
) -> dict[str, Any]:
    token = "wsctl_" + secrets.token_urlsafe(32)
    expires_at = time.time() + body.expires_in if body.expires_in else None
    invalid = sorted(set(body.scopes) - {"read", "write", "admin"})
    if invalid:
        raise HTTPException(
            status_code=400, detail=f"未知的 API Key scope：{', '.join(invalid)}"
        )
    if "admin" in body.scopes and user.role != "admin":
        raise HTTPException(
            status_code=403, detail="只有管理员可以创建带 admin scope 的 API Key"
        )
    db: Database = request.app.state.db
    existing = await asyncio.to_thread(db.api_key_list, user.id)
    if len(existing) >= 100:
        raise HTTPException(
            status_code=409, detail="API Key 数量已达上限（100），请先删除不用的 Key"
        )
    record = await asyncio.to_thread(
        db.api_key_create,
        user.id,
        body.name,
        hash_token(token),
        scopes=body.scopes,
        expires_at=expires_at,
    )
    request.app.state.audit.enqueue(
        "api_key_create", user_id=user.id, payload=body.name[:256]
    )
    return {"token": token, "key": record}


@router.delete("/api-keys/{key_id}")
async def delete_api_key(
    key_id: int, request: Request, user: User = Depends(current_user)
) -> dict[str, bool]:
    db: Database = request.app.state.db
    owner = None if user.role == "admin" else user.id
    deleted = await asyncio.to_thread(db.api_key_delete, key_id, user_id=owner)
    if not deleted:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    request.app.state.audit.enqueue(
        "api_key_delete", user_id=user.id, payload=str(key_id)
    )
    return {"deleted": True}


# -- layouts -----------------------------------------------------------


@router.get("/layouts")
async def list_layouts(
    request: Request, user: User = Depends(current_user)
) -> dict[str, Any]:
    return {"layouts": await asyncio.to_thread(request.app.state.db.layout_list, user.id)}


@router.get("/layouts/{name}")
async def get_layout(
    name: str, request: Request, user: User = Depends(current_user)
) -> dict[str, Any]:
    if len(name) > 80:
        raise HTTPException(status_code=400, detail="布局名称过长")
    row = await asyncio.to_thread(request.app.state.db.layout_get, user.id, name)
    if row is None:
        raise HTTPException(status_code=404, detail="布局不存在")
    from typing import cast

    return cast(dict[str, Any], row)


@router.put("/layouts/{name}")
async def put_layout(
    name: str,
    body: LayoutRequest,
    request: Request,
    user: User = Depends(current_user),
) -> dict[str, Any]:
    if not name or len(name) > 80:
        raise HTTPException(status_code=400, detail="布局名称无效")
    if len(json.dumps(body.layout, ensure_ascii=False).encode("utf-8")) > 256 * 1024:
        raise HTTPException(status_code=413, detail="布局过大")
    existing = await asyncio.to_thread(request.app.state.db.layout_list, user.id)
    if len(existing) >= 100 and not any(item["name"] == name for item in existing):
        raise HTTPException(status_code=409, detail="布局数量已达上限（100）")
    await asyncio.to_thread(request.app.state.db.layout_put, user.id, name, body.layout)
    return {"saved": True, "name": name}


@router.delete("/layouts/{name}")
async def delete_layout(
    name: str, request: Request, user: User = Depends(current_user)
) -> dict[str, bool]:
    if len(name) > 80:
        raise HTTPException(status_code=400, detail="布局名称过长")
    deleted = await asyncio.to_thread(request.app.state.db.layout_delete, user.id, name)
    if not deleted:
        raise HTTPException(status_code=404, detail="布局不存在")
    return {"deleted": True}


# -- command blocks ----------------------------------------------------


@router.get("/sessions/{sid}/commands")
async def session_commands(
    sid: str,
    request: Request,
    limit: int = 100,
    user: User = Depends(current_user),
) -> dict[str, Any]:
    session = owned_session(request, sid, user)
    page = min(max(limit, 1), 1000)
    live = getattr(session, "command_blocks", None)
    rows = live(page) if callable(live) else []
    stored = await asyncio.to_thread(request.app.state.db.command_blocks, sid, page)
    if stored:
        # Merge instead of "stored wins": a command that just finished is in
        # the live parser but its row may still be on the worker thread.
        seen = {str(item.get("id")) for item in stored}
        merged = list(stored) + [
            item for item in rows if str(item.get("id")) not in seen
        ]
        merged.sort(key=lambda item: float(item.get("started_at") or 0))
        return {"commands": merged[:page]}
    return {"commands": rows}


# -- audit -------------------------------------------------------------


@router.get("/audit/verify")
async def verify_audit(
    request: Request, _: User = Depends(require_admin)
) -> dict[str, Any]:
    return await asyncio.to_thread(request.app.state.store.verify_audit)


# -- SSE ---------------------------------------------------------------


def _event_visible(message: dict[str, Any], user: User) -> bool:
    """Whether ``user`` may see one broker message.

    The broker is process-wide, but the sidebar is per-user. Broadcasting every
    ``presence``/``session_ended`` event to every authenticated subscriber
    leaked other users' session ids, names and viewer names.
    """
    if user.role == "admin" or message.get("event") == "heartbeat":
        return True
    owner_id = message.get("owner_id")
    return owner_id is not None and int(owner_id) == user.id


@router.get("/events")
async def events_endpoint(
    request: Request, user: User = Depends(current_user)
) -> EventSourceResponse:
    broker = request.app.state.events
    queue = await broker.subscribe()
    settings = request.app.state.settings
    token, source = token_source(request)
    current = user
    last_check = time.monotonic()

    async def generator() -> AsyncIterator[str]:
        nonlocal current, last_check
        try:
            async for message in broker.stream(queue):
                # An SSE subscription is long-lived; the role/disabled state at
                # connect time is not a permission for the next hour. Re-resolve
                # periodically and stop as soon as the session is gone.
                now = time.monotonic()
                if settings.auth_required and now - last_check >= 5.0:
                    last_check = now
                    fresh = await asyncio.to_thread(
                        resolve_token_user,
                        request,
                        token,
                        allow_api_key=(source == "bearer"),
                    )
                    if fresh is None:
                        return
                    current = fresh
                if _event_visible(message, current):
                    yield format_sse(message)
        finally:
            await broker.unsubscribe(queue)

    return EventSourceResponse(generator())


# -- push --------------------------------------------------------------


@router.get("/push/vapid")
async def push_vapid(request: Request) -> dict[str, Any]:
    push = request.app.state.push
    return {"enabled": push.enabled, "public_key": push.public_key()}


@router.post("/push/subscribe", status_code=status.HTTP_201_CREATED)
async def push_subscribe(
    body: PushSubscriptionRequest,
    request: Request,
    user: User = Depends(current_user),
) -> dict[str, Any]:
    push = request.app.state.push
    if not push.enabled:
        raise HTTPException(
            status_code=409,
            detail="Web Push 未启用：请先配置 VAPID 公钥/私钥并开启 web_push_enabled",
        )
    try:
        record = await asyncio.to_thread(push.subscribe, user.id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"subscription": record}


@router.delete("/push/subscribe")
async def push_unsubscribe(
    body: PushUnsubscribeRequest,
    request: Request,
    user: User = Depends(current_user),
) -> dict[str, bool]:
    removed = await asyncio.to_thread(
        request.app.state.push.unsubscribe, user.id, body.endpoint
    )
    return {"removed": removed}


# -- config schema -----------------------------------------------------


@router.get("/config/schema")
async def config_schema(
    request: Request, _: User = Depends(require_admin)
) -> dict[str, Any]:
    from wsctl.core.config import Settings

    settings = request.app.state.settings
    values = settings.model_dump()
    metadata = config_v2.sectioned_metadata(Settings.model_fields)
    sections: dict[str, list[dict[str, Any]]] = {name: [] for name in config_v2.SECTIONS}
    for item in metadata:
        if item["key"] in config_v2.SECRET_KEYS:
            item["value"] = config_v2.SECRET_PLACEHOLDER
            item["secret"] = True
        else:
            item["value"] = _serialise(values.get(item["key"]))
        sections.setdefault(item["section"], []).append(item)
    return {
        "version": 2,
        "config_path": str(settings.config_path),
        "section_titles": config_v2.SECTION_TITLES,
        "sections": sections,
    }


def _serialise(value: Any) -> Any:
    from pathlib import Path

    if isinstance(value, Path):
        return str(value)
    return value


__all__ = ["router"]
