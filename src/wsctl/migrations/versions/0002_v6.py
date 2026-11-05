"""v6: protocol state, multi-share, API keys, layouts, command blocks, push,
and an audit hash chain.

Revision ID: 0002_v6
Revises: 0001_v5_baseline
Create Date: 2026-11-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import inspect

revision: str = "0002_v6"
down_revision: str | None = "0001_v5_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_COLUMNS: dict[str, dict[str, str]] = {
    "term_sessions": {
        "generation": "TEXT",
        "snapshot_mode": "TEXT NOT NULL DEFAULT 'auto'",
        "shell_integration": "INTEGER NOT NULL DEFAULT 1",
    },
    "audit_logs": {
        "request_id": "TEXT",
        "prev_hash": "TEXT NOT NULL DEFAULT ''",
        "hash": "TEXT NOT NULL DEFAULT ''",
    },
}

_NEW_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS shares (
        id              {pk},
        term_session_id TEXT NOT NULL,
        token_hash      TEXT NOT NULL UNIQUE,
        token_hint      TEXT NOT NULL DEFAULT '',
        scope           TEXT NOT NULL DEFAULT 'read',
        label           TEXT NOT NULL DEFAULT '',
        created_by      INTEGER,
        created_at      REAL NOT NULL,
        expires_at      REAL,
        last_used       REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS api_keys (
        id         {pk},
        user_id    INTEGER NOT NULL,
        name       TEXT NOT NULL DEFAULT '',
        token_hash TEXT NOT NULL UNIQUE,
        scopes     TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        expires_at REAL,
        last_used  REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS layouts (
        id          {pk},
        user_id     INTEGER NOT NULL,
        name        TEXT NOT NULL,
        layout_json TEXT NOT NULL DEFAULT '{{}}',
        updated_at  REAL NOT NULL,
        UNIQUE(user_id, name)
    )
    """,
    """
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
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS push_subscriptions (
        id         {pk},
        user_id    INTEGER NOT NULL,
        endpoint   TEXT NOT NULL UNIQUE,
        keys_json  TEXT NOT NULL DEFAULT '{{}}',
        created_at REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_shares_session ON shares(term_session_id)",
    "CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_layouts_user ON layouts(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_command_blocks_session ON command_blocks(term_session_id)",
)


def _columns(table: str) -> set[str]:
    inspector = inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    connection = op.get_bind()
    pk = (
        "INTEGER PRIMARY KEY AUTOINCREMENT"
        if connection.dialect.name == "sqlite"
        else "SERIAL PRIMARY KEY"
    )
    for statement in _NEW_TABLES:
        connection.exec_driver_sql(statement.strip().format(pk=pk))
    for table, columns in _NEW_COLUMNS.items():
        existing = _columns(table)
        for name, declaration in columns.items():
            if name in existing:
                continue
            connection.exec_driver_sql(
                f"ALTER TABLE {table} ADD COLUMN {name} {declaration}"
            )
    connection.exec_driver_sql(
        "INSERT INTO meta(key, value) VALUES('schema_version', '6')"
        " ON CONFLICT(key) DO NOTHING"
    )


def downgrade() -> None:
    connection = op.get_bind()
    for table in (
        "push_subscriptions",
        "command_blocks",
        "layouts",
        "api_keys",
        "shares",
    ):
        connection.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
    for table, columns in _NEW_COLUMNS.items():
        existing = _columns(table)
        for name in columns:
            if name in existing:
                op.drop_column(table, name)
