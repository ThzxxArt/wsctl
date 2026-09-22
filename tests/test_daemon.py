"""Pid-file lifecycle: claim, identity checks, stale pruning and discovery."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from wsctl.cli import daemon
from wsctl.core.config import Settings, load_settings

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="background lifecycle is POSIX-only"
)


def settings_for(tmp_path: Path, port: int = 7699) -> Settings:
    return load_settings(data_dir=tmp_path, port=port)


def _write_pidfile(settings: Settings, payload: dict[str, object]) -> Path:
    path = daemon.pidfile_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_claim_read_release(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    instance = daemon.claim_pidfile(settings)
    assert instance.pid == os.getpid()
    read = daemon.read_instance(settings)
    assert read is not None
    assert read.pid == os.getpid()
    assert read.version
    daemon.release_pidfile(settings, instance)
    assert daemon.read_instance(settings) is None


def test_claim_refuses_when_already_live(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    daemon.claim_pidfile(settings)
    with pytest.raises(daemon.DaemonError):
        daemon.claim_pidfile(settings)


def test_stale_pidfile_is_pruned(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    dead = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
    dead.wait()
    path = _write_pidfile(
        settings,
        {
            "pid": dead.pid,
            "host": settings.host,
            "port": settings.port,
            "started_at": 0.0,
            "version": "0",
            "identity": "",
        },
    )
    assert daemon.read_instance(settings) is None
    assert not path.exists()


def test_recycled_pid_is_pruned(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    # A live PID (ours) with a mismatching identity must be treated as stale.
    path = _write_pidfile(
        settings,
        {
            "pid": os.getpid(),
            "host": settings.host,
            "port": settings.port,
            "started_at": 0.0,
            "version": "0",
            "identity": "definitely-not-this-process",
        },
    )
    assert daemon.read_instance(settings) is None
    assert not path.exists()


def test_corrupt_pidfile_is_pruned(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    path = daemon.pidfile_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert daemon.read_instance(settings) is None
    assert not path.exists(), "a corrupt pid file must be removed"


def test_corrupt_pidfile_does_not_block_start(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    path = daemon.pidfile_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("garbage", encoding="utf-8")
    instance = daemon.claim_pidfile(settings)  # must not raise
    assert instance.pid == os.getpid()


def test_release_only_removes_own_pidfile(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    instance = daemon.claim_pidfile(settings)
    other = daemon.Instance(
        pid=instance.pid + 1,
        host=settings.host,
        port=settings.port,
        started_at=0.0,
        version="0",
        identity="x",
    )
    daemon.release_pidfile(settings, other)
    assert daemon.pidfile_path(settings).exists()
    daemon.release_pidfile(settings, instance)
    assert not daemon.pidfile_path(settings).exists()


def test_discover_finds_live_instances(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, port=7699)
    daemon.claim_pidfile(settings)
    found = daemon.discover(settings)
    assert [i.pid for i in found] == [os.getpid()]
    assert found[0].port == 7699


def test_process_alive_probe() -> None:
    assert daemon.process_alive(os.getpid())
    assert not daemon.process_alive(0)
    dead = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
    dead.wait()
    assert not daemon.process_alive(dead.pid)


def test_instance_to_dict_includes_uptime(tmp_path: Path) -> None:
    instance = daemon.Instance(
        pid=1, host="127.0.0.1", port=7681, started_at=0.0, version="x", identity="i"
    )
    payload = instance.to_dict()
    assert payload["pid"] == 1
    assert float(payload["uptime"]) > 0  # type: ignore[arg-type]


def test_child_env_tells_the_child_where_to_log(tmp_path: Path) -> None:
    """Without WSCTL_LOG_FILE the in-process rotator never engages."""
    settings = settings_for(tmp_path)
    env = daemon.child_env(settings)
    assert env["WSCTL_DAEMON"] == "1"
    assert env["WSCTL_LOG_FILE"] == str(daemon.logfile_path(settings))


def test_log_history_spans_rotated_backups(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    live = daemon.logfile_path(settings)
    live.parent.mkdir(parents=True, exist_ok=True)
    for index in (3, 2, 1):
        live.with_name(f"{live.name}.{index}").write_text(f"old{index}\n", encoding="utf-8")
    live.write_text("live\n", encoding="utf-8")

    paths = daemon.log_history_paths(settings)
    # Oldest backup first, live file last: a rotation must not hide history.
    assert [p.name for p in paths] == [
        f"{live.name}.3", f"{live.name}.2", f"{live.name}.1", live.name,
    ]


def test_log_history_without_backups_is_just_the_live_file(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    live = daemon.logfile_path(settings)
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text("live\n", encoding="utf-8")
    assert daemon.log_history_paths(settings) == [live]


def test_tail_log_reads_across_a_rotation(tmp_path: Path, capsys: object) -> None:
    settings = settings_for(tmp_path)
    live = daemon.logfile_path(settings)
    live.parent.mkdir(parents=True, exist_ok=True)
    live.with_name(f"{live.name}.1").write_text("first\nsecond\n", encoding="utf-8")
    live.write_text("third\nfourth\n", encoding="utf-8")

    daemon.tail_log(settings, lines=3)
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert out.splitlines() == ["second", "third", "fourth"]
