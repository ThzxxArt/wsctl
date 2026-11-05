from __future__ import annotations

import sqlite3
from pathlib import Path

from wsctl.core.migrations import (
    current_revision,
    downgrade_database,
    head_revision,
    upgrade_database,
)
from wsctl.core.store import SCHEMA_VERSION, Store


def test_alembic_creates_the_full_schema_on_an_empty_database(tmp_path: Path) -> None:
    db = tmp_path / "fresh.db"
    url = f"sqlite:///{db}"
    assert upgrade_database(url) == head_revision(url) == "0004_session_exit_code"
    connection = sqlite3.connect(db)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    connection.close()
    for expected in {
        "users",
        "auth_sessions",
        "term_sessions",
        "audit_logs",
        "shares",
        "api_keys",
        "layouts",
        "command_blocks",
        "push_subscriptions",
        "recordings",
        "idempotency_keys",
        "alembic_version",
    }:
        assert expected in tables, expected


def test_upgrade_is_idempotent_on_a_store_created_database(tmp_path: Path) -> None:
    db = tmp_path / "store.db"
    store = Store(db)
    store.close()
    head = head_revision(f"sqlite:///{db}")
    assert upgrade_database(f"sqlite:///{db}") == head
    assert upgrade_database(f"sqlite:///{db}") == head
    assert current_revision(f"sqlite:///{db}") == head


def test_downgrade_to_base_then_upgrade_again(tmp_path: Path) -> None:
    db = tmp_path / "roundtrip.db"
    url = f"sqlite:///{db}"
    upgrade_database(url)
    assert downgrade_database(url) == ""
    assert upgrade_database(url) == head_revision(url)


def test_store_schema_version_matches_the_migration_head(tmp_path: Path) -> None:
    store = Store(tmp_path / "wsctl.db")
    try:
        assert SCHEMA_VERSION == 6
        assert current_revision(f"sqlite:///{tmp_path / 'wsctl.db'}") in (
            "",
            "0004_session_exit_code",
        )
    finally:
        store.close()


def test_v5_share_token_is_imported_into_the_shares_table(tmp_path: Path) -> None:
    db = tmp_path / "legacy-share.db"
    url = f"sqlite:///{db}"
    upgrade_database(url)
    downgrade_database(url, "0001_v5_baseline")
    connection = sqlite3.connect(db)
    connection.execute(
        "INSERT INTO term_sessions"
        "(id, name, owner_id, backend, status, share_token, share_expires,"
        " share_writable, created_at, last_active)"
        " VALUES('legacy01', 'old', NULL, 'local', 'running', 'old-token', NULL, 0, 1.0, 1.0)"
    )
    connection.commit()
    connection.close()
    upgrade_database(url)
    connection = sqlite3.connect(db)
    row = connection.execute(
        "SELECT scope, label FROM shares WHERE term_session_id = 'legacy01'"
    ).fetchone()
    connection.close()
    assert row is not None
    assert row[0] == "read"
    assert "0.1.x" in row[1]


def test_upgrade_takes_a_backup_before_touching_a_v5_database(tmp_path: Path) -> None:
    db = tmp_path / "backup-me.db"
    url = f"sqlite:///{db}"
    upgrade_database(url)
    downgrade_database(url, "0001_v5_baseline")
    upgrade_database(url)
    backups = list(tmp_path.glob("backup-me.db.pre-*.bak"))
    assert backups, "a pending migration must produce a consistent snapshot"


def test_last_seq_column_exists_after_upgrade(tmp_path: Path) -> None:
    db = tmp_path / "columns.db"
    url = f"sqlite:///{db}"
    upgrade_database(url)
    connection = sqlite3.connect(db)
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(term_sessions)").fetchall()
    }
    connection.close()
    assert "last_seq" in columns


def test_exit_code_column_exists_after_upgrade(tmp_path: Path) -> None:
    db = tmp_path / "exit-code.db"
    url = f"sqlite:///{db}"
    upgrade_database(url)
    connection = sqlite3.connect(db)
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(term_sessions)").fetchall()
    }
    connection.close()
    assert "exit_code" in columns
