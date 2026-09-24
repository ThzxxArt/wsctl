"""Session recording start/stop/download and the admin recording library."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse

from wsctl.core import recording as recording_mod
from wsctl.core.store import User

from ..deps import current_user, owned_session, require_admin
from ..maintenance import active_recording_paths
from ..models import RecordingStart
from ..security import can_access

router = APIRouter(prefix="/api", tags=["recordings"])


def _scan_recordings(directory: Path) -> list[dict[str, Any]]:
    """List ``*.cast`` files (blocking; call via ``asyncio.to_thread``)."""
    if not directory.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for entry in sorted(directory.glob("*.cast")):
        try:
            stat = entry.stat()
        except OSError:
            continue
        items.append({"name": entry.name, "size": stat.st_size, "mtime": stat.st_mtime})
    return items


def _recording_path(request: Request, name: str) -> Path:
    safe = Path(name).name
    if safe != name or not safe.endswith(".cast") or "\x00" in name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件名无效")
    directory: Path = request.app.state.settings.recordings_dir
    return directory / safe


@router.post("/sessions/{sid}/recording/start")
async def start_recording(
    sid: str, body: RecordingStart, request: Request, user: User = Depends(current_user)
) -> dict[str, str]:
    session = owned_session(request, sid, user)
    if session.is_recording:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="已在录制中")
    settings = request.app.state.settings
    # The capacity gate is not optional: the auto-record path has always had
    # it, and a manual start walked straight past ``recordings_max_bytes``.
    if not await asyncio.to_thread(
        recording_mod.has_room,
        settings.recordings_dir,
        settings.recordings_max_bytes,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="录制目录已达容量上限"
        )
    path = await session.start_recording(
        settings.recordings_dir / f"{sid}.cast", record_input=body.record_input
    )
    request.app.state.audit.enqueue("recording_start", user_id=user.id, term_session_id=sid)
    return {"path": str(path)}


@router.post("/sessions/{sid}/recording/stop")
async def stop_recording(
    sid: str, request: Request, user: User = Depends(current_user)
) -> dict[str, bool]:
    session = owned_session(request, sid, user)
    if not session.is_recording:
        # ``{"ok": True}`` for "there was nothing to stop" is a claim of work
        # that never happened.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该会话未在录制")
    await session.stop_recording()
    request.app.state.audit.enqueue("recording_stop", user_id=user.id, term_session_id=sid)
    return {"ok": True}


@router.get("/sessions/{sid}/recording")
async def download_recording(
    sid: str, request: Request, user: User = Depends(current_user)
) -> FileResponse:
    """The cast for one session -- while it runs *and* after it is gone.

    A session that had ended made ``owned_session`` 404 and the only other
    door (``GET /api/recordings``) is admin-only, so a plain user could never
    get their own recording back. The row outlives the session; so should the
    download.
    """
    session = request.app.state.manager.get(sid)
    path = session.recording_path if session is not None else None
    if session is not None and not can_access(user, session):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该会话")
    if path is None or not path.is_file():
        row = await asyncio.to_thread(request.app.state.store.term_session_get, sid)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="没有录制")
        if user.role != "admin" and row.get("owner_id") != user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该会话")
        candidate = request.app.state.settings.recordings_dir / f"{sid}.cast"
        if not candidate.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="没有录制")
        path = candidate
    return FileResponse(
        path, media_type="application/x-asciicast", filename=f"{sid}.cast"
    )


@router.get("/recordings")
async def list_recordings(
    request: Request, _: User = Depends(require_admin)
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        _scan_recordings, request.app.state.settings.recordings_dir
    )


@router.get("/recordings/{name}")
async def download_recording_by_name(
    request: Request, name: str, _: User = Depends(require_admin)
) -> FileResponse:
    path = _recording_path(request, name)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="没有录制")
    return FileResponse(path, media_type="application/x-asciicast", filename=name)


@router.delete("/recordings/{name}")
async def delete_recording_by_name(
    name: str, request: Request, actor: User = Depends(require_admin)
) -> dict[str, bool]:
    path = _recording_path(request, name)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="没有录制")
    # A live recorder holds this file open and keeps appending to it. Unlinking
    # underneath it made the data vanish from disk while the cast continued to
    # be written into a deleted inode -- and ``stop`` then closed it for good.
    # The retention pruner already knew this (``active_recording_paths``); the
    # delete endpoint did not.
    active = active_recording_paths(request.app.state.manager)
    if str(path) in active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="录制进行中，请先停止录制"
        )
    await asyncio.to_thread(path.unlink, missing_ok=True)
    request.app.state.audit.enqueue("recording_delete", user_id=actor.id, payload=name)
    return {"ok": True}
