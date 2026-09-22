"""User mutations carrying the invariants the whole product relies on.

Both the HTTP API and the CLI go through these helpers, so the rules hold no
matter which surface is used:

* the last enabled admin can never be demoted, disabled or deleted;
* an operator cannot disable or delete their own account (when an actor is
  known, i.e. over the API);
* changing a password revokes that user's existing login sessions.

Doing this in one place is what stops ``wsctl user disable admin`` from locking
an instance out while the web UI correctly refuses the same operation.
"""

from __future__ import annotations

from .passwords import PasswordPolicyError, validate_password
from .store import Store, User

VALID_ROLES = ("admin", "user")


class UserAdminError(Exception):
    """A user mutation that would violate a product invariant."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _check_password(password: str) -> None:
    """Enforce the shared password policy as a 400-level product error."""
    try:
        validate_password(password)
    except PasswordPolicyError as exc:
        raise UserAdminError(str(exc)) from exc


def _target(store: Store, username: str) -> User:
    user = store.user_get(username)
    if user is None:
        raise UserAdminError(f"用户不存在：{username}", status=404)
    return user


def _guard_last_admin(user: User, store: Store) -> None:
    if user.role == "admin" and store.admin_count() <= 1:
        raise UserAdminError("至少保留一个管理员")


def create(store: Store, username: str, password: str, role: str = "user") -> User:
    if role not in VALID_ROLES:
        raise UserAdminError(f"角色无效：{role}")
    if not username or not username.strip():
        raise UserAdminError("用户名不能为空")
    if len(username) > 64:
        raise UserAdminError("用户名过长（最多 64 个字符）")
    _check_password(password)
    if store.user_get(username) is not None:
        raise UserAdminError(f"用户已存在：{username}", status=409)
    try:
        return store.user_create(username, password, role=role)
    except PasswordPolicyError as exc:  # pragma: no cover - already checked above
        raise UserAdminError(str(exc)) from exc


def set_role(store: Store, username: str, role: str, *, actor: User | None = None) -> None:
    if role not in VALID_ROLES:
        raise UserAdminError(f"角色无效：{role}")
    target = _target(store, username)
    if role == "user" and target.role == "admin":
        _guard_last_admin(target, store)
    store.user_set_role(username, role)


def set_disabled(store: Store, username: str, disabled: bool, *, actor: User | None = None) -> None:
    target = _target(store, username)
    if disabled:
        if actor is not None and actor.username == username:
            raise UserAdminError("不能禁用自己")
        _guard_last_admin(target, store)
    store.user_set_disabled(username, disabled)
    if disabled:
        # A disabled account must lose its live logins immediately.
        store.delete_user_sessions(target.id)


def set_password(store: Store, username: str, password: str, *, actor: User | None = None) -> None:
    target = _target(store, username)
    _check_password(password)
    try:
        store.user_set_password(username, password)
    except PasswordPolicyError as exc:  # pragma: no cover - already checked above
        raise UserAdminError(str(exc)) from exc
    # A password change revokes existing logins for that user.
    store.delete_user_sessions(target.id)


def delete(store: Store, username: str, *, actor: User | None = None) -> None:
    target = _target(store, username)
    if actor is not None and actor.username == username:
        raise UserAdminError("不能删除自己")
    _guard_last_admin(target, store)
    store.user_delete(username)
