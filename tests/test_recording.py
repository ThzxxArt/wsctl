from __future__ import annotations

import json
from pathlib import Path

from wsctl.core.recording import Recorder, has_room, recordings_usage


def test_cast_header_and_events(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path / "a.cast", width=100, height=30)
    recorder.output(b"hello")
    recorder.input(b"x")
    recorder.close()

    lines = (tmp_path / "a.cast").read_text().splitlines()
    header = json.loads(lines[0])
    assert header["version"] == 2
    assert header["width"] == 100
    assert header["height"] == 30
    assert "timestamp" in header

    out = json.loads(lines[1])
    assert out[1] == "o" and out[2] == "hello"
    inp = json.loads(lines[2])
    assert inp[1] == "i" and inp[2] == "x"


def test_close_is_idempotent(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path / "b.cast")
    recorder.close()
    recorder.close()
    recorder.output(b"ignored after close")
    # only the header line remains
    assert (tmp_path / "b.cast").read_text().count("\n") == 1


def test_creates_parent_directory(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path / "nested" / "deep" / "c.cast")
    recorder.output(b"data")
    recorder.close()
    assert (tmp_path / "nested" / "deep" / "c.cast").is_file()


def test_recordings_usage_and_room(tmp_path: Path) -> None:
    directory = tmp_path / "rec"
    assert recordings_usage(directory) == (0, 0)
    assert has_room(directory, 0)  # unlimited

    directory.mkdir()
    (directory / "a.cast").write_text("x" * 10)
    (directory / "b.cast").write_text("y" * 5)
    (directory / "ignored.txt").write_text("z" * 100)

    total, count = recordings_usage(directory)
    assert (total, count) == (15, 2)
    assert has_room(directory, 100)
    assert not has_room(directory, 10)
