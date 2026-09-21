"""``wsctl connect`` — attach a local terminal to a remote wsctl server.

The client puts the local terminal into raw mode and bridges stdin/stdout to
the server's WebSocket protocol (binary frames for terminal bytes, JSON text
frames for control). Reconnecting is a server-side feature, so a dropped link
ends the local session cleanly.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
from urllib.parse import urlsplit, urlunsplit

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

from wsctl.cli.client import ApiClient, ApiError, load_credentials

DEFAULT_SIZE = (80, 24)
READ_SIZE = 4096


class ConnectError(RuntimeError):
    """Raised when the local client cannot establish or maintain a link."""


def run_connect(url: str | None, session: str | None, token: str | None) -> None:
    creds = load_credentials()
    base = url or (str(creds["url"]) if creds.get("url") else None)
    if not base:
        raise ConnectError("no server URL: pass <url> or run 'wsctl login <url>' first")
    bearer = token or (str(creds["token"]) if creds.get("token") else None)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run(base, session, bearer))


def _ws_url(base: str) -> str:
    parsed = urlsplit(base)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = parsed.path.rstrip("/") + "/ws"
    return urlunsplit((scheme, parsed.netloc, path, "", ""))


def _winsize() -> tuple[int, int]:
    try:
        size = os.get_terminal_size(sys.stdin.fileno())
        return size.columns, size.lines
    except OSError:
        return DEFAULT_SIZE


def _create_session(base: str, token: str | None) -> str:
    client = ApiClient(base, token)
    try:
        info = client.request("POST", "/api/sessions", {})
    except ApiError as exc:
        raise ConnectError(f"cannot create a session: {exc}") from exc
    return str(info["id"])


def _notice(message: str) -> None:
    sys.stderr.write(f"\r\n\x1b[33m[wsctl] {message}\x1b[0m\r\n")
    sys.stderr.flush()


async def _run(base: str, session: str | None, token: str | None) -> None:
    if sys.platform == "win32":
        raise ConnectError("wsctl connect currently requires a POSIX terminal")
    if not sys.stdin.isatty():
        raise ConnectError("wsctl connect requires an interactive terminal")

    session_id = session or _create_session(base, token)
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    try:
        ws = await websockets.connect(_ws_url(base), additional_headers=headers, max_size=None)
    except InvalidStatus as exc:
        code = exc.response.status_code
        hint = " (is the token valid?)" if code in (401, 403) else ""
        raise ConnectError(f"server rejected the connection: HTTP {code}{hint}") from exc
    except OSError as exc:
        raise ConnectError(f"cannot reach {base}: {exc}") from exc

    import termios
    import tty

    async with ws:
        cols, rows = _winsize()
        await ws.send(
            json.dumps({"type": "attach", "session": session_id, "cols": cols, "rows": rows})
        )

        loop = asyncio.get_running_loop()
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        tty.setraw(fd)

        outbound: asyncio.Queue[bytes | str | None] = asyncio.Queue()

        def on_stdin() -> None:
            try:
                data = os.read(fd, READ_SIZE)
            except OSError:
                data = b""
            outbound.put_nowait(data if data else None)

        def on_resize() -> None:
            width, height = _winsize()
            outbound.put_nowait(json.dumps({"type": "resize", "cols": width, "rows": height}))

        loop.add_reader(fd, on_stdin)
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signal.SIGWINCH, on_resize)

        async def sender() -> None:
            while True:
                item = await outbound.get()
                if item is None:
                    await ws.close()
                    return
                await ws.send(item)

        send_task = asyncio.create_task(sender())
        notice: str | None = None
        try:
            async for message in ws:
                if isinstance(message, bytes):
                    os.write(sys.stdout.fileno(), message)
                    continue
                data = json.loads(message)
                kind = data.get("type")
                if kind == "exit":
                    notice = f"session exited (code {data.get('code')})"
                    break
                if kind == "error":
                    notice = str(data.get("msg"))
                    break
        except ConnectionClosed:
            notice = "connection closed"
        finally:
            send_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await send_task
            loop.remove_reader(fd)
            with contextlib.suppress(NotImplementedError):
                loop.remove_signal_handler(signal.SIGWINCH)
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)

        if notice:
            _notice(notice)
