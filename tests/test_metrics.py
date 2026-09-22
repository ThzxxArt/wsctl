from __future__ import annotations

from wsctl.core.metrics import Metrics


def test_counters_and_gauges() -> None:
    metrics = Metrics()
    metrics.inc("wsctl_things_total")
    metrics.inc("wsctl_things_total")
    metrics.inc("wsctl_logins_total", result="ok")
    metrics.set("wsctl_sessions", 3)
    text = metrics.render()
    assert "# TYPE wsctl_things_total counter" in text
    assert "wsctl_things_total 2.0" in text
    assert 'wsctl_logins_total{result="ok"} 1.0' in text
    assert "wsctl_sessions 3.0" in text


def test_collector_is_lazy() -> None:
    metrics = Metrics()
    state = {"n": 1}
    metrics.collect("wsctl_dynamic", "help", lambda: float(state["n"]))
    assert "wsctl_dynamic 1.0" in metrics.render()
    state["n"] = 5
    assert "wsctl_dynamic 5.0" in metrics.render()


def test_multiple_label_series() -> None:
    metrics = Metrics()
    metrics.inc("wsctl_x_total", result="ok")
    metrics.inc("wsctl_x_total", result="failed")
    metrics.inc("wsctl_x_total", result="ok")
    text = metrics.render()
    assert 'wsctl_x_total{result="ok"} 2.0' in text
    assert 'wsctl_x_total{result="failed"} 1.0' in text


def test_label_values_are_escaped() -> None:
    """Prometheus text format requires quotes/backslashes/newlines to be escaped."""
    metrics = Metrics()
    metrics.inc("wsctl_odd_total", note='say "hi"\\there')
    metrics.inc("wsctl_odd_total", note="two\nlines")
    text = metrics.render()
    assert 'note="say \\"hi\\"\\\\there"' in text
    assert 'note="two\\nlines"' in text


def test_collect_counter_declares_the_counter_type() -> None:
    """A monotonic `_total` series must be exposed as a counter, not a gauge."""
    metrics = Metrics()
    metrics.collect_counter("wsctl_x_total", "help", lambda: 3.0)
    text = metrics.render()
    assert "# TYPE wsctl_x_total counter" in text
    assert "wsctl_x_total 3.0" in text
