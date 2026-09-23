"""What happened to a session: history rows, per-session detail, overview.

The database has always recorded ``status`` (running / stopped / expired /
killed), the owning instance, ``argv`` and ``cwd``, and a retention policy to
prune old rows -- and nothing ever read them. A session that ended simply
vanished from the product. These endpoints are the missing read path.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from wsctl import __version__
from wsctl.core import session as session_mod
from wsctl.core.store import User

from ..deps import current_user, require_admin, serialize_session

router = APIRouter(prefix="/api", tags=["status"])


#: Fallback sentences for rows written before ``end_reason`` existed.
_REASONS = {
    "killed": "被管理员终止",
    "expired": "超过空闲或最长寿命",
    "stopped": "服务退出或实例崩溃",
    "interrupted": "实例中断",
}


def _history_row(row: dict[str, Any]) -> dict[str, Any]:
    """Shape a finished ``term_sessions`` row for the UI."""
    created = float(row.get("created_at") or 0.0)
    last = float(row.get("last_active") or created)
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "pid": None,  # the process is gone; the column does not exist
        "owner_id": row.get("owner_id"),
        "owner": None,
        "backend": row.get("backend"),
        "command": row.get("command"),
        "argv": row.get("argv"),
        "cwd": row.get("cwd"),
        "status": row.get("status"),
        # ``status`` is the enum, ``ended_reason`` is the sentence: "killed"
        # versus "被管理员终止". A bare enum left the user guessing.
        "ended_reason": row.get("end_reason") or _REASONS.get(str(row.get("status")), ""),
        "created_at": created,
        "last_active": last,
        # Round it once here rather than making every client guess at the
        # meaning of two timestamps.
        "duration": max(0.0, last - created),
        # Symmetric with the live path (see deps.serialize_session): a finished
        # session has no clients, no buffer and no share, and saying so is
        # clearer than omitting the keys and making the reader branch.
        "clients": 0,
        "bytes": 0,
        "alive": False,
        "shared": False,
        "share_writable": False,
        "recording": False,
    }


@router.get("/sessions/history")
async def session_history(
    request: Request,
    user: User = Depends(current_user),
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Finished sessions: stopped, expired or killed.

    Admins see every row; a normal user sees only their own. The rows already
    lived in ``term_sessions`` and were pruned by ``term_session_retention_days``
    -- retained and cleaned up, but never shown.
    """
    owner = None if user.role == "admin" else user.id
    rows = await asyncio.to_thread(
        request.app.state.store.term_session_history, owner_id=owner, limit=limit
    )
    return [_history_row(row) for row in rows]


@router.get("/sessions/{sid}/detail")
async def session_detail(
    sid: str, request: Request, user: User = Depends(current_user)
) -> dict[str, Any]:
    """Everything known about one session, live or finished.

    Deliberately richer than the list view: ``argv``/``cwd`` can contain
    sensitive paths, so a normal user only ever sees their own and admins see
    all (documented in SECURITY.md).
    """
    store = request.app.state.store
    manager = request.app.state.manager
    live = manager.get(sid)
    row = await asyncio.to_thread(store.term_session_get, sid)
    if live is None and row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")

    owner_id = live.owner_id if live is not None else (row or {}).get("owner_id")
    if user.role != "admin" and owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该会话")

    if live is not None:
        payload = serialize_session(live)
        payload["state"] = "running" if live.is_alive else "exited"
        payload["exit_code"] = live.exit_code
    else:
        payload = _history_row(row or {})
        payload["state"] = "finished"
        payload["exit_code"] = None

    if owner_id is not None:
        owner = await asyncio.to_thread(store.user_get_by_id, int(owner_id))
        payload["owner"] = owner.username if owner is not None else None

    clients: list[dict[str, Any]] = []
    if live is not None:
        for entry in live.clients():
            clients.append({"writable": entry["writable"], "shared": entry["share"] is not None})
    payload["attached"] = clients
    payload["share_active"] = live.peek_share() is not None if live is not None else False
    payload["recording_path"] = (
        str(live.recording_path) if live is not None and live.recording_path else None
    )
    payload["exit_code"] = live.exit_code if live is not None else None
    payload["instance_id"] = (row or {}).get("instance_id")
    # The session's story: create / attach / detach / kill …, oldest first.
    payload["timeline"] = await asyncio.to_thread(store.term_session_timeline, sid)
    return payload


@router.get("/overview")
async def overview(request: Request, _: User = Depends(require_admin)) -> dict[str, Any]:
    """Admin dashboard: what is running, on what, and what just happened.

    Everything here was already observable piecemeal (``/healthz``,
    ``/metrics``, ``/api/audit``, ``instances``) but required four separate
    lookups to answer "is this box healthy".
    """
    store = request.app.state.store
    manager = request.app.state.manager
    settings = request.app.state.settings
    metrics = request.app.state.metrics

    live = manager.list_sessions()
    owners = {s.owner_id: None for s in live if s.owner_id is not None}
    if owners:
        resolved = await asyncio.to_thread(store.users_by_ids, set(owners))
        for key in owners:
            owners[key] = resolved.get(key)

    instances = await asyncio.to_thread(store.instance_all)
    recent = await asyncio.to_thread(store.recent_audit, 20)
    history = await asyncio.to_thread(store.term_session_history, owner_id=None, limit=5)

    return {
        "version": __version__,
        "sessions": len(live),
        "clients": sum(s.client_count for s in live),
        "bytes": sum(s.memory_usage() for s in live),
        "evicted_clients": session_mod.evicted_clients_total(),
        "max_sessions": settings.max_sessions,
        "instances": [
            {
                "id": inst.get("id"),
                "pid": inst.get("pid"),
                "host": inst.get("host"),
                "heartbeat": inst.get("heartbeat"),
                "age": max(0.0, time.time() - float(inst.get("heartbeat") or 0.0)),
            }
            for inst in instances
        ],
        "recent_audit": [
            {
                "ts": row.get("ts"),
                "event": row.get("event"),
                "user_id": row.get("user_id"),
                "ip": row.get("ip"),
                "payload": row.get("payload"),
            }
            for row in recent
        ],
        "recently_finished": [_history_row(row) for row in history],
        # Curated gauges, not a raw dump of the first 40 exposition lines:
        # an operator asking "is this box healthy" wants four numbers, and a
        # text scrape truncated mid-series is worse than nothing.
        "metrics": _key_metrics(metrics),
    }


def _key_metrics(metrics: Any) -> dict[str, float]:
    """The handful of numbers that answer "is this box healthy"."""
    gauges: dict[str, float] = {}
    for line in metrics.render().splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, value = line.partition(" ")
        try:
            gauges[name] = float(value)
        except ValueError:
            continue
    wanted = (
        "wsctl_up",
        "wsctl_sessions",
        "wsctl_clients",
        "wsctl_session_bytes",
        "wsctl_maintenance_lag_seconds",
        "wsctl_pty_input_dropped_total",
        "wsctl_audit_dropped_total",
        "wsctl_audit_write_errors_total",
        "wsctl_recording_failures_total",
    )
    return {key: gauges[key] for key in wanted if key in gauges}
