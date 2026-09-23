"""The three "do not reconnect" sets must stay equal.

The server, the CLI and the browser each decide whether a close code is
permanent. The first two import :mod:`wsctl.core.closecodes`; the browser
cannot, so ``static/app.js`` mirrors the set as a literal. Before this test
existed the CLI and the browser disagreed about ``4401`` and nothing
complained -- a mirror nobody checks is just a second place to forget.
"""

from __future__ import annotations

import re
from pathlib import Path

from wsctl.core import closecodes

APP_JS = Path(__file__).resolve().parent.parent / "src" / "wsctl" / "static" / "app.js"
CONNECT_PY = Path(__file__).resolve().parent.parent / "src" / "wsctl" / "cli" / "connect.py"


def _browser_fatal_codes() -> set[int]:
    source = APP_JS.read_text(encoding="utf-8")
    match = re.search(r"FATAL_CLOSE_CODES = new Set\(\[([^\]]+)\]\)", source)
    assert match is not None, "FATAL_CLOSE_CODES literal missing from app.js"
    return {int(part.strip()) for part in match.group(1).split(",") if part.strip()}


def test_browser_fatal_set_matches_the_python_definition() -> None:
    assert _browser_fatal_codes() == set(closecodes.FATAL_CLOSE_CODES)


def test_cli_imports_the_shared_definition_instead_of_relisting() -> None:
    source = CONNECT_PY.read_text(encoding="utf-8")
    assert "from wsctl.core.closecodes import" in source
    # A re-listed literal would silently drift the moment a code is added.
    assert re.search(r"FATAL_CODES\s*=\s*frozenset\(\{", source) is None


def test_half_open_timeout_is_recoverable() -> None:
    """4408 must stay *out* of the fatal set: reconnecting is the fix."""
    assert closecodes.CLOSE_TIMEOUT not in closecodes.FATAL_CLOSE_CODES


def test_rate_limiting_gets_its_own_permanent_code() -> None:
    """A peer that ignores rate limiting must not be told "log in again"."""
    assert closecodes.CLOSE_RATE_LIMITED in closecodes.FATAL_CLOSE_CODES
    assert closecodes.CLOSE_RATE_LIMITED != closecodes.CLOSE_UNAUTHORIZED


def test_every_code_is_classified() -> None:
    """No close code may exist without a deliberate reconnect decision."""
    recoverable = {
        # a half-open link: reconnecting and replaying is the fix
        closecodes.CLOSE_TIMEOUT,
        # output outran one viewer; the session is fine and a reconnect works
        closecodes.CLOSE_SLOW_CONSUMER,
    }
    assert set(closecodes.ALL_CLOSE_CODES) == (
        set(closecodes.FATAL_CLOSE_CODES) | recoverable
    )
    assert not (set(closecodes.FATAL_CLOSE_CODES) & recoverable)
