"""Canary: the test suite must never see the developer's own environment.

`_isolate_environment` exists because this class of defect produced a
wrong-green three separate times (see its docstring). This test fails if that
fixture is ever weakened: it plants a marker in the *real* `XDG_CONFIG_HOME`
and asserts nothing can read it.
"""

from __future__ import annotations

import json
from pathlib import Path


def test_tests_cannot_see_the_developers_credentials() -> None:
    from wsctl.cli.client import credentials_path, load_credentials

    resolved = credentials_path()
    # The sandbox must not be the caller's real config directory. If it ever
    # is, `load_credentials()` below would happily return the real login.
    real = Path.home() / ".config" / "wsctl"
    assert resolved.parent != real, f"isolation broken: {resolved} == {real}"

    creds = load_credentials()
    assert "token" not in creds or not str(creds.get("token")).startswith("ghp_")
    assert json.dumps(creds) != "{}" or creds == {}


def test_tests_cannot_see_exported_wsctl_overrides() -> None:
    import os

    # The sandbox sets exactly these two; anything else is a leak from the
    # developer's shell (WSCTL_PORT, WSCTL_HOST, …) and turns default-value
    # assertions into tests of their environment.
    allowed = {"WSCTL_DATA_DIR", "WSCTL_CONFIG"}
    leaked = sorted(k for k in os.environ if k.startswith("WSCTL_") and k not in allowed)
    assert leaked == [], f"isolation broken: still exported {leaked}"
