"""Multi-instance leases, retention purging and per-instance reconciliation."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from wsctl.core.store import Store


def make_store(tmp_path: Path) -> Store:
    return Store(tmp_path / "instances.db")


def test_instance_lease_lifecycle(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.instance_register("a", pid=123, host="host-a")
        store.instance_register("b")
        assert store.instance_alive_ids(60) == {"a", "b"}
        assert store.instance_dead_ids(60) == []

        # A negative ttl puts the cutoff in the future, so every lease looks dead.
        assert set(store.instance_dead_ids(-1)) == {"a", "b"}
        assert store.instance_alive_ids(-1) == set()

        store.instance_heartbeat("a")
        store.instance_remove("a")
        assert store.instance_alive_ids(60) == {"b"}
    finally:
        store.close()


def test_register_is_idempotent(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.instance_register("a", pid=1, host="h")
        store.instance_register("a", pid=2, host="h2")
        rows = store.term_session_list()
        assert rows == []
        assert store.instance_alive_ids(60) == {"a"}
    finally:
        store.close()


def test_stop_missing_is_instance_scoped(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.term_session_upsert("s1", name="a", owner_id=None, instance_id="A")
        store.term_session_upsert("s2", name="b", owner_id=None, instance_id="B")

        # Instance A reconciles with no in-memory sessions: only its own row stops.
        changed = store.term_session_stop_missing(set(), instance_id="A")
        assert changed == 1
        statuses = {str(r["id"]): str(r["status"]) for r in store.term_session_list()}
        assert statuses["s1"] == "stopped"
        assert statuses["s2"] == "running"
    finally:
        store.close()


def test_stop_missing_without_instance_scope(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.term_session_upsert("s1", name="a", owner_id=None, instance_id="A")
        store.term_session_upsert("s2", name="b", owner_id=None, instance_id="B")
        assert store.term_session_stop_missing({"s1"}) == 1
    finally:
        store.close()


def test_term_sessions_owned_by(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.term_session_upsert("s1", name="a", owner_id=None, instance_id="A")
        store.term_session_upsert("s2", name="b", owner_id=None, instance_id="B")
        owned = store.term_sessions_owned_by({"A"})
        assert [str(r["id"]) for r in owned] == ["s1"]
        assert store.term_sessions_owned_by(set()) == []
    finally:
        store.close()


def test_purge_retention(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.log_event("login")
        assert store.recent_audit()
        assert store.purge_audit(time.time() + 1) == 1
        assert store.recent_audit() == []

        store.term_session_upsert("done", name="done", owner_id=None, status="stopped")
        store.term_session_upsert("live", name="live", owner_id=None, status="running")
        # Running sessions are never purged, only finished ones.
        assert store.purge_term_sessions(time.time() + 1) == 1
        remaining = {str(r["id"]) for r in store.term_session_list()}
        assert remaining == {"live"}
    finally:
        store.close()


def test_instance_id_is_persisted_and_updated(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.term_session_upsert("s", name="n", owner_id=None, instance_id="A")
        assert store.term_session_list()[0]["instance_id"] == "A"
        store.term_session_upsert("s", name="n2", owner_id=None, instance_id="B")
        row = store.term_session_list()[0]
        assert row["instance_id"] == "B"
        assert row["name"] == "n2"
    finally:
        store.close()


def test_migrates_a_v2_database(tmp_path: Path) -> None:
    """Opening a pre-0.1.1 database must add the new column before its index."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            totp_secret TEXT,
            disabled INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        );
        CREATE TABLE term_sessions(
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            owner_id INTEGER,
            backend TEXT NOT NULL DEFAULT 'local',
            command TEXT,
            argv TEXT,
            env TEXT,
            cwd TEXT,
            idle_timeout REAL,
            max_life REAL,
            status TEXT NOT NULL DEFAULT 'running',
            created_at REAL NOT NULL,
            last_active REAL NOT NULL
        );
        """
    )
    conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', '2')")
    conn.commit()
    conn.close()

    store = Store(path)
    try:
        store.term_session_upsert("s", name="n", owner_id=None, instance_id="A")
        assert store.term_session_list()[0]["instance_id"] == "A"
        assert store.instance_alive_ids(60) == set()
    finally:
        store.close()
