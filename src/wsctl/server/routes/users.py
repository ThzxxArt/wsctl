"""User administration and TOTP enrolment (admin only)."""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from wsctl.core import totp, user_admin
from wsctl.core.store import User

from ..deps import qr_svg, require_admin
from ..models import UserCreate, UserUpdate

router = APIRouter(prefix="/api", tags=["users"])


@router.get("/users")
async def list_users(request: Request, _: User = Depends(require_admin)) -> list[dict[str, Any]]:
    def snapshot() -> list[dict[str, Any]]:
        store_ = request.app.state.store
        return [
            {
                "id": u.id,
                "username": u.username,
                "role": u.role,
                "disabled": u.disabled,
                "totp": store_.user_totp_secret(u.username) is not None,
            }
            for u in store_.user_list()
        ]

    return await asyncio.to_thread(snapshot)


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate, request: Request, actor: User = Depends(require_admin)
) -> dict[str, Any]:
    try:
        user = await asyncio.to_thread(
            user_admin.create,
            request.app.state.store,
            body.username,
            body.password,
            body.role,
        )
    except user_admin.UserAdminError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="用户已存在"
        ) from exc
    request.app.state.audit.enqueue(
        "user_create", user_id=actor.id, payload=f"{user.username}:{user.role}"
    )
    return {"id": user.id, "username": user.username, "role": user.role}


@router.delete("/users/{username}")
async def delete_user(
    username: str, request: Request, actor: User = Depends(require_admin)
) -> dict[str, bool]:
    try:
        await asyncio.to_thread(
            user_admin.delete, request.app.state.store, username, actor=actor
        )
    except user_admin.UserAdminError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    request.app.state.audit.enqueue("user_delete", user_id=actor.id, payload=username)
    return {"ok": True}


@router.patch("/users/{username}")
async def update_user(
    username: str, body: UserUpdate, request: Request, actor: User = Depends(require_admin)
) -> dict[str, bool]:
    if body.role is None and body.disabled is None and body.password is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="没有需要更新的字段"
        )
    store_ = request.app.state.store
    changed: list[str] = []
    try:
        if body.role is not None:
            await asyncio.to_thread(
                user_admin.set_role, store_, username, body.role, actor=actor
            )
            changed.append(f"role={body.role}")
        if body.disabled is not None:
            await asyncio.to_thread(
                user_admin.set_disabled, store_, username, body.disabled, actor=actor
            )
            changed.append(f"disabled={body.disabled}")
        if body.password is not None:
            # Password hashing is CPU-bound; revokes existing logins.
            await asyncio.to_thread(
                user_admin.set_password, store_, username, body.password, actor=actor
            )
            changed.append("password")
    except user_admin.UserAdminError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    request.app.state.audit.enqueue(
        "user_update", user_id=actor.id, payload=f"{username}:{','.join(changed)}"
    )
    return {"ok": True}


@router.post("/users/{username}/totp")
async def enable_totp(
    username: str, request: Request, actor: User = Depends(require_admin)
) -> dict[str, str]:
    settings = request.app.state.settings
    if request.app.state.store.user_get(username) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    secret = totp.generate_secret()
    request.app.state.store.user_set_totp(username, secret)
    request.app.state.audit.enqueue("user_totp_on", user_id=actor.id, payload=username)
    uri = totp.provisioning_uri(secret, username, issuer=settings.totp_issuer)
    return {"secret": secret, "uri": uri, "qr_svg": qr_svg(uri)}


@router.delete("/users/{username}/totp")
async def disable_totp(
    username: str, request: Request, actor: User = Depends(require_admin)
) -> dict[str, bool]:
    if not request.app.state.store.user_clear_totp(username):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    request.app.state.audit.enqueue("user_totp_off", user_id=actor.id, payload=username)
    return {"ok": True}
