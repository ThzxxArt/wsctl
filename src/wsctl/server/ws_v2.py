"""WebSocket protocol v2 endpoint.

Compared with v1, this endpoint:

* negotiates ``wsctl.terminal.v2``;
* frames every server→client data chunk with its global byte offset;
* resumes exactly from ``last_seq`` when the scrollback still holds it;
* falls back to a real screen snapshot (tmux/pyte) when it does not;
* receives ACKs and reports the exact lost range as ``gap``;
* keeps the v1 close-code vocabulary and the same security checks.

The legacy ``/ws`` endpoint remains available for one release. It speaks the
old unframed protocol, which is why protocol v2 is a separate endpoint rather
than a flag: a client that guesses wrong must fail loudly, not misinterpret a
sequence header as terminal text.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from fastapi import FastAPI, WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from wsctl.core.closecodes import (
    CLOSE_BAD_REQUEST,
    CLOSE_FORBIDDEN,
    CLOSE_LIMIT,
    CLOSE_NOT_FOUND,
    CLOSE_OVERLOAD,
    CLOSE_PROTOCOL_VERSION,
    CLOSE_RATE_LIMITED,
    CLOSE_SERVER_ERROR,
    CLOSE_TIMEOUT,
    CLOSE_UNAUTHORIZED,
)
from wsctl.core.config import Settings
from wsctl.core.metrics import Metrics
from wsctl.core.observability import (
    WS_ACK_LAG_BYTES,
    WS_GAP_TOTAL,
    WS_SNAPSHOT_TOTAL,
    observe_input_latency,
    session_id_var,
    span,
    user_id_var,
)
from wsctl.core.protocol import (
    SUBPROTOCOL,
    ProtocolError,
    decode_control,
    decode_input,
    encode_control,
)
from wsctl.core.pty import PtyError
from wsctl.core.recording import has_room as recordings_have_room
from wsctl.core.session import (
    ClientGone,
    SessionManager,
    within_user_quota,
)
from wsctl.core.store import Store, User

from .client import WsClient
from .deps import resolve_token_user, token_from_request
from .security import (
    client_ip,
    forwarded_scheme,
    ip_allowed,
    is_origin_allowed,
)
from .services.events import EventBroker
from .services.shares import ShareService
from .ws import (
    ACCESS_RECHECK,
    CONTROL_BURST,
    CONTROL_RATE,
    DETACH_TIMEOUT,
    EXIT_DRAIN_TIMEOUT,
    HANDSHAKE_TIMEOUT,
    IDLE_TIMEOUT,
    MAX_INPUT_FRAME,
    MAX_RATE_STRIKES,
    RESYNC_BURST,
    RESYNC_RATE,
    ConnectionCaps,
    _default_spec,
    _Denied,
    _input_auditor,
    _input_bucket,
    _resolve_user,
    api_key_scopes,
)

log = logging.getLogger("wsctl.ws.v2")


async def terminal_endpoint_v2(websocket: WebSocket) -> None:
    app: FastAPI = websocket.app
    settings: Settings = app.state.settings
    store: Store = app.state.store
    manager: SessionManager = app.state.manager
    metrics: Metrics = app.state.metrics
    audit = app.state.audit
    shares: ShareService | None = getattr(app.state, "shares", None)
    events: EventBroker | None = getattr(app.state, "events", None)

    ip = client_ip(websocket, settings)
    user = await _resolve_user(websocket, store, settings.auth_required)
    key_scopes = await api_key_scopes(websocket)
    auth_token = token_from_request(websocket)
    share_param = websocket.query_params.get("share")

    offered = websocket.scope.get("subprotocols") or []
    subprotocol = SUBPROTOCOL if SUBPROTOCOL in offered else None
    await websocket.accept(subprotocol=subprotocol)
    metrics.inc("wsctl_ws_connections_total", protocol="v2")

    if not ip_allowed(ip, settings.allowed_ips):
        audit.enqueue("ip_rejected", ip=ip, payload="ws/v2")
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

    if user is None and not share_param:
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return

    caps: ConnectionCaps = app.state.ws_caps
    slot = caps.admit(ip, settings)
    if slot is None:
        audit.enqueue("ws_capacity_rejected", ip=ip, payload="ws/v2")
        with contextlib.suppress(Exception):
            await websocket.send_text(
                encode_control({"type": "error", "msg": "连接数已达上限，请稍后重连"})
            )
        await websocket.close(code=CLOSE_OVERLOAD)
        return

    task = asyncio.current_task()
    if task is not None:
        task.add_done_callback(lambda _done: caps.release(slot))

    client = WsClient(
        websocket,
        max_bytes=settings.client_max_bytes,
        protocol_version=2,
        on_gap=lambda _start, _end: _gap_metric("shed"),
    )
    writer = asyncio.create_task(client.run())
    session: Any = None
    user_id = user.id if user is not None else None
    close_code = 1000
    try:
        result = await _handshake_v2(
            websocket,
            client,
            settings,
            manager,
            store,
            shares,
            user,
            share_param,
            ip,
            events,
            key_scopes,
        )
        if result is None:
            return
        session, writable, can_resize, share_filter = result
        if user_id is not None:
            user_id_var.set(str(user_id))
        session_id_var.set(str(session.id))
        if shares is not None:
            # The broadcast filter runs on every chunk and must not touch the
            # database; this installs the cached resolver for this stream.
            session.set_external_share_resolver(
                shares.resolver(session.id), shares.known(session.id)
            )
        client.on_shed = session.request_repaint
        client.viewer_label = user.username if user is not None else "分享观众"
        if events is not None:
            events.publish(
                "presence",
                owner_id=session.owner_id,
                session=session.id,
                clients=session.client_count,
                viewers=[entry["viewer"] for entry in session.clients()],
                action="attach",
            )
        audit.enqueue(
            "session_attach",
            user_id=user_id,
            term_session_id=session.id,
            ip=ip,
            payload="readonly" if not writable else "v2",
        )
        await _pump_v2(
            websocket,
            client,
            session,
            settings,
            audit,
            user_id,
            share_filter,
            writable,
            can_resize,
            shares,
            metrics,
            auth_token=auth_token,
            key_scopes=key_scopes,
        )
        if getattr(client, "close_code", None):
            code = client.close_code
            if code is not None:
                close_code = int(code)
    except _Denied as exc:
        close_code = exc.code
        with contextlib.suppress(ClientGone):
            client.put({"type": "error", "msg": exc.message})
    except ProtocolError as exc:
        close_code = CLOSE_BAD_REQUEST
        with contextlib.suppress(ClientGone):
            client.put({"type": "error", "msg": str(exc)})
    except Exception:
        log.exception("websocket v2 handler failed")
        if close_code == 1000:
            close_code = CLOSE_SERVER_ERROR
    finally:
        if session is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(session.detach(client), timeout=DETACH_TIMEOUT)
            audit.enqueue(
                "session_detach", user_id=user_id, term_session_id=session.id, ip=ip
            )
            if events is not None:
                events.publish(
                    "presence",
                    owner_id=session.owner_id,
                    session=session.id,
                    clients=session.client_count,
                    viewers=[entry["viewer"] for entry in session.clients()],
                    action="detach",
                )
        client.close()
        with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError, Exception):
            await asyncio.wait_for(writer, timeout=2.0)
        writer.cancel()
        final_code = getattr(client, "close_code", None) or close_code
        if websocket.application_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await websocket.close(code=final_code)
        caps.release(slot)


async def _handshake_v2(
    websocket: WebSocket,
    client: WsClient,
    settings: Settings,
    manager: SessionManager,
    store: Store,
    shares: ShareService | None,
    user: User | None,
    share_param: str | None,
    ip: str | None,
    events: EventBroker | None,
    key_scopes: set[str] | None,
) -> tuple[Any, bool, bool, str | None] | None:
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=HANDSHAKE_TIMEOUT)
    except TimeoutError:
        raise _Denied("握手超时：连接后 5 秒内没有发送 attach", CLOSE_TIMEOUT) from None
    except WebSocketDisconnect:
        raise _Denied("客户端在握手期间断开", CLOSE_BAD_REQUEST) from None
    except (KeyError, RuntimeError):
        raise _Denied("首条消息必须是 JSON 格式的 attach", CLOSE_BAD_REQUEST) from None
    try:
        msg = decode_control(raw)
    except ProtocolError as exc:
        text = str(exc)
        code = CLOSE_PROTOCOL_VERSION if "协议版本" in text else CLOSE_BAD_REQUEST
        raise _Denied(text, code) from None
    if msg.get("type") != "attach":
        raise _Denied("首条消息必须是 attach", CLOSE_BAD_REQUEST)

    try:
        cols = min(max(1, int(msg.get("cols") or 80)), 1000)
        rows = min(max(1, int(msg.get("rows") or 24)), 1000)
    except (TypeError, ValueError, OverflowError):
        raise _Denied("cols/rows 必须是整数", CLOSE_BAD_REQUEST) from None
    last_seq_raw = msg.get("last_seq")
    last_seq: int | None
    if last_seq_raw is None:
        last_seq = None
    elif (
        isinstance(last_seq_raw, bool)
        or not isinstance(last_seq_raw, int)
        or last_seq_raw < 0
    ):
        raise _Denied("last_seq 必须是非负整数", CLOSE_BAD_REQUEST)
    else:
        last_seq = last_seq_raw
    generation = msg.get("generation")
    if generation is not None and not isinstance(generation, str):
        raise _Denied("generation 必须是字符串", CLOSE_BAD_REQUEST)

    sid = msg.get("session")
    share = msg.get("share") or share_param
    if share is not None and not isinstance(share, str):
        raise _Denied("share 必须是字符串", CLOSE_BAD_REQUEST)

    # A share URL may carry only the token; resolve the session it points at.
    if sid is None and share and shares is not None:
        record = await asyncio.to_thread(shares.resolve, share)
        if record is None:
            raise _Denied("分享链接无效或已过期", CLOSE_FORBIDDEN)
        sid = record.term_session_id

    session: Any = None
    writable = True
    can_resize = True
    created = False
    owner_connection = False
    if sid:
        session = manager.get(str(sid))
        if session is None:
            raise _Denied(f"会话不存在：{sid}", CLOSE_NOT_FOUND)
        legacy_scope = session.legacy_share_scope(share) if share else None
        db_scope = None
        if share and shares is not None:
            record = await asyncio.to_thread(shares.resolve, share)
            if record is not None and record.term_session_id == session.id:
                db_scope = record.scope
        scope = legacy_scope or db_scope
        owner_connection = user is not None and (
            user.role == "admin" or session.owner_id == user.id
        )
        if not owner_connection and scope is None:
            raise _Denied("无权访问该会话", CLOSE_FORBIDDEN)
        if owner_connection:
            writable = True
            can_resize = True
        else:
            writable = scope in ("write", "control")
            can_resize = scope == "control"
        if key_scopes is not None and "admin" not in key_scopes:
            writable = writable and "write" in key_scopes
            # Resizing is a write operation on the terminal, not an admin
            # operation. Requiring the management scope here made every
            # non-admin API key unable to resize, even with ``write``.
            can_resize = can_resize and "write" in key_scopes
        if shares is not None:
            session.set_external_share_resolver(
                shares.resolver(session.id), shares.known(session.id)
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
        spec.snapshot_mode = settings.snapshot_mode
        spec.shell_integration = settings.shell_integration
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
                payload=str(msg.get("name") or session.spec.name)[:512],
            )
            if events is not None:
                events.publish(
                    "session_created",
                    owner_id=user.id if user.id > 0 else None,
                    session=session.id,
                    name=session.spec.name,
                    owner=user.username,
                )
        except Exception:
            await manager.remove(session.id)
            raise _Denied("创建会话失败", CLOSE_SERVER_ERROR) from None

    if writable and can_resize:
        session.resize(cols, rows)
    filter_share = None if owner_connection else share
    try:
        with span(
            "ws.attach",
            session=session.id,
            writable=writable,
            resumed=last_seq is not None,
        ):
            meta = await session.attach_v2(
                client,
                writable=writable,
                share=filter_share,
                last_seq=last_seq,
                generation=generation,
            )
    except ClientGone as exc:
        if created:
            await manager.remove(session.id)
        raise _Denied(str(exc), CLOSE_LIMIT) from exc
    except Exception:
        if created:
            await manager.remove(session.id)
        raise
    if meta.get("snapshot_applied"):
        _snapshot_metric("ok", "session")
    elif meta.get("snapshot_required"):
        _snapshot_metric("missing", "raw")
    return session, writable, can_resize, filter_share


def _snapshot_metric(result: str, source: str) -> None:
    with contextlib.suppress(Exception):
        WS_SNAPSHOT_TOTAL.labels(result=result, source=source).inc()


def _gap_metric(reason: str) -> None:
    with contextlib.suppress(Exception):
        WS_GAP_TOTAL.labels(reason=reason).inc()


async def _pump_v2(
    websocket: WebSocket,
    client: WsClient,
    session: Any,
    settings: Settings,
    audit: Any,
    user_id: int | None,
    share: str | None,
    writable: bool,
    can_resize: bool,
    shares: ShareService | None,
    metrics: Metrics,
    *,
    auth_token: str | None = None,
    key_scopes: set[str] | None = None,
) -> None:
    from wsctl.core.ratelimit import TokenBucket

    line_buffer = bytearray()
    on_line = _input_auditor(audit, settings, user_id, session.id)
    bucket = _input_bucket(settings)
    control_bucket = TokenBucket(CONTROL_RATE, CONTROL_BURST)
    resync_bucket = TokenBucket(RESYNC_RATE, RESYNC_BURST)
    last_rx = time.monotonic()
    last_recheck = time.monotonic()
    rate_strikes = 0
    notices: set[str] = set()

    def notice(key: str, message: str) -> None:
        if key in notices:
            return
        notices.add(key)
        with contextlib.suppress(ClientGone):
            client.put({"type": "notice", "level": "warn", "msg": message})

    def feed_audit(data: bytes) -> None:
        if on_line is None:
            return
        line_buffer.extend(data)
        if len(line_buffer) > 8192:
            del line_buffer[:-8192]
        while b"\r" in line_buffer or b"\n" in line_buffer:
            indices = [i for i in (line_buffer.find(b"\r"), line_buffer.find(b"\n")) if i >= 0]
            cut = min(indices)
            line = bytes(line_buffer[:cut])
            del line_buffer[: cut + 1]
            if line.strip():
                on_line(line.decode("utf-8", "replace"))

    def forward_input(data: bytes) -> None:
        if session.closed:
            return
        started = time.perf_counter()
        if not session.write_input(data):
            notice("backpressure", "终端输入过快，部分按键已丢弃")
            return
        observe_input_latency(started)
        feed_audit(data)

    def over_limit(size: int) -> bool:
        nonlocal rate_strikes
        if bucket is None:
            return False
        if bucket.allow(size):
            rate_strikes = 0
            return False
        rate_strikes += 1
        metrics.inc("wsctl_input_rate_limited_total")
        if rate_strikes < MAX_RATE_STRIKES:
            notice("rate", "输入速率超限，部分按键已丢弃")
        return True

    async def deny(message: str, code: int = CLOSE_UNAUTHORIZED) -> None:
        with contextlib.suppress(ClientGone):
            client.put({"type": "error", "msg": message})
        raise _Denied(message, code)

    async def send_snapshot(*, requested: bool) -> bool:
        """Send a screen snapshot (or a sequenced full replay) and mark it."""
        if not resync_bucket.allow(1.0):
            with contextlib.suppress(ClientGone):
                client.put(
                    {
                        "type": "resync-rejected",
                        "msg": "重新同步过于频繁，请稍候再试",
                    }
                )
            return False
        with contextlib.suppress(ClientGone):
            client.put(
                {
                    "type": "resync-begin",
                    "session": session.id,
                    "requested": requested,
                    "seq": session.end_seq,
                }
            )
        try:
            with span("ws.snapshot", session=session.id, requested=requested):
                # Capture *and* queue under the session lock. Doing only the
                # capture there still let live output jump ahead of the
                # snapshot frame, which paints over bytes the client just got.
                result = await session.resync(client, requested=requested)
        except ClientGone:
            return False
        with contextlib.suppress(ClientGone):
            client.put(
                {
                    "type": "snapshot",
                    "seq": result["seq"],
                    "format": result["format"],
                    "source": result["source"],
                    "bytes": session.scrollback_size
                    if result["format"] == "raw"
                    else 0,
                    "requested": requested,
                }
            )
            client.put(
                {
                    "type": "resynced",
                    "session": session.id,
                    "seq": result["seq"],
                    "snapshot": result["format"] == "ansi",
                    "incomplete": result["incomplete"],
                }
            )
        if result["format"] == "ansi":
            _snapshot_metric("ok", result["source"])
        else:
            _snapshot_metric("fallback", "raw")
            with contextlib.suppress(Exception):
                await session.nudge_repaint()
        return True

    async def refresh_share() -> bool:
        """Re-check a share at most every few seconds.

        Covers both authorities: the legacy single token stored on the session
        and the database-backed row. Checking only the database made a legacy
        link report "revoked" on the first input after the cache expired.
        """
        if share is None:
            return True
        if await asyncio.to_thread(session.share_valid, share):
            return True
        if shares is None:
            return False
        record = await asyncio.to_thread(shares.resolve, share)
        return record is not None and record.term_session_id == session.id

    async def revalidate_access() -> None:
        """Re-check login identity and share authority every few seconds.

        The v2 pump used to re-check only the share, so a logged-out browser or
        a disabled account kept a writable terminal until it closed the tab.
        """
        nonlocal writable, can_resize
        fresh = None
        if auth_token and user_id is not None:
            fresh = await asyncio.to_thread(resolve_token_user, websocket, auth_token)
            if fresh is None:
                await deny("登录已失效，请重新登录", CLOSE_UNAUTHORIZED)
        # No ``auth_token`` (or an anonymous share with a stale cookie) is a
        # share-only connection: identity is the token in the URL, not the
        # cookie. Requiring a fresh login there denied a perfectly valid share.
        owner = fresh is not None and (
            fresh.role == "admin" or session.owner_id == fresh.id
        )
        if owner:
            writable, can_resize = True, True
        else:
            # Resolve the database row explicitly on every re-check. The cached
            # resolver used by the broadcast filter is intentionally tolerant
            # of a cold/aged entry, so it cannot be the authority that notices
            # a revocation performed by another process.
            legacy_scope = session.legacy_share_scope(share) if share else None
            db_scope = None
            if share is not None and shares is not None:
                record = await asyncio.to_thread(shares.resolve, share)
                if record is not None and record.term_session_id == session.id:
                    db_scope = record.scope
            scope = legacy_scope or db_scope
            if scope is None:
                await deny("无权访问该会话", CLOSE_FORBIDDEN)
                return
            writable = scope in ("write", "control")
            can_resize = scope == "control"
        if key_scopes is not None and "admin" not in key_scopes:
            writable = writable and "write" in key_scopes
            can_resize = can_resize and "write" in key_scopes

    while True:
        try:
            if time.monotonic() - last_recheck >= ACCESS_RECHECK:
                last_recheck = time.monotonic()
                await revalidate_access()
            if session.closed:
                await session.wait_finalized(timeout=EXIT_DRAIN_TIMEOUT)
                return
            message = await asyncio.wait_for(websocket.receive(), timeout=ACCESS_RECHECK)
        except TimeoutError:
            if client.closed:
                return
            if time.monotonic() - last_rx > IDLE_TIMEOUT:
                await deny("连接空闲超时，请重新连接", CLOSE_TIMEOUT)
            continue
        last_rx = time.monotonic()
        if client.closed:
            return
        msg_type = message.get("type")
        if msg_type == "websocket.disconnect":
            return
        data_bytes = message.get("bytes")
        if data_bytes is not None:
            try:
                frame = decode_input(data_bytes)
            except ProtocolError as exc:
                await deny(str(exc), CLOSE_BAD_REQUEST)
            if not writable:
                notice("readonly", "会话为只读")
                continue
            if len(frame.payload) > MAX_INPUT_FRAME:
                metrics.inc("wsctl_ws_oversized_input_frames_total")
                notice("oversized", "单条输入过大，已丢弃（可分批粘贴）")
                continue
            if over_limit(len(frame.payload)):
                if rate_strikes >= MAX_RATE_STRIKES:
                    await deny("输入速率持续超限", CLOSE_RATE_LIMITED)
                continue
            forward_input(frame.payload)
            continue

        text = message.get("text")
        if not text:
            continue
        if not control_bucket.allow(1.0):
            metrics.inc("wsctl_control_rate_limited_total")
            notice("control", "控制消息过快，部分已被丢弃")
            continue
        try:
            data = decode_control(text)
        except ProtocolError as exc:
            await deny(str(exc), CLOSE_BAD_REQUEST)
        kind = data.get("type")
        if kind == "ack":
            seq = data.get("seq")
            if isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0:
                client.ack(seq)
                lag = max(0, session.end_seq - client.last_acked_seq)
                with contextlib.suppress(Exception):
                    WS_ACK_LAG_BYTES.set(float(lag))
        elif kind == "resize":
            if not can_resize:
                notice("control-only", "当前分享没有控制权限")
                continue
            try:
                session.resize(int(data.get("cols") or 80), int(data.get("rows") or 24))
            except (TypeError, ValueError, OverflowError):
                continue
        elif kind == "input":
            if not writable:
                notice("readonly", "会话为只读")
                continue
            raw_data = data.get("data")
            if not isinstance(raw_data, str):
                continue
            chunk = raw_data.encode("utf-8")
            if len(chunk) > MAX_INPUT_FRAME:
                metrics.inc("wsctl_ws_oversized_input_frames_total")
                notice("oversized", "单条输入过大，已丢弃（可分批粘贴）")
                continue
            if over_limit(len(chunk)):
                if rate_strikes >= MAX_RATE_STRIKES:
                    await deny("输入速率持续超限", CLOSE_RATE_LIMITED)
                continue
            forward_input(chunk)
        elif kind == "ping":
            coalesced = data.get("coalesced")
            if isinstance(coalesced, (int, float)) and coalesced > 0:
                metrics.inc("wsctl_ws_frames_coalesced_total", min(float(coalesced), 1e6))
            with contextlib.suppress(ClientGone):
                client.put(
                    {
                        "type": "pong",
                        "nonce": data.get("nonce"),
                        "seq": session.end_seq,
                    }
                )
        elif kind in ("resync", "snapshot-request"):
            if share is not None and not await refresh_share():
                await deny("分享已撤销或过期", CLOSE_FORBIDDEN)
            metrics.inc("wsctl_shed_resync_requests_total")
            await send_snapshot(requested=True)
        elif kind == "claim-control":
            # ``claim-control`` is not a privilege escalation: a write share
            # still may not resize the owner's terminal. Only a connection
            # that already holds control (owner/control scope) is acknowledged.
            if can_resize:
                with contextlib.suppress(ClientGone):
                    client.put({"type": "control", "owner": True})
            else:
                notice("control-only", "当前分享没有控制权限")
        elif kind == "close":
            return
        else:
            # Unknown-but-valid control types are ignored for forward
            # compatibility; malformed ones were rejected by the decoder.
            continue


__all__ = ["terminal_endpoint_v2"]
