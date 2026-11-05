"""Observability: request correlation, standard metrics and tracing.

Three pieces, each optional at runtime:

* a ``request_id`` context variable, injected into every log record;
* a ``prometheus_client`` registry for the *new* v2 metrics that need
  histogram buckets and exporters (the existing :class:`~wsctl.core.metrics.Metrics`
  registry stays for the 0.1.x series, so dashboards do not break);
* OpenTelemetry tracing, enabled only when an endpoint is configured.

Nothing here may crash the server because an optional dependency is missing:
the package ships them, but a stripped install must still serve terminals.
"""

from __future__ import annotations

import contextvars
import logging
import re
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

log = logging.getLogger("wsctl.observability")

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "wsctl_request_id", default=""
)
user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("wsctl_user_id", default="")
session_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "wsctl_session_id", default=""
)


class ContextFilter(logging.Filter):
    """Attach request/user/session identifiers to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.user_id = user_id_var.get()
        record.session_id = session_id_var.get()
        return True


try:  # pragma: no cover - exercised by integration, not unit tests
    from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

    PROM_REGISTRY: Any = CollectorRegistry()
    WS_ACK_LAG_BYTES = Gauge(
        "wsctl_ws_ack_lag_bytes",
        "Bytes queued for a viewer beyond what it has acknowledged",
        registry=PROM_REGISTRY,
    )
    WS_SNAPSHOT_TOTAL = Counter(
        "wsctl_ws_snapshot_total",
        "Screen snapshots produced, by result",
        ["result", "source"],
        registry=PROM_REGISTRY,
    )
    WS_GAP_TOTAL = Counter(
        "wsctl_ws_gap_total",
        "Protocol v2 gap events, by reason",
        ["reason"],
        registry=PROM_REGISTRY,
    )
    TERMINAL_INPUT_LATENCY = Histogram(
        "wsctl_terminal_input_latency_seconds",
        "Time from a client input frame to the PTY write",
        buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
        registry=PROM_REGISTRY,
    )
    ACTOR_MAILBOX_DEPTH = Gauge(
        "wsctl_session_actor_mailbox_depth",
        "Commands waiting in a session actor mailbox",
        registry=PROM_REGISTRY,
    )

    def render_prometheus() -> str:
        return generate_latest(PROM_REGISTRY).decode("utf-8")

    PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover - declared dependency
    PROM_REGISTRY = None

    def render_prometheus() -> str:
        return ""

    PROMETHEUS_AVAILABLE = False


class RequestContextMiddleware:
    """Pure-ASGI middleware that assigns and echoes a request id.

    Implemented at the ASGI layer rather than with ``BaseHTTPMiddleware`` so it
    also covers WebSocket handshakes and does not add a task to every request.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        incoming = headers.get(b"x-request-id", b"").decode("latin-1").strip()
        # The value is echoed in a response header. A parser normally rejects
        # CR/LF in a header, but a proxy or an HTTP/2 peer that normalises late
        # must not be able to split that response. Keep the correlation shape
        # (hex, dots, dashes, underscores) and drop everything else.
        incoming = re.sub(r"[^A-Za-z0-9._-]", "", incoming)[:72]
        request_id = incoming or uuid.uuid4().hex
        token = request_id_var.set(request_id)

        async def send_with_id(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                raw_headers = list(message.get("headers") or [])
                if not any(key.lower() == b"x-request-id" for key, _ in raw_headers):
                    raw_headers.append((b"x-request-id", request_id.encode("latin-1")))
                message = {**message, "headers": raw_headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_var.reset(token)


def setup_tracing(app: Any, settings: Any) -> Any:
    """Initialise OpenTelemetry when the operator asked for it.

    Returns the tracer provider (or ``None``). A failure to initialise is
    logged and swallowed: telemetry is never a reason a terminal stops working.
    """
    global _TRACING_ENABLED
    if not getattr(settings, "otel_enabled", False):
        return None
    try:  # pragma: no cover - needs a collector to be meaningful
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

        resource = Resource.create({"service.name": "wsctl", "service.version": "0.2.0"})
        provider = TracerProvider(
            resource=resource,
            sampler=TraceIdRatioBased(float(settings.otel_sample_ratio)),
        )
        if settings.otel_endpoint:
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_endpoint))
            )
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
        _TRACING_ENABLED = True
        return provider
    except Exception:
        log.exception("OpenTelemetry 初始化失败，已继续运行（不影响终端）")
        return None


_TRACING_ENABLED = False


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """A span when tracing is enabled, a no-op otherwise.

    Instrumenting the HTTP layer is not enough to explain a terminal problem:
    the interesting work is a WebSocket attach, a replay, a snapshot and a PTY
    read, none of which are HTTP requests. Callers use this helper so the
    disabled path costs one boolean check.
    """
    if not _TRACING_ENABLED:
        yield None
        return
    try:  # pragma: no cover - depends on an exporter being configured
        from opentelemetry import trace

        tracer = trace.get_tracer("wsctl")
        with tracer.start_as_current_span(name) as current:
            for key, value in attributes.items():
                if value is None or isinstance(value, (str, int, float, bool)):
                    current.set_attribute(f"wsctl.{key}", value)
            yield current
    except Exception:  # pragma: no cover
        yield None


def observe_input_latency(started: float) -> None:
    if PROMETHEUS_AVAILABLE:
        TERMINAL_INPUT_LATENCY.observe(max(0.0, time.perf_counter() - started))


__all__ = [
    "ACTOR_MAILBOX_DEPTH",
    "PROMETHEUS_AVAILABLE",
    "PROM_REGISTRY",
    "TERMINAL_INPUT_LATENCY",
    "WS_ACK_LAG_BYTES",
    "WS_GAP_TOTAL",
    "WS_SNAPSHOT_TOTAL",
    "ContextFilter",
    "RequestContextMiddleware",
    "observe_input_latency",
    "render_prometheus",
    "request_id_var",
    "session_id_var",
    "setup_tracing",
    "span",
    "user_id_var",
]
