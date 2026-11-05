from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from wsctl.cli.connect import ConnectError, _recv_loop, _ws_url, run_connect
from wsctl.core.protocol import FLAG_SNAPSHOT, encode_data


def test_ws_url_http() -> None:
    assert _ws_url("http://example.com:7681") == "ws://example.com:7681/ws/v2"


def test_ws_url_https_with_base() -> None:
    assert _ws_url("https://example.com/base/") == "wss://example.com/base/ws/v2"


def test_missing_url_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("WSCTL_TOKEN", raising=False)
    with pytest.raises(ConnectError, match="缺少服务器地址"):
        run_connect(None, None, None)


class _FakeWS:
    def __init__(self, messages: list[str | bytes]) -> None:
        self._messages = list(messages)
        self.sent: list[str | bytes] = []

    async def send(self, data: Any) -> None:
        self.sent.append(data)

    async def recv(self) -> str | bytes:
        return self._messages.pop(0)


def test_recv_loop_attaches_with_version_and_resume_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wsctl.cli import connect as connect_mod

    monkeypatch.setattr(connect_mod, "_notice", lambda _message: None)
    ws = _FakeWS([json.dumps({"type": "exit", "code": 0})])
    asyncio.run(
        _recv_loop(ws, "sid", 80, 24, {"last_seq": 99, "generation": "gen-1"})
    )
    attach = json.loads(ws.sent[0])
    assert attach["v"] == 2
    assert attach["type"] == "attach"
    assert attach["last_seq"] == 99
    assert attach["generation"] == "gen-1"
    assert attach["capabilities"]


def test_recv_loop_decodes_data_frames_and_acks(monkeypatch: pytest.MonkeyPatch) -> None:
    from wsctl.cli import connect as connect_mod

    written: list[bytes] = []

    def fake_write(_fd: int, data: bytes) -> int:
        written.append(data)
        return len(data)

    monkeypatch.setattr(connect_mod.os, "write", fake_write)
    monkeypatch.setattr(connect_mod, "_notice", lambda _message: None)
    ws = _FakeWS(
        [
            encode_data(7, b"hello", FLAG_SNAPSHOT),
            encode_data(7, b" world", 0),
            json.dumps({"type": "exit", "code": 0}),
        ]
    )
    state: dict[str, Any] = {}
    asyncio.run(_recv_loop(ws, "sid", 80, 24, state))
    assert b"".join(written) == b"hello world"
    # A snapshot is anchored at ``seq``; its byte length is not stream offset.
    # The live frame that follows advances by its real length from that anchor.
    assert state["last_seq"] == 13
    acks = [json.loads(item) for item in ws.sent if isinstance(item, str)]
    assert any(item.get("type") == "ack" and item.get("seq") == 7 for item in acks)


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
    _notice_msg, code, exited, _exit_code = asyncio.run(
        _recv_loop(_FakeWS(messages), "sid", 80, 24, {})
    )
    assert exited and code == 1000
    joined = "\n".join(said)
    assert "输出过快" in joined, "desync must reach the operator"
    assert "只读" in joined, "notices must reach the operator"
    assert "连接已被释放" in joined, "eviction must reach the operator"
    assert "回放未完整" in joined, "an incomplete replay must not look whole"


def test_recv_loop_requests_a_snapshot_on_a_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    from wsctl.cli import connect as connect_mod

    said: list[str] = []
    monkeypatch.setattr(connect_mod, "_notice", said.append)
    ws = _FakeWS(
        [
            json.dumps({"type": "gap", "from": 10, "to": 20, "reason": "shed"}),
            json.dumps({"type": "exit", "code": 0}),
        ]
    )
    asyncio.run(_recv_loop(ws, "sid", 80, 24, {}))
    sent = [json.loads(item) for item in ws.sent if isinstance(item, str)]
    assert any(item.get("type") == "snapshot-request" for item in sent)
    assert any("丢失了字节" in message for message in said)


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
    _notice, _code, exited, _exit_code = asyncio.run(
        _recv_loop(_FakeWS(messages), "sid", 80, 24, {})
    )
    assert exited
    assert said == [], f"a clean attach printed noise: {said}"
