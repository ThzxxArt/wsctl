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


class Metrics:
    """Thread-safe counter/gauge registry that renders Prometheus text."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, dict[tuple[tuple[str, str], ...], float]] = {}
        self._gauges: dict[str, dict[tuple[tuple[str, str], ...], float]] = {}
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

        for name, series in sorted(counters.items()):
            lines.append(f"# TYPE {name} counter")
            for key, value in sorted(series.items()):
                lines.append(f"{name}{_format_labels(dict(key))} {value}")

        for name, series in sorted(gauges.items()):
            lines.append(f"# TYPE {name} gauge")
            for key, value in sorted(series.items()):
                lines.append(f"{name}{_format_labels(dict(key))} {value}")

        for name, help_text, kind, fn in self._collectors:
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {kind}")
            lines.append(f"{name} {fn()}")

        return "\n".join(lines) + "\n"
