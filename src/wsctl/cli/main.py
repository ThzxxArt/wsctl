"""wsctl command-line entry point."""

from __future__ import annotations

import contextlib
import logging
import secrets
import time
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
from wsctl.core import totp
from wsctl.core.config import Settings, load_settings
from wsctl.core.store import Store

app = typer.Typer(
    name="wsctl",
    help="A modern, Python-based web terminal server.",
    no_args_is_help=True,
    add_completion=False,
)
user_app = typer.Typer(name="user", help="Manage users.", no_args_is_help=True)
config_app = typer.Typer(name="config", help="Inspect configuration.", no_args_is_help=True)
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
) -> None:
    """Start the wsctl server."""
    overrides: dict[str, object] = {
        "host": host,
        "port": port,
        "ssl_cert": ssl_cert,
        "ssl_key": ssl_key,
    }
    if no_auth:
        overrides["auth_required"] = False
    settings = _settings_from(config, **overrides)
    if log_level:
        settings.log_level = log_level

    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from wsctl.server.app import create_app

    store = Store(settings.db_path)
    _ensure_admin(store, settings, admin_password)
    application = create_app(settings, store=store)

    import uvicorn

    scheme = "https" if settings.ssl_cert else "http"
    console.print(f"[bold green]wsctl {__version__}[/] serving on [cyan]{scheme}://{settings.host}:{settings.port}[/]")
    if not settings.auth_required:
        err_console.print("[bold yellow]warning:[/] authentication is disabled")

    uvicorn.run(
        application,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        ssl_certfile=str(settings.ssl_cert) if settings.ssl_cert else None,
        ssl_keyfile=str(settings.ssl_key) if settings.ssl_key else None,
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
def connect(url: Annotated[str, typer.Argument(help="Server URL, e.g. http://host:7681")]) -> None:
    """Attach a local terminal to a remote wsctl server."""
    _ = url
    err_console.print("[yellow]not implemented yet[/] (planned for M5)")


if __name__ == "__main__":
    app()
