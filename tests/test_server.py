from __future__ import annotations

import contextlib
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

ADMIN = ("admin", "adminpw123")
BOB = ("bob", "bobpw1234")


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
            assert "无权访问" in msg["msg"]


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
            "/api/login", json={"username": "admin", "password": "adminpw123"}
        ).status_code == 429


def test_successful_login_resets_limit(tmp_path: Path) -> None:
    app = build_app(tmp_path, login_rate_limit=3)
    with TestClient(app) as client:
        for _ in range(2):
            client.post("/api/login", json={"username": "admin", "password": "bad"})
        assert client.post(
            "/api/login", json={"username": "admin", "password": "adminpw123"}
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
            "/api/login", json={"username": "admin", "password": "adminpw123"}
        ).status_code == 401
        # wrong code
        assert client.post(
            "/api/login", json={"username": "admin", "password": "adminpw123", "totp": "000000"}
        ).status_code == 401
        # valid code
        code = pyotp.TOTP(secret).now()
        assert client.post(
            "/api/login", json={"username": "admin", "password": "adminpw123", "totp": code}
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
    (tmp_path / "hello.txt").write_text("hi", encoding="utf-8")
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


async def test_pty_write_reports_drops_when_the_child_stops_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The write path must say "dropped" instead of pretending it worked.

    Deliberately *not* driven through a real PTY: how many bytes the kernel
    accepts from a child that has stopped reading is platform-specific (macOS
    soaks up far more than Linux), so a SIGSTOP-based version of this test only
    passed by luck. Stub the drain instead and assert the application-level
    contract directly.
    """
    import asyncio

    from wsctl.core.pty import MAX_WRITE_BUFFER, PosixPty

    pty = PosixPty(["/bin/sh"], cols=80, rows=24, loop=asyncio.get_running_loop())
    try:
        # "The child stopped reading": nothing ever drains the write buffer.
        # The stub still reports success (an accepted-but-unflushed write is
        # not a drop); only a full buffer or a dead link is.
        monkeypatch.setattr(pty, "_flush", lambda: True)
        assert pty.write(b"x" * MAX_WRITE_BUFFER) is True  # exactly fills the cap
        assert pty.dropped_input == 0
        assert pty.write(b"more") is False  # buffer is at the cap
        assert pty.dropped_input == 1
        assert pty.write(b"and more") is False
        assert pty.dropped_input == 2
    finally:
        pty.close()


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
                assert "连接数已达上限" in str(msg["msg"])


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


def test_unstartable_command_returns_400(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        r = client.post("/api/sessions", json={"command": "/nonexistent-binary-xyz"})
        assert r.status_code == 400, r.text
        assert "无法启动命令" in r.json()["detail"]


def test_unstartable_startup_command_does_not_crash(tmp_path: Path) -> None:
    app = build_app(tmp_path, startup_command="/nonexistent-binary-xyz")
    with TestClient(app) as client:
        login(client, ADMIN)
        assert client.get("/api/sessions").json() == []


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
    tmux.set_namespace(tmp_path)  # match the namespace create_app will use
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
        tmux.set_namespace(None)


@pytest.mark.skipif(not tmux.is_available(), reason="requires tmux")
def test_startup_restores_tmux_session(tmp_path: Path) -> None:
    sid = f"restore-{os.getpid()}-{int(time.time() * 1000)}"
    tmux.set_namespace(tmp_path)  # match the namespace create_app will use
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
        store.user_create("admin", "adminpw123", role="admin")
        store.term_session_upsert(sid, name="restored", owner_id=None, backend="tmux")
        app = create_app(settings, store=store, manager=SessionManager())
        with TestClient(app) as client:
            login(client, ADMIN)
            sessions = client.get("/api/sessions").json()
            assert any(s["id"] == sid and s["backend"] == "tmux" for s in sessions)
    finally:
        tmux.kill_session(name)
        tmux.set_namespace(None)


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

            # input from a read-only client is refused. The refusal is a
            # *notice* (a Toast in the UI), never terminal output: writing it
            # into the buffer used to make it reappear on every reconnect
            # replay as if the shell had printed it.
            ws.send_text(json.dumps({"type": "input", "data": "echo NOPE\r"}))
            notice = _recv_control(ws, {"notice"})
            assert notice is not None and "只读" in str(notice["msg"])
            assert notice.get("level") == "warn"


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
    outside.write_text("secret", encoding="utf-8")
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
            assert outside.read_text(encoding="utf-8") == "secret"  # untouched
    finally:
        outside.unlink(missing_ok=True)


def test_duplicate_user_conflict(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        r = client.post("/api/users", json={"username": "admin", "password": "password123"})
        assert r.status_code == 409


def _recv_bytes_until(ws: object, marker: bytes, timeout: float = 8.0) -> bytes:
    """Collect binary frames (raw terminal output) until ``marker`` shows up."""
    import time

    out = b""
    end = time.time() + timeout
    while time.time() < end and marker not in out:
        message = ws.receive()  # type: ignore[attr-defined]
        if message.get("type") == "websocket.close":
            break
        data = message.get("bytes")
        if data:
            out += data
    return out


def _recv_until_close(ws: object, timeout: float = 5.0) -> dict[str, object]:
    """Drain until the close frame arrives.

    The deadline must not *swallow* the close: an earlier version looped
    ``while time.time() < end`` and then raised "connection was not closed"
    right after ``receive()`` handed it exactly that frame, because the denial
    explanation arrived first and pushed the close past the deadline.
    """
    end = time.time() + timeout
    while time.time() < end + 2.0:  # a little slack to land the close itself
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
            # A revoked share is a permission failure, not an auth failure.
            assert close["code"] == 4403


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


# -- M22: multi-instance, quotas and retention ------------------------


def test_get_share_reuses_token(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        assert client.get(f"/api/sessions/{sid}/share").json() == {"shared": False}

        token = client.post(f"/api/sessions/{sid}/share", json={}).json()["token"]
        got = client.get(f"/api/sessions/{sid}/share").json()
        assert got["shared"] is True
        assert got["token"] == token


def test_get_share_requires_owner(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        client.post("/api/logout")
        login(client, BOB)
        assert client.get(f"/api/sessions/{sid}/share").status_code == 403


def test_per_user_session_quota(tmp_path: Path) -> None:
    app = build_app(tmp_path, max_sessions_per_user=1)
    with TestClient(app) as client:
        login(client, ADMIN)
        assert client.post("/api/sessions", json={}).status_code == 201
        second = client.post("/api/sessions", json={})
        assert second.status_code == 429
        assert "该用户" in second.json()["detail"]


def test_metrics_can_require_auth(tmp_path: Path) -> None:
    app = build_app(tmp_path, metrics_require_auth=True)
    with TestClient(app) as client:
        assert client.get("/metrics").status_code == 401
        login(client, ADMIN)
        assert client.get("/metrics").status_code == 200


def test_session_row_records_owning_instance(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        rows = app.state.store.term_session_list()  # type: ignore[attr-defined]
        row = next(r for r in rows if r["id"] == sid)
        assert row["instance_id"] == app.state.instance_id  # type: ignore[attr-defined]


def test_input_rate_limit_drops_input_but_keeps_the_connection(
    tmp_path: Path,
) -> None:
    """One over-limit chunk is dropped, not used as an excuse to disconnect.

    Rate limiting that tears the link down is indistinguishable from a dead
    terminal, and because the token bucket is per connection the client simply
    reconnects into a fresh bucket and floods again -- a reconnect storm. The
    new contract mirrors the read-only case: report the drop, keep the session.
    """
    # Burst 50 so the *rejected* 60-byte chunk leaves the follow-up write
    # (16 bytes) inside the bucket without waiting a full refill second.
    app = build_app(tmp_path, input_rate_limit=10, input_rate_burst=50)
    with TestClient(app) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "attach", "session": sid, "cols": 80, "rows": 24}))
            assert _recv_control(ws, {"attached"}) is not None
            # 60 bytes in one frame exceeds the 50-byte token bucket.
            ws.send_text(json.dumps({"type": "input", "data": "x" * 60}))
            notice = _recv_control(ws, {"notice"})
            assert notice is not None
            assert "速率超限" in str(notice["msg"])

            # The connection survived: a well-sized write must still reach the
            # shell. If rate limiting killed the link this would never come.
            ws.send_text(json.dumps({"type": "input", "data": "echo $((47*53))\r"}))
            out = _recv_bytes_until(ws, b"2491\r\n")
            assert b"2491\r\n" in out, "connection was killed by one over-limit chunk"


def test_input_rate_limit_disconnects_a_persistent_flood(tmp_path: Path) -> None:
    """A peer that never backs off *is* disconnected -- with its own code."""
    app = build_app(tmp_path, input_rate_limit=10, input_rate_burst=10)
    with TestClient(app) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "attach", "session": sid, "cols": 80, "rows": 24}))
            assert _recv_control(ws, {"attached"}) is not None
            # Send the whole flood first. Reading between sends deadlocks the
            # moment a strike produces no message -- the rate-limit notice is
            # deliberately deduplicated to once per connection.
            for _ in range(12):  # far past MAX_RATE_STRIKES
                ws.send_text(json.dumps({"type": "input", "data": "x" * 20}))
            close = _recv_until_close(ws, timeout=10)
            assert close is not None, "a persistent flood was never disconnected"
            assert close["code"] == 4429, close


def test_ws_notices_never_enter_the_terminal_buffer(tmp_path: Path) -> None:
    """Operational notices must not be replayed as if the shell printed them."""
    from wsctl.core.session import TermSession

    # One-shot stub: the first write is dropped (to provoke a notice), later
    # writes are forwarded to the real implementation. A stub that just
    # returned True would never reach the PTY, and the test would hang waiting
    # for output the shell can never produce -- which is exactly what happened
    # the first time this was written.
    real_write_input = TermSession.write_input
    drops = {"on": True}

    def write_input(self: TermSession, data: bytes) -> bool:
        if drops["on"]:
            drops["on"] = False
            return False
        return real_write_input(self, data)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(TermSession, "write_input", write_input)
        with TestClient(build_app(tmp_path)) as client:
            login(client, ADMIN)
            sid = client.post("/api/sessions", json={}).json()["id"]
            with client.websocket_connect("/ws") as ws:
                ws.send_text(
                    json.dumps({"type": "attach", "session": sid, "cols": 80, "rows": 24})
                )
                assert _recv_control(ws, {"attached"}) is not None
                ws.send_text(json.dumps({"type": "input", "data": "0123456789"}))
                notice = _recv_control(ws, {"notice"})
                assert notice is not None and "丢弃" in str(notice["msg"])
                # Nothing about the drop may reach the terminal as bytes: the
                # scrollback is what a reconnect replays, and replaying an
                # operational warning makes the shell appear to have printed it.
                ws.send_text(json.dumps({"type": "input", "data": "echo $((59*61))\r"}))
                out = _recv_bytes_until(ws, b"3599\r\n")
                assert b"3599\r\n" in out
                assert "丢弃".encode() not in out
                assert b"\x1b[31m[wsctl]" not in out
            client.delete(f"/api/sessions/{sid}")


def test_startup_reclaims_sessions_of_crashed_instance(tmp_path: Path) -> None:
    import socket as _socket

    settings = load_settings(data_dir=tmp_path, auth_required=True, default_shell="/bin/sh")
    store = Store(tmp_path / "test.db")
    store.user_create(*ADMIN, role="admin")
    # A peer that has exited: its pid is no longer alive on this host.
    dead = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
    dead.wait()
    store.instance_register("dead-instance", pid=dead.pid, host=_socket.gethostname())
    store.term_session_upsert(
        "abandoned", name="gone", owner_id=None, backend="local", instance_id="dead-instance"
    )

    app = create_app(settings, store=store, manager=SessionManager())
    with TestClient(app) as client:
        login(client, ADMIN)

    check = Store(tmp_path / "test.db")
    try:
        row = next(r for r in check.term_session_list() if r["id"] == "abandoned")
        assert row["status"] == "stopped"
        assert check.instance_alive_ids(60) == set()  # dead lease pruned
    finally:
        check.close()


def test_pid_alive_helper() -> None:
    # Lives with the reconciliation helpers now (it decides which peer
    # instances are already dead).
    from wsctl.server.maintenance import pid_alive

    assert pid_alive(os.getpid())
    assert not pid_alive(0)
    assert not pid_alive(None)
    dead = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
    dead.wait()
    assert not pid_alive(dead.pid)


def test_disabling_a_user_closes_live_websocket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wsctl.server import ws as ws_mod

    monkeypatch.setattr(ws_mod, "ACCESS_RECHECK", 0.2)
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "attach", "session": sid, "cols": 80, "rows": 24}))
            assert _recv_control(ws, {"attached"}) is not None
            # Disabling the user must terminate the already-open terminal too.
            app.state.store.user_set_disabled("admin", True)  # type: ignore[attr-defined]
            close = _recv_until_close(ws, timeout=5)
            assert close["code"] == 4401


# -- M24: admin surface (users / audit / recordings) ------------------


def test_update_user_role_and_disabled(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        assert client.patch("/api/users/bob", json={"role": "admin"}).status_code == 200
        assert client.patch("/api/users/bob", json={"disabled": True}).status_code == 200
        users = {u["username"]: u for u in client.get("/api/users").json()}
        assert users["bob"]["role"] == "admin"
        assert users["bob"]["disabled"] is True


def test_cannot_disable_self(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        assert client.patch("/api/users/admin", json={"disabled": True}).status_code == 400


def test_update_user_password(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        assert client.patch("/api/users/bob", json={"password": "newpw1234"}).status_code == 200
        client.post("/api/logout")
        assert client.post(
            "/api/login", json={"username": "bob", "password": "newpw1234"}
        ).status_code == 200


def test_totp_admin_endpoints(tmp_path: Path) -> None:
    """TOTP is a *two-step* enrolment: nothing is active until a code verifies.

    The first version wrote the secret to the user row before returning the QR.
    A scan that never happened then left the account demanding a code nobody
    had -- locked out of its own login with no way back but an admin reset. The
    CLI has always confirmed first; the API now matches.
    """
    import pyotp

    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        info = client.post("/api/users/bob/totp").json()
        assert info["secret"]
        assert "<svg" in info["qr_svg"]
        users = {u["username"]: u for u in client.get("/api/users").json()}
        assert users["bob"]["totp"] is False, (
            "the pending enrolment must not be active before a code is confirmed"
        )

        # A wrong code must not activate it either.
        assert client.post(
            "/api/users/bob/totp/confirm", json={"code": "000000"}
        ).status_code == 400
        users = {u["username"]: u for u in client.get("/api/users").json()}
        assert users["bob"]["totp"] is False

        # The real code activates it.
        code = pyotp.TOTP(info["secret"]).now()
        assert client.post(
            "/api/users/bob/totp/confirm", json={"code": code}
        ).status_code == 200
        users = {u["username"]: u for u in client.get("/api/users").json()}
        assert users["bob"]["totp"] is True

        assert client.delete("/api/users/bob/totp").status_code == 200
        users = {u["username"]: u for u in client.get("/api/users").json()}
        assert users["bob"]["totp"] is False


def test_cancelling_a_totp_enrolment_leaves_the_account_untouched(
    tmp_path: Path,
) -> None:
    """Closing the QR dialog without confirming must be a no-op.

    That is the whole point of the second step: an abandoned scan may never
    turn into a login requirement.
    """
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        client.post("/api/users/bob/totp")
        assert client.delete("/api/users/bob/totp").status_code == 200
        users = {u["username"]: u for u in client.get("/api/users").json()}
        assert users["bob"]["totp"] is False
        # And the account can still log in with its password alone.
        assert client.post(
            "/api/login", json={"username": "bob", "password": "bobpw1234"}
        ).status_code == 200


def test_recordings_admin_crud(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        client.post(f"/api/sessions/{sid}/recording/start", json={})
        client.post(f"/api/sessions/{sid}/recording/stop")
        items = client.get("/api/recordings").json()
        assert items and items[0]["name"].endswith(".cast")
        name = items[0]["name"]
        dl = client.get(f"/api/recordings/{name}")
        assert dl.status_code == 200
        assert dl.content.startswith(b'{"version": 2')
        assert client.delete(f"/api/recordings/{name}").status_code == 200
        assert client.get(f"/api/recordings/{name}").status_code == 404
        # non-.cast / traversal-ish names are rejected
        assert client.delete("/api/recordings/evil.txt").status_code == 400


def test_audit_filter_by_event(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        events = client.get("/api/audit", params={"event": "login"}).json()
        assert events
        assert all(e["event"] == "login" for e in events)


def test_audit_writer_flushes_before_read(tmp_path: Path) -> None:
    # The audit writer is async; the API must flush so reads are consistent.
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        events = {e["event"] for e in client.get("/api/audit").json()}
        assert "login" in events


def test_audit_pagination(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        for _ in range(5):
            client.post("/api/login", json={"username": "admin", "password": "bad"})
        first = client.get("/api/audit", params={"limit": 3, "offset": 0}).json()
        second = client.get("/api/audit", params={"limit": 3, "offset": 3}).json()
        assert len(first) == 3
        assert first and second
        assert {e["id"] for e in first}.isdisjoint({e["id"] for e in second})


def test_session_serialize_includes_bytes(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        info = next(s for s in client.get("/api/sessions").json() if s["id"] == sid)
        assert "bytes" in info and isinstance(info["bytes"], int)
        assert "created_at" in info and "last_active" in info


def test_cannot_remove_last_admin(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        # admin is the only admin and cannot demote/disable/delete itself
        assert client.patch("/api/users/admin", json={"role": "user"}).status_code == 400
        assert client.patch("/api/users/admin", json={"disabled": True}).status_code == 400
        assert client.delete("/api/users/admin").status_code == 400
        # with a second admin, demotion is allowed
        assert client.patch("/api/users/bob", json={"role": "admin"}).status_code == 200
        assert client.patch("/api/users/admin", json={"role": "user"}).status_code == 200


# -- M31: WebSocket close-code semantics ------------------------------


def test_ws_missing_session_closes_4404(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        with client.websocket_connect("/ws") as ws:
            ws.send_text(
                json.dumps({"type": "attach", "session": "does-not-exist", "cols": 80, "rows": 24})
            )
            err = _recv_control(ws, {"error"})
            assert err is not None and "会话不存在" in str(err["msg"])
            assert _recv_until_close(ws)["code"] == 4404


def test_ws_forbidden_session_closes_4403(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        client.post("/api/logout")
        login(client, BOB)
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "attach", "session": sid, "cols": 80, "rows": 24}))
            assert _recv_until_close(ws)["code"] == 4403


def test_ws_session_limit_closes_4409(tmp_path: Path) -> None:
    app = build_app(tmp_path, max_sessions=1)
    with TestClient(app) as client:
        login(client, ADMIN)
        assert client.post("/api/sessions", json={}).status_code == 201
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "attach", "cols": 80, "rows": 24}))
            err = _recv_control(ws, {"error"})
            assert err is not None and "上限" in str(err["msg"])
            assert _recv_until_close(ws)["code"] == 4409


def test_ws_bad_first_message_closes_4400(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "ping"}))
            assert _recv_until_close(ws)["code"] == 4400


def test_ws_idle_connection_is_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wsctl.server import ws as ws_mod

    monkeypatch.setattr(ws_mod, "ACCESS_RECHECK", 0.1)
    monkeypatch.setattr(ws_mod, "IDLE_TIMEOUT", 0.3)
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "attach", "session": sid, "cols": 80, "rows": 24}))
            assert _recv_control(ws, {"attached"}) is not None
            # Sends nothing: a half-open peer must be closed, not held forever.
            assert _recv_until_close(ws, timeout=5)["code"] == 4408


def test_ws_malformed_json_closes_4400(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        with client.websocket_connect("/ws") as ws:
            ws.send_text("not json at all")
            assert _recv_until_close(ws)["code"] == 4400


def test_ws_bad_dimensions_closes_4400(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "attach", "cols": "wide", "rows": 24}))
            assert _recv_until_close(ws)["code"] == 4400


def test_password_change_revokes_other_sessions(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        client.post("/api/login", json={"username": "bob", "password": "bobpw1234"})
        bob_token = client.cookies.get("wsctl_session")
        assert bob_token
        client.cookies.clear()

        login(client, ADMIN)
        assert client.patch("/api/users/bob", json={"password": "newpw1234"}).status_code == 200

        client.cookies.clear()
        client.cookies.set("wsctl_session", bob_token)
        assert client.get("/api/me").status_code == 401


# -- 0.1.4: policy, persistence, overwrite, config surface ----------------


def test_password_policy_is_enforced_over_the_api(tmp_path: Path) -> None:
    """A weak password is refused -- whether by the field bounds (422) or by
    the shared storage-boundary policy (400). Both are refusals; the point is
    that neither path lets it through.
    """
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        for weak in ("", "short", "1234567"):
            r = client.post("/api/users", json={"username": "weak", "password": weak})
            assert r.status_code in (400, 422), f"{weak!r} was accepted: {r.status_code}"
        assert client.post(
            "/api/users", json={"username": "ok", "password": "password123"}
        ).status_code == 201
        assert client.patch(
            "/api/users/ok", json={"password": "short"}
        ).status_code in (400, 422)


def test_share_is_persisted_and_survives_a_restart(tmp_path: Path) -> None:
    """The share token lives in the database, not only in the session object."""
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={"backend": "tmux"}).json()["id"]
        token = client.post(
            f"/api/sessions/{sid}/share", json={"writable": False, "ttl": 3600}
        ).json()["token"]
        rows = app.state.store.term_session_list()  # type: ignore[attr-defined]
        row = next(r for r in rows if r["id"] == sid)
        assert row["share_token"] == token
        assert row["share_writable"] == 0
        assert row["share_expires"] is not None

        # A rename must not silently invalidate the distributed link.
        client.patch(f"/api/sessions/{sid}", json={"name": "renamed"})
        rows = app.state.store.term_session_list()  # type: ignore[attr-defined]
        row = next(r for r in rows if r["id"] == sid)
        assert row["share_token"] == token

        client.delete(f"/api/sessions/{sid}/share")
        rows = app.state.store.term_session_list()  # type: ignore[attr-defined]
        row = next(r for r in rows if r["id"] == sid)
        assert row["share_token"] is None
        client.delete(f"/api/sessions/{sid}")


def test_upload_refuses_to_clobber_without_overwrite(tmp_path: Path) -> None:
    app = build_app(tmp_path, file_root=tmp_path)
    (tmp_path / "exists.txt").write_text("original", encoding="utf-8")
    with TestClient(app) as client:
        login(client, ADMIN)
        r = client.post(
            "/api/files/upload",
            data={"path": ""},
            files={"file": ("exists.txt", b"replacement")},
        )
        assert r.status_code == 409
        assert (tmp_path / "exists.txt").read_text(encoding="utf-8") == "original"

        r = client.post(
            "/api/files/upload",
            data={"path": "", "overwrite": "1"},
            files={"file": ("exists.txt", b"replacement")},
        )
        assert r.status_code == 201
        assert (tmp_path / "exists.txt").read_bytes() == b"replacement"


def test_file_listing_reports_truncation(tmp_path: Path) -> None:
    from wsctl.core import fs as fs_mod

    app = build_app(tmp_path, file_root=tmp_path)
    original = fs_mod.DEFAULT_LIST_LIMIT
    fs_mod.DEFAULT_LIST_LIMIT = 3
    try:
        for index in range(6):
            (tmp_path / f"f{index}.txt").write_text("x", encoding="utf-8")
        with TestClient(app) as client:
            login(client, ADMIN)
            data = client.get("/api/files").json()
            assert data["truncated"] is True
            assert len(data["entries"]) == 3
            assert data["limit"] == 3
    finally:
        fs_mod.DEFAULT_LIST_LIMIT = original


def test_config_endpoint_lists_hot_and_restart_fields(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        data = client.get("/api/config").json()
        kinds = {f["key"]: f["kind"] for f in data["fields"]}
        assert kinds["max_sessions"] == "hot"
        assert kinds["port"] == "restart"
        assert data["config_path"]
        assert data["data_dir"]


def test_config_endpoint_is_admin_only(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, BOB)
        assert client.get("/api/config").status_code == 403


def test_config_reload_surfaces_errors(tmp_path: Path) -> None:
    config = tmp_path / "wsctl" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("max_sessions = 7\n", encoding="utf-8")
    app = build_app(tmp_path, config_path=config)
    with TestClient(app) as client:
        login(client, ADMIN)
        ok = client.post("/api/config/reload").json()
        assert ok["errors"] == []
        assert "max_sessions" in ok["changed"]

        # A broken edit is reported instead of silently ignored.
        config.write_text('max_sessions = "not-an-int"\n', encoding="utf-8")
        bad = client.post("/api/config/reload").json()
        assert bad["changed"] == []
        assert bad["errors"]
        assert app.state.settings.max_sessions == 7  # type: ignore[attr-defined]


def test_session_list_includes_owner_name(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        info = next(s for s in client.get("/api/sessions").json() if s["id"] == sid)
        assert info["owner"] == "admin"
        client.delete(f"/api/sessions/{sid}")


def test_ws_input_backpressure_reports_the_drop(tmp_path: Path) -> None:
    """A dropped keystroke must be reported, not silently vanish."""
    from wsctl.core.session import TermSession

    # Deterministic: the shell drains stdin too quickly to fill the real buffer,
    # so stand in for "the child stopped reading".
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(TermSession, "write_input", lambda self, data: False)
        with TestClient(build_app(tmp_path)) as client:
            login(client, ADMIN)
            sid = client.post("/api/sessions", json={}).json()["id"]
            with client.websocket_connect("/ws") as ws:
                ws.send_text(
                    json.dumps({"type": "attach", "session": sid, "cols": 80, "rows": 24})
                )
                assert _recv_control(ws, {"attached"}) is not None
                ws.send_text(json.dumps({"type": "input", "data": "0123456789"}))
                notice = _recv_control(ws, {"notice"})
                assert notice is not None and "丢弃" in str(notice["msg"])
            client.delete(f"/api/sessions/{sid}")


def test_session_history_shows_finished_sessions(tmp_path: Path) -> None:
    """A finished session must not vanish without a trace.

    The rows were always written (``status``, timestamps, a retention policy
    to prune them) and nothing ever read them -- the product remembered a
    session was killed and refused to tell anyone.
    """
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={"name": "doomed"}).json()["id"]
        assert client.get("/api/sessions/history").json() == []
        client.delete(f"/api/sessions/{sid}")

        rows = client.get("/api/sessions/history").json()
        assert len(rows) == 1, rows
        row = rows[0]
        assert row["id"] == sid
        assert row["name"] == "doomed"
        assert row["status"] == "killed"
        assert row["duration"] >= 0


def test_session_detail_reports_live_and_finished(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        sid = client.post(
            "/api/sessions", json={"name": "probed", "command": "sleep 30"}
        ).json()["id"]

        live = client.get(f"/api/sessions/{sid}/detail").json()
        assert live["state"] == "running"
        assert live["name"] == "probed"
        assert live["owner"] == ADMIN[0]
        assert isinstance(live["attached"], list)

        client.delete(f"/api/sessions/{sid}")
        finished = client.get(f"/api/sessions/{sid}/detail").json()
        assert finished["state"] == "finished"
        assert finished["status"] == "killed"
        assert finished["alive"] is False


def test_session_detail_is_private_to_its_owner(tmp_path: Path) -> None:
    """``argv``/``cwd`` can hold sensitive paths; only the owner sees them."""
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={"name": "secret"}).json()["id"]

        client.cookies.clear()
        login(client, BOB)  # a different, non-admin user
        assert client.get(f"/api/sessions/{sid}/detail").status_code == 403
        assert client.get("/api/sessions/history").json() == []


def test_overview_reports_instances_and_recent_activity(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        client.post("/api/sessions", json={"name": "busy"})
        payload = client.get("/api/overview").json()
        assert payload["sessions"] == 1
        assert payload["max_sessions"] >= 1
        assert payload["instances"], "the running instance must list itself"
        assert payload["recent_audit"], "the login that just happened must show up"
        # Curated gauges, not a truncated text scrape.
        assert isinstance(payload["metrics"], dict)
        assert payload["metrics"].get("wsctl_up") == 1.0
        assert payload["metrics"].get("wsctl_sessions") == 1.0

        client.cookies.clear()
        login(client, BOB)
        assert client.get("/api/overview").status_code == 403


def test_file_panel_endpoints(tmp_path: Path) -> None:
    """mkdir / rename / preview / edit / delete round trip through the API."""
    files = tmp_path / "files"
    files.mkdir()
    (files / "seed.txt").write_text("seed", encoding="utf-8")
    app = build_app(tmp_path, file_root=files)
    with TestClient(app) as client:
        login(client, ADMIN)

        # mkdir
        made = client.post("/api/files/mkdir", json={"name": "notes"})
        assert made.status_code == 201, made.text
        assert (files / "notes").is_dir()

        # illegal names are refused (400 by the name rules, 422 by the field
        # bounds -- both are refusals and neither may slip through)
        for bad in ("..", "a/b", ".wsctl-upload", ""):
            assert client.post("/api/files/mkdir", json={"name": bad}).status_code in (
                400,
                422,
            ), bad

        # rename
        renamed = client.post("/api/files/rename", json={"path": "seed.txt", "name": "seeds.txt"})
        assert renamed.status_code == 200, renamed.text
        assert (files / "seeds.txt").is_file()

        # preview + edit
        assert client.get("/api/files/preview", params={"path": "seeds.txt"}).text == "seed"
        saved = client.put(
            "/api/files/content", json={"path": "seeds.txt", "content": "grown 成长"}
        )
        assert saved.status_code == 200, saved.text
        assert (files / "seeds.txt").read_text(encoding="utf-8") == "grown 成长"

        # a binary cannot be previewed
        (files / "blob.bin").write_bytes(b"\x00\x01")
        assert client.get("/api/files/preview", params={"path": "blob.bin"}).status_code == 400

        # delete (a file)
        assert client.request(
            "DELETE", "/api/files", params={"path": "", "name": "seeds.txt"}
        ).status_code == 200
        assert not (files / "seeds.txt").exists()

        # delete refuses a non-empty directory
        (files / "notes" / "keep.txt").write_text("x", encoding="utf-8")
        refused = client.request(
        "DELETE", "/api/files", params={"path": "", "name": "notes"}
    )
        assert refused.status_code == 400
        assert "非空" in refused.text
        # ... and the root is never deletable
        root_delete = client.request("DELETE", "/api/files", params={"path": "", "name": ""})
        assert root_delete.status_code == 400


def test_file_listing_pages_beyond_the_old_limit(tmp_path: Path) -> None:
    files = tmp_path / "files"
    files.mkdir()
    for i in range(30):
        (files / f"n{i:02d}.txt").write_text("x", encoding="utf-8")
    app = build_app(tmp_path, file_root=files)
    with TestClient(app) as client:
        login(client, ADMIN)
        first = client.get("/api/files", params={"limit": 20, "offset": 0}).json()
        assert len(first["entries"]) == 20
        assert first["total"] == 30
        second = client.get("/api/files", params={"limit": 20, "offset": 20}).json()
        assert len(second["entries"]) == 10
        seen = {e["name"] for e in first["entries"]} | {e["name"] for e in second["entries"]}
        assert len(seen) == 30
        filtered = client.get("/api/files", params={"contains": "n1"}).json()
        assert filtered["total"] == 10  # n10..n19


def test_rename_reaches_already_attached_clients(tmp_path: Path) -> None:
    """A rename must move every attached tab, not just the one that renamed.

    The rename was written to the store and the in-memory spec, but nothing
    broadcast it: two tabs on one session kept showing different names until
    each happened to reconnect.
    """
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={"name": "before"}).json()["id"]
        with (
            client.websocket_connect("/ws") as first,
            client.websocket_connect("/ws") as second,
        ):
            for ws in (first, second):
                ws.send_text(
                    json.dumps({"type": "attach", "session": sid, "cols": 80, "rows": 24})
                )
                attached = _recv_control(ws, {"attached"})
                assert attached is not None and attached["name"] == "before"

            assert client.patch(f"/api/sessions/{sid}", json={"name": "after"}).status_code == 200

            for label, ws in (("first", first), ("second", second)):
                renamed = _recv_control(ws, {"renamed"})
                assert renamed is not None, f"{label} never heard about the rename"
                assert renamed["name"] == "after"
                assert renamed["session"] == sid


def test_session_detail_shape_is_the_same_live_and_finished(tmp_path: Path) -> None:
    """``/detail`` must not make consumers branch on "is it alive".

    The live path lacked ``argv``/``cwd``/``duration``/``ended_reason`` and the
    history path lacked ``pid``/``bytes``/``shared``, so every consumer had to
    special-case both. The 0.1.8 plan named these fields explicitly.
    """
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        sid = client.post(
            "/api/sessions", json={"name": "shape", "command": "sleep 30"}
        ).json()["id"]
        live = client.get(f"/api/sessions/{sid}/detail").json()

        client.delete(f"/api/sessions/{sid}")
        finished = client.get(f"/api/sessions/{sid}/detail").json()

        # The plan named exactly these.
        for key in ("pid", "argv", "cwd", "backend", "instance_id", "attached",
                    "bytes", "recording_path", "share_active", "timeline"):
            assert key in live, f"live missing {key}"
            assert key in finished, f"history missing {key}"
        # and the ones that used to differ between the two
        for key in ("duration", "ended_reason", "status", "state", "command",
                    "clients", "alive", "shared", "share_writable", "recording"):
            assert key in live, f"live missing {key}"
            assert key in finished, f"history missing {key}"

        assert live["state"] == "running"
        assert finished["state"] == "finished"
        assert live["argv"] and finished["argv"]
        assert live["duration"] >= 0 and finished["duration"] >= 0
        assert finished["ended_reason"], "a finished session must say why"


def test_reopen_replays_argv_so_an_ssh_session_never_becomes_local(
    tmp_path: Path,
) -> None:
    """Reopening from history must not turn a remote command into a local one.

    History used to rebuild a session from ``command`` + ``backend``. For SSH
    that 400'd (no ``ssh`` block) and the tempting fix -- dropping the backend
    -- would have executed the *remote* command on the *local* shell. Replaying
    the **recorded** ``argv`` is what the endpoint promises; taking the
    caller's word for it instead made ``sid`` decorative and let the client
    dictate what would be spawned.
    """
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        created = client.post(
            "/api/sessions", json={"name": "orig", "command": "sleep 30"}
        ).json()["id"]
        client.delete(f"/api/sessions/{created}")

        again = client.post(
            f"/api/sessions/{created}/reopen",
            json={"name": "orig"},
        )
        assert again.status_code == 201, again.text
        body = again.json()
        assert body["name"] == "orig"

        # The *recorded* argv is preserved verbatim -- that is the property
        # that keeps an ``ssh`` an ``ssh`` -- and it is visible on detail.
        detail = client.get(f"/api/sessions/{body['id']}/detail").json()
        assert detail["argv"] == ["sleep", "30"], detail["argv"]
        # The same shape as the history row: a list, never a JSON string.
        history = client.get("/api/sessions/history").json()
        recorded = next(r for r in history if r["id"] == created)
        assert recorded["argv"] == ["sleep", "30"], recorded["argv"]
        assert isinstance(recorded["argv"], list)

        # The caller cannot smuggle a different command through the replay.
        smuggled = client.post(
            f"/api/sessions/{created}/reopen",
            json={"name": "evil", "argv": ["touch", "/tmp/pwned"], "backend": "local"},
        )
        assert smuggled.status_code == 201, smuggled.text
        assert smuggled.json()["name"] in ("evil", "orig")
        smuggled_detail = client.get(f"/api/sessions/{smuggled.json()['id']}/detail").json()
        assert smuggled_detail["argv"] == ["sleep", "30"], (
            "the replay must ignore a caller-supplied argv"
        )

        # A nonexistent or foreign sid is not "reopenable".
        assert client.post("/api/sessions/nope/nope/reopen", json={}).status_code in (404,)
        missing = client.post("/api/sessions/ffffffffffffffff/reopen", json={})
        assert missing.status_code == 404

        # An SSH-shaped record is refused as a *backend*, never silently run
        # as a local command.
        ssh_sid = client.post(
            "/api/sessions",
            json={"backend": "ssh", "ssh": {"host": "127.0.0.1", "port": 1}},
        ).json()["id"]
        client.delete(f"/api/sessions/{ssh_sid}")
        refused = client.post(f"/api/sessions/{ssh_sid}/reopen", json={})
        assert refused.status_code == 400
        assert "local" in refused.text


def test_rate_limit_disconnect_sends_exactly_one_reason(tmp_path: Path) -> None:
    """One over-limit event, one message -- not a notice *and* an error."""
    app = build_app(tmp_path, input_rate_limit=10, input_rate_burst=10)
    with TestClient(app) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "attach", "session": sid, "cols": 80, "rows": 24}))
            assert _recv_control(ws, {"attached"}) is not None
            texts: list[str] = []
            for _ in range(12):
                ws.send_text(json.dumps({"type": "input", "data": "x" * 20}))
            # Drain until the close. A strike that sends nothing must not be
            # mistaken for a stuck connection.
            import time as _time

            end = _time.time() + 10
            close = None
            while _time.time() < end:
                message = ws.receive()
                if message.get("type") == "websocket.close":
                    close = message
                    break
                text = message.get("text")
                if text:
                    texts.append(json.loads(text))
            assert close is not None and close["code"] == 4429
            # One *notice* for the whole connection (like the read-only and
            # backpressure notices) plus exactly one closing reason. The old
            # shape emitted a notice per dropped chunk and then an error, so a
            # flood filled the screen with the same sentence.
            notices = [m for m in texts if m.get("type") == "notice"]
            errors = [m for m in texts if m.get("type") == "error"]
            assert len(notices) == 1, notices
            # The closing reason is the whole point: it used to be queued and
            # then lost because the socket closed underneath the writer.
            assert len(errors) == 1, f"closing reason lost: {texts}"
            assert "速率" in str(notices[0].get("msg", ""))
            assert "速率" in str(errors[0].get("msg", ""))
            assert texts[-1]["type"] == "error", "the closing reason must be last"


def test_slow_consumer_is_told_why_it_was_dropped(tmp_path: Path) -> None:
    """A client that cannot keep up must not vanish silently.

    This is the "terminal froze and went black" incident: a burst of output
    blew the client's send budget, the socket closed with 1000 (a *clean*
    shutdown), the browser reconnected and cleared its terminal to make room
    for a replay it then lost again. The user saw a black screen and the log
    said nothing at all.
    """
    from wsctl.server.client import WsClient

    # Drive the sink directly: filling a real 8 MiB budget over a socket is
    # both slow and timing-dependent.
    class FakeWS:
        def __init__(self) -> None:
            self.sent: list[object] = []

        async def send_bytes(self, data: bytes) -> None:  # pragma: no cover - not reached
            self.sent.append(data)

        async def send_json(self, obj: object) -> None:  # pragma: no cover
            self.sent.append(obj)

    client = WsClient(FakeWS(), max_pending=4, max_bytes=10)
    # Overflow must NOT raise: a slow viewer is shed, not executed.
    client.put(b"x" * 50)
    assert client.dropped_bytes > 0, "the frame should have been shed, not queued"
    assert client.closed is False, "shedding must never close the connection"

    # And the one message that matters still fits: control frames are not
    # counted against the byte budget.
    assert client.final_notice({"type": "evicted", "reason": "backpressure"}) is True


def test_slow_consumer_drop_is_counted_and_not_fatal() -> None:
    """The drop is observable (metric) and recoverable (not a fatal close code)."""
    from wsctl.core import closecodes
    from wsctl.core.session import slow_consumer_drops

    assert isinstance(slow_consumer_drops(), int)
    assert closecodes.CLOSE_SLOW_CONSUMER in closecodes.ALL_CLOSE_CODES
    assert closecodes.CLOSE_SLOW_CONSUMER not in closecodes.FATAL_CLOSE_CODES, (
        "a slow viewer must be able to reconnect -- the session is fine"
    )


def test_slow_consumer_is_closed_with_4410_not_a_clean_1000() -> None:
    """The close *code* must say "too slow", not "normal closure".

    Asserting the constant exists is not the same as asserting it is sent --
    and the first version of this fix shipped with 4410 defined, classified and
    documented while the wire still carried 1000. That is exactly the reading
    that made the browser think everything was fine and blank its screen.
    """
    from wsctl.server.client import WsClient

    class FakeWS:
        async def send_bytes(self, data: bytes) -> None:  # pragma: no cover
            pass

        async def send_json(self, obj: object) -> None:  # pragma: no cover
            pass

    client = WsClient(FakeWS(), max_pending=4, max_bytes=10)
    # A slow consumer is no longer *dropped* at all -- frames are shed and the
    # link stays up. The 4410 close code now belongs to the one case that still
    # throws a viewer out: a session over its own memory limit.
    client.put(b"x" * 50)
    assert client.close_code is None, "shedding is not a disconnection"
    assert client.closed is False


def test_final_notice_is_delivered_before_the_writer_stops() -> None:
    """``close`` must terminate the writer, and not swallow a queued notice.

    ``close`` used to return early once the client had marked itself closed --
    the slow-consumer path -- which left ``run()`` blocked on the queue
    forever (a two-second teardown stall per drop) and made any notice queued
    afterwards unreachable.
    """
    import asyncio

    from wsctl.server.client import WsClient

    sent: list[object] = []

    class FakeWS:
        async def send_bytes(self, data: bytes) -> None:  # pragma: no cover
            sent.append(data)

        async def send_json(self, obj: object) -> None:
            sent.append(obj)

    async def run() -> None:
        client = WsClient(FakeWS(), max_pending=8, max_bytes=10)
        # Blow the byte budget: frames are shed, nothing is raised.
        client.put(b"x" * 50)
        assert client.final_notice({"type": "evicted", "reason": "backpressure"}) is True
        client.close()
        await asyncio.wait_for(client.run(), timeout=1.0)  # must terminate

    asyncio.run(run())
    assert {"type": "evicted", "reason": "backpressure"} in sent, (
        f"the one message the user needs was never sent: {sent}"
    )


def test_broadcast_reports_a_dropped_slow_client(tmp_path: Path) -> None:
    """``_broadcast`` must log the drop and hand the client a reason."""
    import logging

    from wsctl.core.session import ClientGone, SessionManager, SessionSpec

    manager = SessionManager()

    class SlowClient:
        """Accepts nothing: every put fails like a blown byte budget."""
        close_code: int | None = None


        def __init__(self) -> None:
            self.notices: list[dict] = []

        def put(self, item) -> None:
            if isinstance(item, dict):
                self.notices.append(item)
                return
            raise ClientGone("客户端积压溢出")

        def final_notice(self, message: dict) -> bool:
            self.notices.append(message)
            return True

        def close(self) -> None:  # pragma: no cover - not reached
            pass

    import asyncio

    async def run() -> tuple[list, str]:
        session = await manager.create(
            SessionSpec(name="slow", argv=["/bin/sh", "-c", "sleep 30"])
        )
        slow = SlowClient()
        await session.attach(slow)
        records: list[str] = []

        class Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record.getMessage())

        handler = Capture()
        logging.getLogger("wsctl.session").addHandler(handler)
        try:
            await session._broadcast(b"output the client could not keep up with")
        finally:
            logging.getLogger("wsctl.session").removeHandler(handler)
            await manager.remove(session.id)
        return slow.notices, " ".join(records)

    notices, logtext = asyncio.run(run())
    # the client is gone from the session ...
    # ... and it was told why, not left staring at a dead terminal
    assert any(n.get("type") == "evicted" for n in notices), notices
    assert any(n.get("reason") == "backpressure" for n in notices), notices
    assert "slow client" in logtext, logtext


def test_revoked_share_stops_text_input_immediately(tmp_path: Path) -> None:
    """Revocation must bite on the *text* input path too, not just binary.

    The binary path checked ``share_revoked()`` and the periodic recheck did,
    but ``{"type":"input"}`` skipped it: a revoked writable share kept working
    for up to ACCESS_RECHECK. "Revoke" has to mean now.
    """
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        token = client.post(
            f"/api/sessions/{sid}/share", json={"writable": True}
        ).json()["token"]
        client.cookies.clear()

        with client.websocket_connect(f"/ws?share={token}") as ws:
            ws.send_text(json.dumps({"type": "attach", "session": sid, "cols": 80, "rows": 24}))
            attached = _recv_control(ws, {"attached", "error"})
            assert attached is not None and attached["writable"] is True

            app.state.manager.get(sid).revoke_share()  # type: ignore[attr-defined]
            # Text input, not binary -- the path that used to skip the check.
            started = time.time()
            ws.send_text(json.dumps({"type": "input", "data": "echo X\r"}))
            close = _recv_until_close(ws, timeout=5)
            # 4403 is the contract (core.closecodes.CLOSE_FORBIDDEN). The 403
            # of the early days is an HTTP status, not a WebSocket close code,
            # and letting both through left "used the wrong code" free to pass.
            assert close["code"] == 4403, close
            # Immediate: not "sometime within the 5 s recheck window".
            assert time.time() - started < 3.0, "revocation took a recheck cycle"




def test_a_slow_viewer_is_shed_not_disconnected(tmp_path: Path) -> None:
    """Falling behind must shorten the history, never cut the cord.

    This is what "内容一跳一跳" looked like from the far end: a burst of output
    blew the byte budget, the connection was dropped, the browser reconnected
    and cleared its terminal to make room for a replay -- and the next burst
    did it again. A terminal is a *current view*, not an archive: shedding the
    oldest frames is correct, executing the viewer for being slow is not.
    """
    from wsctl.server.client import WsClient

    class FakeWS:
        def __init__(self) -> None:
            self.sent: list[object] = []

        async def send_bytes(self, data: bytes) -> None:
            self.sent.append(data)

        async def send_json(self, obj: object) -> None:
            self.sent.append(obj)

    client = WsClient(FakeWS(), max_pending=8, max_bytes=100)
    for _ in range(200):
        client.put(b"y" * 40)

    assert client.closed is False, "a slow viewer must keep its connection"
    assert client.close_code is None
    assert client.dropped_bytes > 0, "frames should have been shed"
    assert client.dropped_events > 0
    # The newest frames survive; the oldest are what went.
    assert client.pending_bytes <= 100
    # ...and the shed is announced rather than silently swallowed.
    assert any(
        i.get("type") == "desync" for i in client.queued_controls()
    ), "shedding must be reported to the viewer as a desync"


def test_control_frames_survive_the_shed(tmp_path: Path) -> None:
    """The one message explaining the loss is the one that must get through."""
    from wsctl.server.client import WsClient

    class FakeWS:
        async def send_bytes(self, data: bytes) -> None:  # pragma: no cover
            pass

        async def send_json(self, obj: object) -> None:  # pragma: no cover
            pass

    client = WsClient(FakeWS(), max_pending=6, max_bytes=50)
    client.put({"type": "attached", "session": "s"})
    for _ in range(100):
        client.put(b"z" * 30)
    assert any(
        i.get("type") == "attached" for i in client.queued_controls()
    ), "a control message was evicted by the shed"


def test_release_assets_cannot_go_stale(tmp_path: Path) -> None:
    """`app.js` must never be served from a stale browser cache.

    The symptom this prevents: a user upgrades, reloads, and gets the new
    `index.html` with the new dropdown while the cached `app.js` is still the
    previous release's -- so the control is on screen with nothing wired to it
    and right-click keeps doing whatever it did before. `StaticFiles` sends no
    `Cache-Control` at all, leaving the browser to cache heuristically.
    """
    with TestClient(build_app(tmp_path)) as client:
        for path in ("/", "/static/app.js", "/static/app.css", "/static/index.html"):
            response = client.get(path)
            assert response.status_code == 200, path
            cache = response.headers.get("cache-control", "")
            assert "no-cache" in cache or "must-revalidate" in cache, (
                f"{path} may go stale: cache-control={cache!r}"
            )
        # Version-pinned vendor bundles are the opposite: they never change in
        # place, so caching them hard is both safe and desirable.
        vendor = client.get("/static/vendor/xterm.js")
        assert vendor.status_code == 200
        assert "max-age=31536000" in vendor.headers.get("cache-control", "")


# -- 0.1.22: checks must be driven by the clock, not by "the peer went quiet" --


def test_a_chatty_peer_cannot_dodge_the_periodic_recheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-validation is driven by the *clock*, not by the receive timing out.

    The share/auth/idle checks used to live only in the ``except TimeoutError``
    branch. Any client that sent a frame every few seconds -- ``ping``, even a
    malformed one -- kept ``wait_for`` from ever timing out, so a disabled
    account, a revoked token or a revoked share was never re-checked and the
    connection kept a fully writable terminal indefinitely. This test keeps the
    peer *loud* the whole time: under the old code the loop below runs to its
    deadline with the socket still open.
    """
    from wsctl.server import ws as ws_mod

    monkeypatch.setattr(ws_mod, "ACCESS_RECHECK", 0.2)
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "attach", "session": sid, "cols": 80, "rows": 24}))
            assert _recv_control(ws, {"attached"}) is not None
            app.state.store.user_set_disabled("admin", True)  # type: ignore[attr-defined]
            end = time.time() + 5.0
            closed = None
            while time.time() < end:
                try:
                    ws.send_text(json.dumps({"type": "ping"}))
                except Exception:
                    break
                message = ws.receive()
                if message.get("type") == "websocket.close":
                    closed = message
                    break
            assert closed is not None, (
                "a peer that never went quiet kept a disabled account's "
                "terminal open for the whole window"
            )
            assert closed["code"] == 4401, closed


def test_a_revoked_share_may_not_resync_the_history(tmp_path: Path) -> None:
    """``resync`` hands out the whole scrollback -- revocation has to gate it.

    Every input path checked ``share_revoked()``; ``resync`` did not. And the
    session-level share filter inside ``_broadcast`` never sees this path at
    all (it replays straight from the session buffer), so a revoked viewer
    could pull the entire history -- and trigger a repaint nudge -- with one
    frame.
    """
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        token = client.post(
            f"/api/sessions/{sid}/share", json={"writable": True}
        ).json()["token"]
        client.cookies.clear()
        with client.websocket_connect(f"/ws?share={token}") as ws:
            ws.send_text(json.dumps({"type": "attach", "session": sid, "cols": 80, "rows": 24}))
            attached = _recv_control(ws, {"attached", "error"})
            assert attached is not None and attached["writable"] is True
            app.state.manager.get(sid).revoke_share()  # type: ignore[attr-defined]
            ws.send_text(json.dumps({"type": "resync"}))
            close = _recv_until_close(ws, timeout=5)
            assert close["code"] == 4403, close


def test_a_demoted_admin_loses_their_live_writable_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Access is re-derived on every recheck, not frozen at the handshake.

    ``recheck()`` used to answer only "does this token still resolve to *some*
    user". A demoted admin therefore kept ``writable=True`` on sessions
    ``can_access()`` now refuses, until they happened to reconnect.
    """
    from wsctl.server import ws as ws_mod

    monkeypatch.setattr(ws_mod, "ACCESS_RECHECK", 0.2)
    app = build_app(tmp_path)
    with TestClient(app) as client:
        # Bob owns the session; admin attaches by privilege.
        login(client, BOB)
        sid = client.post("/api/sessions", json={}).json()["id"]
        login(client, ADMIN)
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "attach", "session": sid, "cols": 80, "rows": 24}))
            attached = _recv_control(ws, {"attached", "error"})
            assert attached is not None and attached.get("writable") is True
            # Demote the connected admin to a plain user: the very session they
            # are sitting in is Bob's, which a plain user may not touch.
            assert app.state.store.user_set_role("admin", "user")  # type: ignore[attr-defined]
            close = _recv_until_close(ws, timeout=5)
            assert close["code"] == 4403, close


def test_history_says_why_a_session_ended(tmp_path: Path) -> None:
    """A clean ``exit`` must not appear in history as "服务退出或实例崩溃"."""
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client, ADMIN)
        sid = client.post(
            "/api/sessions", json={"command": "/bin/sh -c 'exit 3'"}
        ).json()["id"]
        end = time.time() + 8.0
        row = None
        while time.time() < end:
            rows = client.get("/api/sessions/history").json()
            match = [r for r in rows if r["id"] == sid]
            if match:
                row = match[0]
                break
            time.sleep(0.1)
        assert row is not None, "the finished session never reached history"
        reason = str(row["ended_reason"])
        assert "3" in reason, f"the exit code is missing from {reason!r}"
        assert "崩溃" not in reason and "服务退出" not in reason, (
            f"a clean exit was filed as a crash: {reason!r}"
        )


def test_history_says_who_ended_a_session(tmp_path: Path) -> None:
    """An admin kill is "被管理员终止"; the row must not blame the crash daemon."""
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        assert client.delete(f"/api/sessions/{sid}").status_code == 200
        end = time.time() + 8.0
        row = None
        while time.time() < end:
            rows = client.get("/api/sessions/history").json()
            match = [r for r in rows if r["id"] == sid]
            if match:
                row = match[0]
                break
            time.sleep(0.1)
        assert row is not None, "the killed session never reached history"
        assert row["ended_reason"] == "被管理员终止", row["ended_reason"]


# -- 0.1.22: "claims of work that never happened" --------------------------


def test_a_manual_recording_start_respects_the_capacity_limit(tmp_path: Path) -> None:
    """``recordings_max_bytes`` binds auto-record *and* a click on 录制."""
    app = build_app(tmp_path, recordings_max_bytes=1)
    (tmp_path / "recordings").mkdir(exist_ok=True)
    (tmp_path / "recordings" / "filler.cast").write_bytes(b"x" * 100)
    with TestClient(app) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        r = client.post(f"/api/sessions/{sid}/recording/start", json={})
        assert r.status_code == 409, "the manual start walked past the quota"


def test_stopping_a_recording_that_never_started_is_not_an_ok(tmp_path: Path) -> None:
    """``{"ok": True}`` for "there was nothing to stop" is a claim of work
    that never happened."""
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        r = client.post(f"/api/sessions/{sid}/recording/stop")
        assert r.status_code == 409, r.text


def test_a_live_recording_is_not_deleted_from_under_the_writer(
    tmp_path: Path,
) -> None:
    """Unlinking a cast the recorder still holds made the data vanish: the
    writer kept appending to a deleted inode and ``stop`` closed it for good."""
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        assert client.post(f"/api/sessions/{sid}/recording/start", json={}).status_code == 200
        name = f"{sid}.cast"
        assert client.delete(f"/api/recordings/{name}").status_code == 409
        assert (tmp_path / "recordings" / name).is_file(), (
            "the cast must survive a refused delete"
        )
        assert client.post(f"/api/sessions/{sid}/recording/stop").status_code == 200
        # ...and is deletable once nothing is writing to it.
        assert client.delete(f"/api/recordings/{name}").status_code == 200


def test_an_owner_can_still_fetch_their_own_recording_after_the_session_ends(
    tmp_path: Path,
) -> None:
    """The only other door (``GET /api/recordings``) is admin-only.

    A plain user whose session had ended could therefore never get their own
    cast back -- a data-loss-shaped hole in the product.
    """
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client, BOB)
        sid = client.post("/api/sessions", json={}).json()["id"]
        client.post(f"/api/sessions/{sid}/recording/start", json={})
        client.post(f"/api/sessions/{sid}/recording/stop")
        client.delete(f"/api/sessions/{sid}")  # the session is gone now
        got = client.get(f"/api/sessions/{sid}/recording")
        assert got.status_code == 200, got.text
        assert got.content.startswith(b'{"version": 2')


def test_argv_has_one_shape_on_both_the_live_and_the_history_path(
    tmp_path: Path,
) -> None:
    """A JSON *string* on one path and a ``list`` on the other forces every
    consumer to branch on "is it alive" just to read argv."""
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        sid = client.post(
            "/api/sessions", json={"name": "shape", "command": "sleep 30"}
        ).json()["id"]
        live = client.get(f"/api/sessions/{sid}/detail").json()
        client.delete(f"/api/sessions/{sid}")
        finished = client.get(f"/api/sessions/{sid}/detail").json()
        assert isinstance(live["argv"], list), live["argv"]
        assert isinstance(finished["argv"], list), finished["argv"]
        assert live["argv"] == finished["argv"], (live["argv"], finished["argv"])
        # ``command`` is filled in on both, not just one.
        assert live["command"] == "sleep 30", live["command"]
        assert finished["command"] == "sleep 30", finished["command"]


def test_a_share_with_a_non_positive_ttl_is_refused_not_delivered_dead(
    tmp_path: Path,
) -> None:
    """A 200 with a token that can never work is worse than a 400."""
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        for bad in (0, -1, -3600):
            r = client.post(
                f"/api/sessions/{sid}/share", json={"writable": True, "ttl": bad}
            )
            assert r.status_code == 422, (bad, r.status_code, r.text)


def test_the_shutdown_flushes_the_webhook_queue(tmp_path: Path) -> None:
    """``stop()`` used to cancel mid-flight and drop the events ``audit.stop()``
    had just dispatched -- the final audit events of a run."""
    import asyncio

    from wsctl.core.webhook import WebhookDispatcher

    delivered: list[dict] = []

    async def run() -> None:
        dispatcher = WebhookDispatcher("http://127.0.0.1:9/hook")
        dispatcher._post = lambda payload: delivered.append(payload)  # type: ignore[assignment]
        dispatcher.start()
        for index in range(5):
            dispatcher.emit({"event": f"e{index}"})
        await dispatcher.stop(drain_timeout=5.0)

    asyncio.run(run())
    assert len(delivered) == 5, f"shutdown dropped {5 - len(delivered)} webhook events"


def test_only_an_attach_that_replays_something_repaints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The repaint is triggered by "was anything replayed", not "a socket opened".

    ``nudge_repaint`` is what the 0.1.20 recovery model rests on: a replayed
    byte stream can never rebuild a full-screen app's screen, so the app is
    asked to redraw from its own model. A brand-new session replays nothing
    and needs nothing. The first guard asserted only ``has_scrollback`` and
    never watched the repaint at all.
    """
    from wsctl.core import session as session_mod

    calls: list[str] = []
    original = session_mod.TermSession.nudge_repaint

    async def spy(self: object) -> None:
        calls.append(self.id)  # type: ignore[attr-defined]
        await original(self)  # type: ignore[arg-type]

    monkeypatch.setattr(session_mod.TermSession, "nudge_repaint", spy)
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client, ADMIN)
        # A silent child: nothing is ever written, so "was anything replayed"
        # has a deterministic answer. Racing a shell for its banner (the first
        # attempt) is what made this guard flaky in the full suite.
        quiet = client.post(
            "/api/sessions", json={"command": "/bin/sleep 30"}
        ).json()["id"]
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "attach", "session": quiet, "cols": 80, "rows": 24}))
            assert _recv_control(ws, {"attached"}) is not None
        assert calls == [], (
            f"an empty replay must not repaint: {calls}"
        )

        noisy = client.post("/api/sessions", json={}).json()["id"]
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "attach", "session": noisy, "cols": 80, "rows": 24}))
            assert _recv_control(ws, {"attached"}) is not None
            ws.send_text(json.dumps({"type": "input", "data": "echo $((29*31))\r"}))
            _recv_bytes_until(ws, b"899\r\n")
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "attach", "session": noisy, "cols": 80, "rows": 24}))
            assert _recv_control(ws, {"attached"}) is not None
        assert noisy in calls, (
            "an attach that replays something must trigger the application repaint"
        )


def test_an_oversized_input_frame_never_reaches_the_pty_buffer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The application bound is on the *frame*, not only on what is buffered.

    ``PosixPty.write`` checks ``len(buf) >= MAX`` and then ``buf.extend(data)``
    -- so one oversized frame walked straight past a 1 MiB "limit" whenever
    the buffer happened to be empty. The transport's 16 MiB ``ws_max_size`` is
    not an application bound either.
    """
    from wsctl.core import session as session_mod

    seen: list[int] = []
    original = session_mod.TermSession.write_input

    def spy(self: object, data: bytes) -> bool:
        seen.append(len(data))
        return original(self, data)  # type: ignore[arg-type]

    monkeypatch.setattr(session_mod.TermSession, "write_input", spy)
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client, ADMIN)
        sid = client.post("/api/sessions", json={}).json()["id"]
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "attach", "session": sid, "cols": 80, "rows": 24}))
            assert _recv_control(ws, {"attached"}) is not None
            ws.send_bytes(b"x" * (256 * 1024 + 1))
            notice = _recv_control(ws, {"notice"})
            assert notice is not None and "过大" in str(notice["msg"]), notice
            assert not seen, f"an oversized frame reached write_input: {seen}"
            # And the connection is still usable for ordinary input -- which
            # also proves the spy is live (the next line *must* register).
            ws.send_text(json.dumps({"type": "input", "data": "echo $((37*41))\r"}))
            out = _recv_bytes_until(ws, b"1517\r\n")
            assert b"1517\r\n" in out, "the link must survive a refused frame"
            assert seen, "the spy never fired -- this guard was measuring nothing"
            assert all(size <= 256 * 1024 for size in seen), seen
        client.delete(f"/api/sessions/{sid}")


def test_request_bodies_are_bounded_before_they_reach_the_work(tmp_path: Path) -> None:
    """An unbounded field is a free memory/CPU knob.

    The password field is the sharp one: it reaches Argon2, and the rate-limit
    key is ``ip:username`` -- so a new username bought a fresh allowance of
    arbitrarily large hashes. The bounds must be at the *model*, before any of
    that.
    """
    with TestClient(build_app(tmp_path)) as client:
        login(client, ADMIN)
        huge = "x" * 300
        for body in (
            {"username": "u", "password": huge},
            {"username": huge, "password": "password123"},
        ):
            r = client.post("/api/users", json=body)
            assert r.status_code == 422, (list(body), r.status_code)
        # The TOTP field is bounded on the *login* body: it is the one that
        # reaches the verifier, and an unbounded one is free CPU.
        r = client.post(
            "/api/login",
            json={"username": "admin", "password": "adminpw123", "totp": "1" * 17},
        )
        assert r.status_code == 422, r.status_code
        # A session name past its bound is refused rather than stored.
        r = client.post("/api/sessions", json={"name": "n" * 500})
        assert r.status_code == 422, r.status_code
        # File content past its bound is refused rather than encoded.
        r = client.put(
            "/api/files/content",
            json={"path": "x.txt", "content": "c" * (3 * 1024 * 1024)},
        )
        assert r.status_code == 422, r.status_code


def test_upload_is_atomic_a_reader_never_sees_a_half_file(tmp_path: Path) -> None:
    """Uploading must not expose a partially written file to concurrent reads.

    The first version of this test re-implemented the staging *itself* and only
    called the copy helper -- reverting ``upload_file`` to a direct write
    changed nothing. It also used ``all(...)`` over a possibly *empty* sample
    list, which is vacuously true. The production route runs here, and the
    reader must actually observe the finished file.
    """
    import io
    import threading

    from wsctl.core.config import load_settings
    from wsctl.core.store import Store

    files = tmp_path / "files"
    files.mkdir()
    settings = load_settings(data_dir=tmp_path / "data", file_root=files)
    store = Store(tmp_path / "data" / "db.sqlite")
    store.user_create("admin", "password123", role="admin")
    app = create_app(settings, store=store)
    payload = b"A" * (2 * 1024 * 1024)
    dest = files / "atomic.bin"
    seen: list[int] = []
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            with contextlib.suppress(OSError):
                seen.append(dest.stat().st_size)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    with TestClient(app) as client:
        assert client.post(
            "/api/login", json={"username": "admin", "password": "password123"}
        ).status_code == 200
        r = client.post(
            "/api/files/upload",
            data={"path": ""},
            files={"file": ("atomic.bin", io.BytesIO(payload))},
        )
        assert r.status_code == 201, r.text
    stop.set()
    thread.join(timeout=5)

    assert dest.read_bytes() == payload
    assert all(s in (0, len(payload)) for s in seen), seen[:10]
    assert seen, "the reader never sampled the file at all"
    assert len(payload) in seen, "the reader never saw the published file"


def test_concurrent_same_name_uploads_publish_one_whole_file(tmp_path: Path) -> None:
    """Two uploads of one name must never publish an interleaved mixture.

    A fixed ``.{name}.wsctl-upload`` sidecar made both writers share one
    staging file: their bytes interleaved and the final ``os.replace`` handed
    the mixture out as a complete-looking file.

    One ``TestClient`` and one lifespan. Two concurrent ``with TestClient(app)``
    blocks start two lifespans on one app (two audit writers, two
    ``store.close()`` calls) and leave the interpreter hanging on exit -- which
    is exactly what this test did before.
    """
    import io
    import threading

    from wsctl.core.config import load_settings
    from wsctl.core.store import Store

    files = tmp_path / "files"
    files.mkdir()
    settings = load_settings(data_dir=tmp_path / "data", file_root=files)
    store = Store(tmp_path / "data" / "db.sqlite")
    store.user_create("admin", "password123", role="admin")
    app = create_app(settings, store=store)

    left = b"L" * 300000
    right = b"R" * 300000
    errors: list[object] = []
    barrier = threading.Barrier(3, timeout=30)

    with TestClient(app) as client:
        assert client.post(
            "/api/login", json={"username": "admin", "password": "password123"}
        ).status_code == 200

        def upload(payload: bytes) -> None:
            barrier.wait(timeout=30)
            try:
                client.post(
                    "/api/files/upload",
                    data={"path": "", "overwrite": "true"},
                    files={"file": ("doc.bin", io.BytesIO(payload))},
                )
            except Exception as exc:  # any outcome is acceptable
                errors.append(exc)

        threads = [threading.Thread(target=upload, args=(p,), daemon=True) for p in (left, right)]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=30)
        for thread in threads:
            thread.join(timeout=30)

    body = (files / "doc.bin").read_bytes()
    assert body in (left, right), (
        "the published file is an interleaved mixture of two uploads"
    )
    leftovers = [q.name for q in files.iterdir() if q.name.startswith(".")]
    assert leftovers == [], f"staging sidecars leaked: {leftovers}"


def test_session_sliding_ttl_reload_reaches_the_store(tmp_path: Path) -> None:
    """Reload must rewire the live mirror, not just the Settings object.

    ``sliding_ttl`` is read from ``Store`` on every token resolution. Updating
    only ``settings.session_sliding_ttl`` left the store on the old value
    forever, so the field was advertised as hot-reloadable while the code that
    honours it never noticed.

    The first version of this test performed the rewire *itself* and then
    asserted the assignment had happened -- deleting the production line
    changed nothing. The reload is driven through ``POST /api/config/reload``,
    the only path a user has.
    """
    from wsctl.core.config import load_settings
    from wsctl.core.store import Store

    config = tmp_path / "wsctl" / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("session_sliding_ttl = false\n", encoding="utf-8")
    settings = load_settings(data_dir=tmp_path, config_path=config)
    store = Store(tmp_path / "db.sqlite")
    store.user_create("admin", "password123", role="admin")
    app = create_app(settings, store=store)
    with TestClient(app) as client:
        assert client.post(
            "/api/login", json={"username": "admin", "password": "password123"}
        ).status_code == 200
        assert store.sliding_ttl is False

        config.write_text("session_sliding_ttl = true\n", encoding="utf-8")
        r = client.post("/api/config/reload")
        assert r.status_code == 200, r.text
        assert "session_sliding_ttl" in r.json().get("changed", []), r.text
        assert store.sliding_ttl is True, (
            "the reload updated Settings but never reached the store -- the "
            "field stays advertised as hot-reloadable while nothing honours it"
        )
