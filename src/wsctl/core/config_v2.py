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

#: Human-readable section names for the web editor and future CLI help.
SECTION_TITLES: dict[str, str] = {
    "server": "服务器",
    "auth": "认证",
    "security": "安全",
    "terminal": "终端",
    "files": "文件",
    "recording": "录制",
    "observability": "可观测性",
    "data": "数据",
    "database": "数据库",
    "push": "Web Push",
}

#: One Chinese title and one plain-language description per setting. The key
#: remains the stable machine identifier; this is what a person reads in the
#: settings dialog. A test asserts every ``Settings`` field has an entry, so a
#: newly added option cannot ship as a bare English key again.
FIELD_META: dict[str, tuple[str, str]] = {
    # server
    "host": ("监听地址", "服务绑定的 IP。0.0.0.0 允许所有网卡访问，127.0.0.1 仅本机。"),
    "port": ("监听端口", "Web 页面与 WebSocket 共用的 TCP 端口。"),
    "reuse_port": (
        "端口复用",
        "允许使用 SO_REUSEPORT：滚动重启时新旧实例可短暂共享端口，避免连接被拒绝。",
    ),
    "ssl_cert": ("TLS 证书", "HTTPS 证书文件路径。留空表示由反向代理终止 TLS。"),
    "ssl_key": ("TLS 私钥", "HTTPS 私钥文件路径，必须与上面的证书匹配。"),
    # auth
    "auth_required": (
        "启用登录认证",
        "关闭后任何人无需登录即可访问终端，只应在本机临时排障时使用。",
    ),
    "session_ttl": ("登录有效期", "登录会话的存活时间，例如 12h；到期后需要重新登录。"),
    "session_sliding_ttl": (
        "滑动续期",
        "每次请求都顺延登录有效期；关闭后从登录时刻固定计算。",
    ),
    "cookie_secure": (
        "仅 HTTPS Cookie",
        "只在 HTTPS 连接中发送登录 Cookie。纯 HTTP 访问时必须关闭。",
    ),
    "totp_issuer": ("两步验证名称", "认证器中显示的发行方名称，便于区分多个站点。"),
    # security
    "trust_proxy": (
        "信任反向代理",
        "从可信代理读取 X-Forwarded-For / X-Forwarded-Proto；仅在 nginx、Caddy 等代理后开启。",
    ),
    "trusted_proxies": (
        "可信代理地址",
        "允许提供转发头的代理 IP/CIDR。留空表示信任所有直连代理。",
    ),
    "allowed_ips": ("允许的来源 IP", "只允许这些 IP/CIDR 访问；留空表示不限制。"),
    "allowed_origins": ("允许的 Origin", "允许跨站访问的 Origin；留空只允许同源。"),
    "security_headers": ("安全响应头", "添加 CSP、X-Frame-Options 等浏览器安全头。"),
    "login_rate_limit": (
        "登录失败次数",
        "统计窗口内允许的失败登录次数，超过后暂时拒绝该来源。",
    ),
    "login_rate_window": ("登录限流窗口", "统计失败登录的时间窗口，单位秒。"),
    # terminal
    "default_shell": ("默认 Shell", "新建本地会话时启动的 shell；留空使用系统默认。"),
    "default_cwd": ("默认工作目录", "新建会话时的工作目录；留空使用服务进程所在目录。"),
    "default_backend": (
        "默认后端",
        "新会话默认使用 local（本地 PTY）或 tmux（可跨服务重启恢复）。",
    ),
    "tmux_preserve_on_shutdown": (
        "关闭时保留 tmux",
        "服务停止时不结束 tmux 会话，下次启动可以重新接管。",
    ),
    "idle_timeout": ("空闲超时", "会话无输入输出多久后自动结束；0 表示不限制。"),
    "max_life": ("最长寿命", "会话从创建起最多存活多久；0 表示不限制。"),
    "max_sessions": ("最大会话数", "整个实例允许同时存在的终端会话总数；0 表示不限制。"),
    "max_sessions_per_user": (
        "每用户会话上限",
        "单个用户允许同时存在的会话数；0 表示不限制。",
    ),
    "session_max_clients": (
        "每会话客户端上限",
        "单个会话允许同时连接的浏览器/CLI 数量；0 表示不限制。",
    ),
    "session_memory_limit": (
        "会话内存上限",
        "该会话所有客户端输出队列的总内存上限，超过时丢弃最旧输出。",
    ),
    "client_max_bytes": (
        "单客户端缓冲",
        "每个连接允许积压的输出字节数，超过时丢弃最旧帧并触发重新同步。",
    ),
    "ws_max_connections": ("WebSocket 连接上限", "整个实例允许的 WebSocket 连接总数。"),
    "ws_max_connections_per_ip": (
        "单 IP 连接上限",
        "每个来源 IP 允许的 WebSocket 连接数。",
    ),
    "input_rate_limit": (
        "输入速率限制",
        "每个连接每秒允许的输入字节数；0 表示不限制。",
    ),
    "input_rate_burst": ("输入突发上限", "允许瞬时超过速率的字节数。"),
    "scrollback_bytes": (
        "回滚缓冲",
        "每个会话保留的原始输出字节数，用于断线续传和屏幕快照。",
    ),
    "snapshot_mode": (
        "快照模式",
        "auto 仅在全屏应用时启用影子屏幕；always 始终启用；off 只回放原始字节。",
    ),
    "shell_integration": (
        "Shell 集成",
        "注入 OSC 133/633 脚本，提供命令块、工作目录、耗时和退出码。",
    ),
    # files
    "file_root": ("文件根目录", "文件面板可访问的根目录；留空使用数据目录。"),
    "file_max_upload": ("单文件上传上限", "单次上传允许的最大字节数。"),
    # recording
    "auto_record": ("自动录制", "新会话创建时自动开始 asciinema 录制。"),
    "record_input": (
        "记录键盘输入",
        "录制时同时保存输入内容；可能包含密码，敏感环境慎用。",
    ),
    "recordings_retention_days": (
        "录制保留天数",
        "超过天数的录制自动删除；0 表示永久保留。",
    ),
    "recordings_max_bytes": (
        "录制总容量",
        "录制目录允许占用的最大字节数；0 表示不限制。",
    ),
    # observability
    "metrics_enabled": ("Prometheus 指标", "暴露 /metrics 指标端点。"),
    "metrics_require_auth": (
        "指标需要认证",
        "访问 /metrics 时必须登录；关闭后对网络公开。",
    ),
    "log_level": ("日志级别", "debug / info / warning / error，级别越低输出越多。"),
    "log_json": ("JSON 日志", "以 JSON 行输出日志，便于采集和检索。"),
    "log_file": ("日志文件", "日志写入的文件路径；留空输出到标准错误。"),
    "log_max_bytes": ("单个日志上限", "日志轮转前允许的最大字节数。"),
    "log_backup_count": ("日志保留份数", "轮转后保留的旧日志文件数量。"),
    "webhook_url": ("事件 Webhook", "会话和审计事件推送到的 HTTP 地址；留空关闭。"),
    "audit_input": (
        "审计键盘输入",
        "将输入内容写入审计日志；可能包含密码，敏感环境慎用。",
    ),
    "audit_retention_days": (
        "审计保留天数",
        "超过天数的审计记录自动删除；0 表示永久保留。",
    ),
    "otel_enabled": ("OpenTelemetry", "启用 OpenTelemetry 追踪导出。"),
    "otel_endpoint": ("OTLP 端点", "OpenTelemetry Collector 的 OTLP HTTP 地址。"),
    "otel_sample_ratio": ("采样比例", "0 到 1 之间的追踪采样比例。"),
    # data
    "data_dir": (
        "数据目录",
        "数据库、录制、日志、自签证书等运行数据的存放目录。",
    ),
    "config_path": ("配置文件", "当前加载的 TOML 配置文件路径。"),
    "instance_ttl": ("实例心跳有效期", "实例注册信息的过期时间，用于发现残留进程。"),
    "term_session_retention_days": (
        "会话历史保留天数",
        "已结束会话记录保留天数；0 表示永久保留。",
    ),
    # database
    "database_url": (
        "数据库 URL",
        "SQLAlchemy 连接串；留空使用 data_dir 下的 SQLite 数据库。",
    ),
    "database_echo": ("SQL 日志", "输出每条 SQL 语句，仅用于调试。"),
    # push
    "web_push_enabled": ("Web Push", "启用浏览器会话结束和长命令完成通知。"),
    "web_push_vapid_public": (
        "VAPID 公钥",
        "Web Push 的公开密钥，可用 wsctl push keys 生成。",
    ),
    "web_push_vapid_private": ("VAPID 私钥", "Web Push 的私有密钥，必须保密。"),
    "web_push_subject": (
        "VAPID 联系地址",
        "VAPID 要求的 mailto: 或 https: 联系地址。",
    ),
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
        title, description = FIELD_META.get(key, (key, ""))
        out.append(
            {
                "key": key,
                "section": section_of(key),
                "section_title": SECTION_TITLES.get(section_of(key), section_of(key)),
                "title": title,
                "description": description,
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
    "FIELD_META",
    "SECTIONS",
    "SECTION_MAP",
    "SECTION_TITLES",
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
