"""Non-blocking audit event writer.

Audit logging must never stall the event loop: nearly every keystroke (with
``audit_input``) and every connection event can produce a record, and a
synchronous SQLite write inside an async handler would add latency to *every*
other session. Events are queued and flushed in batches by a background task,
so the hot path is a single ``put_nowait``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from .store import Store

log = logging.getLogger("wsctl.audit")

BATCH = 256
MAX_QUEUE = 20000


class AuditWriter:
    """Queues audit events and writes them off the event loop."""

    def __init__(self, store: Store, *, max_queue: int = MAX_QUEUE) -> None:
        self._store = store
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_queue)
        self._task: asyncio.Task[None] | None = None
        self.dropped = 0

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        await self.flush()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    def enqueue(
        self,
        event: str,
        *,
        user_id: int | None = None,
        term_session_id: str | None = None,
        payload: str | None = None,
        ip: str | None = None,
    ) -> None:
        """Queue an event without blocking. Drops it if the queue is saturated."""
        record = {
            "event": event,
            "user_id": user_id,
            "term_session_id": term_session_id,
            "payload": payload,
            "ip": ip,
            "ts": time.time(),
        }
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            self.dropped += 1
            if self.dropped == 1 or self.dropped % 1000 == 0:
                log.warning("audit queue full; dropped %d events", self.dropped)

    def _drain_batch(self) -> list[dict[str, Any]]:
        batch: list[dict[str, Any]] = []
        while len(batch) < BATCH:
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return batch

    async def _run(self) -> None:
        while True:
            batch = [await self._queue.get()]
            batch.extend(self._drain_batch())
            await self._write(batch)

    async def _write(self, batch: list[dict[str, Any]]) -> None:
        if not batch:
            return
        # DB write on a worker thread; webhook dispatch back on the loop.
        await asyncio.to_thread(self._store.write_events, batch)
        self._store.dispatch_events(batch)

    async def flush(self) -> None:
        """Write everything currently queued (used before shutdown and by the
        audit API so reads see recent events)."""
        while True:
            batch = self._drain_batch()
            if not batch:
                return
            await self._write(batch)
