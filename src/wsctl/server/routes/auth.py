"""Login, logout and identity."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from wsctl.core import totp
from wsctl.core.config import Settings
from wsctl.core.store import Store, User

from ..deps import current_user, require_admin, token_from_request
from ..models import LoginRequest
from ..security import COOKIE_NAME, client_ip

router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    store: Store = request.app.state.store
    metrics = request.app.state.metrics
    audit = request.app.state.audit
    limiter = request.app.state.limiter
    ip = client_ip(request, settings)
    key = f"{ip or '-'}:{body.username}"

    if limiter.is_blocked(key):
        retry_after = int(limiter.retry_after(key)) + 1
        metrics.inc("wsctl_logins_total", result="blocked")
        audit.enqueue("login_blocked", ip=ip, payload=body.username)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="尝试次数过多，请稍后再试",
            headers={"Retry-After": str(retry_after)},
        )

    # Argon2 verification is CPU-bound (~tens of ms): keep it off the event
    # loop so a login storm cannot stall every terminal.
    user = await asyncio.to_thread(store.user_authenticate, body.username, body.password)
    if user is None:
        limiter.record_failure(key)
        metrics.inc("wsctl_logins_total", result="failed")
        audit.enqueue("login_failed", ip=ip, payload=body.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误"
        )

    secret = await asyncio.to_thread(store.user_totp_secret, body.username)
    if secret is not None and not totp.verify(secret, body.totp or ""):
        limiter.record_failure(key)
        metrics.inc("wsctl_logins_total", result="totp_failed")
        audit.enqueue("login_totp_failed", user_id=user.id, ip=ip, payload=body.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="一次性验证码错误"
        )

    limiter.reset(key)
    metrics.inc("wsctl_logins_total", result="ok")
    token = await asyncio.to_thread(
        store.create_auth_session,
        user.id,
        ttl=settings.session_ttl,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
    )
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.session_ttl,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    audit.enqueue("login", user_id=user.id, ip=ip)
    return {"username": user.username, "role": user.role}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, bool]:
    token = token_from_request(request)
    if token:
        def revoke() -> int | None:
            store: Store = request.app.state.store
            user = store.resolve_auth_session(token)
            store.delete_auth_session(token)
            return user.id if user else None

        user_id = await asyncio.to_thread(revoke)
        request.app.state.audit.enqueue(
            "logout",
            user_id=user_id,
            ip=client_ip(request, request.app.state.settings),
        )
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
async def me(user: User = Depends(current_user)) -> dict[str, Any]:
    return {"username": user.username, "role": user.role}


# Re-exported so ``app.py`` can wire the admin dependency without importing
# this module's internals.
__all__ = ["require_admin", "router"]
