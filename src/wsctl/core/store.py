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
from typing import Any

from .passwords import hash_password, verify_password

SCHEMA_VERSION = 2

# Verified against when a username does not exist, so login timing does not
# reveal whether an account is present.
_DUMMY_HASH = hash_password("wsctl-timing-equalizer")

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
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    owner_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    backend     TEXT NOT NULL DEFAULT 'local',
    command     TEXT,
    argv        TEXT,
    env         TEXT,
    cwd         TEXT,
    idle_timeout REAL,
    max_life    REAL,
    status      TEXT NOT NULL DEFAULT 'running',
    created_at  REAL NOT NULL,
    last_active REAL NOT NULL
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
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
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
                "idle_timeout": "REAL",
                "max_life": "REAL",
            }
        }
        with self._lock, self._conn:
            for table, columns in additions.items():
                existing = {
                    str(row["name"])
                    for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
                for name, decl in columns.items():
                    if name not in existing:
                        self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
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
        now = time.time()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO users(username, password_hash, role, created_at) VALUES(?, ?, ?, ?)",
                (username, hash_password(password), role, now),
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

    def user_list(self) -> list[User]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        return [self._row_to_user(r) for r in rows]

    def user_delete(self, username: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM users WHERE username = ?", (username,))
        return cur.rowcount > 0

    def user_set_password(self, username: str, password: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE users SET password_hash = ? WHERE username = ?",
                (hash_password(password), username),
            )
        return cur.rowcount > 0

    def user_set_role(self, username: str, role: str) -> bool:
        if role not in ("admin", "user"):
            raise ValueError(f"invalid role: {role}")
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE users SET role = ? WHERE username = ?", (role, username)
            )
        return cur.rowcount > 0

    def user_set_disabled(self, username: str, disabled: bool) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE users SET disabled = ? WHERE username = ?",
                (1 if disabled else 0, username),
            )
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
            verify_password(password, _DUMMY_HASH)  # equalize timing
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

    def resolve_auth_session(self, token: str) -> User | None:
        now = time.time()
        token_hash = hash_token(token)
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT user_id, expires_at FROM auth_sessions WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            if float(row["expires_at"]) < now:
                self._conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
                return None
            self._conn.execute(
                "UPDATE auth_sessions SET last_seen = ? WHERE token_hash = ?", (now, token_hash)
            )
        user = self.user_get_by_id(int(row["user_id"]))
        if user is not None and user.disabled:
            self.delete_auth_session(token)
            return None
        return user

    def delete_auth_session(self, token: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM auth_sessions WHERE token_hash = ?", (hash_token(token),)
            )

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
    ) -> None:
        now = time.time()
        argv_json = json.dumps(argv) if argv is not None else None
        env_json = json.dumps(env) if env is not None else None
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO term_sessions"
                "(id, name, owner_id, backend, command, argv, env, cwd, idle_timeout,"
                " max_life, status, created_at, last_active)"
                " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET"
                " name=excluded.name, status=excluded.status, last_active=excluded.last_active",
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
                    now,
                    now,
                ),
            )

    def term_session_set_status(self, sid: str, status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE term_sessions SET status = ?, last_active = ? WHERE id = ?",
                (status, time.time(), sid),
            )

    def term_session_stop_missing(self, alive_ids: set[str]) -> int:
        """Mark rows for sessions no longer held in memory as stopped."""
        with self._lock, self._conn:
            if alive_ids:
                placeholders = ",".join("?" for _ in alive_ids)
                cur = self._conn.execute(
                    f"UPDATE term_sessions SET status = 'stopped'"
                    f" WHERE status = 'running' AND id NOT IN ({placeholders})",
                    tuple(alive_ids),
                )
            else:
                cur = self._conn.execute(
                    "UPDATE term_sessions SET status = 'stopped' WHERE status = 'running'"
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

    def log_event(
        self,
        event: str,
        *,
        user_id: int | None = None,
        term_session_id: str | None = None,
        payload: str | None = None,
        ip: str | None = None,
    ) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO audit_logs(user_id, term_session_id, event, payload, ip, ts)"
                " VALUES(?, ?, ?, ?, ?, ?)",
                (user_id, term_session_id, event, payload, ip, now),
            )
        if self._sink is not None:
            self._sink(
                {
                    "event": event,
                    "user_id": user_id,
                    "term_session_id": term_session_id,
                    "payload": payload,
                    "ip": ip,
                    "ts": now,
                }
            )

    def recent_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]
