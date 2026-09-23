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
    assert len(client.queued_controls()) <= MAX_CONTROL_PENDING + 1, (
        f"control frames are unbounded: {len(client.queued_controls())}"
    )


def test_shedding_reaches_a_sequence_boundary_before_stopping(tmp_path) -> None:
    """Eviction must keep dropping until the cut lands *outside* a sequence.

    A PTY read can cut ``ESC[31m`` in half. Dropping the first half on its own
    leaves the stream to resume at ``1m...``, which a terminal reads as literal
    text -- that is what made ``vi``/``htop`` render as garbage after a shed.

    The budget below is built so that **freeing one frame's worth of room is
    already enough**. Dropping only the partial sequence satisfies the byte cap
    while leaving the cut inside ``ESC[3``; a correct eviction keeps going to
    the next frame, which is the one that ends outside. That single extra drop
    is the whole contract, and this input is what makes it observable: with it,
    "drop one frame and stop" and "drop until a boundary" end with different
    heads.
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
    # The queue-length cap is what triggers the eviction here, deliberately:
    # the byte cap has a second "does not fit, drop *this* frame" escape hatch
    # in ``put`` that would eat the pressure before eviction ever ran, and the
    # point of the test is what eviction does.
    client = WsClient(ws, max_pending=4, max_bytes=0)  # type: ignore[arg-type]
    client.put(b"\x1b[3")     # ends INSIDE a CSI
    client.put(b"1mABC")     # completes it, then plain text
    client.put(b"Z" * 40)    # plain output
    client.put(b"Q")
    client.put(b"R")         # 5th frame: exactly one must go

    kept = client.queued_binary()
    assert client.dropped_bytes > 0, "this test only means something if we shed"
    # "drop one frame and stop" would leave `1mABC` at the head -- the tail of
    # a colour sequence, about to be read as text. A boundary-aligned cut puts
    # the plain output first.
    assert kept.startswith(b"Z"), (
        f"the cut did not reach a sequence boundary; the surviving stream opens {kept[:8]!r}"
    )
    assert is_boundary_aligned(kept)


def test_a_dropped_frame_still_advances_the_sequence_tracker(tmp_path) -> None:
    """Dropping a frame must not desynchronise where the *next* cut may land.

    The streaming tracker models the source stream. A frame discarded under
    pressure still moves the parser along -- the next kept frame begins
    wherever that one would have left off. The bookkeeping kept per *queued*
    frame has to reflect that, or a later cut is judged against a state the
    stream is not in.
    """
    class Capture:
        async def send_bytes(self, data: bytes) -> None:
            pass

        async def send_json(self, obj: object) -> None:  # pragma: no cover
            pass

    client = WsClient(Capture(), max_pending=512, max_bytes=20)  # type: ignore[arg-type]
    # Frame 2 is discarded entirely by the byte cap (nothing left to evict and
    # it still does not fit), and it is the one that *completes* the sequence
    # frame 1 opened.
    client.put(b"\x1b[3")          # ends inside
    client.put(b"1m" + b"A" * 20)  # discarded whole
    client.put(b"PLAIN")           # must be judged as starting outside
    queued = list(client._binary)
    assert queued, "the surviving frames must still be there"
    _seq, payload, ends_inside = queued[-1]
    assert payload == b"PLAIN"
    assert ends_inside is False, (
        "the tracker was not advanced by the discarded frame: the state after "
        "PLAIN is being computed from a stream that never saw the sequence tail"
    )


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
