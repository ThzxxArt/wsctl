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
import ssl
import sys
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

from wsctl.cli.client import ApiClient, ApiError, ca_bundle_for, load_credentials

# Imported, never re-listed: the fatal set used to be maintained in three
# places and 4401 was fatal here but not in the browser.
from wsctl.core.closecodes import FATAL_CLOSE_CODES as FATAL_CODES
from wsctl.core.protocol import (
    FLAG_SNAPSHOT,
    SUBPROTOCOL,
    DataFrame,
    ProtocolError,
    decode_data,
    encode_control,
    encode_input,
)

DEFAULT_SIZE = (80, 24)
READ_SIZE = 4096
INITIAL_DELAY = 0.5
MAX_DELAY = 30.0
PING_INTERVAL = 25.0



class ConnectError(RuntimeError):
    """Raised when the local client cannot establish or maintain a link."""


def run_connect(
    url: str | None, session: str | None, token: str | None, *, reconnect: bool = True
) -> int:
    """Bridge the local terminal to a remote session; return a process exit code.

    A fatal close (revoked login, forbidden, missing session, server error) is
    a *failure* and must say so through the exit status. Returning normally
    made ``wsctl session attach … && echo ok`` print "ok" after an
    authentication failure -- a script could not tell success from "the
    server threw me out".
    """
    creds = load_credentials()
    base = url or (str(creds["url"]) if creds.get("url") else None)
    if not base:
        raise ConnectError("缺少服务器地址：请传入 <url>，或先运行 'wsctl login <url>'")
    bearer = token or (str(creds["token"]) if creds.get("token") else None)
    code = 0
    with contextlib.suppress(KeyboardInterrupt):
        code = asyncio.run(_run(base, session, bearer, reconnect=reconnect))
    return code


def _ws_url(base: str) -> str:
    parsed = urlsplit(base)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = parsed.path.rstrip("/") + "/ws/v2"
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


async def _run(base: str, session: str | None, token: str | None, *, reconnect: bool) -> int:
    if sys.platform == "win32":
        raise ConnectError("wsctl connect 目前需要 POSIX 终端")
    if not sys.stdin.isatty():
        raise ConnectError("wsctl connect 需要交互式终端")

    session_id = session or await asyncio.to_thread(_create_session, base, token)
    assert session_id is not None
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    ws_url = _ws_url(base)

    import termios
    import tty

    loop = asyncio.get_running_loop()
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    outbound: asyncio.Queue[bytes | str | None] = asyncio.Queue()
    state: dict[str, Any] = {
        "stdin_closed": False,
        "last_seq": None,
        "generation": None,
        #: Set after a loss signal until a snapshot restores contiguity. Live
        #: frames still paint, but must not advance the resume offset.
        "awaiting_snapshot": False,
        "attached": asyncio.Event(),
    }

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

    def restore() -> None:
        """Whatever happens to the bridge, the user's terminal must come back.

        ``tty.setraw`` used to run *outside* the try, so a failure in the very
        next line left the shell with no echo and no SIGINT -- only ``reset``
        could save it. SIGTERM/SIGHUP also skipped the ``finally`` entirely.
        """
        with contextlib.suppress(Exception):
            loop.remove_reader(fd)
        with contextlib.suppress(NotImplementedError):
            loop.remove_signal_handler(signal.SIGWINCH)
        with contextlib.suppress(Exception):
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)

    def on_fatal_signal() -> None:
        restore()
        sys.exit(1)

    code = 1
    try:
        tty.setraw(fd)
        loop.add_reader(fd, on_stdin)
        with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
            loop.add_signal_handler(signal.SIGWINCH, on_resize)
            loop.add_signal_handler(signal.SIGTERM, on_fatal_signal)
            loop.add_signal_handler(signal.SIGHUP, on_fatal_signal)
        code = await _session_loop(
            ws_url, headers, session_id, outbound, state, reconnect=reconnect
        )
    finally:
        restore()
    return code


def _drain(queue: asyncio.Queue[bytes | str | None]) -> None:
    """Drop items queued for a connection that no longer exists."""
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return


async def _recv_loop(
    ws: object, session_id: str, cols: int, rows: int, state: dict[str, Any]
) -> tuple[str | None, int | None, bool, int]:
    """Attach and pump output. Returns ``(notice, close_code, exited, code)``."""
    attach: dict[str, Any] = {
        "v": 2,
        "type": "attach",
        "session": session_id,
        "cols": cols,
        "rows": rows,
        "capabilities": ["snapshot", "osc133", "resume"],
    }
    if state.get("last_seq") is not None and not state.get("awaiting_snapshot"):
        attach["last_seq"] = state["last_seq"]
    if state.get("generation"):
        attach["generation"] = state["generation"]
    await ws.send(encode_control(attach))  # type: ignore[attr-defined]

    notice: str | None = None
    exit_code = 0
    last_ack = 0.0
    resync_requested = False

    async def send_ack(force: bool = False) -> None:
        nonlocal last_ack
        now = time.monotonic()
        if not force and now - last_ack < 0.1:
            return
        last_ack = now
        seq = state.get("last_seq")
        if isinstance(seq, int):
            with contextlib.suppress(Exception):
                await ws.send(  # type: ignore[attr-defined]
                    encode_control({"type": "ack", "seq": seq})
                )

    try:
        while True:
            message = await ws.recv()  # type: ignore[attr-defined]
            if isinstance(message, bytes):
                try:
                    frame: DataFrame = decode_data(message)
                except ProtocolError as exc:
                    _notice(f"协议错误：{exc}")
                    continue
                try:
                    os.write(sys.stdout.fileno(), frame.payload)
                except OSError:
                    return notice, None, True, 0
                if frame.flags & FLAG_SNAPSHOT:
                    # A snapshot is a rendered screen anchored at ``seq``; its
                    # byte length is not stream offset. Adding it claimed bytes
                    # that were never sent and made the next resume skip data.
                    current = state.get("last_seq")
                    state["last_seq"] = (
                        frame.seq if not isinstance(current, int) else max(current, frame.seq)
                    )
                    state["awaiting_snapshot"] = False
                elif not state.get("awaiting_snapshot"):
                    current = state.get("last_seq")
                    end = frame.seq + len(frame.payload)
                    state["last_seq"] = end if not isinstance(current, int) else max(current, end)
                await send_ack()
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
            if kind == "attached":
                event = state.get("attached")
                if event is not None:
                    event.set()
                if isinstance(data.get("generation"), str):
                    state["generation"] = data["generation"]
                if data.get("incomplete"):
                    # The server's replay was itself shed. Do not adopt its
                    # offset: that would skip exactly the missing bytes on the
                    # next reconnect. Ask for a real screen snapshot.
                    notice = "会话回放未完整，正在请求快照"
                    _notice(notice)
                    state["awaiting_snapshot"] = True
                    if not resync_requested:
                        resync_requested = True
                        with contextlib.suppress(Exception):
                            await ws.send(  # type: ignore[attr-defined]
                                encode_control({"type": "snapshot-request"})
                            )
                elif isinstance(data.get("seq"), int):
                    # Replay is complete and was queued before ``attached``.
                    state["last_seq"] = data["seq"]
                    state["awaiting_snapshot"] = False
            elif kind == "exit":
                raw = data.get("code")
                exit_code = int(raw) if isinstance(raw, (int, float)) else 0
                return f"会话已退出（退出码 {raw}）", 1000, True, exit_code
            elif kind == "error":
                notice = str(data.get("msg"))
            elif kind == "notice":
                _notice(str(data.get("msg") or "提示"))
            elif kind == "desync":
                _notice(
                    str(data.get("msg") or "输出过快，部分内容已省略")
                    + "。同步后全屏程序会自动重绘；仍不完整可再请求一次同步"
                )
            elif kind == "gap":
                lost_from = data.get("from")
                lost_to = data.get("to")
                _notice(f"连接丢失了字节 {lost_from}..{lost_to}，正在请求快照")
                state["awaiting_snapshot"] = True
                if not resync_requested:
                    resync_requested = True
                    with contextlib.suppress(Exception):
                        await ws.send(  # type: ignore[attr-defined]
                            encode_control({"type": "snapshot-request"})
                        )
            elif kind == "snapshot":
                source = data.get("source") or data.get("format") or "unknown"
                _notice(f"已收到屏幕快照（{source}）")
            elif kind == "snapshot-request":
                continue
            elif kind == "resynced":
                seq = data.get("seq")
                if data.get("incomplete"):
                    notice = "重新同步未完整，部分内容仍缺失"
                    state["awaiting_snapshot"] = True
                elif isinstance(seq, int):
                    current = state.get("last_seq")
                    state["last_seq"] = seq if not isinstance(current, int) else max(current, seq)
                    state["awaiting_snapshot"] = False
                resync_requested = False
            elif kind == "evicted":
                _notice(str(data.get("msg") or "连接已被释放（会话仍在运行）"))
    except ConnectionClosed as exc:
        code: int | None = None
        if exc.rcvd is not None:
            code = exc.rcvd.code
        elif exc.sent is not None:
            code = exc.sent.code
        return notice, code, False, 0


async def _session_loop(
    ws_url: str,
    headers: dict[str, str],
    session_id: str,
    outbound: asyncio.Queue[bytes | str | None],
    state: dict[str, bool],
    *,
    reconnect: bool,
) -> int:
    delay = INITIAL_DELAY
    # A ``wss://`` connection to a self-signed server needs the same trust the
    # HTTP client already applies -- otherwise ``wsctl connect`` cannot reach
    # the very instance ``--tls-self-signed`` just set up, and the failure is
    # an SSL error that reads like a network problem. ``ca_bundle_for`` only
    # ever *adds* a pinned certificate to the system store; it never disables
    # verification.
    tls_context = None
    if ws_url.startswith("wss://"):
        bundle = ca_bundle_for("https://" + ws_url[len("wss://"):])
        if bundle is not None:
            tls_context = ssl.create_default_context(cafile=str(bundle))
    while True:
        try:
            ws = await websockets.connect(
                ws_url,
                additional_headers=headers,
                # Data frames are capped at 1 MiB by the protocol and control
                # frames at 64 KiB. An unbounded read would let a broken (or
                # hostile) server exhaust this process's memory instead.
                max_size=2 * 1024 * 1024,
                ssl=tls_context,
                subprotocols=[SUBPROTOCOL],  # type: ignore[list-item]
            )
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

        # A successful connection resets the backoff. Without this the delay
        # kept growing across a long-lived session: after a few early hiccups
        # every later reconnect waited 16-30s and looked like a dead server.
        delay = INITIAL_DELAY
        _drain(outbound)  # discard input meant for the previous connection
        attached_event = state.get("attached")
        if isinstance(attached_event, asyncio.Event):
            attached_event.clear()
        cols, rows = _winsize()
        async with ws:
            async def sender(conn: object = ws) -> None:
                event = state.get("attached")
                if isinstance(event, asyncio.Event):
                    await event.wait()
                while True:
                    item = await outbound.get()
                    if item is None:
                        with contextlib.suppress(Exception):
                            await conn.close()  # type: ignore[attr-defined]
                        return
                    try:
                        if isinstance(item, bytes):
                            await conn.send(encode_input(item))  # type: ignore[attr-defined]
                        else:
                            await conn.send(item)  # type: ignore[attr-defined]
                    except ConnectionClosed:
                        return

            async def pinger(conn: object = ws) -> None:
                # Keeps the connection from being seen as idle/half-open.
                while True:
                    await asyncio.sleep(PING_INTERVAL)
                    try:
                        await conn.send(  # type: ignore[attr-defined]
                            encode_control({"type": "ping", "nonce": int(time.time())})
                        )
                    except ConnectionClosed:
                        return

            send_task = asyncio.create_task(sender())
            ping_task = asyncio.create_task(pinger())
            try:
                notice, code, exited, exit_code = await _recv_loop(
                    ws, session_id, cols, rows, state
                )
            finally:
                send_task.cancel()
                ping_task.cancel()
                for task in (send_task, ping_task):
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task

        if notice:
            _notice(notice)
        if exited:
            return exit_code
        if state["stdin_closed"]:
            return 0
        if code in FATAL_CODES:
            # Revoked login, forbidden, missing session, server error: retrying
            # cannot help and the exit status must say so.
            return 1
        if not reconnect:
            return 1
        _notice(f"连接断开；{delay:.0f}s 后重连…")
        await asyncio.sleep(delay)
        delay = min(delay * 2, MAX_DELAY)
