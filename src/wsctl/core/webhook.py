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
    def __init__(
        self,
        url: str,
        *,
        timeout: float = 10.0,
        max_queue: int = 1000,
        max_attempts: int = 3,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.max_attempts = max_attempts
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_queue)
        self._task: asyncio.Task[None] | None = None
        self._stop = False

    def start(self) -> None:
        if self._task is None:
            self._stop = False
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self, *, drain_timeout: float = 5.0) -> None:
        """Stop delivering -- but only after the queue has had its say.

        ``app``'s shutdown comment promises "flush queued audit events (and
        deliver them to the webhook) before tearing the webhook down". A bare
        ``cancel()`` did not: the events ``audit.stop()`` had just dispatched
        were sitting in this queue, and the cancel dropped them -- the final
        audit events of a run, exactly the ones an operator is most likely to
        want in the SIEM.
        """
        if self._task is None:
            return
        task, self._task = self._task, None
        # Then give the runner a bounded window to drain. (Intake is *not*
        # closed here: ``app`` calls ``audit.stop()`` first, which is exactly
        # what pushes the final events into this queue.)
        self._stop = True
        with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), timeout=drain_timeout)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    def emit(self, payload: dict[str, Any]) -> None:
        try:
            self._queue.put_nowait(payload)
        except asyncio.QueueFull:
            log.warning("webhook queue full, dropping event %s", payload.get("event"))

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                payload = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except TimeoutError:
                if self._stop and self._queue.empty():
                    return
                continue
            for attempt in range(1, self.max_attempts + 1):
                try:
                    await loop.run_in_executor(None, self._post, payload)
                    break
                except Exception:
                    if attempt >= self.max_attempts:
                        log.exception(
                            "webhook delivery failed after %d attempts", attempt
                        )
                    else:
                        # Exponential backoff, capped, without blocking the loop.
                        await asyncio.sleep(min(0.5 * 2 ** (attempt - 1), 5.0))

    def _post(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # ``urlopen`` follows redirects unconditionally, so a compromised (or
        # simply misconfigured) endpoint could bounce the delivery at an
        # internal address. No redirects: the configured URL is the only place
        # audit events may go.
        opener = urllib.request.build_opener(_NoRedirect)
        with opener.open(request, timeout=self.timeout) as response:
            response.read()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None
