"""Consistent backups and restores of a wsctl data directory.

Copying a WAL-mode SQLite file on its own can miss transactions that are still
in the ``-wal`` sidecar (or catch a torn write). We therefore take a consistent
snapshot with SQLite's online backup API and archive *that*, alongside the
recordings and, optionally, the config file.
"""

from __future__ import annotations

import contextlib
import json
import os
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
    """Refuse a database that is not SQLite or does not pass ``integrity_check``.

    The header check alone accepted a truncated file, and a truncated file was
    then installed over the live database.
    """
    with path.open("rb") as handle:
        header = handle.read(len(_SQLITE_HEADER))
    if header != _SQLITE_HEADER:
        raise BackupError("备份中的数据库文件无效")
    from urllib.parse import quote

    try:
        conn = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise BackupError(f"无法校验备份数据库：{exc}") from exc
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as exc:
        raise BackupError(f"备份数据库校验失败：{exc}") from exc
    finally:
        conn.close()
    if not row or str(row[0]) != "ok":
        raise BackupError(f"备份数据库损坏：{row[0] if row else '未知原因'}")


def _live_instances(db_path: Path, ttl: float) -> list[str]:
    """Instance leases that are still being refreshed.

    ``restore`` used to be happy to overwrite a database a *running* server had
    open. The server holds its own connection; SQLite would let the copy land
    and the next write would fail or, worse, the live process would keep
    serving rows from a file that no longer exists. The lease table is the
    product's own answer to "is a server using this?", and it also covers a
    peer instance on another host sharing the data directory.
    """
    if not db_path.is_file():
        return []
    from urllib.parse import quote

    try:
        conn = sqlite3.connect(f"file:{quote(str(db_path))}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(
            "SELECT id FROM instances WHERE heartbeat >= ?", (time.time() - ttl,)
        ).fetchall()
    except sqlite3.Error:
        # Older schema without the table: nothing to go on, not a blocker.
        return []
    finally:
        conn.close()
    return [str(row[0]) for row in rows]


def _fsync_file(path: Path) -> None:
    """Flush a staged file to disk. ``os.replace`` alone is not durability.

    A rename that reaches the filesystem but whose *contents* are still in the
    page cache after a power loss leaves a file that exists and is truncated --
    and the old database it replaced is gone.
    """
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    """Make the rename itself durable (POSIX; a no-op where unsupported)."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        # Directories cannot be fsynced on every platform (Windows).
        pass
    finally:
        os.close(fd)


def _install_database(source: Path, db_path: Path) -> None:
    """Replace ``db_path`` atomically and drop sidecars that no longer apply.

    ``shutil.copy2`` straight onto the live path left a window where a crash
    produced a half database. ``-wal``/``-shm`` are *worse*: a WAL belongs to
    the database it was written for, and replaying the old one over the new file
    is how a restore corrupts an otherwise good backup.

    The sidecars are removed *before* the rename. Removing them afterwards left
    a window -- new database, old WAL -- in which a crash (or a reader opening
    the file) would replay pages that belong to a database that no longer
    exists. The old database's committed frames are already in the ``.bak``
    snapshot, which was taken with SQLite's online backup API.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    staging = db_path.with_name(f".{db_path.name}.restore-{time.time_ns()}.tmp")
    try:
        shutil.copy2(source, staging)
        _fsync_file(staging)
        for suffix in ("-wal", "-shm", "-journal"):
            with contextlib.suppress(FileNotFoundError):
                db_path.with_name(db_path.name + suffix).unlink()
        os.replace(staging, db_path)
        _fsync_dir(db_path.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            staging.unlink()


def restore_backup(
    archive: Path,
    db_path: Path,
    recordings_dir: Path,
    *,
    config_path: Path | None = None,
    restore_config: bool = False,
    force: bool = False,
    instance_ttl: float = 30.0,
) -> dict[str, Any]:
    """Restore ``archive`` into the data directory.

    Refuses while an instance lease is alive (``instance_ttl`` seconds): a
    running server keeps its own handle on the file, and no amount of copying
    makes that safe. An existing database is only replaced with ``force`` (and
    is first kept as ``<name>.bak``), so a mistaken restore cannot silently
    destroy live data.
    """
    archive = archive.expanduser()
    if not archive.is_file():
        raise BackupError(f"备份文件不存在：{archive}")
    live = _live_instances(db_path, instance_ttl)
    if live:
        raise BackupError(
            "服务仍在运行（实例 "
            + ", ".join(live)
            + "），请先执行 wsctl stop 再恢复；运行中的进程持有数据库连接，覆盖它是不安全的"
            "（若是刚刚异常退出，请等待约 30 秒让租约过期）"
        )
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
            if db_path.exists() and not force:
                raise BackupError(
                    f"数据库已存在：{db_path}；确认后使用 --force（原库会保存为 .bak）"
                )

        # Everything that does not touch the live database happens first. A
        # failure copying recordings now leaves the database exactly as it was,
        # instead of reporting a failed restore after the most important file
        # had already been swapped.
        try:
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
        except OSError as exc:
            raise BackupError(f"恢复录制/配置失败（数据库未改动）：{exc}") from exc

        if source_db.is_file() and db_path.exists():
            # The first check ran before the archive was unpacked; a restart
            # during those minutes (a service manager, a container policy) is
            # exactly the way a live server ended up under the installer.
            live = _live_instances(db_path, instance_ttl)
            if live:
                raise BackupError(
                    "恢复过程中服务又被启动了（实例 "
                    + ", ".join(live)
                    + "），数据库未改动；请先 wsctl stop 再重试"
                )
            # Snapshot the *old* database with the online backup API: a plain
            # copy of a WAL-mode file can miss committed pages. Never reuse the
            # name: a second restore (or a retry) used to overwrite the only
            # copy of the original with the result of the first restore.
            backup_path = db_path.with_name(db_path.name + ".bak")
            if backup_path.exists():
                backup_path = db_path.with_name(
                    f"{db_path.name}.bak.{time.strftime('%Y%m%d-%H%M%S')}"
                    f"-{time.time_ns() % 1_000_000:06d}"
                )
            try:
                _snapshot_db(db_path, backup_path)
            except BackupError:
                # A corrupt old database is exactly why someone restores; keep
                # the bytes anyway rather than refusing the restore.
                shutil.copy2(db_path, backup_path)

        if source_db.is_file():
            _install_database(source_db, db_path)

    return manifest
