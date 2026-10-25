"""The opt-in policy for the expensive suites, tested as a unit.

``tests/conftest.py`` decides whether ``-m`` named a suite. The first version
asked "does the string 'browser' appear anywhere in ``-m``?", so
``-m "not browser"`` ran the browser suite it was asked to exclude; the second
asked "did the caller pass ``-m`` at all?", so ``-m "not browser"`` ran the
25-minute load suite. Both were wrong in opposite directions, and neither was
covered by a test.

``pytest_collection_modifyitems`` only touches ``config.getoption`` and
``item.keywords``/``add_marker``, so it can be exercised directly without
collecting a real session (and without spawning pytest recursively).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

CONFTEST = Path(__file__).resolve().parent / "conftest.py"


def _conftest_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_wsctl_conftest", CONFTEST)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Item:
    def __init__(self, *markers: str) -> None:
        self.keywords: dict[str, Any] = {marker: marker for marker in markers}
        self.added: list[Any] = []

    def add_marker(self, marker: Any) -> None:
        self.added.append(marker)


class _Config:
    def __init__(self, expression: str) -> None:
        self._expression = expression

    def getoption(self, name: str, default: Any = None) -> Any:
        return self._expression if name == "-m" else default


def _skipped(expression: str, marker: str) -> bool:
    conftest = _conftest_module()
    item = _Item(marker)
    conftest.pytest_collection_modifyitems(_Config(expression), [item])  # type: ignore[arg-type]
    return bool(item.added)


def test_without_dash_m_both_opt_in_suites_are_skipped() -> None:
    assert _skipped("", "browser")
    assert _skipped("", "slow")


def test_negated_marker_does_not_unskip_the_other_suite() -> None:
    """``-m "not browser"`` means "fast suite", not "everything including load"."""
    assert not _skipped("not browser", "browser")
    assert _skipped("not browser", "slow")
    assert not _skipped("not slow", "slow")
    assert _skipped("not slow", "browser")


def test_naming_a_suite_selects_it() -> None:
    assert not _skipped("browser", "browser")
    assert _skipped("browser", "slow")
    assert not _skipped("slow", "slow")
    assert _skipped("slow", "browser")


def test_marker_names_match_as_words_not_substrings() -> None:
    """``-m browserlike`` does not name the ``browser`` suite."""
    assert _skipped("browserlike", "browser")
    assert _skipped("not browserlike", "browser")
