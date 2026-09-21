from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

from wsctl.core.webhook import WebhookDispatcher


class _Handler(BaseHTTPRequestHandler):
    received: ClassVar[list[dict[str, object]]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", 0))
        body = self.rfile.read(length)
        _Handler.received.append(json.loads(body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


async def test_delivers_event() -> None:
    _Handler.received = []
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    dispatcher = WebhookDispatcher(f"http://127.0.0.1:{port}/hook")
    dispatcher.start()
    try:
        dispatcher.emit({"event": "session_create", "user_id": 1})
        for _ in range(100):
            if _Handler.received:
                break
            await asyncio.sleep(0.05)
    finally:
        await dispatcher.stop()
        server.shutdown()

    assert _Handler.received
    assert _Handler.received[0]["event"] == "session_create"
    assert _Handler.received[0]["user_id"] == 1


async def test_stop_is_safe_without_start() -> None:
    dispatcher = WebhookDispatcher("http://127.0.0.1:1/hook")
    await dispatcher.stop()
