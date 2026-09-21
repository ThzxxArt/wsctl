from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pyotp
import pytest
from fastapi.testclient import TestClient

from wsctl.core import ssh, tmux
from wsctl.core.config import load_settings
from wsctl.core.session import SessionManager
from wsctl.core.store import Store
from wsctl.server.app import create_app

ADMIN = ("admin", "adminpw")
BOB = ("bob", "bobpw")


def build_app(
    tmp_path: Path, *, startup_command: str | None = None, **overrides: object
) -> object:
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
    return create_app(
        settings, store=store, manager=SessionManager(), startup_command=startup_command
    )


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


def test_ws_unauthenticated_rejected_with_close_code(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client, client.websocket_connect("/ws") as ws:
        message = ws.receive()
        assert message["type"] == "websocket.close"
        assert message["code"] == 4401


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


# -- M4: file panel & observability -----------------------------------


def test_files_list_download_upload(tmp_path: Path) -> None:
    app = build_app(tmp_path, file_root=tmp_path)
    (tmp_path / "hello.txt").write_text("hi")
    with TestClient(app) as client:
        login(client, ADMIN)

        names = {e["name"] for e in client.get("/api/files").json()["entries"]}
        assert "hello.txt" in names

        download = client.get("/api/files/download", params={"path": "hello.txt"})
        assert download.status_code == 200
        assert download.content == b"hi"

        upload = client.post(
            "/api/files/upload",
            data={"path": ""},
            files={"file": ("up.txt", b"payload")},
        )
        assert upload.status_code == 201
        assert (tmp_path / "up.txt").read_bytes() == b"payload"


def test_files_reject_traversal(tmp_path: Path) -> None:
    app = build_app(tmp_path, file_root=tmp_path)
    with TestClient(app) as client:
        login(client, ADMIN)
        assert client.get("/api/files", params={"path": "../../etc"}).status_code == 400
        assert client.get("/api/files/download", params={"path": "../x"}).status_code == 400


def test_files_reject_oversized_upload(tmp_path: Path) -> None:
    app = build_app(tmp_path, file_root=tmp_path, file_max_upload=4)
    with TestClient(app) as client:
        login(client, ADMIN)
        upload = client.post(
            "/api/files/upload",
            data={"path": ""},
            files={"file": ("big.bin", b"more than four bytes")},
        )
        assert upload.status_code == 413


def test_files_require_auth(tmp_path: Path) -> None:
    app = build_app(tmp_path, file_root=tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/files").status_code == 401


def test_metrics_endpoint(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        text = client.get("/metrics").text
        assert "wsctl_up 1.0" in text
        login(client, ADMIN)
        text = client.get("/metrics").text
        assert 'wsctl_logins_total{result="ok"} 1.0' in text


def test_metrics_can_be_disabled(tmp_path: Path) -> None:
    app = build_app(tmp_path, metrics_enabled=False)
    with TestClient(app) as client:
        assert client.get("/metrics").status_code == 404


# -- M6: gap closure --------------------------------------------------


def test_rename_session(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        r = client.patch(f"/api/sessions/{sid}", json={"name": "renamed"})
        assert r.status_code == 200
        assert r.json()["name"] == "renamed"
        names = {s["id"]: s["name"] for s in client.get("/api/sessions").json()}
        assert names[sid] == "renamed"


def _recv_control(ws: object, wanted: set[str], timeout: float = 5.0) -> dict[str, object] | None:
    import time

    end = time.time() + timeout
    while time.time() < end:
        message = ws.receive()  # type: ignore[attr-defined]
        if message.get("type") == "websocket.close":
            return None
        text = message.get("text")
        if not text:
            continue
        data = json.loads(text)
        if data.get("type") in wanted:
            return data
    return None


def test_session_max_clients_enforced(tmp_path: Path) -> None:
    app = build_app(tmp_path, session_max_clients=1)
    with TestClient(app) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        attach = {"type": "attach", "session": sid, "cols": 80, "rows": 24}

        with client.websocket_connect("/ws") as first:
            first.send_text(json.dumps(attach))
            assert _recv_control(first, {"attached"}) is not None

            with client.websocket_connect("/ws") as second:
                second.send_text(json.dumps(attach))
                msg = _recv_control(second, {"error", "attached"})
                assert msg is not None and msg["type"] == "error"
                assert "client limit" in str(msg["msg"])


def test_startup_command_creates_session(tmp_path: Path) -> None:
    app = build_app(tmp_path, startup_command="sleep 30")
    with TestClient(app) as client:
        login(client, ADMIN)
        sessions = client.get("/api/sessions").json()
        assert sessions
        assert sessions[0]["name"] == "sleep"


def test_invalid_backend_rejected(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        assert client.post("/api/sessions", json={"backend": "nope"}).status_code == 400


@pytest.mark.skipif(not tmux.is_available(), reason="requires tmux")
def test_create_and_kill_tmux_session(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        r = client.post("/api/sessions", json={"backend": "tmux", "name": "tmuxed"})
        assert r.status_code == 201, r.text
        sid = r.json()["id"]
        try:
            assert r.json()["backend"] == "tmux"
            assert client.delete(f"/api/sessions/{sid}").status_code == 200
        finally:
            tmux.kill_session(tmux.session_name(sid))


@pytest.mark.skipif(not tmux.is_available(), reason="requires tmux")
def test_orphan_tmux_sessions_are_reaped(tmp_path: Path) -> None:
    name = tmux.session_name(f"orphan-{os.getpid()}-{int(time.time() * 1000)}")
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", name, "/bin/sh"], check=True, timeout=10
    )
    try:
        assert tmux.has_session(name)
        app = build_app(tmp_path)  # startup should reap the orphan
        with TestClient(app) as client:
            login(client, ADMIN)
        assert not tmux.has_session(name)
    finally:
        tmux.kill_session(name)


@pytest.mark.skipif(not tmux.is_available(), reason="requires tmux")
def test_startup_restores_tmux_session(tmp_path: Path) -> None:
    sid = f"restore-{os.getpid()}-{int(time.time() * 1000)}"
    name = tmux.session_name(sid)
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", name, "/bin/sh"], check=True, timeout=10
    )
    try:
        deadline = time.time() + 5
        while time.time() < deadline and not tmux.has_session(name):
            time.sleep(0.2)
        assert tmux.has_session(name), "tmux session did not become visible"
        settings = load_settings(data_dir=tmp_path, auth_required=True, default_shell="/bin/sh")
        store = Store(tmp_path / "test.db")
        store.user_create("admin", "adminpw", role="admin")
        store.term_session_upsert(sid, name="restored", owner_id=None, backend="tmux")
        app = create_app(settings, store=store, manager=SessionManager())
        with TestClient(app) as client:
            login(client, ADMIN)
            sessions = client.get("/api/sessions").json()
            assert any(s["id"] == sid and s["backend"] == "tmux" for s in sessions)
    finally:
        tmux.kill_session(name)


# -- M8: read-only sharing --------------------------------------------


def test_share_flow(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]

        r = client.post(f"/api/sessions/{sid}/share", json={})
        assert r.status_code == 200, r.text
        assert r.json()["token"]

        assert client.get("/api/sessions").json()[0]["shared"] is True

        qr = client.get(f"/api/sessions/{sid}/qr.svg")
        assert qr.status_code == 200
        assert "svg" in qr.headers["content-type"]
        assert "<svg" in qr.text
        assert 'xmlns="http://www.w3.org/2000/svg"' in qr.text

        assert client.delete(f"/api/sessions/{sid}/share").status_code == 200
        assert client.get(f"/api/sessions/{sid}/qr.svg").status_code == 400


def test_share_requires_owner(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        client.post("/api/logout")

        login(client, BOB)
        assert client.post(f"/api/sessions/{sid}/share", json={}).status_code == 403


def test_share_attach_readonly_without_login(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        token = client.post(f"/api/sessions/{sid}/share", json={}).json()["token"]
        client.cookies.clear()  # simulate an anonymous viewer

        with client.websocket_connect(f"/ws?share={token}") as ws:
            ws.send_text(json.dumps({"type": "attach", "session": sid, "cols": 80, "rows": 24}))
            attached = _recv_control(ws, {"attached", "error"})
            assert attached is not None and attached["type"] == "attached"
            assert attached["writable"] is False

            # input from a read-only client is refused
            ws.send_text(json.dumps({"type": "input", "data": "echo NOPE\r"}))
            err = _recv_control(ws, {"error"})
            assert err is not None and "read-only" in str(err["msg"])


def test_share_attach_writable(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        token = client.post(
            f"/api/sessions/{sid}/share", json={"writable": True}
        ).json()["token"]
        client.cookies.clear()

        with client.websocket_connect(f"/ws?share={token}") as ws:
            ws.send_text(json.dumps({"type": "attach", "session": sid, "cols": 80, "rows": 24}))
            attached = _recv_control(ws, {"attached", "error"})
            assert attached is not None and attached["type"] == "attached"
            assert attached["writable"] is True


def test_ws_origin_mismatch_rejected_with_close_code(tmp_path: Path) -> None:
    with (
        TestClient(build_app(tmp_path)) as client,
        client.websocket_connect("/ws", headers={"origin": "http://evil.example"}) as ws,
    ):
        message = ws.receive()
        assert message["type"] == "websocket.close"
        assert message["code"] == 4403


# -- M9: SSH backend --------------------------------------------------


def test_ssh_backend_requires_config(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        assert client.post("/api/sessions", json={"backend": "ssh"}).status_code == 400


@pytest.mark.skipif(not ssh.ssh_available(), reason="requires ssh client")
def test_ssh_backend_creates_session(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        r = client.post(
            "/api/sessions",
            json={"backend": "ssh", "ssh": {"host": "127.0.0.1", "port": 1}},
        )
        assert r.status_code == 201, r.text
        assert r.json()["backend"] == "ssh"
        assert r.json()["name"].startswith("ssh:")


def test_ssh_backend_rejects_bad_host(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        r = client.post("/api/sessions", json={"backend": "ssh", "ssh": {"host": "-evil"}})
        assert r.status_code == 400


# -- M10: recording ---------------------------------------------------


def test_recording_endpoints(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]

        assert client.post(f"/api/sessions/{sid}/recording/start", json={}).status_code == 200
        assert client.post(f"/api/sessions/{sid}/recording/start", json={}).status_code == 409
        assert client.post(f"/api/sessions/{sid}/recording/stop").status_code == 200

        download = client.get(f"/api/sessions/{sid}/recording")
        assert download.status_code == 200
        assert download.content.startswith(b'{"version": 2')
        assert client.get("/api/recordings").status_code == 200


def test_auto_record(tmp_path: Path) -> None:
    app = build_app(tmp_path, auto_record=True)
    with TestClient(app) as client:
        login(client, ADMIN)
        client.post("/api/sessions", json={})
        assert client.get("/api/sessions").json()[0]["recording"] is True
        assert list((tmp_path / "recordings").glob("*.cast"))


def test_recordings_admin_only(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, BOB)
        assert client.get("/api/recordings").status_code == 403


# -- M17: config hot reload -------------------------------------------


def test_config_reload_endpoint(tmp_path: Path) -> None:
    config = tmp_path / "wsctl" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("max_sessions = 5\n", encoding="utf-8")
    app = build_app(tmp_path, config_path=config)
    with TestClient(app) as client:
        login(client, ADMIN)
        r = client.post("/api/config/reload")
        assert r.status_code == 200, r.text
        assert "max_sessions" in r.json()["changed"]
        assert app.state.settings.max_sessions == 5  # type: ignore[attr-defined]


def test_config_reload_admin_only(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, BOB)
        assert client.post("/api/config/reload").status_code == 403


# -- M21: hardening after adversarial review --------------------------


def test_empty_command_rejected(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        assert client.post("/api/sessions", json={"command": "   "}).status_code == 400


def test_out_of_range_dimensions_rejected(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        assert client.post("/api/sessions", json={"cols": 100000}).status_code == 422
        assert client.post("/api/sessions", json={"rows": 0}).status_code == 422


def test_upload_rejects_symlink(tmp_path: Path) -> None:
    app = build_app(tmp_path, file_root=tmp_path)
    outside = tmp_path.parent / "outside-target.txt"
    outside.write_text("secret")
    (tmp_path / "link").symlink_to(outside)
    try:
        with TestClient(app) as client:
            login(client, ADMIN)
            r = client.post(
                "/api/files/upload",
                data={"path": ""},
                files={"file": ("link", b"overwritten")},
            )
            assert r.status_code == 400
            assert outside.read_text() == "secret"  # untouched
    finally:
        outside.unlink(missing_ok=True)


def test_duplicate_user_conflict(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        r = client.post("/api/users", json={"username": "admin", "password": "x"})
        assert r.status_code == 409


def _recv_until_close(ws: object, timeout: float = 5.0) -> dict[str, object]:
    end = time.time() + timeout
    while time.time() < end:
        message = ws.receive()  # type: ignore[attr-defined]
        if message.get("type") == "websocket.close":
            return message
    raise AssertionError("connection was not closed")


def test_revoked_writable_share_blocks_input(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        token = client.post(
            f"/api/sessions/{sid}/share", json={"writable": True}
        ).json()["token"]
        client.cookies.clear()  # anonymous share viewer

        with client.websocket_connect(f"/ws?share={token}") as ws:
            ws.send_text(json.dumps({"type": "attach", "session": sid, "cols": 80, "rows": 24}))
            attached = _recv_control(ws, {"attached", "error"})
            assert attached is not None and attached["writable"] is True

            app.state.manager.get(sid).revoke_share()  # type: ignore[attr-defined]
            ws.send_text(json.dumps({"type": "input", "data": "echo X\r"}))
            close = _recv_until_close(ws)
            assert close["code"] == 4401


def test_readonly_attach_does_not_resize(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={"cols": 100, "rows": 40}).json()["id"]
        token = client.post(f"/api/sessions/{sid}/share", json={}).json()["token"]
        client.cookies.clear()

        with client.websocket_connect(f"/ws?share={token}") as ws:
            ws.send_text(json.dumps({"type": "attach", "session": sid, "cols": 1, "rows": 1}))
            attached = _recv_control(ws, {"attached", "error"})
            assert attached is not None and attached["writable"] is False

        session = app.state.manager.get(sid)  # type: ignore[attr-defined]
        assert session is not None
        assert (session.spec.cols, session.spec.rows) == (100, 40)
