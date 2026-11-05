from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from wsctl.core.config import load_settings
from wsctl.core.session import SessionManager
from wsctl.core.store import Store
from wsctl.server.app import create_app

ADMIN = ("admin", "adminpw123")


def build_app(tmp_path: Path):
    settings = load_settings(
        data_dir=tmp_path,
        auth_required=True,
        default_shell="/bin/sh",
        cookie_secure=False,
    )
    store = Store(tmp_path / "test.db")
    store.user_create(*ADMIN, role="admin")
    return create_app(settings, store=store, manager=SessionManager())


def login(client: TestClient) -> None:
    response = client.post("/api/v2/login", json={"username": ADMIN[0], "password": ADMIN[1]})
    assert response.status_code == 200, response.text


def create_key(client: TestClient, scopes: list[str]) -> str:
    response = client.post("/api/v2/api-keys", json={"name": "test", "scopes": scopes})
    assert response.status_code == 201, response.text
    return str(response.json()["token"])


def test_api_key_in_a_cookie_is_not_a_login(tmp_path: Path) -> None:
    """An API key must come from Authorization, where its scopes are visible.

    ``current_user`` used to accept ``wsctl_...`` from the cookie too, while
    the scope middleware only inspected the header -- turning a read-only key
    into the user's full permissions.
    """
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        token = create_key(client, ["read"])
        client.cookies.clear()
        client.cookies.set("wsctl_session", token)
        assert client.post("/api/v2/sessions", json={}).status_code == 401

        # The same key works as a bearer credential, but only with its scope.
        denied = client.post(
            "/api/v2/sessions",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert denied.status_code == 403, denied.text


def test_duplicate_authorization_headers_are_rejected(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        token = create_key(client, ["read"])
        client.cookies.clear()
        response = client.post(
            "/api/v2/sessions",
            json={},
            headers=[
                ("Authorization", f"Bearer {token}"),
                ("Authorization", "Bearer wsctl_bogus"),
            ],
        )
        assert response.status_code in {400, 401, 403}, response.text
