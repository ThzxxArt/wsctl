"""Traversal-proof web file panel.

The panel used to offer exactly three verbs -- list, download, upload -- and
one of them (upload) had no progress, no cancel and no way to organise
anything afterwards. These routes complete the file-management surface while
keeping every path through :func:`~wsctl.core.fs.safe_resolve` and refusing
symlinks at every write.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from wsctl.core import fs as fs_mod
from wsctl.core.store import User

from ..deps import current_user

router = APIRouter(prefix="/api", tags=["files"])


class NameBody(BaseModel):
    name: str


class RenameBody(BaseModel):
    path: str
    name: str


class ContentBody(BaseModel):
    path: str
    content: str


class _UploadTooLarge(Exception):
    """Raised when an upload exceeds ``file_max_upload``."""


def _copy_upload(src: Any, fd: int, limit: int, chunk_size: int = 1 << 20) -> int:
    """Copy an uploaded file to ``fd`` on a worker thread (blocking I/O).

    The bytes are ``fsync``ed *before* the handle is closed, so the rename that
    follows can never publish a name whose contents are still in the page
    cache: a crash right after ``os.replace`` would leave a zero-length file
    that looks complete. ``fd`` is always closed. Raises
    :class:`_UploadTooLarge` past ``limit``.
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
        out.flush()
        os.fsync(out.fileno())
        return size
    finally:
        out.close()


def _fs_error(exc: fs_mod.FsError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/files")
async def list_files(
    request: Request,
    user: User = Depends(current_user),
    path: str = "",
    offset: int = 0,
    limit: int = 0,
    contains: str | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """One page of a directory, optionally filtered.

    ``limit=0`` keeps the old "default page size" behaviour. Paging exists now
    because the previous endpoint hard-stopped at 2000 entries with no way to
    reach entry 2001.
    """
    root = request.app.state.settings.files_root
    try:
        page = await asyncio.to_thread(
            fs_mod.list_dir_paged,
            root,
            path,
            offset=max(0, offset),
            limit=limit or None,
            contains=contains,
            kind=kind,
        )
    except fs_mod.FsError as exc:
        raise _fs_error(exc) from exc
    page["path"] = path.strip().lstrip("/")
    page["root"] = str(root)
    return page


@router.get("/files/download")
async def download_file(
    request: Request, user: User = Depends(current_user), path: str = ""
) -> FileResponse:
    root = request.app.state.settings.files_root
    try:
        target = fs_mod.safe_resolve(root, path)
    except fs_mod.FsError as exc:
        raise _fs_error(exc) from exc
    if not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="不是文件")
    return FileResponse(target, filename=target.name)


@router.get("/files/preview")
async def preview_file(
    request: Request, user: User = Depends(current_user), path: str = ""
) -> PlainTextResponse:
    """A small text file's contents, so the panel is not download-or-nothing."""
    root = request.app.state.settings.files_root
    try:
        text, encoding = await asyncio.to_thread(fs_mod.read_text_file, root, path)
    except fs_mod.FsError as exc:
        raise _fs_error(exc) from exc
    return PlainTextResponse(text, headers={"X-Wsctl-Encoding": encoding})


@router.put("/files/content")
async def write_file(
    body: ContentBody, request: Request, user: User = Depends(current_user)
) -> dict[str, Any]:
    """Replace a small text file (atomic: sidecar + ``os.replace``)."""
    root = request.app.state.settings.files_root
    try:
        size = await asyncio.to_thread(fs_mod.write_text_file, root, body.path, body.content)
    except fs_mod.FsError as exc:
        raise _fs_error(exc) from exc
    request.app.state.metrics.inc("wsctl_file_edits_total")
    request.app.state.audit.enqueue(
        "file_edit",
        user_id=user.id,
        payload=fs_mod.relative_to(root, fs_mod.safe_resolve(root, body.path))[:256],
    )
    return {"size": size}


@router.post("/files/mkdir", status_code=status.HTTP_201_CREATED)
async def make_dir(
    body: NameBody, request: Request, user: User = Depends(current_user), path: str = ""
) -> dict[str, Any]:
    root = request.app.state.settings.files_root
    try:
        target = await asyncio.to_thread(fs_mod.make_dir, root, path, body.name)
    except fs_mod.FsError as exc:
        raise _fs_error(exc) from exc
    request.app.state.audit.enqueue(
        "file_mkdir",
        user_id=user.id,
        payload=fs_mod.relative_to(root, target)[:256],
    )
    return {"name": target.name, "path": fs_mod.relative_to(root, target)}


@router.post("/files/rename")
async def rename_entry(
    body: RenameBody, request: Request, user: User = Depends(current_user)
) -> dict[str, Any]:
    root = request.app.state.settings.files_root
    try:
        target = await asyncio.to_thread(fs_mod.rename_entry, root, body.path, body.name)
    except (fs_mod.FsError, OSError) as exc:
        if isinstance(exc, OSError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"重命名失败：{exc}"
            ) from exc
        raise _fs_error(exc) from exc
    request.app.state.audit.enqueue(
        "file_rename",
        user_id=user.id,
        payload=f"{body.path} -> {fs_mod.relative_to(root, target)}"[:256],
    )
    return {"name": target.name, "path": fs_mod.relative_to(root, target)}


@router.delete("/files")
async def delete_entry(
    request: Request,
    user: User = Depends(current_user),
    path: str = "",
    name: str = "",
) -> dict[str, bool]:
    """Delete one entry.

    Only a file, or a directory that is already empty: recursive delete is
    deliberately absent so one misplaced click cannot empty a home directory.
    """
    root = request.app.state.settings.files_root
    target_rel = f"{path.rstrip('/')}/{name}" if path.strip("/") and name else (name or path)
    try:
        await asyncio.to_thread(fs_mod.delete_entry, root, target_rel)
    except (fs_mod.FsError, OSError) as exc:
        detail = str(exc)
        if isinstance(exc, OSError) and getattr(exc, "errno", None) in (39, 66):
            detail = "目录非空（为安全起见不提供递归删除，请先进入目录逐项删除）"
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc
    request.app.state.audit.enqueue(
        "file_delete", user_id=user.id, payload=target_rel[:256]
    )
    return {"ok": True}


@router.post("/files/upload", status_code=status.HTTP_201_CREATED)
async def upload_file(
    request: Request,
    user: User = Depends(current_user),
    file: UploadFile = File(...),
    path: str = Form(""),
    overwrite: bool = Form(False),
) -> dict[str, Any]:
    settings = request.app.state.settings
    root = settings.files_root
    name = Path(file.filename or "").name
    if not name or name in (".", ".."):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件名无效")
    try:
        directory = fs_mod.safe_resolve(root, path)
    except fs_mod.FsError as exc:
        raise _fs_error(exc) from exc
    if not directory.is_dir():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="不是目录")

    dest = directory / name
    # A symlink at the destination is a security problem (it would redirect
    # the write), so it is refused outright rather than offered for
    # "overwrite"; the O_NOFOLLOW open below re-checks against a race.
    if dest.is_symlink():
        await file.close()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="拒绝覆盖符号链接或非法路径",
        )
    if dest.exists() and not overwrite:
        await file.close()
        # Never silently destroy an existing file: the client must opt in.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"文件已存在：{name}（确认后可覆盖）",
        )
    # Write to a sidecar and rename into place. Writing straight into ``dest``
    # with O_TRUNC meant a concurrent download could read a half-written file
    # for the whole duration of a large upload. ``os.replace`` is atomic on the
    # same filesystem, so a reader sees either the old bytes or the new ones.
    staging = directory / f".{name}.wsctl-upload"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(staging, flags, 0o600)
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
        os.replace(staging, dest)
    except _UploadTooLarge as exc:
        staging.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail="文件过大") from exc
    except Exception:
        # Do not leave a half-written sidecar behind on any other failure.
        staging.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    request.app.state.metrics.inc("wsctl_uploads_total")
    request.app.state.audit.enqueue(
        "file_upload",
        user_id=user.id,
        payload=fs_mod.relative_to(root, dest)[:256],
    )
    return {"name": name, "size": size}
