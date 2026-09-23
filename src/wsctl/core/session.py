"""Terminal sessions and their manager.

The central design rule: **a session's lifetime is independent of any client
connection**. Clients attach and detach freely; the underlying PTY keeps
running and its output is retained in a bounded scrollback so reconnecting
clients can rebuild their screen.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from . import tmux
from .pty import DEFAULT_TERM_SIGNAL, Pty, create_pty
from .recording import Recorder
from .scrollback import DEFAULT_MAX_BYTES, Scrollback

log = logging.getLogger("wsctl.session")

# Process-wide, monotonic count of clients dropped because output outran them
# (their send queue filled, or blew the per-client byte budget). Distinct from
# the memory-limit eviction: this one is about one slow *viewer*, not one
# greedy session. Reported to the user and to Prometheus because it used to be
# invisible -- the socket just closed "normally" and the browser blanked its
# terminal with no explanation.
_slow_consumer_drops = 0


def slow_consumer_drops() -> int:
    return _slow_consumer_drops


def _bump_slow_consumer() -> None:
    global _slow_consumer_drops
    _slow_consumer_drops += 1


# Process-wide, monotonic count of clients dropped by a memory limit. Kept
# monotonic for the same reason ``pty.dropped_input_total`` is: a sum of
# per-session counters would go *down* when a session ends and a Prometheus
# ``rate()`` would see resets.
_evicted_clients_total = 0


def evicted_clients_total() -> int:
    return _evicted_clients_total


def shed_frames_total(manager: SessionManager | None = None) -> int:
    """Frames discarded across every attached client, for the metrics page.

    Shedding is the *good* outcome now -- it is what keeps a slow viewer
    connected instead of cycling them through reconnect/clear/replay -- so it
    is worth watching. A high number with low ``wsctl_clients_evicted_total``
    is healthy; the two rising together is not.
    """
    total = 0
    for session in (manager.list_sessions() if manager is not None else []):
        for entry in session.clients():
            total += int(entry.get("dropped_events", 0))
    return total


def _bump_evicted() -> None:
    global _evicted_clients_total
    _evicted_clients_total += 1


#: How long ``_finalize`` waits for the child before escalating to ``kill``.
FINALIZE_WAIT_TIMEOUT = 3.0
#: How long it waits after ``kill`` before giving up and reporting exit code -1.
FINALIZE_KILL_TIMEOUT = 2.0


class ClientGone(Exception):
    """Raised by a client sink that can no longer receive data."""


@runtime_checkable
class Client(Protocol):
    """A consumer attached to a session (browser tab or CLI client)."""

    #: Why this client ended, when the sink decided for itself. ``None`` means
    #: nothing unusual; the transport otherwise reports this instead of a plain
    #: 1000 ("normal closure"), which would be a lie for "we threw you out".
    close_code: int | None

    def put(self, item: bytes | dict[str, Any]) -> None:
        """Queue an item for delivery without blocking.

        ``bytes`` are sent as binary terminal data; ``dict`` as a JSON control
        message. Raises :class:`ClientGone` if the client is no longer usable.
        """
        ...


@dataclass
class SessionSpec:
    """Description of how to create a session.

    ``name``/``cols``/``rows`` are updated as the session is renamed or
    resized; the rest is fixed at creation time.
    """

    name: str
    argv: list[str]
    cwd: str | None = None
    env: dict[str, str] | None = None
    backend: str = "local"
    cols: int = 80
    rows: int = 24
    idle_timeout: float | None = None
    max_life: float | None = None
    max_clients: int = 0
    memory_limit: int = 0
    scrollback_bytes: int = DEFAULT_MAX_BYTES


@dataclass
class _ClientEntry:
    client: Client
    writable: bool = True
    share: str | None = None


class TermSession:
    """A single PTY-backed terminal session."""

    def __init__(
        self,
        sid: str,
        spec: SessionSpec,
        *,
        loop: asyncio.AbstractEventLoop,
        owner_id: int | None = None,
        on_close: Callable[[TermSession], None] | None = None,
    ) -> None:
        self.id = sid
        self.spec = spec
        self.owner_id = owner_id
        self.created_at = time.time()
        self.last_active = self.created_at
        self.exit_code: int | None = None
        self.closed = False
        self._on_close = on_close
        self._loop = loop
        self._scrollback = Scrollback(spec.scrollback_bytes)
        self._memory_limit = spec.memory_limit
        self._clients: dict[int, _ClientEntry] = {}
        self._lock = asyncio.Lock()
        self._share_token: str | None = None
        self._share_expires: float | None = None
        self._share_writable = False
        self._tmux_name: str | None = None
        self._recorder: Recorder | None = None
        self._record_input = False
        self.recording_path: Path | None = None
        argv = spec.argv
        env = spec.env
        if spec.backend == "tmux":
            self._tmux_name = tmux.session_name(sid)
            argv = tmux.wrap_argv(self._tmux_name, spec.argv)
            # tmux needs a usable TERM; a headless/CI environment may not set it.
            env = dict(os.environ if env is None else env)
            env.setdefault("TERM", "xterm-256color")
        self._pty: Pty = create_pty(
            argv,
            cwd=spec.cwd,
            env=env,
            cols=spec.cols,
            rows=spec.rows,
            loop=loop,
        )
        if self._tmux_name is not None:
            detach_only = getattr(self._pty, "set_detach_only", None)
            if callable(detach_only):
                detach_only()
        self._read_task = loop.create_task(self._read_loop())

    # -- introspection -------------------------------------------------

    @property
    def pid(self) -> int:
        return self._pty.pid

    @property
    def client_count(self) -> int:
        return len(self._clients)

    @property
    def is_alive(self) -> bool:
        return not self.closed and self._pty.poll() is None

    @property
    def dropped_input(self) -> int:
        """Write calls dropped because the child stopped draining its terminal."""
        return int(getattr(self._pty, "dropped_input", 0))

    def clients(self) -> list[dict[str, Any]]:
        """A read-only view of who is attached (for the detail endpoint).

        Returns copies: callers must not be able to reach into the live
        ``_ClientEntry`` objects and close or mutate another client.
        """
        return [
            {
                "writable": entry.writable,
                "share": entry.share,
                "pending_bytes": int(getattr(entry.client, "pending_bytes", 0)),
                "dropped_events": int(getattr(entry.client, "dropped_events", 0)),
                "dropped_bytes": int(getattr(entry.client, "dropped_bytes", 0)),
            }
            for entry in list(self._clients.values())
        ]

    def scrollback_snapshot(self) -> bytes:
        return self._scrollback.snapshot()

    def memory_usage(self) -> int:
        """Approximate bytes held by this session (scrollback + client backlogs)."""
        total = self._scrollback.size
        for entry in self._clients.values():
            total += int(getattr(entry.client, "pending_bytes", 0))
        return total

    def _enforce_memory_limit(self) -> None:
        """Drop the greediest client once the session is over its memory cap.

        The client is *told* before it is dropped. A silently vanishing
        terminal is indistinguishable from a network failure, and the product
        already promises the same thing for a dropped keystroke ("report it
        rather than leave a keyboard that appears dead").
        """
        if self._memory_limit <= 0:
            return
        # Give up queued terminal data before giving up on a viewer. The
        # previous behaviour evicted the greediest client outright, which is
        # the same over-punishment this project just spent a release removing
        # from the per-client path -- a session over *its* cap should shed
        # backlog first and only then, if it is still over, let someone go.
        if self.memory_usage() > self._memory_limit:
            excess = self.memory_usage() - self._memory_limit
            for entry in list(self._clients.values()):
                shed = getattr(entry.client, "shed_oldest", None)
                if callable(shed):
                    with contextlib.suppress(Exception):
                        shed(excess)
                if self.memory_usage() <= self._memory_limit:
                    return
        while self._clients and self.memory_usage() > self._memory_limit:
            key, entry = max(
                self._clients.items(),
                key=lambda kv: int(getattr(kv[1].client, "pending_bytes", 0)),
            )
            self._clients.pop(key, None)
            _bump_evicted()
            # Say why on the wire, not just in the close code: the endpoint
            # reports ``client.close_code`` and 1000 ("normal closure") is a
            # lie for "we threw you out over memory". ``setattr`` because not
            # every ``Client`` implementation carries the attribute.
            if entry.client.close_code is None:
                entry.client.close_code = 4410  # CLOSE_SLOW_CONSUMER
            with contextlib.suppress(ClientGone):
                entry.client.put(
                    {
                        "type": "evicted",
                        "reason": "memory",
                        "msg": "会话缓冲已达上限，本连接被释放（会话仍在运行）",
                    }
                )
            close = getattr(entry.client, "close", None)
            if callable(close):
                close()

    def rename(self, name: str) -> str:
        """Rename the session; blank names are ignored. Returns the new name."""
        cleaned = name.strip()
        if cleaned:
            self.spec.name = cleaned
        return self.spec.name

    def is_expired(self, now: float | None = None) -> bool:
        """Whether the session has exceeded its idle or maximum lifetime."""
        now = time.time() if now is None else now
        if self.spec.max_life is not None and now - self.created_at >= self.spec.max_life:
            return True
        if self.spec.idle_timeout is None:
            return False
        return now - self.last_active >= self.spec.idle_timeout

    # -- client attachment ---------------------------------------------

    async def attach(
        self, client: Client, *, writable: bool = True, share: str | None = None
    ) -> None:
        """Attach a client, replaying buffered output first.

        Replay is enqueued and the client registered while holding the same
        lock used by broadcasting, guaranteeing replay-before-live ordering.
        A read-only client is still attached (and receives output) but its
        input is ignored by the caller.
        """
        async with self._lock:
            if self.closed:
                raise ClientGone("会话已关闭")
            if self.spec.max_clients > 0 and len(self._clients) >= self.spec.max_clients:
                raise ClientGone("会话连接数已达上限")
            replay = self._scrollback.chunks()
            key = id(client)
            self._clients[key] = _ClientEntry(client, writable=writable, share=share)
            self.last_active = time.time()
            try:
                for chunk in replay:
                    client.put(chunk)
                client.put(
                    {
                        "type": "attached",
                        "session": self.id,
                        "name": self.spec.name,
                        "cols": self.spec.cols,
                        "rows": self.spec.rows,
                        "writable": writable,
                    }
                )
            except ClientGone:
                # Roll back the registration so a client that could not accept
                # the replay never lingers in the session's client set.
                self._clients.pop(key, None)
                raise

    async def detach(self, client: Client) -> None:
        async with self._lock:
            self._clients.pop(id(client), None)

    # -- sharing -------------------------------------------------------

    def create_share(self, ttl: float | None = None, *, writable: bool = False) -> str:
        """Create (or replace) a share token for this session."""
        self._share_token = secrets.token_urlsafe(24)
        self._share_expires = time.time() + ttl if ttl else None
        self._share_writable = writable
        return self._share_token

    def revoke_share(self) -> None:
        self._share_token = None
        self._share_expires = None
        self._share_writable = False

    def share_access(self, token: str | None) -> str | None:
        """Return ``"write"``, ``"read"`` or ``None`` for a share token."""
        if not token or self._share_token is None:
            return None
        if self._share_expires is not None and time.time() >= self._share_expires:
            return None
        if not secrets.compare_digest(token, self._share_token):
            return None
        return "write" if self._share_writable else "read"

    def share_valid(self, token: str | None) -> bool:
        return self.share_access(token) is not None

    @property
    def share_writable(self) -> bool:
        return self._share_writable and self.is_shared

    @property
    def is_shared(self) -> bool:
        return self._share_token is not None and (
            self._share_expires is None or time.time() < self._share_expires
        )

    @property
    def share_expiry(self) -> float | None:
        return self._share_expires

    def peek_share(self) -> str | None:
        """Return the active share token, or ``None`` if not shared."""
        return self._share_token if self.is_shared else None

    def restore_share(
        self, token: str | None, expires: float | None, writable: bool
    ) -> None:
        """Rehydrate a share link persisted before a restart.

        The token is restored verbatim (not rotated) so a link that was already
        handed out keeps working across a server restart -- the same promise
        the session itself makes on the tmux backend.
        """
        self._share_token = token or None
        self._share_expires = expires
        self._share_writable = bool(writable)

    # -- recording -----------------------------------------------------

    async def start_recording(self, path: Path | str, *, record_input: bool = False) -> Path:
        """Begin recording this session to an asciinema cast file."""
        await self.stop_recording()
        self._recorder = Recorder(
            Path(path), width=self.spec.cols, height=self.spec.rows
        )
        self._record_input = record_input
        self.recording_path = Path(path)
        return self.recording_path

    async def stop_recording(self) -> None:
        """Stop recording.

        The recorder joins its writer thread, so the blocking part runs on a
        worker thread instead of stalling the event loop.
        """
        recorder = self._recorder
        self._recorder = None
        if recorder is not None:
            await asyncio.to_thread(recorder.close)

    @property
    def is_recording(self) -> bool:
        return self._recorder is not None and not self._recorder.closed

    # -- I/O -----------------------------------------------------------

    def write_input(self, data: bytes) -> bool:
        """Forward input to the child.

        Returns ``False`` when the input was dropped because the child stopped
        draining its terminal, so the caller can tell the user instead of
        leaving them with a keyboard that appears dead.
        """
        if self.closed:
            return False
        self.last_active = time.time()
        if self._recorder is not None and self._record_input:
            self._recorder.input(data)
        return self._pty.write(data)

    def resize(self, cols: int, rows: int) -> None:
        if self.closed:
            return
        cols = min(max(1, cols), 1000)
        rows = min(max(1, rows), 1000)
        self.spec.cols = cols
        self.spec.rows = rows
        self._pty.resize(cols, rows)

    async def _broadcast(self, data: bytes) -> None:
        async with self._lock:
            self._scrollback.append(data)
            if self._recorder is not None:
                self._recorder.output(data)
            dead: list[int] = []
            for key, entry in self._clients.items():
                if entry.share is not None and not self.share_valid(entry.share):
                    dead.append(key)
                    continue
                try:
                    entry.client.put(data)
                except ClientGone as exc:
                    dead.append(key)
                    self._report_slow_consumer(entry, exc)
            for key in dead:
                self._clients.pop(key, None)
            self._enforce_memory_limit()

    def _report_slow_consumer(self, entry: _ClientEntry, exc: ClientGone) -> None:
        """Tell a client why it is being dropped, and leave a trace.

        Silent here meant three things at once: the operator's log said
        nothing, the metrics said nothing, and the user saw a terminal that
        simply stopped and then went black on reconnect.
        """
        _bump_slow_consumer()
        log.warning(
            "dropped a slow client from session %s: %s", self.id, exc
        )
        with contextlib.suppress(ClientGone):
            getattr(entry.client, "final_notice", entry.client.put)(
                {
                    "type": "evicted",
                    "reason": "backpressure",
                    "msg": "输出过快，本连接已被释放（会话仍在运行，重连即可恢复）",
                }
            )

    async def notify(self, message: dict[str, Any]) -> None:
        """Broadcast a control message to every attached client.

        Public because a rename (issued over HTTP) must reach the tabs already
        attached over WebSocket; without it two clients on one session keep
        showing different names until each happens to reconnect.
        """
        await self._notify(message)

    async def _notify(self, message: dict[str, Any]) -> None:
        async with self._lock:
            dead: list[int] = []
            for key, entry in self._clients.items():
                try:
                    entry.client.put(message)
                except ClientGone:
                    dead.append(key)
            for key in dead:
                self._clients.pop(key, None)

    async def _read_loop(self) -> None:
        try:
            while True:
                data = await self._pty.read()
                if not data:
                    break
                self.last_active = time.time()
                await self._broadcast(data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._notify({"type": "error", "msg": f"session read failed: {exc}"})
        finally:
            await self._finalize()

    async def _finalize(self) -> None:
        if self.closed:
            return
        self.closed = True
        await self.stop_recording()
        code = self._pty.poll()
        if code is None:
            # A bounded wait: a child that outlives its terminal (for example a
            # daemonized grandchild still holding the slave open) would make an
            # unbounded ``wait()`` hang forever and leak this session object.
            try:
                code = await asyncio.to_thread(self._pty.wait, FINALIZE_WAIT_TIMEOUT)
            except subprocess.TimeoutExpired:
                try:
                    self._pty.kill()
                    code = await asyncio.to_thread(self._pty.wait, FINALIZE_KILL_TIMEOUT)
                except Exception:
                    code = -1
            except Exception:
                code = -1
        self.exit_code = code if code is not None else -1
        await self._notify({"type": "exit", "code": self.exit_code})
        self._pty.close()
        if self._on_close is not None:
            self._on_close(self)

    # -- lifecycle -----------------------------------------------------

    async def stop(
        self,
        *,
        sig: int | None = None,
        timeout: float = 3.0,
        preserve: bool = False,
        reason: str | None = None,
    ) -> None:
        """Terminate the session and wait for its read loop to finish.

        With ``preserve=True`` a tmux-backed session's shell is left running so
        it can be reattached after a server restart; only the local attach
        client is closed.

        ``reason`` is announced to every attached client *before* the terminal
        goes away. Without it a stop is indistinguishable from a crash at the
        far end -- the screen simply dies -- and a remote operator has no way
        to tell "I asked for this" from "the box fell over".
        """
        if self.closed:
            return
        if reason and not preserve:
            with contextlib.suppress(Exception):
                await self._notify({"type": "notice", "level": "warn", "msg": reason})
        if self._tmux_name is not None and not preserve:
            await tmux.kill_session_async(self._tmux_name)
        if sig is None:
            sig = DEFAULT_TERM_SIGNAL
        if self._tmux_name is not None and preserve:
            # Detach the client without signalling its process group, so a
            # freshly forked tmux server is not caught by the signal.
            signaler = getattr(self._pty, "signal_process", None)
            if callable(signaler):
                signaler(sig)
            else:
                self._pty.terminate(sig)
        else:
            self._pty.terminate(sig)
        try:
            await asyncio.wait_for(asyncio.shield(self._read_task), timeout=timeout)
        except (TimeoutError, asyncio.CancelledError):
            self._read_task.cancel()
            await self._finalize()

    @property
    def backend(self) -> str:
        return self.spec.backend


class SessionManager:
    """Owns the lifecycle of every terminal session on this server."""

    def __init__(self, *, loop: asyncio.AbstractEventLoop | None = None) -> None:
        # The loop is resolved lazily: managers are often built while a server
        # factory runs, before the serving event loop exists.
        self._loop = loop
        self._sessions: dict[str, TermSession] = {}
        self._lock = asyncio.Lock()

    def list_sessions(self) -> list[TermSession]:
        self._reap()
        return list(self._sessions.values())

    def get(self, sid: str) -> TermSession | None:
        self._reap()
        return self._sessions.get(sid)

    async def create(
        self,
        spec: SessionSpec,
        *,
        sid: str | None = None,
        owner_id: int | None = None,
    ) -> TermSession:
        loop = self._loop or asyncio.get_running_loop()
        async with self._lock:
            sid = sid or self._new_id()
            if sid in self._sessions:
                raise ValueError(f"session id already exists: {sid}")
            session = TermSession(
                sid, spec, loop=loop, owner_id=owner_id, on_close=self._on_session_close
            )
            self._sessions[sid] = session
            return session

    async def reap_expired(self, now: float | None = None) -> list[str]:
        """Stop and remove sessions past their idle/max lifetime."""
        expired = [s for s in self.list_sessions() if s.is_expired(now)]
        if expired:
            reason = "会话已超过空闲/最长寿命，正在回收"
            await asyncio.gather(
                *(s.stop(reason=reason) for s in expired), return_exceptions=True
            )
        return [s.id for s in expired]

    async def remove(self, sid: str, *, sig: int | None = None) -> bool:
        async with self._lock:
            session = self._sessions.get(sid)
        if session is None:
            return False
        await session.stop(sig=sig, reason="会话已被终止")
        return True

    async def shutdown(self, *, preserve: bool = False) -> None:
        """Stop every session. ``preserve`` keeps tmux sessions alive.

        The reason is announced first so an operator watching the terminal sees
        "服务正在停止" instead of a screen that simply dies.
        """
        sessions = list(self._sessions.values())
        reason = None if preserve else "服务正在停止，会话即将结束"
        await asyncio.gather(
            *(s.stop(preserve=preserve, reason=reason) for s in sessions),
            return_exceptions=True,
        )
        self._sessions.clear()

    def _on_session_close(self, session: TermSession) -> None:
        # Called from the read loop; the entry is pruned lazily via _reap().
        self._sessions.pop(session.id, None)

    def _reap(self) -> None:
        for sid, session in list(self._sessions.items()):
            if session.closed:
                del self._sessions[sid]

    def _new_id(self) -> str:
        # Hex, not url-safe base64: an id must never start with "-", which the
        # CLI would parse as an option (e.g. `wsctl session kill -6dM4...`).
        return secrets.token_hex(8)


def within_user_quota(manager: SessionManager, limit: int, user_id: int | None) -> bool:
    """Whether ``user_id`` is still below the per-user session quota (0 = none)."""
    if limit <= 0:
        return True
    return sum(1 for s in manager.list_sessions() if s.owner_id == user_id) < limit
