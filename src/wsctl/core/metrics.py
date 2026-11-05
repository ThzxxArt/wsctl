"""A tiny dependency-free Prometheus metrics registry.

Only the primitives wsctl needs are supported: labelled counters, labelled
gauges and dynamic gauge collectors (evaluated at scrape time).
"""

from __future__ import annotations

import threading
from collections.abc import Callable

Labels = dict[str, str]


def _escape_label(value: str) -> str:
    """Escape a label value for the Prometheus text exposition format."""
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def _format_labels(labels: Labels) -> str:
    if not labels:
        return ""
    inner = ",".join(
        f'{key}="{_escape_label(value)}"' for key, value in sorted(labels.items())
    )
    return "{" + inner + "}"


def _format_float(value: float) -> str:
    """Render a bucket bound the way Prometheus expects (``0.005``, ``+Inf``)."""
    if value == float("inf"):
        return "+Inf"
    return repr(float(value))


class Metrics:
    """Thread-safe counter/gauge/histogram registry that renders Prometheus text."""

    #: Prometheus' standard latency buckets. A terminal server's interesting
    #: range is milliseconds to seconds; these are the same buckets the
    #: ``prometheus_client`` default uses, so dashboards can be shared.
    DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, dict[tuple[tuple[str, str], ...], float]] = {}
        self._gauges: dict[str, dict[tuple[tuple[str, str], ...], float]] = {}
        self._histograms: dict[
            str, dict[tuple[tuple[str, str], ...], tuple[list[float], float, int]]
        ] = {}
        self._collectors: list[tuple[str, str, str, Callable[[], float]]] = []

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        key = tuple(sorted(labels.items()))
        with self._lock:
            bucket = self._counters.setdefault(name, {})
            bucket[key] = bucket.get(key, 0.0) + value

    def set(self, name: str, value: float, **labels: str) -> None:
        key = tuple(sorted(labels.items()))
        with self._lock:
            self._gauges.setdefault(name, {})[key] = float(value)

    def observe(
        self,
        name: str,
        value: float,
        **labels: str,
    ) -> None:
        """Record one histogram observation."""
        buckets = self.DEFAULT_BUCKETS
        key = tuple(sorted(labels.items()))
        number = float(value)
        with self._lock:
            series = self._histograms.setdefault(name, {})
            counts, total, count = series.get(key, ([0.0] * len(buckets), 0.0, 0))
            for index, bound in enumerate(buckets):
                if number <= bound:
                    counts[index] += 1
            series[key] = (counts, total + number, count + 1)

    def collect(self, name: str, help_text: str, fn: Callable[[], float]) -> None:
        """Register a gauge evaluated lazily at scrape time."""
        self._collectors.append((name, help_text, "gauge", fn))

    def collect_counter(self, name: str, help_text: str, fn: Callable[[], float]) -> None:
        """Register a lazily-evaluated *monotonic* series.

        ``fn`` must never return a value below its previous one. Declaring the
        type as ``counter`` matters: a ``_total`` series exposed as a gauge is
        rejected by ``rate()``.
        """
        self._collectors.append((name, help_text, "counter", fn))

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            counters = {name: dict(series) for name, series in self._counters.items()}
            gauges = {name: dict(series) for name, series in self._gauges.items()}
            histograms = {
                name: dict(series) for name, series in self._histograms.items()
            }

        for name, counter_series in sorted(counters.items()):
            lines.append(f"# TYPE {name} counter")
            for key, value in sorted(counter_series.items()):
                lines.append(f"{name}{_format_labels(dict(key))} {value}")

        for name, gauge_series in sorted(gauges.items()):
            lines.append(f"# TYPE {name} gauge")
            for key, value in sorted(gauge_series.items()):
                lines.append(f"{name}{_format_labels(dict(key))} {value}")

        for name, histogram_series in sorted(histograms.items()):
            lines.append(f"# TYPE {name} histogram")
            for key, (counts, total, count) in sorted(histogram_series.items()):
                labels = dict(key)
                for index, bound in enumerate(self.DEFAULT_BUCKETS):
                    bucket_labels = dict(labels)
                    bucket_labels["le"] = _format_float(bound)
                    lines.append(
                        f"{name}_bucket{_format_labels(bucket_labels)} {counts[index]}"
                    )
                bucket_labels = dict(labels)
                bucket_labels["le"] = "+Inf"
                lines.append(f"{name}_bucket{_format_labels(bucket_labels)} {count}")
                lines.append(f"{name}_sum{_format_labels(labels)} {total}")
                lines.append(f"{name}_count{_format_labels(labels)} {count}")

        for name, help_text, kind, fn in self._collectors:
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {kind}")
            lines.append(f"{name} {fn()}")

        return "\n".join(lines) + "\n"
