"""wsctl command-line entry point."""

from __future__ import annotations

import logging
import secrets
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from wsctl import __version__
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
def session_list() -> None:
    """List sessions (requires a running server)."""
    err_console.print("[yellow]not implemented yet[/] (planned for M2)")


@session_app.command("kill")
def session_kill(sid: Annotated[str, typer.Argument(help="Session id.")]) -> None:
    """Kill a session (requires a running server)."""
    _ = sid
    err_console.print("[yellow]not implemented yet[/] (planned for M2)")


@app.command()
def connect(url: Annotated[str, typer.Argument(help="Server URL, e.g. http://host:7681")]) -> None:
    """Attach a local terminal to a remote wsctl server."""
    _ = url
    err_console.print("[yellow]not implemented yet[/] (planned for M5)")


if __name__ == "__main__":
    app()
