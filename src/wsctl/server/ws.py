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
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from wsctl.core import tmux
from wsctl.core.audit import AuditWriter

# Close codes live in one place (``core.closecodes``) so the server, the
# browser and the CLI cannot drift apart about which failures are permanent.
from wsctl.core.closecodes import (
    CLOSE_BAD_REQUEST,
    CLOSE_FORBIDDEN,
    CLOSE_LIMIT,
    CLOSE_NOT_FOUND,
    CLOSE_OVERLOAD,
    CLOSE_RATE_LIMITED,
    CLOSE_SERVER_ERROR,
    CLOSE_TIMEOUT,
    CLOSE_UNAUTHORIZED,
)
from wsctl.core.config import Settings
from wsctl.core.metrics import Metrics
from wsctl.core.protocol import MAX_CONTROL_FRAME
from wsctl.core.pty import PtyError
from wsctl.core.ratelimit import TokenBucket
from wsctl.core.recording import has_room as recordings_have_room
from wsctl.core.session import (
    ClientGone,
    SessionManager,
    SessionSpec,
    TermSession,
    replay_target_for,
    within_user_quota,
)
from wsctl.core.store import Store, User, hash_token

from .client import WsClient
from .deps import resolve_token_user
from .security import (
    COOKIE_NAME,
    client_ip,
    forwarded_scheme,
    ip_allowed,
    is_origin_allowed,
)

log = logging.getLogger("wsctl.ws")

MAX_AUDIT_LINE = 512
MAX_AUDIT_BUFFER = 8192
ACCESS_RECHECK = 5.0

#: How long a pump waits for a closed session's final ``exit`` notice before
#: closing the socket anyway. The finalizer itself is bounded (3s PTY wait +
#: 2s kill wait), so this only covers "just a little longer than the worst
#: normal case"; a stuck finalizer must not hold the socket forever.
EXIT_DRAIN_TIMEOUT = 6.0

#: A detach only pops a viewer from the session's client map. If the actor is
#: already terminating, waiting forever would keep the socket open and the
#: browser would never learn that the connection is gone.
DETACH_TIMEOUT = 2.0

#: Largest single input frame accepted from a client. The PTY write buffer has
#: its own hard cap, but that check runs on what is *already buffered* -- one
#: 16 MiB frame sailed past a 1 MiB "limit" whenever the buffer happened to be
#: empty. The transport limit (uvicorn's ``ws_max_size``, 16 MiB) is not an
#: application bound either. Rejected whole rather than sliced: a half-
#: delivered keystroke sequence is worse than a reported drop.
MAX_INPUT_FRAME = 256 * 1024


# Close a connection that has sent nothing for this long. Both clients send a
# `ping` every 25s, so this detects a half-open TCP connection (a peer that
# vanished without a FIN) without ever tripping on a genuinely idle session.
IDLE_TIMEOUT = 120.0

#: A WebSocket that has not sent its ``attach`` by then is not a terminal. The
#: endpoint used to await the first frame with no bound at all: one peer (or a
#: hundred) could open sockets and never speak, holding fds and tasks forever.
#: The browser and the CLI both send ``attach`` immediately; five seconds is
#: already generous for a slow link.
HANDSHAKE_TIMEOUT = 5.0

#: Control frames (``ping`` / ``resize`` / text ``input``) per second and burst.
#: These are cheap individually, but nothing used to bound them: a client could
#: queue an unbounded stream of them and keep the loop busy parsing JSON.
CONTROL_RATE = 40.0
CONTROL_BURST = 80.0

#: ``resync`` replays up to the whole scrollback (4 MiB by default) and asks
#: the application to repaint. Three in a burst, one every five seconds
#: sustained -- a screen cannot become stale faster than a human can notice.
RESYNC_RATE = 0.2
RESYNC_BURST = 3.0

#: Consecutive over-limit chunks before the peer is disconnected.
MAX_RATE_STRIKES = 3


class ConnectionCaps:
    """Open terminal sockets: process-wide, and per peer address.

    Runtimes are single-threaded event loops, so plain counters need no lock.
    The limits are read from settings on every call, which is what makes them
    hot-reloadable.

    Every admission is a *token*, and releasing is idempotent. That is not
    bookkeeping for its own sake: the handler releases the slot at the end of
    its ``finally``, but a cancellation delivered inside that block (the
    TestClient cancels the app task when its socket context closes; a server
    shutdown cancels every task) skips the remaining statements of the
    ``finally`` -- including a release that was a plain call after an
    ``await``. A task done-callback releases the same token as a backstop, and
    because the token is popped, the second release is a no-op instead of a
    double decrement that would let the process exceed its cap.
    """

    def __init__(self) -> None:
        self._total = 0
        self._by_ip: dict[str, int] = {}
        self._next_token = 0
        self._active: dict[int, str] = {}

    @property
    def total(self) -> int:
        return self._total

    def for_ip(self, ip: str | None) -> int:
        return self._by_ip.get(ip or "-", 0)

    def admit(self, ip: str | None, settings: Settings) -> int | None:
        """Reserve a slot, returning its token (or ``None`` at capacity)."""
        key = ip or "-"
        if settings.ws_max_connections > 0 and self._total >= settings.ws_max_connections:
            return None
        if (
            settings.ws_max_connections_per_ip > 0
            and self._by_ip.get(key, 0) >= settings.ws_max_connections_per_ip
        ):
            return None
        self._next_token += 1
        token = self._next_token
        self._active[token] = key
        self._total += 1
        self._by_ip[key] = self._by_ip.get(key, 0) + 1
        return token

    def release(self, token: int | None) -> None:
        """Give a slot back. Releasing an already-released token is a no-op."""
        if token is None:
            return
        key = self._active.pop(token, None)
        if key is None:
            return
        self._total = max(0, self._total - 1)
        left = self._by_ip.get(key, 0) - 1
        if left > 0:
            self._by_ip[key] = left
        else:
            self._by_ip.pop(key, None)


def _auth_token(websocket: WebSocket) -> str | None:
    auth = websocket.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return websocket.cookies.get(COOKIE_NAME)


async def api_key_scopes(websocket: WebSocket) -> set[str] | None:
    """Scopes of a ``wsctl_`` bearer token, or ``None`` for cookie auth.

    ``None`` means "not an API key": the caller should use the user's normal
    permissions. An empty set means the key exists but carries no scopes, which
    the HTTP middleware documents as "inherit the user's permissions" -- so it
    is normalised to ``None`` here as well.
    """
    auth = websocket.headers.get("authorization")
    if not auth or not auth.lower().startswith("bearer "):
        return None
    token = auth[7:].strip()
    if not token.startswith("wsctl_"):
        return None
    db = getattr(websocket.app.state, "db", None)
    if db is None:
        return set()
    record = await asyncio.to_thread(db.api_key_resolve, hash_token(token))
    if record is None:
        return set()
    return set(record.get("scopes") or []) or None


def _default_spec(settings: Settings, cols: int, rows: int) -> SessionSpec:
    shell = settings.shell
    backend = settings.default_backend
    if backend == "tmux" and not tmux.is_available():
        backend = "local"
    return SessionSpec(
        name=Path(shell).name or "终端",
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
        snapshot_mode=settings.snapshot_mode,
        shell_integration=settings.shell_integration,
    )


async def _resolve_user(
    websocket: WebSocket, store: Store, auth_required: bool
) -> User | None:
    """Resolve the connecting user; the token lookup runs off the event loop."""
    if not auth_required:
        return User(id=0, username="anonymous", role="admin", disabled=False, created_at=0.0)
    token = _auth_token(websocket)
    if not token:
        return None
    db = getattr(websocket.app.state, "db", None)
    if db is not None and token.startswith("wsctl_"):

        def resolve_key() -> User | None:
            record = db.api_key_resolve(hash_token(token))
            if record is None:
                return None
            user = store.user_get_by_id(int(record["user_id"]))
            return user if user is not None and not user.disabled else None

        user = await asyncio.to_thread(resolve_key)
        if user is not None:
            return user
    return await asyncio.to_thread(store.resolve_auth_session, token)


class _Denied(Exception):
    """Handshake denial: the client is told and the socket closed with a code."""

    def __init__(self, message: str, code: int = CLOSE_UNAUTHORIZED) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


async def terminal_endpoint(websocket: WebSocket) -> None:
    app: FastAPI = websocket.app
    settings = app.state.settings
    store: Store = app.state.store
    manager = app.state.manager
    metrics: Metrics = app.state.metrics
    audit: AuditWriter = app.state.audit

    ip = client_ip(websocket, settings)
    user = await _resolve_user(websocket, store, settings.auth_required)
    auth_token = _auth_token(websocket)
    key_scopes = await api_key_scopes(websocket)
    shares = getattr(app.state, "shares", None)
    share_param = websocket.query_params.get("share")

    # Accept first so the client receives a WebSocket close *code* (a close
    # before accept becomes an opaque HTTP 403 / code 1006 in browsers).
    await websocket.accept()
    metrics.inc("wsctl_ws_connections_total")

    if not ip_allowed(ip, settings.allowed_ips):
        audit.enqueue("ip_rejected", ip=ip, payload="ws")
        await websocket.close(code=CLOSE_FORBIDDEN)
        return

    if not is_origin_allowed(
        websocket.headers.get("origin"),
        websocket.headers.get("host"),
        settings.allowed_origins,
        scheme=forwarded_scheme(websocket, settings) or websocket.url.scheme,
    ):
        await websocket.close(code=CLOSE_FORBIDDEN)
        return

    # An unauthenticated connection is only allowed when it presents a share
    # token (validated during the handshake).
    if user is None and not share_param:
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return

    # Admission control comes *before* the handshake frame and before any
    # background task is created: a peer that opens sockets and never speaks
    # must not be able to hold fds, tasks or memory beyond these bounds.
    caps: ConnectionCaps = websocket.app.state.ws_caps
    slot = caps.admit(ip, settings)
    if slot is None:
        audit.enqueue("ws_capacity_rejected", ip=ip, payload="ws")
        metrics.inc("wsctl_ws_capacity_rejected_total")
        # 4413, not 4409: capacity is transient. A fatal code told every tab in
        # a NAT office that reconnecting was pointless, so a momentary limit
        # became a dead tab until a manual reload.
        #
        # Say why *before* closing (the socket is accepted, so a control frame
        # still reaches the client): a bare close with a non-fatal code made
        # the tab reconnect, but "why did this happen" was unanswerable.
        with contextlib.suppress(Exception):
            await websocket.send_json(
                {"type": "error", "msg": "连接数已达上限，请稍后重连"}
            )
        await websocket.close(code=CLOSE_OVERLOAD)
        return

    # Backstop: the ``finally`` below releases the same token, but a
    # cancellation delivered while that block awaits skips the rest of it.
    # ``release`` pops the token, so whichever runs first wins and the other is
    # a no-op.
    task = asyncio.current_task()
    if task is not None:
        task.add_done_callback(lambda _done: caps.release(slot))

    try:
        client = WsClient(websocket, max_bytes=settings.client_max_bytes)
        writer = asyncio.create_task(client.run())
    except BaseException:
        # Nothing was set up, but the admission slot must not leak.
        caps.release(slot)
        raise
    session: TermSession | None = None
    user_id = user.id if user is not None else None
    close_code = 1000
    try:
        result = await _handshake(
            websocket,
            client,
            settings,
            manager,
            user,
            share_param,
            key_scopes=key_scopes,
            shares=shares,
        )
        if result is None:
            return
        session, writable, share = result
        if shares is not None:
            session.set_external_share_resolver(
                shares.resolver(session.id), shares.known(session.id)
            )
        # Shedding is how a slow viewer is kept alive, but the frames it lost
        # are gone from *its* screen. A full-screen application redraws only
        # what it believes changed, so the regions it last painted are the ones
        # nobody paints again -- a sidebar, a status bar, an input box simply
        # stop existing. That repair is the application's own repaint, and
        # waiting for a human to click 重新同步 is why a gap could become
        # permanent. Ask for it the moment the loss is known.
        client.on_shed = session.request_repaint
        audit.enqueue(
            "session_attach",
            user_id=user_id,
            term_session_id=session.id,
            ip=ip,
            payload="readonly" if not writable else None,
        )
        # A replayed byte stream cannot reconstruct a full-screen app's
        # screen: it draws by cursor-addressed overwrites on top of a state
        # the stream may no longer carry (shed holes, evicted head). Anything
        # replayed is followed by an *application* repaint -- the app holds
        # the only complete model. A brand-new session replays nothing and
        # needs nothing.
        #
        # A read-only viewer gets this too: it is the only repair path a TUI
        # viewer has, and a viewer that joins a running full-screen app would
        # otherwise see a wrong screen from the first frame. The cost is that
        # the owner's PTY is resized twice (the application's own repaint), so
        # repeats are what the ``resync`` token bucket below bounds -- not the
        # one-off attach.
        if session.has_scrollback:
            await session.nudge_repaint()
        on_line = _input_auditor(audit, settings, user_id, session.id)
        bucket = _input_bucket(settings)
        control_bucket = TokenBucket(CONTROL_RATE, CONTROL_BURST)
        resync_bucket = TokenBucket(RESYNC_RATE, RESYNC_BURST)
        # The reason a connection is being closed must reach the client *before*
        # the socket goes away. ``deny`` used to ``websocket.close()`` inline,
        # which raced the outbound writer and lost the explanation: the peer got
        # the close code but never the sentence. Every denial path (share
        # revoked, login expired, idle timeout, rate limit) shared the defect.
        pending_close: list[int | None] = [None]
        recheck: Callable[[], Awaitable[User | None]] | None = None
        share_recheck: Callable[[], Awaitable[bool]] | None = None
        if user is not None and auth_token is not None:
            token = auth_token

            async def recheck() -> User | None:
                # Off the event loop: this runs every few seconds per connection
                # and the store consults a short-lived cache on the hot path.
                # ``resolve_token_user`` (not ``resolve_auth_session``) is what
                # keeps an API key working past the first re-check: the latter
                # only knows browser sessions, so a ``wsctl_`` token was denied
                # five seconds after it connected.
                return await asyncio.to_thread(resolve_token_user, websocket, token)

        if share is not None and shares is not None:
            held_share = share

            async def share_recheck() -> bool:
                # Legacy token first: it lives on the session, not in the v2
                # table, so asking the database alone would revoke a valid old
                # link five seconds after it attached.
                if await asyncio.to_thread(session.share_valid, held_share):
                    return True
                record = await asyncio.to_thread(shares.resolve, held_share)
                return record is not None and record.term_session_id == session.id

        await _pump(
            websocket,
            client,
            session,
            on_line,
            bucket,
            writable=writable,
            share=share,
            recheck=recheck,
            share_recheck=share_recheck,
            key_scopes=key_scopes,
            metrics=metrics,
            close_code_out=pending_close,
            control_bucket=control_bucket,
            resync_bucket=resync_bucket,
        )
        if pending_close[0] is not None:
            close_code = int(pending_close[0])
    except _Denied as exc:
        close_code = exc.code
        with contextlib.suppress(ClientGone):
            client.put({"type": "error", "msg": exc.message})
    except Exception:
        log.exception("websocket handler failed")
        # An internal failure is *not* a clean shutdown. Reporting 1000 made
        # the browser treat it as one and reconnect as if nothing happened.
        if close_code == 1000:
            close_code = CLOSE_SERVER_ERROR
    finally:
        if session is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(session.detach(client), timeout=DETACH_TIMEOUT)
            audit.enqueue(
                "session_detach", user_id=user_id, term_session_id=session.id, ip=ip
            )
        client.close()
        # Let the writer drain queued control messages (e.g. a final error)
        # before tearing the connection down.
        with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError, Exception):
            await asyncio.wait_for(writer, timeout=2.0)
        writer.cancel()
        # A client that dropped itself (slow consumer) knows why. Reporting
        # 1000 -- "normal closure" -- is what let the browser treat a drop as a
        # clean shutdown and blank its own screen on the reconnect that
        # followed.
        final_code = getattr(client, "close_code", None) or close_code
        if websocket.application_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await websocket.close(code=final_code)
        # Released last: the slot should cover the socket, not just the handler.
        # The done-callback installed at admission releases the same token if a
        # cancellation skips this line.
        caps.release(slot)


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
    *,
    key_scopes: set[str] | None = None,
    shares: Any = None,
) -> tuple[TermSession, bool, str | None] | None:
    try:
        # Only the *first frame* is on a clock. Wrapping the whole handshake in
        # ``wait_for`` also timed session creation and its bookkeeping, and a
        # timeout there left a freshly created session with no client at all.
        raw = await asyncio.wait_for(
            websocket.receive_text(), timeout=HANDSHAKE_TIMEOUT
        )
        if len(raw.encode("utf-8")) > MAX_CONTROL_FRAME:
            raise _Denied("attach 消息超过上限", CLOSE_BAD_REQUEST)
        msg = json.loads(raw)
    except TimeoutError:
        raise _Denied("握手超时：连接后 5 秒内没有发送 attach", CLOSE_TIMEOUT) from None
    except WebSocketDisconnect:
        # A tab closed before it attached. That is not a server error, and it
        # used to be logged as one (the generic handler turns it into 4500).
        raise _Denied("客户端在握手期间断开", CLOSE_BAD_REQUEST) from None
    except (json.JSONDecodeError, KeyError, RuntimeError):
        # ``KeyError`` is what Starlette raises for ``message["text"]`` when the
        # first frame is *binary*; ``RuntimeError`` covers a socket torn down
        # mid-handshake. Both are protocol violations, not internal errors --
        # and falling through to the generic handler used to close with 1000
        # ("normal closure"), which made the browser retry as if all were well.
        raise _Denied("首条消息必须是 JSON 格式的 attach", CLOSE_BAD_REQUEST) from None
    if not isinstance(msg, dict) or msg.get("type") != "attach":
        raise _Denied("首条消息必须是 attach", CLOSE_BAD_REQUEST)

    try:
        cols = int(msg.get("cols") or 80)
        rows = int(msg.get("rows") or 24)
    except (TypeError, ValueError, OverflowError):
        # ``int(1e400)`` is an ``inf`` float that raises ``OverflowError``, not
        # ``ValueError`` -- a bare 1000 close used to be the result.
        raise _Denied("cols/rows 必须是整数", CLOSE_BAD_REQUEST) from None
    cols = min(max(1, cols), 1000)
    rows = min(max(1, rows), 1000)
    sid = msg.get("session")
    share = msg.get("share") or share_param

    session: TermSession | None
    writable = True
    created = False
    #: Whether this connection's authority comes from being the owner (or an
    #: admin) rather than from the share link it arrived with. A share *link*
    #: opened by the owner is still an owner connection: making it subject to
    #: share revocation is what let rotating a share 4403 the owner out of their
    #: own terminal (and drop their output at the session's broadcast filter).
    owner_connection = False
    if sid:
        session = manager.get(str(sid))
        if session is None:
            raise _Denied(f"会话不存在：{sid}", CLOSE_NOT_FOUND)
        # Resolve a database-backed share before ``_access`` so the cached
        # resolver the broadcast filter uses is warm, and so a v1 client can
        # open a v2 share link at all (the resolver is installed per session,
        # not globally).
        access = None
        if share and shares is not None:
            record = await asyncio.to_thread(shares.resolve, share)
            if record is not None and record.term_session_id == session.id:
                access = record.scope
                session.set_external_share_resolver(
                    shares.resolver(session.id), shares.known(session.id)
                )
        if access is None and user is not None and (
            user.role == "admin" or session.owner_id == user.id
        ):
            access = "write"
        if access is None:
            # Legacy token only. A database token that failed the explicit
            # resolve above must not be resurrected from the live cache.
            access = session.legacy_share_scope(share) if share else None
        if access is None:
            raise _Denied("无权访问该会话", CLOSE_FORBIDDEN)
        writable = access == "write"
        if key_scopes is not None and "admin" not in key_scopes:
            # A scoped API key is never more powerful than its scopes, even
            # when the underlying user owns the session. Before this check the
            # legacy endpoint ignored scopes entirely: a read-only key could
            # type into the owner's shell.
            writable = writable and "write" in key_scopes
        owner_connection = user is not None and (
            user.role == "admin" or session.owner_id == user.id
        )
    else:
        if user is None:
            raise _Denied("创建会话需要登录", CLOSE_UNAUTHORIZED)
        if key_scopes is not None and "admin" not in key_scopes and "write" not in key_scopes:
            raise _Denied("该 API Key 没有创建会话的权限", CLOSE_FORBIDDEN)
        if len(manager.list_sessions()) >= settings.max_sessions:
            raise _Denied("会话数量已达上限", CLOSE_LIMIT)
        if not within_user_quota(manager, settings.max_sessions_per_user, user.id):
            raise _Denied("该用户的会话数量已达上限", CLOSE_LIMIT)
        spec = _default_spec(settings, cols, rows)
        try:
            session = await manager.create(spec, owner_id=user.id if user.id > 0 else None)
        except (OSError, PtyError) as exc:
            raise _Denied(f"无法启动命令：{exc}", CLOSE_BAD_REQUEST) from exc
        created = True
        try:
            if settings.auto_record and await asyncio.to_thread(
                recordings_have_room,
                settings.recordings_dir,
                settings.recordings_max_bytes,
            ):
                await session.start_recording(
                    settings.recordings_dir / f"{session.id}.cast",
                    record_input=settings.record_input,
                )
            websocket.app.state.metrics.inc("wsctl_sessions_created_total")
            store: Store = websocket.app.state.store
            await asyncio.to_thread(
                store.term_session_upsert,
                session.id,
                name=spec.name,
                owner_id=user.id if user.id > 0 else None,
                backend=spec.backend,
                command=None,
                argv=spec.argv,
                env=spec.env,
                cwd=spec.cwd,
                idle_timeout=spec.idle_timeout,
                max_life=spec.max_life,
                instance_id=getattr(websocket.app.state, "instance_id", None),
                generation=session.generation,
                snapshot_mode=spec.snapshot_mode,
                shell_integration=spec.shell_integration,
            )
            websocket.app.state.audit.enqueue(
                "session_create",
                user_id=user.id,
                term_session_id=session.id,
                # Coerced: a JSON object here used to reach the sqlite binder
                # and take the whole audit batch down with it.
                payload=str(msg.get("name") or session.spec.name)[:MAX_AUDIT_LINE],
            )
        except Exception:
            # Do not leave a live process/fd behind if post-create setup fails.
            await manager.remove(session.id)
            raise _Denied("创建会话失败", CLOSE_SERVER_ERROR) from None

    # Only writers may set the shared terminal size; a read-only viewer must not
    # be able to shrink the owner's window at attach time.
    if writable:
        session.resize(cols, rows)
    # An owner who arrived through their own share link keeps owner semantics
    # everywhere downstream: no revocation filter in the broadcast path, and no
    # ``4403`` when the link is rotated while they are watching.
    filter_share = None if owner_connection else share
    try:
        await session.attach(client, writable=writable, share=filter_share)
    except ClientGone as exc:
        # A session we just created has no other owner and must not be leaked.
        if created:
            await manager.remove(session.id)
        raise _Denied(str(exc), CLOSE_LIMIT) from exc
    except Exception:
        if created:
            await manager.remove(session.id)
        raise
    return session, writable, filter_share


def _input_auditor(
    audit: AuditWriter, settings: Settings, user_id: int | None, session_id: str
) -> Callable[[str], None] | None:
    """Return a line callback that audits submitted input, if enabled."""
    if not settings.audit_input:
        return None

    def record(line: str) -> None:
        audit.enqueue(
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
    recheck: Callable[[], Awaitable[User | None]] | None = None,
    share_recheck: Callable[[], Awaitable[bool]] | None = None,
    key_scopes: set[str] | None = None,
    metrics: Metrics | None = None,
    close_code_out: list[int | None] | None = None,
    control_bucket: TokenBucket | None = None,
    resync_bucket: TokenBucket | None = None,
) -> None:
    line_buffer = bytearray()
    read_only_notice = False
    backpressure_notice = False
    control_notice = False
    resync_notice = False
    last_rx = time.monotonic()
    # Drives the periodic re-validation. This must be a *clock*, not a side
    # effect of "the receive timed out": the checks used to live only in the
    # ``except TimeoutError`` branch, so any peer that kept sending a frame
    # every few seconds -- ``ping``, even a malformed one -- postponed the
    # share/auth/idle checks forever. A disabled account, a revoked token or a
    # revoked share then kept a fully writable terminal indefinitely.
    last_recheck = time.monotonic()

    def share_revoked() -> bool:
        # ``False`` only: a database-backed token that is merely absent from
        # the local cache is not revoked. The periodic async re-check resolves
        # it and closes the connection if the row is really gone.
        return share is not None and session.share_state(share) is False

    async def enforce_access() -> bool:
        """Run the periodic re-validation. ``True`` = a denial was issued, stop.

        Called whenever ``ACCESS_RECHECK`` has elapsed, at the top of the loop,
        so it is driven by time rather than by which branch happened to run.
        Every input path therefore inherits it, and a peer that never goes
        quiet cannot dodge it.
        """
        nonlocal writable, last_recheck
        if client.closed:
            return True
        if share_recheck is not None and not await share_recheck():
            await deny("分享已撤销或过期", CLOSE_FORBIDDEN)
            return True
        if share_revoked():
            await deny("分享已撤销或过期", CLOSE_FORBIDDEN)
            return True
        if recheck is not None:
            fresh = await recheck()
            if fresh is None:
                await deny("登录已失效，请重新登录", CLOSE_UNAUTHORIZED)
                return True
            # Re-derive access with the *fresh* user: a role change since the
            # handshake must downgrade (or drop) this connection now rather
            # than at the next reconnect.
            access = _access(fresh, session, share)
            if access is None:
                await deny("无权访问该会话", CLOSE_FORBIDDEN)
                return True
            writable = access == "write"
            if key_scopes is not None and "admin" not in key_scopes:
                writable = writable and "write" in key_scopes
        last_recheck = time.monotonic()
        return False

    async def deny(message: str, code: int = CLOSE_UNAUTHORIZED) -> None:
        """Explain, then let the caller close -- after the writer has flushed.

        Closing here used to race the outbound writer: the control message was
        queued and the socket torn down underneath it, so the peer received the
        close code and *none* of the words. Record the code and return; the
        endpoint drains the writer first and then closes with it.
        """
        with contextlib.suppress(ClientGone):
            client.put({"type": "error", "msg": message})
        if close_code_out is not None:
            close_code_out[0] = code

    # Rate limiting must not masquerade as a dead terminal. One over-limit
    # chunk is *dropped* and reported, exactly like the read-only case; only a
    # peer that keeps hammering past the limit is disconnected (and then with a
    # dedicated code so the client stops retrying). Killing the link on the
    # first over-limit paste used to produce a reconnect loop: every new
    # connection got a fresh token bucket.
    rate_strikes = 0
    rate_notice = False

    def over_limit(size: int) -> bool:
        nonlocal rate_strikes, rate_notice
        if bucket is None:
            return False
        if bucket.allow(size):
            rate_strikes = 0
            return False
        rate_strikes += 1
        if metrics is not None:
            metrics.inc("wsctl_input_rate_limited_total")
        # Same contract as ``reject_readonly`` / ``reject_backpressure``: report
        # the problem **once per connection**, not once per dropped chunk. The
        # strike that ends the connection is explained by ``deny`` alone, so a
        # flood produces at most one notice and one closing reason.
        if rate_strikes < MAX_RATE_STRIKES and not rate_notice:
            rate_notice = True
            reject()
        return True

    def persistently_over_limit() -> bool:
        return rate_strikes >= MAX_RATE_STRIKES

    def reject() -> None:
        with contextlib.suppress(ClientGone):
            client.put({"type": "notice", "level": "warn", "msg": "输入速率超限，部分按键已丢弃"})

    def reject_readonly() -> None:
        nonlocal read_only_notice
        if read_only_notice:
            return
        read_only_notice = True
        with contextlib.suppress(ClientGone):
            client.put({"type": "notice", "level": "warn", "msg": "会话为只读"})

    def reject_backpressure() -> None:
        nonlocal backpressure_notice
        if backpressure_notice:
            return
        backpressure_notice = True
        with contextlib.suppress(ClientGone):
            client.put(
                {"type": "notice", "level": "warn", "msg": "终端输入过快，部分按键已丢弃"}
            )

    oversized_notice = False

    def reject_oversized() -> None:
        """One notice per connection for a frame past the application bound."""
        nonlocal oversized_notice
        if oversized_notice:
            return
        oversized_notice = True
        with contextlib.suppress(ClientGone):
            client.put(
                {
                    "type": "notice",
                    "level": "warn",
                    "msg": "单条输入过大，已丢弃（可分批粘贴）",
                }
            )

    def reject_control_flood() -> None:
        """One notice per connection for control frames past the token bucket."""
        nonlocal control_notice
        if control_notice:
            return
        control_notice = True
        with contextlib.suppress(ClientGone):
            client.put(
                {"type": "notice", "level": "warn", "msg": "控制消息过快，部分已被丢弃"}
            )

    def reject_resync_flood() -> None:
        """A resync replays the scrollback; it must not be free to repeat.

        The client clears its terminal when it *sends* a resync (the server's
        replay is the only complete copy). A refusal that arrived as a generic
        notice therefore left a blank screen for the timeout window, so the
        refusal is a typed event the client can settle its state machine on.
        """
        nonlocal resync_notice
        if resync_notice:
            return
        resync_notice = True
        with contextlib.suppress(ClientGone):
            client.put(
                {
                    "type": "resync-rejected",
                    "msg": "重新同步过于频繁，请稍候再试",
                }
            )

    def forward_input(data: bytes) -> None:
        """Feed input to the child; report a drop instead of hiding it.

        Only input that actually reached the child is audited: a record
        implying a command ran when it was silently discarded would be worse
        than no record at all.
        """
        if session.closed:
            return
        if not session.write_input(data):
            reject_backpressure()
            return
        feed_audit(data)

    def feed_audit(data: bytes) -> None:
        if on_line is None:
            return
        line_buffer.extend(data)
        if len(line_buffer) > MAX_AUDIT_BUFFER:
            # A line with no newline must not grow the buffer without bound.
            del line_buffer[:-MAX_AUDIT_BUFFER]
        while b"\r" in line_buffer or b"\n" in line_buffer:
            indices = [i for i in (line_buffer.find(b"\r"), line_buffer.find(b"\n")) if i >= 0]
            cut = min(indices)
            line = bytes(line_buffer[:cut])
            del line_buffer[: cut + 1]
            if line.strip():
                on_line(line.decode("utf-8", "replace"))

    while True:
        if time.monotonic() - last_recheck >= ACCESS_RECHECK and await enforce_access():
            return
        if session.closed:
            # The session ended (exit, kill, account disabled...). Returning
            # closes the socket instead of leaving a viewer attached to a dead
            # terminal for up to the idle timeout.
            #
            # Wait for the finalizer's ``exit`` notice first: ``closed`` flips
            # before that notice is written, and returning on it detached the
            # client mid-finalize -- the browser never saw the exit code and
            # reconnected into a 4404 for a session that had simply exited.
            await session.wait_finalized(timeout=EXIT_DRAIN_TIMEOUT)
            return
        try:
            message = await asyncio.wait_for(websocket.receive(), timeout=ACCESS_RECHECK)
        except TimeoutError:
            # Only the *idle* rule lives here now. The share/auth re-validation
            # is time-driven (``enforce_access`` above) and would already have
            # fired on the way around the loop; keeping a second copy in this
            # branch is what made "checks only run when the peer goes quiet"
            # possible in the first place.
            if client.closed:
                # The session dropped this client (e.g. memory backpressure);
                # close the socket rather than lingering here.
                return
            if time.monotonic() - last_rx > IDLE_TIMEOUT:
                # No ping/input for a long time: the peer is gone (half-open).
                await deny("连接空闲超时，请重新连接", CLOSE_TIMEOUT)
                return
            continue
        last_rx = time.monotonic()
        if client.closed:
            # The session dropped this client (e.g. memory backpressure); stop
            # accepting further input from it.
            return
        msg_type = message.get("type")
        if msg_type == "websocket.disconnect":
            return
        data_bytes = message.get("bytes")
        if data_bytes is not None:
            if share_revoked():
                await deny("分享已撤销或过期", CLOSE_FORBIDDEN)
                return
            if not writable:
                reject_readonly()
                continue
            if len(data_bytes) > MAX_INPUT_FRAME:
                # Whole-or-nothing: a frame past the application bound never
                # reaches the PTY write buffer (which would hold it in full).
                #
                # This is *not* ``wsctl_pty_input_dropped_total``. That series
                # counts writes the PTY refused because the child stopped
                # reading; this one counts frames the WebSocket layer refused
                # before they got anywhere near the PTY. Both were counted
                # under the same name, so ``/metrics`` printed two series with
                # one name and two different meanings -- and the app.py comment
                # that warns about exactly this double registration named the
                # other three counters and missed this one.
                if metrics is not None:
                    metrics.inc("wsctl_ws_oversized_input_frames_total")
                reject_oversized()
                continue
            if over_limit(len(data_bytes)):
                if persistently_over_limit():
                    await deny("输入速率持续超限", CLOSE_RATE_LIMITED)
                    return
                continue
            forward_input(data_bytes)
            continue
        text = message.get("text")
        if not text:
            continue
        # One budget for every text frame, charged *before* parsing. Bounding
        # only the handlers left the JSON decoder itself unbounded -- and the
        # decoder is where a flood actually spends the server's CPU (a frame can
        # be a megabyte). Malformed frames pay a token too, which is the point.
        if control_bucket is not None and not control_bucket.allow(1.0):
            if metrics is not None:
                metrics.inc("wsctl_control_rate_limited_total")
            reject_control_flood()
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            # ``null``, ``[]`` and ``"text"`` are valid JSON but have no
            # ``.get``: they raised AttributeError and closed a healthy session
            # with 4500. A malformed control frame is ignored, not fatal.
            continue
        kind = data.get("type")
        if kind == "resize":
            if share_revoked():
                await deny("分享已撤销或过期", CLOSE_FORBIDDEN)
                return
            if writable:
                try:
                    session.resize(
                        int(data.get("cols") or 80), int(data.get("rows") or 24)
                    )
                except (TypeError, ValueError, OverflowError):
                    continue  # ignore a malformed resize rather than dropping the link
        elif kind == "input":
            # The same revocation check as the binary path. Text ``input`` used
            # to skip it entirely, so a revoked writable share kept working
            # until the periodic recheck noticed -- up to ACCESS_RECHECK later.
            # "Revoke" has to mean *now*, on every input path alike.
            if share_revoked():
                await deny("分享已撤销或过期", CLOSE_FORBIDDEN)
                return
            if not writable:
                reject_readonly()
                continue
            raw_data = data.get("data", "")
            if not isinstance(raw_data, str):
                # Anything else used to be ``str()``-ified and typed into the
                # shell -- a JSON object became the literal ``{'a': 1}``.
                continue
            chunk = raw_data.encode("utf-8")
            if len(chunk) > MAX_INPUT_FRAME:
                # Same distinction as the binary path above: a refused frame,
                # not a refused PTY write.
                if metrics is not None:
                    metrics.inc("wsctl_ws_oversized_input_frames_total")
                reject_oversized()
                continue
            if over_limit(len(chunk)):
                if persistently_over_limit():
                    await deny("输入速率持续超限", CLOSE_RATE_LIMITED)
                    return
                continue
            forward_input(chunk)
        elif kind == "ping":
            # The heartbeat doubles as the client's write-batching report: the
            # browser counts frames it merged and sends the saving here, so
            # "is coalescing actually happening on real pages" is answerable
            # from /metrics rather than only from a synthetic test.
            if share_revoked():
                await deny("分享已撤销或过期", CLOSE_FORBIDDEN)
                return
            coalesced = data.get("coalesced")
            if metrics is not None and isinstance(coalesced, (int, float)) and coalesced > 0:
                # Clamp: a client must not be able to inflate the metric (or
                # feed it a huge value) at will.
                metrics.inc("wsctl_ws_frames_coalesced_total", min(float(coalesced), 1e6))
            try:
                client.put({"type": "pong"})
            except ClientGone:
                return
        elif kind == "resync":
            # The viewer has learned its screen is incomplete (frames were shed)
            # and is asking for everything this server still holds, replayed into
            # a buffer it has already cleared. The whole replay is enqueued in one
            # synchronous stretch -- no ``await`` between the chunks -- so nothing
            # can interleave and reorder it.
            #
            # A best effort *by construction*: bytes the server also shed are
            # gone, and a full-screen application may still need its own repaint
            # afterwards. The client says exactly that instead of claiming a
            # restore it cannot deliver.
            #
            # The revocation check is *not* optional here: ``resync`` hands out
            # the whole scrollback, and the session-level ``share_valid`` filter
            # in ``_broadcast`` never sees this path (it replays straight from
            # the session buffer). A revoked viewer used to be able to pull the
            # entire history this way.
            if share_revoked():
                await deny("分享已撤销或过期", CLOSE_FORBIDDEN)
                return
            # A resync replays up to the whole scrollback and (for a writer)
            # asks the application to repaint. It cannot become stale faster
            # than a human can notice, so repeats are refused with a notice
            # rather than served for free.
            if resync_bucket is not None and not resync_bucket.allow(1.0):
                if metrics is not None:
                    metrics.inc("wsctl_resync_rate_limited_total")
                reject_resync_flood()
                continue
            if metrics is not None:
                metrics.inc("wsctl_shed_resync_requests_total")
            try:
                # ``resync-begin`` marks where the replay starts. The viewer
                # drops live frames up to this point (they are duplicates of
                # what is about to be replayed) and accepts everything from
                # here on -- without the marker it cannot tell the replay apart
                # from the live stream and would either drop the replay it just
                # asked for or draw the same bytes twice.
                client.put({"type": "resync-begin", "session": session.id})
                before = client.dropped_events
                for chunk in session.scrollback_chunks(replay_target_for(client)):
                    client.put(chunk)
                # This replay can shed too -- and what it sheds is the *head* of
                # the very screen being rebuilt. ``resynced`` must not then
                # announce success; the viewer would clear its desync bar while
                # the display is still missing its beginning.
                incomplete = int(client.dropped_events) > before
                client.put(
                    {
                        "type": "resynced",
                        "session": session.id,
                        "incomplete": incomplete,
                    }
                )
                # And then the thing replaying bytes cannot do: ask the
                # application to redraw from its own model. Replaying the
                # same tail into a full-screen app reproduces the same
                # garbage -- the app is the only party that knows what its
                # screen is supposed to look like. A read-only viewer needs
                # this as much as a writer (it is the only way its TUI screen
                # can be repaired); what a viewer must not have is an
                # *unbounded* supply of it, which is the bucket above.
                await session.nudge_repaint()
            except ClientGone:
                return
