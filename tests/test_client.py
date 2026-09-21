from __future__ import annotations

import asyncio
from typing import Any

import pytest

from wsctl.core.session import ClientGone
from wsctl.server.client import WsClient


class _StubWs:
    async def send_bytes(self, data: bytes) -> None:
        pass

    async def send_json(self, data: Any) -> None:
        pass


def test_byte_cap_rejects_overflow() -> None:
    client = WsClient(_StubWs(), max_bytes=10)  # type: ignore[arg-type]
    client.put(b"12345")
    client.put(b"6789")
    assert client.pending_bytes == 9
    with pytest.raises(ClientGone):
        client.put(b"xx")


def test_queue_limit_rejects_overflow() -> None:
    client = WsClient(_StubWs(), max_pending=1)  # type: ignore[arg-type]
    client.put(b"a")
    with pytest.raises(ClientGone):
        client.put(b"b")


async def test_pending_drains_after_send() -> None:
    client = WsClient(_StubWs(), max_bytes=100)  # type: ignore[arg-type]
    client.put(b"abc")
    assert client.pending_bytes == 3
    task = asyncio.create_task(client.run())
    await asyncio.sleep(0.05)
    assert client.pending_bytes == 0
    client.close()
    await task
