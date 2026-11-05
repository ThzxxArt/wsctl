"""Liveness and observability endpoints."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response

from wsctl import __version__
from wsctl.core.config import Settings
from wsctl.core.observability import render_prometheus

from ..deps import current_user

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"

router = APIRouter(tags=["health"])


#: Assets whose *content* changes between releases and must be revalidated.
#: Vite's content-hashed files under ``assets/`` are cached immutably by the
#: static mount instead; this list covers the entry document and manifest.
VOLATILE_ASSETS = ("index.html", "manifest.webmanifest")
NO_CACHE = "no-cache, must-revalidate"


@router.get("/", include_in_schema=False)
async def index() -> Response:
    return FileResponse(
        STATIC_DIR / "index.html", headers={"Cache-Control": NO_CACHE}
    )


@router.get("/sw.js", include_in_schema=False)
async def service_worker() -> Response:
    """Serve the service worker from the origin root.

    A worker served under ``/static/`` would be scoped to ``/static/`` and
    could not handle the app-shell navigation at ``/``. The
    ``Service-Worker-Allowed`` header is what lifts the scope; serving it at
    the root is the simpler, less surprising half of the same fix.
    """
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={
            "Cache-Control": NO_CACHE,
            "Service-Worker-Allowed": "/",
        },
    )


@router.get("/s/{token}", include_in_schema=False)
async def share_page(token: str) -> Response:
    """Serve the SPA; the token is read by the client from the URL."""
    return FileResponse(
        STATIC_DIR / "index.html", headers={"Cache-Control": NO_CACHE}
    )


@router.get("/healthz")
async def healthz(request: Request, response: Response) -> dict[str, Any]:
    """Liveness **and** identity.

    The pid is not decoration: during a rolling restart two instances share
    the port for a moment, and a plain 200 on this endpoint cannot tell the
    orchestrator which one answered. Without it the gate passed as soon as the
    *predecessor* replied, the predecessor was retired, and the successor was
    not yet serving -- a refused-connection window, which is the entire thing a
    rolling restart exists to avoid.
    """
    result: dict[str, Any] = {
        "status": "ok",
        "version": __version__,
        "pid": os.getpid(),
        "instance_id": getattr(request.app.state, "instance_id", None),
        "sessions": len(request.app.state.manager.list_sessions()),
    }
    response.headers["Cache-Control"] = "no-store"
    try:
        import psutil  # type: ignore[import-untyped]

        process = psutil.Process(os.getpid())
        result["rss_bytes"] = process.memory_info().rss
        result["threads"] = process.num_threads()
    except Exception:
        pass
    return result


@router.get("/readyz")
async def readyz(request: Request) -> Response:
    """Readiness: every dependency a request may need must be usable now."""
    import shutil

    checks: dict[str, bool] = {}
    try:
        db = request.app.state.db
        await asyncio.to_thread(_ping_db, db)
        checks["database"] = True
    except Exception:
        checks["database"] = False
    try:
        data_dir = request.app.state.settings.data_dir
        await asyncio.to_thread(data_dir.mkdir, parents=True, exist_ok=True)
        free = await asyncio.to_thread(shutil.disk_usage, data_dir)
        checks["disk"] = free.free > 0
    except Exception:
        checks["disk"] = False
    checks["maintenance"] = bool(getattr(request.app.state, "maintenance_last", 0))
    settings = request.app.state.settings
    if settings.default_backend == "tmux":
        # tmux is optional for local sessions, but a server configured to
        # create tmux-backed sessions cannot serve a new one without it.
        checks["tmux"] = await asyncio.to_thread(lambda: shutil.which("tmux") is not None)
    ok = all(checks.values())
    return JSONResponse(
        {"status": "ready" if ok else "degraded", "checks": checks},
        status_code=200 if ok else 503,
        headers={"Cache-Control": "no-store"},
    )


def _ping_db(db: Any) -> None:
    from sqlalchemy import text

    with db.engine.connect() as connection:
        connection.execute(text("SELECT 1"))


@router.get("/metrics", include_in_schema=False)
async def metrics_endpoint(request: Request) -> PlainTextResponse:
    settings: Settings = request.app.state.settings
    if not settings.metrics_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="指标已禁用")
    if settings.metrics_require_auth:
        await asyncio.to_thread(current_user, request)
    metrics = request.app.state.metrics
    text = metrics.render()
    standard = render_prometheus()
    if standard:
        text = text + standard
    return PlainTextResponse(
        text,
        media_type="text/plain; version=0.0.4",
        headers={"Cache-Control": "no-store"},
    )
