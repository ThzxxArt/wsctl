from __future__ import annotations

import asyncio

import pytest

from wsctl.core.actor import SessionActor


class _SlowStopSession:
    """A session whose ``stop`` stays in flight until the test releases it."""

    id = "slow-stop"

    def __init__(self) -> None:
        self.stopping = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    async def stop(self, **_kwargs: object) -> None:
        self.stopping.set()
        await self.release.wait()
        self.closed = True

    async def detach(self, _client: object) -> None:
        pass

    async def _broadcast(self, _data: bytes) -> int:
        return 0


async def test_detach_queued_during_stop_is_failed_instead_of_hanging() -> None:
    """A viewer that detaches while ``stop`` is running must not wait forever.

    The WebSocket endpoint detaches in its ``finally``. If the session is being
    stopped, ``TermSession.closed`` can already be true while the actor worker
    is still awaiting the PTY read loop. A detach queued in that window used to
    sit in the mailbox after the worker exited on ``stop``; the endpoint then
    never reached ``websocket.close()`` and the browser saw a dead socket.
    """
    session = _SlowStopSession()
    actor = SessionActor(session)  # type: ignore[arg-type]

    stop = asyncio.create_task(actor.stop())
    await asyncio.wait_for(session.stopping.wait(), timeout=1.0)

    detach = asyncio.create_task(actor.detach(object()))
    await asyncio.sleep(0)  # let detach enqueue behind the in-flight stop

    session.release.set()
    await asyncio.wait_for(stop, timeout=1.0)
    with pytest.raises(RuntimeError, match="actor is closed"):
        await asyncio.wait_for(detach, timeout=1.0)

    assert actor._worker is not None
    await asyncio.wait_for(actor._worker, timeout=1.0)


class _ResyncSession:
    id = "resync"

    def __init__(self) -> None:
        self.calls: list[tuple[object, bool, int | None]] = []

    async def resync(
        self, client: object, *, requested: bool = True, last_seq: int | None = None
    ) -> dict[str, object]:
        self.calls.append((client, requested, last_seq))
        return {"format": "raw", "seq": 1, "source": "ring", "incomplete": False}

    async def stop(self, **_kwargs: object) -> None:
        pass


async def test_resync_forwards_last_seq_to_the_session() -> None:
    """The v2 pump passes ``last_seq`` on every repair; the actor must too.

    Dropping the keyword raised ``TypeError`` inside the WebSocket endpoint,
    which closed the connection with 4500 and made every resync button a dead
    end.
    """
    session = _ResyncSession()
    actor = SessionActor(session)  # type: ignore[arg-type]
    client = object()

    result = await actor.resync(client, requested=False, last_seq=42)
    assert result["seq"] == 1
    assert session.calls == [(client, False, 42)]

    await asyncio.wait_for(actor.stop(), timeout=1.0)
