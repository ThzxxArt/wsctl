"""SQLite-backed persistence for users, auth sessions and audit logs.

Synchronous by design: queries are small and infrequent relative to PTY I/O,
and ``sqlite3`` with WAL mode is plenty fast for a single-server deployment.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
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

SCHEMA_VERSION = 6

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
    created_at    REAL NOT NULL,
    password_changed_at REAL NOT NULL DEFAULT 0
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
    end_reason     TEXT,
    exit_code      INTEGER,
    share_token    TEXT,
    share_expires  REAL,
    share_writable INTEGER NOT NULL DEFAULT 0,
    generation     TEXT,
    snapshot_mode  TEXT NOT NULL DEFAULT 'auto',
    shell_integration INTEGER NOT NULL DEFAULT 1,
    last_seq       INTEGER NOT NULL DEFAULT 0,
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
    request_id      TEXT,
    ts              REAL NOT NULL,
    prev_hash       TEXT NOT NULL DEFAULT '',
    hash            TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS shares (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    term_session_id TEXT NOT NULL,
    token_hash      TEXT NOT NULL UNIQUE,
    token_hint      TEXT NOT NULL DEFAULT '',
    scope           TEXT NOT NULL DEFAULT 'read',
    label           TEXT NOT NULL DEFAULT '',
    created_by      INTEGER,
    created_at      REAL NOT NULL,
    expires_at      REAL,
    last_used       REAL
);

CREATE TABLE IF NOT EXISTS api_keys (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    name       TEXT NOT NULL DEFAULT '',
    token_hash TEXT NOT NULL UNIQUE,
    scopes     TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    expires_at REAL,
    last_used  REAL
);

CREATE TABLE IF NOT EXISTS layouts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    name        TEXT NOT NULL,
    layout_json TEXT NOT NULL DEFAULT '{}',
    updated_at  REAL NOT NULL,
    UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS command_blocks (
    id              TEXT PRIMARY KEY,
    term_session_id TEXT NOT NULL,
    command         TEXT NOT NULL DEFAULT '',
    cwd             TEXT,
    started_at      REAL,
    ended_at        REAL,
    duration_ms     INTEGER,
    exit_code       INTEGER,
    status          TEXT NOT NULL DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    endpoint   TEXT NOT NULL UNIQUE,
    keys_json  TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_token ON auth_sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_audit_logs_ts ON audit_logs(ts);
CREATE INDEX IF NOT EXISTS idx_term_sessions_status ON term_sessions(status);
CREATE INDEX IF NOT EXISTS idx_shares_session ON shares(term_session_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_layouts_user ON layouts(user_id);
"""


@dataclass(frozen=True)
class User:
    id: int
    username: str
    role: str
    disabled: bool
    created_at: float
    #: Wall-clock time of the last password change (0 = never recorded).
    #: The server uses it to end terminals opened under the old password: a
    #: rotation done by ``wsctl user passwd`` runs in *another* process and
    #: cannot reach this one's live shells, so the database is the only signal
    #: both processes share.
    password_changed_at: float = 0.0


def _is_lock_contention(exc: BaseException) -> bool:
    """Is this "a peer is holding the database", i.e. worth another try?

    Kept narrow: anything else -- a corrupt file, a missing directory -- must
    surface immediately rather than be hidden behind twenty seconds of
    retries.
    """
    text = str(exc).lower()
    return "locked" in text or "busy" in text


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


GENESIS_HASH = "0" * 64


def audit_digest(
    *,
    prev_hash: str,
    ts: float,
    user_id: int | None,
    term_session_id: str | None,
    event: str,
    payload: str | None,
    ip: str | None,
    request_id: str | None,
) -> str:
    """Hash one audit row, chained to its predecessor.

    The encoding is explicit and stable: a verification tool written in
    another language must be able to reproduce it from the exported rows. JSON
    is the obvious canonical form, and ``separators`` removes the whitespace
    ambiguity.
    """
    body = json.dumps(
        {
            "prev": prev_hash,
            "ts": f"{float(ts):.6f}",
            "user_id": user_id,
            "term_session_id": term_session_id,
            "event": event,
            "payload": payload,
            "ip": ip,
            "request_id": request_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class LastAdminError(Exception):
    """A guarded mutation would leave the instance without an enabled admin."""


class Store:
    """Thin, thread-safe wrapper around a SQLite database."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._sink: Any = None
        self._auth_cache: Any = None
        #: SQLite ``data_version`` observed when the auth cache was last
        #: trusted. It changes when *another* connection commits, which is how
        #: a CLI ``user passwd/disable/role`` invalidates this process's cache
        #: immediately instead of after the 30s TTL.
        self._auth_data_version: int | None = None
        # When true, an active session's expiry slides forward on each use.
        self.sliding_ttl = False
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._open_schema()

    #: How long an open waits for a peer that is mid-migration. Concurrent
    #: opens are a supported mode -- two instances sharing a data directory is
    #: exactly what a SO_REUSEPORT handover does -- so "someone else holds the
    #: lock" must not fail the open.
    OPEN_LOCK_TIMEOUT = 20.0

    def _open_schema(self) -> None:
        """Apply pragmas, schema and migrations, waiting out a busy peer.

        ``PRAGMA journal_mode=WAL`` and the DDL both take write locks. On
        Windows in particular a peer mid-migration surfaces as
        ``database is locked`` rather than waiting: ``busy_timeout`` covers
        ordinary statements, not this window. The work below is idempotent
        (``CREATE TABLE IF NOT EXISTS`` plus column additions that are already
        tolerated), so retrying the whole sequence is safe and is what makes
        "start two instances at once" work instead of one of them dying.
        """
        deadline = time.monotonic() + self.OPEN_LOCK_TIMEOUT
        while True:
            try:
                self._conn.execute("PRAGMA busy_timeout=5000")
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA foreign_keys=ON")
                self._init_schema()
                self._migrate()
                # Password hashes, TOTP secrets, API-key digests and the audit
                # trail live in this file. SQLite creates it 0644 under the
                # usual umask, which makes every account's second factor
                # readable by any local user. The WAL is equally sensitive:
                # committed pages live there until the next checkpoint.
                for suffix in ("", "-wal", "-shm"):
                    with contextlib.suppress(OSError):
                        os.chmod(self.path.with_name(self.path.name + suffix), 0o600)
                return
            except sqlite3.OperationalError as exc:
                if not _is_lock_contention(exc):
                    raise
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

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
            "users": {
                "password_changed_at": "REAL NOT NULL DEFAULT 0",
            },
            "term_sessions": {
                "argv": "TEXT",
                "env": "TEXT",
                "end_reason": "TEXT",
                "exit_code": "INTEGER",
                "idle_timeout": "REAL",
                "max_life": "REAL",
                "instance_id": "TEXT",
                "share_token": "TEXT",
                "share_expires": "REAL",
                "share_writable": "INTEGER NOT NULL DEFAULT 0",
                "generation": "TEXT",
                "snapshot_mode": "TEXT NOT NULL DEFAULT 'auto'",
                "shell_integration": "INTEGER NOT NULL DEFAULT 1",
                "last_seq": "INTEGER NOT NULL DEFAULT 0",
            },
            "audit_logs": {
                "request_id": "TEXT",
                "prev_hash": "TEXT NOT NULL DEFAULT ''",
                "hash": "TEXT NOT NULL DEFAULT ''",
            },
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
            password_changed_at=float(row["password_changed_at"] or 0.0),
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
                "INSERT INTO users(username, password_hash, role, created_at,"
                " password_changed_at) VALUES(?, ?, ?, ?, ?)",
                (username, password_hash, role, now, now),
            )
            user_id = int(cur.lastrowid or 0)
        return User(
            id=user_id,
            username=username,
            role=role,
            disabled=False,
            created_at=now,
            password_changed_at=now,
        )

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
        now = time.time()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone()
            cur = self._conn.execute(
                "UPDATE users SET password_hash = ?, password_changed_at = ?"
                " WHERE username = ?",
                (password_hash, now, username),
            )
        self._invalidate_user(int(row["id"]) if row else None)
        return cur.rowcount > 0

    def _guarded_admin_mutation(
        self,
        username: str,
        *,
        role: str | None = None,
        disabled: bool | None = None,
        delete: bool = False,
    ) -> bool:
        """Apply one mutation and its last-admin check in one write transaction."""
        user_id: int | None = None
        changed = False
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT id, role, disabled FROM users WHERE username = ?",
                    (username,),
                ).fetchone()
                if row is None:
                    self._conn.rollback()
                else:
                    user_id = int(row["id"])
                    removing_admin = (
                        str(row["role"]) == "admin"
                        and int(row["disabled"]) == 0
                        and ((role == "user") or bool(disabled) or delete)
                    )
                    if removing_admin:
                        count = self._conn.execute(
                            "SELECT COUNT(*) FROM users"
                            " WHERE role = 'admin' AND disabled = 0"
                        ).fetchone()[0]
                        if int(count) <= 1:
                            raise LastAdminError("至少保留一个管理员")
                    if delete:
                        cur = self._conn.execute(
                            "DELETE FROM users WHERE username = ?", (username,)
                        )
                    elif role is not None:
                        cur = self._conn.execute(
                            "UPDATE users SET role = ? WHERE username = ?",
                            (role, username),
                        )
                    else:
                        cur = self._conn.execute(
                            "UPDATE users SET disabled = ? WHERE username = ?",
                            (1 if disabled else 0, username),
                        )
                    changed = cur.rowcount > 0
                    self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise
        self._invalidate_user(user_id)
        return changed

    def user_set_role_guarded(self, username: str, role: str) -> bool:
        return self._guarded_admin_mutation(username, role=role)

    def user_set_disabled_guarded(self, username: str, disabled: bool) -> bool:
        return self._guarded_admin_mutation(username, disabled=disabled)

    def user_delete_guarded(self, username: str) -> bool:
        return self._guarded_admin_mutation(username, delete=True)

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
            with self._lock:
                row = self._conn.execute("PRAGMA data_version").fetchone()
            version = int(row[0]) if row is not None else 0
            if self._auth_data_version is None:
                self._auth_data_version = version
            elif version != self._auth_data_version:
                # A different process committed (CLI password/role/disable).
                # Drop every cached identity before trusting a hit.
                self._auth_data_version = version
                cache.clear()
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
                # The *window* is what must stay constant. Anchoring it at the
                # original ``created_at`` while pushing ``expires_at`` forward
                # made every renewal widen the window itself (100 -> 180 ->
                # 340 -> ...): ``session_ttl`` silently stopped meaning "this
                # token is good for at most N seconds". Move ``created_at``
                # along with ``expires_at`` so ``expires_at - created_at`` is
                # invariant across any number of renewals.
                ttl = expires_at - float(row["created_at"])
                if ttl > 0:
                    expires_at = now + ttl
                    sep = ", " if updates else ""
                    updates = f"{updates}{sep}expires_at = ?, created_at = ?"
                    params.append(expires_at)
                    params.append(now)
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
        generation: str | None = None,
        snapshot_mode: str = "auto",
        shell_integration: bool = True,
    ) -> None:
        now = time.time()
        argv_json = json.dumps(argv) if argv is not None else None
        env_json = json.dumps(env) if env is not None else None
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO term_sessions"
                "(id, name, owner_id, backend, command, argv, env, cwd, idle_timeout,"
                " max_life, status, instance_id, created_at, last_active, generation,"
                " snapshot_mode, shell_integration)"
                " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET"
                " name=excluded.name,"
                # A command can exit before the create request finishes its
                # INSERT. The finalizer then writes the real terminal row and
                # the late create upsert must not reset it to running -- the
                # maintenance sweep would file a clean exit as a crash and
                # lose the exit code.
                " status=CASE"
                "  WHEN excluded.status='running' AND term_sessions.status<>'running'"
                "  THEN term_sessions.status ELSE excluded.status END,"
                " instance_id=excluded.instance_id, last_active=excluded.last_active,"
                " generation=excluded.generation, snapshot_mode=excluded.snapshot_mode,"
                " shell_integration=excluded.shell_integration",
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
                    generation,
                    snapshot_mode,
                    int(bool(shell_integration)),
                ),
            )

    def term_session_set_name(self, sid: str, name: str) -> None:
        """Update only the display name.

        A rename used to call :meth:`term_session_upsert` with defaults for
        every other column; its ``ON CONFLICT`` clause then rewrote
        ``generation``/``snapshot_mode``/``shell_integration`` with those
        defaults. Renaming a tmux session could erase the generation a resume
        depends on and silently reset its snapshot policy.
        """
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE term_sessions SET name = ?, last_active = ? WHERE id = ?",
                (name, time.time(), sid),
            )

    def term_session_set_seq(self, sid: str, last_seq: int) -> None:
        """Persist the last output offset this session has produced.

        It is not used for resume across a restart -- ``generation`` changes
        then, so a client must take a snapshot -- but it makes the row an
        honest record of how much output a session generated and lets an
        operator compare it with the scrollback cap.
        """
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE term_sessions SET last_seq = ? WHERE id = ?",
                (max(0, int(last_seq)), sid),
            )

    def term_session_set_status(
        self,
        sid: str,
        status: str,
        reason: str | None = None,
        exit_code: int | None = None,
    ) -> int:
        """Record how a session ended; return the number of rows updated.

        ``reason`` is the human half of ``status``: "killed" says *what*
        happened, "被管理员终止" says *why*. Without it the history list showed
        a bare enum value and left the user to guess. ``exit_code`` is the
        process's own answer and cannot be recovered after the row is written,
        so it is stored here rather than recomputed later.
        """
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE term_sessions SET status = ?, end_reason = ?, exit_code = ?,"
                " last_active = ? WHERE id = ?",
                (status, reason, exit_code, time.time(), sid),
            )
            return int(cur.rowcount)

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
                f"UPDATE term_sessions SET status = 'stopped',"
                f" end_reason = coalesce(end_reason, '服务退出或实例崩溃')"
                f" WHERE {' AND '.join(clauses)}",
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
        self,
        *,
        owner_id: int | None = None,
        limit: int = 200,
        before_ts: float | None = None,
        before_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Finished session rows (newest first).

        These rows were always written -- ``status``, ``created_at`` and the
        retention policy all exist -- but nothing ever read them, so a session
        that ended left no trace anywhere in the product. ``owner_id=None``
        means "everyone" (admin view).

        ``before_ts``/``before_id`` are the cursor pair: rows are ordered by
        ``last_active DESC, id DESC`` and the next page is strictly older than
        the last row the caller saw.
        """
        clauses = ["status != 'running'"]
        params: list[Any] = []
        if owner_id is not None:
            clauses.append("owner_id = ?")
            params.append(owner_id)
        if before_ts is not None and before_id is not None:
            clauses.append("(last_active < ? OR (last_active = ? AND id < ?))")
            params.extend([before_ts, before_ts, before_id])
        params.append(max(1, min(limit, 1000)))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM term_sessions WHERE {' AND '.join(clauses)}"
                " ORDER BY last_active DESC, id DESC LIMIT ?",
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
        with self._lock, self._conn:
            # Take the write lock *before* reading the previous hash: two
            # instances sharing a data directory (rolling restart) would
            # otherwise read the same head and fork the chain.
            try:
                self._conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                if "within a transaction" not in str(exc).lower():
                    raise
            last = self._conn.execute(
                "SELECT hash FROM audit_logs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            prev = str(last["hash"]) if last is not None and last["hash"] else GENESIS_HASH
            for event in events:
                ts = float(event.get("ts", now))
                event["ts"] = ts
                user_id = event.get("user_id")
                sid = event.get("term_session_id")
                payload = event.get("payload")
                ip = event.get("ip")
                request_id = event.get("request_id")
                event_name = str(event["event"])
                digest = audit_digest(
                    prev_hash=prev,
                    ts=ts,
                    user_id=user_id,
                    term_session_id=sid,
                    event=event_name,
                    payload=payload,
                    ip=ip,
                    request_id=request_id,
                )
                rows.append(
                    (user_id, sid, event_name, payload, ip, request_id, ts, prev, digest)
                )
                event["prev_hash"] = prev
                event["hash"] = digest
                prev = digest
            self._conn.executemany(
                "INSERT INTO audit_logs"
                "(user_id, term_session_id, event, payload, ip, request_id, ts, prev_hash, hash)"
                " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        before_id: int | None = None,
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
        if before_id is not None:
            clauses.append("id < ?")
            params.append(int(before_id))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM audit_logs{where} ORDER BY id DESC LIMIT ? OFFSET ?",
                (*params, limit, max(offset, 0)),
            ).fetchall()
        return [dict(row) for row in rows]

    def verify_audit(self) -> dict[str, Any]:
        """Recompute the audit hash chain and report the first broken link.

        Retention deletes old rows, so the chain may legitimately start at a
        non-genesis ``prev_hash``. That first row is treated as an anchor and
        reported as ``truncated``; every subsequent row must link exactly.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, user_id, term_session_id, event, payload, ip,"
                " request_id, ts, prev_hash, hash FROM audit_logs ORDER BY id"
            ).fetchall()
        if not rows:
            return {"ok": True, "checked": 0, "truncated": False, "broken_at": None}
        hashed = [row for row in rows if str(row["hash"] or "")]
        unhashed = len(rows) - len(hashed)
        if not hashed:
            # A database upgraded from 0.1.x may contain only legacy rows:
            # there is nothing to verify yet, and saying "broken" would be a
            # false accusation against a valid migration.
            return {
                "ok": True,
                "checked": 0,
                "truncated": True,
                "unhashed": unhashed,
                "broken_at": None,
            }
        truncated = unhashed > 0 or str(hashed[0]["prev_hash"] or "") != GENESIS_HASH
        expected_prev = str(hashed[0]["prev_hash"] or GENESIS_HASH)
        for index, row in enumerate(hashed):
            actual_prev = str(row["prev_hash"] or "")
            if index > 0 and actual_prev != expected_prev:
                return {
                    "ok": False,
                    "checked": index,
                    "truncated": truncated,
                    "broken_at": int(row["id"]),
                    "reason": "prev_hash 与上一行不连续",
                }
            digest = audit_digest(
                prev_hash=actual_prev,
                ts=float(row["ts"]),
                user_id=row["user_id"],
                term_session_id=row["term_session_id"],
                event=str(row["event"]),
                payload=row["payload"],
                ip=row["ip"],
                request_id=row["request_id"],
            )
            if digest != str(row["hash"] or ""):
                return {
                    "ok": False,
                    "checked": index,
                    "truncated": truncated,
                    "broken_at": int(row["id"]),
                    "reason": "hash 与记录内容不匹配",
                }
            expected_prev = digest
        return {
            "ok": True,
            "checked": len(hashed),
            "truncated": truncated,
            "unhashed": unhashed,
            "broken_at": None,
        }
