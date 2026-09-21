"""wsctl command-line entry point."""

from __future__ import annotations

import contextlib
import os
import secrets
import subprocess
import sys
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
    help="A modern, Python-based web terminal server.",
    no_args_is_help=True,
    add_completion=False,
)
user_app = typer.Typer(name="user", help="Manage users.", no_args_is_help=True)
config_app = typer.Typer(
    name="config", help="Inspect and manage configuration.", no_args_is_help=True
)
session_app = typer.Typer(name="session", help="Manage terminal sessions.", no_args_is_help=True)
app.add_typer(user_app)
app.add_typer(config_app)
app.add_typer(session_app)

console = Console()
err_console = Console(stderr=True)


def _fail(message: str) -> NoReturn:
    err_console.print(f"[red]{message}[/]")
    raise typer.Exit(code=1)


def _api_client(url: str | None) -> ApiClient:
    creds = load_credentials()
    base = url or (str(creds["url"]) if creds.get("url") else None)
    if not base:
        _fail("no server URL: pass --url or run 'wsctl login <url>' first")
    token = str(creds["token"]) if creds.get("token") else None
    return ApiClient(base, token)


def _settings_from(config: Path | None, **overrides: object) -> Settings:
    if config is not None:
        import os

        os.environ["WSCTL_CONFIG"] = str(config)
    return load_settings(**overrides)


@app.command()
def version() -> None:
    """Print the wsctl version."""
    console.print(f"wsctl {__version__}")


@app.command()
def serve(
    host: Annotated[str | None, typer.Option(help="Interface to bind.")] = None,
    port: Annotated[int | None, typer.Option(help="Port to listen on.")] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Config file path.")] = None,
    no_auth: Annotated[
        bool, typer.Option("--no-auth", help="Disable authentication (unsafe).")
    ] = False,
    admin_password: Annotated[
        str | None, typer.Option("--admin-password", help="Bootstrap admin password.")
    ] = None,
    ssl_cert: Annotated[Path | None, typer.Option(help="TLS certificate file.")] = None,
    ssl_key: Annotated[Path | None, typer.Option(help="TLS key file.")] = None,
    log_level: Annotated[str | None, typer.Option(help="Log level.")] = None,
    log_json: Annotated[bool, typer.Option("--log-json", help="Emit JSON logs.")] = False,
    new: Annotated[
        str | None, typer.Option("--new", help="Create a session running this command at startup.")
    ] = None,
    backend: Annotated[
        str | None, typer.Option("--backend", help="Default session backend: local or tmux.")
    ] = None,
    reuse_port: Annotated[
        bool,
        typer.Option("--reuse-port", help="Bind with SO_REUSEPORT for zero-downtime restarts."),
    ] = False,
) -> None:
    """Start the wsctl server."""
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
    store.user_create("admin", password, role="admin")
    if generated:
        err_console.print(
            "[bold yellow]Created admin user 'admin' with a generated password:[/]\n"
            f"    [bold]{password}[/]\n"
            "[dim]Change it with: wsctl user passwd admin[/]"
        )
    else:
        console.print("[green]Created admin user 'admin'.[/]")


@user_app.command("add")
def user_add(
    username: Annotated[str, typer.Argument(help="Username.")],
    password: Annotated[
        str | None, typer.Option("--password", "-p", help="Password (prompted if omitted).")
    ] = None,
    role: Annotated[str, typer.Option(help="Role: admin or user.")] = "user",
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Create a user."""
    settings = _settings_from(config)
    if password is None:
        password = typer.prompt("Password", hide_input=True, confirmation_prompt=True)
    store = Store(settings.db_path)
    try:
        if store.user_get(username) is not None:
            err_console.print(f"[red]user already exists: {username}[/]")
            raise typer.Exit(code=1)
        store.user_create(username, password, role=role)
    finally:
        store.close()
    console.print(f"[green]Created user[/] {username} ([cyan]{role}[/])")


@user_app.command("list")
def user_list(config: Annotated[Path | None, typer.Option("--config", "-c")] = None) -> None:
    """List users."""
    settings = _settings_from(config)
    store = Store(settings.db_path)
    try:
        users = store.user_list()
    finally:
        store.close()
    table = Table("id", "username", "role", "disabled")
    for u in users:
        table.add_row(str(u.id), u.username, u.role, "yes" if u.disabled else "no")
    console.print(table)


@user_app.command("del")
def user_del(
    username: Annotated[str, typer.Argument(help="Username.")],
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Delete a user."""
    settings = _settings_from(config)
    store = Store(settings.db_path)
    try:
        if not store.user_delete(username):
            err_console.print(f"[red]no such user: {username}[/]")
            raise typer.Exit(code=1)
    finally:
        store.close()
    console.print(f"[green]Deleted user[/] {username}")


@user_app.command("passwd")
def user_passwd(
    username: Annotated[str, typer.Argument(help="Username.")],
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Change a user's password."""
    settings = _settings_from(config)
    password = typer.prompt("New password", hide_input=True, confirmation_prompt=True)
    store = Store(settings.db_path)
    try:
        if not store.user_set_password(username, password):
            err_console.print(f"[red]no such user: {username}[/]")
            raise typer.Exit(code=1)
    finally:
        store.close()
    console.print(f"[green]Updated password for[/] {username}")


@user_app.command("role")
def user_role(
    username: Annotated[str, typer.Argument(help="Username.")],
    role: Annotated[str, typer.Argument(help="Role: admin or user.")],
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Set a user's role."""
    settings = _settings_from(config)
    store = Store(settings.db_path)
    try:
        if not store.user_set_role(username, role):
            err_console.print(f"[red]no such user: {username}[/]")
            raise typer.Exit(code=1)
    finally:
        store.close()
    console.print(f"[green]{username}[/] is now [cyan]{role}[/]")


@user_app.command("totp")
def user_totp(
    username: Annotated[str, typer.Argument(help="Username.")],
    disable: Annotated[bool, typer.Option("--disable", help="Disable TOTP.")] = False,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Enable or disable TOTP two-factor authentication for a user."""
    settings = _settings_from(config)
    store = Store(settings.db_path)
    try:
        if store.user_get(username) is None:
            _fail(f"no such user: {username}")
        if disable:
            store.user_clear_totp(username)
            console.print(f"[green]TOTP disabled[/] for {username}")
            return
        secret = totp.generate_secret()
        uri = totp.provisioning_uri(secret, username, issuer=settings.totp_issuer)
        console.print(f"Secret: [bold]{secret}[/]")
        console.print(f"otpauth URI: {uri}")
        console.print("[dim]Scan the URI in your authenticator app, then confirm.[/]")
        code = typer.prompt("One-time code")
        if not totp.verify(secret, code):
            _fail("code did not verify; TOTP was not enabled")
        store.user_set_totp(username, secret)
        console.print(f"[green]TOTP enabled[/] for {username}")
    finally:
        store.close()


@app.command()
def audit(
    limit: Annotated[int, typer.Option("--limit", "-n", help="Number of entries.")] = 50,
    url: Annotated[str | None, typer.Option("--url", help="Server URL.")] = None,
) -> None:
    """Show the server audit log (admin only)."""
    client = _api_client(url)
    try:
        rows = client.request("GET", f"/api/audit?limit={limit}")
    except ApiError as exc:
        _fail(str(exc))
    table = Table("time", "event", "user", "session", "ip", "payload")
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
    """Show effective configuration."""
    settings = _settings_from(config)
    table = Table("key", "value")
    for key, value in settings.model_dump().items():
        table.add_row(key, str(value))
    console.print(table)
    console.print(f"[dim]config file: {settings.config_path}[/]")


@config_app.command("path")
def config_path(config: Annotated[Path | None, typer.Option("--config", "-c")] = None) -> None:
    """Print the configuration file path."""
    settings = _settings_from(config)
    console.print(str(settings.config_path))


CONFIG_TEMPLATE = """# wsctl configuration
host = "127.0.0.1"
port = 7681

# auth_required = true
# session_ttl = 43200
# cookie_secure = false
# trust_proxy = false

# allowed_ips = ["10.0.0.0/8"]
# login_rate_limit = 10
# login_rate_window = 300
# audit_input = false

# idle_timeout = 3600
# max_life = 86400
# max_sessions = 64
# session_max_clients = 0
# input_rate_limit = 0
# input_rate_burst = 0
# scrollback_bytes = 4194304

# file_root = "/home/me"
# file_max_upload = 104857600

# metrics_enabled = true
# log_level = "info"
# log_json = false

# ssl_cert = "/etc/wsctl/cert.pem"
# ssl_key = "/etc/wsctl/key.pem"
"""


@config_app.command("edit")
def config_edit(config: Annotated[Path | None, typer.Option("--config", "-c")] = None) -> None:
    """Create (if needed) and open the configuration file in your editor."""
    settings = _settings_from(config)
    path = settings.config_path
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        console.print(f"[green]Created[/] {path}")
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    try:
        subprocess.call([editor, str(path)])
    except OSError as exc:
        _fail(f"cannot launch editor '{editor}': {exc}")


@config_app.command("set")
def config_set(
    key: Annotated[str, typer.Argument(help="Configuration key.")],
    value: Annotated[
        str, typer.Argument(help='TOML value, e.g. 8080, "text", true, ["a", "b"]')
    ],
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Set a configuration value in the config file."""
    settings = _settings_from(config)
    try:
        tomllib.loads(f"__value__ = {value}")
    except tomllib.TOMLDecodeError:
        _fail('value must be valid TOML, e.g. 8080, "text", true, ["a", "b"]')

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
    console.print(f"[green]Set[/] {key} = {value}  [dim]({path})[/]")


@config_app.command("reload")
def config_reload(
    url: Annotated[str | None, typer.Option("--url", help="Server URL.")] = None,
) -> None:
    """Ask a running server to reload its configuration (admin)."""
    client = _api_client(url)
    try:
        info = client.request("POST", "/api/config/reload")
    except ApiError as exc:
        _fail(str(exc))
    changed = info.get("changed") or []
    console.print("[green]Reloaded[/] config; changed: " + (", ".join(changed) or "none"))


@session_app.command("list")
def session_list(
    url: Annotated[str | None, typer.Option("--url", help="Server URL.")] = None,
) -> None:
    """List sessions held by a running server."""
    client = _api_client(url)
    try:
        sessions = client.request("GET", "/api/sessions")
    except ApiError as exc:
        _fail(str(exc))
    table = Table("id", "name", "pid", "clients", "owner")
    for s in sessions:
        table.add_row(
            str(s["id"]), str(s["name"]), str(s["pid"]), str(s["clients"]), str(s["owner_id"])
        )
    console.print(table)


@session_app.command("kill")
def session_kill(
    sid: Annotated[str, typer.Argument(help="Session id.")],
    url: Annotated[str | None, typer.Option("--url", help="Server URL.")] = None,
) -> None:
    """Kill a session on a running server."""
    client = _api_client(url)
    try:
        client.request("DELETE", f"/api/sessions/{sid}")
    except ApiError as exc:
        _fail(str(exc))
    console.print(f"[green]Killed session[/] {sid}")


@session_app.command("new")
def session_new(
    name: Annotated[str | None, typer.Option("--name", "-n", help="Session name.")] = None,
    command: Annotated[
        str | None, typer.Option("--command", "-x", help="Command to run instead of a shell.")
    ] = None,
    cwd: Annotated[str | None, typer.Option("--cwd", help="Working directory.")] = None,
    backend: Annotated[
        str | None, typer.Option("--backend", help="Backend: local, tmux or ssh.")
    ] = None,
    ssh_host: Annotated[
        str | None, typer.Option("--ssh", help="SSH target host (implies --backend ssh).")
    ] = None,
    ssh_user: Annotated[str | None, typer.Option("--ssh-user", help="SSH user.")] = None,
    ssh_port: Annotated[int | None, typer.Option("--ssh-port", help="SSH port.")] = None,
    ssh_identity: Annotated[
        str | None, typer.Option("--ssh-identity", help="SSH identity file.")
    ] = None,
    ssh_option: Annotated[
        list[str] | None, typer.Option("--ssh-option", help="Extra ssh -o option (repeatable).")
    ] = None,
    url: Annotated[str | None, typer.Option("--url", help="Server URL.")] = None,
) -> None:
    """Create a session on a running server."""
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
    console.print(f"[green]Created session[/] {info['id']} ([cyan]{info['name']}[/])")


@session_app.command("attach")
def session_attach(
    sid: Annotated[str, typer.Argument(help="Session id.")],
    url: Annotated[str | None, typer.Option("--url", help="Server URL.")] = None,
    token: Annotated[
        str | None, typer.Option("--token", envvar="WSCTL_TOKEN", help="Bearer token.")
    ] = None,
) -> None:
    """Attach this terminal to an existing session."""
    try:
        run_connect(url, sid, token)
    except ConnectError as exc:
        _fail(str(exc))


@session_app.command("record")
def session_record(
    sid: Annotated[str, typer.Argument(help="Session id.")],
    record_input: Annotated[
        bool, typer.Option("--input", help="Also record typed input.")
    ] = False,
    url: Annotated[str | None, typer.Option("--url", help="Server URL.")] = None,
) -> None:
    """Start recording a session to an asciinema cast file."""
    client = _api_client(url)
    try:
        info = client.request(
            "POST", f"/api/sessions/{sid}/recording/start", {"record_input": record_input}
        )
    except ApiError as exc:
        _fail(str(exc))
    console.print(f"[green]Recording[/] {sid} -> {info['path']}")


@session_app.command("record-stop")
def session_record_stop(
    sid: Annotated[str, typer.Argument(help="Session id.")],
    url: Annotated[str | None, typer.Option("--url", help="Server URL.")] = None,
) -> None:
    """Stop recording a session."""
    client = _api_client(url)
    try:
        client.request("POST", f"/api/sessions/{sid}/recording/stop")
    except ApiError as exc:
        _fail(str(exc))
    console.print(f"[green]Stopped recording[/] {sid}")


@session_app.command("recording")
def session_recording(
    sid: Annotated[str, typer.Argument(help="Session id.")],
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write the cast here.")
    ] = None,
    url: Annotated[str | None, typer.Option("--url", help="Server URL.")] = None,
) -> None:
    """Download a session recording (asciinema cast)."""
    client = _api_client(url)
    try:
        data = client.download(f"/api/sessions/{sid}/recording")
    except ApiError as exc:
        _fail(str(exc))
    if output is None:
        sys.stdout.buffer.write(data)
    else:
        output.write_bytes(data)
        console.print(f"[green]Wrote[/] {output}")


@app.command()
def login(
    url: Annotated[str, typer.Argument(help="Server URL, e.g. http://127.0.0.1:7681")],
    username: Annotated[str, typer.Option("--username", "-u", help="Username.")] = "admin",
    password: Annotated[str | None, typer.Option("--password", "-p", help="Password.")] = None,
) -> None:
    """Authenticate against a server and cache the token for later commands."""
    if password is None:
        password = typer.prompt("Password", hide_input=True)
    try:
        token = api_login(url, username, password)
    except ApiError as exc:
        _fail(f"login failed: {exc}")
    save_credentials(url, token)
    console.print(f"[green]Logged in as[/] {username} @ {url}")


@app.command()
def logout() -> None:
    """Remove cached server credentials."""
    creds = load_credentials()
    if creds.get("url") and creds.get("token"):
        with contextlib.suppress(ApiError):
            ApiClient(str(creds["url"]), str(creds["token"])).request("POST", "/api/logout")
    clear_credentials()
    console.print("[green]Logged out.[/]")


@app.command()
def connect(
    url: Annotated[str | None, typer.Argument(help="Server URL, e.g. http://host:7681")] = None,
    session: Annotated[
        str | None, typer.Option("--session", "-s", help="Attach to an existing session id.")
    ] = None,
    token: Annotated[
        str | None, typer.Option("--token", envvar="WSCTL_TOKEN", help="Bearer token.")
    ] = None,
) -> None:
    """Attach this terminal to a remote wsctl server."""
    try:
        run_connect(url, session, token)
    except ConnectError as exc:
        _fail(str(exc))


if __name__ == "__main__":
    app()
