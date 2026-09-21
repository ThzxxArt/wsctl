"""WebSocket terminal endpoint.

Protocol (shared by browser tabs and the CLI client):

* **binary** frames carry raw terminal bytes in both directions;
* **text** frames carry JSON control messages (``attach``, ``resize``,
  ``ping`` → ``pong``, ``attached``, ``exit``, ``error``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, WebSocket
from starlette.websockets import WebSocketState

from wsctl.core import tmux
from wsctl.core.config import Settings
from wsctl.core.metrics import Metrics
from wsctl.core.ratelimit import TokenBucket
from wsctl.core.session import ClientGone, SessionManager, SessionSpec, TermSession
from wsctl.core.store import Store, User

from .client import WsClient
from .security import COOKIE_NAME, can_access, client_ip, ip_allowed, is_origin_allowed

log = logging.getLogger("wsctl.ws")

MAX_AUDIT_LINE = 512


def _resolve_user(websocket: WebSocket, store: Store, auth_required: bool) -> User | None:
    if not auth_required:
        return User(id=0, username="anonymous", role="admin", disabled=False, created_at=0.0)
    token: str | None = None
    auth = websocket.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token:
        token = websocket.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return store.resolve_auth_session(token)


def _default_spec(settings: Settings, cols: int, rows: int) -> SessionSpec:
    shell = settings.shell
    backend = settings.default_backend
    if backend == "tmux" and not tmux.is_available():
        backend = "local"
    return SessionSpec(
        name=Path(shell).name or "shell",
        argv=[shell],
        cwd=settings.default_cwd,
        backend=backend,
        cols=cols,
        rows=rows,
        idle_timeout=settings.idle_timeout,
        max_life=settings.max_life,
        max_clients=settings.session_max_clients,
        scrollback_bytes=settings.scrollback_bytes,
    )


async def terminal_endpoint(websocket: WebSocket) -> None:
    app: FastAPI = websocket.app
    settings = app.state.settings
    store: Store = app.state.store
    manager = app.state.manager
    metrics: Metrics = app.state.metrics

    ip = client_ip(websocket, settings)
    if not ip_allowed(ip, settings.allowed_ips):
        store.log_event("ip_rejected", ip=ip, payload="ws")
        await websocket.close(code=4403)
        return

    if not is_origin_allowed(
        websocket.headers.get("origin"),
        websocket.headers.get("host"),
        settings.allowed_origins,
    ):
        await websocket.close(code=4403)
        return

    user = _resolve_user(websocket, store, settings.auth_required)
    if user is None:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    metrics.inc("wsctl_ws_connections_total")

    client = WsClient(websocket)
    writer = asyncio.create_task(client.run())
    session: TermSession | None = None
    try:
        session = await _handshake(websocket, client, settings, manager, user)
        if session is None:
            return
        store.log_event(
            "session_attach",
            user_id=user.id,
            term_session_id=session.id,
            ip=ip,
        )
        on_line = _input_auditor(store, settings, user.id, session.id)
        bucket = _input_bucket(settings)
        await _pump(websocket, client, session, on_line, bucket)
    except Exception:
        log.exception("websocket handler failed")
    finally:
        if session is not None:
            with contextlib.suppress(Exception):
                await session.detach(client)
            store.log_event(
                "session_detach", user_id=user.id, term_session_id=session.id, ip=ip
            )
        client.close()
        # Let the writer drain queued control messages (e.g. a final error)
        # before tearing the connection down.
        with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError, Exception):
            await asyncio.wait_for(writer, timeout=2.0)
        writer.cancel()
        if websocket.application_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await websocket.close()


async def _handshake(
    websocket: WebSocket,
    client: WsClient,
    settings: Settings,
    manager: SessionManager,
    user: User,
) -> TermSession | None:
    raw = await websocket.receive_text()
    msg = json.loads(raw)
    if msg.get("type") != "attach":
        client.put({"type": "error", "msg": "expected an 'attach' message first"})
        return None

    cols = int(msg.get("cols") or 80)
    rows = int(msg.get("rows") or 24)
    sid = msg.get("session")

    session: TermSession | None
    if sid:
        session = manager.get(str(sid))
        if session is None:
            client.put({"type": "error", "msg": f"no such session: {sid}"})
            return None
        if not can_access(user, session):
            client.put({"type": "error", "msg": "not authorized for this session"})
            return None
    else:
        if len(manager.list_sessions()) >= settings.max_sessions:
            client.put({"type": "error", "msg": "session limit reached"})
            return None
        spec = _default_spec(settings, cols, rows)
        session = await manager.create(spec, owner_id=user.id)
        websocket.app.state.metrics.inc("wsctl_sessions_created_total")
        store: Store = websocket.app.state.store
        store.term_session_upsert(
            session.id,
            name=spec.name,
            owner_id=user.id,
            backend=spec.backend,
            command=None,
            cwd=spec.cwd,
        )
        store.log_event(
            "session_create",
            user_id=user.id,
            term_session_id=session.id,
            payload=msg.get("name") or session.spec.name,
        )

    session.resize(cols, rows)
    try:
        await session.attach(client)
    except ClientGone as exc:
        client.put({"type": "error", "msg": str(exc)})
        return None
    return session


def _input_auditor(
    store: Store, settings: Settings, user_id: int, session_id: str
) -> Callable[[str], None] | None:
    """Return a line callback that audits submitted input, if enabled."""
    if not settings.audit_input:
        return None

    def record(line: str) -> None:
        store.log_event(
            "input",
            user_id=user_id,
            term_session_id=session_id,
            payload=line[:MAX_AUDIT_LINE],
        )

    return record


def _input_bucket(settings: Settings) -> TokenBucket | None:
    if settings.input_rate_limit <= 0:
        return None
    capacity = settings.input_rate_burst or settings.input_rate_limit
    return TokenBucket(float(settings.input_rate_limit), float(capacity))


async def _pump(
    websocket: WebSocket,
    client: WsClient,
    session: TermSession,
    on_line: Callable[[str], None] | None = None,
    bucket: TokenBucket | None = None,
) -> None:
    line_buffer = bytearray()

    def over_limit(size: int) -> bool:
        return bucket is not None and not bucket.allow(size)

    def reject() -> None:
        with contextlib.suppress(ClientGone):
            client.put({"type": "error", "msg": "input rate limit exceeded"})

    def feed_audit(data: bytes) -> None:
        if on_line is None:
            return
        line_buffer.extend(data)
        while b"\r" in line_buffer or b"\n" in line_buffer:
            indices = [i for i in (line_buffer.find(b"\r"), line_buffer.find(b"\n")) if i >= 0]
            cut = min(indices)
            line = bytes(line_buffer[:cut])
            del line_buffer[: cut + 1]
            if line.strip():
                on_line(line.decode("utf-8", "replace"))

    while True:
        message = await websocket.receive()
        msg_type = message.get("type")
        if msg_type == "websocket.disconnect":
            return
        data_bytes = message.get("bytes")
        if data_bytes is not None:
            if over_limit(len(data_bytes)):
                reject()
                return
            session.write_input(data_bytes)
            feed_audit(data_bytes)
            continue
        text = message.get("text")
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        kind = data.get("type")
        if kind == "resize":
            session.resize(int(data.get("cols") or 80), int(data.get("rows") or 24))
        elif kind == "input":
            chunk = str(data.get("data", "")).encode("utf-8")
            if over_limit(len(chunk)):
                reject()
                return
            session.write_input(chunk)
            feed_audit(chunk)
        elif kind == "ping":
            try:
                client.put({"type": "pong"})
            except ClientGone:
                return
