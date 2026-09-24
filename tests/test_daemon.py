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


def test_resolve_instance_follows_the_running_one(tmp_path: Path) -> None:
    """Without --port, a running instance on a non-default port wins.

    This is the "你把 7681 端口写死了" report: `wsctl start --port 7682` then
    `wsctl status`/`logs`/`stop` used to look at the default port and say
    未在运行 / 没有日志文件 while the instance was serving right there.
    """
    live = daemon.Instance(
        pid=os.getpid(), host="0.0.0.0", port=18111,
        started_at=0.0, version="0", identity="",
    )
    path = daemon.pidfile_path(settings_for(tmp_path, port=18111))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(daemon.asdict(live)), encoding="utf-8")
    try:
        resolved = daemon.resolve_instance(settings_for(tmp_path, port=7681))
        assert resolved.instance is not None
        assert resolved.instance.port == 18111
        assert resolved.fallback is True

        # An explicit --port is never second-guessed.
        explicit = daemon.resolve_instance(settings_for(tmp_path, port=7681), port_explicit=True)
        assert explicit.instance is None
        assert explicit.fallback is False

        # Pointing at the right port finds it directly, with no fallback note.
        direct = daemon.resolve_instance(settings_for(tmp_path, port=18111))
        assert direct.instance is not None and direct.fallback is False
    finally:
        path.unlink(missing_ok=True)


def test_resolve_instance_is_ambiguous_with_two(tmp_path: Path) -> None:
    first = daemon.Instance(
        pid=os.getpid(), host="127.0.0.1", port=18111,
        started_at=0.0, version="0", identity="",
    )
    second = daemon.Instance(
        pid=os.getpid(), host="127.0.0.1", port=18222,
        started_at=0.0, version="0", identity="",
    )
    paths = []
    for inst in (first, second):
        path = daemon.pidfile_path(settings_for(tmp_path, port=inst.port))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(daemon.asdict(inst)), encoding="utf-8")
        paths.append(path)
    try:
        resolved = daemon.resolve_instance(settings_for(tmp_path, port=7681))
        assert resolved.instance is None
        assert resolved.ambiguous is True
        assert {i.port for i in resolved.found} == {18111, 18222}
    finally:
        for path in paths:
            path.unlink(missing_ok=True)


def test_settings_for_repoints_at_the_instance(tmp_path: Path) -> None:
    base = settings_for(tmp_path, port=7681)
    inst = daemon.Instance(
        pid=1, host="0.0.0.0", port=18111, started_at=0.0, version="0", identity="",
    )
    aimed = daemon.settings_for(inst, base)
    assert (aimed.host, aimed.port) == ("0.0.0.0", 18111)
    assert daemon.logfile_path(aimed).name == "wsctl-18111.log"


def test_found_lists_every_live_instance_even_when_one_matches(tmp_path: Path) -> None:
    """``found`` is "everything live here", not just the one we matched.

    `doctor` uses it to say what else is running, so dropping the others hides
    the very thing an operator is looking for.
    """
    for port in (18111, 18222):
        inst = daemon.Instance(
            pid=os.getpid(), host="127.0.0.1", port=port,
            started_at=0.0, version="0", identity="",
        )
        path = daemon.pidfile_path(settings_for(tmp_path, port=port))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(daemon.asdict(inst)), encoding="utf-8")
    try:
        resolved = daemon.resolve_instance(settings_for(tmp_path, port=18111))
        assert resolved.instance is not None and resolved.instance.port == 18111
        assert {i.port for i in resolved.found} == {18111, 18222}, (
            "the sibling instance must stay visible"
        )
    finally:
        for port in (18111, 18222):
            daemon.pidfile_path(settings_for(tmp_path, port=port)).unlink(missing_ok=True)


def test_explicit_host_is_never_second_guessed(tmp_path: Path) -> None:
    """Naming the target means "this one", not "whichever is running"."""
    live = daemon.Instance(
        pid=os.getpid(), host="0.0.0.0", port=18111,
        started_at=0.0, version="0", identity="",
    )
    path = daemon.pidfile_path(settings_for(tmp_path, port=18111))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(daemon.asdict(live)), encoding="utf-8")
    try:
        aimed = settings_for(tmp_path, port=7681).model_copy(update={"host": "127.0.0.1"})
        resolved = daemon.resolve_instance(aimed, port_explicit=True, host_explicit=True)
        assert resolved.instance is None
        assert resolved.fallback is False
    finally:
        path.unlink(missing_ok=True)


def test_explicit_host_refuses_a_different_host(tmp_path: Path) -> None:
    """`--host 127.0.0.1` must not be handed the `0.0.0.0` instance.

    A pid file is keyed by port alone, so an explicit host has to be checked
    here or the command silently acts on an instance the user did not name.
    """
    live = daemon.Instance(
        pid=os.getpid(), host="0.0.0.0", port=18111,
        started_at=0.0, version="0", identity="",
    )
    path = daemon.pidfile_path(settings_for(tmp_path, port=18111))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(daemon.asdict(live)), encoding="utf-8")
    try:
        aimed = settings_for(tmp_path, port=18111).model_copy(update={"host": "127.0.0.1"})
        resolved = daemon.resolve_instance(aimed, port_explicit=True, host_explicit=True)
        assert resolved.instance is None, "host mismatch must not match"
        assert resolved.fallback is False

        # Without --host the same instance is still the right answer.
        loose = daemon.resolve_instance(settings_for(tmp_path, port=18111))
        assert loose.instance is not None and loose.instance.host == "0.0.0.0"
    finally:
        path.unlink(missing_ok=True)


def test_stop_force_reports_failure_when_the_process_is_still_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SIGKILL is not a promise. Reporting "已停止" over a surviving process is
    how the next ``start`` collided with a ghost and the operator lost the trail.
    """
    from wsctl.cli import daemon as daemon_mod

    monkeypatch.setattr(daemon_mod, "process_alive", lambda pid: True)
    monkeypatch.setattr(daemon_mod, "read_instance", lambda settings: _fake_instance())
    monkeypatch.setattr(daemon_mod, "release_pidfile", lambda *a, **k: None)
    with pytest.raises(daemon_mod.DaemonError, match=r"仍未消失|未退出"):
        daemon_mod.stop(_fake_settings(tmp_path), timeout=0.0, force=True)


def test_tail_log_follow_recovers_from_a_copytruncate_rotation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    """copytruncate empties the *same* inode; sitting at the old offset then
    reads nothing until the file grows past it -- the next rotation's first
    lines vanished from ``logs -f`` exactly when they mattered most.
    """
    import os as _os
    import threading
    import time as _time

    from wsctl.cli import daemon as daemon_mod

    live = tmp_path / "run" / "wsctl-7681.log"
    live.parent.mkdir(parents=True)
    live.write_text("first\n", encoding="utf-8")
    monkeypatch.setattr(daemon_mod, "logfile_path", lambda settings: live)
    monkeypatch.setattr(daemon_mod, "log_history_paths", lambda settings, **k: [live])

    def rotate() -> None:
        _time.sleep(0.2)
        # Exactly the rotator's sequence: empty the inode in place, then write
        # the new content. Doing it in one go is the *hard* case -- by the time
        # the follower looks, the file is already longer than its old offset,
        # so a size check alone would resume mid-text and skip the prefix.
        fd = _os.open(live, _os.O_WRONLY | _os.O_APPEND)
        _os.ftruncate(fd, 0)
        _os.close(fd)
        live.write_text("AFTER-ROTATION\n", encoding="utf-8")
        from wsctl.core import logrotate

        logrotate._bump_generation(live)

    thread = threading.Thread(target=rotate, daemon=True)
    thread.start()
    # Bound the follow: after a handful of polls, stop it the way a user would.
    original_sleep = _time.sleep
    counter = {"n": 0}

    def bounded_sleep(seconds: float) -> None:
        counter["n"] += 1
        if counter["n"] > 8:
            raise KeyboardInterrupt
        original_sleep(seconds)

    monkeypatch.setattr(daemon_mod.time, "sleep", bounded_sleep)
    daemon_mod.tail_log(_fake_settings(tmp_path), lines=1, follow=True)
    thread.join(timeout=2)
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "AFTER-ROTATION" in out, (
        f"the follow never recovered from the rotation: {out!r}"
    )


def _fake_instance() -> object:
    from wsctl.cli.daemon import Instance

    return Instance(
        pid=999999,
        host="127.0.0.1",
        port=7681,
        started_at=0.0,
        version="t",
        identity="",
        reuse_port=True,
    )


def _fake_settings(tmp_path: Path) -> object:
    from wsctl.core.config import load_settings

    return load_settings(data_dir=tmp_path, port=7681)


def test_restart_replays_reuse_port_from_the_running_instance() -> None:
    """A plain ``restart`` used to drop ``--reuse-port``, silently downgrading
    the instance to one that must take the visible-gap path on the next
    ``restart --rolling``."""
    main_src = (Path(__file__).resolve().parent.parent
                / "src" / "wsctl" / "cli" / "main.py").read_text(encoding="utf-8")
    assert "options.set(\"reuse_port\", True)" in main_src, (
        "restart must carry the instance's binding mode into the child argv"
    )
    assert "running.instance.reuse_port" in main_src
