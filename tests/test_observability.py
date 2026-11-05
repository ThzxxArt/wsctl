from __future__ import annotations

import asyncio
import json
import logging

from wsctl.core.logging import JsonFormatter, configure_logging
from wsctl.core.metrics import Metrics
from wsctl.core.observability import (
    ContextFilter,
    render_prometheus,
    request_id_var,
)
from wsctl.server.services.events import EventBroker, format_sse


def test_metrics_render_help_type_and_histograms() -> None:
    metrics = Metrics()
    metrics.inc("wsctl_test_total", 2, kind="a")
    metrics.set("wsctl_test_gauge", 5)
    metrics.collect_counter("wsctl_collected_total", "help", lambda: 3.0)
    metrics.observe("wsctl_test_latency_seconds", 0.02)
    text = metrics.render()
    assert "# TYPE wsctl_test_total counter" in text
    assert 'wsctl_test_total{kind="a"} 2' in text
    assert "# TYPE wsctl_test_gauge gauge" in text
    assert "# HELP wsctl_collected_total help" in text
    assert "# TYPE wsctl_collected_total counter" in text
    assert "# TYPE wsctl_test_latency_seconds histogram" in text
    assert "wsctl_test_latency_seconds_count 1" in text
    assert "wsctl_test_latency_seconds_sum 0.02" in text


def test_prometheus_registry_is_available_and_renders() -> None:
    text = render_prometheus()
    assert "wsctl_ws_ack_lag_bytes" in text
    assert "wsctl_ws_snapshot_total" in text


def test_json_formatter_includes_request_context() -> None:
    token = request_id_var.set("req-123")
    try:
        record = logging.LogRecord("wsctl.test", logging.INFO, __file__, 1, "hello", (), None)
        ContextFilter().filter(record)
        payload = json.loads(JsonFormatter().format(record))
        assert payload["request_id"] == "req-123"
        assert payload["message"] == "hello"
    finally:
        request_id_var.reset(token)


def test_configure_logging_json_does_not_raise() -> None:
    configure_logging("DEBUG", json_output=True)
    logging.getLogger("wsctl.test").info("structured")
    configure_logging("INFO", json_output=False)


def test_event_broker_delivers_and_unsubscribes() -> None:
    broker = EventBroker()

    async def main() -> list[dict]:
        queue = await broker.subscribe()
        assert broker.subscriber_count == 1
        broker.publish("session_created", session="s1")
        message = await asyncio.wait_for(queue.get(), timeout=1)
        await broker.unsubscribe(queue)
        assert broker.subscriber_count == 0
        return [message]

    messages = asyncio.run(main())
    assert messages[0]["event"] == "session_created"
    assert messages[0]["session"] == "s1"
    assert messages[0]["v"] == 2


def test_event_broker_overflow_terminates_the_stream() -> None:
    """A slow subscriber is told to reconnect, not silently forgotten.

    The broker used to discard a full queue from its subscriber set. The SSE
    generator then waited on a queue nobody fed: the browser saw only
    heartbeats, never concluded the state feed was dead, and never resynced.
    """
    broker = EventBroker(max_queue=1)

    async def main() -> list[dict]:
        queue = await broker.subscribe()
        broker.publish("first", session="s1")
        broker.publish("second", session="s2")
        assert broker.subscriber_count == 0
        seen: list[dict] = []
        async for message in broker.stream(queue):
            seen.append(message)
        return seen

    seen = asyncio.run(asyncio.wait_for(main(), timeout=2))
    assert seen and seen[-1]["event"] == "overflow"


def test_sse_format_is_two_lines_and_json() -> None:
    text = format_sse({"event": "heartbeat", "ts": 1})
    assert text.startswith("event: heartbeat\n")
    assert text.endswith("\n\n")
    assert json.loads(text.split("data: ", 1)[1].strip())["event"] == "heartbeat"


def test_log_redaction_removes_credentials_from_messages_and_fields() -> None:
    from wsctl.core.logging import redact_event, redact_text

    assert "hunter2" not in redact_text("password=hunter2 user=bob")
    assert "Bearer abc" not in redact_text("authorization: Bearer abc")
    redacted = redact_event(
        {
            "event": "login",
            "password": "hunter2",
            "nested": {"token": "abc", "message": "api_key=xyz"},
            "safe": "visible",
        }
    )
    assert redacted["password"] == "***"
    assert redacted["nested"]["token"] == "***"
    assert "xyz" not in redacted["nested"]["message"]
    assert redacted["safe"] == "visible"


def test_span_is_a_noop_when_tracing_is_disabled() -> None:
    from wsctl.core.observability import span

    with span("test") as current:
        assert current is None

