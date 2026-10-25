"""Consistent backups and safe restores."""

from __future__ import annotations

import os
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
    payload.write_text("x", encoding="utf-8")
    with tarfile.open(evil, "w:gz") as tar:
        tar.add(payload, arcname="../escape")
    with pytest.raises(backup.BackupError):
        backup.restore_backup(evil, tmp_path / "db", tmp_path / "rec", force=True)


def test_restore_rejects_non_sqlite_database(tmp_path: Path) -> None:
    bad = tmp_path / "bad.tar.gz"
    fake = tmp_path / "wsctl.db"
    fake.write_text("not a database", encoding="utf-8")
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


def test_a_symlink_in_recordings_does_not_make_the_backup_unrestorable(
    tmp_path: Path,
) -> None:
    """``create`` stored links while ``restore`` rejected any archive with one.

    A single symlink in the recordings directory (a NAS mount, a cross-volume
    archive) therefore produced a backup that could never be restored -- the
    worst possible place to find out.
    """
    data = tmp_path / "data"
    data.mkdir()
    store = _make_store(data / "wsctl.db")
    store.close()
    recordings = data / "recordings"
    recordings.mkdir()
    (recordings / "a.cast").write_text('{"version": 2}\n', encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("not-a-recording", encoding="utf-8")
    try:
        (recordings / "link.cast").symlink_to(outside)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - Windows
        pytest.skip(f"symlinks unavailable here: {exc}")

    out = tmp_path / "bak.tar.gz"
    backup.create_backup(data / "wsctl.db", recordings, out, version="t")

    # The link is skipped rather than stored...
    with tarfile.open(out) as tar:
        names = tar.getnames()
        assert not any(n.endswith("link.cast") for n in names), names
        # ...and nothing outside the data directory leaked in through it.
        assert outside.read_text(encoding="utf-8") == "not-a-recording"

    # ...so the archive restores, which is the whole point of a backup.
    dest = tmp_path / "restored"
    dest.mkdir()
    backup.restore_backup(
        out,
        dest / "wsctl.db",
        dest / "recordings",
        force=False,
    )
    assert (dest / "wsctl.db").is_file()
    assert (dest / "recordings" / "a.cast").is_file()


# -- 0.1.30: restoring over a live database, and WAL sidecars ---------------


def test_restore_refuses_while_an_instance_lease_is_alive(tmp_path: Path) -> None:
    """A running server holds its own handle; replacing the file is not safe."""
    data = tmp_path / "data"
    data.mkdir()
    store = _make_store(data / "wsctl.db")
    store.instance_register("live-instance", pid=os.getpid(), host="here")
    store.close()

    out = tmp_path / "bak.tar.gz"
    backup.create_backup(data / "wsctl.db", data / "recordings", out)

    with pytest.raises(backup.BackupError) as excinfo:
        backup.restore_backup(
            out, data / "wsctl.db", data / "recordings", force=True, instance_ttl=60
        )
    assert "wsctl stop" in str(excinfo.value)
    assert "live-instance" in str(excinfo.value)


def test_restore_drops_wal_sidecars_that_belong_to_the_old_database(
    tmp_path: Path,
) -> None:
    """Replaying the old WAL over the restored file corrupts it.

    The fixture writes junk next to the target: after a restore neither
    ``-wal`` nor ``-shm`` may survive, or SQLite would apply pages that belong
    to a database that is no longer there.
    """
    data = tmp_path / "data"
    data.mkdir()
    store = _make_store(data / "wsctl.db")
    store.close()
    out = tmp_path / "bak.tar.gz"
    backup.create_backup(data / "wsctl.db", data / "recordings", out)

    wal = data / "wsctl.db-wal"
    shm = data / "wsctl.db-shm"
    wal.write_bytes(b"\x00" * 64)
    shm.write_bytes(b"\x00" * 64)

    backup.restore_backup(out, data / "wsctl.db", data / "recordings", force=True)

    assert not wal.exists(), "a stale -wal was left to be replayed"
    assert not shm.exists(), "a stale -shm was left behind"
    conn = sqlite3.connect(data / "wsctl.db")
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
    finally:
        conn.close()


def test_restore_rejects_a_database_that_fails_integrity_check(
    tmp_path: Path,
) -> None:
    """The header check accepted a truncated file, which was then installed."""
    bad = tmp_path / "corrupt.tar.gz"
    fake = tmp_path / "wsctl.db"
    fake.write_bytes(b"SQLite format 3\x00" + b"\x00" * 2048)
    with tarfile.open(bad, "w:gz") as tar:
        tar.add(fake, arcname="wsctl.db")
    with pytest.raises(backup.BackupError, match=r"损坏|校验"):
        backup.restore_backup(bad, tmp_path / "db", tmp_path / "rec", force=True)


def test_restore_keeps_a_consistent_copy_of_the_previous_database(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    store = _make_store(data / "wsctl.db")
    store.close()
    out = tmp_path / "bak.tar.gz"
    backup.create_backup(data / "wsctl.db", data / "recordings", out)

    # A second user added after the backup: the .bak must contain them.
    store = Store(data / "wsctl.db")
    store.user_create("carol", "password123")
    store.close()

    backup.restore_backup(out, data / "wsctl.db", data / "recordings", force=True)

    bak = data / "wsctl.db.bak"
    assert bak.is_file()
    conn = sqlite3.connect(bak)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        names = {row[0] for row in conn.execute("SELECT username FROM users")}
    finally:
        conn.close()
    assert names == {"alice", "bob", "carol"}
    # No half-written staging file may be left next to the database.
    assert not list(data.glob(".*.restore-*.tmp"))


def test_a_second_restore_never_overwrites_the_first_backup(
    tmp_path: Path,
) -> None:
    """A retried restore used to eat the only copy of the original database.

    ``.bak`` had one fixed name. Restore #1 replaced it with the original; a
    second run (an operator who thought the first had hung) then overwrote that
    snapshot with the *already restored* file -- the original was gone.
    """
    data = tmp_path / "data"
    data.mkdir()
    store = _make_store(data / "wsctl.db")
    store.close()
    out = tmp_path / "bak.tar.gz"
    backup.create_backup(data / "wsctl.db", data / "recordings", out)

    store = Store(data / "wsctl.db")
    store.user_create("carol", "password123")
    store.close()
    backup.restore_backup(out, data / "wsctl.db", data / "recordings", force=True)
    original_bak = data / "wsctl.db.bak"
    assert original_bak.is_file()

    # A second restore: it must keep the first snapshot and write a new one.
    backup.restore_backup(out, data / "wsctl.db", data / "recordings", force=True)
    backups = sorted(data.glob("wsctl.db.bak*"))
    assert len(backups) == 2, [p.name for p in backups]

    def users(path: Path) -> set[str]:
        conn = sqlite3.connect(path)
        try:
            return {row[0] for row in conn.execute("SELECT username FROM users")}
        finally:
            conn.close()

    assert users(original_bak) == {"alice", "bob", "carol"}, (
        "the first backup was overwritten by the second restore"
    )
