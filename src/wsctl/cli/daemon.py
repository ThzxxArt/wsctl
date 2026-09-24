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
    #: Whether the listening socket uses SO_REUSEPORT. Recorded because a
    #: rolling restart needs it, and because "can this instance be replaced
    #: without a refused-connection window" is exactly what the operator is
    #: asking when they want to restart the terminal from inside itself.
    reuse_port: bool = False

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
    when the target was picked without the user naming ``--host``/``--port``,
    which is the case that used to report "未在运行" while a real instance was
    serving on a non-default port right next to it.
    """

    instance: Instance | None
    found: list[Instance]
    fallback: bool = False
    ambiguous: bool = False


def resolve_instance(
    settings: Settings, *, port_explicit: bool = False, host_explicit: bool = False
) -> Resolved:
    """Find the instance this invocation should act on.

    Naming a flag narrows the search, it never broadens it:

    * ``--port`` — only that port is a candidate (a pid file is keyed by port);
    * ``--host`` — only instances bound to that host are candidates;
    * both — the candidate must match both;
    * neither — the instance actually running here wins over "the default
      address", which is what used to report 未在运行 / 没有日志文件 for
      ``wsctl-7681.log`` while ``wsctl start --port 7682`` was serving.

    ``found`` is every live instance considered (before host filtering is not
    useful to a caller, so it reflects the narrowed set), so a caller can show
    what else is running or why nothing matched.
    """
    found = discover(settings)
    if host_explicit:
        found = [i for i in found if i.host == settings.host]
    instance = read_instance(settings)
    if instance is not None and (not host_explicit or instance.host == settings.host):
        return Resolved(instance=instance, found=found or [instance])
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
    """Return the live instance, or ``None`` (pruning a stale pid file).

    Pruning is compare-then-unlink, and the comparison is repeated immediately
    before the unlink: between "this record is stale" and "unlink" a peer can
    have written a *fresh, live* record to the same path (``claim_pidfile``'s
    ``O_EXCL`` create), and the blind unlink deleted that one instead.
    """
    path = pidfile_path(settings)

    def prune(stale_pid: int, stale_identity: str) -> None:
        with contextlib.suppress(OSError):
            current = _read_raw(path)
            if current is None:
                return
            if (
                int(current.get("pid", -1)) != stale_pid
                or str(current.get("identity", "")) != stale_identity
            ):
                return  # someone else's record is there now
            path.unlink()

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
            reuse_port=bool(data.get("reuse_port", False)),
        )
    except (KeyError, TypeError, ValueError):
        with contextlib.suppress(OSError):
            path.unlink()
        return None
    if not process_alive(instance.pid) or not _identity_matches(instance):
        prune(instance.pid, instance.identity)
        return None
    return instance


def claim_pidfile(
    settings: Settings, *, takeover_from: int | None = None, reuse_port: bool = False
) -> Instance:
    """Write this process's pid file, refusing to clobber a live instance.

    The file is created with ``O_EXCL`` so two instances racing to start on the
    same port cannot both "win": the loser re-checks liveness and fails cleanly.

    ``takeover_from`` is the one deliberate exception -- a rolling restart. The
    replacement names the exact pid it is replacing, so the window stays shut
    for every other case. ``release_pidfile`` already refuses to unlink a file
    that no longer describes *its* instance, so the retiring instance cannot
    delete its successor's record.
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
        reuse_port=reuse_port,
    )
    for attempt in range(2):
        existing = read_instance(settings)
        if existing is not None:
            if takeover_from is not None and existing.pid == takeover_from:
                with contextlib.suppress(OSError):
                    path.unlink()
                continue
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
    return health_info(settings, timeout=timeout) is not None


def health_pid(settings: Settings, *, timeout: float = 2.0) -> int | None:
    """Which pid actually answered ``/healthz``.

    During a rolling restart the predecessor and the successor share the port,
    so "something answered" is not enough -- the gate has to know *who* did.
    """
    info = health_info(settings, timeout=timeout)
    if not info:
        return None
    raw = info.get("pid")
    try:
        return int(str(raw)) if raw is not None else None
    except (TypeError, ValueError):
        return None


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


def start(
    settings: Settings,
    argv: list[str],
    *,
    timeout: float = 20.0,
    admin_password: str | None = None,
) -> Instance:
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
                env=child_env(settings, admin_password=admin_password),
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
        # The ready gate must identify *who* answered: ``health()`` alone is
        # true when any process on the port speaks HTTP, and "已在后台启动" was
        # then reported over a child that died of EADDRINUSE in the log.
        if instance is not None and health_pid(settings) == instance.pid:
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
    if process_alive(instance.pid):
        # SIGKILL is not a promise: a zombie awaiting reap, or an unkillable
        # state, is still there. Reporting "已停止" anyway is how the next
        # ``start`` collided with a ghost and the operator lost the trail.
        raise DaemonError(
            f"进程 {instance.pid} 在 SIGKILL 后仍未消失（可能是僵尸进程，等待 init 回收）"
        )
    release_pidfile(settings, instance)
    return instance


def stop_pid(pid: int, *, timeout: float = 15.0, force: bool = False) -> None:
    """Stop one specific process, without touching any pid file.

    A rolling restart retires the *old* instance by pid after its successor has
    already taken over the pid file; going through :func:`stop` would then read
    the successor's record and kill the wrong process.
    """
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_alive(pid):
            return
        time.sleep(0.2)
    if not force:
        raise DaemonError(
            f"进程 {pid} 在 {timeout:g}s 内未退出；确认无误后使用 --force 强制结束"
        )
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)
    for _ in range(25):
        if not process_alive(pid):
            return
        time.sleep(0.2)


def describe_handover(settings: Settings, old: Instance) -> str:
    """What this handover will do to the operator's sessions.

    Reads the session rows the instance wrote. A tmux-backed session is
    attached again after the swap and keeps running; anything else dies with
    the predecessor. Say which before the swap, not after.
    """
    from .main import store_mod  # local: keeps CLI imports lazy

    try:
        store = store_mod.Store(settings.db_path)
    except Exception:
        return ""
    try:
        rows = store.term_session_list()
    except Exception:
        return ""
    finally:
        with contextlib.suppress(Exception):
            store.close()
    live = [r for r in rows if r.get("status") == "running"]
    if not live:
        return "预检：当前无运行中的会话。"
    tmux = [r for r in live if r.get("backend") == "tmux"]
    other = [r for r in live if r.get("backend") != "tmux"]
    parts = [f"预检：{len(live)} 个运行中的会话"]
    if tmux:
        parts.append(f"{len(tmux)} 个 tmux 后端会话**可跨重启存活**")
    if other:
        parts.append(
            f"{len(other)} 个非 tmux 会话（local/ssh）**会随本次重启结束**，请先保存工作"
        )
    return "；".join(parts) + "。"


# Set by :func:`rolling_restart` so the CLI can tell the operator up front
# whether this handover can be gap-free. A side channel is ugly; silence
# about a non-zero gap would be worse.
HANDOVER_NOTES: dict[str, str] = {}


def rolling_restart(
    settings: Settings, argv: list[str], *, timeout: float = 20.0
) -> tuple[Instance, Instance]:
    """Replace an instance **without** a refused-connection window.

    The order is the whole point -- the opposite of ``stop`` then ``start``,
    which leaves a gap and, when run from inside one of the instance's own
    sessions, kills the shell before ``start`` can ever run:

    1. start the successor on the same port with ``SO_REUSEPORT`` so both
       sockets are live at once;
    2. gate on the successor actually serving (health, and its own pid file);
    3. only then retire the predecessor, by pid.

    If the gate fails the successor is killed and the predecessor is left
    serving -- the operator keeps their terminal and gets the log tail.
    """
    old = read_instance(settings)
    if old is None:
        raise DaemonError("未在运行")

    # Be upfront about the one case where the gap cannot be zero: SO_REUSEPORT
    # only shares a port when both sockets set the option, so a predecessor
    # started without it keeps exclusive ownership until it exits. That first
    # switch is bounded and short, and every rolling restart after it is
    # gap-free -- but pretending it is free would be the exact kind of
    # declaration this project keeps catching itself making.
    HANDOVER_NOTES["note"] = (
        ""
        if old.reuse_port
        else (
            "注意：原实例未以 --reuse-port 运行，端口无法由两个进程共享。"
            "本次为一次性迁移，会有一个毫秒级的中断窗口；"
            "此后再用 --rolling 即为零停机。"
        )
    )

    child_argv = list(argv)
    if "--reuse-port" not in child_argv:
        child_argv.append("--reuse-port")

    log_path = logfile_path(settings)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log:
        try:
            proc = subprocess.Popen(
                child_argv,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
                env=child_env(settings, rolling_from=old.pid),
            )
        except OSError as exc:
            raise DaemonError(f"无法启动替代实例：{exc}") from exc

    successor: Instance | None = None

    # Pre-check, before anything moves. The operator needs to know *now*
    # whether the sessions in front of them are about to die: only a tmux-backed
    # session outlives an instance swap. Discovering that afterwards is exactly
    # the kind of surprise this project keeps trying to remove.
    preflight = describe_handover(settings, old)
    HANDOVER_NOTES["preflight"] = preflight

    started_after = time.time()

    def _find_successor() -> Instance | None:
        current = read_instance(settings)
        # Three things at once, and all three matter:
        #   * the pid file already names someone other than the predecessor;
        #   * the *successor itself* is the one answering /healthz -- a
        #     predecessor that shares the port answers just as happily, and
        #     testing only "something answered" is what retired the predecessor
        #     before the successor was serving (the refused-connection window
        #     this whole path exists to avoid);
        #   * and it started after we began, so a recycled pid cannot fool the
        #     first check into accepting a process that predates the swap.
        if (
            current is not None
            and current.pid != old.pid
            and current.started_at >= started_after - 1.0
            and health_pid(settings) == current.pid
        ):
            return current
        return None

    if old.reuse_port:
        # Both sockets set SO_REUSEPORT, so they share the port and the gate can
        # be satisfied *before* the predecessor goes anywhere. Zero gap.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise DaemonError(
                    f"替代实例启动失败（退出码 {proc.returncode}）。"
                    f"原实例仍在运行。日志：{log_path}\n{_tail(log_path)}"
                )
            successor = _find_successor()
            if successor is not None:
                break
            time.sleep(0.2)
    else:
        # A predecessor without SO_REUSEPORT owns the port alone, so the
        # successor cannot answer until it is gone. Order matters here: retire
        # the predecessor first, then let the successor's bind retry land. That
        # is the announced, bounded gap -- and why the operator is told about it
        # before anything happens.
        if proc.poll() is None:
            stop_pid(old.pid, timeout=timeout, force=False)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise DaemonError(
                    f"替代实例启动失败（退出码 {proc.returncode}）。"
                    f"日志：{log_path}\n{_tail(log_path)}"
                )
            successor = _find_successor()
            if successor is not None:
                break
            time.sleep(0.1)
        if successor is None:
            with contextlib.suppress(OSError, ProcessLookupError):
                proc.kill()
            raise DaemonError(
                f"替代实例在 {timeout:g}s 内未接管端口。日志：{log_path}\n{_tail(log_path)}"
            )
        # Predecessor already retired above.
        return old, successor

    if successor is None:
        with contextlib.suppress(OSError, ProcessLookupError):
            proc.kill()
        raise DaemonError(
            f"替代实例在 {timeout:g}s 内未通过健康检查，已回滚。"
            f"原实例仍在运行。日志：{log_path}\n{_tail(log_path)}"
        )

    stop_pid(old.pid, timeout=timeout, force=False)
    return old, successor


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


#: The variables wsctl's own lifecycle uses to talk to its managed child.
#: Re-exported from :mod:`wsctl.core.session`, which strips exactly the same
#: set from a session's environment -- one list, because two lists is how
#: ``4401`` ended up fatal in one client and not another (``core.closecodes``).
from wsctl.core.session import LIFECYCLE_ENV_KEYS  # noqa: E402


def child_env(
    settings: Settings,
    *,
    rolling_from: int | None = None,
    admin_password: str | None = None,
) -> dict[str, str]:
    """Environment for the detached server child.

    ``WSCTL_LOG_FILE`` tells the child which file it writes to so *it* can
    rotate by size. Rotation has to happen in-process: the append-mode
    descriptor we hand over keeps writing to the same inode, which is exactly
    what the copy-and-truncate rotator is designed for.

    ``WSCTL_ADMIN_PASSWORD`` carries the bootstrap password instead of a
    ``--admin-password`` flag: a flag is world-readable in ``/proc/*/cmdline``.
    The variable is a one-shot -- the child must not re-export it downwards.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in LIFECYCLE_ENV_KEYS
    }
    env["WSCTL_DAEMON"] = "1"
    env["WSCTL_LOG_FILE"] = str(logfile_path(settings))
    if rolling_from is not None:
        # The replacement is allowed to take over the pid file from exactly
        # this pid -- see ``claim_pidfile``.
        env["WSCTL_ROLLING_FROM_PID"] = str(rolling_from)
    if admin_password:
        env["WSCTL_ADMIN_PASSWORD"] = admin_password
    return env


def log_history_paths(settings: Settings, backup_count: int | None = None) -> list[Path]:
    """Log files oldest-first: ``.N`` … ``.1`` then the live file.

    Rotation keeps history in sidecar files, so "show me the last N lines" has
    to read across them or a rotation would silently hide everything.
    ``backup_count`` defaults to the configured value -- hardcoding 3 hid
    history from anyone who had raised the setting.
    """
    if backup_count is None:
        backup_count = int(getattr(settings, "log_backup_count", 3) or 0)
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
        offset = handle.tell()
        # copy-and-truncate empties the *same* inode. A size check alone is
        # not enough: if the rotation finishes between two polls and the new
        # content is at least as long as the old offset, the reader resumes in
        # the middle of the new text and silently skips its prefix. The
        # rotator leaves a generation marker for exactly this case.
        gen_path = live.with_name(f"{live.name}.gen")

        def generation() -> str:
            try:
                return gen_path.read_text(encoding="utf-8")
            except OSError:
                return ""

        seen_gen = generation()
        try:
            while True:
                if generation() != seen_gen:
                    # This inode was emptied since we last looked: whatever is
                    # at the old offset is not a continuation of our stream.
                    seen_gen = generation()
                    handle.seek(0)
                    offset = 0
                chunk = handle.readline()
                if chunk:
                    offset = handle.tell()
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                else:
                    try:
                        size = os.fstat(handle.fileno()).st_size
                    except OSError:
                        size = offset
                    if size < offset:
                        handle.seek(0)
                        offset = 0
                    time.sleep(0.3)
        except KeyboardInterrupt:
            return
