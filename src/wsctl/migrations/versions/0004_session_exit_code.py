"""Persist the child's exit code on the session row.

0.2.0's first v6 draft stored only ``status`` and ``end_reason``. The live
``TermSession`` knew the exit code, but the history endpoint could only return
``None`` for a finished session -- and the moment the object was gone, the
number was gone with it. A process's own exit code is part of "what happened",
so it gets a column while the value is still available.

Revision ID: 0004_session_exit_code
Revises: 0003_v6_complete
Create Date: 2026-11-05
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import inspect

revision: str = "0004_session_exit_code"
down_revision: str | None = "0003_v6_complete"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "exit_code" not in _columns("term_sessions"):
        op.get_bind().exec_driver_sql(
            "ALTER TABLE term_sessions ADD COLUMN exit_code INTEGER"
        )


def downgrade() -> None:
    if "exit_code" in _columns("term_sessions"):
        op.drop_column("term_sessions", "exit_code")
