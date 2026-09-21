"""FastAPI application factory."""

from __future__ import annotations

import logging
import shlex
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from wsctl import __version__
from wsctl.core.config import Settings
from wsctl.core.session import SessionManager, SessionSpec
from wsctl.core.store import Store, User

from .security import COOKIE_NAME
from .ws import terminal_endpoint

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

log = logging.getLogger("wsctl.app")


class LoginRequest(BaseModel):
    username: str
    password: str


class SessionCreate(BaseModel):
    name: str | None = None
    command: str | None = None
    cwd: str | None = None
    cols: int = 80
    rows: int = 24


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


def create_app(
    settings: Settings,
    *,
    store: Store | None = None,
    manager: SessionManager | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info("wsctl %s listening on %s:%s", __version__, settings.host, settings.port)
        try:
            yield
        finally:
            await app.state.manager.shutdown()
            app.state.store.close()

    app = FastAPI(
        title="wsctl", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan
    )
    app.state.settings = settings
    app.state.store = store or Store(settings.db_path)
    app.state.manager = manager or SessionManager()

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "sessions": len(app.state.manager.list()),
        }

    @app.post("/api/login")
    async def login(body: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
        store_: Store = request.app.state.store
        user = store_.user_authenticate(body.username, body.password)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials"
            )
        token = store_.create_auth_session(
            user.id,
            ttl=settings.session_ttl,
            ip=request.client.host if request.client else None,
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
        ip = request.client.host if request.client else None
        store_.log_event("login", user_id=user.id, ip=ip)
        return {"username": user.username, "role": user.role}

    @app.post("/api/logout")
    async def logout(request: Request, response: Response) -> dict[str, bool]:
        token = _token_from_request(request)
        if token:
            request.app.state.store.delete_auth_session(token)
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"ok": True}

    @app.get("/api/me")
    async def me(user: User = Depends(current_user)) -> dict[str, Any]:
        return {"username": user.username, "role": user.role}

    @app.get("/api/sessions")
    async def list_sessions(user: User = Depends(current_user)) -> list[dict[str, Any]]:
        return [
            {
                "id": s.id,
                "name": s.spec.name,
                "pid": s.pid,
                "clients": s.client_count,
                "alive": s.is_alive,
                "created_at": s.created_at,
                "last_active": s.last_active,
            }
            for s in app.state.manager.list()
        ]

    @app.post("/api/sessions", status_code=status.HTTP_201_CREATED)
    async def create_session(
        body: SessionCreate, user: User = Depends(current_user)
    ) -> dict[str, Any]:
        manager: SessionManager = app.state.manager
        if len(manager.list()) >= settings.max_sessions:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="session limit"
            )
        argv = shlex.split(body.command) if body.command else [settings.shell]
        spec = SessionSpec(
            name=body.name or (Path(argv[0]).name if argv else "shell"),
            argv=argv,
            cwd=body.cwd or settings.default_cwd,
            cols=body.cols,
            rows=body.rows,
            idle_timeout=settings.idle_timeout,
            max_life=settings.max_life,
            scrollback_bytes=settings.scrollback_bytes,
        )
        session = await manager.create(spec)
        app.state.store.log_event(
            "session_create", user_id=user.id, term_session_id=session.id, payload=spec.name
        )
        return {"id": session.id, "name": session.spec.name, "pid": session.pid}

    @app.delete("/api/sessions/{sid}")
    async def delete_session(sid: str, user: User = Depends(current_user)) -> dict[str, bool]:
        removed = await app.state.manager.remove(sid)
        if not removed:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such session")
        app.state.store.log_event("session_kill", user_id=user.id, term_session_id=sid)
        return {"ok": True}

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await terminal_endpoint(websocket)

    return app
