"""v5 baseline: the schema shipped with 0.1.30.

Revision ID: 0001_v5_baseline
Revises:
Create Date: 2026-11-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_v5_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS users (
        id            {pk},
        username      TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role          TEXT NOT NULL DEFAULT 'user',
        totp_secret   TEXT,
        disabled      INTEGER NOT NULL DEFAULT 0,
        created_at    REAL NOT NULL,
        password_changed_at REAL NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS auth_sessions (
        id         {pk},
        user_id    INTEGER NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        ip         TEXT,
        user_agent TEXT,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        last_seen  REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS term_sessions (
        id             TEXT PRIMARY KEY,
        name           TEXT NOT NULL,
        owner_id       INTEGER,
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
        end_reason     TEXT,
        created_at     REAL NOT NULL,
        last_active    REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS instances (
        id         TEXT PRIMARY KEY,
        pid        INTEGER,
        host       TEXT,
        started_at REAL NOT NULL,
        heartbeat  REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_logs (
        id              {pk},
        user_id         INTEGER,
        term_session_id TEXT,
        event           TEXT NOT NULL,
        payload         TEXT,
        ip              TEXT,
        ts              REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_auth_sessions_token ON auth_sessions(token_hash)",
    "CREATE INDEX IF NOT EXISTS idx_audit_logs_ts ON audit_logs(ts)",
    "CREATE INDEX IF NOT EXISTS idx_term_sessions_status ON term_sessions(status)",
)


def upgrade() -> None:
    connection = op.get_bind()
    pk = (
        "INTEGER PRIMARY KEY AUTOINCREMENT"
        if connection.dialect.name == "sqlite"
        else "SERIAL PRIMARY KEY"
    )
    for statement in _STATEMENTS:
        connection.exec_driver_sql(statement.strip().format(pk=pk))


def downgrade() -> None:
    connection = op.get_bind()
    for table in (
        "audit_logs",
        "instances",
        "term_sessions",
        "auth_sessions",
        "users",
        "settings",
        "meta",
    ):
        connection.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
