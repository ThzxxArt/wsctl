from __future__ import annotations

import json
import time
from pathlib import Path

from wsctl.core.recording import (
    Recorder,
    failure_count,
    has_room,
    recordings_usage,
)


def test_cast_header_and_events(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path / "a.cast", width=100, height=30)
    recorder.output(b"hello")
    recorder.input(b"x")
    recorder.close()

    lines = (tmp_path / "a.cast").read_text(encoding="utf-8").splitlines()
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
    assert (tmp_path / "b.cast").read_text(encoding="utf-8").count("\n") == 1


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
    (directory / "a.cast").write_text("x" * 10, encoding="utf-8")
    (directory / "b.cast").write_text("y" * 5, encoding="utf-8")
    (directory / "ignored.txt").write_text("z" * 100, encoding="utf-8")

    total, count = recordings_usage(directory)
    assert (total, count) == (15, 2)
    assert has_room(directory, 100)
    assert not has_room(directory, 10)


def test_write_failure_stops_the_recorder(tmp_path: Path) -> None:
    """A dead cast must not keep claiming to be recording."""
    path = tmp_path / "fail.cast"
    recorder = Recorder(path)
    # Simulate a broken disk: close the underlying handle behind the writer's back.
    assert recorder._fh is not None
    recorder._fh.close()  # type: ignore[attr-defined]
    recorder.output(b"this write must fail")
    recorder.output(b"and this one must be ignored")

    deadline = time.monotonic() + 5.0
    while not recorder.closed and time.monotonic() < deadline:
        time.sleep(0.02)
    assert recorder.closed, "a failed write must close the recorder"
    assert recorder.failed
    assert recorder.error
    # Events queued after the failure are dropped, not silently "recorded".
    before = path.stat().st_size
    recorder.output(b"after close")
    assert path.stat().st_size == before
    recorder.close()

    # The header written before the failure must still be a valid cast, so the
    # partial recording stays replayable instead of being a corrupt file.
    header = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert header["version"] == 2
    assert header["width"] == 80
    assert failure_count() >= 1


def test_close_is_safe_after_a_failure(tmp_path: Path) -> None:
    path = tmp_path / "fail2.cast"
    recorder = Recorder(path)
    assert recorder._fh is not None
    recorder._fh.close()  # type: ignore[attr-defined]
    recorder.output(b"x")
    recorder.close()
    recorder.close()  # idempotent
