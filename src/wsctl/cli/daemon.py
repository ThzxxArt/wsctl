"""Background (non-systemd) lifecycle for a single wsctl instance.

``wsctl start`` spawns a detached child (``start_new_session=True``) with its
stdio redirected to a per-port log file, so it survives the parent shell and
needs no terminal multiplexer. A pid file carrying a *process identity*
fingerprint lets ``status``/``stop``/``restart`` find the instance and refuse to
signal a recycled PID. Only the single-instance (non ``--reuse-port``) case is
managed this way; ``--reuse-port`` handovers are intentionally out of scope.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from wsctl import __version__
from wsctl.core.config import Settings
from wsctl.core.net import opener_for


class DaemonError(RuntimeError):
    """Raised when the background instance cannot be started or controlled."""


@dataclass(frozen=True)
class Instance:
    """A live (or recorded) wsctl server process."""

    pid: int
    host: str
    port: int
    started_at: float
    version: str
    identity: str

    @property
    def uptime(self) -> float:
        return max(0.0, time.time() - self.started_at)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["uptime"] = round(self.uptime, 3)
        return payload


@dataclass(frozen=True)
class Resolved:
    """Which live instance a lifecycle command should act on.

    ``instance`` is the target. ``found`` lists every live instance in this data
    directory so a caller can explain an ambiguous match. ``fallback`` is set
    when the target was picked without the user naming a port, which is the
    case that used to report "未在运行" while a real instance was serving on a
    non-default port right next to it.
    """

    instance: Instance | None
    found: list[Instance]
    fallback: bool = False
    ambiguous: bool = False


def resolve_instance(settings: Settings, *, port_explicit: bool = False) -> Resolved:
    """Find the instance this invocation should act on.

    An explicit ``--port`` is authoritative and is never second-guessed.
    Without one, prefer an instance that is actually running in this data
    directory over "the default port": otherwise ``wsctl start --port 7682``
    followed by ``wsctl status``/``logs`` reports 未在运行 / 没有日志文件 while
    the instance is sitting right there, and the only clue is a line further
    down about "发现其他实例".
    """
    instance = read_instance(settings)
    if instance is not None:
        return Resolved(instance=instance, found=[instance])
    found = discover(settings)
    if port_explicit or len(found) != 1:
        return Resolved(instance=None, found=found, ambiguous=len(found) > 1)
    only = found[0]
    return Resolved(
        instance=only,
        found=found,
        fallback=(only.port != settings.port or only.host != settings.host),
    )


def settings_for(instance: Instance, base: Settings) -> Settings:
    """``base`` re-pointed at ``instance``, so paths/health match the target."""
    return base.model_copy(update={"host": instance.host, "port": instance.port})


# -- paths -------------------------------------------------------------


def run_dir(settings: Settings) -> Path:
    return settings.data_dir / "run"


def pidfile_path(settings: Settings) -> Path:
    return run_dir(settings) / f"wsctl-{settings.port}.pid"


def logfile_path(settings: Settings) -> Path:
    return run_dir(settings) / f"wsctl-{settings.port}.log"


# -- process identity --------------------------------------------------


def _process_identity(pid: int) -> str | None:
    """A value that changes when a PID is recycled by another process.

    On Linux the process start time (``/proc/<pid>/stat`` field 22) is stable
    for the lifetime of the process and differs for a reused PID. Elsewhere we
    fall back to the ``ps`` start time.
    """
    if sys.platform == "win32":  # pragma: no cover - platform specific
        return None
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return _identity_from_ps(pid)
    try:
        # Field 2 is "(comm)" and may itself contain spaces and parens.
        rest = text[text.rindex(")") + 2 :].split()
        return f"linux:{rest[19]}"
    except (ValueError, IndexError):
        return None


def _identity_from_ps(pid: int) -> str | None:
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return f"ps:{result.stdout.strip()}"
    return None


def process_alive(pid: int) -> bool:
    """Best-effort liveness probe (POSIX only, like the server's own)."""
    if pid <= 0:
        return False
    if sys.platform == "win32":  # pragma: no cover - platform specific
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _identity_matches(instance: Instance) -> bool:
    """Whether the PID still belongs to the process we recorded."""
    if not instance.identity:
        return True
    current = _process_identity(instance.pid)
    # If we cannot read an identity (exotic platform), trust liveness alone.
    return current is None or current == instance.identity


# -- pid file ----------------------------------------------------------


def _read_raw(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def read_instance(settings: Settings) -> Instance | None:
    """Return the live instance, or ``None`` (pruning a stale pid file)."""
    path = pidfile_path(settings)
    data = _read_raw(path)
    if data is None:
        # A missing file is a no-op; a corrupt one must be removed so it cannot
        # block a future start forever.
        with contextlib.suppress(OSError):
            path.unlink()
        return None
    try:
        instance = Instance(
            pid=int(data["pid"]),
            host=str(data.get("host", settings.host)),
            port=int(data.get("port", settings.port)),
            started_at=float(data.get("started_at", 0.0)),
            version=str(data.get("version", "?")),
            identity=str(data.get("identity", "")),
        )
    except (KeyError, TypeError, ValueError):
        with contextlib.suppress(OSError):
            path.unlink()
        return None
    if not process_alive(instance.pid) or not _identity_matches(instance):
        with contextlib.suppress(OSError):
            path.unlink()
        return None
    return instance


def claim_pidfile(settings: Settings) -> Instance:
    """Write this process's pid file, refusing to clobber a live instance.

    The file is created with ``O_EXCL`` so two instances racing to start on the
    same port cannot both "win": the loser re-checks liveness and fails cleanly.
    """
    path = pidfile_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    instance = Instance(
        pid=os.getpid(),
        host=settings.host,
        port=settings.port,
        started_at=time.time(),
        version=__version__,
        identity=_process_identity(os.getpid()) or "",
    )
    for attempt in range(2):
        existing = read_instance(settings)
        if existing is not None:
            raise DaemonError(
                f"已有实例在运行（pid {existing.pid}，{existing.host}:{existing.port}）"
            )
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            if attempt == 0:
                # A peer created it between the check and here; re-check whether
                # it is live (and prune it if not) before giving up.
                continue
            raise DaemonError("并发启动冲突，请稍后重试") from None
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(instance), ensure_ascii=False))
        return instance
    raise DaemonError("并发启动冲突，请稍后重试")


def release_pidfile(settings: Settings, instance: Instance) -> None:
    """Remove the pid file only if it still describes ``instance``."""
    path = pidfile_path(settings)
    data = _read_raw(path)
    if data is not None and int(data.get("pid", -1)) != instance.pid:
        return
    with contextlib.suppress(OSError):
        path.unlink()


# -- health ------------------------------------------------------------


def _probe_host(host: str) -> str:
    if host in ("", "0.0.0.0", "::", "[::]"):
        return "127.0.0.1"
    return host


def health_url(settings: Settings) -> str:
    scheme = "https" if settings.ssl_cert else "http"
    return f"{scheme}://{_probe_host(settings.host)}:{settings.port}/healthz"


def health(settings: Settings, *, timeout: float = 2.0) -> bool:
    """Whether the instance answers ``/healthz`` (TLS is not verified)."""
    context = None
    if settings.ssl_cert:
        context = ssl._create_unverified_context()
    try:
        opener = opener_for(health_url(settings), ssl_context=context)
        with opener.open(health_url(settings), timeout=timeout) as resp:
            return int(resp.status) == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def health_info(settings: Settings, *, timeout: float = 2.0) -> dict[str, object] | None:
    context = None
    if settings.ssl_cert:
        context = ssl._create_unverified_context()
    try:
        opener = opener_for(health_url(settings), ssl_context=context)
        with opener.open(health_url(settings), timeout=timeout) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _tail(path: Path, lines: int = 12) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(text[-lines:])


# -- lifecycle ---------------------------------------------------------


def start(settings: Settings, argv: list[str], *, timeout: float = 20.0) -> Instance:
    """Spawn ``argv`` detached and wait until it answers ``/healthz``."""
    existing = read_instance(settings)
    if existing is not None:
        raise DaemonError(
            f"已在运行（pid {existing.pid}）。请使用 'wsctl restart'，或先 'wsctl stop'。"
        )
    if sys.platform == "win32":  # pragma: no cover - platform specific
        raise DaemonError(
            "Windows 不支持后台启动；请注册为服务（如 NSSM）或在计划任务中运行 "
            "'wsctl serve --foreground'"
        )
    log_path = logfile_path(settings)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Deliberately not closed here: the descriptor is inherited by the child and
    # must stay open for the whole life of the server.
    with open(log_path, "ab") as log:
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
                env=child_env(settings),
            )
        except OSError as exc:
            raise DaemonError(f"无法启动子进程：{exc}") from exc

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise DaemonError(
                f"启动失败（退出码 {proc.returncode}）。日志：{log_path}\n{_tail(log_path)}"
            )
        instance = read_instance(settings)
        if instance is not None and health(settings):
            return instance
        time.sleep(0.2)

    with contextlib.suppress(OSError, ProcessLookupError):
        os.kill(proc.pid, signal.SIGTERM)
    raise DaemonError(f"启动超时。日志：{log_path}\n{_tail(log_path)}")


def stop(settings: Settings, *, timeout: float = 15.0, force: bool = False) -> Instance:
    """Stop the instance gracefully; ``force`` escalates to ``SIGKILL``."""
    instance = read_instance(settings)
    if instance is None:
        raise DaemonError("未在运行")
    with contextlib.suppress(ProcessLookupError):
        os.kill(instance.pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_alive(instance.pid):
            release_pidfile(settings, instance)
            return instance
        time.sleep(0.2)
    if not force:
        raise DaemonError(
            f"进程 {instance.pid} 在 {timeout:g}s 内未退出；确认无误后使用 --force 强制结束"
        )
    with contextlib.suppress(ProcessLookupError):
        os.kill(instance.pid, signal.SIGKILL)
    for _ in range(25):
        if not process_alive(instance.pid):
            break
        time.sleep(0.2)
    release_pidfile(settings, instance)
    return instance


def restart(settings: Settings, argv: list[str], *, timeout: float = 20.0) -> Instance:
    # Only "not running" is ignorable; a stop that times out must surface rather
    # than be hidden behind a confusing "already running" from start().
    if read_instance(settings) is not None:
        stop(settings, timeout=timeout)
    return start(settings, argv, timeout=timeout)


def reload_(settings: Settings) -> Instance:
    """Ask the running instance to reload its config (SIGHUP)."""
    instance = read_instance(settings)
    if instance is None:
        raise DaemonError("未在运行")
    with contextlib.suppress(ProcessLookupError):
        os.kill(instance.pid, signal.SIGHUP)
    return instance


def discover(settings: Settings) -> list[Instance]:
    """All live instances whose pid files live in this data directory."""
    found: list[Instance] = []
    directory = run_dir(settings)
    if not directory.is_dir():
        return found
    for path in sorted(directory.glob("wsctl-*.pid")):
        data = _read_raw(path)
        if data is None:
            continue
        try:
            port = int(data.get("port", 0))
        except (TypeError, ValueError):
            continue
        local = settings.model_copy(update={"port": port})
        instance = read_instance(local)
        if instance is not None:
            found.append(instance)
    return found


def child_env(settings: Settings) -> dict[str, str]:
    """Environment for the detached server child.

    ``WSCTL_LOG_FILE`` tells the child which file it writes to so *it* can
    rotate by size. Rotation has to happen in-process: the append-mode
    descriptor we hand over keeps writing to the same inode, which is exactly
    what the copy-and-truncate rotator is designed for.
    """
    return {
        **os.environ,
        "WSCTL_DAEMON": "1",
        "WSCTL_LOG_FILE": str(logfile_path(settings)),
    }


def log_history_paths(settings: Settings, backup_count: int = 3) -> list[Path]:
    """Log files oldest-first: ``.N`` … ``.1`` then the live file.

    Rotation keeps history in sidecar files, so "show me the last N lines" has
    to read across them or a rotation would silently hide everything.
    """
    live = logfile_path(settings)
    backups = [live.with_name(f"{live.name}.{i}") for i in range(backup_count, 0, -1)]
    return [p for p in backups if p.is_file()] + ([live] if live.is_file() else [])


def tail_log(settings: Settings, *, lines: int = 50, follow: bool = False) -> None:
    """Print the tail of the logs, spanning rotated backups, then optionally follow."""
    live = logfile_path(settings)
    if not live.is_file():
        raise DaemonError(f"没有日志文件：{live}")

    if not follow:
        collected: list[str] = []
        for path in log_history_paths(settings):
            try:
                collected.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                continue
        sys.stdout.write("\n".join(collected[-lines:]) + "\n")
        return

    # Following only makes sense on the live file; show the blended tail first.
    collected = []
    for path in log_history_paths(settings):
        try:
            collected.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            continue
    sys.stdout.write("\n".join(collected[-lines:]) + "\n")
    sys.stdout.flush()
    with live.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(0, os.SEEK_END)
        try:
            while True:
                chunk = handle.readline()
                if chunk:
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                else:
                    time.sleep(0.3)
        except KeyboardInterrupt:
            return
