"""Consistent backups and restores of a wsctl data directory.

Copying a WAL-mode SQLite file on its own can miss transactions that are still
in the ``-wal`` sidecar (or catch a torn write). We therefore take a consistent
snapshot with SQLite's online backup API and archive *that*, alongside the
recordings and, optionally, the config file.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

MANIFEST = "manifest.json"
_SQLITE_HEADER = b"SQLite format 3\x00"


class BackupError(RuntimeError):
    """Raised when a backup cannot be created or safely restored."""


def _snapshot_db(src: Path, dst: Path) -> bool:
    """Write a consistent copy of ``src`` to ``dst`` (no-op if ``src`` is absent)."""
    if not src.is_file():
        return False
    try:
        # The URI must be percent-encoded: a data directory whose path contains
        # ``?`` or ``#`` (both legal in a POSIX filename) would otherwise be
        # parsed as a query string or a fragment and open the wrong thing.
        from urllib.parse import quote

        source = sqlite3.connect(f"file:{quote(str(src))}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise BackupError(f"无法打开数据库 {src}：{exc}") from exc
    try:
        dest = sqlite3.connect(dst)
        try:
            source.backup(dest)
        finally:
            dest.close()
    except sqlite3.Error as exc:
        raise BackupError(f"数据库快照失败：{exc}") from exc
    finally:
        source.close()
    return True


def _skip_links(member: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """Drop symlinks from an archive instead of storing them.

    ``create`` used to archive links as links while ``restore`` rejected any
    archive containing one -- so a single symlink in the recordings directory
    (a NAS mount, a cross-volume archive) produced a backup that could never
    be restored. Skipping them is also the safer of the two repairs: following
    them instead would pull whatever they point at (possibly outside the data
    directory) into the archive.
    """
    if member.issym() or member.islnk():
        return None
    return member


def create_backup(
    db_path: Path,
    recordings_dir: Path,
    output: Path,
    *,
    config_path: Path | None = None,
    include_config: bool = False,
    version: str = "",
) -> Path:
    """Archive a consistent snapshot of the data directory to ``output``."""
    output = output.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wsctl-backup-") as tmp:
        staging = Path(tmp)
        snapshot = staging / "wsctl.db"
        has_db = _snapshot_db(db_path, snapshot)
        has_config = bool(include_config and config_path and config_path.is_file())
        manifest: dict[str, Any] = {
            "version": version,
            "created_at": time.time(),
            "has_db": has_db,
            "has_recordings": recordings_dir.is_dir(),
            "has_config": has_config,
        }
        manifest_path = staging / MANIFEST
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with tarfile.open(output, "w:gz") as tar:
            if has_db:
                tar.add(snapshot, arcname="wsctl.db")
            tar.add(manifest_path, arcname=MANIFEST)
            if has_config and config_path is not None:
                tar.add(config_path, arcname="config.toml")
            if recordings_dir.is_dir():
                tar.add(recordings_dir, arcname="recordings", filter=_skip_links)
    return output


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract without allowing path traversal or links out of ``dest``.

    ``create`` no longer stores links (see :func:`_skip_links`); this stays as
    the second line of defence for archives produced by older versions or by
    hand.
    """
    root = dest.resolve()
    for member in tar.getmembers():
        if member.issym() or member.islnk():
            raise BackupError(f"备份包含链接，已拒绝：{member.name}")
        target = (root / member.name).resolve()
        if target != root and root not in target.parents:
            raise BackupError(f"备份包含非法路径：{member.name}")
    try:
        tar.extractall(dest, filter="data")
    except TypeError:  # Python < 3.12 has no filter argument
        tar.extractall(dest)


def _verify_sqlite(path: Path) -> None:
    with path.open("rb") as handle:
        header = handle.read(len(_SQLITE_HEADER))
    if header != _SQLITE_HEADER:
        raise BackupError("备份中的数据库文件无效")


def restore_backup(
    archive: Path,
    db_path: Path,
    recordings_dir: Path,
    *,
    config_path: Path | None = None,
    restore_config: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Restore ``archive`` into the data directory.

    An existing database is only replaced with ``force`` (and is first kept as
    ``<name>.bak``), so a mistaken restore cannot silently destroy live data.
    """
    archive = archive.expanduser()
    if not archive.is_file():
        raise BackupError(f"备份文件不存在：{archive}")
    with tempfile.TemporaryDirectory(prefix="wsctl-restore-") as tmp:
        staging = Path(tmp)
        try:
            with tarfile.open(archive, "r:gz") as tar:
                _safe_extract(tar, staging)
        except (tarfile.TarError, OSError) as exc:
            raise BackupError(f"无法读取备份：{exc}") from exc

        manifest: dict[str, Any] = {}
        manifest_path = staging / MANIFEST
        if manifest_path.is_file():
            with contextlib.suppress(OSError, json.JSONDecodeError):
                loaded = json.loads(manifest_path.read_text("utf-8"))
                if isinstance(loaded, dict):
                    manifest = loaded

        source_db = staging / "wsctl.db"
        if source_db.is_file():
            _verify_sqlite(source_db)
            if db_path.exists():
                if not force:
                    raise BackupError(
                        f"数据库已存在：{db_path}；确认后使用 --force（原库会保存为 .bak）"
                    )
                shutil.copy2(db_path, db_path.with_name(db_path.name + ".bak"))
            db_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_db, db_path)

        source_rec = staging / "recordings"
        if source_rec.is_dir():
            recordings_dir.mkdir(parents=True, exist_ok=True)
            for entry in source_rec.iterdir():
                if entry.is_file():
                    shutil.copy2(entry, recordings_dir / entry.name)

        source_cfg = staging / "config.toml"
        if restore_config and source_cfg.is_file() and config_path is not None:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_cfg, config_path)

    return manifest
