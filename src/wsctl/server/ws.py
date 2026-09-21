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
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket
from starlette.websockets import WebSocketState

from wsctl.core.session import ClientGone, SessionSpec, TermSession
from wsctl.core.store import Store, User

from .client import WsClient
from .security import COOKIE_NAME, can_access, is_origin_allowed

log = logging.getLogger("wsctl.ws")


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


def _default_spec(settings: Any, cols: int, rows: int) -> SessionSpec:
    shell = settings.shell
    return SessionSpec(
        name=Path(shell).name or "shell",
        argv=[shell],
        cwd=settings.default_cwd,
        cols=cols,
        rows=rows,
        idle_timeout=settings.idle_timeout,
        max_life=settings.max_life,
        scrollback_bytes=settings.scrollback_bytes,
    )


async def terminal_endpoint(websocket: WebSocket) -> None:
    app: FastAPI = websocket.app
    settings = app.state.settings
    store: Store = app.state.store
    manager = app.state.manager

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

    client = WsClient(websocket)
    writer = asyncio.create_task(client.run())
    session: TermSession | None = None
    try:
        session = await _handshake(websocket, client, settings, manager, user)
        if session is None:
            return
        await _pump(websocket, client, session)
    except Exception:
        log.exception("websocket handler failed")
    finally:
        if session is not None:
            with contextlib.suppress(Exception):
                await session.detach(client)
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
    settings: Any,
    manager: Any,
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
        session = await manager.create(spec)
        settings_store: Store = websocket.app.state.store
        settings_store.log_event(
            "session_create",
            user_id=user.id,
            term_session_id=session.id,
            payload=msg.get("name") or session.spec.name,
        )

    session.resize(cols, rows)
    await session.attach(client)
    return session


async def _pump(websocket: WebSocket, client: WsClient, session: TermSession) -> None:
    while True:
        message = await websocket.receive()
        msg_type = message.get("type")
        if msg_type == "websocket.disconnect":
            return
        data_bytes = message.get("bytes")
        if data_bytes is not None:
            session.write_input(data_bytes)
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
            session.write_input(str(data.get("data", "")).encode("utf-8"))
        elif kind == "ping":
            try:
                client.put({"type": "pong"})
            except ClientGone:
                return
