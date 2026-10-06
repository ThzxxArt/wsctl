"""Effective configuration view, hot reload and online editing."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from wsctl.core.config import HOT_FIELDS, kind_of
from wsctl.core.config import RESTART_FIELDS as RESTART_FIELDS_ALL
from wsctl.core.store import User

from ..deps import render_setting, require_admin
from ..models import ConfigSetRequest

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
            # The typed value, so the editor can pick the right control
            # (a checkbox for a bool, a number box for an int) instead of
            # handing every field a free-text box and re-parsing strings.
            "raw": values.get(key),
            "kind": kind_of(key),
        }
        for key in sorted(HOT_FIELDS | set(RESTART_FIELDS_ALL))
    ]
    return {
        "config_path": str(settings.config_path),
        "data_dir": str(settings.data_dir),
        "fields": fields,
    }


@router.put("/config")
async def put_config(
    body: ConfigSetRequest, request: Request, actor: User = Depends(require_admin)
) -> dict[str, Any]:
    """Write configuration values to the config file, then apply what can be.

    The write goes through :mod:`wsctl.core.config_edit` -- the same code the
    CLI's ``config set`` uses -- so both entry points keep the author's inline
    comments, type-check the value, validate the whole file and roll back
    rather than publish a document the server cannot start from.

    Only fields that a reload can actually reach are writable. A ``restart``
    field is refused with that reason rather than accepted and silently doing
    nothing until someone remembers to restart.
    """
    config_edit = _config_edit()
    settings = request.app.state.settings
    path = settings.config_path

    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    written: list[str] = []
    for item in body.items:
        kind = kind_of(item.key)
        if kind == "restart":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"配置项 {item.key} 需要修改配置文件并重启服务后生效，"
                    "不支持在线写入"
                ),
            )
        try:
            text = config_edit.apply_key(text, item.key, item.value)
        except config_edit.ConfigEditError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        written.append(item.key)

    # Publish once for the whole batch: a reader never sees a half-applied set.
    try:
        config_edit.write_text_atomic(path, text)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"写入配置文件失败：{exc}",
        ) from exc

    request.app.state.audit.enqueue(
        "config_set",
        user_id=actor.id,
        payload=",".join(written)[:256],
    )

    changed: list[str] = []
    errors: list[str] = []
    if body.reload:
        changed, errors = await asyncio.to_thread(request.app.state.reload_config)
    return {"written": written, "changed": changed, "errors": errors}


def _config_edit() -> Any:
    """Import the writer lazily: config reads must not pay for it."""
    from wsctl.core import config_edit as config_edit_mod

    return config_edit_mod
