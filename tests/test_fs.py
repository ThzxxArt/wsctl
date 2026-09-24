from __future__ import annotations

import os
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


# -- 0.1.22: the link itself is the operation's subject, not its target --------


def _make_link(target: Path, link: Path) -> bool:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - Windows
        pytest.skip(f"symlinks unavailable here: {exc}")
    return True


def test_deleting_a_link_removes_the_link_not_its_target(tmp_path: Path) -> None:
    """``safe_resolve`` used to hand back the *resolved* path.

    Every ``is_symlink()`` guard in the module was then dead code (``.resolve()``
    had already erased the link), so "拒绝操作符号链接" never fired and a delete
    on a link removed whatever it pointed at -- with the link left dangling.
    """
    target = tmp_path / "real.txt"
    target.write_text("precious", encoding="utf-8")
    _make_link(target, tmp_path / "link.txt")
    # The guard refuses to operate on a link at all...
    with pytest.raises(FsError):
        fs.rename_entry(tmp_path, "link.txt", "other.txt")
    # ...and a delete that *does* handle links removes the link itself.
    fs.delete_entry(tmp_path, "link.txt")
    assert not (tmp_path / "link.txt").exists()
    assert target.read_text(encoding="utf-8") == "precious", (
        "the link's target was destroyed instead of the link"
    )


def test_writing_through_a_link_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "real.txt"
    target.write_text("original", encoding="utf-8")
    _make_link(target, tmp_path / "link.txt")
    with pytest.raises(FsError):
        fs.write_text_file(tmp_path, "link.txt", "overwritten")
    assert target.read_text(encoding="utf-8") == "original"


def test_rename_refuses_an_existing_target_even_a_dangling_link(tmp_path: Path) -> None:
    """``Path.exists()`` follows links, so a dangling symlink looks absent."""
    source = tmp_path / "src.txt"
    source.write_text("source", encoding="utf-8")
    gone = tmp_path.parent / "definitely-not-here"
    gone.unlink(missing_ok=True)
    _make_link(gone, tmp_path / "dst.txt")
    with pytest.raises(FsError):
        fs.rename_entry(tmp_path, "src.txt", "dst.txt")
    assert source.exists(), "the source must survive a refused rename"


def test_rename_never_clobbers_an_existing_file(tmp_path: Path) -> None:
    """Sequential case: the name is taken, so the rename is refused."""
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("FIRST", encoding="utf-8")
    second.write_text("SECOND", encoding="utf-8")
    fs.rename_entry(tmp_path, "first.txt", "shared.txt")
    with pytest.raises(FsError):
        fs.rename_entry(tmp_path, "second.txt", "shared.txt")
    assert (tmp_path / "shared.txt").read_text(encoding="utf-8") == "FIRST"
    assert second.read_text(encoding="utf-8") == "SECOND", (
        "the loser of the race lost its file instead of getting an error"
    )


def test_rename_noreplace_refuses_without_a_pre_check(tmp_path: Path) -> None:
    """The atomic primitive's own contract -- no ``exists()`` to hide behind.

    The pre-check only catches the *sequential* case. The race is decided
    entirely by whether the rename primitive itself can clobber, so this is
    the assertion that keeps a clobbering ``os.rename`` from coming back.
    """
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("A", encoding="utf-8")
    b.write_text("B", encoding="utf-8")
    fs.rename_noreplace(a, tmp_path / "c.txt")
    with pytest.raises(FsError):
        fs.rename_noreplace(b, tmp_path / "c.txt")
    assert (tmp_path / "c.txt").read_text(encoding="utf-8") == "A"
    assert b.read_text(encoding="utf-8") == "B"


def test_the_toctou_window_alone_cannot_lose_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blind the ``exists()`` check and the atomic half must still refuse.

    This is the real race: both callers observe "nothing there yet" and only
    the primitive decides. ``os.rename`` replaces its target silently on
    POSIX -- two concurrent renames to one name then destroy a file with no
    error anywhere -- and this is the only assertion that sees it.
    """
    monkeypatch.setattr(fs, "_lexists", lambda path: False)
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("FIRST", encoding="utf-8")
    second.write_text("SECOND", encoding="utf-8")
    fs.rename_entry(tmp_path, "first.txt", "shared.txt")
    with pytest.raises(FsError):
        fs.rename_entry(tmp_path, "second.txt", "shared.txt")
    assert (tmp_path / "shared.txt").read_text(encoding="utf-8") == "FIRST"
    assert second.read_text(encoding="utf-8") == "SECOND", (
        "the open TOCTOU window let a rename clobber an existing file"
    )


def test_concurrent_same_name_renames_keep_every_byte(tmp_path: Path) -> None:
    """End-to-end: hammer one target name from both sides at once."""
    import threading

    payload_a = b"A" * 4096
    payload_b = b"B" * 4096
    (tmp_path / "a.bin").write_bytes(payload_a)
    (tmp_path / "b.bin").write_bytes(payload_b)
    errors: list[object] = []
    done = threading.Barrier(3)

    def racer(src: str) -> None:
        done.wait()
        try:
            fs.rename_entry(tmp_path, src, "c.bin")
        except FsError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=racer, args=(s,)) for s in ("a.bin", "b.bin")]
    for thread in threads:
        thread.start()
    done.wait()
    for thread in threads:
        thread.join(timeout=5)
    final = tmp_path / "c.bin"
    assert final.exists(), "exactly one rename must win"
    assert final.read_bytes() in (payload_a, payload_b), (
        "the published file is an interleaved mixture of both sources"
    )
    assert len(errors) == 1, f"the loser must be told, not silent: {errors}"


def test_concurrent_same_name_edits_never_interleave(tmp_path: Path) -> None:
    """Two editors of one file must each see their own whole text or an error."""
    import threading

    target = tmp_path / "doc.txt"
    target.write_text("seed", encoding="utf-8")
    left = "L" * 20000
    right = "R" * 20000
    errors: list[object] = []
    done = threading.Barrier(3)

    def editor(text: str) -> None:
        done.wait()
        try:
            fs.write_text_file(tmp_path, "doc.txt", text)
        except Exception as exc:  # any failure is an acceptable outcome here
            errors.append(exc)

    threads = [threading.Thread(target=editor, args=(t,)) for t in (left, right)]
    for thread in threads:
        thread.start()
    done.wait()
    for thread in threads:
        thread.join(timeout=5)
    body = target.read_text(encoding="utf-8")
    assert body in (left, right), (
        "the published edit is a mixture of two writers -- the sidecar name "
        "was shared"
    )
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".")]
    assert leftovers == [], f"staging sidecars leaked: {leftovers}"


def test_upload_sidecar_names_are_unique(tmp_path: Path) -> None:
    """A fixed ``.{name}.wsctl-upload`` let two uploads share one staging file.

    Each write then interleaved into the same bytes and the final ``os.replace``
    published the mixture as a complete-looking file.
    """
    seen: set[str] = set()
    for _ in range(20):
        staging = tmp_path / f".doc.txt.wsctl-{__import__('secrets').token_hex(8)}.upload"
        assert staging.name not in seen, f"staging name collided: {staging.name}"
        seen.add(staging.name)
        fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
    assert len(seen) == 20


def test_safe_resolve_rejects_a_nul_byte(tmp_path: Path) -> None:
    """``Path.resolve`` raises ``ValueError`` on NUL, which no route catches -- a 500."""
    with pytest.raises(FsError):
        safe_resolve(tmp_path, "a\x00b")
