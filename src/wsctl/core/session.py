"""Terminal sessions and their manager.

The central design rule: **a session's lifetime is independent of any client
connection**. Clients attach and detach freely; the underlying PTY keeps
running and its output is retained in a bounded scrollback so reconnecting
clients can rebuild their screen.
"""

from __future__ import annotations

import asyncio
import secrets
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from . import tmux
from .pty import Pty, create_pty
from .recording import Recorder
from .scrollback import DEFAULT_MAX_BYTES, Scrollback


class ClientGone(Exception):
    """Raised by a client sink that can no longer receive data."""


@runtime_checkable
class Client(Protocol):
    """A consumer attached to a session (browser tab or CLI client)."""

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
    joined_at: float = field(default_factory=time.time)


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
        if spec.backend == "tmux":
            self._tmux_name = tmux.session_name(sid)
            argv = tmux.wrap_argv(self._tmux_name, spec.argv)
        self._pty: Pty = create_pty(
            argv,
            cwd=spec.cwd,
            env=spec.env,
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

    def scrollback_snapshot(self) -> bytes:
        return self._scrollback.snapshot()

    def memory_usage(self) -> int:
        """Approximate bytes held by this session (scrollback + client backlogs)."""
        total = self._scrollback.size
        for entry in self._clients.values():
            total += int(getattr(entry.client, "pending_bytes", 0))
        return total

    def _enforce_memory_limit(self) -> None:
        if self._memory_limit <= 0:
            return
        while self._clients and self.memory_usage() > self._memory_limit:
            key, entry = max(
                self._clients.items(),
                key=lambda kv: int(getattr(kv[1].client, "pending_bytes", 0)),
            )
            self._clients.pop(key, None)
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
                raise ClientGone("session is closed")
            if self.spec.max_clients > 0 and len(self._clients) >= self.spec.max_clients:
                raise ClientGone("session has reached its client limit")
            replay = self._scrollback.snapshot()
            self._clients[id(client)] = _ClientEntry(client, writable=writable, share=share)
            self.last_active = time.time()
            if replay:
                client.put(replay)
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

    def peek_share(self) -> str | None:
        """Return the active share token, or ``None`` if not shared."""
        return self._share_token if self.is_shared else None

    # -- recording -----------------------------------------------------

    def start_recording(self, path: Path | str, *, record_input: bool = False) -> Path:
        """Begin recording this session to an asciinema cast file."""
        self.stop_recording()
        self._recorder = Recorder(
            Path(path), width=self.spec.cols, height=self.spec.rows
        )
        self._record_input = record_input
        self.recording_path = Path(path)
        return self.recording_path

    def stop_recording(self) -> None:
        if self._recorder is not None:
            self._recorder.close()
            self._recorder = None

    @property
    def is_recording(self) -> bool:
        return self._recorder is not None and not self._recorder.closed

    # -- I/O -----------------------------------------------------------

    def write_input(self, data: bytes) -> None:
        if self.closed:
            return
        self.last_active = time.time()
        if self._recorder is not None and self._record_input:
            self._recorder.input(data)
        self._pty.write(data)

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
                except ClientGone:
                    dead.append(key)
            for key in dead:
                self._clients.pop(key, None)
            self._enforce_memory_limit()

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
        self.stop_recording()
        code = self._pty.poll()
        if code is None:
            try:
                code = await self._loop.run_in_executor(None, self._pty.wait)
            except Exception:
                code = -1
        self.exit_code = code if code is not None else -1
        await self._notify({"type": "exit", "code": self.exit_code})
        self._pty.close()
        if self._on_close is not None:
            self._on_close(self)

    # -- lifecycle -----------------------------------------------------

    async def stop(
        self, *, sig: int | None = None, timeout: float = 3.0, preserve: bool = False
    ) -> None:
        """Terminate the session and wait for its read loop to finish.

        With ``preserve=True`` a tmux-backed session's shell is left running so
        it can be reattached after a server restart; only the local attach
        client is closed.
        """
        if self.closed:
            return
        if self._tmux_name is not None and not preserve:
            tmux.kill_session(self._tmux_name)
        if sig is None:
            sig = signal.SIGHUP
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
        for session in expired:
            await session.stop()
        return [s.id for s in expired]

    async def remove(self, sid: str, *, sig: int | None = None) -> bool:
        async with self._lock:
            session = self._sessions.get(sid)
        if session is None:
            return False
        await session.stop(sig=sig)
        return True

    async def shutdown(self, *, preserve: bool = False) -> None:
        """Stop every session. ``preserve`` keeps tmux sessions alive."""
        sessions = list(self._sessions.values())
        await asyncio.gather(
            *(s.stop(preserve=preserve) for s in sessions), return_exceptions=True
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
        return secrets.token_urlsafe(8)
