"""Programmatic Alembic runner.

There is no separate ``alembic.ini``: the URL comes from ``Settings`` and the
script directory is packaged next to this module. That removes an entire class
of "the CLI migrated database A while the server used database B" defects.

Upgrading is safe to run on a database created by 0.1.30 (which has no
``alembic_version`` table): the baseline migration is idempotent ``CREATE TABLE
IF NOT EXISTS`` and the v6 migration checks columns before adding them.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError, OperationalError

from wsctl.migrations import MIGRATIONS_DIR

from .database import _normalise_url

log = logging.getLogger("wsctl.migrations")


def _sqlite_path(url: str) -> Path | None:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return None
    raw = url[len(prefix) :]
    return Path(raw)


@contextlib.contextmanager
def _migration_lock(url: str) -> Iterator[None]:
    """Serialise migrations across processes sharing one SQLite file.

    Alembic's own version-table update is not a compare-and-swap: two servers
    starting at once can both read ``0001`` and race the update to ``0002``.
    The retry loop below makes the loser survive, but a lock is what makes the
    operation *ordered*. The lock file is next to the database, so it follows
    the data directory and is cleaned with it.
    """
    path = _sqlite_path(url)
    if path is None:
        yield
        return
    lock_path = path.with_name(path.name + ".migrate.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+", encoding="utf-8")  # noqa: SIM115 - closed in finally
    try:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):  # pragma: no cover - Windows
            try:
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
            except (ImportError, OSError):
                pass
        yield
    finally:
        with contextlib.suppress(OSError, ValueError):
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
        handle.close()


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    # ``Config.set_main_option`` performs ``%`` interpolation; escape a literal
    # percent in a path or password before handing the URL to Alembic.
    cfg.set_main_option("sqlalchemy.url", _normalise_url(url).replace("%", "%%"))
    return cfg


def _secure_sqlite(url: str) -> None:
    """Tighten a database created by Alembic to owner-only.

    A migration run from the CLI may be the first thing to create the file.
    SQLite then uses the process umask (0644) and the password hashes are
    world-readable until the server happens to start.
    """
    path = _sqlite_path(url)
    if path is None:
        return
    for suffix in ("", "-wal", "-shm"):
        with contextlib.suppress(OSError):
            os.chmod(path.with_name(path.name + suffix), 0o600)


def upgrade_database(url: str) -> str:
    """Upgrade ``url`` to ``head`` and return the resulting revision.

    Two instances may start at once against one data directory (the documented
    SO_REUSEPORT/rolling-restart mode). Alembic creates ``alembic_version``
    before it takes any application lock, so the loser of that race sees
    "table alembic_version already exists". The DDL is idempotent, so retrying
    the whole upgrade is safe and is what makes concurrent startup work.
    """
    cfg = alembic_config(url)
    deadline = time.monotonic() + 20.0
    with _migration_lock(url):
        backup_before_migration(url)
        while True:
            try:
                command.upgrade(cfg, "head")
                break
            except (OperationalError, IntegrityError, CommandError) as exc:
                text = str(exc).lower()
                retryable = (
                    "already exists" in text
                    or "locked" in text
                    or "busy" in text
                    or ("unique constraint failed" in text and "alembic_version" in text)
                    or "overlaps with other requested revisions" in text
                    or "not a valid upgrade target" in text
                )
                if not retryable:
                    raise
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
    _secure_sqlite(url)
    return current_revision(url)


def downgrade_database(url: str, revision: str = "base") -> str:
    cfg = alembic_config(url)
    command.downgrade(cfg, revision)
    _secure_sqlite(url)
    return current_revision(url)


def current_revision(url: str) -> str:
    engine = create_engine(_normalise_url(url), future=True)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            return context.get_current_revision() or ""
    finally:
        engine.dispose()


def head_revision(url: str) -> str:
    """The newest revision in the packaged migration chain."""
    script = ScriptDirectory.from_config(alembic_config(url))
    return script.get_current_head() or ""


def backup_before_migration(url: str) -> Path | None:
    """Take a consistent SQLite snapshot before applying a pending migration.

    The roadmap requires a backup before a schema change, and an operator who
    upgrades by restarting the service never runs ``wsctl backup`` first. The
    server therefore makes one itself, next to the database, using SQLite's
    online backup API -- copying the file while WAL is active would miss
    committed pages that still live in the WAL.

    A database already at head, or an empty/new one, produces no backup.
    """
    path = _sqlite_path(url)
    if path is None or not path.is_file() or path.stat().st_size == 0:
        return None
    current = current_revision(url)
    target = head_revision(url)
    if not target or current == target:
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    label = current or "v5"
    backup_path = path.with_name(f"{path.name}.pre-{label}-{stamp}.bak")
    source = sqlite3.connect(str(path))
    destination = sqlite3.connect(str(backup_path))
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    with contextlib.suppress(OSError):
        os.chmod(backup_path, 0o600)
    log.warning("数据库迁移前已备份：%s -> %s", path, backup_path)
    # Keep the directory tidy: three snapshots are enough to recover from a
    # bad upgrade, and an unbounded pile of multi-megabyte .bak files is a
    # disk-full incident of its own.
    snapshots = []
    for item in path.parent.glob(f"{path.name}.pre-*-*.bak"):
        try:
            snapshots.append((item.stat().st_mtime, item))
        except OSError:
            continue
    snapshots.sort(key=lambda pair: pair[0])
    for _mtime, stale in snapshots[:-3]:
        with contextlib.suppress(OSError):
            stale.unlink()
    return backup_path


def has_alembic_version(url: str) -> bool:
    engine = create_engine(_normalise_url(url), future=True)
    try:
        from sqlalchemy import inspect

        return "alembic_version" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def migration_script_dir() -> Path:
    return MIGRATIONS_DIR


__all__ = [
    "alembic_config",
    "backup_before_migration",
    "current_revision",
    "downgrade_database",
    "has_alembic_version",
    "head_revision",
    "migration_script_dir",
    "upgrade_database",
]
