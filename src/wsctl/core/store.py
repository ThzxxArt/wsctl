"""SQLite-backed persistence for users, auth sessions and audit logs.

Synchronous by design: queries are small and infrequent relative to PTY I/O,
and ``sqlite3`` with WAL mode is plenty fast for a single-server deployment.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .authcache import MISS as _CACHE_MISS
from .passwords import (
    burn_verification,
    hash_password,
    validate_password,
    verify_password,
)

SCHEMA_VERSION = 4

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    totp_secret   TEXT,
    disabled      INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    ip         TEXT,
    user_agent TEXT,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    last_seen  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS term_sessions (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    owner_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
    backend        TEXT NOT NULL DEFAULT 'local',
    command        TEXT,
    argv           TEXT,
    env            TEXT,
    cwd            TEXT,
    idle_timeout   REAL,
    max_life       REAL,
    status         TEXT NOT NULL DEFAULT 'running',
    instance_id    TEXT,
    share_token    TEXT,
    share_expires  REAL,
    share_writable INTEGER NOT NULL DEFAULT 0,
    created_at     REAL NOT NULL,
    last_active    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS instances (
    id         TEXT PRIMARY KEY,
    pid        INTEGER,
    host       TEXT,
    started_at REAL NOT NULL,
    heartbeat  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER,
    term_session_id TEXT,
    event           TEXT NOT NULL,
    payload         TEXT,
    ip              TEXT,
    ts              REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_token ON auth_sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_audit_logs_ts ON audit_logs(ts);
CREATE INDEX IF NOT EXISTS idx_term_sessions_status ON term_sessions(status);
"""


@dataclass(frozen=True)
class User:
    id: int
    username: str
    role: str
    disabled: bool
    created_at: float


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Store:
    """Thin, thread-safe wrapper around a SQLite database."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._sink: Any = None
        self._auth_cache: Any = None
        # When true, an active session's expiry slides forward on each use.
        self.sliding_ttl = False
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def set_event_sink(self, sink: Any) -> None:
        """Register a callback invoked with every audit event (e.g. a webhook)."""
        self._sink = sink

    def set_auth_cache(self, cache: Any) -> None:
        """Register a short-lived cache for :meth:`resolve_auth_session`."""
        self._auth_cache = cache

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def _migrate(self) -> None:
        """Add columns introduced after the initial schema (idempotent)."""
        additions = {
            "term_sessions": {
                "argv": "TEXT",
                "env": "TEXT",
                "end_reason": "TEXT",
                "idle_timeout": "REAL",
                "max_life": "REAL",
                "instance_id": "TEXT",
                "share_token": "TEXT",
                "share_expires": "REAL",
                "share_writable": "INTEGER NOT NULL DEFAULT 0",
            }
        }
        with self._lock, self._conn:
            for table, columns in additions.items():
                existing = {
                    str(row["name"])
                    for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
                for name, decl in columns.items():
                    if name in existing:
                        continue
                    try:
                        self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                    except sqlite3.OperationalError as exc:
                        # Two instances sharing one data directory both open
                        # the store at startup and both run this migration: each
                        # sees "the column is absent" and both try to add it,
                        # and the loser used to fail the whole open. That is the
                        # documented multi-instance mode (SO_REUSEPORT handover),
                        # so the end state is what matters -- and it is now the
                        # same column either way.
                        if "duplicate column name" not in str(exc).lower():
                            raise
            # Indexes on migrated columns must be created after the ALTER TABLE.
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_term_sessions_instance"
                " ON term_sessions(instance_id)"
            )
            self._conn.execute(
                "UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION),)
            )

    def _row_to_user(self, row: sqlite3.Row) -> User:
        return User(
            id=int(row["id"]),
            username=str(row["username"]),
            role=str(row["role"]),
            disabled=bool(row["disabled"]),
            created_at=float(row["created_at"]),
        )

    # -- users ---------------------------------------------------------

    def user_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"])

    def user_create(self, username: str, password: str, role: str = "user") -> User:
        if role not in ("admin", "user"):
            raise ValueError(f"invalid role: {role}")
        validate_password(password)
        # Hash *before* taking the lock: Argon2 costs tens of milliseconds of
        # CPU and holding the store lock over it would serialise every other
        # database operation (including authentication) behind one hash.
        password_hash = hash_password(password)
        now = time.time()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO users(username, password_hash, role, created_at) VALUES(?, ?, ?, ?)",
                (username, password_hash, role, now),
            )
            user_id = int(cur.lastrowid or 0)
        return User(id=user_id, username=username, role=role, disabled=False, created_at=now)

    def user_get(self, username: str) -> User | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
        return self._row_to_user(row) if row else None

    def user_get_by_id(self, user_id: int) -> User | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_to_user(row) if row else None

    def users_by_ids(self, ids: set[int]) -> dict[int, User]:
        """Resolve many owners in one query.

        The session list used to call :meth:`user_get_by_id` once per row --
        64 sessions meant 64 synchronous SQLite round trips *on the event
        loop*. One ``IN`` query replaces them.
        """
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM users WHERE id IN ({placeholders})", tuple(sorted(ids))
            ).fetchall()
        users = {int(row["id"]): self._row_to_user(row) for row in rows}
        return users

    def user_list(self) -> list[User]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        return [self._row_to_user(r) for r in rows]

    def user_delete(self, username: str) -> bool:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone()
            cur = self._conn.execute("DELETE FROM users WHERE username = ?", (username,))
        self._invalidate_user(int(row["id"]) if row else None)
        return cur.rowcount > 0

    def user_set_password(self, username: str, password: str) -> bool:
        validate_password(password)
        # See user_create: hash outside the lock.
        password_hash = hash_password(password)
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone()
            cur = self._conn.execute(
                "UPDATE users SET password_hash = ? WHERE username = ?",
                (password_hash, username),
            )
        self._invalidate_user(int(row["id"]) if row else None)
        return cur.rowcount > 0

    def user_set_role(self, username: str, role: str) -> bool:
        if role not in ("admin", "user"):
            raise ValueError(f"invalid role: {role}")
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone()
            cur = self._conn.execute(
                "UPDATE users SET role = ? WHERE username = ?", (role, username)
            )
        self._invalidate_user(int(row["id"]) if row else None)
        return cur.rowcount > 0

    def user_set_disabled(self, username: str, disabled: bool) -> bool:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone()
            cur = self._conn.execute(
                "UPDATE users SET disabled = ? WHERE username = ?",
                (1 if disabled else 0, username),
            )
        self._invalidate_user(int(row["id"]) if row else None)
        return cur.rowcount > 0

    def user_set_totp(self, username: str, secret: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE users SET totp_secret = ? WHERE username = ?", (secret, username)
            )
        return cur.rowcount > 0

    def user_clear_totp(self, username: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE users SET totp_secret = NULL WHERE username = ?", (username,)
            )
        return cur.rowcount > 0

    def user_totp_secret(self, username: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT totp_secret FROM users WHERE username = ?", (username,)
            ).fetchone()
        if row is None or row["totp_secret"] is None:
            return None
        return str(row["totp_secret"])

    def user_authenticate(self, username: str, password: str) -> User | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
        if row is None:
            burn_verification(password)  # equalize timing
            return None
        if row["disabled"]:
            verify_password(password, str(row["password_hash"]))
            return None
        if not verify_password(password, str(row["password_hash"])):
            return None
        return self._row_to_user(row)

    # -- auth sessions -------------------------------------------------

    def create_auth_session(
        self,
        user_id: int,
        *,
        ttl: int,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO auth_sessions"
                "(user_id, token_hash, ip, user_agent, created_at, expires_at, last_seen)"
                " VALUES(?, ?, ?, ?, ?, ?, ?)",
                (user_id, hash_token(token), ip, user_agent, now, now + ttl, now),
            )
        return token

    LAST_SEEN_THROTTLE = 30.0

    def _invalidate_user(self, user_id: int | None) -> None:
        cache = self._auth_cache
        if cache is not None and user_id is not None:
            cache.invalidate_user(user_id)

    def _invalidate_token(self, token_hash: str) -> None:
        cache = self._auth_cache
        if cache is not None:
            cache.invalidate_token(token_hash)

    def resolve_auth_session(self, token: str) -> User | None:
        """Resolve a bearer/cookie token to its user, honouring expiry.

        Consults the optional short-lived cache first. Every mutation that
        changes what a token may do (disable, password, role, delete) drops the
        affected entries, so revocation is immediate even on a cache hit.
        """
        token_hash = hash_token(token)
        cache = self._auth_cache
        if cache is not None:
            hit = cache.get(token_hash)
            if hit is not _CACHE_MISS:
                return cast("User | None", hit)
            # Taken *before* the read: if a revocation lands while we are
            # talking to the database the write-back below is discarded rather
            # than repopulating the cache with the pre-revocation user.
            epoch = cache.epoch()
        else:
            epoch = None
        user, token_expiry = self._resolve_auth_session_uncached(token_hash)
        if cache is not None:
            cache.put(token_hash, user, token_expiry=token_expiry, epoch=epoch)
        return user

    def _resolve_auth_session_uncached(
        self, token_hash: str
    ) -> tuple[User | None, float]:
        """Return ``(user, token_expiry)``; ``user`` is ``None`` when rejected."""
        now = time.time()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT user_id, expires_at, created_at, last_seen FROM auth_sessions"
                " WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None:
                # A known-bad token: cache the rejection for one cache TTL only.
                ttl = getattr(self._auth_cache, "ttl", 0.0) if self._auth_cache else 0.0
                return None, now + float(ttl)
            expires_at = float(row["expires_at"])
            if expires_at < now:
                self._conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
                return None, now
            updates = ""
            params: list[Any] = []
            # Throttle the last_seen write: it runs on every request and on the
            # periodic WebSocket re-check, so writing every time would add a DB
            # write per request even for an idle connection.
            if now - float(row["last_seen"]) >= self.LAST_SEEN_THROTTLE:
                updates = "last_seen = ?"
                params.append(now)
            if self.sliding_ttl:
                ttl = expires_at - float(row["created_at"])
                expires_at = now + ttl
                updates = f"{updates + ', ' if updates else ''}expires_at = ?"
                params.append(expires_at)
            if updates:
                params.append(token_hash)
                self._conn.execute(
                    f"UPDATE auth_sessions SET {updates} WHERE token_hash = ?", tuple(params)
                )
        user = self.user_get_by_id(int(row["user_id"]))
        if user is not None and user.disabled:
            with self._lock, self._conn:
                self._conn.execute(
                    "DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,)
                )
            self._invalidate_token(token_hash)
            return None, expires_at
        return user, expires_at

    def delete_auth_session(self, token: str) -> None:
        token_hash = hash_token(token)
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,)
            )
        self._invalidate_token(token_hash)

    def delete_user_sessions(self, user_id: int) -> int:
        """Revoke every login session of a user (e.g. after a password reset)."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM auth_sessions WHERE user_id = ?", (user_id,)
            )
        self._invalidate_user(user_id)
        return cur.rowcount

    def admin_count(self) -> int:
        """Number of enabled admins (used to protect the last one)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND disabled = 0"
            ).fetchone()
        return int(row["n"])

    def purge_expired_sessions(self) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM auth_sessions WHERE expires_at < ?", (time.time(),)
            )
        return cur.rowcount

    # -- term session metadata -----------------------------------------

    def term_session_upsert(
        self,
        sid: str,
        *,
        name: str,
        owner_id: int | None,
        backend: str = "local",
        command: str | None = None,
        argv: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        idle_timeout: float | None = None,
        max_life: float | None = None,
        status: str = "running",
        instance_id: str | None = None,
    ) -> None:
        now = time.time()
        argv_json = json.dumps(argv) if argv is not None else None
        env_json = json.dumps(env) if env is not None else None
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO term_sessions"
                "(id, name, owner_id, backend, command, argv, env, cwd, idle_timeout,"
                " max_life, status, instance_id, created_at, last_active)"
                " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET"
                " name=excluded.name, status=excluded.status,"
                " instance_id=excluded.instance_id, last_active=excluded.last_active",
                (
                    sid,
                    name,
                    owner_id,
                    backend,
                    command,
                    argv_json,
                    env_json,
                    cwd,
                    idle_timeout,
                    max_life,
                    status,
                    instance_id,
                    now,
                    now,
                ),
            )

    def term_session_set_status(
        self, sid: str, status: str, reason: str | None = None
    ) -> None:
        """Record how a session ended.

        ``reason`` is the human half of ``status``: "killed" says *what*
        happened, "被管理员终止" says *why*. Without it the history list showed
        a bare enum value and left the user to guess.
        """
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE term_sessions SET status = ?, end_reason = ?, last_active = ?"
                " WHERE id = ?",
                (status, reason, time.time(), sid),
            )

    def term_session_set_instance(self, sid: str, instance_id: str | None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE term_sessions SET instance_id = ? WHERE id = ?", (instance_id, sid)
            )

    def term_session_stop_missing(
        self, alive_ids: set[str], *, instance_id: str | None = None
    ) -> int:
        """Mark running sessions not held in memory as stopped.

        When ``instance_id`` is given, only rows owned by that instance are
        considered, so a concurrently running peer (sharing the same data
        directory via SO_REUSEPORT) never has its live sessions marked stopped.
        """
        clauses = ["status = 'running'"]
        params: list[Any] = []
        if instance_id is not None:
            clauses.append("instance_id = ?")
            params.append(instance_id)
        if alive_ids:
            placeholders = ",".join("?" for _ in alive_ids)
            clauses.append(f"id NOT IN ({placeholders})")
            params.extend(sorted(alive_ids))
        with self._lock, self._conn:
            cur = self._conn.execute(
                f"UPDATE term_sessions SET status = 'stopped' WHERE {' AND '.join(clauses)}",
                tuple(params),
            )
        return cur.rowcount

    def term_session_list(self, owner_id: int | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if owner_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM term_sessions ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM term_sessions WHERE owner_id = ? ORDER BY created_at DESC",
                    (owner_id,),
                ).fetchall()
        return [dict(row) for row in rows]

    def term_session_history(
        self, *, owner_id: int | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Finished session rows (newest first).

        These rows were always written -- ``status``, ``created_at`` and the
        retention policy all exist -- but nothing ever read them, so a session
        that ended left no trace anywhere in the product. ``owner_id=None``
        means "everyone" (admin view).
        """
        clauses = ["status != 'running'"]
        params: list[Any] = []
        if owner_id is not None:
            clauses.append("owner_id = ?")
            params.append(owner_id)
        params.append(max(1, min(limit, 1000)))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM term_sessions WHERE {' AND '.join(clauses)}"
                " ORDER BY last_active DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def term_session_timeline(self, sid: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Audit events for one session, oldest first -- the session's story.

        The events were always written (``session_create`` / ``session_attach``
        / ``session_detach`` / ``session_kill`` …); nothing joined them to the
        session they describe.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, event, user_id, ip, payload FROM audit_logs"
                " WHERE term_session_id = ? ORDER BY ts ASC, id ASC LIMIT ?",
                (sid, max(1, min(limit, 500))),
            ).fetchall()
        return [dict(row) for row in rows]

    def term_session_get(self, sid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM term_sessions WHERE id = ?", (sid,)
            ).fetchone()
        return dict(row) if row else None

    def term_session_set_share(
        self,
        sid: str,
        *,
        token: str | None,
        expires: float | None = None,
        writable: bool = False,
    ) -> None:
        """Persist (or clear, with ``token=None``) a session's share link.

        Shares live in the database rather than only in memory so a link keeps
        working across a server restart -- the same promise the session itself
        makes when it runs on the tmux backend.
        """
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE term_sessions SET share_token = ?, share_expires = ?,"
                " share_writable = ? WHERE id = ?",
                (token, expires, 1 if writable else 0, sid),
            )

    def term_session_clear_expired_shares(self, now: float | None = None) -> int:
        """Drop share links whose TTL has elapsed (their token is worthless)."""
        now = time.time() if now is None else now
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE term_sessions SET share_token = NULL, share_expires = NULL,"
                " share_writable = 0"
                " WHERE share_token IS NOT NULL AND share_expires IS NOT NULL"
                " AND share_expires < ?",
                (now,),
            )
        return cur.rowcount

    # -- instance leases -----------------------------------------------

    def instance_register(self, instance_id: str, *, pid: int | None = None,
                          host: str | None = None) -> None:
        """Register this process and refresh its lease (idempotent)."""
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO instances(id, pid, host, started_at, heartbeat)"
                " VALUES(?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET"
                " pid=excluded.pid, host=excluded.host, heartbeat=excluded.heartbeat",
                (instance_id, pid, host, now, now),
            )

    def instance_heartbeat(self, instance_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE instances SET heartbeat = ? WHERE id = ?", (time.time(), instance_id)
            )

    def instance_remove(self, instance_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM instances WHERE id = ?", (instance_id,))

    def instance_all(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, pid, host, heartbeat FROM instances"
            ).fetchall()
        return [dict(row) for row in rows]

    def instance_alive_ids(self, ttl: float) -> set[str]:
        """Ids whose lease was refreshed within ``ttl`` seconds."""
        cutoff = time.time() - ttl
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM instances WHERE heartbeat >= ?", (cutoff,)
            ).fetchall()
        return {str(row["id"]) for row in rows}

    def instance_dead_ids(self, ttl: float) -> list[str]:
        """Ids whose lease is older than ``ttl`` seconds."""
        cutoff = time.time() - ttl
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM instances WHERE heartbeat < ?", (cutoff,)
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def term_sessions_owned_by(self, instance_ids: set[str]) -> list[dict[str, Any]]:
        """All session rows owned by any of ``instance_ids`` (including stopped)."""
        if not instance_ids:
            return []
        placeholders = ",".join("?" for _ in instance_ids)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM term_sessions WHERE instance_id IN ({placeholders})",
                tuple(sorted(instance_ids)),
            ).fetchall()
        return [dict(row) for row in rows]

    # -- retention -----------------------------------------------------

    def purge_audit(self, before: float) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM audit_logs WHERE ts < ?", (before,))
        return cur.rowcount

    def purge_term_sessions(self, before: float) -> int:
        """Delete finished session rows older than ``before`` (never running ones)."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM term_sessions WHERE status != 'running' AND created_at < ?",
                (before,),
            )
        return cur.rowcount

    # -- settings ------------------------------------------------------

    def setting_get(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row is not None else default

    def setting_set(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def setting_all(self) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM settings").fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    # -- audit ---------------------------------------------------------

    def write_events(self, events: list[dict[str, Any]]) -> None:
        """Persist a batch of audit events (no sink dispatch).

        Kept separate from :meth:`dispatch_events` so the async audit writer can
        run the DB write in a worker thread while dispatching the webhook sink
        back on the event loop (``asyncio.Queue`` is not thread-safe).
        """
        if not events:
            return
        now = time.time()
        rows: list[tuple[Any, ...]] = []
        for event in events:
            ts = float(event.get("ts", now))
            event["ts"] = ts
            rows.append(
                (
                    event.get("user_id"),
                    event.get("term_session_id"),
                    event["event"],
                    event.get("payload"),
                    event.get("ip"),
                    ts,
                )
            )
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO audit_logs(user_id, term_session_id, event, payload, ip, ts)"
                " VALUES(?, ?, ?, ?, ?, ?)",
                rows,
            )

    def dispatch_events(self, events: list[dict[str, Any]]) -> None:
        """Forward events to the registered sink (e.g. a webhook)."""
        if self._sink is None:
            return
        for event in events:
            self._sink(dict(event))

    def log_events(self, events: list[dict[str, Any]]) -> None:
        self.write_events(events)
        self.dispatch_events(events)

    def log_event(
        self,
        event: str,
        *,
        user_id: int | None = None,
        term_session_id: str | None = None,
        payload: str | None = None,
        ip: str | None = None,
    ) -> None:
        self.log_events(
            [
                {
                    "event": event,
                    "user_id": user_id,
                    "term_session_id": term_session_id,
                    "payload": payload,
                    "ip": ip,
                }
            ]
        )

    def recent_audit(
        self,
        limit: int = 100,
        *,
        offset: int = 0,
        event: str | None = None,
        user_id: int | None = None,
        ip: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if event:
            clauses.append("event = ?")
            params.append(event)
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if ip:
            clauses.append("ip = ?")
            params.append(ip)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM audit_logs{where} ORDER BY id DESC LIMIT ? OFFSET ?",
                (*params, limit, max(offset, 0)),
            ).fetchall()
        return [dict(row) for row in rows]
