"""Copy-and-truncate log rotation keeps an append-mode descriptor valid."""

from __future__ import annotations

import os
from pathlib import Path

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
