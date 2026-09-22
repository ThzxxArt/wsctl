"""Consistent backups and safe restores."""

from __future__ import annotations

import sqlite3
import tarfile
from pathlib import Path

import pytest

from wsctl.core import backup
from wsctl.core.store import Store


def _make_store(path: Path) -> Store:
    store = Store(path)
    store.user_create("alice", "password123", role="admin")
    store.user_create("bob", "password123")
    return store


def test_backup_snapshot_includes_uncheckpointed_wal(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    store = _make_store(data / "wsctl.db")
    # Keep the connection open: the write lives in the WAL sidecar, which a plain
    # file copy could miss.
    try:
        out = tmp_path / "bak.tar.gz"
        backup.create_backup(data / "wsctl.db", data / "recordings", out, version="t")
    finally:
        store.close()

    with tarfile.open(out) as tar:
        names = tar.getnames()
        assert "wsctl.db" in names
        assert "manifest.json" in names
        tar.extract("wsctl.db", path=tmp_path / "extracted")

    conn = sqlite3.connect(tmp_path / "extracted" / "wsctl.db")
    try:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        conn.close()
    assert count == 2


def test_restore_roundtrip_with_recordings(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    store = _make_store(data / "wsctl.db")
    store.close()
    recordings = data / "recordings"
    recordings.mkdir()
    (recordings / "a.cast").write_text('{"version": 2}\n', encoding="utf-8")

    out = tmp_path / "bak.tar.gz"
    backup.create_backup(data / "wsctl.db", recordings, out, version="t")

    store = Store(data / "wsctl.db")
    store.user_delete("alice")
    store.close()

    manifest = backup.restore_backup(out, data / "wsctl.db", recordings, force=True)
    assert manifest["has_db"] is True

    store = Store(data / "wsctl.db")
    try:
        assert store.user_get("alice") is not None
    finally:
        store.close()
    assert (recordings / "a.cast").is_file()


def test_restore_refuses_to_clobber_without_force(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    store = _make_store(data / "wsctl.db")
    store.close()
    out = tmp_path / "bak.tar.gz"
    backup.create_backup(data / "wsctl.db", data / "recordings", out)
    with pytest.raises(backup.BackupError):
        backup.restore_backup(out, data / "wsctl.db", data / "recordings")


def test_restore_keeps_a_backup_of_the_previous_db(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    store = _make_store(data / "wsctl.db")
    store.close()
    out = tmp_path / "bak.tar.gz"
    backup.create_backup(data / "wsctl.db", data / "recordings", out)
    backup.restore_backup(out, data / "wsctl.db", data / "recordings", force=True)
    assert (data / "wsctl.db.bak").is_file()


def test_restore_rejects_traversal_members(tmp_path: Path) -> None:
    evil = tmp_path / "evil.tar.gz"
    payload = tmp_path / "payload"
    payload.write_text("x")
    with tarfile.open(evil, "w:gz") as tar:
        tar.add(payload, arcname="../escape")
    with pytest.raises(backup.BackupError):
        backup.restore_backup(evil, tmp_path / "db", tmp_path / "rec", force=True)


def test_restore_rejects_non_sqlite_database(tmp_path: Path) -> None:
    bad = tmp_path / "bad.tar.gz"
    fake = tmp_path / "wsctl.db"
    fake.write_text("not a database")
    with tarfile.open(bad, "w:gz") as tar:
        tar.add(fake, arcname="wsctl.db")
    with pytest.raises(backup.BackupError):
        backup.restore_backup(bad, tmp_path / "db", tmp_path / "rec", force=True)


def test_backup_without_database_is_allowed(tmp_path: Path) -> None:
    out = tmp_path / "empty.tar.gz"
    backup.create_backup(tmp_path / "missing.db", tmp_path / "rec", out)
    with tarfile.open(out) as tar:
        assert "manifest.json" in tar.getnames()
        assert "wsctl.db" not in tar.getnames()
