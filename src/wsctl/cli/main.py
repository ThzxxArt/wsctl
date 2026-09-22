"""wsctl command-line entry point."""

from __future__ import annotations

import contextlib
import difflib
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import tomllib
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, NoReturn, cast

import typer
from rich.console import Console
from rich.table import Table

from wsctl import __version__
from wsctl.cli import validate as vmod

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from wsctl.cli.client import ApiClient
    from wsctl.cli.daemon import Instance
    from wsctl.core.config import Settings
    from wsctl.core.store import Store


class _LazyModule:
    """Import a module on first attribute access.

    ``wsctl version`` and ``wsctl --help`` must not pay for pydantic-settings,
    websockets, argon2 and pyotp: those belong to commands that actually talk
    to a server or touch the database.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._module: Any = None

    def _load(self) -> Any:
        if self._module is None:
            import importlib

            self._module = importlib.import_module(self._name)
        return self._module

    def __getattr__(self, item: str) -> Any:
        return getattr(self._load(), item)

daemon_mod = _LazyModule("wsctl.cli.daemon")
backup_mod = _LazyModule("wsctl.core.backup")
totp = _LazyModule("wsctl.core.totp")
user_admin = _LazyModule("wsctl.core.user_admin")
config_mod = _LazyModule("wsctl.core.config")
logging_mod = _LazyModule("wsctl.core.logging")
store_mod = _LazyModule("wsctl.core.store")
client_mod = _LazyModule("wsctl.cli.client")
connect_mod = _LazyModule("wsctl.cli.connect")

app = typer.Typer(
    name="wsctl",
    help="单机部署的 Web 在线终端：多会话、多用户、审计、分享、录制与可观测。",
    no_args_is_help=True,
    add_completion=True,
)
user_app = typer.Typer(name="user", help="管理用户。", no_args_is_help=True)
config_app = typer.Typer(name="config", help="查看与管理配置。", no_args_is_help=True)
session_app = typer.Typer(name="session", help="管理终端会话。", no_args_is_help=True)
completion_app = typer.Typer(
    name="completion", help="安装或查看 shell 补全脚本。", no_args_is_help=True
)
app.add_typer(user_app)
app.add_typer(config_app)
app.add_typer(session_app)
app.add_typer(completion_app)

console = Console()
err_console = Console(stderr=True)


@app.callback(invoke_without_command=True)
def _root(
    version_flag: Annotated[
        bool, typer.Option("--version", help="显示版本并退出。", is_eager=True)
    ] = False,
) -> None:
    if version_flag:
        console.print(f"wsctl {__version__}")
        raise typer.Exit()


def _fail(message: str) -> NoReturn:
    err_console.print(f"[red]{message}[/]")
    raise typer.Exit(code=1)


def _usage_fail(error: vmod.CliUsageError) -> NoReturn:
    """Report a contradictory command line (exit code 2, like a usage error)."""
    err_console.print(f"[red]参数错误：[/]{error.message}")
    if error.hint:
        err_console.print(f"[dim]{error.hint}[/]")
    raise typer.Exit(code=2)


def _port_free(host: str, port: int) -> bool:
    """Whether nothing is listening on ``host:port`` right now."""
    probe = "127.0.0.1" if host in ("", "0.0.0.0", "::", "[::]") else host
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((probe, port)) != 0


def _fmt_duration(seconds: float) -> str:
    seconds = int(max(0.0, seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}天{hours}时{minutes}分"
    if hours:
        return f"{hours}时{minutes}分"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


def _api_client(url: str | None) -> ApiClient:
    creds = client_mod.load_credentials()
    base = url or (str(creds["url"]) if creds.get("url") else None)
    if not base:
        _fail("缺少服务器地址：请传入 --url，或先运行 'wsctl login <url>'")
    token = str(creds["token"]) if creds.get("token") else None
    return cast("ApiClient", client_mod.ApiClient(base, token))


def _settings_from(config: Path | None, **overrides: object) -> Settings:
    if config is not None:
        import os

        os.environ["WSCTL_CONFIG"] = str(config)
    return cast("Settings", config_mod.load_settings(**overrides))


def _print_json(payload: object) -> None:
    """Machine-readable output on stdout, so it composes with pipes."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")


@app.command()
def version() -> None:
    """显示 wsctl 版本。"""
    console.print(f"wsctl {__version__}")


SUPPORTED_SHELLS = ("bash", "zsh", "fish", "powershell", "pwsh")


def _detect_shell() -> str:
    """Best-effort shell detection, preferring ``$SHELL`` over the parent proc."""
    import os as _os

    name = _os.path.basename(_os.environ.get("SHELL", "") or "")
    if name in SUPPORTED_SHELLS:
        return name
    for candidate in ("bash", "zsh", "fish"):
        if _os.environ.get(f"{candidate.upper()}_VERSION"):
            return candidate
    return "bash"


@completion_app.command("install")
def completion_install(
    shell: Annotated[
        str | None,
        typer.Argument(help="目标 shell：bash / zsh / fish / powershell（省略则自动识别）。"),
    ] = None,
) -> None:
    """安装 shell 补全脚本，并写入对应的 rc 文件。

    支持 bash / zsh / fish / PowerShell。Typer 的 ``--install-completion``
    只覆盖 bash，这里补齐其余 shell。
    """
    target = shell or _detect_shell()
    if target not in SUPPORTED_SHELLS:
        _fail(f"不支持的 shell：{target}（可选：{'、'.join(SUPPORTED_SHELLS)}）")
    try:
        from typer._completion_shared import install as typer_install

        used, path = typer_install(shell=target)
    except Exception as exc:
        _fail(f"安装补全失败：{exc}")
        return
    console.print(f"[green]{used} 补全已安装到[/] {path}")
    if used == "bash":
        console.print("[dim]请重新打开终端，或执行：source ~/.bashrc[/]")
    elif used == "zsh":
        console.print("[dim]请重新打开终端，或执行：exec zsh[/]")
    elif used == "fish":
        console.print("[dim]请重新打开终端，或执行：exec fish[/]")


@completion_app.command("show")
def completion_show(
    shell: Annotated[
        str | None,
        typer.Argument(help="目标 shell：bash / zsh / fish / powershell（省略则自动识别）。"),
    ] = None,
) -> None:
    """打印补全脚本（可自行保存到任意位置后 source）。"""
    target = shell or _detect_shell()
    if target not in SUPPORTED_SHELLS:
        _fail(f"不支持的 shell：{target}（可选：{'、'.join(SUPPORTED_SHELLS)}）")
    try:
        from typer._completion_shared import get_completion_script

        script = get_completion_script(
            prog_name="wsctl", complete_var="_WSCTL_COMPLETE", shell=target
        )
    except Exception as exc:
        _fail(f"生成补全脚本失败：{exc}")
        return
    sys.stdout.write(script + "\n")


def _which(name: str) -> str:
    return shutil.which(name) or ""


@app.command()
def doctor(
    config: Annotated[Path | None, typer.Option("--config", "-c", help="配置文件路径。")] = None,
    url: Annotated[
        str | None, typer.Option("--url", help="服务器地址（可选，用于检查连通性）。")
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="以 JSON 输出检查结果。")
    ] = False,
) -> None:
    """检查运行环境、配置与依赖是否就绪。"""
    ok, warn, bad = "[green]正常[/]", "[yellow]警告[/]", "[red]失败[/]"
    rows: list[tuple[str, str]] = []

    def add(label: str, value: str, plain: str) -> None:
        rows.append((label, f"{value} {plain}"))

    py = sys.version_info
    add(
        "Python",
        f"{py.major}.{py.minor}.{py.micro}",
        ok if py >= (3, 11) else bad,
    )

    settings = _settings_from(config)

    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        probe = settings.data_dir / ".wsctl-doctor"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add("数据目录", str(settings.data_dir), ok)
    except OSError as exc:
        add("数据目录", f"{settings.data_dir}（{exc}）", bad)

    try:
        store = store_mod.Store(settings.db_path)
        try:
            users = store.user_count()
        finally:
            store.close()
        add("数据库", f"{settings.db_path}（{users} 个用户）", ok)
    except Exception as exc:
        add("数据库", f"（{exc}）", bad)

    cfg = settings.config_path
    if cfg.is_file():
        try:
            tomllib.loads(cfg.read_text("utf-8"))
            add("配置文件", str(cfg), ok)
        except tomllib.TOMLDecodeError as exc:
            add("配置文件", f"{cfg}（{exc}）", bad)
    else:
        add("配置文件", f"{cfg}（不存在，使用默认值）", warn)

    shell = settings.shell
    shell_ok = Path(shell).exists() or bool(_which(shell))
    add("默认 shell", shell, ok if shell_ok else bad)

    tmux = _which("tmux")
    add("tmux（跨重启恢复）", tmux or "未安装", ok if tmux else warn)
    ssh = _which("ssh")
    add("ssh（SSH 后端）", ssh or "未安装", ok if ssh else warn)
    lrzsz = _which("sz") or _which("rz")
    add("lrzsz（ZMODEM）", lrzsz or "未安装", ok if lrzsz else warn)

    daemon_ok = sys.platform != "win32"
    add(
        "后台启动（wsctl start）",
        "支持" if daemon_ok else "不支持（Windows，请用 NSSM 或计划任务）",
        ok if daemon_ok else warn,
    )

    instance = daemon_mod.read_instance(settings)
    if instance is not None:
        add("运行状态", f"运行中（pid {instance.pid}，{instance.host}:{instance.port}）", ok)
    else:
        add("运行状态", "未运行", "[dim]-[/]")

    reuse_ok = hasattr(socket, "SO_REUSEPORT")
    add(
        "SO_REUSEPORT（零停机重启）",
        "支持" if reuse_ok else "不支持",
        ok if reuse_ok else warn,
    )

    if not settings.reuse_port:
        free = _port_free(settings.host, settings.port)
        add(
            "监听端口",
            f"{settings.host}:{settings.port}（{'可用' if free else '被占用'}）",
            ok if free else warn,
        )

    if url or client_mod.load_credentials().get("url"):
        try:
            client = _api_client(url)
            health = client.request("GET", "/healthz", auth=False)
            add("服务器", f"{client.base_url}（版本 {health.get('version')}）", ok)
        except client_mod.ApiError as exc:
            add("服务器", f"（{exc}）", bad)
    else:
        add("服务器", "未配置（可用 --url 检查）", "[dim]-[/]")

    # Operational details that only show up once something goes wrong.
    try:
        usage = shutil.disk_usage(settings.data_dir)
        free_gb = usage.free / (1024**3)
        add(
            "磁盘剩余空间",
            f"{free_gb:.1f} GB（数据目录所在分区）",
            ok if free_gb >= 1.0 else (bad if free_gb < 0.1 else warn),
        )
    except OSError as exc:
        add("磁盘剩余空间", f"（{exc}）", warn)

    add(
        "密码策略",
        f"创建/改密强制：非空且至少 {8} 位（存量弱密码不会被强制修改）",
        ok,
    )

    log_file = settings.log_file or (
        daemon_mod.logfile_path(settings) if daemon_mod.read_instance(settings) else None
    )
    if log_file is not None and log_file.is_file():
        size_mb = log_file.stat().st_size / (1024 * 1024)
        detail = f"{log_file}（{size_mb:.1f} MB"
        if settings.log_max_bytes > 0:
            detail += f"，超过 {settings.log_max_bytes / (1024 * 1024):.0f} MB 自动轮转"
        detail += "）"
        add("日志文件", detail, ok if size_mb < 512 else warn)
    else:
        add("日志文件", "未使用文件日志（前台输出到终端）", "[dim]-[/]")

    add(
        "系统时间",
        time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "[dim]-[/]",
    )

    if json_output:
        import re

        payload = [
            {"check": label, "result": re.sub(r"\[[^\]]*\]", "", value).strip()}
            for label, value in rows
        ]
        # ``sys.stdout`` so it composes with pipes.
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return

    table = Table("检查项", "结果")
    for label, value in rows:
        table.add_row(label, value)
    console.print(table)


@app.command()
def backup(
    output: Annotated[Path, typer.Argument(help="输出的 tar.gz 路径。")],
    config: Annotated[Path | None, typer.Option("--config", "-c", help="配置文件路径。")] = None,
    include_config: Annotated[
        bool, typer.Option("--include-config", help="同时备份配置文件。")
    ] = False,
) -> None:
    """一致性备份数据库与录制到 tar.gz（SQLite 在线备份）。"""
    settings = _settings_from(config)
    try:
        path = backup_mod.create_backup(
            settings.db_path,
            settings.recordings_dir,
            output,
            config_path=settings.config_path,
            include_config=include_config,
            version=__version__,
        )
    except backup_mod.BackupError as exc:
        _fail(str(exc))
    console.print(f"[green]已备份到[/] {path}")


@app.command()
def restore(
    archive: Annotated[Path, typer.Argument(help="备份的 tar.gz 路径。")],
    config: Annotated[Path | None, typer.Option("--config", "-c", help="配置文件路径。")] = None,
    force: Annotated[
        bool, typer.Option("--force", help="覆盖现有数据库（原库保存为 .bak）。")
    ] = False,
    with_config: Annotated[
        bool, typer.Option("--with-config", help="同时恢复配置文件。")
    ] = False,
) -> None:
    """从备份恢复数据库与录制。"""
    settings = _settings_from(config)
    try:
        manifest = backup_mod.restore_backup(
            archive,
            settings.db_path,
            settings.recordings_dir,
            config_path=settings.config_path,
            restore_config=with_config,
            force=force,
        )
    except backup_mod.BackupError as exc:
        _fail(str(exc))
    created = manifest.get("created_at")
    when = (
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(created)))
        if isinstance(created, (int, float))
        else "未知时间"
    )
    console.print(f"[green]已从备份恢复[/] {archive}（备份于 {when}）")


# Rules shared by ``serve`` and ``start``: they describe which options cannot be
# combined, so a contradictory command line fails before any process is spawned.
SERVE_RULES = [
    vmod.exclusive("daemon", "foreground"),
    vmod.exclusive(
        "daemon",
        "reuse_port",
        message="--daemon 与 --reuse-port 不兼容：多实例热切换请用 --foreground（或 systemd）管理",
    ),
    vmod.exclusive(
        "no_auth",
        "admin_password",
        message="--no-auth 已关闭认证，--admin-password 无意义",
    ),
    vmod.requires("ssl_cert", "ssl_key", message="--ssl-cert 需要同时提供 --ssl-key"),
    vmod.requires("ssl_key", "ssl_cert", message="--ssl-key 需要同时提供 --ssl-cert"),
    vmod.choices(
        "backend",
        ("local", "tmux"),
        message="--backend 只能是 local 或 tmux（ssh 仅用于单个会话：wsctl session new --ssh）",
    ),
]

_SERVE_FLAG_MAP = {
    "host": "--host",
    "port": "--port",
    "config": "--config",
    "admin_password": "--admin-password",
    "ssl_cert": "--ssl-cert",
    "ssl_key": "--ssl-key",
    "log_level": "--log-level",
    "new": "--new",
    "backend": "--backend",
}


def _serve_child_argv(options: vmod.Options) -> list[str]:
    """The argv a detached child should run to reproduce this invocation."""
    argv = [sys.executable, "-m", "wsctl", "serve", "--foreground"]
    for key, flag in _SERVE_FLAG_MAP.items():
        if options.has(key):
            argv += [flag, str(options.get(key))]
    if options.has("no_auth"):
        argv.append("--no-auth")
    if options.has("log_json"):
        argv.append("--log-json")
    return argv


def _serve_settings(
    config: Path | None,
    *,
    host: str | None,
    port: int | None,
    ssl_cert: Path | None,
    ssl_key: Path | None,
    backend: str | None,
    reuse_port: bool,
    no_auth: bool,
    log_json: bool,
    log_level: str | None,
) -> Settings:
    overrides: dict[str, object] = {
        "host": host,
        "port": port,
        "ssl_cert": ssl_cert,
        "ssl_key": ssl_key,
        "default_backend": backend,
        "reuse_port": reuse_port or None,
    }
    if no_auth:
        overrides["auth_required"] = False
    if log_json:
        overrides["log_json"] = True
    settings = _settings_from(config, **overrides)
    if log_level:
        settings.log_level = log_level
    return settings


def _start_background(settings: Settings, argv: list[str], *, timeout: float) -> None:
    try:
        instance = daemon_mod.start(settings, argv, timeout=timeout)
    except daemon_mod.DaemonError as exc:
        _fail(str(exc))
    console.print(
        f"[green]已在后台启动[/] pid {instance.pid}  "
        f"[cyan]{instance.host}:{instance.port}[/]"
    )
    console.print(f"[dim]日志：{daemon_mod.logfile_path(settings)}（wsctl logs -f 可跟踪）[/]")
    console.print("[dim]若为首次启动，admin 随机密码打印在日志中。[/]")


def _serve_foreground(
    settings: Settings, *, admin_password: str | None, startup_command: str | None
) -> None:
    logging_mod.configure_logging(settings.log_level, json_output=settings.log_json)

    from wsctl.server.app import create_app

    try:
        store = store_mod.Store(settings.db_path)
    except (OSError, sqlite3.Error) as exc:
        _fail(f"无法打开数据库 {settings.db_path}：{exc}")
    _ensure_admin(store, settings, admin_password)
    application = create_app(settings, store=store, startup_command=startup_command)

    if settings.reuse_port and not hasattr(socket, "SO_REUSEPORT"):
        _fail("当前平台不支持 SO_REUSEPORT，无法使用 --reuse-port")

    instance: Instance | None = None
    if settings.reuse_port:
        console.print("[dim]SO_REUSEPORT：多实例共享端口，后台生命周期命令不可用[/]")
    else:
        try:
            instance = daemon_mod.claim_pidfile(settings)
        except daemon_mod.DaemonError as exc:
            _fail(str(exc))

    import uvicorn

    scheme = "https" if settings.ssl_cert else "http"
    console.print(
        f"[bold green]wsctl {__version__}[/] serving on "
        f"[cyan]{scheme}://{settings.host}:{settings.port}[/]"
    )
    if not settings.auth_required:
        err_console.print("[bold yellow]warning:[/] authentication is disabled")

    ssl_certfile = str(settings.ssl_cert) if settings.ssl_cert else None
    ssl_keyfile = str(settings.ssl_key) if settings.ssl_key else None

    try:
        if settings.reuse_port:
            from wsctl.core.net import make_reuse_socket

            uv_config = uvicorn.Config(
                application,
                log_level=settings.log_level,
                ssl_certfile=ssl_certfile,
                ssl_keyfile=ssl_keyfile,
            )
            server = uvicorn.Server(uv_config)
            try:
                sock = make_reuse_socket(settings.host, settings.port)
            except OSError as exc:
                _fail(f"无法以 SO_REUSEPORT 绑定 {settings.host}:{settings.port}：{exc}")
            console.print("[dim]SO_REUSEPORT enabled: a new instance can take over this port[/]")
            try:
                server.run(sockets=[sock])
            finally:
                sock.close()
            return

        uvicorn.run(
            application,
            host=settings.host,
            port=settings.port,
            log_level=settings.log_level,
            ssl_certfile=ssl_certfile,
            ssl_keyfile=ssl_keyfile,
        )
    finally:
        if instance is not None:
            daemon_mod.release_pidfile(settings, instance)


@app.command()
def serve(
    host: Annotated[str | None, typer.Option(help="监听地址。")] = None,
    port: Annotated[int | None, typer.Option(help="监听端口。")] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c", help="配置文件路径。")] = None,
    no_auth: Annotated[
        bool, typer.Option("--no-auth", help="关闭认证（不安全）。")
    ] = False,
    admin_password: Annotated[
        str | None, typer.Option("--admin-password", help="初始管理员密码。")
    ] = None,
    ssl_cert: Annotated[Path | None, typer.Option(help="TLS 证书文件。")] = None,
    ssl_key: Annotated[Path | None, typer.Option(help="TLS 私钥文件。")] = None,
    log_level: Annotated[str | None, typer.Option(help="日志级别。")] = None,
    log_json: Annotated[bool, typer.Option("--log-json", help="输出 JSON 日志。")] = False,
    new: Annotated[
        str | None, typer.Option("--new", help="启动时创建并运行此命令的会话。")
    ] = None,
    backend: Annotated[
        str | None, typer.Option("--backend", help="默认会话后端：local 或 tmux。")
    ] = None,
    reuse_port: Annotated[
        bool,
        typer.Option("--reuse-port", help="以 SO_REUSEPORT 绑定，实现零停机重启。"),
    ] = False,
    daemon: Annotated[
        bool, typer.Option("--daemon", help="后台运行（等价于 wsctl start）。")
    ] = False,
    foreground: Annotated[
        bool, typer.Option("--foreground", help="前台运行（默认）。")
    ] = False,
    timeout: Annotated[
        float, typer.Option("--timeout", help="后台启动时等待就绪的秒数。")
    ] = 20.0,
) -> None:
    """启动 wsctl 服务（前台；加 --daemon 转后台）。"""
    options = vmod.Options(
        {
            "host": host,
            "port": port,
            "config": config,
            "no_auth": no_auth,
            "admin_password": admin_password,
            "ssl_cert": ssl_cert,
            "ssl_key": ssl_key,
            "log_level": log_level,
            "log_json": log_json,
            "new": new,
            "backend": backend,
            "reuse_port": reuse_port,
            "daemon": daemon,
            "foreground": foreground,
        }
    )
    try:
        vmod.validate(options, SERVE_RULES)
    except vmod.CliUsageError as exc:
        _usage_fail(exc)

    settings = _serve_settings(
        config,
        host=host,
        port=port,
        ssl_cert=ssl_cert,
        ssl_key=ssl_key,
        backend=backend,
        reuse_port=reuse_port,
        no_auth=no_auth,
        log_json=log_json,
        log_level=log_level,
    )
    if daemon:
        _start_background(settings, _serve_child_argv(options), timeout=timeout)
        return
    _serve_foreground(settings, admin_password=admin_password, startup_command=new)


@app.command()
def start(
    host: Annotated[str | None, typer.Option(help="监听地址。")] = None,
    port: Annotated[int | None, typer.Option(help="监听端口。")] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c", help="配置文件路径。")] = None,
    no_auth: Annotated[
        bool, typer.Option("--no-auth", help="关闭认证（不安全）。")
    ] = False,
    admin_password: Annotated[
        str | None, typer.Option("--admin-password", help="初始管理员密码。")
    ] = None,
    ssl_cert: Annotated[Path | None, typer.Option(help="TLS 证书文件。")] = None,
    ssl_key: Annotated[Path | None, typer.Option(help="TLS 私钥文件。")] = None,
    log_level: Annotated[str | None, typer.Option(help="日志级别。")] = None,
    log_json: Annotated[bool, typer.Option("--log-json", help="输出 JSON 日志。")] = False,
    new: Annotated[
        str | None, typer.Option("--new", help="启动时创建并运行此命令的会话。")
    ] = None,
    backend: Annotated[
        str | None, typer.Option("--backend", help="默认会话后端：local 或 tmux。")
    ] = None,
    timeout: Annotated[
        float, typer.Option("--timeout", help="等待服务就绪的秒数。")
    ] = 20.0,
    force: Annotated[
        bool, typer.Option("--force", help="已在运行时先停止再启动。")
    ] = False,
) -> None:
    """在后台启动 wsctl（非 systemd；配合 stop/status/logs/restart）。"""
    options = vmod.Options(
        {
            "host": host,
            "port": port,
            "config": config,
            "no_auth": no_auth,
            "admin_password": admin_password,
            "ssl_cert": ssl_cert,
            "ssl_key": ssl_key,
            "log_level": log_level,
            "log_json": log_json,
            "new": new,
            "backend": backend,
        }
    )
    try:
        vmod.validate(options, SERVE_RULES)
    except vmod.CliUsageError as exc:
        _usage_fail(exc)

    settings = _serve_settings(
        config,
        host=host,
        port=port,
        ssl_cert=ssl_cert,
        ssl_key=ssl_key,
        backend=backend,
        reuse_port=False,
        no_auth=no_auth,
        log_json=log_json,
        log_level=log_level,
    )
    if force and daemon_mod.read_instance(settings) is not None:
        try:
            daemon_mod.stop(settings, timeout=timeout)
        except daemon_mod.DaemonError as exc:
            _fail(str(exc))
    _start_background(settings, _serve_child_argv(options), timeout=timeout)


@app.command()
def stop(
    host: Annotated[str | None, typer.Option(help="监听地址（与启动时一致）。")] = None,
    port: Annotated[int | None, typer.Option(help="监听端口（与启动时一致）。")] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    timeout: Annotated[float, typer.Option("--timeout", help="优雅退出的等待秒数。")] = 15.0,
    force: Annotated[bool, typer.Option("--force", help="超时后强制 SIGKILL。")] = False,
) -> None:
    """停止后台运行的 wsctl。"""
    settings = _settings_from(config, host=host, port=port)
    try:
        instance = daemon_mod.stop(settings, timeout=timeout, force=force)
    except daemon_mod.DaemonError as exc:
        _fail(str(exc))
    console.print(f"[green]已停止[/] pid {instance.pid}")


@app.command()
def restart(
    host: Annotated[str | None, typer.Option(help="监听地址。")] = None,
    port: Annotated[int | None, typer.Option(help="监听端口。")] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c", help="配置文件路径。")] = None,
    no_auth: Annotated[bool, typer.Option("--no-auth", help="关闭认证。")] = False,
    admin_password: Annotated[str | None, typer.Option("--admin-password")] = None,
    ssl_cert: Annotated[Path | None, typer.Option(help="TLS 证书文件。")] = None,
    ssl_key: Annotated[Path | None, typer.Option(help="TLS 私钥文件。")] = None,
    log_level: Annotated[str | None, typer.Option(help="日志级别。")] = None,
    log_json: Annotated[bool, typer.Option("--log-json")] = False,
    new: Annotated[str | None, typer.Option("--new")] = None,
    backend: Annotated[str | None, typer.Option("--backend")] = None,
    timeout: Annotated[float, typer.Option("--timeout", help="等待就绪的秒数。")] = 20.0,
) -> None:
    """重启后台运行的 wsctl。"""
    options = vmod.Options(
        {
            "host": host,
            "port": port,
            "config": config,
            "no_auth": no_auth,
            "admin_password": admin_password,
            "ssl_cert": ssl_cert,
            "ssl_key": ssl_key,
            "log_level": log_level,
            "log_json": log_json,
            "new": new,
            "backend": backend,
        }
    )
    try:
        vmod.validate(options, SERVE_RULES)
    except vmod.CliUsageError as exc:
        _usage_fail(exc)
    settings = _serve_settings(
        config,
        host=host,
        port=port,
        ssl_cert=ssl_cert,
        ssl_key=ssl_key,
        backend=backend,
        reuse_port=False,
        no_auth=no_auth,
        log_json=log_json,
        log_level=log_level,
    )
    try:
        instance = daemon_mod.restart(settings, _serve_child_argv(options), timeout=timeout)
    except daemon_mod.DaemonError as exc:
        _fail(str(exc))
    console.print(f"[green]已重启[/] pid {instance.pid}  [cyan]{instance.host}:{instance.port}[/]")


@app.command()
def status(
    host: Annotated[str | None, typer.Option(help="监听地址（与启动时一致）。")] = None,
    port: Annotated[int | None, typer.Option(help="监听端口（与启动时一致）。")] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="以 JSON 输出。")] = False,
) -> None:
    """查看后台 wsctl 的运行状态。"""
    settings = _settings_from(config, host=host, port=port)
    instance = daemon_mod.read_instance(settings)
    if instance is None:
        others = daemon_mod.discover(settings)
        if json_output:
            missing = {"running": False, "others": [o.to_dict() for o in others]}
            sys.stdout.write(json.dumps(missing, ensure_ascii=False, indent=2) + "\n")
        else:
            console.print("[yellow]未在运行[/]")
            for other in others:
                console.print(
                    f"[dim]发现其他实例：pid {other.pid} {other.host}:{other.port}[/]"
                )
        raise typer.Exit(code=1)

    info = daemon_mod.health_info(settings)
    payload: dict[str, object] = {"running": True, **instance.to_dict(), "health": info}
    if json_output:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return
    table = Table("项", "值")
    table.add_row("状态", "[green]运行中[/]")
    table.add_row("PID", str(instance.pid))
    table.add_row("地址", f"{instance.host}:{instance.port}")
    table.add_row("版本", instance.version)
    table.add_row("运行时长", _fmt_duration(instance.uptime))
    if info is not None:
        table.add_row("会话数", str(info.get("sessions", "?")))
    console.print(table)


@app.command()
def logs(
    host: Annotated[str | None, typer.Option(help="监听地址（与启动时一致）。")] = None,
    port: Annotated[int | None, typer.Option(help="监听端口（与启动时一致）。")] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="持续跟踪。")] = False,
    lines: Annotated[int, typer.Option("--lines", "-n", help="显示末尾行数。")] = 100,
) -> None:
    """查看后台 wsctl 的日志。"""
    settings = _settings_from(config, host=host, port=port)
    try:
        daemon_mod.tail_log(settings, lines=max(lines, 0), follow=follow)
    except daemon_mod.DaemonError as exc:
        _fail(str(exc))


@app.command()
def reload(
    host: Annotated[str | None, typer.Option(help="监听地址（与启动时一致）。")] = None,
    port: Annotated[int | None, typer.Option(help="监听端口（与启动时一致）。")] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """请求运行中的 wsctl 重载配置（发送 SIGHUP）。"""
    settings = _settings_from(config, host=host, port=port)
    try:
        instance = daemon_mod.reload_(settings)
    except daemon_mod.DaemonError as exc:
        _fail(str(exc))
    console.print(f"[green]已请求重载配置[/]（pid {instance.pid}）")


def _ensure_admin(
    store: Store, settings: Settings, password: str | None
) -> None:
    if not settings.auth_required or store.user_count() > 0:
        return
    if not password:
        password = secrets.token_urlsafe(12)
        generated = True
    else:
        generated = False
    try:
        store.user_create("admin", password, role="admin")
    except sqlite3.IntegrityError:
        # Another instance sharing this data directory won the bootstrap race.
        err_console.print("[dim]admin user already created by another instance[/]")
        return
    if generated:
        err_console.print(
            "[bold yellow]已创建管理员用户 'admin'，密码为：[/]\n"
            f"    [bold]{password}[/]\n"
            "[dim]可用以下命令修改：wsctl user passwd admin[/]"
        )
    else:
        console.print("[green]已创建管理员用户 'admin'。[/]")


@user_app.command("add")
def user_add(
    username: Annotated[str, typer.Argument(help="用户名。")],
    password: Annotated[
        str | None, typer.Option("--password", "-p", help="密码（省略则交互输入）。")
    ] = None,
    role: Annotated[str, typer.Option(help="角色：admin 或 user。")] = "user",
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="以 JSON 输出。")] = False,
) -> None:
    """创建一个用户（密码须非空且至少 8 位）。"""
    try:
        vmod.validate(vmod.Options({"role": role}), [vmod.choices("role", ("admin", "user"))])
    except vmod.CliUsageError as exc:
        _usage_fail(exc)
    settings = _settings_from(config)
    if password is None:
        password = typer.prompt("密码", hide_input=True, confirmation_prompt=True)
    store = store_mod.Store(settings.db_path)
    try:
        user_admin.create(store, username, password, role=role)
    except user_admin.UserAdminError as exc:
        _fail(exc.message)
    finally:
        store.close()
    if json_output:
        _print_json({"username": username, "role": role})
        return
    console.print(f"[green]已创建用户[/] {username}（[cyan]{role}[/]）")


@user_app.command("list")
def user_list(
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="以 JSON 输出。")] = False,
) -> None:
    """列出所有用户。"""
    settings = _settings_from(config)
    store = store_mod.Store(settings.db_path)
    try:
        users = store.user_list()
    finally:
        store.close()
    if json_output:
        _print_json(
            [
                {
                    "id": u.id,
                    "username": u.username,
                    "role": u.role,
                    "disabled": u.disabled,
                    "created_at": u.created_at,
                }
                for u in users
            ]
        )
        return
    table = Table("ID", "用户名", "角色", "已禁用")
    for u in users:
        table.add_row(str(u.id), u.username, u.role, "是" if u.disabled else "否")
    console.print(table)


@user_app.command("del")
def user_del(
    username: Annotated[str, typer.Argument(help="用户名。")],
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """删除一个用户（不会删除最后一个管理员）。"""
    settings = _settings_from(config)
    store = store_mod.Store(settings.db_path)
    try:
        user_admin.delete(store, username)
    except user_admin.UserAdminError as exc:
        _fail(exc.message)
    finally:
        store.close()
    console.print(f"[green]已删除用户[/] {username}")


@user_app.command("passwd")
def user_passwd(
    username: Annotated[str, typer.Argument(help="用户名。")],
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """修改用户密码（并吊销该用户已有的登录态）。"""
    settings = _settings_from(config)
    password = typer.prompt("新密码", hide_input=True, confirmation_prompt=True)
    store = store_mod.Store(settings.db_path)
    try:
        user_admin.set_password(store, username, password)
    except user_admin.UserAdminError as exc:
        _fail(exc.message)
    finally:
        store.close()
    console.print(f"[green]已更新密码并吊销旧登录态：[/] {username}")


@user_app.command("role")
def user_role(
    username: Annotated[str, typer.Argument(help="用户名。")],
    role: Annotated[str, typer.Argument(help="角色：admin 或 user。")],
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """设置用户角色（不会降级最后一个管理员）。"""
    try:
        vmod.validate(vmod.Options({"role": role}), [vmod.choices("role", ("admin", "user"))])
    except vmod.CliUsageError as exc:
        _usage_fail(exc)
    settings = _settings_from(config)
    store = store_mod.Store(settings.db_path)
    try:
        user_admin.set_role(store, username, role)
    except user_admin.UserAdminError as exc:
        _fail(exc.message)
    finally:
        store.close()
    console.print(f"[green]{username}[/] 现在是 [cyan]{role}[/]")


@user_app.command("totp")
def user_totp(
    username: Annotated[str, typer.Argument(help="用户名。")],
    disable: Annotated[bool, typer.Option("--disable", help="关闭 TOTP。")] = False,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """启用或关闭用户的 TOTP 两步验证。"""
    settings = _settings_from(config)
    store = store_mod.Store(settings.db_path)
    try:
        if store.user_get(username) is None:
            _fail(f"没有此用户：{username}")
        if disable:
            store.user_clear_totp(username)
            console.print(f"[green]已关闭 TOTP[/]：{username}")
            return
        secret = totp.generate_secret()
        uri = totp.provisioning_uri(secret, username, issuer=settings.totp_issuer)
        console.print(f"密钥：[bold]{secret}[/]")
        console.print(f"otpauth URI：{uri}")
        try:
            import segno

            segno.make(uri, error="m").terminal(compact=True)
        except Exception:
            # A QR is a convenience; if it cannot be rendered fall back to the URI.
            console.print("[dim]（无法在终端渲染二维码，请手动录入上方 URI）[/]")
        console.print("[dim]在认证器中扫描二维码，然后输入一次验证码确认。[/]")
        code = typer.prompt("一次性验证码")
        if not totp.verify(secret, code):
            _fail("验证码校验失败，未启用 TOTP")
        store.user_set_totp(username, secret)
        console.print(f"[green]已启用 TOTP[/]：{username}")
    finally:
        store.close()


@user_app.command("disable")
def user_disable(
    username: Annotated[str, typer.Argument(help="用户名。")],
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """禁用一个用户（其登录态立即失效；不能禁用最后一个管理员）。"""
    settings = _settings_from(config)
    store = store_mod.Store(settings.db_path)
    try:
        user_admin.set_disabled(store, username, True)
    except user_admin.UserAdminError as exc:
        _fail(exc.message)
    finally:
        store.close()
    console.print(f"[green]已禁用[/] {username}")


@user_app.command("enable")
def user_enable(
    username: Annotated[str, typer.Argument(help="用户名。")],
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """重新启用一个用户。"""
    settings = _settings_from(config)
    store = store_mod.Store(settings.db_path)
    try:
        user_admin.set_disabled(store, username, False)
    except user_admin.UserAdminError as exc:
        _fail(exc.message)
    finally:
        store.close()
    console.print(f"[green]已启用[/] {username}")


@app.command()
def audit(
    limit: Annotated[int, typer.Option("--limit", "-n", help="条目数量。")] = 50,
    url: Annotated[str | None, typer.Option("--url", help="服务器地址。")] = None,
    event: Annotated[str | None, typer.Option("--event", help="按事件类型筛选。")] = None,
    user_id: Annotated[int | None, typer.Option("--user-id", help="按用户 ID 筛选。")] = None,
    ip: Annotated[str | None, typer.Option("--ip", help="按 IP 筛选。")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="以 JSON 输出。")] = False,
) -> None:
    """查看服务端审计日志（仅管理员）。"""
    client = _api_client(url)
    params = [f"limit={limit}"]
    if event:
        params.append(f"event={urllib.parse.quote(event)}")
    if user_id is not None:
        params.append(f"user_id={user_id}")
    if ip:
        params.append(f"ip={urllib.parse.quote(ip)}")
    try:
        rows = client.request("GET", "/api/audit?" + "&".join(params))
    except client_mod.ApiError as exc:
        _fail(str(exc))
    if json_output:
        _print_json(rows)
        return
    table = Table("时间", "事件", "用户", "会话", "IP", "详情")
    for row in rows:
        table.add_row(
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["ts"])),
            str(row["event"]),
            str(row["user_id"]),
            str(row["term_session_id"] or ""),
            str(row["ip"] or ""),
            (str(row["payload"]) if row["payload"] else "")[:40],
        )
    console.print(table)


@config_app.command("show")
def config_show(
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="以 JSON 输出。")] = False,
) -> None:
    """显示生效中的配置。"""
    settings = _settings_from(config)
    values = settings.model_dump()
    if json_output:
        _print_json(
            {
                "config_path": str(settings.config_path),
                "values": {k: str(v) if isinstance(v, Path) else v for k, v in values.items()},
            }
        )
        return
    table = Table("配置项", "值")
    for key, value in values.items():
        table.add_row(key, str(value))
    console.print(table)
    console.print(f"[dim]配置文件：{settings.config_path}[/]")


@config_app.command("path")
def config_path(config: Annotated[Path | None, typer.Option("--config", "-c")] = None) -> None:
    """打印配置文件路径。"""
    settings = _settings_from(config)
    console.print(str(settings.config_path))


@config_app.command("get")
def config_get(
    key: Annotated[str, typer.Argument(help="配置项名称。")],
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="以 JSON 输出。")] = False,
) -> None:
    """打印某个配置项的生效值（默认裸值便于 $( ) 捕获）。"""
    settings = _settings_from(config)
    if key not in config_mod.Settings.model_fields:
        suggestion = difflib.get_close_matches(key, set(config_mod.Settings.model_fields), n=1)
        hint = f"，是否想用 “{suggestion[0]}”？" if suggestion else ""
        _fail(f"未知的配置项：{key}{hint}")
    value = getattr(settings, key)
    if json_output:
        _print_json({"key": key, "value": value})
        return
    # Bare value on stdout so it composes with $( ) and pipes.
    sys.stdout.write(f"{value}\n")


@config_app.command("validate")
def config_validate(
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """校验配置文件是否合法。"""
    path = _settings_from(config).config_path
    if not path.is_file():
        console.print(f"[yellow]配置文件不存在[/]（将使用默认值）：{path}")
        return
    try:
        data = tomllib.loads(path.read_text("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        _fail(f"配置文件 TOML 语法错误：{exc}")
    try:
        config_mod.Settings(**data)
    except Exception as exc:
        _fail(f"配置项无效：{exc}")
    console.print(f"[green]配置有效[/]：{path}")


CONFIG_TEMPLATE = """# wsctl 配置文件
host = "127.0.0.1"
port = 7681

# --- 认证与安全 ---
# auth_required = true
# session_ttl = 43200
# cookie_secure = false
# trust_proxy = false
# allowed_origins = []
# allowed_ips = ["10.0.0.0/8"]
# security_headers = true
# login_rate_limit = 10
# login_rate_window = 300
# audit_input = false
# totp_issuer = "wsctl"

# --- 会话默认值 ---
# default_shell = "/bin/bash"
# default_cwd = "/home/me"
# default_backend = "local"
# tmux_preserve_on_shutdown = true
# idle_timeout = 3600
# max_life = 86400
# max_sessions = 64
# max_sessions_per_user = 0
# session_max_clients = 0
# session_memory_limit = 67108864
# client_max_bytes = 8388608
# input_rate_limit = 0
# input_rate_burst = 0
# scrollback_bytes = 4194304

# --- 文件面板 ---
# file_root = "/home/me"
# file_max_upload = 104857600

# --- 录制 ---
# auto_record = false
# record_input = false

# --- 可观测 / 日志 ---
# metrics_enabled = true
# metrics_require_auth = false
# log_level = "info"
# log_json = false
# webhook_url = ""

# --- 多实例与保留策略 ---
# instance_ttl = 30
# audit_retention_days = 30
# term_session_retention_days = 30
# recordings_retention_days = 0
# recordings_max_bytes = 0

# --- 数据与 TLS ---
# data_dir = "/var/lib/wsctl"
# ssl_cert = "/etc/wsctl/cert.pem"
# ssl_key = "/etc/wsctl/key.pem"
# reuse_port = false
"""


@config_app.command("edit")
def config_edit(config: Annotated[Path | None, typer.Option("--config", "-c")] = None) -> None:
    """创建（如缺失）并用编辑器打开配置文件。"""
    settings = _settings_from(config)
    path = settings.config_path
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        console.print(f"[green]已创建[/] {path}")
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    try:
        subprocess.call([editor, str(path)])
    except OSError as exc:
        _fail(f"无法启动编辑器 '{editor}'：{exc}")


def _split_trailing_comment(text: str) -> tuple[str, str | None]:
    """Split ``value  # comment`` into ``(value, comment)``, quote-aware."""
    in_single = in_double = False
    for index, ch in enumerate(text):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif (
            ch == "#"
            and not in_single
            and not in_double
            and (index == 0 or text[index - 1] in " \t")
        ):
            return text[:index].rstrip(), text[index:].strip()
    return text.rstrip(), None


_KEY_LINE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*=")


@config_app.command("set")
def config_set(
    key: Annotated[str, typer.Argument(help="配置项名称。")],
    value: Annotated[
        str, typer.Argument(help='TOML 值，例如 8080、"文本"、true、["a", "b"]')
    ],
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """在配置文件中设置一个值（保留原有行内注释）。"""
    settings = _settings_from(config)
    known = set(config_mod.Settings.model_fields)
    if key not in known:
        suggestion = difflib.get_close_matches(key, known, n=1)
        hint = f"，是否想用 “{suggestion[0]}”？" if suggestion else ""
        _fail(f"未知的配置项：{key}{hint}")
    try:
        parsed = tomllib.loads(f"__value__ = {value}")["__value__"]
    except tomllib.TOMLDecodeError:
        _fail('值必须是合法 TOML，例如 8080、"文本"、true、["a", "b"]')

    # Type-check the single value against the settings model before touching the
    # file, so `port = "abc"` is rejected here rather than at the next start.
    try:
        config_mod.Settings(**{key: parsed})
    except Exception as exc:
        _fail(f"配置项 {key} 的值无效：{exc}")

    path = settings.config_path
    original = path.read_text(encoding="utf-8") if path.is_file() else None
    lines = original.splitlines() if original is not None else []
    replaced = False
    for index, line in enumerate(lines):
        match = _KEY_LINE.match(line)
        if match is None or match.group(2) != key:
            continue
        # Keep the author's trailing comment: editing one key must not strip
        # the explanation they wrote next to it.
        _, comment = _split_trailing_comment(line[match.end():])
        suffix = f"  {comment}" if comment else ""
        lines[index] = f"{key} = {value}{suffix}"
        replaced = True
        break
    if not replaced:
        lines.append(f"{key} = {value}")
    new_text = "\n".join(lines).rstrip("\n") + "\n"

    # Validate the whole file (duplicate keys, conflicting values) and roll back
    # on failure so a bad edit never leaves the server unable to start.
    try:
        data = tomllib.loads(new_text)
        config_mod.Settings(**data)
    except Exception as exc:
        _fail(f"写入后配置无效，已放弃修改：{exc}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")
    console.print(f"[green]已设置[/] {key} = {value}  [dim]（{path}）[/]")


@config_app.command("reload")
def config_reload(
    url: Annotated[str | None, typer.Option("--url", help="服务器地址。")] = None,
) -> None:
    """请求运行中的服务重载配置（仅管理员）。

    配置文件写错时**不会静默忽略**：错误会在这里原样打印，服务端日志里也有。
    """
    client = _api_client(url)
    try:
        info = client.request("POST", "/api/config/reload")
    except client_mod.ApiError as exc:
        _fail(str(exc))
    changed = info.get("changed") or []
    errors = info.get("errors") or []
    if errors:
        err_console.print("[red]配置重载存在问题：[/]")
        for message in errors:
            err_console.print(f"  [red]-[/] {message}")
    console.print("[green]已重载[/] 配置；变更项：" + ("、".join(changed) or "无"))
    if errors:
        raise typer.Exit(code=1)


@session_app.command("list")
def session_list(
    url: Annotated[str | None, typer.Option("--url", help="服务器地址。")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="以 JSON 输出。")] = False,
) -> None:
    """列出运行中服务的会话。"""
    client = _api_client(url)
    try:
        sessions = client.request("GET", "/api/sessions")
    except client_mod.ApiError as exc:
        _fail(str(exc))
    if json_output:
        _print_json(sessions)
        return
    table = Table("id", "名称", "PID", "连接数", "所有者", "后端", "状态")
    for s in sessions:
        owner = s.get("owner") or (s.get("owner_id") if s.get("owner_id") is not None else "-")
        state = "运行" if s.get("alive") else "已退出"
        table.add_row(
            str(s["id"]),
            str(s["name"]),
            str(s["pid"]),
            str(s["clients"]),
            str(owner),
            str(s.get("backend", "local")),
            state,
        )
    console.print(table)


@session_app.command("kill")
def session_kill(
    sid: Annotated[str, typer.Argument(help="会话 id。")],
    url: Annotated[str | None, typer.Option("--url", help="服务器地址。")] = None,
) -> None:
    """终止运行中服务的一个会话。"""
    client = _api_client(url)
    try:
        client.request("DELETE", f"/api/sessions/{sid}")
    except client_mod.ApiError as exc:
        _fail(str(exc))
    console.print(f"[green]已终止会话[/] {sid}")


@session_app.command("new")
def session_new(
    name: Annotated[str | None, typer.Option("--name", "-n", help="会话名称。")] = None,
    command: Annotated[
        str | None, typer.Option("--command", "-x", help="要运行的命令（替代 shell）。")
    ] = None,
    cwd: Annotated[str | None, typer.Option("--cwd", help="工作目录。")] = None,
    backend: Annotated[
        str | None, typer.Option("--backend", help="后端：local、tmux 或 ssh。")
    ] = None,
    ssh_host: Annotated[
        str | None, typer.Option("--ssh", help="SSH 目标主机（隐含 --backend ssh）。")
    ] = None,
    ssh_user: Annotated[str | None, typer.Option("--ssh-user", help="SSH 用户名。")] = None,
    ssh_port: Annotated[int | None, typer.Option("--ssh-port", help="SSH 端口。")] = None,
    ssh_identity: Annotated[
        str | None, typer.Option("--ssh-identity", help="SSH 私钥文件。")
    ] = None,
    ssh_option: Annotated[
        list[str] | None, typer.Option("--ssh-option", help="额外的 ssh -o 选项（可重复）。")
    ] = None,
    url: Annotated[str | None, typer.Option("--url", help="服务器地址。")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="以 JSON 输出。")] = False,
) -> None:
    """在运行中的服务上创建会话。"""
    options = vmod.Options(
        {
            "backend": backend,
            "ssh": ssh_host,
            "ssh_user": ssh_user,
            "ssh_port": ssh_port,
            "ssh_identity": ssh_identity,
            "ssh_option": ssh_option,
        }
    )

    def _ssh_backend_conflict(o: vmod.Options) -> str | None:
        if o.has("ssh") and o.has("backend") and o.get("backend") != "ssh":
            return "--ssh 隐含 --backend ssh，不能与 --backend local/tmux 同时使用"
        return None

    try:
        vmod.validate(
            options,
            [
                vmod.choices("backend", ("local", "tmux", "ssh")),
                vmod.requires("ssh_user", "ssh", message="--ssh-user 需要配合 --ssh 使用"),
                vmod.requires("ssh_port", "ssh", message="--ssh-port 需要配合 --ssh 使用"),
                vmod.requires(
                    "ssh_identity", "ssh", message="--ssh-identity 需要配合 --ssh 使用"
                ),
                vmod.requires(
                    "ssh_option", "ssh", message="--ssh-option 需要配合 --ssh 使用"
                ),
                vmod.requires_if(
                    "backend",
                    "ssh",
                    "ssh",
                    message="--backend ssh 需要提供 --ssh <host>",
                ),
                vmod.custom(_ssh_backend_conflict),
            ],
        )
    except vmod.CliUsageError as exc:
        _usage_fail(exc)

    client = _api_client(url)
    ssh_body: dict[str, object] | None = None
    if ssh_host:
        ssh_body = {"host": ssh_host}
        if ssh_user:
            ssh_body["user"] = ssh_user
        if ssh_port:
            ssh_body["port"] = ssh_port
        if ssh_identity:
            ssh_body["identity"] = ssh_identity
        if ssh_option:
            ssh_body["options"] = ssh_option
        if command:
            ssh_body["command"] = command
        backend = backend or "ssh"
    body: dict[str, object] = {
        key: value
        for key, value in {
            "name": name,
            "command": command if ssh_body is None else None,
            "cwd": cwd,
            "backend": backend,
            "ssh": ssh_body,
        }.items()
        if value is not None
    }
    try:
        info = client.request("POST", "/api/sessions", body)
    except client_mod.ApiError as exc:
        _fail(str(exc))
    if json_output:
        _print_json(info)
        return
    console.print(f"[green]已创建会话[/] {info['id']}（[cyan]{info['name']}[/]）")


@session_app.command("rename")
def session_rename(
    sid: Annotated[str, typer.Argument(help="会话 id。")],
    name: Annotated[str, typer.Argument(help="新名称。")],
    url: Annotated[str | None, typer.Option("--url", help="服务器地址。")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="以 JSON 输出。")] = False,
) -> None:
    """重命名一个会话。"""
    client = _api_client(url)
    try:
        info = client.request("PATCH", f"/api/sessions/{sid}", {"name": name})
    except client_mod.ApiError as exc:
        _fail(str(exc))
    if json_output:
        _print_json(info)
        return
    console.print(f"[green]已重命名为[/] {info['name']}")


@session_app.command("attach")
def session_attach(
    sid: Annotated[str, typer.Argument(help="会话 id。")],
    url: Annotated[str | None, typer.Option("--url", help="服务器地址。")] = None,
    token: Annotated[
        str | None, typer.Option("--token", envvar="WSCTL_TOKEN", help="Bearer 令牌。")
    ] = None,
    no_reconnect: Annotated[
        bool, typer.Option("--no-reconnect", help="断线后不自动重连。")
    ] = False,
) -> None:
    """把当前终端连接到已有会话。"""
    try:
        connect_mod.run_connect(url, sid, token, reconnect=not no_reconnect)
    except connect_mod.ConnectError as exc:
        _fail(str(exc))


@session_app.command("record")
def session_record(
    sid: Annotated[str, typer.Argument(help="会话 id。")],
    record_input: Annotated[
        bool, typer.Option("--input", help="同时记录输入内容。")
    ] = False,
    url: Annotated[str | None, typer.Option("--url", help="服务器地址。")] = None,
) -> None:
    """开始将会话录制为 asciinema cast 文件。"""
    client = _api_client(url)
    try:
        info = client.request(
            "POST", f"/api/sessions/{sid}/recording/start", {"record_input": record_input}
        )
    except client_mod.ApiError as exc:
        _fail(str(exc))
    console.print(f"[green]开始录制[/] {sid} -> {info['path']}")


@session_app.command("record-stop")
def session_record_stop(
    sid: Annotated[str, typer.Argument(help="会话 id。")],
    url: Annotated[str | None, typer.Option("--url", help="服务器地址。")] = None,
) -> None:
    """停止录制会话。"""
    client = _api_client(url)
    try:
        client.request("POST", f"/api/sessions/{sid}/recording/stop")
    except client_mod.ApiError as exc:
        _fail(str(exc))
    console.print(f"[green]已停止录制[/] {sid}")


@session_app.command("recording")
def session_recording(
    sid: Annotated[str, typer.Argument(help="会话 id。")],
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="写入 cast 文件。")
    ] = None,
    url: Annotated[str | None, typer.Option("--url", help="服务器地址。")] = None,
) -> None:
    """下载会话录制（asciinema cast）。"""
    client = _api_client(url)
    try:
        data = client.download(f"/api/sessions/{sid}/recording")
    except client_mod.ApiError as exc:
        _fail(str(exc))
    if output is None:
        sys.stdout.buffer.write(data)
    else:
        output.write_bytes(data)
        console.print(f"[green]已写入[/] {output}")


@app.command()
def login(
    url: Annotated[str, typer.Argument(help="服务器地址，例如 http://127.0.0.1:7681")],
    username: Annotated[str, typer.Option("--username", "-u", help="用户名。")] = "admin",
    password: Annotated[str | None, typer.Option("--password", "-p", help="密码。")] = None,
    totp: Annotated[
        str | None,
        typer.Option("--totp", help="TOTP 一次性验证码（启用两步验证的账号必填）。"),
    ] = None,
) -> None:
    """登录服务器并缓存令牌，供后续命令使用。

    启用了两步验证的账号可用 ``--totp 123445`` 直接传码，或省略 ``--totp``
    在被要求时交互输入（也可用 ``WSCTL_TOTP`` 环境变量供脚本使用）。
    """
    if password is None:
        password = typer.prompt("密码", hide_input=True)
    code = totp or os.environ.get("WSCTL_TOTP") or None
    try:
        try:
            token = client_mod.login(url, username, password, totp=code)
        except client_mod.ApiError as exc:
            # A 2FA-enabled account rejects a missing/wrong code with a
            # code-specific message: that is the signal to prompt and retry
            # rather than to fail the whole login.
            if code is None and "验证码" in exc.detail:
                code = typer.prompt("一次性验证码")
                token = client_mod.login(url, username, password, totp=code)
            else:
                raise
    except client_mod.ApiError as exc:
        _fail(f"登录失败：{exc.detail}")
    client_mod.save_credentials(url, token)
    console.print(f"[green]已登录为[/] {username} @ {url}")


@app.command()
def logout() -> None:
    """清除缓存的服务器凭据。"""
    creds = client_mod.load_credentials()
    if creds.get("url") and creds.get("token"):
        with contextlib.suppress(client_mod.ApiError):
            client_mod.ApiClient(str(creds["url"]), str(creds["token"])).request(
                "POST", "/api/logout"
            )
    client_mod.clear_credentials()
    console.print("[green]已退出登录。[/]")


@app.command()
def connect(
    url: Annotated[str | None, typer.Argument(help="服务器地址，例如 http://host:7681")] = None,
    session: Annotated[
        str | None, typer.Option("--session", "-s", help="连接到已有会话 id。")
    ] = None,
    token: Annotated[
        str | None, typer.Option("--token", envvar="WSCTL_TOKEN", help="Bearer 令牌。")
    ] = None,
    no_reconnect: Annotated[
        bool, typer.Option("--no-reconnect", help="断线后不自动重连。")
    ] = False,
) -> None:
    """把当前终端连接到远程 wsctl 服务。"""
    try:
        connect_mod.run_connect(url, session, token, reconnect=not no_reconnect)
    except connect_mod.ConnectError as exc:
        _fail(str(exc))


if __name__ == "__main__":
    app()
