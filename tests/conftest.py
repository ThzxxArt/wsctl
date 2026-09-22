from __future__ import annotations

import contextlib
import os
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
def _isolate_environment(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Give every test a private config/data directory.

    This class of defect has produced a wrong-green three separate times: a
    test passed because the developer's own ``~/.config/wsctl/credentials.json``
    happened to satisfy it (0.1.5), a batch of default-value assertions passed
    because ``WSCTL_PORT`` happened to be exported (0.1.6), and the doctor
    probe tests asserted against the real cached login (0.1.7). Each was fixed
    case by case and each fix was easy to forget in the next test.

    Isolating by default makes the mistake impossible instead of memorable.
    A test that deliberately needs a real value sets it in its own body, which
    runs after this fixture and therefore still wins.

    Two subtleties, both learned the hard way:

    * ``WSCTL_CONFIG`` is *not* set here. It would override the
      ``XDG_CONFIG_HOME``-based path that ``config set``/``edit`` write to and
      silently redirect a test's own writes elsewhere.
    * The sandbox lives under ``tmp_path_factory``, not ``tmp_path``. Creating
      directories inside ``tmp_path`` shows up in tests that list it.
    """
    base = tmp_path_factory.mktemp("env")
    for key in [k for k in os.environ if k.startswith("WSCTL_")]:
        monkeypatch.delenv(key, raising=False)
    conf = base / "config"
    conf.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(conf))
    monkeypatch.setenv("WSCTL_DATA_DIR", str(base / "data"))


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
