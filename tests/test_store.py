from __future__ import annotations

from pathlib import Path

from wsctl.core.store import Store


def make_store(tmp_path: Path) -> Store:
    return Store(tmp_path / "test.db")


def test_user_lifecycle(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        assert store.user_count() == 0
        user = store.user_create("alice", "s3cret", role="admin")
        assert user.username == "alice"
        assert store.user_count() == 1
        assert store.user_get("alice") is not None
        assert store.user_set_password("alice", "newpass")
        assert store.user_set_role("alice", "user")
        assert store.user_get("alice") is not None
        assert store.user_get("alice").role == "user"  # type: ignore[union-attr]
        assert store.user_delete("alice")
        assert store.user_count() == 0
    finally:
        store.close()


def test_authenticate(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.user_create("bob", "hunter2")
        assert store.user_authenticate("bob", "hunter2") is not None
        assert store.user_authenticate("bob", "wrong") is None
        assert store.user_authenticate("nobody", "hunter2") is None
    finally:
        store.close()


def test_duplicate_username(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.user_create("carol", "pw")
        import sqlite3

        import pytest

        with pytest.raises(sqlite3.IntegrityError):
            store.user_create("carol", "pw")
    finally:
        store.close()


def test_auth_session_lifecycle(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        user = store.user_create("dave", "pw")
        token = store.create_auth_session(user.id, ttl=3600)
        resolved = store.resolve_auth_session(token)
        assert resolved is not None
        assert resolved.id == user.id
        store.delete_auth_session(token)
        assert store.resolve_auth_session(token) is None
    finally:
        store.close()


def test_expired_session(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        user = store.user_create("erin", "pw")
        token = store.create_auth_session(user.id, ttl=-1)
        assert store.resolve_auth_session(token) is None
    finally:
        store.close()


def test_audit_roundtrip(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.log_event("login", payload="ok")
        rows = store.recent_audit()
        assert rows and rows[0]["event"] == "login"
    finally:
        store.close()


def test_totp_secret_lifecycle(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.user_create("frank", "pw")
        assert store.user_totp_secret("frank") is None
        assert store.user_set_totp("frank", "ABCDEF")
        assert store.user_totp_secret("frank") == "ABCDEF"
        assert store.user_clear_totp("frank")
        assert store.user_totp_secret("frank") is None
        assert not store.user_set_totp("nobody", "X")
    finally:
        store.close()
