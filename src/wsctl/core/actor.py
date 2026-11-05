"""Per-session actor: one mailbox, one serial order.

0.1.x protected session state with an ``asyncio.Lock``. It was correct, but it
left the *order* of client operations dependent on which coroutine happened to
acquire the lock first: an attach could land between two output chunks, a
detach could race a replay, and the reasoning about "did this viewer see this
byte" lived in comments rather than in the code.

0.2.0 gives every session a mailbox. Output, attach, detach and stop are
commands processed by one task, in arrival order. The lock remains as a
defence-in-depth for direct ``TermSession`` use (tests, the legacy endpoint),
but the v2 path never relies on it for ordering.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .session import TermSession


class SessionActor:
    """Serialises all state-changing operations for one terminal session.

    ``__getattr__`` delegates reads (``id``, ``spec``, ``closed``, ...) to the
    underlying session, so existing code that treats a managed session as a
    ``TermSession`` keeps working. Writes that must be ordered go through
    :meth:`_call`.
    """

    def __init__(self, session: TermSession, *, mailbox_size: int = 4096) -> None:
        self.session = session
        self._mailbox: asyncio.Queue[
            tuple[str, tuple[Any, ...], dict[str, Any], asyncio.Future[Any]]
        ] = asyncio.Queue(maxsize=mailbox_size)
        self._worker: asyncio.Task[None] | None = None
        self._closed = False
        self._stopping = False
        # Route the read loop's output through this actor. ``_emit`` is the
        # single hook every byte passes through, so an attach queued in the
        # mailbox cannot observe a half-delivered chunk.
        session._emit = self.emit  # type: ignore[method-assign]
        self._worker = asyncio.get_running_loop().create_task(self._run())

    # -- delegation ----------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        return getattr(self.session, name)

    def __setattr__(self, name: str, value: Any) -> None:
        """Writes to the facade land on the session.

        Tests (and a few call sites) patch ``session.nudge_repaint`` or set an
        internal like ``_repaint_requested_at``. With an actor in front, a
        naive attribute assignment would create a shadow on the actor while the
        real session kept using the old value -- the patch would appear to
        work and change nothing. Forwarding keeps the object contract.
        """
        if name in {"session", "_mailbox", "_worker", "_closed", "_stopping"}:
            object.__setattr__(self, name, value)
        else:
            setattr(self.session, name, value)

    @property
    def mailbox_depth(self) -> int:
        return self._mailbox.qsize()

    # -- mailbox -------------------------------------------------------

    async def _call(self, op: str, *args: Any, **kwargs: Any) -> Any:
        if self._closed:
            raise RuntimeError("session actor is closed")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        await self._mailbox.put((op, args, kwargs, future))
        return await future

    async def _run(self) -> None:
        while True:
            op, args, kwargs, future = await self._mailbox.get()
            if future.cancelled():
                continue
            try:
                result = await self._dispatch(op, *args, **kwargs)
            except asyncio.CancelledError:
                if not future.done():
                    future.cancel()
                raise
            except BaseException as exc:
                if not future.done():
                    future.set_exception(exc)
            else:
                if not future.done():
                    future.set_result(result)
            if op == "stop":
                self._closed = True
                return

    async def _dispatch(self, op: str, *args: Any, **kwargs: Any) -> Any:
        if op == "emit":
            return await self.session._broadcast(args[0])
        if op == "attach":
            return await self.session.attach(*args, **kwargs)
        if op == "attach_v2":
            return await self.session.attach_v2(*args, **kwargs)
        if op == "detach":
            return await self.session.detach(*args, **kwargs)
        if op == "stop":
            return await self.session.stop(*args, **kwargs)
        if op == "resync":
            return await self.session.resync(*args, **kwargs)
        if op == "start_recording":
            return await self.session.start_recording(*args, **kwargs)
        if op == "stop_recording":
            return await self.session.stop_recording()
        raise RuntimeError(f"unknown actor command: {op}")

    # -- public operations --------------------------------------------

    async def emit(self, data: bytes) -> int:
        # ``stop`` runs on the mailbox worker and awaits the read loop, while
        # the read loop delivers its last chunk through ``emit``. Sending that
        # chunk through the same mailbox deadlocks until the stop timeout:
        # the worker is waiting for the read loop, and the read loop is waiting
        # for the worker. During shutdown the ordering guarantee no longer
        # matters (no new attach is admitted), so hand the bytes straight to
        # the session and let it finish.
        if self._stopping or self._closed:
            return int(await self.session._broadcast(data))
        return int(await self._call("emit", data))

    async def attach(self, client: Any, *, writable: bool = True, share: str | None = None) -> None:
        await self._call("attach", client, writable=writable, share=share)

    async def attach_v2(self, client: Any, **kwargs: Any) -> dict[str, Any]:
        return dict(await self._call("attach_v2", client, **kwargs))

    async def detach(self, client: Any) -> None:
        await self._call("detach", client)

    async def stop(self, **kwargs: Any) -> None:
        if self._closed or self._stopping:
            return
        self._stopping = True
        try:
            await self._call("stop", **kwargs)
        finally:
            self._closed = True

    async def resync(self, client: Any, *, requested: bool = True) -> dict[str, Any]:
        return dict(await self._call("resync", client, requested=requested))

    async def start_recording(self, path: Any, *, record_input: bool = False) -> Any:
        return await self._call("start_recording", path, record_input=record_input)

    async def stop_recording(self) -> None:
        await self._call("stop_recording")


__all__ = ["SessionActor"]
