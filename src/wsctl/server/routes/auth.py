"""Login, logout and identity."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from wsctl.core import totp
from wsctl.core.config import Settings
from wsctl.core.ratelimit import RateLimiter
from wsctl.core.store import Store, User

from ..deps import current_user, require_admin, token_from_request
from ..models import LoginRequest
from ..security import COOKIE_NAME, client_ip, forwarded_scheme

router = APIRouter(tags=["auth"])

#: How long one login may wait for an Argon2 slot before the server says it is
#: busy. Verification takes tens of milliseconds; a login that waited ten
#: seconds is a login storm, and answering 503 is more honest (and cheaper)
#: than letting requests pile up behind the CPU.
LOGIN_VERIFY_TIMEOUT = 10.0


def _blocked_retry_after(limiters: list[tuple[RateLimiter, str]]) -> int:
    """Worst retry-after across the limiters that are currently blocking.

    ``0`` means "not blocked". It used to be ``int(0) + 1`` unconditionally, so
    the very first login of an untouched server answered 429.
    """
    worst = 0.0
    blocked = False
    for limiter, key in limiters:
        if limiter.is_blocked(key):
            blocked = True
            worst = max(worst, limiter.retry_after(key))
    return int(worst) + 1 if blocked else 0


@router.post("/login")
@router.post("/auth/login")
async def login(body: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    store: Store = request.app.state.store
    metrics = request.app.state.metrics
    audit = request.app.state.audit
    limiter = request.app.state.limiter
    ip_limiter = request.app.state.ip_limiter
    ip = client_ip(request, settings)
    key = f"{ip or '-'}:{body.username}"
    ip_key = ip or "-"

    # Two budgets, because they defend different things. ``key`` stops one
    # account from being brute-forced; ``ip_key`` stops one host from turning
    # a *list* of usernames into an unbounded number of Argon2 verifications.
    # Only the account key is cleared by a success -- otherwise one valid
    # credential would reset the attacker's host budget, which is exactly the
    # knob the per-IP limit exists to bound.
    retry_after = _blocked_retry_after([(limiter, key), (ip_limiter, ip_key)])
    if retry_after:
        metrics.inc("wsctl_logins_total", result="blocked")
        audit.enqueue("login_blocked", ip=ip, payload=body.username)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="尝试次数过多，请稍后再试",
            headers={"Retry-After": str(retry_after)},
        )

    # Argon2 verification is CPU-bound (~tens of ms): keep it off the event
    # loop so a login storm cannot stall every terminal. The bounded wait is
    # what stops a storm from becoming an unbounded queue of threads holding
    # 100 MB bodies: the body cap already ran, and this answers 503 instead of
    # waiting forever for a slot.
    #
    # ``login_slots`` is acquired *here*, in the event loop, before a thread is
    # requested. The process-wide semaphore inside ``passwords`` caps CPU, but
    # a thread blocked on it still occupies an executor slot; a storm could fill
    # the default executor with waiters and starve every other ``to_thread``
    # call (store queries, backups) while the CPU idled between lanes.
    slots = request.app.state.login_slots
    try:
        async with asyncio.timeout(LOGIN_VERIFY_TIMEOUT):
            async with slots:
                user = await asyncio.to_thread(
                    store.user_authenticate, body.username, body.password
                )
    except TimeoutError:
        metrics.inc("wsctl_logins_total", result="busy")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="服务繁忙，请稍后再试",
        ) from None
    if user is None:
        limiter.record_failure(key)
        ip_limiter.record_failure(ip_key)
        metrics.inc("wsctl_logins_total", result="failed")
        audit.enqueue("login_failed", ip=ip, payload=body.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误"
        )

    secret = await asyncio.to_thread(store.user_totp_secret, body.username)
    if secret is not None:
        if not body.totp:
            # This is a second step, not a failed credential. Tell the client
            # explicitly so it can reveal the code field without painting a
            # red "login failed" error over a perfectly good password.
            metrics.inc("wsctl_logins_total", result="totp_required")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="请输入一次性验证码",
                headers={"X-Wsctl-Totp-Required": "1"},
            )
        if not totp.verify(secret, body.totp):
            limiter.record_failure(key)
            ip_limiter.record_failure(ip_key)
            metrics.inc("wsctl_logins_total", result="totp_failed")
            audit.enqueue(
                "login_totp_failed", user_id=user.id, ip=ip, payload=body.username
            )
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
        # A cookie served over HTTPS must not be allowed to travel over plain
        # HTTP just because an old config left cookie_secure at its false
        # default. Trust the request's actual scheme (including a trusted
        # X-Forwarded-Proto) as well as the explicit setting.
        secure=(
            settings.cookie_secure
            or request.url.scheme == "https"
            or forwarded_scheme(request, settings) == "https"
        ),
        samesite="lax",
        path="/",
    )
    audit.enqueue("login", user_id=user.id, ip=ip)
    return {"username": user.username, "role": user.role}


@router.post("/logout")
@router.post("/auth/logout")
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
@router.get("/auth/me")
async def me(user: User = Depends(current_user)) -> dict[str, Any]:
    return {"username": user.username, "role": user.role}


# Re-exported so ``app.py`` can wire the admin dependency without importing
# this module's internals.
__all__ = ["require_admin", "router"]
