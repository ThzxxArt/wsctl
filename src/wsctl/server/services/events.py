"""In-process event broker for Server-Sent Events.

Terminal bytes never travel over SSE: they belong to the WebSocket protocol,
where they have sequence numbers and backpressure. SSE carries *state* --
session created/ended, share changed, audit maintenance, presence -- which is
small, low-rate, and exactly what a sidebar can consume without holding a
socket per tab.

The broker is deliberately lossy for slow subscribers: a browser that cannot
keep up with state events must not be able to grow an unbounded queue inside
the server. Subscribers get a bounded queue and are disconnected when it
overflows; they reconnect and resync from the snapshot endpoints.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from typing import Any

MAX_QUEUE = 256


class EventBroker:
    def __init__(self, *, max_queue: int = MAX_QUEUE) -> None:
        self._max_queue = max_queue
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()
        self._sequence = 0

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._max_queue)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    def publish(self, event: str, **data: Any) -> None:
        """Publish without blocking. A full subscriber is dropped."""
        self._sequence += 1
        message = {
            "v": 2,
            "event": event,
            "seq": self._sequence,
            "ts": time.time(),
            **data,
        }
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # Tell the slow subscriber to reconnect and resync rather than
                # leaving it connected to a queue nobody feeds: it used to be
                # removed from the set silently, so the browser saw only
                # heartbeats and never learned its state feed had stopped.
                self._subscribers.discard(queue)
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(
                        {
                            "v": 2,
                            "event": "overflow",
                            "seq": self._sequence,
                            "ts": time.time(),
                        }
                    )

    async def stream(
        self, queue: asyncio.Queue[dict[str, Any]]
    ) -> AsyncIterator[dict[str, Any]]:
        try:
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=30.0)
                except TimeoutError:
                    yield {"v": 2, "event": "heartbeat", "ts": time.time()}
                    continue
                if message.get("event") == "overflow":
                    # The subscriber fell behind and was removed from the
                    # broadcast set. End the response so EventSource reconnects
                    # and resyncs from the snapshot endpoints instead of
                    # staring at a connection that will never carry state again.
                    yield message
                    return
                yield message
        finally:
            await self.unsubscribe(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


def format_sse(message: dict[str, Any]) -> str:
    """Encode one broker message as an SSE ``data:`` line pair."""
    event = message.get("event", "message")
    payload = json.dumps(message, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


__all__ = ["EventBroker", "format_sse"]
