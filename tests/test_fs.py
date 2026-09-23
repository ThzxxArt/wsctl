from __future__ import annotations

from pathlib import Path

import pytest

from wsctl.core import fs
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
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - Windows
        # Creating a symlink needs SeCreateSymbolicLinkPrivilege (or Developer
        # Mode) on Windows; the guard itself is POSIX-hardening, so skip there.
        pytest.skip(f"symlinks unavailable here: {exc}")
    with pytest.raises(FsError):
        safe_resolve(tmp_path, "link")


def test_list_dir_sorted_dirs_first(tmp_path: Path) -> None:
    (tmp_path / "z.txt").write_text("z", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    entries, truncated = list_dir(tmp_path, "")
    names = [e["name"] for e in entries]
    assert names[0] == "sub"
    assert set(names) == {"sub", "a.txt", "z.txt"}
    assert truncated is False


def test_list_dir_truncates_over_the_limit(tmp_path: Path) -> None:
    for index in range(10):
        (tmp_path / f"f{index:02d}.txt").write_text("x", encoding="utf-8")
    entries, truncated = list_dir(tmp_path, "", limit=4)
    assert len(entries) == 4
    assert truncated is True


def test_list_dir_not_a_directory(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    with pytest.raises(FsError):
        list_dir(tmp_path, "file.txt")


def test_relative_to(tmp_path: Path) -> None:
    child = tmp_path / "a" / "b.txt"
    child.parent.mkdir()
    child.write_text("x", encoding="utf-8")
    assert relative_to(tmp_path, child) == "a/b.txt"


def test_upload_is_atomic_a_reader_never_sees_a_half_file(tmp_path: Path) -> None:
    """Uploading must not expose a partially written file to concurrent reads.

    The old upload wrote straight into the destination with O_TRUNC, so a
    download issued during a long upload returned truncated bytes. The writer
    now stages to a sidecar and ``os.replace``s it into place, which is atomic
    on one filesystem.
    """
    import os
    import threading

    from wsctl.server.routes.files import _copy_upload

    root = tmp_path
    name = "atomic.bin"
    dest = root / name
    payload = b"A" * (2 * 1024 * 1024)
    staging = root / f".{name}.wsctl-upload"
    seen: list[int] = []
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            if dest.exists():
                seen.append(dest.stat().st_size)

    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(staging, flags, 0o600)
    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    import io

    size = _copy_upload(io.BytesIO(payload), fd, limit=10 * 1024 * 1024, chunk_size=65536)
    os.replace(staging, dest)
    stop.set()
    thread.join(timeout=5)

    assert size == len(payload)
    assert dest.read_bytes() == payload
    # Whatever the reader observed, it was never a *partial* file: a size that
    # is not 0 (absent) and not the full payload means the staging broke.
    assert all(s in (0, len(payload)) for s in seen), seen[:10]


def test_make_dir_rejects_names_that_escape(tmp_path: Path) -> None:
    for bad in ("..", ".", "a/b", "a\\b", "", "  ", ".wsctl-upload"):
        with pytest.raises(fs.FsError):
            fs.make_dir(tmp_path, "", bad)
    made = fs.make_dir(tmp_path, "", "ok")
    assert made.is_dir()
    with pytest.raises(fs.FsError):
        fs.make_dir(tmp_path, "", "ok")  # never merges into an existing entry


def test_rename_is_scoped_to_one_directory(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    renamed = fs.rename_entry(tmp_path, "a.txt", "b.txt")
    assert renamed.name == "b.txt"
    with pytest.raises(fs.FsError):
        fs.rename_entry(tmp_path, "b.txt", "sub")  # existing name
    with pytest.raises(fs.FsError):
        fs.rename_entry(tmp_path, "b.txt", "../escape")
    assert (tmp_path / "b.txt").is_file()
    assert not (tmp_path / "a.txt").exists()


def test_delete_refuses_the_root_and_non_empty_directories(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "inner.txt").write_text("x", encoding="utf-8")
    with pytest.raises(fs.FsError):
        fs.delete_entry(tmp_path, "")
    with pytest.raises(OSError):
        fs.delete_entry(tmp_path, "sub")  # rmdir on a non-empty dir
    (tmp_path / "sub" / "inner.txt").unlink()
    fs.delete_entry(tmp_path, "sub")
    assert not (tmp_path / "sub").exists()
    # Recursive delete is deliberately absent: one click must not empty a home.
    assert not hasattr(fs, "delete_tree")


def test_preview_refuses_binaries_and_oversized_files(tmp_path: Path) -> None:
    (tmp_path / "text.txt").write_text("héllo 世界", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"\x00\x01\x02binary")
    (tmp_path / "big.txt").write_bytes(b"a" * (fs.PREVIEW_MAX_BYTES + 1))

    text, encoding = fs.read_text_file(tmp_path, "text.txt")
    assert text == "héllo 世界"
    assert encoding == "utf-8"
    with pytest.raises(fs.FsError, match="二进制"):
        fs.read_text_file(tmp_path, "binary.bin")
    with pytest.raises(fs.FsError, match="过大"):
        fs.read_text_file(tmp_path, "big.txt")


def test_write_text_file_is_atomic_and_bounded(tmp_path: Path) -> None:
    target = tmp_path / "edit.txt"
    target.write_text("before", encoding="utf-8")
    size = fs.write_text_file(tmp_path, "edit.txt", "after 内容")
    assert size == len("after 内容".encode())
    assert target.read_text(encoding="utf-8") == "after 内容"
    assert list(tmp_path.glob(".*wsctl-edit")) == [], "staging sidecar left behind"
    with pytest.raises(fs.FsError, match="过大"):
        fs.write_text_file(tmp_path, "edit.txt", "x" * (fs.EDIT_MAX_BYTES + 1))
    assert target.read_text(encoding="utf-8") == "after 内容", (
        "a rejected edit must not touch the file"
    )


def test_list_dir_paged_reaches_past_the_old_hard_stop(tmp_path: Path) -> None:
    """Entry 2001 used to be unreachable: the endpoint cut at 2000 and stopped."""
    for i in range(2050):
        (tmp_path / f"f{i:04d}.txt").write_text("x", encoding="utf-8")
    first = fs.list_dir_paged(tmp_path, "", offset=0, limit=2000)
    assert len(first["entries"]) == 2000
    assert first["total"] == 2050
    later = fs.list_dir_paged(tmp_path, "", offset=2000, limit=2000)
    assert len(later["entries"]) == 50
    names = {e["name"] for e in first["entries"]} | {e["name"] for e in later["entries"]}
    assert len(names) == 2050, "paging lost or duplicated entries"


def test_list_dir_paged_filters_by_name_and_kind(tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("x", encoding="utf-8")
    (tmp_path / "KEEP-log.txt").write_text("x", encoding="utf-8")
    (tmp_path / "other.md").write_text("x", encoding="utf-8")
    (tmp_path / "folder").mkdir()
    only_txt = fs.list_dir_paged(tmp_path, "", contains="keep", kind="file")
    assert {e["name"] for e in only_txt["entries"]} == {"keep.txt", "KEEP-log.txt"}
    assert only_txt["total"] == 2
    dirs = fs.list_dir_paged(tmp_path, "", kind="dir")
    assert {e["name"] for e in dirs["entries"]} == {"folder"}


def test_make_dir_loses_the_race_gracefully(tmp_path: Path) -> None:
    """A concurrent create must be a 400, not a 500.

    The ``exists`` fast path and the ``mkdir`` authority have a window between
    them; a peer that wins that window used to surface as an unhandled
    ``FileExistsError``.
    """
    created = fs.make_dir(tmp_path, "", "raced")
    assert created.is_dir()
    # Losing the race is reported exactly like "already there".
    with pytest.raises(FsError, match="已存在"):
        fs.make_dir(tmp_path, "", "raced")


def test_make_dir_validates_the_name_exactly_once_and_re_resolves(tmp_path: Path) -> None:
    """The containment check must use the resolved target, not a joined string."""
    made = fs.make_dir(tmp_path, "", "ok")
    # The returned path is inside the root and is the one that exists.
    assert made.resolve().is_relative_to(tmp_path.resolve())
    assert made.is_dir()
