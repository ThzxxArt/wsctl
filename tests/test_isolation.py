"""Canary: the test suite must never see the developer's own environment.

`_isolate_environment` exists because that class of defect produced a
wrong-green three separate times (see its docstring). These tests fail if the
fixture is ever weakened.

The assertions are deliberately strict. An earlier version of this file
contained `assert json.dumps(creds) != "{}" or creds == {}`, which is a
tautology -- it cannot fail no matter what leaks -- and only looked for a
`ghp_` prefix, so any other credential passed. A canary that cannot fail is
worse than none: it manufactures confidence.
"""

from __future__ import annotations

import os
from pathlib import Path

from wsctl.cli.client import credentials_path, load_credentials
from wsctl.core.config import default_config_dir


def test_the_sandbox_is_outside_the_developers_home() -> None:
    """Nothing the suite touches may live under the caller's own home."""
    home = Path.home().resolve()
    conf = default_config_dir().resolve()
    assert home not in conf.parents and conf != home, (
        f"isolation broken: config dir {conf} is inside {home}"
    )
    creds = credentials_path().resolve()
    assert home not in creds.parents and creds != home, (
        f"isolation broken: credentials {creds} is inside {home}"
    )


def test_the_sandbox_starts_empty() -> None:
    """Any credential at all is a leak -- not just a recognisable token shape."""
    creds = load_credentials()
    assert creds == {}, f"isolation broken: saw someone else's credentials: {creds!r}"


def test_only_the_sandbox_variables_are_exported() -> None:
    # The sandbox sets exactly these two; anything else is a leak from the
    # developer's shell (WSCTL_PORT, WSCTL_HOST, …) and turns default-value
    # assertions into tests of their environment.
    allowed = {"WSCTL_DATA_DIR", "WSCTL_CONFIG"}
    leaked = sorted(k for k in os.environ if k.startswith("WSCTL_") and k not in allowed)
    assert leaked == [], f"isolation broken: still exported {leaked}"
