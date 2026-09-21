from __future__ import annotations

from typing import Any

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip browser tests unless explicitly selected with ``-m browser``."""
    marker_expr = config.getoption("-m", default="") or ""
    if "browser" in marker_expr:
        return
    skip = pytest.mark.skip(reason="browser tests: select with -m browser")
    for item in items:
        if "browser" in item.keywords:
            item.add_marker(skip)


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
