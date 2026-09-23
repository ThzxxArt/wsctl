"""FastAPI application factory.

This module only *assembles*: request bodies live in :mod:`~wsctl.server.models`,
per-request helpers in :mod:`~wsctl.server.deps`, endpoints under
``server/routes/``, and cross-instance reconciliation in
:mod:`~wsctl.server.maintenance`. Keeping the wiring here thin is what stops
the factory from turning into a second monolith.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
import sys
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint

from wsctl import __version__
from wsctl.core import logrotate as logrotate_mod
from wsctl.core import pty as pty_mod
from wsctl.core import recording as recording_mod
from wsctl.core import session as session_mod
from wsctl.core.audit import AuditWriter
from wsctl.core.authcache import AuthCache
from wsctl.core.config import (
    Settings,
    reload_settings_file,
)
from wsctl.core.metrics import Metrics
from wsctl.core.pty import PtyError
from wsctl.core.ratelimit import RateLimiter
from wsctl.core.session import SessionManager, SessionSpec
from wsctl.core.store import Store
from wsctl.core.webhook import WebhookDispatcher

from . import routes
from .maintenance import (
    active_recording_paths,
    dead_instance_ids,
    prune_recordings,
    reconcile_instances,
    retention_cutoffs,
)
from .routes.health import NO_CACHE, VENDOR_CACHE, VOLATILE_ASSETS
from .security import (
    SECURITY_HEADERS,
    client_ip,
    ip_allowed,
)
from .ws import terminal_endpoint

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
MAINTENANCE_INTERVAL = 5.0

log = logging.getLogger("wsctl.app")


def _config_mtime(settings: Settings) -> float:
    try:
        return settings.config_path.stat().st_mtime
    except OSError:
        return 0.0


def _request_reload(app: FastAPI) -> None:
    """Signal handler: defer the reload to the maintenance loop."""
    app.state.reload_requested = True


def _install_sighup(app: FastAPI) -> None:
    """Reload config on ``SIGHUP`` (what ``wsctl reload`` sends)."""
    if sys.platform == "win32":  # pragma: no cover - platform specific
        return
    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
        loop.add_signal_handler(signal.SIGHUP, _request_reload, app)


def create_app(
    settings: Settings,
    *,
    store: Store | None = None,
    manager: SessionManager | None = None,
    startup_command: str | None = None,
) -> FastAPI:
    import shlex

    instance_id = uuid.uuid4().hex
    config_clock = {"mtime": _config_mtime(settings)}

    def _reload_config() -> tuple[list[str], list[str]]:
        changed, errors = reload_settings_file(settings)
        if changed or errors:
            limiter.limit = settings.login_rate_limit
            limiter.window = float(settings.login_rate_window)
            # ``session_sliding_ttl`` is read from the store on every
            # resolution, not from ``settings``. Without this the field was
            # listed as hot-reloadable while a reload never reached the code
            # that honours it -- the exact "label says one thing, behaviour
            # does another" defect this release is about.
            app.state.store.sliding_ttl = settings.session_sliding_ttl
            if errors:
                # Never silent: a broken edit must be visible in the log, the
                # API response and the CLI, otherwise it looks like the reload
                # simply did nothing.
                log.error("config reload failed: %s", "; ".join(errors))
            if changed:
                log.info("config reloaded: %s", ", ".join(changed))
        return changed, errors

    async def _purge_retention() -> None:
        """Bound on-disk growth: audit log, finished session rows, old casts.

        The database purges and the directory scan are blocking, so they run on
        a worker thread rather than stalling the event loop every 5 seconds.
        """
        now = time.time()
        store_ = app.state.store
        cutoffs = retention_cutoffs(now, settings)
        if cutoffs["audit"] is not None:
            await asyncio.to_thread(store_.purge_audit, cutoffs["audit"])
        if cutoffs["term_sessions"] is not None:
            await asyncio.to_thread(
                store_.purge_term_sessions, cutoffs["term_sessions"]
            )
        if cutoffs["recordings"] is not None:
            active = active_recording_paths(app.state.manager)
            await asyncio.to_thread(
                prune_recordings, settings.recordings_dir, cutoffs["recordings"], active
            )

    def _maintenance_db_writes(instance_id_: str, ttl: float) -> None:
        """Maintenance database writes that depend on no live snapshot.

        Genuinely runs on a worker thread: these used to sit on the event loop
        and made it pay for instance leases and retention every 5s. Anything
        that must be consistent with a freshly-taken in-memory snapshot is
        deliberately kept on the loop in :func:`_maintenance_db_tick` instead.
        """
        store_ = app.state.store
        store_.instance_register(
            instance_id_, pid=os.getpid(), host=socket.gethostname()
        )
        store_.purge_expired_sessions()
        store_.term_session_clear_expired_shares()
        for dead in dead_instance_ids(store_, ttl, instance_id_):
            store_.instance_remove(dead)

    async def _maintenance_db_tick() -> None:
        """Collect on the loop, persist off it -- with one deliberate exception.

        ``term_session_stop_missing`` takes the in-memory session set as its
        "these are alive" list. Running it on a worker thread would widen the
        window between the snapshot and the UPDATE to full thread-scheduling
        latency, and a session created in that window would be marked
        ``stopped`` while it is in fact running (and never adopted again after
        a restart). The snapshot and that one UPDATE therefore stay in a single
        synchronous stretch on the loop.
        """
        expired = await app.state.manager.reap_expired()
        for sid in expired:
            app.state.store.term_session_set_status(sid, "expired", "超过空闲或最长寿命")
        alive = {s.id for s in app.state.manager.list_sessions()}
        app.state.store.term_session_stop_missing(alive, instance_id=instance_id)
        limiter.sweep()
        await asyncio.to_thread(
            _maintenance_db_writes,
            instance_id,
            float(settings.instance_ttl),
        )

    async def _rotate_log() -> None:
        if not settings.log_file or settings.log_max_bytes <= 0:
            return
        rotated = await asyncio.to_thread(
            logrotate_mod.rotate_if_needed,
            settings.log_file,
            settings.log_max_bytes,
            settings.log_backup_count,
        )
        if rotated:
            log.info("log rotated: %s", settings.log_file)

    async def _maintenance() -> None:
        ticks = 0
        while True:
            await asyncio.sleep(MAINTENANCE_INTERVAL)
            try:
                # Re-register (idempotent upsert) rather than a bare heartbeat:
                # if a peer pruned our lease while we were paused, this restores it.
                await _maintenance_db_tick()
                await reconcile_instances(app)
                await _purge_retention()
                if app.state.reload_requested:
                    app.state.reload_requested = False
                    _reload_config()
                mtime = _config_mtime(settings)
                if mtime != config_clock["mtime"]:
                    config_clock["mtime"] = mtime
                    _reload_config()
                ticks += 1
                if ticks % 12 == 0:  # about once a minute
                    await _rotate_log()
                app.state.maintenance_last = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("maintenance task failed")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        from wsctl.core import tmux

        log.info("wsctl %s listening on %s:%s", __version__, settings.host, settings.port)
        tmux.set_namespace(settings.data_dir)
        app.state.store.instance_register(
            instance_id, pid=os.getpid(), host=socket.gethostname()
        )
        maint = asyncio.create_task(_maintenance())
        # Start the event-loop lag watchdog now that a loop is actually running.
        app.state.loop_lag_starter()
        _install_sighup(app)
        audit.start()
        if webhook is not None:
            webhook.start()
        await reconcile_instances(app)
        if startup_command:
            argv = shlex.split(startup_command)
            spec = SessionSpec(
                name=Path(argv[0]).name if argv else "终端",
                argv=argv,
                cwd=settings.default_cwd,
                backend=settings.default_backend,
                idle_timeout=settings.idle_timeout,
                max_life=settings.max_life,
                max_clients=settings.session_max_clients,
                memory_limit=settings.session_memory_limit,
                scrollback_bytes=settings.scrollback_bytes,
            )
            try:
                session = await app.state.manager.create(spec)
            except (OSError, PtyError) as exc:
                log.error("启动会话失败：%s", exc)
            else:
                app.state.store.term_session_upsert(
                    session.id,
                    name=spec.name,
                    owner_id=None,
                    backend=spec.backend,
                    instance_id=instance_id,
                )
                log.info("startup session %s: %s", session.id, startup_command)
        try:
            yield
        finally:
            maint.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await maint
            await app.state.manager.shutdown(preserve=settings.tmux_preserve_on_shutdown)
            # Flush queued audit events (and deliver them to the webhook) before
            # tearing the webhook down.
            await audit.stop()
            if webhook is not None:
                await webhook.stop()
            with contextlib.suppress(Exception):
                app.state.store.instance_remove(instance_id)
            app.state.store.close()

    app = FastAPI(
        title="wsctl", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan
    )
    app.state.settings = settings
    app.state.instance_id = instance_id
    app.state.store = store or Store(settings.db_path)
    app.state.store.sliding_ttl = settings.session_sliding_ttl
    # A short-lived resolution cache: WebSocket re-checks run every few seconds
    # per connection and must not become a database query on the event loop.
    app.state.auth_cache = AuthCache(ttl=30.0)
    app.state.store.set_auth_cache(app.state.auth_cache)
    app.state.manager = manager or SessionManager()

    app.state.reload_requested = False
    app.state.maintenance_last = time.monotonic()
    app.state.reload_config = _reload_config

    audit = AuditWriter(app.state.store)
    app.state.audit = audit

    webhook = WebhookDispatcher(settings.webhook_url) if settings.webhook_url else None
    if webhook is not None:
        app.state.store.set_event_sink(webhook.emit)

    metrics = Metrics()
    app.state.metrics = metrics
    metrics.collect("wsctl_up", "1 when the server is serving", lambda: 1.0)
    metrics.collect(
        "wsctl_sessions",
        "Current number of terminal sessions",
        lambda: float(len(app.state.manager.list_sessions())),
    )
    metrics.collect(
        "wsctl_clients",
        "Current number of attached terminal clients",
        lambda: float(sum(s.client_count for s in app.state.manager.list_sessions())),
    )
    metrics.collect(
        "wsctl_session_bytes",
        "Approximate bytes buffered across all sessions",
        lambda: float(sum(s.memory_usage() for s in app.state.manager.list_sessions())),
    )
    metrics.collect_counter(
        "wsctl_audit_dropped_total",
        "Audit events dropped because the async queue was saturated",
        lambda: float(audit.dropped),
    )
    metrics.collect_counter(
        "wsctl_audit_write_errors_total",
        "Audit batches that failed to persist (logged, then dropped)",
        lambda: float(audit.errors),
    )
    metrics.collect(
        "wsctl_maintenance_lag_seconds",
        "How far behind the maintenance loop is (0 when healthy)",
        lambda: max(
            0.0, time.monotonic() - app.state.maintenance_last - MAINTENANCE_INTERVAL
        ),
    )
    metrics.collect_counter(
        "wsctl_pty_input_dropped_total",
        "Input writes dropped because a session's child stopped reading",
        lambda: float(pty_mod.dropped_input_total()),
    )
    # ``wsctl_input_rate_limited_total`` is not registered here: ``ws.py``
    # counts it with ``metrics.inc``, which already puts it in the exposition
    # as a counter. Registering a collector too would print the series twice.
    metrics.collect_counter(
        "wsctl_client_frames_shed_total",
        "Terminal frames discarded to keep a slow viewer connected (never a disconnect)",
        lambda: float(session_mod.shed_frames_total(app.state.manager)),
    )
    metrics.collect_counter(
        "wsctl_clients_backpressure_dropped_total",
        "Clients dropped because output outran them (queue full / byte budget)",
        lambda: float(session_mod.slow_consumer_drops()),
    )
    metrics.collect_counter(
        "wsctl_clients_evicted_total",
        "Clients dropped by a session's memory limit (session itself survives)",
        lambda: float(session_mod.evicted_clients_total()),
    )
    metrics.collect_counter(
        "wsctl_recording_failures_total",
        "Recorders stopped by a write failure (cast may be truncated)",
        lambda: float(recording_mod.failure_count()),
    )

    # Event-loop lag: the one number that says whether the server is keeping up.
    # A terminal that "卡住" because the loop is wedged is indistinguishable from
    # one that is merely busy unless something measures the difference, and the
    # shedding work this release moved off the lock is exactly the kind of thing
    # that used to hide here. A 0.5s tick measures its own scheduling delay:
    # the callback is promised for T and reports how late it actually ran.
    LOOP_LAG_INTERVAL = 0.5
    lag = {"due": 0.0, "last": 0.0, "peak": 0.0}

    def _lag_tick() -> None:
        now = time.monotonic()
        due = lag["due"]
        if due:
            observed = max(0.0, now - due)
            # Instantaneous value, and a decaying peak so one outlier stays
            # visible without pinning the gauge forever.
            lag["last"] = observed
            lag["peak"] = max(observed, lag["peak"] * 0.97)
        lag["due"] = now + LOOP_LAG_INTERVAL
        with contextlib.suppress(RuntimeError, AttributeError):
            asyncio.get_running_loop().call_later(LOOP_LAG_INTERVAL, _lag_tick)

    app.state.loop_lag_starter = _lag_tick
    app.state.loop_lag = lag
    metrics.collect(
        "wsctl_event_loop_lag_seconds",
        "How late the last 0.5s watchdog tick actually ran (0 when healthy)",
        lambda: float(lag["last"]),
    )
    metrics.collect(
        "wsctl_event_loop_lag_peak_seconds",
        "Decaying peak of the watchdog tick delay since startup",
        lambda: float(lag["peak"]),
    )
    # ``wsctl_shed_resync_requests_total`` and ``wsctl_ws_frames_coalesced_total``
    # are *not* registered here: ``ws.py`` counts both with ``metrics.inc``,
    # which already puts them in the exposition as counters. Registering a
    # collector as well would print each series twice -- the same trap the
    # input-rate-limit counter already fell into.

    limiter = RateLimiter(settings.login_rate_limit, settings.login_rate_window)
    app.state.limiter = limiter

    @app.middleware("http")
    async def _guard(request: Request, call_next: RequestResponseEndpoint) -> Response:
        ip = client_ip(request, settings)
        if not ip_allowed(ip, settings.allowed_ips):
            app.state.audit.enqueue("ip_rejected", ip=ip, payload=request.url.path)
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        response = await call_next(request)
        if settings.security_headers:
            for key, value in SECURITY_HEADERS.items():
                response.headers.setdefault(key, value)
        return response

    class _CachingStatic(StaticFiles):
        """Static files that cannot go stale on the user.

        ``StaticFiles`` sends no ``Cache-Control`` at all, so the browser
        applies heuristic caching. For ``vendor/`` that is fine -- those files
        are version-pinned and never change in place. For ``app.js`` and
        ``app.css`` it is not: a stale bundle under a fresh ``index.html`` is
        exactly how a new control appears on screen with nothing wired to it.
        """

        def file_response(
            self,
            full_path: Any,
            stat_result: Any,
            scope: Any,
            status_code: int = 200,
        ) -> Response:
            response = super().file_response(full_path, stat_result, scope, status_code)
            name = str(full_path).replace("\\", "/").rsplit("/", 1)[-1]
            in_vendor = "/vendor/" in str(full_path).replace("\\", "/")
            if in_vendor:
                response.headers.setdefault("Cache-Control", VENDOR_CACHE)
            elif name in VOLATILE_ASSETS or name.endswith((".js", ".css", ".html")):
                response.headers["Cache-Control"] = NO_CACHE
            return response

    app.mount("/static", _CachingStatic(directory=STATIC_DIR), name="static")

    for router, _prefix in routes.ROUTERS:
        app.include_router(router)

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await terminal_endpoint(websocket)

    return app
