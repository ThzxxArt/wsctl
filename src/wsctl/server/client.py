"""Adapts a Starlette WebSocket to the :class:`~wsctl.core.session.Client` protocol."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import WebSocket

from wsctl.core.session import ClientGone

MAX_PENDING = 512


class WsClient:
    """Ordered, bounded outbound queue for one WebSocket connection.

    ``bytes`` items become binary frames (terminal data); ``dict`` items become
    JSON text frames (control messages). A full queue means the client cannot
    keep up, so it is dropped: it can reconnect and receive a scrollback replay.
    """

    def __init__(self, websocket: WebSocket, *, max_pending: int = MAX_PENDING) -> None:
        self._ws = websocket
        self._queue: asyncio.Queue[bytes | dict[str, Any] | None] = asyncio.Queue(
            maxsize=max_pending
        )
        self._closed = False

    def put(self, item: bytes | dict[str, Any]) -> None:
        if self._closed:
            raise ClientGone("client closed")
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            self._closed = True
            raise ClientGone("client backlog overflow") from None

    def close(self) -> None:
        if self._closed:
            return
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
                else:
                    await self._ws.send_json(item)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._closed = True
