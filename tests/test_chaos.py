from __future__ import annotations

import pytest

from wsctl.core.session import SessionManager, SessionSpec
from wsctl.server.client import WsClient

SHELL = "/bin/sh"


@pytest.mark.asyncio
async def test_a_slow_viewer_gets_a_gap_not_a_disconnect() -> None:
    """The chaos contract: shedding is reported, the session survives."""

    class SlowSocket:
        def __init__(self) -> None:
            self.sent: list[bytes] = []

        async def send_bytes(self, data: bytes) -> None:
            # Never actually drain; the client queue is what we exercise.
            self.sent.append(data)

        async def send_text(self, data: str) -> None:
            self.sent.append(data.encode("utf-8"))

        async def close(self, code: int = 1000) -> None:
            return None

    manager = SessionManager()
    session = await manager.create(SessionSpec(name="slow", argv=[SHELL]))
    client = WsClient(SlowSocket(), max_pending=4, max_bytes=512, protocol_version=2)  # type: ignore[arg-type]
    try:
        await session.attach_v2(client)
        for _index in range(50):
            await session._broadcast(b"Z" * 64)
        controls = client.queued_controls()
        gap = next((item for item in controls if item.get("type") == "gap"), None)
        assert gap is not None, "a shed burst must produce an exact gap"
        assert gap["from"] < gap["to"]
        assert session.closed is False
        assert session.client_count == 1
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_generation_mismatch_forces_a_snapshot() -> None:
    manager = SessionManager()
    session = await manager.create(
        SessionSpec(name="gen", argv=[SHELL], snapshot_mode="always")
    )

    class Sink:
        def __init__(self) -> None:
            self.items: list[object] = []

        def put(self, item: object) -> None:
            self.items.append(item)

        def put_data(self, seq: int, payload: bytes, flags: int = 0) -> None:
            self.items.append((seq, payload, flags))

        dropped_events = 0

    try:
        sink = Sink()
        meta = await session.attach_v2(
            sink, last_seq=0, generation="a-generation-from-another-process"
        )
        assert meta["snapshot_required"] is True
        assert meta["snapshot_applied"] in (True, False)
    finally:
        await manager.shutdown()
