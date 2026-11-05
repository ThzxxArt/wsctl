"""Sectioned configuration v2: schema, flattening and migration.

0.1.x used a flat TOML document. That was easy to parse and impossible to
group: the web editor showed 50 keys in one alphabetical list, and a new
setting had to be added to the model, the reload tiers, the editor, the README
and the tests by hand. 0.2.0 makes the file a document with sections:

.. code-block:: toml

    [server]
    host = "127.0.0.1"
    port = 7681

    [terminal]
    scrollback_bytes = "8MiB"

Every flat ``Settings`` field still exists internally -- the rest of the
codebase addresses ``settings.port`` -- but the file, the API and the editor
speak sections. The mapping is declared exactly once in :data:`SECTION_MAP`;
a field missing from it is a test failure, which is what keeps "add a setting"
from becoming a five-file scavenger hunt again.

Values may be written in human units (``"12h"``, ``"8MiB"``) and are
normalised to the integer seconds/bytes the model stores. The legacy flat
format is read transparently and migrated by :func:`migrate_file`.
"""

from __future__ import annotations

import contextlib
import re
import shutil
import tomllib
from pathlib import Path
from typing import Any, get_args, get_origin

#: Fixed section order. Rendering is deterministic, so a migration produces a
#: readable diff instead of reshuffling every line.
SECTIONS = (
    "server",
    "auth",
    "security",
    "terminal",
    "files",
    "recording",
    "observability",
    "data",
    "database",
    "push",
)

SECTION_MAP: dict[str, str] = {
    # server
    "host": "server",
    "port": "server",
    "reuse_port": "server",
    "ssl_cert": "server",
    "ssl_key": "server",
    # auth
    "auth_required": "auth",
    "session_ttl": "auth",
    "session_sliding_ttl": "auth",
    "cookie_secure": "auth",
    "totp_issuer": "auth",
    # security
    "trust_proxy": "security",
    "trusted_proxies": "security",
    "allowed_ips": "security",
    "allowed_origins": "security",
    "security_headers": "security",
    "login_rate_limit": "security",
    "login_rate_window": "security",
    # terminal
    "default_shell": "terminal",
    "default_cwd": "terminal",
    "default_backend": "terminal",
    "tmux_preserve_on_shutdown": "terminal",
    "idle_timeout": "terminal",
    "max_life": "terminal",
    "max_sessions": "terminal",
    "max_sessions_per_user": "terminal",
    "session_max_clients": "terminal",
    "session_memory_limit": "terminal",
    "client_max_bytes": "terminal",
    "ws_max_connections": "terminal",
    "ws_max_connections_per_ip": "terminal",
    "input_rate_limit": "terminal",
    "input_rate_burst": "terminal",
    "scrollback_bytes": "terminal",
    "snapshot_mode": "terminal",
    "shell_integration": "terminal",
    # files
    "file_root": "files",
    "file_max_upload": "files",
    # recording
    "auto_record": "recording",
    "record_input": "recording",
    "recordings_retention_days": "recording",
    "recordings_max_bytes": "recording",
    # observability
    "metrics_enabled": "observability",
    "metrics_require_auth": "observability",
    "log_level": "observability",
    "log_json": "observability",
    "log_file": "observability",
    "log_max_bytes": "observability",
    "log_backup_count": "observability",
    "webhook_url": "observability",
    "audit_input": "observability",
    "audit_retention_days": "observability",
    "otel_enabled": "observability",
    "otel_endpoint": "observability",
    "otel_sample_ratio": "observability",
    # data
    "data_dir": "data",
    "config_path": "data",
    "instance_ttl": "data",
    "term_session_retention_days": "data",
    # database
    "database_url": "database",
    "database_echo": "database",
    # push
    "web_push_enabled": "push",
    "web_push_vapid_public": "push",
    "web_push_vapid_private": "push",
    "web_push_subject": "push",
}

#: Tiers, mirroring ``config.HOT_*``. Declared here so the API can label a
#: field without importing the server. ``config.kind_of`` remains the runtime
#: authority and a test asserts the two agree.
APPLY_IMMEDIATE = frozenset(
    {
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
        "ws_max_connections",
        "ws_max_connections_per_ip",
        "web_push_enabled",
        "web_push_subject",
        "web_push_vapid_private",
        "web_push_vapid_public",
    }
)
APPLY_NEW = frozenset(
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
        "snapshot_mode",
        "shell_integration",
        "totp_issuer",
    }
)

_BYTE_UNITS = {
    "b": 1,
    "k": 1024,
    "kb": 1024,
    "kib": 1024,
    "m": 1024**2,
    "mb": 1024**2,
    "mib": 1024**2,
    "g": 1024**3,
    "gb": 1024**3,
    "gib": 1024**3,
}
_TIME_UNITS = {
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hr": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
}
_UNIT_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([A-Za-z]+)\s*$")


class ConfigV2Error(ValueError):
    """A configuration document that cannot be migrated or parsed."""


def section_of(key: str) -> str:
    section, _, _ = key.partition(".")
    if section in SECTIONS:
        return section
    return SECTION_MAP.get(key, "server")


#: Values that must never be echoed by the config API. The web editor does not
#: need the current secret to replace it, and a browser (or a screenshot of one)
#: is not where a VAPID private key or a webhook credential belongs.
SECRET_KEYS = frozenset({"web_push_vapid_private", "webhook_url"})
SECRET_PLACEHOLDER = ""

#: Sectioned spellings that differ from the flat setting name. The flat names
#: stay canonical on disk (0.1.x compatibility), but both the documentation and
#: ``config set auth.required`` speak the shorter section-local name.
_SECTION_ALIASES: dict[tuple[str, str], str] = {
    ("auth", "required"): "auth_required",
    ("files", "root"): "file_root",
    ("files", "max_upload"): "file_max_upload",
}


def canonical_key(section: str, key: str) -> str:
    return _SECTION_ALIASES.get((section, key), key)


def local_aliases(section: str, key: str) -> tuple[str, ...]:
    """Section-local spellings that mean the canonical flat ``key``.

    ``[auth] required`` and ``auth_required`` are the same setting. The config
    editor has to recognise both when looking for the line to replace;
    otherwise it inserts a second spelling and the whole file is rejected as a
    duplicate -- which is what made ``wsctl config set auth.required`` fail on
    a hand-written sectioned file.
    """
    return tuple(
        local
        for (alias_section, local), canonical in _SECTION_ALIASES.items()
        if alias_section == section and canonical == key
    )


def split_key(key: str) -> tuple[str | None, str]:
    """Split ``server.port`` into ``("server", "port")``; flat keys pass through."""
    section, dot, name = key.partition(".")
    if dot and section in SECTIONS and name:
        return section, name
    return None, key


def normalise_key(key: str) -> str:
    """Accept ``auth.required`` / ``files.root`` wherever a flat key is expected."""
    section, name = split_key(key)
    if section is None:
        return key
    return canonical_key(section, name)


def apply_tier(key: str) -> str:
    if key in APPLY_IMMEDIATE:
        return "hot"
    if key in APPLY_NEW:
        return "new"
    return "restart"


def parse_human_int(key: str, value: Any, *, kind: str = "auto") -> Any:
    """Convert ``"12h"`` / ``"8MiB"`` to the integer the model stores.

    Only strings that *look like* a unit are converted; anything else is
    returned untouched so Pydantic can report the real type error. Integer
    fields keep accepting plain integers, which is what every 0.1.x config
    contains.
    """
    if not isinstance(value, str):
        return value
    match = _UNIT_RE.match(value)
    if match is None:
        return value
    number = float(match.group(1))
    unit = match.group(2).lower()
    if key in _DAYS_KEYS:
        day_factors = {
            "d": 1.0,
            "day": 1.0,
            "days": 1.0,
            "w": 7.0,
            "week": 7.0,
            "weeks": 7.0,
            "h": 1 / 24,
            "hour": 1 / 24,
            "hours": 1 / 24,
        }
        if unit in day_factors:
            # These fields are consumed as *days* and multiplied by 86400 by
            # the retention sweep. Converting "30d" to seconds here made the
            # product keep audit rows for roughly 7,000 years.
            return max(1, int(number * day_factors[unit]))
        return value
    if kind in ("auto", "bytes") and unit in _BYTE_UNITS:
        return int(number * _BYTE_UNITS[unit])
    if kind in ("auto", "time") and unit in _TIME_UNITS:
        return int(number * _TIME_UNITS[unit])
    return value


_DAYS_KEYS = frozenset(
    {
        "audit_retention_days",
        "term_session_retention_days",
        "recordings_retention_days",
    }
)


_TIME_KEYS = frozenset(
    {
        "session_ttl",
        "login_rate_window",
        "instance_ttl",
        "idle_timeout",
        "max_life",
        "audit_retention_days",
        "term_session_retention_days",
        "recordings_retention_days",
    }
)
_BYTE_KEYS = frozenset(
    {
        "scrollback_bytes",
        "session_memory_limit",
        "client_max_bytes",
        "file_max_upload",
        "log_max_bytes",
        "recordings_max_bytes",
    }
)


def coerce_value(key: str, value: Any) -> Any:
    """Normalise one field value before model validation."""
    kind = "time" if key in _TIME_KEYS else ("bytes" if key in _BYTE_KEYS else "auto")
    return parse_human_int(key, value, kind=kind)


def flatten_document(data: dict[str, Any]) -> dict[str, Any]:
    """Flatten ``{section: {key: value}}`` into ``{key: value}``.

    A legacy flat document (keys at top level) is accepted unchanged. Mixing
    both shapes is allowed but a duplicate key is an error: silently letting a
    section win over a flat key is how two edits become one mysterious value.
    """
    flat: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            if key not in SECTIONS:
                # Unknown tables are preserved by the editor but must not be
                # mistaken for settings.
                continue
            for inner, inner_value in value.items():
                canonical = canonical_key(key, inner)
                if canonical in flat:
                    raise ConfigV2Error(f"配置项 {canonical} 同时出现在顶层和 [{key}] 中")
                flat[canonical] = coerce_value(canonical, inner_value)
        else:
            if key in flat:
                raise ConfigV2Error(f"配置项 {key} 重复出现")
            flat[key] = coerce_value(key, value)
    return flat


def read_flat(path: Path) -> dict[str, Any]:
    """Read a config file in either format and return flat values."""
    try:
        raw = tomllib.loads(path.read_text("utf-8"))
    except OSError as exc:
        raise ConfigV2Error(f"无法读取配置文件 {path}：{exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigV2Error(f"配置文件 TOML 语法错误：{exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigV2Error("配置文件顶层必须是键值表")
    return flatten_document(raw)


def unflatten(values: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Group flat values into their sections (fixed order)."""
    out: dict[str, dict[str, Any]] = {name: {} for name in SECTIONS}
    for key, value in values.items():
        out.setdefault(section_of(key), {})[key] = value
    return {name: body for name, body in out.items() if body}


def render_toml(values: dict[str, Any]) -> str:
    """Render flat values as the canonical sectioned document."""
    from .config_edit import format_toml_value

    lines: list[str] = []
    grouped = unflatten(values)
    for section in SECTIONS:
        body = grouped.get(section)
        if not body:
            continue
        if lines:
            lines.append("")
        lines.append(f"[{section}]")
        for key in body:
            lines.append(f"{key} = {format_toml_value(body[key])}")
    return "\n".join(lines) + ("\n" if lines else "")


_KEY_LINE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*=")
_SECTION_LINE = re.compile(r"^\s*\[([A-Za-z_][A-Za-z0-9_]*)\]\s*$")


def migrate_text(original: str) -> str:
    """Convert a legacy flat document to sections, preserving comments.

    Comments that sit on their own line are kept at the top when they cannot
    be attributed to a key; trailing comments move with their key. This is a
    document a human maintains, so the migration must not silently delete the
    explanations they wrote.
    """
    ending = "\r\n" if "\r\n" in original else "\n"
    lines = original.splitlines()
    # Parse to know which keys exist and whether the file is already sectioned.
    try:
        parsed = tomllib.loads(original)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigV2Error(f"配置文件 TOML 语法错误：{exc}") from exc
    already_sectioned = any(
        isinstance(value, dict) and key in SECTIONS for key, value in parsed.items()
    )
    if already_sectioned:
        return original if original.endswith(ending) else original + ending

    preamble: list[str] = []
    entries: dict[str, list[str]] = {name: [] for name in SECTIONS}
    current_comment: list[str] = []
    for line in lines:
        stripped = line.strip()
        match = _KEY_LINE.match(line)
        if match is None:
            if stripped.startswith("#") or not stripped:
                current_comment.append(line)
            else:
                preamble.append(line)
            continue
        key = match.group(2)
        target = section_of(key)
        if current_comment:
            entries[target].extend(current_comment)
            current_comment = []
        entries[target].append(line)
    preamble.extend(current_comment)

    out: list[str] = list(preamble)
    for section in SECTIONS:
        body = entries.get(section)
        if not body:
            continue
        if out and out[-1].strip():
            out.append("")
        out.append(f"[{section}]")
        out.extend(body)
    return ending.join(out).rstrip("\r\n") + ending


def migrate_file(path: Path, *, backup: bool = True) -> tuple[bool, Path | None]:
    """Migrate ``path`` in place. Returns ``(changed, backup_path)``."""
    if not path.is_file():
        return False, None
    with path.open("r", encoding="utf-8", newline="") as handle:
        original = handle.read()
    migrated = migrate_text(original)
    if migrated == original:
        return False, None
    backup_path: Path | None = None
    if backup:
        backup_path = path.with_name(path.name + ".v1.bak")
        shutil.copy2(path, backup_path)
        with contextlib.suppress(OSError):
            backup_path.chmod(0o600)
    from .config_edit import write_text_atomic

    write_text_atomic(path, migrated)
    return True, backup_path


def sectioned_metadata(model_fields: dict[str, Any]) -> list[dict[str, Any]]:
    """Describe every setting for the web editor, grouped by section."""
    from .config_edit import format_toml_value

    out: list[dict[str, Any]] = []
    for key in sorted(model_fields):
        field = model_fields[key]
        annotation = field.annotation
        out.append(
            {
                "key": key,
                "section": section_of(key),
                "apply": apply_tier(key),
                "type": _type_name(annotation),
                "default": _default_text(field, format_toml_value),
            }
        )
    return out


def _type_name(annotation: Any) -> str:
    origin = get_origin(annotation)
    if origin is not None:
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if origin in (list, tuple, set):
            return "list"
        if str(origin).endswith("Path"):
            return "path"
        if len(args) == 1:
            return _type_name(args[0])
        return "string"
    if annotation is bool:
        return "bool"
    if annotation is int:
        return "int"
    if annotation is float:
        return "float"
    if str(annotation).endswith("Path"):
        return "path"
    return "string"


def _default_text(field: Any, renderer: Any) -> str:
    default = field.default
    if default is None:
        return ""
    try:
        return str(renderer(default))
    except Exception:
        return str(default)


__all__ = [
    "APPLY_IMMEDIATE",
    "APPLY_NEW",
    "SECTIONS",
    "SECTION_MAP",
    "ConfigV2Error",
    "apply_tier",
    "canonical_key",
    "coerce_value",
    "flatten_document",
    "migrate_file",
    "migrate_text",
    "normalise_key",
    "parse_human_int",
    "read_flat",
    "render_toml",
    "section_of",
    "sectioned_metadata",
    "split_key",
    "unflatten",
]
