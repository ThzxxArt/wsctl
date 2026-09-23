from __future__ import annotations

import asyncio
from typing import Any

from wsctl.server.client import WsClient


class _StubWs:
    async def send_bytes(self, data: bytes) -> None:
        pass

    async def send_json(self, data: Any) -> None:
        pass


def test_byte_cap_sheds_oldest_instead_of_killing_the_link() -> None:
    """Over budget sheds the oldest frames -- it does not drop the viewer.

    The old contract raised ``ClientGone`` here. That turned a merely slow
    viewer into a dropped one, and the reconnect/clear/replay cycle it started
    is what an operator saw as a terminal that "froze and jumped".
    """
    client = WsClient(_StubWs(), max_bytes=10)  # type: ignore[arg-type]
    client.put(b"12345")
    client.put(b"6789")
    assert client.pending_bytes == 9
    client.put(b"xx")
    assert client.closed is False
    assert client.close_code is None
    assert client.dropped_bytes > 0, "the overflow must be shed"
    assert client.pending_bytes <= 10


def test_queue_limit_sheds_oldest_instead_of_killing_the_link() -> None:
    client = WsClient(_StubWs(), max_pending=1)  # type: ignore[arg-type]
    client.put(b"a")
    client.put(b"b")
    assert client.closed is False
    assert client.dropped_bytes == 1, "exactly the oldest frame should go"


async def test_pending_drains_after_send() -> None:
    client = WsClient(_StubWs(), max_bytes=100)  # type: ignore[arg-type]
    client.put(b"abc")
    assert client.pending_bytes == 3
    task = asyncio.create_task(client.run())
    await asyncio.sleep(0.05)
    assert client.pending_bytes == 0
    client.close()
    await task


def test_control_frames_are_bounded_too() -> None:
    """Control frames skip the byte budget, not the bound.

    Shedding bytes keeps a slow viewer alive; letting control frames pile up
    without limit is just another way to take the connection down, and the
    first rewrite of this class lost the bound the queue used to impose on
    every frame.
    """
    from wsctl.server.client import MAX_CONTROL_PENDING

    client = WsClient(_StubWs(), max_pending=8, max_bytes=100)  # type: ignore[arg-type]
    for i in range(5 * MAX_CONTROL_PENDING):
        client.put({"type": "noise", "n": i})
    assert client.closed is False, "a flood of control frames must not close the link"
    assert len(client._items) <= MAX_CONTROL_PENDING + 1, (
        f"control frames are unbounded: {len(client._items)}"
    )


def test_shedding_lands_on_an_ansi_boundary(tmp_path) -> None:
    """A dropped frame must not leave the next one starting mid-escape.

    A PTY read can cut ``ESC[31m`` in half. Dropping the first half in
    isolation leaves the stream starting at ``31m``, which a terminal reads as
    literal text -- that is what made ``vi``/``htop`` render as garbage after a
    shed. Whatever survives must start where a terminal considers fresh.
    """
    from wsctl.core.ansi import is_boundary_aligned

    class Capture:
        def __init__(self) -> None:
            self.parts: list[bytes] = []

        async def send_bytes(self, data: bytes) -> None:
            self.parts.append(data)

        async def send_json(self, obj: object) -> None:  # pragma: no cover
            pass

    ws = Capture()
    client = WsClient(ws, max_pending=6, max_bytes=60)  # type: ignore[arg-type]
    red = b"\x1b[31m"
    split = [red[:3], red[3:] + b"AAAA", b"\x1b[0m" + b"BBBB", b"plainCCCC"]
    for part in split * 20:
        client.put(part)

    kept = b"".join(i for i in client._items if isinstance(i, bytes))
    assert client.dropped_bytes > 0, "this test only means something if we shed"
    assert is_boundary_aligned(kept), "the surviving stream starts mid-escape"


def test_shed_oldest_gives_up_backlog_before_a_viewer(tmp_path) -> None:
    """A session over its own memory cap sheds backlog, not people."""
    client = WsClient(_StubWs(), max_pending=64, max_bytes=10_000)  # type: ignore[arg-type]
    for _ in range(200):
        client.put(b"w" * 200)
    before = client.pending_bytes
    assert before > 0
    gone = client.shed_oldest(before // 2)
    assert gone > 0, "backlog must actually be given up"
    assert client.pending_bytes < before
    assert client.closed is False, "shedding backlog must never close the link"
