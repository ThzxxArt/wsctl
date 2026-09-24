"""Cross-instance reconciliation: adopt orphans, leave live peers alone."""

from __future__ import annotations

import logging
import os
import socket
import sys
from typing import Any

from fastapi import FastAPI

from wsctl.core import tmux
from wsctl.core.config import Settings
from wsctl.core.session import SessionManager, SessionSpec
from wsctl.core.store import Store

log = logging.getLogger("wsctl.app")


def pid_alive(pid: int | None) -> bool:
    """Whether a local pid is still running (best-effort, POSIX only).

    On Windows ``os.kill(pid, 0)`` is not a liveness probe -- CPython maps it
    to ``TerminateProcess`` -- so we never probe there and fall back to the
    heartbeat TTL instead.
    """
    if sys.platform == "win32":
        return True
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    except OSError:
        return False
    return True


def prune_dead_local_leases(store: Store, me: str, host: str) -> None:
    """Immediately drop leases of crashed same-host instances.

    Heartbeat TTL alone would make crash recovery wait up to ``instance_ttl``;
    a same-host pid check lets a restarted server adopt orphaned sessions at
    once instead of after the lease expires.
    """
    for inst in store.instance_all():
        if inst["id"] == me or inst["host"] != host:
            continue
        if not pid_alive(inst["pid"]):
            store.instance_remove(str(inst["id"]))


async def reconcile_instances(app: FastAPI) -> None:
    """Adopt sessions abandoned by dead instances; leave live peers alone.

    Runs at startup and on every maintenance tick so that a session becomes
    adoptable as soon as its owning instance goes away (rather than only on the
    next restart). tmux sessions are namespaced per data directory, so a peer
    using a different data directory on the same host is never mistaken for an
    orphan.
    """
    settings: Settings = app.state.settings
    store: Store = app.state.store
    manager: SessionManager = app.state.manager
    me = app.state.instance_id
    host = socket.gethostname()
    prune_dead_local_leases(store, me, host)

    rows = store.term_session_list()
    tick = int(getattr(app.state, "_reconcile_tick", 0))
    app.state._reconcile_tick = tick + 1
    has_tmux_rows = any(row.get("backend") == "tmux" for row in rows)
    # Probing tmux spawns a subprocess; only scan for orphans when there are
    # tmux-backed rows, and otherwise just occasionally.
    if tmux.is_available() and (has_tmux_rows or tick % 12 == 0):
        known = {str(row["id"]) for row in rows}
        for name in await tmux.list_sessions_async():
            if tmux.owns(name) and tmux.sid_from_name(name) not in known:
                await tmux.kill_session_async(name)
                log.info("reaped orphan tmux session %s", name)

    live = store.instance_alive_ids(settings.instance_ttl) - {me}
    for row in rows:
        if row.get("status") not in ("running", "interrupted"):
            continue
        if row.get("instance_id") in live:
            continue  # a live peer still owns this session
        sid = str(row["id"])
        if manager.get(sid) is not None:
            continue  # already held in this process
        if row.get("backend") != "tmux":
            # A local session died together with its instance.
            store.term_session_set_status(sid, "stopped", "服务退出或实例崩溃")
            continue
        if not await tmux.has_session_async(tmux.session_name(sid)):
            store.term_session_set_status(sid, "stopped", "tmux 会话已不存在")
            continue
        spec = SessionSpec(
            name=str(row["name"]),
            argv=[settings.shell],
            cwd=row.get("cwd") or settings.default_cwd,
            backend="tmux",
            idle_timeout=settings.idle_timeout,
            max_life=settings.max_life,
            max_clients=settings.session_max_clients,
            memory_limit=settings.session_memory_limit,
            scrollback_bytes=settings.scrollback_bytes,
        )
        try:
            session = await manager.create(spec, sid=sid, owner_id=row.get("owner_id"))
        except (ValueError, OSError, tmux.TmuxError):
            continue
        # A share link outlives a restart too: the token is rehydrated verbatim
        # so a QR code already handed out keeps working.
        session.restore_share(
            row.get("share_token"),
            row.get("share_expires"),
            bool(row.get("share_writable")),
        )
        store.term_session_set_instance(sid, me)
        store.term_session_set_status(sid, "running", "已接管")
        log.info("adopted tmux session %s", sid)


def retention_cutoffs(now: float, settings: Settings) -> dict[str, float | None]:
    """Cutoffs for the retention sweeps (``None`` disables one)."""
    return {
        "audit": now - settings.audit_retention_days * 86400
        if settings.audit_retention_days > 0
        else None,
        "term_sessions": now - settings.term_session_retention_days * 86400
        if settings.term_session_retention_days > 0
        else None,
        "recordings": now - settings.recordings_retention_days * 86400
        if settings.recordings_retention_days > 0
        else None,
    }


def dead_instance_ids(store: Store, ttl: float, me: str) -> list[str]:
    return [dead for dead in store.instance_dead_ids(ttl) if dead != me]


def active_recording_paths(manager: SessionManager) -> set[str]:
    # Read the live set on the loop (touching the manager from a thread would
    # race with session creation/removal).
    #
    # ``is_recording`` (not merely "a ``recording_path`` is set") is the test:
    # a *stopped* recording keeps its path for the download endpoint, and
    # treating that as live made the delete endpoint refuse to remove a cast
    # nothing was writing to any more.
    return {
        str(s.recording_path)
        for s in manager.list_sessions()
        if s.is_recording and s.recording_path is not None
    }


def prune_recordings(directory: Any, cutoff: float, active: set[str]) -> None:
    """Delete recordings older than ``cutoff`` (blocking; runs in a thread)."""
    from pathlib import Path

    for entry in Path(directory).glob("*.cast"):
        if str(entry) in active:
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink(missing_ok=True)
        except OSError:
            continue
