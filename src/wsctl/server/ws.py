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
from .security import COOKIE_NAME, client_ip, ip_allowed, is_origin_allowed

log = logging.getLogger("wsctl.ws")

MAX_AUDIT_LINE = 512
ACCESS_RECHECK = 5.0


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
        memory_limit=settings.session_memory_limit,
        scrollback_bytes=settings.scrollback_bytes,
    )


class _Denied(Exception):
    """Handshake denial: the client is told and the socket closed with a code."""

    def __init__(self, message: str, code: int = 4401) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


async def terminal_endpoint(websocket: WebSocket) -> None:
    app: FastAPI = websocket.app
    settings = app.state.settings
    store: Store = app.state.store
    manager = app.state.manager
    metrics: Metrics = app.state.metrics

    ip = client_ip(websocket, settings)
    user = _resolve_user(websocket, store, settings.auth_required)
    share_param = websocket.query_params.get("share")

    # Accept first so the client receives a WebSocket close *code* (a close
    # before accept becomes an opaque HTTP 403 / code 1006 in browsers).
    await websocket.accept()
    metrics.inc("wsctl_ws_connections_total")

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

    # An unauthenticated connection is only allowed when it presents a share
    # token (validated during the handshake).
    if user is None and not share_param:
        await websocket.close(code=4401)
        return

    client = WsClient(websocket, max_bytes=settings.client_max_bytes)
    writer = asyncio.create_task(client.run())
    session: TermSession | None = None
    user_id = user.id if user is not None else None
    close_code = 1000
    try:
        result = await _handshake(websocket, client, settings, manager, user, share_param)
        if result is None:
            return
        session, writable, share = result
        store.log_event(
            "session_attach",
            user_id=user_id,
            term_session_id=session.id,
            ip=ip,
            payload="readonly" if not writable else None,
        )
        on_line = _input_auditor(store, settings, user_id, session.id)
        bucket = _input_bucket(settings)
        await _pump(
            websocket, client, session, on_line, bucket, writable=writable, share=share
        )
    except _Denied as exc:
        close_code = exc.code
        with contextlib.suppress(ClientGone):
            client.put({"type": "error", "msg": exc.message})
    except Exception:
        log.exception("websocket handler failed")
    finally:
        if session is not None:
            with contextlib.suppress(Exception):
                await session.detach(client)
            store.log_event(
                "session_detach", user_id=user_id, term_session_id=session.id, ip=ip
            )
        client.close()
        # Let the writer drain queued control messages (e.g. a final error)
        # before tearing the connection down.
        with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError, Exception):
            await asyncio.wait_for(writer, timeout=2.0)
        writer.cancel()
        if websocket.application_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await websocket.close(code=close_code)


def _access(user: User | None, session: TermSession, share: str | None) -> str | None:
    """Return ``"write"``, ``"read"`` or ``None`` for a session attach."""
    if user is not None and (user.role == "admin" or session.owner_id == user.id):
        return "write"
    return session.share_access(share)


async def _handshake(
    websocket: WebSocket,
    client: WsClient,
    settings: Settings,
    manager: SessionManager,
    user: User | None,
    share_param: str | None = None,
) -> tuple[TermSession, bool, str | None] | None:
    raw = await websocket.receive_text()
    msg = json.loads(raw)
    if msg.get("type") != "attach":
        client.put({"type": "error", "msg": "expected an 'attach' message first"})
        return None

    cols = int(msg.get("cols") or 80)
    rows = int(msg.get("rows") or 24)
    sid = msg.get("session")
    share = msg.get("share") or share_param

    session: TermSession | None
    writable = True
    if sid:
        session = manager.get(str(sid))
        if session is None:
            raise _Denied(f"no such session: {sid}")
        access = _access(user, session, share)
        if access is None:
            raise _Denied("not authorized for this session")
        writable = access == "write"
    else:
        if user is None:
            raise _Denied("authentication required to create a session")
        if len(manager.list_sessions()) >= settings.max_sessions:
            client.put({"type": "error", "msg": "session limit reached"})
            return None
        spec = _default_spec(settings, cols, rows)
        session = await manager.create(spec, owner_id=user.id)
        try:
            if settings.auto_record:
                session.start_recording(
                    settings.recordings_dir / f"{session.id}.cast",
                    record_input=settings.record_input,
                )
            websocket.app.state.metrics.inc("wsctl_sessions_created_total")
            store: Store = websocket.app.state.store
            store.term_session_upsert(
                session.id,
                name=spec.name,
                owner_id=user.id,
                backend=spec.backend,
                command=None,
                argv=spec.argv,
                env=spec.env,
                cwd=spec.cwd,
                idle_timeout=spec.idle_timeout,
                max_life=spec.max_life,
            )
            store.log_event(
                "session_create",
                user_id=user.id,
                term_session_id=session.id,
                payload=msg.get("name") or session.spec.name,
            )
        except Exception:
            # Do not leave a live process/fd behind if post-create setup fails.
            await manager.remove(session.id)
            client.put({"type": "error", "msg": "failed to create session"})
            return None

    # Only writers may set the shared terminal size; a read-only viewer must not
    # be able to shrink the owner's window at attach time.
    if writable:
        session.resize(cols, rows)
    try:
        await session.attach(client, writable=writable, share=share)
    except ClientGone as exc:
        client.put({"type": "error", "msg": str(exc)})
        return None
    return session, writable, share


def _input_auditor(
    store: Store, settings: Settings, user_id: int | None, session_id: str
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
    *,
    writable: bool = True,
    share: str | None = None,
) -> None:
    line_buffer = bytearray()
    read_only_notice = False

    def share_revoked() -> bool:
        return share is not None and not session.share_valid(share)

    async def deny(message: str) -> None:
        with contextlib.suppress(ClientGone):
            client.put({"type": "error", "msg": message})
        with contextlib.suppress(Exception):
            await websocket.close(code=4401)

    def over_limit(size: int) -> bool:
        return bucket is not None and not bucket.allow(size)

    def reject() -> None:
        with contextlib.suppress(ClientGone):
            client.put({"type": "error", "msg": "input rate limit exceeded"})

    def reject_readonly() -> None:
        nonlocal read_only_notice
        if read_only_notice:
            return
        read_only_notice = True
        with contextlib.suppress(ClientGone):
            client.put({"type": "error", "msg": "session is read-only"})

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
        try:
            message = await asyncio.wait_for(websocket.receive(), timeout=ACCESS_RECHECK)
        except TimeoutError:
            # Re-validate periodically so a revoked/expired share is enforced
            # even on a session that produces no output.
            if share_revoked():
                await deny("share revoked or expired")
                return
            continue
        msg_type = message.get("type")
        if msg_type == "websocket.disconnect":
            return
        data_bytes = message.get("bytes")
        if data_bytes is not None:
            if share_revoked():
                await deny("share revoked or expired")
                return
            if not writable:
                reject_readonly()
                continue
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
            if writable:
                session.resize(int(data.get("cols") or 80), int(data.get("rows") or 24))
        elif kind == "input":
            if not writable:
                reject_readonly()
                continue
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
