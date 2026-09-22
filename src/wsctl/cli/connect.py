"""``wsctl connect`` — attach a local terminal to a remote wsctl server.

The client puts the local terminal into raw mode and bridges stdin/stdout to
the server's WebSocket protocol (binary frames for terminal bytes, JSON text
frames for control). Because the *session* lives on the server, a dropped link
is recoverable: the client reconnects with exponential backoff, re-attaches to
the same session id and lets the server replay the screen.
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
INITIAL_DELAY = 0.5
MAX_DELAY = 30.0
PING_INTERVAL = 25.0

# Close codes the server uses for permanent failures; retrying cannot help.
FATAL_CODES = frozenset({4400, 4401, 4403, 4404, 4409, 4500})


class ConnectError(RuntimeError):
    """Raised when the local client cannot establish or maintain a link."""


def run_connect(
    url: str | None, session: str | None, token: str | None, *, reconnect: bool = True
) -> None:
    creds = load_credentials()
    base = url or (str(creds["url"]) if creds.get("url") else None)
    if not base:
        raise ConnectError("缺少服务器地址：请传入 <url>，或先运行 'wsctl login <url>'")
    bearer = token or (str(creds["token"]) if creds.get("token") else None)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run(base, session, bearer, reconnect=reconnect))


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
        raise ConnectError(f"无法创建会话：{exc}") from exc
    return str(info["id"])


def _notice(message: str) -> None:
    sys.stderr.write(f"\r\n\x1b[33m[wsctl] {message}\x1b[0m\r\n")
    sys.stderr.flush()


async def _run(base: str, session: str | None, token: str | None, *, reconnect: bool) -> None:
    if sys.platform == "win32":
        raise ConnectError("wsctl connect 目前需要 POSIX 终端")
    if not sys.stdin.isatty():
        raise ConnectError("wsctl connect 需要交互式终端")

    session_id = session or _create_session(base, token)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    ws_url = _ws_url(base)

    import termios
    import tty

    loop = asyncio.get_running_loop()
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    outbound: asyncio.Queue[bytes | str | None] = asyncio.Queue()
    state = {"stdin_closed": False}

    def on_stdin() -> None:
        try:
            data = os.read(fd, READ_SIZE)
        except OSError:
            data = b""
        if not data:
            state["stdin_closed"] = True
        outbound.put_nowait(data if data else None)

    def on_resize() -> None:
        width, height = _winsize()
        outbound.put_nowait(json.dumps({"type": "resize", "cols": width, "rows": height}))

    tty.setraw(fd)
    loop.add_reader(fd, on_stdin)
    with contextlib.suppress(NotImplementedError):
        loop.add_signal_handler(signal.SIGWINCH, on_resize)
    try:
        await _session_loop(
            ws_url, headers, session_id, outbound, state, reconnect=reconnect
        )
    finally:
        with contextlib.suppress(Exception):
            loop.remove_reader(fd)
        with contextlib.suppress(NotImplementedError):
            loop.remove_signal_handler(signal.SIGWINCH)
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def _drain(queue: asyncio.Queue[bytes | str | None]) -> None:
    """Drop items queued for a connection that no longer exists."""
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return


async def _recv_loop(
    ws: object, session_id: str, cols: int, rows: int
) -> tuple[str | None, int | None, bool]:
    """Attach and pump output. Returns ``(notice, close_code, exited)``."""
    await ws.send(  # type: ignore[attr-defined]
        json.dumps({"type": "attach", "session": session_id, "cols": cols, "rows": rows})
    )
    notice: str | None = None
    try:
        while True:
            message = await ws.recv()  # type: ignore[attr-defined]
            if isinstance(message, bytes):
                os.write(sys.stdout.fileno(), message)
                continue
            # A proxy or middlebox can inject non-JSON text frames; ignoring
            # them keeps the terminal alive instead of tearing the link down.
            try:
                data = json.loads(message)
            except (TypeError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            kind = data.get("type")
            if kind == "exit":
                return f"会话已退出（退出码 {data.get('code')}）", 1000, True
            if kind == "error":
                # The reason may be followed by a fatal close; keep reading to
                # learn the close code before deciding whether to retry.
                notice = str(data.get("msg"))
    except ConnectionClosed as exc:
        code: int | None = None
        if exc.rcvd is not None:
            code = exc.rcvd.code
        elif exc.sent is not None:
            code = exc.sent.code
        return notice, code, False


async def _session_loop(
    ws_url: str,
    headers: dict[str, str],
    session_id: str,
    outbound: asyncio.Queue[bytes | str | None],
    state: dict[str, bool],
    *,
    reconnect: bool,
) -> None:
    delay = INITIAL_DELAY
    while True:
        try:
            ws = await websockets.connect(ws_url, additional_headers=headers, max_size=None)
        except InvalidStatus as exc:
            status = exc.response.status_code
            hint = "（令牌是否有效？）" if status in (401, 403) else ""
            raise ConnectError(f"服务器拒绝连接：HTTP {status}{hint}") from exc
        except OSError as exc:
            if not reconnect:
                raise ConnectError(f"无法连接 {ws_url}：{exc}") from exc
            _notice(f"无法连接：{exc}；{delay:.0f}s 后重试")
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_DELAY)
            continue

        _drain(outbound)  # discard input meant for the previous connection
        cols, rows = _winsize()
        async with ws:
            async def sender(conn: object = ws) -> None:
                while True:
                    item = await outbound.get()
                    if item is None:
                        with contextlib.suppress(Exception):
                            await conn.close()  # type: ignore[attr-defined]
                        return
                    try:
                        await conn.send(item)  # type: ignore[attr-defined]
                    except ConnectionClosed:
                        return

            async def pinger(conn: object = ws) -> None:
                # Keeps the connection from being seen as idle/half-open.
                while True:
                    await asyncio.sleep(PING_INTERVAL)
                    try:
                        await conn.send(json.dumps({"type": "ping"}))  # type: ignore[attr-defined]
                    except ConnectionClosed:
                        return

            send_task = asyncio.create_task(sender())
            ping_task = asyncio.create_task(pinger())
            try:
                notice, code, exited = await _recv_loop(ws, session_id, cols, rows)
            finally:
                send_task.cancel()
                ping_task.cancel()
                for task in (send_task, ping_task):
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task

        if notice:
            _notice(notice)
        if exited or state["stdin_closed"] or not reconnect or code in FATAL_CODES:
            return
        _notice(f"连接断开；{delay:.0f}s 后重连…")
        await asyncio.sleep(delay)
        delay = min(delay * 2, MAX_DELAY)
