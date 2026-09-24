"""Terminal session lifecycle: list, create, kill, rename."""

from __future__ import annotations

import asyncio
import json
import shlex
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from wsctl.core import recording as recording_mod
from wsctl.core import ssh, tmux
from wsctl.core.config import Settings
from wsctl.core.pty import PtyError
from wsctl.core.session import SessionManager, SessionSpec, within_user_quota
from wsctl.core.store import User

from ..deps import (
    current_user,
    owned_session,
    resolve_owners,
    serialize_session,
)
from ..models import SessionCreate, SessionRename, SessionReopen
from ..security import can_access

router = APIRouter(prefix="/api", tags=["sessions"])


@router.get("/sessions")
async def list_sessions(
    request: Request, user: User = Depends(current_user)
) -> list[dict[str, Any]]:
    sessions = [s for s in request.app.state.manager.list_sessions() if can_access(user, s)]
    # One owner lookup for the whole page, on a worker thread: resolving each
    # row separately put N synchronous SQLite queries on the event loop.
    owners = await resolve_owners(request, sessions)
    return [serialize_session(s, owners) for s in sessions]


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    body: SessionCreate, request: Request, user: User = Depends(current_user)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    manager: SessionManager = request.app.state.manager
    store = request.app.state.store
    instance_id = request.app.state.instance_id
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
            if argv[0].startswith("-"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="argv 无效"
                )
        else:
            argv = [settings.shell]
        name = body.name or (Path(argv[0]).name if argv else "终端")
    spec = SessionSpec(
        name=name,
        argv=argv,
        command=body.command,
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
        if settings.auto_record and await asyncio.to_thread(
            recording_mod.has_room,
            settings.recordings_dir,
            settings.recordings_max_bytes,
        ):
            await session.start_recording(
                settings.recordings_dir / f"{session.id}.cast",
                record_input=settings.record_input,
            )
        await asyncio.to_thread(
            store.term_session_upsert,
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
        request.app.state.audit.enqueue(
            "session_create", user_id=user.id, term_session_id=session.id, payload=name
        )
    except Exception as exc:
        await manager.remove(session.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="创建会话失败",
        ) from exc
    request.app.state.metrics.inc("wsctl_sessions_created_total")
    return serialize_session(session, {user.id: user})


@router.post("/sessions/{sid}/reopen", status_code=status.HTTP_201_CREATED)
async def reopen_session(
    sid: str, body: SessionReopen, request: Request, user: User = Depends(current_user)
) -> dict[str, Any]:
    """Recreate a session the way ``sid`` originally ran.

    The ``argv`` comes from the *record*, not from the request body. The
    endpoint name promises "the way it originally ran"; taking the caller's
    word for it made ``sid`` decorative -- any id, including one that never
    existed or belonged to someone else, answered 201 -- while letting the
    client dictate what would be spawned. History's "reopen" is a replay of
    what was recorded, and only of that.
    """
    row = await asyncio.to_thread(request.app.state.store.term_session_get, sid)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
    if user.role != "admin" and row.get("owner_id") != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该会话")

    raw_argv = row.get("argv")
    argv: list[str] | None = None
    if isinstance(raw_argv, list):
        argv = [str(a) for a in raw_argv]
    elif isinstance(raw_argv, str):
        try:
            loaded = json.loads(raw_argv)
        except json.JSONDecodeError:
            loaded = None
        if isinstance(loaded, list):
            argv = [str(a) for a in loaded]
    if not argv or not all(a for a in argv) or argv[0].startswith("-"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="该会话没有可重开的命令记录"
        )
    backend = str(row.get("backend") or "local")
    # A reopen must never escalate what the original was allowed to do.
    if backend not in ("local", "tmux"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="重新打开只支持 local / tmux 后端",
        )
    settings: Settings = request.app.state.settings
    manager: SessionManager = request.app.state.manager
    if len(manager.list_sessions()) >= settings.max_sessions:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="会话数量已达上限"
        )
    if not within_user_quota(manager, settings.max_sessions_per_user, user.id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="该用户的会话数量已达上限"
        )
    if backend == "tmux" and not tmux.is_available():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="未安装 tmux")
    name = body.name or str(row.get("name") or Path(argv[0]).name)
    cwd = body.cwd or (str(row["cwd"]) if row.get("cwd") else settings.default_cwd)
    spec = SessionSpec(
        name=name,
        argv=list(argv),
        command=(str(row["command"]) if row.get("command") else None),
        cwd=cwd,
        backend=backend,
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
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"无法启动命令：{exc}"
        ) from exc
    await asyncio.to_thread(
        request.app.state.store.term_session_upsert,
        session.id,
        name=name,
        owner_id=user.id,
        backend=backend,
        command=spec.command,
        argv=spec.argv,
        env=spec.env,
        cwd=spec.cwd,
        idle_timeout=spec.idle_timeout,
        max_life=spec.max_life,
        instance_id=request.app.state.instance_id,
    )
    request.app.state.audit.enqueue(
        "session_create", user_id=user.id, term_session_id=session.id, payload=f"reopen {name}"
    )
    request.app.state.metrics.inc("wsctl_sessions_created_total")
    return serialize_session(session, {user.id: user})


@router.delete("/sessions/{sid}")
async def delete_session(
    sid: str, request: Request, user: User = Depends(current_user)
) -> dict[str, bool]:
    owned_session(request, sid, user)  # 404/403 guard
    # The row is written by the session's own end hook -- with this reason and
    # the real exit code -- rather than here. Writing it here as well raced the
    # hook and always claimed "被管理员终止", even when a plain owner did it.
    reason = "被管理员终止" if user.role == "admin" else "被属主终止"
    await request.app.state.manager.remove(sid, reason=reason, status="killed")
    request.app.state.audit.enqueue("session_kill", user_id=user.id, term_session_id=sid)
    return {"ok": True}


@router.patch("/sessions/{sid}")
async def rename_session(
    sid: str, body: SessionRename, request: Request, user: User = Depends(current_user)
) -> dict[str, Any]:
    session = owned_session(request, sid, user)
    name = session.rename(body.name)
    await asyncio.to_thread(
        request.app.state.store.term_session_upsert,
        sid,
        name=name,
        owner_id=session.owner_id,
        cwd=session.spec.cwd,
        instance_id=request.app.state.instance_id,
    )
    # Tell every attached client so a rename is not invisible until the next
    # reconnect. Two tabs on one session used to keep showing different names.
    await session.notify({"type": "renamed", "session": sid, "name": name})
    return serialize_session(session)
