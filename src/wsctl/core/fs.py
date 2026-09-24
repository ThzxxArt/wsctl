"""Traversal-proof filesystem helpers for the web file panel."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

#: How many entries a single listing returns before it is truncated. A hostile
#: or accidental ``ls`` of a huge directory must not build a giant response.
DEFAULT_LIST_LIMIT = 2000


class FsError(Exception):
    """Raised for invalid paths or filesystem operations."""


def _lexists(path: Path) -> bool:
    """Does ``path`` exist, counting a dangling symlink as existing?

    ``Path.exists()`` follows links, so a symlink whose target has gone away
    looks absent -- and an "absent" target is exactly what a clobbering
    ``rename`` then silently replaces.
    """
    try:
        os.lstat(path)
    except OSError:
        return False
    return True


def rename_noreplace(source: Path, target: Path) -> None:
    """Rename ``source`` to ``target``, refusing to clobber an existing entry.

    ``os.rename`` silently replaces its target on POSIX: two concurrent renames
    to the same new name both pass an ``exists()`` check and the second one
    destroys the first one's file with no error anywhere. ``os.link`` fails
    atomically on an existing name, so link-then-unlink is a rename that cannot
    clobber. Filesystems without hard links (directories, FAT, some network
    mounts) fall back to a checked rename -- narrower than the atomic form, but
    better than refusing to work at all.
    """
    if _lexists(target):
        raise FsError("已存在同名条目")
    try:
        os.link(source, target)
    except FileExistsError as exc:
        # The atomic half of the contract: the name is taken *right now*.
        # This must not fall through to the rename fallback below -- that is
        # exactly the clobbering path this function exists to avoid.
        raise FsError("已存在同名条目") from exc
    except (OSError, NotImplementedError):
        try:
            source.rename(target)
        except FileExistsError as exc:
            raise FsError("已存在同名条目") from exc
        return
    source.unlink()


def safe_resolve(root: Path, rel: str | None) -> Path:
    """Resolve ``rel`` under ``root``, rejecting anything that escapes it.

    Symlinks that point outside the root are rejected: containment is decided
    on the *resolved* path. The caller, however, gets the **lexical** path
    back. That split is deliberate and is the whole point -- operating on the
    resolved path made every ``is_symlink()`` guard in this module dead code
    (``.resolve()`` had already erased the link), so "拒绝操作符号链接" never
    fired and a delete/rename on a link acted on whatever it pointed at.
    """
    root_resolved = root.resolve()
    rel = (rel or "").strip()
    if rel in ("", ".", "/"):
        return root_resolved
    if "\x00" in rel:
        # ``Path.resolve()`` raises ValueError on a NUL byte, which no caller
        # here catches -- a 500 instead of a 400.
        raise FsError("名称无效")
    if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        raise FsError("absolute paths are not allowed")
    lexical = root_resolved / rel
    candidate = lexical.resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise FsError("path escapes the configured root")
    return lexical


def list_dir(
    root: Path, rel: str | None, *, limit: int | None = None
) -> tuple[list[dict[str, Any]], bool]:
    """List ``rel`` under ``root`` as ``(entries, truncated)``.

    Directory entries sort before files, then case-insensitively by name. When
    more than ``limit`` entries exist the listing is cut short and ``truncated``
    is true so the caller can say so instead of silently hiding files.

    ``limit`` is resolved at call time so the module-level default stays
    tunable (by configuration or by a test) after import.
    """
    if limit is None:
        limit = DEFAULT_LIST_LIMIT
    target = safe_resolve(root, rel)
    if not target.is_dir():
        raise FsError("not a directory")
    entries: list[dict[str, Any]] = []
    truncated = False
    for child in target.iterdir():
        try:
            # ``lstat``: a symlink must not advertise its *target's* size and
            # mtime (which may be outside the root). The listing shows the
            # link; following it is a separate, guarded step.
            stat = child.lstat()
            is_dir = child.is_dir() and not child.is_symlink()
        except OSError:
            continue
        entries.append(
            {
                "name": child.name,
                "type": "dir" if is_dir else "file",
                "size": int(stat.st_size),
                "mtime": float(stat.st_mtime),
            }
        )
    entries.sort(key=lambda e: (e["type"] != "dir", str(e["name"]).lower()))
    if limit > 0 and len(entries) > limit:
        entries = entries[:limit]
        truncated = True
    return entries, truncated


#: Refuse to read a "text" file larger than this into memory for preview.
PREVIEW_MAX_BYTES = 1024 * 1024
#: Refuse to accept a whole-file edit larger than this.
EDIT_MAX_BYTES = 1024 * 1024


def validate_name(name: str) -> str:
    """A single path component that cannot escape or confuse the panel."""
    cleaned = (name or "").strip()
    if not cleaned or cleaned in (".", ".."):
        raise FsError("名称无效")
    if "/" in cleaned or "\\" in cleaned or "\x00" in cleaned:
        raise FsError("名称不能包含路径分隔符")
    if cleaned.startswith(".wsctl-"):
        raise FsError("该名称前缀被系统保留")
    return cleaned


def make_dir(root: Path, rel: str, name: str) -> Path:
    """Create ``rel/<name>``; refuse an existing entry rather than merging."""
    parent = safe_resolve(root, rel)
    if not parent.is_dir():
        raise FsError("不是目录")
    clean = validate_name(name)
    # Re-resolve so a crafted ``name`` cannot walk out even if validate_name
    # is ever weakened. One validation, one resolved target.
    target = safe_resolve(root, _join(rel, clean))
    if target.exists():
        raise FsError("已存在同名条目")
    try:
        target.mkdir(parents=False, exist_ok=False)
    except FileExistsError as exc:
        # Lost the race to a concurrent request. ``mkdir`` is the authority
        # (the ``exists`` check above is only a fast path); reporting a 500 for
        # "someone else just created it" would be wrong.
        raise FsError("已存在同名条目") from exc
    return target


def rename_entry(root: Path, rel: str, new_name: str) -> Path:
    """Rename one entry inside its own directory (never across directories)."""
    source = safe_resolve(root, rel)
    if source == root.resolve():
        raise FsError("不能重命名根目录")
    new_name = validate_name(new_name)
    target = source.parent / new_name
    if target == source:
        return source
    # ``safe_resolve`` hands back the lexical path, so these guards finally
    # mean what they say: the link itself is refused, not whatever it points at.
    if target.is_symlink() or source.is_symlink():
        raise FsError("拒绝操作符号链接")
    if _lexists(target):
        raise FsError("已存在同名条目")
    rename_noreplace(source, target)
    return target


def delete_entry(root: Path, rel: str) -> None:
    """Delete a file, or a directory that is already empty.

    Recursive delete is deliberately absent: one misplaced click must not be
    able to empty a home directory. Emptying a directory first, then deleting
    it, is two deliberate acts.
    """
    target = safe_resolve(root, rel)
    if target == root.resolve():
        raise FsError("不能删除根目录")
    if target.is_symlink():
        target.unlink(missing_ok=True)
        return
    if target.is_dir():
        target.rmdir()  # OSError -> FsError below if not empty
        return
    target.unlink(missing_ok=True)


def read_text_file(root: Path, rel: str, *, limit: int = PREVIEW_MAX_BYTES) -> tuple[str, str]:
    """Return ``(text, encoding)`` for preview. Refuses binaries and big files."""
    target = safe_resolve(root, rel)
    if not target.is_file():
        raise FsError("不是文件")
    size = target.stat().st_size
    if size > limit:
        raise FsError(f"文件过大，无法预览（上限 {limit // (1024 * 1024)} MB）")
    raw = target.read_bytes()
    if b"\x00" in raw[:8192]:
        raise FsError("二进制文件无法预览")
    for encoding in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace"), "utf-8"


def write_text_file(root: Path, rel: str, content: str) -> int:
    """Replace a small text file atomically (sidecar + ``os.replace``)."""
    target = safe_resolve(root, rel)
    if target.is_symlink():
        # Checked first and on the *lexical* path: writing through a symlink
        # would land in whatever it points at.
        raise FsError("拒绝写入符号链接")
    if not target.is_file():
        raise FsError("不是文件")
    data = content.encode("utf-8")
    if len(data) > EDIT_MAX_BYTES:
        raise FsError(f"内容过大（上限 {EDIT_MAX_BYTES // 1024} KB）")
    # Unique + ``O_EXCL``: a fixed sidecar name let two concurrent edits of the
    # same file interleave their bytes into one staging file and publish the
    # mixture, and a hostile neighbour could pre-create the predictable name to
    # catch someone else's draft.
    staging = target.parent / f".{target.name}.wsctl-{secrets.token_hex(8)}.edit"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
    )
    fd = os.open(staging, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, target)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
    return len(data)


def list_dir_paged(
    root: Path,
    rel: str | None,
    *,
    offset: int = 0,
    limit: int | None = None,
    contains: str | None = None,
    kind: str | None = None,
) -> dict[str, object]:
    """A page of ``rel`` with an explicit ``total``, plus the old truncation flag.

    The previous endpoint hard-stopped at 2000 entries and said so; there was
    no way to reach entry 2001. Paging keeps the response bounded *and*
    reachable.
    """
    entries, _truncated = list_dir(root, rel, limit=0)  # 0 = no cut
    if contains:
        needle = contains.lower()
        entries = [e for e in entries if needle in str(e["name"]).lower()]
    if kind in ("dir", "file"):
        entries = [e for e in entries if e["type"] == kind]
    total = len(entries)
    if limit is None:
        limit = DEFAULT_LIST_LIMIT
    entries = entries[offset : offset + limit] if limit > 0 else entries[offset:]
    return {
        "entries": entries,
        "total": total,
        "offset": max(0, offset),
        "limit": limit,
        "truncated": total > max(0, offset) + len(entries),
    }


def _join(rel: str | None, name: str) -> str:
    rel = (rel or "").strip().strip("/")
    return f"{rel}/{name}" if rel else name


def relative_to(root: Path, path: Path) -> str:
    """``path`` as a POSIX-style relative path, or ``""`` if it escapes ``root``.

    Always uses ``/`` regardless of platform: these strings end up in audit
    payloads and share links, and ``a\\b.txt`` on one host versus ``a/b.txt``
    on another makes the trail needlessly hard to correlate.
    """
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return ""
