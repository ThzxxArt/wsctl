from __future__ import annotations

from typing import Any

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip opt-in suites unless explicitly selected (``-m browser`` / ``-m slow``)."""
    marker_expr = config.getoption("-m", default="") or ""
    if "browser" not in marker_expr:
        skip_browser = pytest.mark.skip(reason="browser tests: select with -m browser")
        for item in items:
            if "browser" in item.keywords:
                item.add_marker(skip_browser)
    if "slow" not in marker_expr:
        skip_slow = pytest.mark.skip(reason="slow tests: select with -m slow")
        for item in items:
            if "slow" in item.keywords:
                item.add_marker(skip_slow)


class FakeClient:
    """In-memory client sink used by the tests."""

    def __init__(self) -> None:
        self.items: list[bytes | dict[str, Any]] = []

    def put(self, item: bytes | dict[str, Any]) -> None:
        self.items.append(item)

    def output(self) -> bytes:
        return b"".join(i for i in self.items if isinstance(i, bytes))

    def controls(self, kind: str) -> list[dict[str, Any]]:
        return [i for i in self.items if isinstance(i, dict) and i.get("type") == kind]
