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
