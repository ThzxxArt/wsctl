"""Adapts a Starlette WebSocket to the :class:`~wsctl.core.session.Client` protocol."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import WebSocket

from wsctl.core.closecodes import CLOSE_SLOW_CONSUMER
from wsctl.core.session import ClientGone

MAX_PENDING = 512


class WsClient:
    """Ordered, bounded outbound queue for one WebSocket connection.

    ``bytes`` items become binary frames (terminal data); ``dict`` items become
    JSON text frames (control messages). A full queue means the client cannot
    keep up, so it is dropped: it can reconnect and receive a scrollback replay.
    """

    def __init__(
        self,
        websocket: WebSocket,
        *,
        max_pending: int = MAX_PENDING,
        max_bytes: int = 0,
    ) -> None:
        self._ws = websocket
        self._queue: asyncio.Queue[bytes | dict[str, Any] | None] = asyncio.Queue(
            maxsize=max_pending
        )
        self._max_bytes = max_bytes
        self._pending_bytes = 0
        self._closed = False
        # Why this client died, when it decided for itself. The endpoint has
        # to report it: closing with 1000 ("normal closure") after silently
        # dropping a slow viewer is what made the browser think everything was
        # fine, reconnect, clear its terminal -- and leave the user on a black
        # screen with nothing to read.
        self.close_code: int | None = None

    @property
    def pending_bytes(self) -> int:
        return self._pending_bytes

    @property
    def closed(self) -> bool:
        return self._closed

    def put(self, item: bytes | dict[str, Any]) -> None:
        if self._closed:
            raise ClientGone("连接已关闭")
        if (
            isinstance(item, bytes)
            and self._max_bytes > 0
            and self._pending_bytes + len(item) > self._max_bytes
        ):
            self._closed = True
            self.close_code = CLOSE_SLOW_CONSUMER
            raise ClientGone("客户端缓冲超出上限")
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            self._closed = True
            self.close_code = CLOSE_SLOW_CONSUMER
            raise ClientGone("客户端积压溢出") from None
        if isinstance(item, bytes):
            self._pending_bytes += len(item)

    def final_notice(self, message: dict[str, Any]) -> bool:
        """Enqueue one last control message even though the client is gone.

        A client dropped for being too slow (queue full / byte budget blown)
        previously vanished without a word: the socket closed with 1000, the
        browser assumed a clean shutdown, reconnected, cleared its terminal to
        make room for the replay -- and the user was left looking at a black
        screen with no explanation.

        Control messages are small and are *not* counted against the byte
        budget, so the one thing the user needs to read is the one thing that
        still fits. Returns whether it was queued.

        Call this **before** :meth:`close`: ``close`` queues the sentinel that
        ends the writer, and anything after it is never sent.
        """
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            return False
        return True

    def close(self) -> None:
        """Stop the writer.

        Must terminate the writer even when the client already marked itself
        closed (that is exactly the slow-consumer path). Returning early left
        ``run()`` blocked on ``queue.get()`` forever, so every dropped client
        cost a two-second ``wait_for`` timeout in the connection teardown --
        and anything queued after that point was never sent.
        """
        self._closed = True
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(None)

    async def run(self) -> None:
        try:
            while True:
                item = await self._queue.get()
                if item is None:
                    return
                if isinstance(item, bytes):
                    await self._ws.send_bytes(item)
                    self._pending_bytes = max(0, self._pending_bytes - len(item))
                else:
                    await self._ws.send_json(item)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._closed = True
