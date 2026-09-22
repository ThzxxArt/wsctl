"""Traversal-proof filesystem helpers for the web file panel."""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: How many entries a single listing returns before it is truncated. A hostile
#: or accidental ``ls`` of a huge directory must not build a giant response.
DEFAULT_LIST_LIMIT = 2000


class FsError(Exception):
    """Raised for invalid paths or filesystem operations."""


def safe_resolve(root: Path, rel: str | None) -> Path:
    """Resolve ``rel`` under ``root``, rejecting anything that escapes it.

    Symlinks are followed before the containment check, so links that point
    outside the root are rejected too.
    """
    root_resolved = root.resolve()
    rel = (rel or "").strip()
    if rel in ("", ".", "/"):
        return root_resolved
    if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        raise FsError("absolute paths are not allowed")
    candidate = (root_resolved / rel).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise FsError("path escapes the configured root")
    return candidate


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
            stat = child.stat()
            is_dir = child.is_dir()
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
