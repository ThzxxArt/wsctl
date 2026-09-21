from __future__ import annotations

import json
from pathlib import Path

import pyotp
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from wsctl.core.config import load_settings
from wsctl.core.session import SessionManager
from wsctl.core.store import Store
from wsctl.server.app import create_app

ADMIN = ("admin", "adminpw")
BOB = ("bob", "bobpw")


def build_app(tmp_path: Path, **overrides: object) -> object:
    settings = load_settings(
        data_dir=tmp_path,
        auth_required=True,
        default_shell="/bin/sh",
        cookie_secure=False,
        **overrides,
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


# -- M3: security hardening -------------------------------------------


def test_login_rate_limited(tmp_path: Path) -> None:
    app = build_app(tmp_path, login_rate_limit=3, login_rate_window=300)
    with TestClient(app) as client:
        for _ in range(3):
            assert client.post(
                "/api/login", json={"username": "admin", "password": "bad"}
            ).status_code == 401
        blocked = client.post("/api/login", json={"username": "admin", "password": "bad"})
        assert blocked.status_code == 429
        assert "retry-after" in {k.lower() for k in blocked.headers}
        # correct credentials are refused while the key is blocked
        assert client.post(
            "/api/login", json={"username": "admin", "password": "adminpw"}
        ).status_code == 429


def test_successful_login_resets_limit(tmp_path: Path) -> None:
    app = build_app(tmp_path, login_rate_limit=3)
    with TestClient(app) as client:
        for _ in range(2):
            client.post("/api/login", json={"username": "admin", "password": "bad"})
        assert client.post(
            "/api/login", json={"username": "admin", "password": "adminpw"}
        ).status_code == 200
        for _ in range(2):
            assert client.post(
                "/api/login", json={"username": "admin", "password": "bad"}
            ).status_code == 401


def test_login_failure_is_audited(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        client.post("/api/login", json={"username": "admin", "password": "bad"})
        login(client, ADMIN)
        events = [e["event"] for e in client.get("/api/audit").json()]
        assert "login_failed" in events
        assert "login" in events


def test_totp_login_flow(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        secret = pyotp.random_base32()
        app.state.store.user_set_totp("admin", secret)

        # no code provided
        assert client.post(
            "/api/login", json={"username": "admin", "password": "adminpw"}
        ).status_code == 401
        # wrong code
        assert client.post(
            "/api/login", json={"username": "admin", "password": "adminpw", "totp": "000000"}
        ).status_code == 401
        # valid code
        code = pyotp.TOTP(secret).now()
        assert client.post(
            "/api/login", json={"username": "admin", "password": "adminpw", "totp": code}
        ).status_code == 200


def test_security_headers(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        r = client.get("/healthz")
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert "content-security-policy" in {k.lower() for k in r.headers}


def test_ip_allowlist_blocks_unknown_client(tmp_path: Path) -> None:
    app = build_app(tmp_path, allowed_ips=["10.0.0.0/8"])
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 403


def test_audit_endpoint_admin_only(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, BOB)
        assert client.get("/api/audit").status_code == 403
        client.post("/api/logout")

        login(client, ADMIN)
        events = {e["event"] for e in client.get("/api/audit").json()}
        assert "login" in events
