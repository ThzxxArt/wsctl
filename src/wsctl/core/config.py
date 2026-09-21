"""Configuration model and loading.

Precedence (highest first): explicit init args → ``WSCTL_*`` environment
variables → TOML config file (``~/.config/wsctl/config.toml``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from .scrollback import DEFAULT_MAX_BYTES

try:  # pragma: no cover - depends on pydantic-settings version
    from pydantic_settings import TomlConfigSettingsSource
except ImportError:  # pragma: no cover
    TomlConfigSettingsSource = None  # type: ignore[assignment,misc]

APP_NAME = "wsctl"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7681


def default_config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / APP_NAME


def default_config_path() -> Path:
    override = os.environ.get("WSCTL_CONFIG")
    if override:
        return Path(override).expanduser()
    return default_config_dir() / "config.toml"


def default_data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / APP_NAME


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WSCTL_",
        extra="ignore",
        validate_default=True,
    )

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    auth_required: bool = True
    session_ttl: int = 12 * 3600
    cookie_secure: bool = False
    trust_proxy: bool = False

    allowed_ips: list[str] = Field(default_factory=list)
    security_headers: bool = True
    login_rate_limit: int = 10
    login_rate_window: int = 300
    audit_input: bool = False
    totp_issuer: str = APP_NAME

    default_shell: str | None = None
    default_cwd: str | None = None
    default_backend: str = "local"
    tmux_preserve_on_shutdown: bool = True
    allowed_origins: list[str] = Field(default_factory=list)

    idle_timeout: float | None = None
    max_life: float | None = None
    max_sessions: int = 64
    session_max_clients: int = 0
    input_rate_limit: int = 0
    input_rate_burst: int = 0
    scrollback_bytes: int = DEFAULT_MAX_BYTES

    data_dir: Path = Field(default_factory=default_data_dir)
    config_path: Path = Field(default_factory=default_config_path)

    ssl_cert: Path | None = None
    ssl_key: Path | None = None

    file_root: Path | None = None
    file_max_upload: int = 100 * 1024 * 1024

    metrics_enabled: bool = True
    log_level: str = "info"
    log_json: bool = False

    auto_record: bool = False
    record_input: bool = False

    @property
    def db_path(self) -> Path:
        return self.data_dir / "wsctl.db"

    @property
    def files_root(self) -> Path:
        """Root directory exposed by the web file panel."""
        return (self.file_root or Path.home()).expanduser().resolve()

    @property
    def recordings_dir(self) -> Path:
        return self.data_dir / "recordings"

    @property
    def shell(self) -> str:
        if self.default_shell:
            return self.default_shell
        return os.environ.get("SHELL") or ("cmd.exe" if sys.platform == "win32" else "/bin/bash")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings, dotenv_settings]
        path = default_config_path()
        if TomlConfigSettingsSource is not None and path.is_file():
            sources.append(TomlConfigSettingsSource(settings_cls, toml_file=path))
        sources.append(file_secret_settings)
        return tuple(sources)


def load_settings(**overrides: Any) -> Settings:
    """Load settings, allowing CLI overrides on top of the normal precedence."""
    filtered: dict[str, Any] = {k: v for k, v in overrides.items() if v is not None}
    return Settings(**filtered)
