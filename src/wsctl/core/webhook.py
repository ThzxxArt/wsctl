"""Outbound webhook delivery for audit events.

Events are queued and POSTed as JSON by a background task so logging never
blocks on network I/O. Delivery failures are logged and dropped.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import urllib.request
from typing import Any

log = logging.getLogger("wsctl.webhook")


class WebhookDispatcher:
    def __init__(self, url: str, *, timeout: float = 10.0, max_queue: int = 1000) -> None:
        self.url = url
        self.timeout = timeout
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_queue)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    def emit(self, payload: dict[str, Any]) -> None:
        try:
            self._queue.put_nowait(payload)
        except asyncio.QueueFull:
            log.warning("webhook queue full, dropping event %s", payload.get("event"))

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            payload = await self._queue.get()
            try:
                await loop.run_in_executor(None, self._post, payload)
            except Exception:
                log.exception("webhook delivery failed")

    def _post(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            response.read()
