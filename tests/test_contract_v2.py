from __future__ import annotations

import errno
from pathlib import Path

from fastapi.testclient import TestClient

from test_ws_v2 import build_app, login


def test_errors_are_rfc9457_problem_details(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        response = client.get("/api/v2/sessions/does-not-exist/detail")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/problem+json")
        body = response.json()
        assert body["status"] == 404
        assert body["title"]
        assert body["detail"]
        assert body["instance"] == "/api/v2/sessions/does-not-exist/detail"
        assert body["request_id"]


def test_validation_errors_are_problem_details_with_fields(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        response = client.post("/api/v2/users", json={"username": "", "password": "x"})
        assert response.status_code == 422
        body = response.json()
        assert body["status"] == 422
        assert body["errors"]
        assert any("username" in item["field"] for item in body["errors"])


def test_auth_aliases_under_api_v2_auth(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        assert client.post(
            "/api/v2/auth/login", json={"username": "admin", "password": "adminpw123"}
        ).status_code == 200
        me = client.get("/api/v2/auth/me")
        assert me.status_code == 200
        assert me.json()["username"] == "admin"
        assert client.post("/api/v2/auth/logout").status_code == 200
        assert client.get("/api/v2/auth/me").status_code == 401


def test_config_get_supports_etag_and_conditional_get(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        first = client.get("/api/v2/config")
        assert first.status_code == 200
        etag = first.headers["etag"]
        second = client.get("/api/v2/config", headers={"If-None-Match": etag})
        assert second.status_code == 304
        assert second.content == b""
        assert second.headers["etag"] == etag


def test_static_assets_support_conditional_get(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        first = client.get("/static/index.html")
        assert first.status_code == 200
        etag = first.headers.get("etag")
        assert etag
        second = client.get("/static/index.html", headers={"If-None-Match": etag})
        assert second.status_code == 304


def test_idempotency_key_replays_the_same_session(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        headers = {"Idempotency-Key": "create-once"}
        first = client.post("/api/v2/sessions", json={}, headers=headers)
        assert first.status_code == 201, first.text
        second = client.post("/api/v2/sessions", json={}, headers=headers)
        assert second.status_code == 201
        assert second.headers["idempotent-replayed"] == "true"
        assert second.json()["id"] == first.json()["id"]
        assert len(client.get("/api/v2/sessions").json()) == 1

        conflict = client.post(
            "/api/v2/sessions", json={"name": "different"}, headers=headers
        )
        assert conflict.status_code == 409


def test_audit_cursor_pagination_does_not_repeat_rows(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        for index in range(5):
            client.post("/api/v2/sessions", json={"name": f"cursor-{index}"})
        first = client.get("/api/v2/audit", params={"limit": 3})
        assert first.status_code == 200
        cursor = first.headers.get("x-next-cursor")
        assert cursor
        second = client.get(
            "/api/v2/audit", params={"limit": 3, "cursor": cursor}
        )
        assert second.status_code == 200
        first_ids = {row["id"] for row in first.json()}
        second_ids = {row["id"] for row in second.json()}
        assert not (first_ids & second_ids)

        bad = client.get("/api/v2/audit", params={"cursor": "not-a-cursor"})
        assert bad.status_code == 400


def test_recordings_are_indexed_with_session_and_status(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        sid = client.post("/api/v2/sessions", json={}).json()["id"]
        started = client.post(
            f"/api/v2/sessions/{sid}/recording/start", json={"record_input": False}
        )
        assert started.status_code == 200, started.text
        stopped = client.post(f"/api/v2/sessions/{sid}/recording/stop")
        assert stopped.status_code == 200, stopped.text
        rows = client.get("/api/v2/recordings")
        assert rows.status_code == 200
        entry = next((row for row in rows.json() if row["session"] == sid), None)
        assert entry is not None
        assert entry["status"] == "finished"
        assert entry["size"] >= 0


def test_upload_reports_disk_full_as_507(    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    import wsctl.server.routes.files as files_routes

    def full(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(files_routes, "_copy_upload", full)
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        response = client.post(
            "/api/v2/files/upload",
            files={"file": ("full.txt", b"data", "text/plain")},
            data={"path": ""},
        )
        assert response.status_code == 507
        body = response.json()
        assert body["status"] == 507
        assert "空间" in body["detail"]


def test_sse_events_are_filtered_to_the_owner() -> None:
    from wsctl.core.store import User
    from wsctl.server.routes.v2 import _event_visible

    alice = User(id=2, username="alice", role="user", disabled=False, created_at=0.0)
    admin = User(id=1, username="admin", role="admin", disabled=False, created_at=0.0)
    owned = {"event": "presence", "owner_id": 2, "session": "abc"}
    other = {"event": "presence", "owner_id": 3, "session": "def"}
    assert _event_visible(owned, alice) is True
    assert _event_visible(other, alice) is False
    assert _event_visible(other, admin) is True
    assert _event_visible({"event": "heartbeat"}, alice) is True


def test_idempotency_claim_serialises_concurrent_requests(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app):
        db = app.state.db
        first = db.idempotency_claim(
            "race", method="POST", path="/sessions", request_hash="h"
        )
        assert first["state"] == "claimed"
        second = db.idempotency_claim(
            "race", method="POST", path="/sessions", request_hash="h"
        )
        assert second["state"] == "pending"
        db.idempotency_put(
            "race",
            method="POST",
            path="/sessions",
            status=201,
            body={"id": "one"},
            request_hash="h",
        )
        third = db.idempotency_claim(
            "race", method="POST", path="/sessions", request_hash="h"
        )
        assert third["state"] == "done"
        assert third["body"] == {"id": "one"}
        conflict = db.idempotency_claim(
            "race", method="POST", path="/sessions", request_hash="other"
        )
        assert conflict["state"] == "conflict"
        # A failed request releases its claim so the same key can retry.
        db.idempotency_claim("retry", method="POST", path="/sessions")
        db.idempotency_abort("retry")
        assert db.idempotency_get(
            "retry", method="POST", path="/sessions"
        ) is None


def test_idempotency_keys_are_scoped_per_user(tmp_path: Path) -> None:
    """One user's key must never replay another user's stored response."""
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        created = client.post(
            "/api/v2/users",
            json={"username": "bob", "password": "bobpw12345", "role": "user"},
        )
        assert created.status_code == 201, created.text
        headers = {"Idempotency-Key": "shared-key"}
        first = client.post("/api/v2/sessions", json={}, headers=headers)
        assert first.status_code == 201, first.text
        client.post("/api/v2/logout")
        assert client.post(
            "/api/v2/login", json={"username": "bob", "password": "bobpw12345"}
        ).status_code == 200
        second = client.post("/api/v2/sessions", json={}, headers=headers)
        assert second.status_code == 201, second.text
        assert second.json()["id"] != first.json()["id"]
        assert second.json()["owner"] == "bob"


def test_config_api_never_echoes_secrets(tmp_path: Path) -> None:
    app = build_app(tmp_path, web_push_vapid_private="TOP-SECRET-KEY")
    with TestClient(app) as client:
        login(client)
        config = client.get("/api/v2/config")
        assert config.status_code == 200
        assert "TOP-SECRET-KEY" not in config.text
        field = next(
            item for item in config.json()["fields"] if item["key"] == "web_push_vapid_private"
        )
        assert field["secret"] is True
        assert field["value"] == ""
        schema = client.get("/api/v2/config/schema")
        assert schema.status_code == 200
        assert "TOP-SECRET-KEY" not in schema.text


def test_authenticated_api_responses_are_no_store(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        response = client.get("/api/v2/sessions")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"


def test_push_endpoint_validation_rejects_cleartext_and_private_hosts() -> None:
    from wsctl.server.services.push import _endpoint_allowed

    assert _endpoint_allowed("https://fcm.googleapis.com/fcm/send/abc")
    assert not _endpoint_allowed("http://fcm.googleapis.com/fcm/send/abc")
    assert not _endpoint_allowed("https://127.0.0.1/push")
    assert not _endpoint_allowed("https://[::1]/push")
    assert not _endpoint_allowed("https://localhost/push")
    assert not _endpoint_allowed("https://printer.local/push")
    assert not _endpoint_allowed("https://user:pass@example.com/push")



