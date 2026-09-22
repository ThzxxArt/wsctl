"""wsctl command-line entry point."""

from __future__ import annotations

import contextlib
import difflib
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
import tomllib
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console
from rich.table import Table

from wsctl import __version__
from wsctl.cli.client import (
    ApiClient,
    ApiError,
    clear_credentials,
    load_credentials,
    save_credentials,
)
from wsctl.cli.client import (
    login as api_login,
)
from wsctl.cli.connect import ConnectError, run_connect
from wsctl.core import totp
from wsctl.core.config import Settings, load_settings
from wsctl.core.logging import configure_logging
from wsctl.core.store import Store

app = typer.Typer(
    name="wsctl",
    help="单机部署的 Web 在线终端：多会话、多用户、审计、分享、录制与可观测。",
    no_args_is_help=True,
    add_completion=True,
)
user_app = typer.Typer(name="user", help="管理用户。", no_args_is_help=True)
config_app = typer.Typer(name="config", help="查看与管理配置。", no_args_is_help=True)
session_app = typer.Typer(name="session", help="管理终端会话。", no_args_is_help=True)
app.add_typer(user_app)
app.add_typer(config_app)
app.add_typer(session_app)

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


def _api_client(url: str | None) -> ApiClient:
    creds = load_credentials()
    base = url or (str(creds["url"]) if creds.get("url") else None)
    if not base:
        _fail("缺少服务器地址：请传入 --url，或先运行 'wsctl login <url>'")
    token = str(creds["token"]) if creds.get("token") else None
    return ApiClient(base, token)


def _settings_from(config: Path | None, **overrides: object) -> Settings:
    if config is not None:
        import os

        os.environ["WSCTL_CONFIG"] = str(config)
    return load_settings(**overrides)


@app.command()
def version() -> None:
    """显示 wsctl 版本。"""
    console.print(f"wsctl {__version__}")


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
        store = Store(settings.db_path)
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

    if url or load_credentials().get("url"):
        try:
            client = _api_client(url)
            health = client.request("GET", "/healthz", auth=False)
            add("服务器", f"{client.base_url}（版本 {health.get('version')}）", ok)
        except ApiError as exc:
            add("服务器", f"（{exc}）", bad)
    else:
        add("服务器", "未配置（可用 --url 检查）", "[dim]-[/]")

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
) -> None:
    """把数据库与录制打包备份到 tar.gz。"""
    settings = _settings_from(config)
    output = output.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as tar:
        if settings.db_path.is_file():
            tar.add(settings.db_path, arcname="wsctl.db")
        if settings.recordings_dir.is_dir():
            tar.add(settings.recordings_dir, arcname="recordings")
    console.print(f"[green]已备份到[/] {output}")


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
) -> None:
    """启动 wsctl 服务。"""
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

    configure_logging(settings.log_level, json_output=settings.log_json)

    from wsctl.server.app import create_app

    store = Store(settings.db_path)
    _ensure_admin(store, settings, admin_password)
    application = create_app(settings, store=store, startup_command=new)

    import uvicorn

    scheme = "https" if settings.ssl_cert else "http"
    console.print(f"[bold green]wsctl {__version__}[/] serving on [cyan]{scheme}://{settings.host}:{settings.port}[/]")
    if not settings.auth_required:
        err_console.print("[bold yellow]warning:[/] authentication is disabled")

    ssl_certfile = str(settings.ssl_cert) if settings.ssl_cert else None
    ssl_keyfile = str(settings.ssl_key) if settings.ssl_key else None

    if settings.reuse_port:
        from wsctl.core.net import make_reuse_socket

        uv_config = uvicorn.Config(
            application,
            log_level=settings.log_level,
            ssl_certfile=ssl_certfile,
            ssl_keyfile=ssl_keyfile,
        )
        server = uvicorn.Server(uv_config)
        sock = make_reuse_socket(settings.host, settings.port)
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


def _ensure_admin(store: Store, settings: Settings, password: str | None) -> None:
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
) -> None:
    """创建一个用户。"""
    settings = _settings_from(config)
    if password is None:
        password = typer.prompt("密码", hide_input=True, confirmation_prompt=True)
    store = Store(settings.db_path)
    try:
        if store.user_get(username) is not None:
            err_console.print(f"[red]用户已存在：{username}[/]")
            raise typer.Exit(code=1)
        store.user_create(username, password, role=role)
    finally:
        store.close()
    console.print(f"[green]已创建用户[/] {username}（[cyan]{role}[/]）")


@user_app.command("list")
def user_list(config: Annotated[Path | None, typer.Option("--config", "-c")] = None) -> None:
    """列出所有用户。"""
    settings = _settings_from(config)
    store = Store(settings.db_path)
    try:
        users = store.user_list()
    finally:
        store.close()
    table = Table("ID", "用户名", "角色", "已禁用")
    for u in users:
        table.add_row(str(u.id), u.username, u.role, "是" if u.disabled else "否")
    console.print(table)


@user_app.command("del")
def user_del(
    username: Annotated[str, typer.Argument(help="用户名。")],
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """删除一个用户。"""
    settings = _settings_from(config)
    store = Store(settings.db_path)
    try:
        if not store.user_delete(username):
            err_console.print(f"[red]没有此用户：{username}[/]")
            raise typer.Exit(code=1)
    finally:
        store.close()
    console.print(f"[green]已删除用户[/] {username}")


@user_app.command("passwd")
def user_passwd(
    username: Annotated[str, typer.Argument(help="用户名。")],
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """修改用户密码。"""
    settings = _settings_from(config)
    password = typer.prompt("新密码", hide_input=True, confirmation_prompt=True)
    store = Store(settings.db_path)
    try:
        if not store.user_set_password(username, password):
            err_console.print(f"[red]没有此用户：{username}[/]")
            raise typer.Exit(code=1)
    finally:
        store.close()
    console.print(f"[green]已更新密码：[/] {username}")


@user_app.command("role")
def user_role(
    username: Annotated[str, typer.Argument(help="用户名。")],
    role: Annotated[str, typer.Argument(help="角色：admin 或 user。")],
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """设置用户角色。"""
    settings = _settings_from(config)
    store = Store(settings.db_path)
    try:
        if not store.user_set_role(username, role):
            err_console.print(f"[red]没有此用户：{username}[/]")
            raise typer.Exit(code=1)
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
    store = Store(settings.db_path)
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
    """禁用一个用户（其登录态立即失效）。"""
    settings = _settings_from(config)
    store = Store(settings.db_path)
    try:
        if not store.user_set_disabled(username, True):
            _fail(f"没有此用户：{username}")
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
    store = Store(settings.db_path)
    try:
        if not store.user_set_disabled(username, False):
            _fail(f"没有此用户：{username}")
    finally:
        store.close()
    console.print(f"[green]已启用[/] {username}")


@app.command()
def audit(
    limit: Annotated[int, typer.Option("--limit", "-n", help="条目数量。")] = 50,
    url: Annotated[str | None, typer.Option("--url", help="服务器地址。")] = None,
) -> None:
    """查看服务端审计日志（仅管理员）。"""
    client = _api_client(url)
    try:
        rows = client.request("GET", f"/api/audit?limit={limit}")
    except ApiError as exc:
        _fail(str(exc))
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
def config_show(config: Annotated[Path | None, typer.Option("--config", "-c")] = None) -> None:
    """显示生效中的配置。"""
    settings = _settings_from(config)
    table = Table("配置项", "值")
    for key, value in settings.model_dump().items():
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
) -> None:
    """打印某个配置项的生效值。"""
    settings = _settings_from(config)
    if key not in Settings.model_fields:
        suggestion = difflib.get_close_matches(key, set(Settings.model_fields), n=1)
        hint = f"，是否想用 “{suggestion[0]}”？" if suggestion else ""
        _fail(f"未知的配置项：{key}{hint}")
    console.print(str(getattr(settings, key)))


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
        Settings(**data)
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


@config_app.command("set")
def config_set(
    key: Annotated[str, typer.Argument(help="配置项名称。")],
    value: Annotated[
        str, typer.Argument(help='TOML 值，例如 8080、"文本"、true、["a", "b"]')
    ],
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """在配置文件中设置一个值。"""
    settings = _settings_from(config)
    known = set(Settings.model_fields)
    if key not in known:
        suggestion = difflib.get_close_matches(key, known, n=1)
        hint = f"，是否想用 “{suggestion[0]}”？" if suggestion else ""
        _fail(f"未知的配置项：{key}{hint}")
    try:
        tomllib.loads(f"__value__ = {value}")
    except tomllib.TOMLDecodeError:
        _fail('值必须是合法 TOML，例如 8080、"文本"、true、["a", "b"]')

    path = settings.config_path
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    replaced = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if "=" in stripped and stripped.split("=", 1)[0].strip() == key:
            lines[index] = f"{key} = {value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key} = {value}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    console.print(f"[green]已设置[/] {key} = {value}  [dim]（{path}）[/]")


@config_app.command("reload")
def config_reload(
    url: Annotated[str | None, typer.Option("--url", help="服务器地址。")] = None,
) -> None:
    """请求运行中的服务重载配置（仅管理员）。"""
    client = _api_client(url)
    try:
        info = client.request("POST", "/api/config/reload")
    except ApiError as exc:
        _fail(str(exc))
    changed = info.get("changed") or []
    console.print("[green]已重载[/] 配置；变更项：" + ("、".join(changed) or "无"))


@session_app.command("list")
def session_list(
    url: Annotated[str | None, typer.Option("--url", help="服务器地址。")] = None,
) -> None:
    """列出运行中服务的会话。"""
    client = _api_client(url)
    try:
        sessions = client.request("GET", "/api/sessions")
    except ApiError as exc:
        _fail(str(exc))
    table = Table("id", "名称", "PID", "连接数", "所有者")
    for s in sessions:
        table.add_row(
            str(s["id"]), str(s["name"]), str(s["pid"]), str(s["clients"]), str(s["owner_id"])
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
    except ApiError as exc:
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
) -> None:
    """在运行中的服务上创建会话。"""
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
    except ApiError as exc:
        _fail(str(exc))
    console.print(f"[green]已创建会话[/] {info['id']}（[cyan]{info['name']}[/]）")


@session_app.command("rename")
def session_rename(
    sid: Annotated[str, typer.Argument(help="会话 id。")],
    name: Annotated[str, typer.Argument(help="新名称。")],
    url: Annotated[str | None, typer.Option("--url", help="服务器地址。")] = None,
) -> None:
    """重命名一个会话。"""
    client = _api_client(url)
    try:
        info = client.request("PATCH", f"/api/sessions/{sid}", {"name": name})
    except ApiError as exc:
        _fail(str(exc))
    console.print(f"[green]已重命名为[/] {info['name']}")


@session_app.command("attach")
def session_attach(
    sid: Annotated[str, typer.Argument(help="会话 id。")],
    url: Annotated[str | None, typer.Option("--url", help="服务器地址。")] = None,
    token: Annotated[
        str | None, typer.Option("--token", envvar="WSCTL_TOKEN", help="Bearer 令牌。")
    ] = None,
) -> None:
    """把当前终端连接到已有会话。"""
    try:
        run_connect(url, sid, token)
    except ConnectError as exc:
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
    except ApiError as exc:
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
    except ApiError as exc:
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
    except ApiError as exc:
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
) -> None:
    """登录服务器并缓存令牌，供后续命令使用。"""
    if password is None:
        password = typer.prompt("密码", hide_input=True)
    try:
        token = api_login(url, username, password)
    except ApiError as exc:
        _fail(f"登录失败：{exc}")
    save_credentials(url, token)
    console.print(f"[green]已登录为[/] {username} @ {url}")


@app.command()
def logout() -> None:
    """清除缓存的服务器凭据。"""
    creds = load_credentials()
    if creds.get("url") and creds.get("token"):
        with contextlib.suppress(ApiError):
            ApiClient(str(creds["url"]), str(creds["token"])).request("POST", "/api/logout")
    clear_credentials()
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
) -> None:
    """把当前终端连接到远程 wsctl 服务。"""
    try:
        run_connect(url, session, token)
    except ConnectError as exc:
        _fail(str(exc))


if __name__ == "__main__":
    app()
