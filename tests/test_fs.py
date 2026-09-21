from __future__ import annotations

from pathlib import Path

import pytest

from wsctl.core.fs import FsError, list_dir, relative_to, safe_resolve


def test_safe_resolve_root(tmp_path: Path) -> None:
    assert safe_resolve(tmp_path, "") == tmp_path.resolve()
    assert safe_resolve(tmp_path, None) == tmp_path.resolve()


def test_safe_resolve_within(tmp_path: Path) -> None:
    child = tmp_path / "a" / "b"
    child.mkdir(parents=True)
    assert safe_resolve(tmp_path, "a/b") == child.resolve()


def test_safe_resolve_rejects_escape(tmp_path: Path) -> None:
    with pytest.raises(FsError):
        safe_resolve(tmp_path, "../outside")
    with pytest.raises(FsError):
        safe_resolve(tmp_path, "/etc/passwd")


def test_safe_resolve_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-target"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "link"
    link.symlink_to(outside)
    with pytest.raises(FsError):
        safe_resolve(tmp_path, "link")


def test_list_dir_sorted_dirs_first(tmp_path: Path) -> None:
    (tmp_path / "z.txt").write_text("z")
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("a")
    entries = list_dir(tmp_path, "")
    names = [e["name"] for e in entries]
    assert names[0] == "sub"
    assert set(names) == {"sub", "a.txt", "z.txt"}


def test_list_dir_not_a_directory(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("x")
    with pytest.raises(FsError):
        list_dir(tmp_path, "file.txt")


def test_relative_to(tmp_path: Path) -> None:
    child = tmp_path / "a" / "b.txt"
    child.parent.mkdir()
    child.write_text("x")
    assert relative_to(tmp_path, child) == "a/b.txt"
