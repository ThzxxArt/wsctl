"""Configuration model and loading.

Precedence (highest first): explicit init args → ``WSCTL_*`` environment
variables → TOML config file (``~/.config/wsctl/config.toml``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from pydantic import Field, PrivateAttr, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from . import config_v2
from .scrollback import DEFAULT_MAX_BYTES

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
    #: Field names supplied by ``load_settings(**overrides)`` (CLI flags).
    #: A reload must preserve these even when the file omits them; every other
    #: absent key returns to its model default.
    _init_keys: frozenset[str] = PrivateAttr(default_factory=frozenset)

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
    #: Only these peers may speak ``X-Forwarded-For``. Empty means "loopback
    #: only", which is the shape of a reverse proxy on the same machine.
    #: Enabling ``trust_proxy`` without a real proxy lets any *local* process
    #: claim any client address -- so the hop that is allowed to append the
    #: header is a decision that has to be written down, not inferred.
    #:
    #: Validated at load: a typo (``10.0.0.0/33``) used to be dropped silently
    #: by the header parser, so the proxy stopped being trusted, every user
    #: shared the proxy's address as their rate-limit key, and the audit trail
    #: named the proxy for everyone -- while ``config validate`` said "valid".
    trusted_proxies: list[str] = Field(default_factory=list)

    @field_validator("trusted_proxies")
    @classmethod
    def _check_trusted_proxies(cls, value: list[str]) -> list[str]:
        """Refuse a malformed CIDR instead of silently distrusting the proxy."""
        import ipaddress

        cleaned: list[str] = []
        for entry in value:
            text = str(entry).strip()
            if not text:
                continue
            try:
                ipaddress.ip_network(text, strict=False)
            except ValueError as exc:
                raise ValueError(
                    f"trusted_proxies 里的 {text!r} 不是合法的 IP/CIDR"
                ) from exc
            cleaned.append(text)
        return cleaned

    allowed_ips: list[str] = Field(default_factory=list)
    security_headers: bool = True
    #: ``0`` disables throttling on purpose. A negative value is not "more
    #: off": it silently disabled the limiter too, because ``limit <= 0`` is
    #: the off switch. Refuse it instead of pretending it means something.
    login_rate_limit: int = Field(default=10, ge=0)
    login_rate_window: int = Field(default=300, gt=0)
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
    #: Bounds on *unauthenticated* WebSocket sockets, checked before the
    #: handshake frame is read. Without them one peer could hold an unlimited
    #: number of sockets open by sending nothing at all. ``0`` = unlimited;
    #: a negative number is a typo that would have meant "unlimited" silently.
    ws_max_connections: int = Field(default=512, ge=0)
    ws_max_connections_per_ip: int = Field(default=64, ge=0)
    input_rate_limit: int = 0
    input_rate_burst: int = 0
    # 0 is *not* "unlimited" here: Scrollback requires a positive budget and
    # raising ValueError from the session constructor took every attach down
    # with a bare 1000 close. Reject the value where it is read instead.
    scrollback_bytes: int = Field(DEFAULT_MAX_BYTES, ge=1)
    #: Screen snapshot strategy for the v2 protocol. ``auto`` maintains a
    #: shadow terminal only once an alternate screen is entered; ``always``
    #: pays for every session; ``off`` disables the capability.
    snapshot_mode: str = Field(default="auto", pattern="^(auto|always|off)$")
    #: Parse OSC 133/633 and publish command blocks to the UI.
    shell_integration: bool = True

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

    # -- 0.2.0 platform ---------------------------------------------------
    #: Empty means "SQLite at ``data_dir/wsctl.db``". Kept so a future
    #: milestone can accept a PostgreSQL URL without a config migration; 0.2.0
    #: rejects every non-empty value because the auth/session kernel is still
    #: SQLite-dialect.
    database_url: str = ""
    database_echo: bool = False

    @field_validator("database_url")
    @classmethod
    def _check_database_url(cls, value: str) -> str:
        """Only SQLite is a supported kernel in 0.2.0.

        The v2 repository is SQLAlchemy, but the auth/session/audit kernel
        still speaks SQLite's dialect. Accepting a PostgreSQL URL here would
        split one deployment across two databases -- the worst of both. The
        setting is therefore validated loudly instead of failing later with a
        missing table. PostgreSQL is a 0.3 milestone.
        """
        url = (value or "").strip()
        if url:
            raise ValueError(
                "0.2.0 仅使用 data_dir/wsctl.db；database_url 预留给 0.3 的 PostgreSQL 支持"
            )
        return url
    #: OpenTelemetry is opt-in: an endpoint is required before spans leave
    #: the process, and the default sampler is parent-based.
    otel_enabled: bool = False
    otel_endpoint: str | None = None
    otel_sample_ratio: float = Field(default=0.1, ge=0.0, le=1.0)
    #: Web Push is opt-in and requires VAPID keys. Disabled by default: a
    #: terminal server must not contact a push service unless asked.
    web_push_enabled: bool = False
    web_push_vapid_public: str = ""
    web_push_vapid_private: str = ""
    web_push_subject: str = "mailto:admin@localhost"

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
        sources: list[PydanticBaseSettingsSource] = [
            init_settings,
            env_settings,
            dotenv_settings,
            _SectionEnvSource(settings_cls),
        ]
        path = default_config_path()
        if path.is_file():
            sources.append(_SectionedTomlSource(settings_cls, path))
        sources.append(file_secret_settings)
        return tuple(sources)


class _SectionedTomlSource(PydanticBaseSettingsSource):
    """Load a v1 flat or v2 sectioned TOML file as flat settings values."""

    def __init__(self, settings_cls: type[BaseSettings], path: Path) -> None:
        super().__init__(settings_cls)
        self.path = path

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return config_v2.read_flat(self.path)


class _SectionEnvSource(PydanticBaseSettingsSource):
    """Support ``WSCTL_SERVER__PORT`` in addition to ``WSCTL_PORT``.

    The double underscore is the standard section delimiter. The flat spelling
    stays authoritative: an operator upgrading from 0.1.x must not have their
    environment variables silently stop working.
    """

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        prefix = "WSCTL_"
        for name, raw in os.environ.items():
            if not name.startswith(prefix) or "__" not in name:
                continue
            section, _, key = name[len(prefix):].partition("__")
            if section.lower() not in config_v2.SECTIONS or not key:
                continue
            canonical = config_v2.canonical_key(section.lower(), key.lower())
            values[canonical] = config_v2.coerce_value(canonical, raw)
        return values


def load_settings(**overrides: Any) -> Settings:
    """Load settings, allowing CLI overrides on top of the normal precedence."""
    filtered: dict[str, Any] = {k: v for k, v in overrides.items() if v is not None}
    settings = Settings(**filtered)
    settings._init_keys = frozenset(filtered)
    return settings


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
        "trusted_proxies",
        "web_push_enabled",
        "web_push_subject",
        "web_push_vapid_private",
        "web_push_vapid_public",
        "ws_max_connections",
        "ws_max_connections_per_ip",
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
        "shell_integration",
        "snapshot_mode",
        "totp_issuer",
    }
)

RESTART_FIELDS = frozenset(
    {
        "config_path",
        "data_dir",
        "database_url",
        # The engine is created once; ``echo`` cannot be changed on a live one.
        "database_echo",
        "host",
        "log_backup_count",
        "log_file",
        "log_json",
        "log_level",
        "log_max_bytes",
        "otel_enabled",
        "otel_endpoint",
        "otel_sample_ratio",
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
    try:
        data = config_v2.read_flat(path)
    except config_v2.ConfigV2Error as exc:
        return [], [str(exc)]
    try:
        # Build from the *current* effective values plus the file, not from a
        # fresh ``Settings(**data)``. A fresh settings object re-runs the
        # sources (environment and the default config path), which silently
        # replaced CLI init overrides such as ``--no-auth`` and could let a
        # stale ``~/.config/wsctl/config.toml`` turn authentication off.
        merged: dict[str, Any] = dict(data)
        for key in Settings.model_fields:
            if key in merged:
                continue
            if key in settings._init_keys:
                # An explicit CLI flag outranks the file and survives a reload.
                merged[key] = getattr(settings, key)
            # Every other absent key is intentionally omitted, so model_validate
            # restores its default (deleting a security switch must not be a
            # silent no-op).
        candidate = Settings.model_validate(merged)
    except Exception as exc:
        return [], [f"配置项无效，已保持原值：{exc}"]
    changed: list[str] = []
    for key in sorted(HOT_FIELDS):
        # Environment variables take precedence over the file (see load order),
        # including explicitly-set empty values.
        if f"WSCTL_{key.upper()}" in os.environ:
            continue
        # Compare unconditionally: deleting a line must mean "back to the
        # default", not "keep whatever this process started with". The old
        # ``key in data`` guard made removing ``trust_proxy = true`` a no-op
        # reported as "no changes" -- a fail-open silence for a security knob.
        # ``candidate`` was built from the file alone, so an absent key is
        # already its model default.
        current = getattr(settings, key)
        wanted = getattr(candidate, key)
        if current != wanted:
            setattr(settings, key, wanted)
            changed.append(key)
    return changed, []
