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
    session_sliding_ttl: bool = False
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
    max_sessions_per_user: int = 0
    session_max_clients: int = 0
    session_memory_limit: int = 64 * 1024 * 1024
    client_max_bytes: int = 8 * 1024 * 1024
    input_rate_limit: int = 0
    input_rate_burst: int = 0
    # 0 is *not* "unlimited" here: Scrollback requires a positive budget and
    # raising ValueError from the session constructor took every attach down
    # with a bare 1000 close. Reject the value where it is read instead.
    scrollback_bytes: int = Field(DEFAULT_MAX_BYTES, ge=1)

    # How long another instance's lease may go unheard before its sessions are
    # considered abandoned (only relevant with SO_REUSEPORT / shared data_dir).
    instance_ttl: int = 30
    # Retention: 0 disables the corresponding cleanup.
    audit_retention_days: int = 30
    term_session_retention_days: int = 30
    recordings_retention_days: int = 0
    recordings_max_bytes: int = 0

    data_dir: Path = Field(default_factory=default_data_dir)
    config_path: Path = Field(default_factory=default_config_path)

    ssl_cert: Path | None = None
    ssl_key: Path | None = None
    reuse_port: bool = False

    file_root: Path | None = None
    file_max_upload: int = 100 * 1024 * 1024

    metrics_enabled: bool = True
    metrics_require_auth: bool = False
    log_level: str = "info"
    log_json: bool = False
    # Rotation applies to ``log_file`` using copy-and-truncate semantics, so an
    # already-open append-mode descriptor (what ``wsctl start`` gives its child)
    # keeps writing to the same file across a rotation.
    log_file: Path | None = None
    log_max_bytes: int = 10 * 1024 * 1024
    log_backup_count: int = 3

    auto_record: bool = False
    record_input: bool = False
    webhook_url: str | None = None

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


# How a reload reaches each setting. Three tiers, because "hot reloadable" was
# doing too much work: eight of these used to be labelled 热更新 while only
# applying to sessions/connections created *after* the edit, so a user who
# raised ``session_memory_limit`` believed it had taken effect and it had not.
#
#   HOT_IMMEDIATE   -- the very next request / connection / maintenance tick
#   HOT_NEW_OBJECTS -- sessions, recordings or sockets created from now on
#   RESTART_FIELDS  -- only a restart
#
# Every field is classified; ``tests/test_config.py`` fails if one is missing
# or appears twice, so a new setting cannot sneak in unlabelled.
HOT_IMMEDIATE = frozenset(
    {
        # Read on every request and every WebSocket handshake, so a reload
        # flips the boundary at once. Toggling authentication at runtime is a
        # security event and deserves a deliberate edit -- but labelling it
        # "needs restart" would be the same kind of lie this release removes,
        # because the server really does honour it immediately.
        "auth_required",
        "allowed_ips",
        "allowed_origins",
        "audit_retention_days",
        "cookie_secure",
        "file_max_upload",
        "file_root",
        "instance_ttl",
        "login_rate_limit",
        "login_rate_window",
        "max_sessions",
        "max_sessions_per_user",
        "metrics_enabled",
        "metrics_require_auth",
        "recordings_max_bytes",
        "recordings_retention_days",
        "security_headers",
        "session_sliding_ttl",
        "session_ttl",
        "term_session_retention_days",
        "tmux_preserve_on_shutdown",
        "trust_proxy",
    }
)

HOT_NEW_OBJECTS = frozenset(
    {
        "audit_input",
        "auto_record",
        "client_max_bytes",
        "default_backend",
        "default_cwd",
        "default_shell",
        "idle_timeout",
        "input_rate_burst",
        "input_rate_limit",
        "max_life",
        "record_input",
        "scrollback_bytes",
        "session_max_clients",
        "session_memory_limit",
        "totp_issuer",
    }
)

RESTART_FIELDS = frozenset(
    {
        "config_path",
        "data_dir",
        "host",
        "log_backup_count",
        "log_file",
        "log_json",
        "log_level",
        "log_max_bytes",
        "port",
        "reuse_port",
        "ssl_cert",
        "ssl_key",
        "webhook_url",
    }
)

#: Union of the two reloadable tiers: what ``reload_settings_file`` copies over.
HOT_FIELDS = HOT_IMMEDIATE | HOT_NEW_OBJECTS

#: Stable identifiers the API returns as ``kind``. The UI renders one label and
#: colour per tier; renaming one here is a breaking change for the config tab.
KIND_IMMEDIATE = "hot"
KIND_NEW_OBJECTS = "new"
KIND_RESTART = "restart"


def kind_of(key: str) -> str:
    """Which tier ``key`` belongs to (``"restart"`` for anything else)."""
    if key in HOT_IMMEDIATE:
        return KIND_IMMEDIATE
    if key in HOT_NEW_OBJECTS:
        return KIND_NEW_OBJECTS
    return KIND_RESTART


def reload_settings_file(settings: Settings) -> tuple[list[str], list[str]]:
    """Re-read the config file and apply hot-reloadable fields in place.

    Returns ``(changed, errors)``. ``errors`` is what makes a broken edit
    visible: previously a bad value was silently ignored, which looked exactly
    like "the config did not reload" and was impossible to diagnose.
    """
    path = settings.config_path
    if not path.is_file():
        return [], []
    import tomllib

    try:
        data = tomllib.loads(path.read_text("utf-8"))
    except OSError as exc:
        return [], [f"无法读取配置文件 {path}：{exc}"]
    except tomllib.TOMLDecodeError as exc:
        return [], [f"配置文件 TOML 语法错误：{exc}"]
    if not isinstance(data, dict):
        return [], ["配置文件顶层必须是键值表"]
    try:
        candidate = Settings(**data)
    except Exception as exc:
        return [], [f"配置项无效，已保持原值：{exc}"]
    changed: list[str] = []
    for key in sorted(HOT_FIELDS):
        # Environment variables take precedence over the file (see load order),
        # including explicitly-set empty values.
        if f"WSCTL_{key.upper()}" in os.environ:
            continue
        if key in data and getattr(settings, key) != getattr(candidate, key):
            setattr(settings, key, getattr(candidate, key))
            changed.append(key)
    return changed, []
