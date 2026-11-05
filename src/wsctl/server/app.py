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
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint

from wsctl import __version__
from wsctl.core import logrotate as logrotate_mod
from wsctl.core import passwords as passwords_mod
from wsctl.core import pty as pty_mod
from wsctl.core import recording as recording_mod
from wsctl.core import session as session_mod
from wsctl.core.audit import AuditWriter
from wsctl.core.authcache import AuthCache
from wsctl.core.config import (
    Settings,
    reload_settings_file,
)
from wsctl.core.database import Database
from wsctl.core.metrics import Metrics
from wsctl.core.migrations import upgrade_database
from wsctl.core.observability import RequestContextMiddleware, setup_tracing
from wsctl.core.pty import PtyError
from wsctl.core.ratelimit import RateLimiter
from wsctl.core.session import SessionManager, SessionSpec
from wsctl.core.store import Store
from wsctl.core.webhook import WebhookDispatcher

from . import routes
from .api_keys import ApiKeyScopeMiddleware
from .deps import resolve_token_user, token_source
from .maintenance import (
    active_recording_paths,
    dead_instance_ids,
    prune_recordings,
    reconcile_instances,
    retention_cutoffs,
)
from .problems import install_problem_handlers
from .problems import problem as problem_response
from .routes.health import NO_CACHE, VOLATILE_ASSETS
from .security import (
    SECURITY_HEADERS,
    client_ip,
    forwarded_scheme,
    ip_allowed,
    is_origin_allowed,
)
from .ws import ConnectionCaps, terminal_endpoint
from .ws_v2 import terminal_endpoint_v2

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
MAINTENANCE_INTERVAL = 5.0

#: Methods that cannot change state, so a cross-site request for one is not a
#: CSRF. ``OPTIONS`` is included for preflight; there is no CORS middleware, so
#: a preflight never reaches a real handler anyway.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: Login is a JSON object with two short strings. The previous shape bound the
#: *parsed* fields (``max_length``) but read the whole body into memory first,
#: so one 100 MB request cost 100 MB of RAM before validation ever ran. The
#: cap is enforced from ``Content-Length`` before the body is read; a chunked
#: request is refused outright because neither the browser UI nor the CLI
#: streams a login.
MAX_LOGIN_BODY = 8 * 1024

#: JSON API bodies are small (a layout, a config batch, a rename). File upload
#: has its own streaming cap; everything else that claims a huge body is
#: refused before Starlette buffers it into memory.
MAX_JSON_BODY = 16 * 1024 * 1024

log = logging.getLogger("wsctl.app")


#: API paths that must work before a session exists. Everything else is
#: authenticated in middleware *before* Starlette reads the body: otherwise an
#: unauthenticated upload/JSON body is parsed (and spooled to disk/memory)
#: before the route dependency gets a chance to return 401.
_PUBLIC_API_SUFFIXES = (
    "/login",
    "/logout",
    "/shares/resolve",
    "/push/vapid",
)


def _needs_api_auth(path: str) -> bool:
    if not path.startswith("/api/"):
        return False
    return not any(path.rstrip("/").endswith(suffix) for suffix in _PUBLIC_API_SUFFIXES)


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


def _cross_site(request: Request, settings: Settings) -> str | None:
    """Reject a state-changing request that another site's page issued.

    The cookie is ``SameSite=Lax``, which stops a *cross-site* form post but
    not a same-site one: another port on the same host, or a sibling subdomain,
    is "same site" to the cookie jar and the browser attaches the session
    cookie. ``POST /api/files/upload`` is a CORS **simple request** (multipart),
    so a page could send it with ``mode:"no-cors"`` and no preflight -- and
    overwrite ``~/.ssh/authorized_keys``. Comparing ``Origin`` against the
    request's ``Host`` is the check that closes it, and it needs no token
    because every browser sets ``Origin`` on a non-GET request.
    """
    origin = request.headers.get("origin")
    if origin is not None:
        scheme = forwarded_scheme(request, settings) or request.url.scheme
        if is_origin_allowed(
            origin,
            request.headers.get("host"),
            settings.allowed_origins,
            scheme=scheme,
        ):
            return None
        return "跨站请求被拒绝"
    # No Origin. A browser always sends one for a cross-origin non-GET, so this
    # is either a non-browser client (CLI, script -- allowed, it authenticates)
    # or a browser request that claims to be cross-origin/same-site without
    # saying from where. The latter is not something this API serves.
    if request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
        return "跨站请求被拒绝（缺少 Origin）"
    return None


def _login_body_problem(request: Request) -> tuple[int, str] | None:
    """Refuse an oversized login body *before* it is read into memory."""
    raw = request.headers.get("content-length")
    if raw is None:
        # Neither the web UI nor the CLI streams a login. A body with no length
        # would have been buffered in full first, which is the whole bug.
        return 411, "缺少 Content-Length"
    try:
        size = int(raw)
    except ValueError:
        return 400, "Content-Length 无效"
    if size > MAX_LOGIN_BODY:
        return 413, f"请求体过大（上限 {MAX_LOGIN_BODY} 字节）"
    return None


def _per_ip_login_limit(settings: Settings) -> int:
    """The per-host login budget derived from the per-account one.

    ``0`` must stay ``0``: an operator who turned login throttling off did not
    ask for a different limit under another name. The multiplier exists because
    one host may legitimately try several accounts (a shared NAT office), while
    an attacker rotating usernames must still hit a ceiling. Three times the
    account budget, with a floor of 20.
    """
    limit = int(settings.login_rate_limit)
    if limit <= 0:
        return 0
    return max(20, limit * 3)


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
    background_tasks: set[asyncio.Task[Any]] = set()

    def _spawn(coro: Any) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        background_tasks.add(task)

        def _done(finished: asyncio.Task[Any]) -> None:
            background_tasks.discard(finished)
            if finished.cancelled():
                return
            error = finished.exception()
            if error is not None:
                log.warning("background task failed: %s", error)

        task.add_done_callback(_done)
        return task

    def _reload_config() -> tuple[list[str], list[str]]:
        changed, errors = reload_settings_file(settings)
        if changed or errors:
            limiter.limit = settings.login_rate_limit
            limiter.window = float(settings.login_rate_window)
            # The per-host budget is derived from the same setting; leaving it
            # at its startup value made "I raised the login limit" true for one
            # budget and false for the other.
            ip_limiter.limit = _per_ip_login_limit(settings)
            ip_limiter.window = float(settings.login_rate_window)
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
        a worker thread rather than stalling the event loop. They also run
        hourly, not on every five-second tick: a DELETE plus a full recordings
        directory stat every tick took the SQLite write lock and walked the
        data directory for no benefit. Menu-level deletes and explicit stops
        remain immediate; this is only the background sweep.
        """
        now = time.time()
        if now - float(getattr(app.state, "_retention_purged_at", 0.0)) < 3600:
            return
        app.state._retention_purged_at = now
        store_ = app.state.store
        cutoffs = retention_cutoffs(now, settings)
        if cutoffs["audit"] is not None:
            await asyncio.to_thread(store_.purge_audit, cutoffs["audit"])
        if cutoffs["term_sessions"] is not None:
            await asyncio.to_thread(
                store_.purge_term_sessions, cutoffs["term_sessions"]
            )
            # Command blocks were never purged; a busy account could grow the
            # database forever even after its session rows were deleted.
            await asyncio.to_thread(
                app.state.db.command_blocks_purge, cutoffs["term_sessions"]
            )
        if cutoffs["recordings"] is not None:
            active = active_recording_paths(app.state.manager)
            await asyncio.to_thread(
                prune_recordings, settings.recordings_dir, cutoffs["recordings"], active
            )
        # Idempotency keys are useful for retries, not forever. A day is well
        # beyond any client's retry window and keeps the table bounded.
        await asyncio.to_thread(app.state.db.idempotency_purge, older_than=86400)

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
        now = time.time()
        if now - float(getattr(app.state, "_shares_purged_at", 0.0)) >= 3600:
            app.state._shares_purged_at = now
            app.state.shares.purge_expired()
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
        await app.state.manager.reap_expired()
        # The end of an expired session is persisted by the manager's
        # ``on_session_end`` hook (with the real reason); the sweep below only
        # marks rows orphaned by a crash, where "not in memory" is all we know.
        alive = {s.id for s in app.state.manager.list_sessions()}
        app.state.store.term_session_stop_missing(alive, instance_id=instance_id)
        limiter.sweep()
        # The per-host budget is keyed by an *attacker-chosen* address, so it
        # needs the same cleanup as the per-account one: without this, every
        # one-shot source address left a deque behind for the process lifetime.
        ip_limiter.sweep()
        await asyncio.to_thread(
            _maintenance_db_writes,
            instance_id,
            float(settings.instance_ttl),
        )

    async def _reconcile_accounts() -> None:
        """Backstop for account changes made outside this process.

        The HTTP API terminates immediately (``routes/users.py``); ``wsctl user
        disable`` / ``passwd`` / ``user delete`` run in a *different* process
        and can only touch the database. Without this loop the shell kept
        running and a writable share kept working until the server was
        restarted -- for an account that was supposed to be locked out.

        Password rotation is the subtle one: it is not "everything becomes
        invalid", it is "everything opened under the *old* password becomes
        invalid". The database records when the password changed, and sessions
        created before that instant are ended; sessions created after it (with
        the new credential) are not collateral. Runs every maintenance tick.
        """
        if not settings.auth_required:
            return
        live = [
            s
            for s in app.state.manager.list_sessions()
            if s.owner_id is not None and s.owner_id > 0
        ]
        if not live:
            return
        owner_ids = {int(s.owner_id) for s in live if s.owner_id is not None}
        owners = await asyncio.to_thread(app.state.store.users_by_ids, owner_ids)
        for owner_id in owner_ids:
            user = owners.get(owner_id)
            if user is None:
                reason = "账号已被删除，会话已结束"
                cutoff: float | None = None
            elif user.disabled:
                reason = "账号已被停用，会话已结束"
                cutoff = None
            elif user.password_changed_at > 0:
                reason = "账号密码已变更，旧会话已结束"
                cutoff = user.password_changed_at
            else:
                continue
            ids = await app.state.manager.terminate_owned(
                owner_id,
                reason=reason,
                status="stopped",
                created_before=cutoff,
            )
            if not ids:
                # A rotated password leaves newer sessions legitimate; do not
                # log or audit a sweep that found nothing every five seconds.
                continue
            log.warning("terminating sessions of %s: %s", owner_id, reason)
            for sid in ids:
                await asyncio.to_thread(
                    app.state.store.term_session_set_share, sid, token=None
                )
            app.state.audit.enqueue(
                "account_sessions_terminated", payload=str(owner_id)
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
                await _reconcile_accounts()
                with contextlib.suppress(Exception):
                    from wsctl.core.observability import ACTOR_MAILBOX_DEPTH

                    ACTOR_MAILBOX_DEPTH.set(
                        float(
                            sum(
                                int(getattr(session, "mailbox_depth", 0))
                                for session in app.state.manager.list_sessions()
                            )
                        )
                    )
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

    async def _watch_config() -> None:
        """Mark a reload as soon as the config file changes.

        The maintenance loop's mtime check remains as a fallback (network
        filesystems, editors that preserve mtime); watchfiles is the fast path
        and uses inotify/FSEvents instead of polling every five seconds.
        """
        try:
            from watchfiles import awatch

            async for changes in awatch(settings.config_path):
                if any(Path(path) == settings.config_path for _change, path in changes):
                    app.state.reload_requested = True
        except asyncio.CancelledError:
            raise
        except Exception:
            log.debug("watchfiles unavailable; mtime polling remains", exc_info=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        from wsctl.core import tmux

        log.info("wsctl %s listening on %s:%s", __version__, settings.host, settings.port)
        tmux.set_namespace(settings.data_dir)
        app.state.store.instance_register(
            instance_id, pid=os.getpid(), host=socket.gethostname()
        )
        maint = asyncio.create_task(_maintenance())
        watcher = asyncio.create_task(_watch_config())
        # Start the event-loop lag watchdog now that a loop is actually running.
        app.state.loop_lag_starter()
        _install_sighup(app)
        audit.start()
        if webhook is not None:
            webhook.start()
        await reconcile_instances(app)
        command = startup_command
        if command:
            try:
                argv = shlex.split(command)
            except ValueError as exc:
                log.error("启动会话命令无法解析，已跳过：%s", exc)
                argv = []
            if not argv:
                command = None
        if command:
            backend = settings.default_backend
            if backend == "tmux" and not tmux.is_available():
                log.warning("default_backend=tmux but tmux is unavailable; using local")
                backend = "local"
            spec = SessionSpec(
                name=Path(argv[0]).name if argv else "终端",
                argv=argv,
                cwd=settings.default_cwd,
                backend=backend,
                idle_timeout=settings.idle_timeout,
                max_life=settings.max_life,
                max_clients=settings.session_max_clients,
                memory_limit=settings.session_memory_limit,
                scrollback_bytes=settings.scrollback_bytes,
                snapshot_mode=settings.snapshot_mode,
                shell_integration=settings.shell_integration,
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
                    generation=session.generation,
                    snapshot_mode=spec.snapshot_mode,
                    shell_integration=spec.shell_integration,
                )
                log.info("startup session %s: %s", session.id, command)
        try:
            yield
        finally:
            maint.cancel()
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await maint
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
            await app.state.manager.shutdown(preserve=settings.tmux_preserve_on_shutdown)
            # Flush queued audit events (and deliver them to the webhook) before
            # tearing the webhook down.
            await audit.stop()
            if webhook is not None:
                await webhook.stop()
            # Drain short-lived background writes (command blocks, push
            # notifications) before the database handles close. Without this a
            # command that ended during shutdown could lose its row.
            pending = list(background_tasks)
            if pending:
                _done, still_pending = await asyncio.wait(pending, timeout=5.0)
                for task in still_pending:
                    task.cancel()
                await asyncio.gather(*still_pending, return_exceptions=True)
            with contextlib.suppress(Exception):
                app.state.store.instance_remove(instance_id)
            app.state.store.close()
            app.state.db.close()

    app = FastAPI(
        title="wsctl", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan
    )
    install_problem_handlers(app)
    app.state.settings = settings
    app.state.instance_id = instance_id
    app.state.store = store or Store(settings.db_path)
    # The legacy tables are owned by ``Store`` (which applies its idempotent
    # v5→v6 migration on open). Alembic records the same schema as a versioned
    # chain and owns every table added from 0.2.0 onward. Running it on an
    # existing 0.1.30 database is a no-op apart from stamping the revision.
    database_url = settings.database_url or f"sqlite:///{app.state.store.path}"
    upgrade_database(database_url)
    app.state.db = Database(database_url, echo=settings.database_echo)
    from .services import EventBroker, PushService, ShareService

    app.state.events = EventBroker()
    app.state.shares = ShareService(app.state.db)
    app.state.push = PushService(app.state.db, settings)
    app.state.store.sliding_ttl = settings.session_sliding_ttl
    # A short-lived resolution cache: WebSocket re-checks run every few seconds
    # per connection and must not become a database query on the event loop.
    app.state.auth_cache = AuthCache(ttl=30.0)
    app.state.store.set_auth_cache(app.state.auth_cache)
    app.state.manager = manager or SessionManager()
    if getattr(app.state.manager, "_on_command", None) is None:

        async def _persist_command(event: dict[str, Any]) -> None:
            if event.get("phase") != "finished":
                return
            async def _write_block() -> None:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(app.state.db.command_block_add, event)

            # The reader loop awaits this hook. A synchronous SQLite commit on
            # every command must not stall the PTY: schedule it and keep
            # reading. ``session_commands`` falls back to the live parser until
            # the row lands.
            _spawn(_write_block())
            if settings.audit_input:
                app.state.audit.enqueue(
                    "command",
                    term_session_id=str(event.get("session") or ""),
                    payload=str(event.get("command") or "")[:512],
                )
            duration = event.get("duration_ms")
            if (
                settings.web_push_enabled
                and isinstance(duration, (int, float))
                and duration >= 10_000
            ):
                session = app.state.manager.get(str(event.get("session") or ""))
                owner_id = getattr(session, "owner_id", None) if session else None
                if owner_id is not None:
                    with contextlib.suppress(Exception):
                        # Notification I/O must not hold the PTY read loop: a
                        # push service timeout would freeze the shell's output.
                        _spawn(
                            app.state.push.notify(
                                int(owner_id),
                                {
                                    "title": "wsctl 长命令完成",
                                    "body": (
                                        f"{str(event.get('command') or '命令')[:80]}"
                                        f"（{int(duration)} ms）"
                                    ),
                                    "url": "/",
                                },
                            )
                        )

        app.state.manager._on_command = _persist_command
    # A session that ends writes its own row, with the real reason and exit
    # code, at the moment it ends. The maintenance sweep below only catches
    # rows orphaned by a crash -- for those, a bare ``stopped`` and the
    # "服务退出或实例崩溃" fallback are the honest answer.
    if getattr(app.state.manager, "_on_session_end", None) is None:

        async def _persist_session_end(session: session_mod.TermSession) -> None:
            if session.end_status == "running":
                # A preserved tmux session: its shell lives on and the row must
                # stay ``running`` so the next startup adopts it.
                return
            await asyncio.to_thread(
                app.state.store.term_session_set_seq, session.id, session.end_seq
            )
            await asyncio.to_thread(
                app.state.store.term_session_set_status,
                session.id,
                session.end_status,
                session.end_reason,
                session.exit_code,
            )
            # A link to a session that has ended must not stay resolvable.
            with contextlib.suppress(Exception):
                await asyncio.to_thread(
                    app.state.store.term_session_set_share, session.id, token=None
                )
            with contextlib.suppress(Exception):
                await asyncio.to_thread(
                    app.state.shares.revoke_session, session.id
                )
            app.state.events.publish(
                "session_ended",
                owner_id=session.owner_id,
                session=session.id,
                status=session.end_status,
                exit_code=session.exit_code,
            )
            if settings.web_push_enabled and session.owner_id is not None:
                with contextlib.suppress(Exception):
                    _spawn(
                        app.state.push.notify(
                            int(session.owner_id),
                            {
                                "title": "wsctl 会话已结束",
                                "body": f"{session.spec.name}（退出码 {session.exit_code}）",
                                "url": "/",
                            },
                        )
                    )

        app.state.manager._on_session_end = _persist_session_end

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
    # ``wsctl_input_rate_limited_total``, ``wsctl_shed_resync_requests_total``,
    # ``wsctl_ws_frames_coalesced_total`` and
    # ``wsctl_ws_oversized_input_frames_total`` are not registered here:
    # ``ws.py`` counts them with ``metrics.inc``, which already puts them in
    # the exposition as counters. Registering a collector too would print the
    # series twice.
    #
    # That trap had already caught ``wsctl_pty_input_dropped_total`` once: the
    # *same name* was both collected from ``pty.dropped_input_total()`` and
    # incremented by ``ws.py`` for refused oversized frames, so one name
    # carried two unrelated numbers. They are separate series now.
    metrics.collect_counter(
        "wsctl_client_frames_shed_total",
        "Shedding events that discarded terminal frames to keep a slow viewer "
        "connected (one event may drop several frames; never a disconnect). "
        "Process-wide and monotonic, so a detach cannot make it go backwards",
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
    #: Per-IP failure budget, separate from the per-``ip:username`` one. The
    #: latter is reset by a successful login, so rotating usernames used to give
    #: an attacker a fresh budget per name and an unbounded number of Argon2
    #: verifications. Three times the account budget (minimum 20) keeps a shared
    #: NAT office working while still bounding one host.
    ip_limiter = RateLimiter(
        _per_ip_login_limit(settings), settings.login_rate_window
    )
    app.state.ip_limiter = ip_limiter
    #: Lanes for password verification, acquired in the *event loop* before a
    #: worker thread is handed the job. The process-wide semaphore in
    #: ``core.passwords`` caps CPU, but a thread blocked on it still occupies a
    #: slot in the default executor -- a login storm could fill all of them and
    #: starve every other ``to_thread`` call (store queries, backups) while the
    #: CPU sat idle between lanes. Waiting here costs a coroutine, not a thread.
    app.state.login_slots = asyncio.Semaphore(passwords_mod.HASH_LANES)
    app.state.ws_caps = ConnectionCaps()
    metrics.collect(
        "wsctl_ws_connections_open",
        "Terminal WebSockets currently admitted",
        lambda: float(app.state.ws_caps.total),
    )

    @app.middleware("http")
    async def _guard(request: Request, call_next: RequestResponseEndpoint) -> Response:
        ip = client_ip(request, settings)
        if not ip_allowed(ip, settings.allowed_ips):
            app.state.audit.enqueue("ip_rejected", ip=ip, payload=request.url.path)
            return problem_response(request, status=403, detail="forbidden")
        if request.method not in SAFE_METHODS and request.url.path.startswith("/api/"):
            rejection = _cross_site(request, settings)
            if rejection is not None:
                app.state.audit.enqueue(
                    "origin_rejected",
                    ip=ip,
                    payload=f"{request.method} {request.url.path}",
                )
                return problem_response(request, status=403, detail=rejection)
        if settings.auth_required and _needs_api_auth(request.url.path):
            token, source = token_source(request)
            if source == "ambiguous" or (
                source == "cookie" and token is not None and token.startswith("wsctl_")
            ):
                return problem_response(
                    request, status=401, detail="Authorization 头重复或凭证来源无效"
                )
            user = await asyncio.to_thread(
                resolve_token_user,
                request,
                token,
                allow_api_key=source == "bearer",
            )
            if user is None:
                return problem_response(request, status=401, detail="需要登录")
            # The route dependency can reuse this resolution instead of doing
            # a second token lookup after the body was parsed.
            request.state.user = user
            request.state.token = token
            request.state.token_source = source
        if request.method == "POST" and request.url.path.rstrip("/") in {
            "/api/login",
            "/api/v2/login",
            "/api/auth/login",
            "/api/v2/auth/login",
        }:
            issue = _login_body_problem(request)
            if issue is not None:
                code, detail = issue
                return problem_response(request, status=code, detail=detail)
        elif request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith(
            "/api/"
        ):
            is_upload = request.url.path.rstrip("/").endswith("/files/upload")
            if is_upload:
                # The multipart envelope is a little larger than the file; the
                # route still enforces the exact file limit while streaming.
                configured = int(getattr(settings, "file_max_upload", 0) or 0)
                limit = configured + 1024 * 1024 if configured > 0 else 1024**3
            else:
                limit = MAX_JSON_BODY
            raw_length = request.headers.get("content-length")
            if raw_length is None:
                # A chunked body has no length to check before Starlette starts
                # spooling it; both the UI and CLI always send a length. A
                # request with neither framing header has no body at all
                # (POST /share, DELETE without payload) and is safe.
                if "chunked" in request.headers.get("transfer-encoding", "").lower():
                    return problem_response(
                        request,
                        status=411,
                        detail="该接口需要 Content-Length，不接受分块请求体",
                    )
                length = 0
            else:
                try:
                    length = int(raw_length)
                except ValueError:
                    return problem_response(
                        request, status=400, detail="Content-Length 无效"
                    )
            if length > limit:
                return problem_response(
                    request,
                    status=413,
                    detail=f"请求体过大（上限 {limit} 字节）",
                )
        response = await call_next(request)
        # Authenticated API responses are per-user and must not sit in a shared
        # proxy or the browser's back/forward cache. Endpoints with an explicit
        # policy (the config ETag) keep their own header.
        if request.url.path.startswith("/api/") and "cache-control" not in {
            key.lower() for key in response.headers
        }:
            response.headers["Cache-Control"] = "no-store"
        # Conditional GET is what turns a reload of a 2 MB bundle into a 304
        # with no body. Starlette's FileResponse *sets* an ETag but never
        # checks If-None-Match, and our config endpoint sets its own. Handling
        # it once here covers both without each route re-implementing it.
        etag = response.headers.get("etag")
        if etag and request.headers.get("if-none-match"):
            candidates = {
                value.strip() for value in request.headers["if-none-match"].split(",")
            }
            if etag in candidates or "*" in candidates:
                headers = {
                    key: value
                    for key, value in response.headers.items()
                    if key.lower() in {"etag", "cache-control", "last-modified"}
                }
                return Response(status_code=304, headers=headers)
        if request.url.path.startswith("/api/") and not request.url.path.startswith(
            "/api/v2/"
        ):
            response.headers.setdefault("Deprecation", "true")
            response.headers.setdefault("Link", '</api/v2>; rel="successor-version"')
        if settings.security_headers:
            for key, value in SECURITY_HEADERS.items():
                response.headers.setdefault(key, value)
        return response

    class _CachingStatic(StaticFiles):
        """Static files that cannot go stale on the user.

        ``StaticFiles`` sends no ``Cache-Control`` at all, so the browser
        applies heuristic caching. Vite emits content-hashed names under
        ``assets/``; those are safe to cache for a year. The entry document and
        manifest must be revalidated, because they are what *point* at the new
        hashes. A stale entry document under a fresh bundle is exactly how a
        new control appears on screen with nothing wired to it.
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
            in_assets = "/assets/" in str(full_path).replace("\\", "/")
            if in_assets:
                # Vite emits content-hashed filenames. They cannot go stale in
                # place; a year-long immutable cache is correct and removes a
                # revalidation round trip from every page load.
                response.headers["Cache-Control"] = (
                    "public, max-age=31536000, immutable"
                )
            elif name in VOLATILE_ASSETS or name.endswith((".js", ".css", ".html")):
                response.headers["Cache-Control"] = NO_CACHE
            return response

    app.mount("/static", _CachingStatic(directory=STATIC_DIR), name="static")

    app.add_middleware(ApiKeyScopeMiddleware)
    app.add_middleware(RequestContextMiddleware)
    setup_tracing(app, settings)

    for router, _prefix in routes.ROUTERS:
        if router is routes.health.router:
            app.include_router(router)
        else:
            # 0.2.0's canonical surface is /api/v2. The unprefixed version is
            # kept for one release so a 0.1.x script or an open tab does not
            # break mid-upgrade; it is marked deprecated on every response.
            app.include_router(router, prefix="/api/v2")
            app.include_router(router, prefix="/api")

    @app.websocket("/ws/v2")
    async def ws_v2_endpoint(websocket: WebSocket) -> None:
        await terminal_endpoint_v2(websocket)

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await terminal_endpoint(websocket)

    return app
