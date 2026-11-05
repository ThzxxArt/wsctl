"""Logging configuration: stdlib, structlog and request correlation.

The server still uses stdlib ``logging`` loggers (there are hundreds of them,
and rewriting every call site would add risk without adding information).
structlog is wired in as the *renderer*: stdlib records pass through the same
processor chain, so a log line from ``wsctl.session`` and one from a new
structlog logger have the same shape, the same timestamps and the same
``request_id``.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from .observability import ContextFilter

#: Names whose *values* must never reach a log sink. The list is deliberately
#: about credentials, not about every field called "key" (a layout key is not a
#: secret); ``api_key`` is separate from ``key`` for that reason.
_SENSITIVE = re.compile(
    r"(?i)\b(password|passwd|secret|token|authorization|cookie|api[_-]?key|"
    r"vapid[_-]?private|totp[_-]?secret)\b\s*[:=]\s*([^\s,;&]+)"
)
_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "authorization",
        "cookie",
        "api_key",
        "apikey",
        "vapid_private",
        "totp_secret",
        "share_token",
    }
)


def redact_text(value: str) -> str:
    return _SENSITIVE.sub(lambda match: f"{match.group(1)}=***", value)


def redact_event(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***" if str(key).lower() in _SENSITIVE_KEYS else redact_event(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_event(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_processor(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return dict(redact_event(event_dict))


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": round(record.created, 3),
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        request_id = getattr(record, "request_id", "")
        if request_id:
            payload["request_id"] = request_id
        user_id = getattr(record, "user_id", "")
        if user_id:
            payload["user_id"] = user_id
        session_id = getattr(record, "session_id", "")
        if session_id:
            payload["session_id"] = session_id
        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def _build_structlog_formatter(json_output: bool) -> logging.Formatter:
    """Return a ``ProcessorFormatter`` shared by stdlib and structlog records."""
    import structlog
    from structlog.stdlib import ProcessorFormatter

    foreign_pre_chain: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=False),
        redact_processor,
    ]
    if json_output:
        renderer: Any = structlog.processors.JSONRenderer(ensure_ascii=False)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)
    formatter = ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=foreign_pre_chain,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=False),
            redact_processor,  # type: ignore[list-item]
            ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    return formatter


def configure_logging(level: str = "info", *, json_output: bool = False) -> None:
    handler = logging.StreamHandler()
    try:
        handler.setFormatter(_build_structlog_formatter(json_output))
    except Exception:  # pragma: no cover - structlog is a declared dependency
        if json_output:
            handler.setFormatter(JsonFormatter())
        else:
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
            )
    handler.addFilter(ContextFilter())
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())


__all__ = ["JsonFormatter", "configure_logging", "redact_event", "redact_text"]
