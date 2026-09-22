from __future__ import annotations

import contextlib
from collections.abc import Iterator
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


@pytest.fixture(autouse=True)
def _close_stores(tmp_path: Any) -> Iterator[None]:
    """Close any :class:`~wsctl.core.store.Store` a test opened and forgot.

    SQLite connections are file handles; leaving them open across a few hundred
    tests leaks descriptors and can keep WAL sidecars alive past their test.
    """
    from wsctl.core.store import Store

    opened: list[Store] = []
    original_init = Store.__init__

    def tracking_init(self: Store, path: Any) -> None:
        original_init(self, path)
        opened.append(self)

    Store.__init__ = tracking_init  # type: ignore[method-assign]
    try:
        yield
    finally:
        Store.__init__ = original_init  # type: ignore[method-assign]
        for store in opened:
            # Already-closed is fine (the app's lifespan may have closed it).
            with contextlib.suppress(Exception):
                store.close()


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
