"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import os
import shlex
import socket
import sqlite3
import sys
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import segno
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    status,
)
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import RequestResponseEndpoint

from wsctl import __version__
from wsctl.core import fs as fs_mod
from wsctl.core import recording as recording_mod
from wsctl.core import ssh, tmux, totp
from wsctl.core.audit import AuditWriter
from wsctl.core.config import Settings, reload_settings_file
from wsctl.core.metrics import Metrics
from wsctl.core.pty import PtyError
from wsctl.core.ratelimit import RateLimiter
from wsctl.core.session import SessionManager, SessionSpec, TermSession, within_user_quota
from wsctl.core.store import Store, User
from wsctl.core.webhook import WebhookDispatcher

from .security import (
    COOKIE_NAME,
    SECURITY_HEADERS,
    can_access,
    client_ip,
    ip_allowed,
    is_origin_allowed,
)
from .ws import terminal_endpoint

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
MAINTENANCE_INTERVAL = 5.0

log = logging.getLogger("wsctl.app")


class _UploadTooLarge(Exception):
    """Raised when an upload exceeds ``file_max_upload``."""


def _copy_upload(src: Any, fd: int, limit: int, chunk_size: int = 1 << 20) -> int:
    """Copy an uploaded file to ``fd`` on a worker thread (blocking I/O).

    ``fd`` is always closed. Raises :class:`_UploadTooLarge` past ``limit``.
    """
    size = 0
    out = os.fdopen(fd, "wb")
    try:
        while True:
            chunk = src.read(chunk_size)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise _UploadTooLarge
            out.write(chunk)
        return size
    finally:
        out.close()


class LoginRequest(BaseModel):
    username: str
    password: str
    totp: str | None = None


class SessionCreate(BaseModel):
    name: str | None = None
    command: str | None = None
    cwd: str | None = None
    backend: str | None = None
    ssh: SshConfig | None = None
    cols: int = Field(default=80, ge=1, le=1000)
    rows: int = Field(default=24, ge=1, le=1000)


class SessionRename(BaseModel):
    name: str


class SessionShare(BaseModel):
    ttl: int | None = None
    writable: bool = False


class SshConfig(BaseModel):
    host: str
    user: str | None = None
    port: int | None = None
    identity: str | None = None
    options: list[str] = Field(default_factory=list)
    command: str | None = None


class RecordingStart(BaseModel):
    record_input: bool = False


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "user"


class UserUpdate(BaseModel):
    role: str | None = None
    disabled: bool | None = None
    password: str | None = None


def _qr_svg(text: str) -> str:
    """Render ``text`` as a standalone SVG QR code (for TOTP provisioning)."""
    buffer = io.BytesIO()
    segno.make(text, error="m").save(buffer, kind="svg")
    return buffer.getvalue().decode("utf-8")


def _token_from_request(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(COOKIE_NAME)


def current_user(request: Request) -> User:
    settings: Settings = request.app.state.settings
    store: Store = request.app.state.store
    if not settings.auth_required:
        return User(id=0, username="anonymous", role="admin", disabled=False, created_at=0.0)
    token = _token_from_request(request)
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


def _config_mtime(settings: Settings) -> float:
    try:
        return settings.config_path.stat().st_mtime
    except OSError:
        return 0.0


def _pid_alive(pid: int | None) -> bool:
    """Whether a local pid is still running (best-effort, POSIX only).

    On Windows ``os.kill(pid, 0)`` is not a liveness probe -- CPython maps it
    to ``TerminateProcess`` -- so we never probe there and fall back to the
    heartbeat TTL instead.
    """
    if sys.platform == "win32":
        return True
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    except OSError:
        return False
    return True


def _prune_dead_local_leases(store: Store, me: str, host: str) -> None:
    """Immediately drop leases of crashed same-host instances.

    Heartbeat TTL alone would make crash recovery wait up to ``instance_ttl``;
    a same-host pid check lets a restarted server adopt orphaned sessions at
    once instead of after the lease expires.
    """
    for inst in store.instance_all():
        if inst["id"] == me or inst["host"] != host:
            continue
        if not _pid_alive(inst["pid"]):
            store.instance_remove(str(inst["id"]))


async def _reconcile_instances(app: FastAPI) -> None:
    """Adopt sessions abandoned by dead instances; leave live peers alone.

    Runs at startup and on every maintenance tick so that a session becomes
    adoptable as soon as its owning instance goes away (rather than only on the
    next restart). tmux sessions are namespaced per data directory, so a peer
    using a different data directory on the same host is never mistaken for an
    orphan.
    """
    settings: Settings = app.state.settings
    store: Store = app.state.store
    manager: SessionManager = app.state.manager
    me = app.state.instance_id
    host = socket.gethostname()
    _prune_dead_local_leases(store, me, host)

    rows = store.term_session_list()
    tick = int(getattr(app.state, "_reconcile_tick", 0))
    app.state._reconcile_tick = tick + 1
    has_tmux_rows = any(row.get("backend") == "tmux" for row in rows)
    # Probing tmux spawns a subprocess; only scan for orphans when there are
    # tmux-backed rows, and otherwise just occasionally.
    if tmux.is_available() and (has_tmux_rows or tick % 12 == 0):
        known = {str(row["id"]) for row in rows}
        for name in await tmux.list_sessions_async():
            if tmux.owns(name) and tmux.sid_from_name(name) not in known:
                await tmux.kill_session_async(name)
                log.info("reaped orphan tmux session %s", name)

    live = store.instance_alive_ids(settings.instance_ttl) - {me}
    for row in rows:
        if row.get("status") not in ("running", "interrupted"):
            continue
        if row.get("instance_id") in live:
            continue  # a live peer still owns this session
        sid = str(row["id"])
        if manager.get(sid) is not None:
            continue  # already held in this process
        if row.get("backend") != "tmux":
            # A local session died together with its instance.
            store.term_session_set_status(sid, "stopped")
            continue
        if not await tmux.has_session_async(tmux.session_name(sid)):
            store.term_session_set_status(sid, "stopped")
            continue
        spec = SessionSpec(
            name=str(row["name"]),
            argv=[settings.shell],
            cwd=row.get("cwd") or settings.default_cwd,
            backend="tmux",
            idle_timeout=settings.idle_timeout,
            max_life=settings.max_life,
            max_clients=settings.session_max_clients,
            memory_limit=settings.session_memory_limit,
            scrollback_bytes=settings.scrollback_bytes,
        )
        try:
            await manager.create(spec, sid=sid, owner_id=row.get("owner_id"))
        except (ValueError, OSError, tmux.TmuxError):
            continue
        store.term_session_set_instance(sid, me)
        store.term_session_set_status(sid, "running")
        log.info("adopted tmux session %s", sid)


def create_app(
    settings: Settings,
    *,
    store: Store | None = None,
    manager: SessionManager | None = None,
    startup_command: str | None = None,
) -> FastAPI:
    instance_id = uuid.uuid4().hex
    config_clock = {"mtime": _config_mtime(settings)}

    def _reload_config() -> list[str]:
        changed = reload_settings_file(settings)
        if changed:
            limiter.limit = settings.login_rate_limit
            limiter.window = float(settings.login_rate_window)
            log.info("config reloaded: %s", ", ".join(changed))
        return changed

    def _purge_retention() -> None:
        """Bound on-disk growth: audit log, finished session rows, old casts."""
        now = time.time()
        if settings.audit_retention_days > 0:
            app.state.store.purge_audit(now - settings.audit_retention_days * 86400)
        if settings.term_session_retention_days > 0:
            app.state.store.purge_term_sessions(
                now - settings.term_session_retention_days * 86400
            )
        if settings.recordings_retention_days > 0:
            cutoff = now - settings.recordings_retention_days * 86400
            active = {
                str(s.recording_path)
                for s in app.state.manager.list_sessions()
                if s.recording_path is not None
            }
            for entry in settings.recordings_dir.glob("*.cast"):
                if str(entry) in active:
                    continue
                try:
                    if entry.stat().st_mtime < cutoff:
                        entry.unlink(missing_ok=True)
                except OSError:
                    continue

    async def _maintenance() -> None:
        while True:
            await asyncio.sleep(MAINTENANCE_INTERVAL)
            try:
                # Re-register (idempotent upsert) rather than a bare heartbeat:
                # if a peer pruned our lease while we were paused, this restores it.
                app.state.store.instance_register(
                    instance_id, pid=os.getpid(), host=socket.gethostname()
                )
                for sid in await app.state.manager.reap_expired():
                    app.state.store.term_session_set_status(sid, "expired")
                alive = {s.id for s in app.state.manager.list_sessions()}
                # Only this instance's rows may be reconciled here: a live peer
                # sharing the same database owns its own sessions.
                app.state.store.term_session_stop_missing(alive, instance_id=instance_id)
                app.state.store.purge_expired_sessions()
                limiter.sweep()
                for dead in app.state.store.instance_dead_ids(settings.instance_ttl):
                    if dead != instance_id:
                        app.state.store.instance_remove(dead)
                await _reconcile_instances(app)
                _purge_retention()
                mtime = _config_mtime(settings)
                if mtime != config_clock["mtime"]:
                    config_clock["mtime"] = mtime
                    _reload_config()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("maintenance task failed")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info("wsctl %s listening on %s:%s", __version__, settings.host, settings.port)
        tmux.set_namespace(settings.data_dir)
        app.state.store.instance_register(
            instance_id, pid=os.getpid(), host=socket.gethostname()
        )
        maint = asyncio.create_task(_maintenance())
        audit.start()
        if webhook is not None:
            webhook.start()
        await _reconcile_instances(app)
        if startup_command:
            argv = shlex.split(startup_command)
            spec = SessionSpec(
                name=Path(argv[0]).name if argv else "终端",
                argv=argv,
                cwd=settings.default_cwd,
                backend=settings.default_backend,
                idle_timeout=settings.idle_timeout,
                max_life=settings.max_life,
                max_clients=settings.session_max_clients,
                memory_limit=settings.session_memory_limit,
                scrollback_bytes=settings.scrollback_bytes,
            )
            try:
                session = await app.state.manager.create(spec)
            except (OSError, PtyError) as exc:
                log.error("启动会话失败：%s", exc)
            else:
                app.state.store.term_session_upsert(
                    session.id,
                    name=spec.name,
                    owner_id=None,
                    backend=spec.backend,
                    instance_id=instance_id,
                )
                log.info("startup session %s: %s", session.id, startup_command)
        try:
            yield
        finally:
            maint.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await maint
            await app.state.manager.shutdown(preserve=settings.tmux_preserve_on_shutdown)
            # Flush queued audit events (and deliver them to the webhook) before
            # tearing the webhook down.
            await audit.stop()
            if webhook is not None:
                await webhook.stop()
            with contextlib.suppress(Exception):
                app.state.store.instance_remove(instance_id)
            app.state.store.close()

    app = FastAPI(
        title="wsctl", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan
    )
    app.state.settings = settings
    app.state.instance_id = instance_id
    app.state.store = store or Store(settings.db_path)
    app.state.store.sliding_ttl = settings.session_sliding_ttl
    app.state.manager = manager or SessionManager()

    audit = AuditWriter(app.state.store)
    app.state.audit = audit

    webhook = WebhookDispatcher(settings.webhook_url) if settings.webhook_url else None
    if webhook is not None:
        app.state.store.set_event_sink(webhook.emit)

    metrics = Metrics()
    app.state.metrics = metrics
    metrics.collect("wsctl_up", "1 when the server is serving", lambda: 1.0)
    metrics.collect(
        "wsctl_sessions",
        "Current number of terminal sessions",
        lambda: float(len(app.state.manager.list_sessions())),
    )
    metrics.collect(
        "wsctl_clients",
        "Current number of attached terminal clients",
        lambda: float(sum(s.client_count for s in app.state.manager.list_sessions())),
    )
    metrics.collect(
        "wsctl_session_bytes",
        "Approximate bytes buffered across all sessions",
        lambda: float(sum(s.memory_usage() for s in app.state.manager.list_sessions())),
    )
    metrics.collect(
        "wsctl_audit_dropped_total",
        "Audit events dropped because the async queue was saturated",
        lambda: float(audit.dropped),
    )

    limiter = RateLimiter(settings.login_rate_limit, settings.login_rate_window)

    @app.middleware("http")
    async def _guard(request: Request, call_next: RequestResponseEndpoint) -> Response:
        ip = client_ip(request, settings)
        if not ip_allowed(ip, settings.allowed_ips):
            app.state.audit.enqueue("ip_rejected", ip=ip, payload=request.url.path)
            return JSONResponse({"detail": "forbidden"}, status_code=status.HTTP_403_FORBIDDEN)
        response = await call_next(request)
        if settings.security_headers:
            for key, value in SECURITY_HEADERS.items():
                response.headers.setdefault(key, value)
        return response

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "sessions": len(app.state.manager.list_sessions()),
        }

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint(request: Request) -> PlainTextResponse:
        if not settings.metrics_enabled:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="指标已禁用")
        if settings.metrics_require_auth:
            current_user(request)
        return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")

    # -- auth ----------------------------------------------------------

    @app.post("/api/login")
    async def login(body: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
        store_: Store = request.app.state.store
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

        user = store_.user_authenticate(body.username, body.password)
        if user is None:
            limiter.record_failure(key)
            metrics.inc("wsctl_logins_total", result="failed")
            audit.enqueue("login_failed", ip=ip, payload=body.username)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误"
            )

        secret = store_.user_totp_secret(body.username)
        if secret is not None and not totp.verify(secret, body.totp or ""):
            limiter.record_failure(key)
            metrics.inc("wsctl_logins_total", result="totp_failed")
            audit.enqueue("login_totp_failed", user_id=user.id, ip=ip, payload=body.username)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="一次性验证码错误"
            )

        limiter.reset(key)
        metrics.inc("wsctl_logins_total", result="ok")
        token = store_.create_auth_session(
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

    @app.post("/api/logout")
    async def logout(request: Request, response: Response) -> dict[str, bool]:
        token = _token_from_request(request)
        if token:
            user = request.app.state.store.resolve_auth_session(token)
            request.app.state.store.delete_auth_session(token)
            request.app.state.audit.enqueue(
                "logout",
                user_id=user.id if user else None,
                ip=client_ip(request, settings),
            )
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"ok": True}

    @app.get("/api/me")
    async def me(user: User = Depends(current_user)) -> dict[str, Any]:
        return {"username": user.username, "role": user.role}

    # -- sessions ------------------------------------------------------

    def _serialize(session: TermSession) -> dict[str, Any]:
        return {
            "id": session.id,
            "name": session.spec.name,
            "pid": session.pid,
            "owner_id": session.owner_id,
            "backend": session.backend,
            "shared": session.is_shared,
            "share_writable": session.share_writable,
            "recording": session.is_recording,
            "clients": session.client_count,
            "alive": session.is_alive,
            "created_at": session.created_at,
            "last_active": session.last_active,
            "bytes": session.memory_usage(),
        }

    @app.get("/api/sessions")
    async def list_sessions(user: User = Depends(current_user)) -> list[dict[str, Any]]:
        sessions = app.state.manager.list_sessions()
        return [_serialize(s) for s in sessions if can_access(user, s)]

    @app.post("/api/sessions", status_code=status.HTTP_201_CREATED)
    async def create_session(
        body: SessionCreate, user: User = Depends(current_user)
    ) -> dict[str, Any]:
        manager: SessionManager = app.state.manager
        if len(manager.list_sessions()) >= settings.max_sessions:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="会话数量已达上限"
            )
        if not within_user_quota(manager, settings.max_sessions_per_user, user.id):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="该用户的会话数量已达上限",
            )
        backend = body.backend or settings.default_backend
        if backend not in ("local", "tmux", "ssh"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"未知后端：{backend}"
            )
        if backend == "tmux" and not tmux.is_available():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="未安装 tmux"
            )
        if backend == "ssh":
            if body.ssh is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="需要提供 ssh 配置"
                )
            if not ssh.ssh_available():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="未安装 ssh 客户端"
                )
            try:
                target = ssh.SshTarget(
                    host=body.ssh.host,
                    user=body.ssh.user,
                    port=body.ssh.port,
                    identity=body.ssh.identity,
                    options=body.ssh.options,
                    remote_command=body.ssh.command or body.command,
                )
            except ssh.SshError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
                ) from exc
            argv = target.argv()
            name = body.name or f"ssh:{target.destination()}"
        else:
            if body.command is not None:
                argv = shlex.split(body.command)
                if not argv:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST, detail="命令为空"
                    )
            else:
                argv = [settings.shell]
            name = body.name or (Path(argv[0]).name if argv else "终端")
        spec = SessionSpec(
            name=name,
            argv=argv,
            cwd=body.cwd or settings.default_cwd,
            backend=backend,
            cols=body.cols,
            rows=body.rows,
            idle_timeout=settings.idle_timeout,
            max_life=settings.max_life,
            max_clients=settings.session_max_clients,
            memory_limit=settings.session_memory_limit,
            scrollback_bytes=settings.scrollback_bytes,
        )
        try:
            session = await manager.create(spec, owner_id=user.id)
        except (OSError, PtyError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"无法启动命令：{exc}",
            ) from exc
        try:
            if settings.auto_record and recording_mod.has_room(
                settings.recordings_dir, settings.recordings_max_bytes
            ):
                session.start_recording(
                    settings.recordings_dir / f"{session.id}.cast",
                    record_input=settings.record_input,
                )
            app.state.store.term_session_upsert(
                session.id,
                name=name,
                owner_id=user.id,
                backend=backend,
                command=body.command,
                argv=spec.argv,
                env=spec.env,
                cwd=spec.cwd,
                idle_timeout=spec.idle_timeout,
                max_life=spec.max_life,
                instance_id=instance_id,
            )
            app.state.audit.enqueue(
                "session_create", user_id=user.id, term_session_id=session.id, payload=name
            )
        except Exception as exc:
            await manager.remove(session.id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="创建会话失败",
            ) from exc
        metrics.inc("wsctl_sessions_created_total")
        return _serialize(session)

    @app.delete("/api/sessions/{sid}")
    async def delete_session(sid: str, user: User = Depends(current_user)) -> dict[str, bool]:
        session = app.state.manager.get(sid)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
        if not can_access(user, session):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该会话")
        await app.state.manager.remove(sid)
        app.state.store.term_session_set_status(sid, "killed")
        app.state.audit.enqueue("session_kill", user_id=user.id, term_session_id=sid)
        return {"ok": True}

    @app.patch("/api/sessions/{sid}")
    async def rename_session(
        sid: str, body: SessionRename, user: User = Depends(current_user)
    ) -> dict[str, Any]:
        session = app.state.manager.get(sid)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
        if not can_access(user, session):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该会话")
        name = session.rename(body.name)
        app.state.store.term_session_upsert(
            sid,
            name=name,
            owner_id=session.owner_id,
            cwd=session.spec.cwd,
            instance_id=instance_id,
        )
        return _serialize(session)

    # -- sharing -------------------------------------------------------

    def _owned(sid: str, user: User) -> TermSession:
        manager: SessionManager = app.state.manager
        session = manager.get(sid)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
        if not can_access(user, session):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该会话")
        return session

    @app.get("/api/sessions/{sid}/share")
    async def get_share(sid: str, user: User = Depends(current_user)) -> dict[str, Any]:
        """Return the active share token so the UI can reuse an existing link."""
        session = _owned(sid, user)
        token = session.peek_share()
        if token is None:
            return {"shared": False}
        return {"shared": True, "token": token, "writable": session.share_writable}

    @app.post("/api/sessions/{sid}/share")
    async def create_share(
        sid: str, body: SessionShare, user: User = Depends(current_user)
    ) -> dict[str, Any]:
        session = _owned(sid, user)
        token = session.create_share(
            ttl=float(body.ttl) if body.ttl else None, writable=body.writable
        )
        app.state.audit.enqueue(
            "session_share",
            user_id=user.id,
            term_session_id=sid,
            payload="write" if body.writable else "read",
        )
        return {"token": token, "ttl": body.ttl, "writable": body.writable}

    @app.delete("/api/sessions/{sid}/share")
    async def revoke_share(sid: str, user: User = Depends(current_user)) -> dict[str, bool]:
        session = _owned(sid, user)
        session.revoke_share()
        app.state.audit.enqueue("session_unshare", user_id=user.id, term_session_id=sid)
        return {"ok": True}

    @app.get("/api/sessions/{sid}/qr.svg")
    async def share_qr(
        request: Request, sid: str, origin: str = "", user: User = Depends(current_user)
    ) -> Response:
        session = _owned(sid, user)
        token = session.peek_share()
        if token is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="该会话未分享")
        allowed = is_origin_allowed(origin, request.headers.get("host"), settings.allowed_origins)
        base = origin if (origin and allowed) else str(request.base_url).rstrip("/")
        url = f"{base}/?session={sid}&share={token}"
        # A standalone SVG document (with xmlns) so it renders inside an <img>.
        buffer = io.BytesIO()
        segno.make(url, error="m").save(buffer, kind="svg")
        return Response(content=buffer.getvalue(), media_type="image/svg+xml")

    # -- recordings ----------------------------------------------------

    @app.post("/api/sessions/{sid}/recording/start")
    async def start_recording(
        sid: str, body: RecordingStart, user: User = Depends(current_user)
    ) -> dict[str, str]:
        session = _owned(sid, user)
        if session.is_recording:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="已在录制中")
        path = session.start_recording(
            settings.recordings_dir / f"{sid}.cast", record_input=body.record_input
        )
        app.state.audit.enqueue("recording_start", user_id=user.id, term_session_id=sid)
        return {"path": str(path)}

    @app.post("/api/sessions/{sid}/recording/stop")
    async def stop_recording(sid: str, user: User = Depends(current_user)) -> dict[str, bool]:
        session = _owned(sid, user)
        session.stop_recording()
        app.state.audit.enqueue("recording_stop", user_id=user.id, term_session_id=sid)
        return {"ok": True}

    @app.get("/api/sessions/{sid}/recording")
    async def download_recording(sid: str, user: User = Depends(current_user)) -> FileResponse:
        session = _owned(sid, user)
        path = session.recording_path
        if path is None or not path.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="没有录制")
        return FileResponse(
            path, media_type="application/x-asciicast", filename=f"{sid}.cast"
        )

    @app.get("/api/recordings")
    async def list_recordings(_: User = Depends(require_admin)) -> list[dict[str, Any]]:
        directory = settings.recordings_dir
        if not directory.is_dir():
            return []
        items = []
        for entry in sorted(directory.glob("*.cast")):
            try:
                stat = entry.stat()
            except OSError:
                continue
            items.append(
                {"name": entry.name, "size": stat.st_size, "mtime": stat.st_mtime}
            )
        return items

    def _recording_path(name: str) -> Path:
        safe = Path(name).name
        if safe != name or not safe.endswith(".cast"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件名无效")
        return settings.recordings_dir / safe

    @app.get("/api/recordings/{name}")
    async def download_recording_by_name(
        name: str, _: User = Depends(require_admin)
    ) -> FileResponse:
        path = _recording_path(name)
        if not path.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="没有录制")
        return FileResponse(path, media_type="application/x-asciicast", filename=name)

    @app.delete("/api/recordings/{name}")
    async def delete_recording_by_name(
        name: str, actor: User = Depends(require_admin)
    ) -> dict[str, bool]:
        path = _recording_path(name)
        if not path.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="没有录制")
        path.unlink(missing_ok=True)
        audit.enqueue("recording_delete", user_id=actor.id, payload=name)
        return {"ok": True}

    # -- users (admin only) --------------------------------------------

    @app.get("/api/users")
    async def list_users(_: User = Depends(require_admin)) -> list[dict[str, Any]]:
        return [
            {
                "id": u.id,
                "username": u.username,
                "role": u.role,
                "disabled": u.disabled,
                "totp": app.state.store.user_totp_secret(u.username) is not None,
            }
            for u in app.state.store.user_list()
        ]

    @app.post("/api/users", status_code=status.HTTP_201_CREATED)
    async def create_user(body: UserCreate, actor: User = Depends(require_admin)) -> dict[str, Any]:
        if body.role not in ("admin", "user"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="角色无效")
        if app.state.store.user_get(body.username) is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户已存在")
        try:
            user = app.state.store.user_create(body.username, body.password, role=body.role)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="用户已存在"
            ) from exc
        app.state.audit.enqueue(
            "user_create", user_id=actor.id, payload=f"{user.username}:{user.role}"
        )
        return {"id": user.id, "username": user.username, "role": user.role}

    @app.delete("/api/users/{username}")
    async def delete_user(
        username: str, actor: User = Depends(require_admin)
    ) -> dict[str, bool]:
        target = app.state.store.user_get(username)
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
        if username == actor.username:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能删除自己")
        if target.role == "admin" and app.state.store.admin_count() <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="至少保留一个管理员"
            )
        app.state.store.user_delete(username)
        audit.enqueue("user_delete", user_id=actor.id, payload=username)
        return {"ok": True}

    @app.patch("/api/users/{username}")
    async def update_user(
        username: str, body: UserUpdate, actor: User = Depends(require_admin)
    ) -> dict[str, bool]:
        if body.role is None and body.disabled is None and body.password is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="没有需要更新的字段"
            )
        if body.role is not None and body.role not in ("admin", "user"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="角色无效")
        target = app.state.store.user_get(username)
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
        if body.disabled and username == actor.username:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能禁用自己")
        demoting = body.role == "user" and target.role == "admin"
        disabling = body.disabled is True and target.role == "admin"
        if (demoting or disabling) and app.state.store.admin_count() <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="至少保留一个管理员"
            )
        changed: list[str] = []
        if body.role is not None:
            app.state.store.user_set_role(username, body.role)
            changed.append(f"role={body.role}")
        if body.disabled is not None:
            app.state.store.user_set_disabled(username, body.disabled)
            changed.append(f"disabled={body.disabled}")
            if body.disabled:
                app.state.store.delete_user_sessions(target.id)
        if body.password is not None:
            app.state.store.user_set_password(username, body.password)
            # A password change revokes existing logins for that user.
            app.state.store.delete_user_sessions(target.id)
            changed.append("password")
        audit.enqueue("user_update", user_id=actor.id, payload=f"{username}:{','.join(changed)}")
        return {"ok": True}

    @app.post("/api/users/{username}/totp")
    async def enable_totp(
        username: str, actor: User = Depends(require_admin)
    ) -> dict[str, str]:
        if app.state.store.user_get(username) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
        secret = totp.generate_secret()
        app.state.store.user_set_totp(username, secret)
        audit.enqueue("user_totp_on", user_id=actor.id, payload=username)
        uri = totp.provisioning_uri(secret, username, issuer=settings.totp_issuer)
        return {"secret": secret, "uri": uri, "qr_svg": _qr_svg(uri)}

    @app.delete("/api/users/{username}/totp")
    async def disable_totp(
        username: str, actor: User = Depends(require_admin)
    ) -> dict[str, bool]:
        if not app.state.store.user_clear_totp(username):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
        audit.enqueue("user_totp_off", user_id=actor.id, payload=username)
        return {"ok": True}

    @app.get("/api/audit")
    async def get_audit(
        _: User = Depends(require_admin),
        limit: int = 100,
        offset: int = 0,
        event: str | None = None,
        user_id: int | None = None,
        ip: str | None = None,
    ) -> list[dict[str, Any]]:
        # Flush the async writer so the read reflects events just produced.
        await app.state.audit.flush()
        store_: Store = app.state.store
        return store_.recent_audit(
            limit=min(max(limit, 1), 1000),
            offset=max(offset, 0),
            event=event,
            user_id=user_id,
            ip=ip,
        )

    @app.post("/api/config/reload")
    async def reload_config(actor: User = Depends(require_admin)) -> dict[str, Any]:
        changed = _reload_config()
        app.state.audit.enqueue(
            "config_reload", user_id=actor.id, payload=",".join(changed) or None
        )
        return {"changed": changed}

    # -- files (rooted at settings.files_root) -------------------------

    @app.get("/api/files")
    async def list_files(
        user: User = Depends(current_user), path: str = ""
    ) -> dict[str, Any]:
        root = settings.files_root
        try:
            entries = fs_mod.list_dir(root, path)
        except fs_mod.FsError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {"path": path.strip().lstrip("/"), "root": str(root), "entries": entries}

    @app.get("/api/files/download")
    async def download_file(user: User = Depends(current_user), path: str = "") -> FileResponse:
        root = settings.files_root
        try:
            target = fs_mod.safe_resolve(root, path)
        except fs_mod.FsError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        if not target.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="不是文件")
        return FileResponse(target, filename=target.name)

    @app.post("/api/files/upload", status_code=status.HTTP_201_CREATED)
    async def upload_file(
        user: User = Depends(current_user),
        file: UploadFile = File(...),
        path: str = Form(""),
    ) -> dict[str, Any]:
        root = settings.files_root
        name = Path(file.filename or "").name
        if not name or name in (".", ".."):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件名无效")
        try:
            directory = fs_mod.safe_resolve(root, path)
        except fs_mod.FsError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        if not directory.is_dir():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="不是目录")

        dest = directory / name
        # O_NOFOLLOW refuses to follow a symlink planted at the destination.
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(dest, flags, 0o644)
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="拒绝覆盖符号链接或非法路径",
            ) from exc
        size = 0
        try:
            loop = asyncio.get_running_loop()
            size = await loop.run_in_executor(
                None, _copy_upload, file.file, fd, settings.file_max_upload
            )
        except _UploadTooLarge as exc:
            dest.unlink(missing_ok=True)
            raise HTTPException(status_code=413, detail="文件过大") from exc
        except Exception:
            # Do not leave a half-written file behind on any other failure.
            dest.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

        metrics.inc("wsctl_uploads_total")
        app.state.audit.enqueue(
            "file_upload",
            user_id=user.id,
            payload=fs_mod.relative_to(root, dest)[:256],
        )
        return {"name": name, "size": size}

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await terminal_endpoint(websocket)

    return app
