"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import shlex
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
from wsctl.core import ssh, tmux, totp
from wsctl.core.config import Settings, reload_settings_file
from wsctl.core.metrics import Metrics
from wsctl.core.ratelimit import RateLimiter
from wsctl.core.session import SessionManager, SessionSpec, TermSession
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
    cols: int = 80
    rows: int = 24


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


class UserRoleUpdate(BaseModel):
    role: str


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
            status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required"
        )
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin role required")
    return user


def _config_mtime(settings: Settings) -> float:
    try:
        return settings.config_path.stat().st_mtime
    except OSError:
        return 0.0


async def _restore_tmux_sessions(app: FastAPI) -> None:
    """Reattach to tmux-backed sessions that outlived a previous server run."""
    settings: Settings = app.state.settings
    store: Store = app.state.store
    manager: SessionManager = app.state.manager
    if not tmux.is_available():
        return
    # Kill tmux sessions we own that no longer have a running DB row (orphans).
    known = {
        str(row["id"])
        for row in store.term_session_list()
        if row.get("backend") == "tmux" and row.get("status") in ("running", "interrupted")
    }
    for name in tmux.list_sessions():
        if name.startswith(tmux.PREFIX) and name[len(tmux.PREFIX):] not in known:
            tmux.kill_session(name)
            log.info("reaped orphan tmux session %s", name)
    for row in store.term_session_list():
        if row.get("backend") != "tmux" or row.get("status") not in ("running", "interrupted"):
            continue
        sid = str(row["id"])
        if not tmux.has_session(tmux.session_name(sid)):
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
        except ValueError:
            continue
        store.term_session_set_status(sid, "running")
        log.info("restored tmux session %s", sid)


def create_app(
    settings: Settings,
    *,
    store: Store | None = None,
    manager: SessionManager | None = None,
    startup_command: str | None = None,
) -> FastAPI:
    config_clock = {"mtime": _config_mtime(settings)}

    def _reload_config() -> list[str]:
        changed = reload_settings_file(settings)
        if changed:
            limiter.limit = settings.login_rate_limit
            limiter.window = float(settings.login_rate_window)
            log.info("config reloaded: %s", ", ".join(changed))
        return changed

    async def _maintenance() -> None:
        while True:
            await asyncio.sleep(MAINTENANCE_INTERVAL)
            try:
                for sid in await app.state.manager.reap_expired():
                    app.state.store.term_session_set_status(sid, "expired")
                alive = {s.id for s in app.state.manager.list_sessions()}
                app.state.store.term_session_stop_missing(alive)
                app.state.store.purge_expired_sessions()
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
        maint = asyncio.create_task(_maintenance())
        if webhook is not None:
            webhook.start()
        await _restore_tmux_sessions(app)
        if startup_command:
            argv = shlex.split(startup_command)
            spec = SessionSpec(
                name=Path(argv[0]).name if argv else "shell",
                argv=argv,
                cwd=settings.default_cwd,
                backend=settings.default_backend,
                idle_timeout=settings.idle_timeout,
                max_life=settings.max_life,
                max_clients=settings.session_max_clients,
                memory_limit=settings.session_memory_limit,
                scrollback_bytes=settings.scrollback_bytes,
            )
            session = await app.state.manager.create(spec)
            app.state.store.term_session_upsert(
                session.id, name=spec.name, owner_id=None, backend=spec.backend
            )
            log.info("startup session %s: %s", session.id, startup_command)
        try:
            yield
        finally:
            maint.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await maint
            if webhook is not None:
                await webhook.stop()
            await app.state.manager.shutdown(preserve=settings.tmux_preserve_on_shutdown)
            app.state.store.close()

    app = FastAPI(
        title="wsctl", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan
    )
    app.state.settings = settings
    app.state.store = store or Store(settings.db_path)
    app.state.manager = manager or SessionManager()

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

    limiter = RateLimiter(settings.login_rate_limit, settings.login_rate_window)

    @app.middleware("http")
    async def _guard(request: Request, call_next: RequestResponseEndpoint) -> Response:
        ip = client_ip(request, settings)
        if not ip_allowed(ip, settings.allowed_ips):
            app.state.store.log_event("ip_rejected", ip=ip, payload=request.url.path)
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
    async def metrics_endpoint() -> PlainTextResponse:
        if not settings.metrics_enabled:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="metrics disabled")
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
            store_.log_event("login_blocked", ip=ip, payload=body.username)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="too many attempts, try again later",
                headers={"Retry-After": str(retry_after)},
            )

        user = store_.user_authenticate(body.username, body.password)
        if user is None:
            limiter.record_failure(key)
            metrics.inc("wsctl_logins_total", result="failed")
            store_.log_event("login_failed", ip=ip, payload=body.username)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials"
            )

        secret = store_.user_totp_secret(body.username)
        if secret is not None and not totp.verify(secret, body.totp or ""):
            limiter.record_failure(key)
            metrics.inc("wsctl_logins_total", result="totp_failed")
            store_.log_event("login_totp_failed", user_id=user.id, ip=ip, payload=body.username)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid one-time code"
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
        store_.log_event("login", user_id=user.id, ip=ip)
        return {"username": user.username, "role": user.role}

    @app.post("/api/logout")
    async def logout(request: Request, response: Response) -> dict[str, bool]:
        token = _token_from_request(request)
        if token:
            user = request.app.state.store.resolve_auth_session(token)
            request.app.state.store.delete_auth_session(token)
            request.app.state.store.log_event(
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
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="session limit"
            )
        backend = body.backend or settings.default_backend
        if backend not in ("local", "tmux", "ssh"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"unknown backend: {backend}"
            )
        if backend == "tmux" and not tmux.is_available():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="tmux is not installed"
            )
        if backend == "ssh":
            if body.ssh is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="ssh config is required"
                )
            if not ssh.ssh_available():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="ssh client is not installed"
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
            argv = shlex.split(body.command) if body.command else [settings.shell]
            name = body.name or (Path(argv[0]).name if argv else "shell")
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
        session = await manager.create(spec, owner_id=user.id)
        if settings.auto_record:
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
        )
        app.state.store.log_event(
            "session_create", user_id=user.id, term_session_id=session.id, payload=name
        )
        metrics.inc("wsctl_sessions_created_total")
        return _serialize(session)

    @app.delete("/api/sessions/{sid}")
    async def delete_session(sid: str, user: User = Depends(current_user)) -> dict[str, bool]:
        session = app.state.manager.get(sid)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such session")
        if not can_access(user, session):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not your session")
        await app.state.manager.remove(sid)
        app.state.store.term_session_set_status(sid, "killed")
        app.state.store.log_event("session_kill", user_id=user.id, term_session_id=sid)
        return {"ok": True}

    @app.patch("/api/sessions/{sid}")
    async def rename_session(
        sid: str, body: SessionRename, user: User = Depends(current_user)
    ) -> dict[str, Any]:
        session = app.state.manager.get(sid)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such session")
        if not can_access(user, session):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not your session")
        name = session.rename(body.name)
        app.state.store.term_session_upsert(
            sid, name=name, owner_id=session.owner_id, cwd=session.spec.cwd
        )
        return _serialize(session)

    # -- sharing -------------------------------------------------------

    def _owned(sid: str, user: User) -> TermSession:
        manager: SessionManager = app.state.manager
        session = manager.get(sid)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such session")
        if not can_access(user, session):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not your session")
        return session

    @app.post("/api/sessions/{sid}/share")
    async def create_share(
        sid: str, body: SessionShare, user: User = Depends(current_user)
    ) -> dict[str, Any]:
        session = _owned(sid, user)
        token = session.create_share(
            ttl=float(body.ttl) if body.ttl else None, writable=body.writable
        )
        app.state.store.log_event(
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
        app.state.store.log_event("session_unshare", user_id=user.id, term_session_id=sid)
        return {"ok": True}

    @app.get("/api/sessions/{sid}/qr.svg")
    async def share_qr(
        request: Request, sid: str, origin: str = "", user: User = Depends(current_user)
    ) -> Response:
        session = _owned(sid, user)
        token = session.peek_share()
        if token is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not shared")
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
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="already recording")
        path = session.start_recording(
            settings.recordings_dir / f"{sid}.cast", record_input=body.record_input
        )
        app.state.store.log_event("recording_start", user_id=user.id, term_session_id=sid)
        return {"path": str(path)}

    @app.post("/api/sessions/{sid}/recording/stop")
    async def stop_recording(sid: str, user: User = Depends(current_user)) -> dict[str, bool]:
        session = _owned(sid, user)
        session.stop_recording()
        app.state.store.log_event("recording_stop", user_id=user.id, term_session_id=sid)
        return {"ok": True}

    @app.get("/api/sessions/{sid}/recording")
    async def download_recording(sid: str, user: User = Depends(current_user)) -> FileResponse:
        session = _owned(sid, user)
        path = session.recording_path
        if path is None or not path.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no recording")
        return FileResponse(
            path, media_type="application/x-asciicast", filename=f"{sid}.cast"
        )

    @app.get("/api/recordings")
    async def list_recordings(_: User = Depends(require_admin)) -> list[dict[str, Any]]:
        directory = settings.recordings_dir
        if not directory.is_dir():
            return []
        return [
            {"name": entry.name, "size": entry.stat().st_size}
            for entry in sorted(directory.glob("*.cast"))
        ]

    # -- users (admin only) --------------------------------------------

    @app.get("/api/users")
    async def list_users(_: User = Depends(require_admin)) -> list[dict[str, Any]]:
        return [
            {"id": u.id, "username": u.username, "role": u.role, "disabled": u.disabled}
            for u in app.state.store.user_list()
        ]

    @app.post("/api/users", status_code=status.HTTP_201_CREATED)
    async def create_user(body: UserCreate, actor: User = Depends(require_admin)) -> dict[str, Any]:
        if body.role not in ("admin", "user"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid role")
        if app.state.store.user_get(body.username) is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="user exists")
        user = app.state.store.user_create(body.username, body.password, role=body.role)
        app.state.store.log_event(
            "user_create", user_id=actor.id, payload=f"{user.username}:{user.role}"
        )
        return {"id": user.id, "username": user.username, "role": user.role}

    @app.delete("/api/users/{username}")
    async def delete_user(
        username: str, actor: User = Depends(require_admin)
    ) -> dict[str, bool]:
        if not app.state.store.user_delete(username):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such user")
        app.state.store.log_event("user_delete", user_id=actor.id, payload=username)
        return {"ok": True}

    @app.patch("/api/users/{username}")
    async def set_user_role(
        username: str, body: UserRoleUpdate, actor: User = Depends(require_admin)
    ) -> dict[str, bool]:
        if body.role not in ("admin", "user"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid role")
        if not app.state.store.user_set_role(username, body.role):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such user")
        app.state.store.log_event("user_role", user_id=actor.id, payload=f"{username}:{body.role}")
        return {"ok": True}

    @app.get("/api/audit")
    async def get_audit(
        _: User = Depends(require_admin), limit: int = 100
    ) -> list[dict[str, Any]]:
        store_: Store = app.state.store
        return store_.recent_audit(limit=min(max(limit, 1), 1000))

    @app.post("/api/config/reload")
    async def reload_config(actor: User = Depends(require_admin)) -> dict[str, Any]:
        changed = _reload_config()
        app.state.store.log_event(
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
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not a file")
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
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid filename")
        try:
            directory = fs_mod.safe_resolve(root, path)
        except fs_mod.FsError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        if not directory.is_dir():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not a directory")

        dest = directory / name
        size = 0
        try:
            with dest.open("wb") as handle:
                while chunk := await file.read(1 << 20):
                    size += len(chunk)
                    if size > settings.file_max_upload:
                        handle.close()
                        dest.unlink(missing_ok=True)
                        raise HTTPException(
                            status_code=413,
                            detail="file too large",
                        )
                    handle.write(chunk)
        finally:
            await file.close()

        metrics.inc("wsctl_uploads_total")
        app.state.store.log_event(
            "file_upload",
            user_id=user.id,
            payload=fs_mod.relative_to(root, dest)[:256],
        )
        return {"name": name, "size": size}

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await terminal_endpoint(websocket)

    return app
