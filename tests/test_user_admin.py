"""User mutations share one set of invariants across the API and the CLI."""

from __future__ import annotations

from pathlib import Path

import pytest

from wsctl.core import user_admin
from wsctl.core.store import Store, User


def make_store(tmp_path: Path) -> Store:
    return Store(tmp_path / "users.db")


def _actor(username: str = "root") -> User:
    return User(id=1, username=username, role="admin", disabled=False, created_at=0.0)


def test_last_admin_cannot_be_disabled_or_deleted(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.user_create("root", "password123", role="admin")
        with pytest.raises(user_admin.UserAdminError):
            user_admin.set_disabled(store, "root", True)
        with pytest.raises(user_admin.UserAdminError):
            user_admin.delete(store, "root")
        with pytest.raises(user_admin.UserAdminError):
            user_admin.set_role(store, "root", "user")
        assert store.user_get("root") is not None
        assert store.user_get("root").role == "admin"  # type: ignore[union-attr]
    finally:
        store.close()


def test_demotion_allowed_with_a_second_admin(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.user_create("root", "password123", role="admin")
        store.user_create("other", "password123", role="admin")
        user_admin.set_role(store, "root", "user")
        assert store.user_get("root").role == "user"  # type: ignore[union-attr]
    finally:
        store.close()


def test_cannot_disable_or_delete_self(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.user_create("root", "password123", role="admin")
        store.user_create("other", "password123", role="admin")
        actor = _actor("root")
        with pytest.raises(user_admin.UserAdminError):
            user_admin.set_disabled(store, "root", True, actor=actor)
        with pytest.raises(user_admin.UserAdminError):
            user_admin.delete(store, "root", actor=actor)
    finally:
        store.close()


def test_password_change_revokes_sessions(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        user = store.user_create("bob", "password123")
        token = store.create_auth_session(user.id, ttl=3600)
        user_admin.set_password(store, "bob", "newpw1234")
        assert store.resolve_auth_session(token) is None
    finally:
        store.close()


def test_disable_revokes_sessions(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.user_create("root", "password123", role="admin")
        bob = store.user_create("bob", "password123")
        token = store.create_auth_session(bob.id, ttl=3600)
        user_admin.set_disabled(store, "bob", True)
        assert store.resolve_auth_session(token) is None
    finally:
        store.close()


def test_create_rejects_duplicates_and_bad_roles(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        user_admin.create(store, "carol", "password123")
        with pytest.raises(user_admin.UserAdminError) as dup:
            user_admin.create(store, "carol", "password123")
        assert dup.value.status == 409
        with pytest.raises(user_admin.UserAdminError):
            user_admin.create(store, "dave", "password123", role="superuser")
    finally:
        store.close()


def test_missing_user_reports_404(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        with pytest.raises(user_admin.UserAdminError) as err:
            user_admin.delete(store, "ghost")
        assert err.value.status == 404
    finally:
        store.close()
