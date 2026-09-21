from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from wsctl.core.config import load_settings
from wsctl.core.session import SessionManager
from wsctl.core.store import Store
from wsctl.server.app import create_app

ADMIN = ("admin", "adminpw")
BOB = ("bob", "bobpw")


def build_app(tmp_path: Path) -> object:
    settings = load_settings(
        data_dir=tmp_path, auth_required=True, default_shell="/bin/sh", cookie_secure=False
    )
    store = Store(tmp_path / "test.db")
    store.user_create(*ADMIN, role="admin")
    store.user_create(*BOB, role="user")
    return create_app(settings, store=store, manager=SessionManager())


def login(client: TestClient, creds: tuple[str, str]) -> None:
    r = client.post("/api/login", json={"username": creds[0], "password": creds[1]})
    assert r.status_code == 200, r.text


def test_login_required(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        assert client.get("/api/me").status_code == 401
        login(client, ADMIN)
        assert client.get("/api/me").json()["role"] == "admin"


def test_bad_credentials(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        r = client.post("/api/login", json={"username": "admin", "password": "nope"})
        assert r.status_code == 401


def test_session_visibility_is_scoped(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        r = client.post("/api/sessions", json={})
        assert r.status_code == 201, r.text
        admin_session = r.json()
        assert admin_session["owner_id"] == 1

        # admin sees the session
        assert len(client.get("/api/sessions").json()) == 1

        # bob sees none of admin's sessions
        client.post("/api/logout")
        login(client, BOB)
        assert client.get("/api/sessions").json() == []


def test_non_owner_cannot_kill(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        client.post("/api/logout")

        login(client, BOB)
        assert client.delete(f"/api/sessions/{sid}").status_code == 403


def test_admin_can_kill_any(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        assert client.delete(f"/api/sessions/{sid}").status_code == 200


def test_users_endpoint_admin_only(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, BOB)
        assert client.get("/api/users").status_code == 403
        client.post("/api/logout")

        login(client, ADMIN)
        usernames = {u["username"] for u in client.get("/api/users").json()}
        assert usernames == {"admin", "bob"}


def test_ws_rejects_foreign_session(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        client.post("/api/logout")

        login(client, BOB)
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "attach", "session": sid, "cols": 80, "rows": 24}))
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "error"
            assert "not authorized" in msg["msg"]


def test_ws_unauthenticated_rejected(tmp_path: Path) -> None:
    with (
        TestClient(build_app(tmp_path)) as client,
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/ws") as ws,
    ):
        ws.receive_text()
