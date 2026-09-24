from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from wsctl.cli.connect import ConnectError, _recv_loop, _ws_url, run_connect


def test_ws_url_http() -> None:
    assert _ws_url("http://example.com:7681") == "ws://example.com:7681/ws"


def test_ws_url_https_with_base() -> None:
    assert _ws_url("https://example.com/base/") == "wss://example.com/base/ws"


def test_missing_url_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("WSCTL_TOKEN", raising=False)
    with pytest.raises(ConnectError, match="缺少服务器地址"):
        run_connect(None, None, None)


class _FakeWS:
    def __init__(self, messages: list[str]) -> None:
        self._messages = list(messages)

    async def send(self, _data: Any) -> None:  # pragma: no cover - attach frame
        return None

    async def recv(self) -> str:
        return self._messages.pop(0)


def test_recv_loop_surfaces_operational_notices(monkeypatch: pytest.MonkeyPatch) -> None:
    """`desync` / `notice` / `evicted` / `attached.incomplete` must be said.

    The browser raises a desync bar; the CLI used to swallow the very same
    control messages and leave a garbled screen with nothing said -- the same
    defect class as a toast that never appears, on the second client of the
    same protocol.
    """
    from wsctl.cli import connect as connect_mod

    said: list[str] = []
    monkeypatch.setattr(connect_mod, "_notice", said.append)

    messages = [
        json.dumps({"type": "desync", "msg": "输出过快，部分内容已省略"}),
        json.dumps({"type": "notice", "msg": "会话为只读"}),
        json.dumps({"type": "evicted", "msg": "连接已被释放（会话仍在运行）"}),
        json.dumps({"type": "attached", "incomplete": True}),
        json.dumps({"type": "exit", "code": 0}),
    ]
    _notice_msg, code, exited = asyncio.run(_recv_loop(_FakeWS(messages), "sid", 80, 24))
    assert exited and code == 1000
    joined = "\n".join(said)
    assert "输出过快" in joined, "desync must reach the operator"
    assert "只读" in joined, "notices must reach the operator"
    assert "连接已被释放" in joined, "eviction must reach the operator"
    assert "回放未完整" in joined, "an incomplete replay must not look whole"


def test_recv_loop_stays_quiet_when_the_replay_is_whole(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean attach says nothing -- noise is not honesty."""
    from wsctl.cli import connect as connect_mod

    said: list[str] = []
    monkeypatch.setattr(connect_mod, "_notice", said.append)
    messages = [
        json.dumps({"type": "attached", "incomplete": False}),
        json.dumps({"type": "exit", "code": 0}),
    ]
    _notice, _code, exited = asyncio.run(_recv_loop(_FakeWS(messages), "sid", 80, 24))
    assert exited
    assert said == [], f"a clean attach printed noise: {said}"
