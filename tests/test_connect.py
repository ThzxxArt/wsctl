from __future__ import annotations

from pathlib import Path

import pytest

from wsctl.cli.connect import ConnectError, _ws_url, run_connect


def test_ws_url_http() -> None:
    assert _ws_url("http://example.com:7681") == "ws://example.com:7681/ws"


def test_ws_url_https_with_base() -> None:
    assert _ws_url("https://example.com/base/") == "wss://example.com/base/ws"


def test_missing_url_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("WSCTL_TOKEN", raising=False)
    with pytest.raises(ConnectError, match="no server URL"):
        run_connect(None, None, None)
