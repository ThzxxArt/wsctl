"""Effective configuration view and hot reload."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request

from wsctl.core.config import HOT_FIELDS, kind_of
from wsctl.core.config import RESTART_FIELDS as RESTART_FIELDS_ALL
from wsctl.core.store import User

from ..deps import render_setting, require_admin

router = APIRouter(prefix="/api", tags=["config"])


@router.post("/config/reload")
async def reload_config(
    request: Request, actor: User = Depends(require_admin)
) -> dict[str, Any]:
    changed, errors = await asyncio.to_thread(request.app.state.reload_config)
    request.app.state.audit.enqueue(
        "config_reload",
        user_id=actor.id,
        payload=",".join(changed) if changed else (";".join(errors) or None),
    )
    return {"changed": changed, "errors": errors}


@router.get("/config")
async def get_config(request: Request, _: User = Depends(require_admin)) -> dict[str, Any]:
    """Effective configuration, labelled by whether a reload can apply it."""
    settings = request.app.state.settings
    values = await asyncio.to_thread(lambda: settings.model_dump())
    # ``kind`` is one of ``hot`` / ``new`` / ``restart`` (see
    # ``core.config.kind_of``). ``new`` is the tier this release split out of
    # ``hot``: those settings only affect sessions, recordings or sockets
    # created *after* the edit, and labelling them 热更新 was a lie.
    fields = [
        {
            "key": key,
            "value": render_setting(values.get(key)),
            "kind": kind_of(key),
        }
        for key in sorted(HOT_FIELDS | set(RESTART_FIELDS_ALL))
    ]
    return {
        "config_path": str(settings.config_path),
        "data_dir": str(settings.data_dir),
        "fields": fields,
    }
