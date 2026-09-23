"""Liveness and observability endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse, PlainTextResponse

from wsctl import __version__
from wsctl.core.config import Settings

from ..deps import current_user

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"

router = APIRouter(tags=["health"])


@router.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "sessions": len(request.app.state.manager.list_sessions()),
    }


@router.get("/metrics", include_in_schema=False)
async def metrics_endpoint(request: Request) -> PlainTextResponse:
    settings: Settings = request.app.state.settings
    if not settings.metrics_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="指标已禁用")
    if settings.metrics_require_auth:
        current_user(request)
    metrics = request.app.state.metrics
    return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")
