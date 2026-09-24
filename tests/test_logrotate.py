"""Copy-and-truncate log rotation keeps an append-mode descriptor valid."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from wsctl.core.logrotate import rotate_if_needed


def test_no_rotation_below_the_threshold(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    path.write_text("hello\n", encoding="utf-8")
    assert rotate_if_needed(path, max_bytes=1024) is False
    assert not path.with_name("app.log.1").exists()


def test_disabled_when_max_bytes_is_zero(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    path.write_text("x" * 100, encoding="utf-8")
    assert rotate_if_needed(path, max_bytes=0) is False


def test_missing_file_is_a_noop(tmp_path: Path) -> None:
    assert rotate_if_needed(tmp_path / "absent.log", max_bytes=1) is False


def test_rotation_keeps_append_descriptor_usable(tmp_path: Path) -> None:
    """The daemon writes through a descriptor opened before the rotation."""
    path = tmp_path / "app.log"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, b"A" * 100)
        assert rotate_if_needed(path, max_bytes=50, backup_count=3) is True
        # The old content lives in the backup...
        assert path.with_name("app.log.1").read_text(encoding="utf-8") == "A" * 100
        # ...and the *same* descriptor continues into the fresh file.
        os.write(fd, b"B" * 10)
        assert path.read_text(encoding="utf-8") == "B" * 10
    finally:
        os.close(fd)


def test_backups_shift_and_are_capped(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    for label in ("1", "2", "3"):
        path.write_text(label * 30, encoding="utf-8")
        assert rotate_if_needed(path, max_bytes=10, backup_count=2) is True
    assert path.with_name("app.log.1").read_text(encoding="utf-8") == "3" * 30
    assert path.with_name("app.log.2").read_text(encoding="utf-8") == "2" * 30
    assert not path.with_name("app.log.3").exists()


def test_lines_written_during_the_copy_are_not_lost(tmp_path: Path) -> None:
    """Copy-and-truncate must not destroy the copy-to-truncate window.

    ``shutil.copy2`` snapshots the file; anything appended after that snapshot
    used to be thrown away by the ``ftruncate`` and was in *neither* the backup
    nor the live file -- the module even claimed the opposite ("may appear in
    both files"). Usually that window is tiny; over a multi-megabyte log it is
    exactly the burst the rotation exists to preserve.
    """
    from wsctl.core import logrotate

    path = tmp_path / "app.log"
    path.write_text("A" * 200, encoding="utf-8")
    backup_path = path.with_name("app.log.1")
    original_copy = logrotate.shutil.copy2

    def copy_then_append(src: object, dst: object, **kwargs: object) -> None:
        original_copy(src, dst, **kwargs)  # type: ignore[arg-type]
        with path.open("ab") as handle:
            handle.write(b"LOST-LINE\n")

    logrotate.shutil.copy2 = copy_then_append  # type: ignore[assignment]
    try:
        assert logrotate.rotate_if_needed(path, max_bytes=50, backup_count=3) is True
    finally:
        logrotate.shutil.copy2 = original_copy  # type: ignore[assignment]
    archived = backup_path.read_bytes()
    assert b"LOST-LINE" in archived, (
        "a line written during the copy vanished from both files"
    )


def test_zero_backups_keeps_no_history(tmp_path: Path) -> None:
    """``backup_count=0`` means "empty the live file", not "copy to .1 anyway"."""
    path = tmp_path / "app.log"
    path.write_text("x" * 100, encoding="utf-8")
    assert rotate_if_needed(path, max_bytes=50, backup_count=0) is True
    assert path.read_text(encoding="utf-8") == ""
    assert not path.with_name("app.log.1").exists()


def test_rotation_is_serialised_across_instances(tmp_path: Path) -> None:
    """Two instances sharing one log must not double-shift the backup chain."""
    from wsctl.core import logrotate

    path = tmp_path / "app.log"
    path.write_text("x" * 100, encoding="utf-8")
    lock = path.with_name("app.log.rotate.lock")
    lock.write_text("peer", encoding="utf-8")
    try:
        assert logrotate.rotate_if_needed(path, max_bytes=50) is False
        assert path.read_text(encoding="utf-8") == "x" * 100, (
            "a second rotator truncated while its peer held the lock"
        )
    finally:
        lock.unlink()


def test_a_rotation_leaves_a_generation_marker(tmp_path: Path) -> None:
    """Copy-and-truncate keeps the inode, so a follower needs a durable signal.

    Without a marker, a rotation that finishes between two of ``logs -f``'s
    polls is invisible: the new content is at least as long as the old offset,
    so the reader resumes mid-text and silently skips the prefix.
    """
    from wsctl.core import logrotate

    path = tmp_path / "app.log"
    path.write_text("x" * 100, encoding="utf-8")
    gen = path.with_name("app.log.gen")
    assert not gen.exists()
    assert logrotate.rotate_if_needed(path, max_bytes=50) is True
    assert gen.is_file(), "a rotation must leave its generation marker"
    first = gen.read_text(encoding="utf-8")
    assert first
    path.write_text("y" * 100, encoding="utf-8")
    assert logrotate.rotate_if_needed(path, max_bytes=50) is True
    assert gen.read_text(encoding="utf-8") != first, (
        "a second rotation must advance the generation"
    )


def test_a_lock_left_by_a_dead_holder_does_not_disable_rotation_forever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``SIGKILL`` gets no ``finally`` -- the lock outlives its holder.

    Treating that leftover as live forever is how one crash turns into "the
    logs grow without bound until someone notices". A lock whose writer is
    gone (or which is plainly too old) must be breakable.
    """
    from wsctl.core import logrotate

    path = tmp_path / "app.log"
    path.write_text("x" * 100, encoding="utf-8")
    lock = path.with_name("app.log.rotate.lock")
    # A lock held by a pid that is certainly not alive (our own parent is,
    # this one is in the "reaped long ago" range and we force the age).
    lock.write_text("999999", encoding="utf-8")
    assert logrotate._lock_is_stale(lock) in (True, False)  # sanity: callable
    # An *alive* holder that is fresh must be respected.
    lock.write_text(str(os.getpid()), encoding="utf-8")
    monkeypatch.setattr(logrotate, "STALE_LOCK_SECONDS", 3600.0)
    assert logrotate._lock_is_stale(lock) is False, (
        "a live, fresh holder must keep its lock"
    )
    assert logrotate.rotate_if_needed(path, max_bytes=50) is False
    assert path.read_text(encoding="utf-8") == "x" * 100, (
        "rotation ran while its peer held the lock"
    )
    # The same lock from a dead holder must be taken over.
    monkeypatch.setattr(logrotate, "_pid_alive", lambda pid: False)
    assert logrotate._lock_is_stale(lock) is True
    assert logrotate.rotate_if_needed(path, max_bytes=50) is True, (
        "a stale lock disabled rotation forever"
    )
    assert not lock.exists(), "the lock must be released after the rotation"


def test_a_lock_from_a_crashed_writer_is_broken_by_age(tmp_path: Path) -> None:
    """Age is the fallback when the holder cannot be identified at all."""
    import os as _os
    import time as _time

    from wsctl.core import logrotate

    lock = tmp_path / "app.log.rotate.lock"
    lock.write_text("", encoding="utf-8")  # no pid recorded
    old = _time.time() - logrotate.STALE_LOCK_SECONDS - 5
    _os.utime(lock, (old, old))
    assert logrotate._lock_is_stale(lock) is True
