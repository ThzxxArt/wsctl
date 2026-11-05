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
