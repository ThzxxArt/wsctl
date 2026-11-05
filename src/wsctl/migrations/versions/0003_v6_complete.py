"""v6 completion: recordings, idempotency, last_seq and legacy share import.

This revision exists because 0.2.0 is still unreleased and the first v6 draft
missing three pieces of the roadmap's schema:

* ``recordings`` -- casts were files with no session/owner/status metadata;
* ``idempotency_keys`` -- ``Idempotency-Key`` needs durable replay across the
  rolling-restart window;
* ``term_sessions.last_seq`` -- the last acknowledged output offset.

It also imports 0.1.x's single ``term_sessions.share_token`` into the new
``shares`` table so a share link survives the upgrade without waiting for the
old session to be attached to.

Revision ID: 0003_v6_complete
Revises: 0002_v6
Create Date: 2026-11-04
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from alembic import op
from sqlalchemy import inspect

revision: str = "0003_v6_complete"
down_revision: str | None = "0002_v6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS recordings (
        id              {pk},
        term_session_id TEXT NOT NULL,
        owner_id        INTEGER,
        path            TEXT NOT NULL UNIQUE,
        size            INTEGER NOT NULL DEFAULT 0,
        started_at      REAL,
        ended_at        REAL,
        status          TEXT NOT NULL DEFAULT 'recording'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS idempotency_keys (
        key          TEXT PRIMARY KEY,
        method       TEXT NOT NULL,
        path         TEXT NOT NULL,
        request_hash TEXT NOT NULL DEFAULT '',
        status       INTEGER NOT NULL DEFAULT 200,
        body_json    TEXT NOT NULL DEFAULT '{{}}',
        created_at   REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_recordings_session ON recordings(term_session_id)",
    "CREATE INDEX IF NOT EXISTS idx_recordings_owner ON recordings(owner_id)",
    "CREATE INDEX IF NOT EXISTS idx_idempotency_created ON idempotency_keys(created_at)",
    # The roadmap names the table ``audit_events``. The physical table stays
    # ``audit_logs`` for 0.1.x compatibility; this read-only view gives the
    # documented name to tools without breaking the live writer.
    "CREATE VIEW IF NOT EXISTS audit_events AS SELECT * FROM audit_logs",
)


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    connection = op.get_bind()
    pk = (
        "INTEGER PRIMARY KEY AUTOINCREMENT"
        if connection.dialect.name == "sqlite"
        else "SERIAL PRIMARY KEY"
    )
    for statement in _NEW_TABLES:
        connection.exec_driver_sql(statement.strip().format(pk=pk))
    if "last_seq" not in _columns("term_sessions"):
        connection.exec_driver_sql(
            "ALTER TABLE term_sessions ADD COLUMN last_seq INTEGER NOT NULL DEFAULT 0"
        )
    # 0.1.x had one share token per session. Import it into the new table so
    # ``/s/<token>`` keeps working for a link created before the upgrade.
    rows = connection.exec_driver_sql(
        "SELECT id, share_token, share_expires, share_writable, owner_id, created_at"
        " FROM term_sessions WHERE share_token IS NOT NULL AND share_token != ''"
    ).fetchall()
    for row in rows:
        sid, token, expires, writable, owner_id, created_at = row
        digest = hashlib.sha256(str(token).encode("utf-8", "surrogatepass")).hexdigest()
        connection.exec_driver_sql(
            "INSERT OR IGNORE INTO shares"
            "(term_session_id, token_hash, token_hint, scope, label, created_by,"
            " created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(sid),
                digest,
                str(token)[:8],
                "write" if writable else "read",
                "从 0.1.x 迁移",
                owner_id,
                float(created_at or 0.0),
                float(expires) if expires is not None else None,
            ),
        )


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql("DROP VIEW IF EXISTS audit_events")
    connection.exec_driver_sql("DROP TABLE IF EXISTS idempotency_keys")
    connection.exec_driver_sql("DROP TABLE IF EXISTS recordings")
    if "last_seq" in _columns("term_sessions"):
        op.drop_column("term_sessions", "last_seq")
