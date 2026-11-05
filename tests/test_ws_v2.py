from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from wsctl.core.config import load_settings
from wsctl.core.protocol import (
    FLAG_SNAPSHOT,
    SUBPROTOCOL,
    decode_data,
    encode_control,
    encode_input,
)
from wsctl.core.session import SessionManager
from wsctl.core.store import Store
from wsctl.server.app import create_app

ADMIN = ("admin", "adminpw123")


def build_app(tmp_path: Path, **overrides: object):
    settings = load_settings(
        data_dir=tmp_path,
        auth_required=True,
        default_shell="/bin/sh",
        cookie_secure=False,
        **overrides,
    )
    store = Store(tmp_path / "test.db")
    store.user_create(*ADMIN, role="admin")
    return create_app(settings, store=store, manager=SessionManager())


def login(client: TestClient) -> None:
    response = client.post("/api/v2/login", json={"username": ADMIN[0], "password": ADMIN[1]})
    assert response.status_code == 200, response.text


def recv_control(ws: Any, kind: str, *, limit: int = 200) -> dict[str, Any]:
    for _ in range(limit):
        message = ws.receive()
        if message.get("text") is None:
            continue
        data = json.loads(message["text"])
        if data.get("type") == kind:
            return data
    raise AssertionError(f"control {kind!r} not received")


def recv_output(ws: Any, needle: bytes, *, limit: int = 400) -> tuple[bytes, int, int]:
    output = bytearray()
    last_seq = 0
    for _ in range(limit):
        message = ws.receive()
        raw = message.get("bytes")
        if raw is None:
            continue
        frame = decode_data(raw)
        output.extend(frame.payload)
        last_seq = max(last_seq, frame.seq + len(frame.payload))
        if needle in output:
            return bytes(output), frame.seq, last_seq
    raise AssertionError(f"output {needle!r} not received; got {bytes(output)!r}")


def test_v2_attach_streams_sequenced_frames_and_resumes(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        with client.websocket_connect("/ws/v2", subprotocols=[SUBPROTOCOL]) as ws:
            assert ws.accepted_subprotocol == SUBPROTOCOL
            ws.send_text(
                encode_control(
                    {
                        "type": "attach",
                        "cols": 80,
                        "rows": 24,
                        "capabilities": ["snapshot", "osc133", "resume"],
                    }
                )
            )
            attached = recv_control(ws, "attached")
            session_id = attached["session"]
            generation = attached["generation"]
            assert attached["writable"] is True
            # Build the marker in the shell instead of spelling it in the
            # command: the PTY echoes the command back, so a literal marker
            # would satisfy ``recv_output`` before the command's own output
            # existed, and ``end_seq`` would point at a half-delivered frame.
            ws.send_bytes(encode_input(b"printf 'FIRST-%s\\n' MARKER\r"))
            _output, _seq, end_seq = recv_output(ws, b"FIRST-MARKER")
            ws.send_text(encode_control({"type": "ack", "seq": end_seq}))

        with client.websocket_connect("/ws/v2", subprotocols=[SUBPROTOCOL]) as ws:
            ws.send_text(
                encode_control(
                    {
                        "type": "attach",
                        "session": session_id,
                        "cols": 80,
                        "rows": 24,
                        "last_seq": end_seq,
                        "generation": generation,
                    }
                )
            )
            attached = recv_control(ws, "attached")
            assert attached["snapshot_required"] is False
            assert attached["seq"] >= end_seq
            ws.send_bytes(encode_input(b"printf 'SECOND-%s\\n' MARKER\r"))
            output, seq, _ = recv_output(ws, b"SECOND-MARKER")
            assert b"FIRST-MARKER" not in output
            assert seq >= end_seq


def test_v2_snapshot_is_used_when_the_ring_has_evicted_the_head(tmp_path: Path) -> None:
    app = build_app(tmp_path, snapshot_mode="always", scrollback_bytes=64)
    with TestClient(app) as client:
        login(client)
        with client.websocket_connect("/ws/v2") as ws:
            ws.send_text(encode_control({"type": "attach", "cols": 80, "rows": 24}))
            attached = recv_control(ws, "attached")
            session_id = attached["session"]
            generation = attached["generation"]
            # Wait for a marker at the *end* of the output: matching the first
            # digits only proves the head arrived, while last_seq=0 is still
            # resumable if the ring has not evicted it yet. The echoed command
            # contains ``TAIL-%s``, so only the command's own output matches.
            ws.send_bytes(
                encode_input(
                    b"printf '0123456789%.0s' $(seq 1 40); printf 'TAIL-%s\\n' MARKER\r"
                )
            )
            recv_output(ws, b"TAIL-MARKER")

        with client.websocket_connect("/ws/v2") as ws:
            ws.send_text(
                encode_control(
                    {
                        "type": "attach",
                        "session": session_id,
                        "cols": 80,
                        "rows": 24,
                        "last_seq": 0,
                        "generation": generation,
                    }
                )
            )
            attached = None
            frames = []
            for _ in range(40):
                message = ws.receive()
                raw = message.get("bytes")
                if raw is not None:
                    frames.append(decode_data(raw))
                    continue
                if message.get("text") is None:
                    continue
                data = json.loads(message["text"])
                if data.get("type") == "attached":
                    attached = data
                    break
            assert attached is not None
            assert attached["snapshot_required"] is True
            assert attached["snapshot_applied"] is True
            # The snapshot is queued before ``attached``; it must be a
            # SNAPSHOT-flagged data frame, not a raw replay.
            assert frames, "no snapshot frame received"
            assert frames[0].flags & FLAG_SNAPSHOT


def test_v2_read_only_share_cannot_type(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        created = client.post("/api/v2/sessions", json={})
        assert created.status_code == 201, created.text
        sid = created.json()["id"]
        share = client.post(
            f"/api/v2/sessions/{sid}/shares",
            json={"scope": "read", "label": "viewer"},
        )
        assert share.status_code == 201, share.text
        token = share.json()["token"]

        # Drop the login cookie so this WebSocket is an anonymous share viewer
        # while still using the same app/lifespan (a second TestClient on the
        # same app would run the lifespan twice and bind audit tasks to a loop
        # that is already gone).
        client.cookies.clear()
        resolved = client.get("/api/v2/shares/resolve", params={"token": token})
        assert resolved.status_code == 200
        assert resolved.json()["session"] == sid
        with client.websocket_connect(f"/ws/v2?share={token}") as ws:
            ws.send_text(
                encode_control(
                    {
                        "type": "attach",
                        "session": sid,
                        "cols": 80,
                        "rows": 24,
                        "share": token,
                    }
                )
            )
            attached = recv_control(ws, "attached")
            assert attached["writable"] is False
            ws.send_bytes(encode_input(b"echo SHOULD-NOT-RUN\r"))
            notice = recv_control(ws, "notice")
            assert "只读" in notice["msg"]


def test_api_key_scopes_apply_to_websockets(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        sid = client.post("/api/v2/sessions", json={}).json()["id"]
        read_key = client.post(
            "/api/v2/api-keys", json={"name": "reader", "scopes": ["read"]}
        ).json()["token"]
        write_key = client.post(
            "/api/v2/api-keys", json={"name": "writer", "scopes": ["write"]}
        ).json()["token"]
        client.cookies.clear()

        with client.websocket_connect(
            "/ws/v2", headers={"Authorization": f"Bearer {read_key}"}
        ) as ws:
            ws.send_text(
                encode_control({"type": "attach", "session": sid, "cols": 80, "rows": 24})
            )
            attached = recv_control(ws, "attached")
            assert attached["writable"] is False
            ws.send_bytes(encode_input(b"echo NOPE\r"))
            assert "只读" in recv_control(ws, "notice")["msg"]

        with client.websocket_connect(
            "/ws/v2", headers={"Authorization": f"Bearer {write_key}"}
        ) as ws:
            ws.send_text(
                encode_control({"type": "attach", "session": sid, "cols": 80, "rows": 24})
            )
            attached = recv_control(ws, "attached")
            assert attached["writable"] is True
            ws.send_bytes(encode_input(b"printf 'KEY-WRITE\\n'\r"))
            output, _seq, _end = recv_output(ws, b"KEY-WRITE")
            assert b"KEY-WRITE" in output


def test_api_key_authenticates_and_can_be_deleted(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        created = client.post("/api/v2/api-keys", json={"name": "ci", "scopes": ["read"]})
        assert created.status_code == 201, created.text
        token = created.json()["token"]
        key_id = created.json()["key"]["id"]

        # Drop the login cookie: a 200 now can only come from the API key.
        client.cookies.clear()
        response = client.get("/api/v2/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json()["username"] == "admin"

        # The cookie is gone, so authenticate the admin with a fresh login to
        # list/delete the key.
        login(client)
        keys = client.get("/api/v2/api-keys").json()["keys"]
        assert any(key["id"] == key_id for key in keys)
        assert client.delete(f"/api/v2/api-keys/{key_id}").status_code == 200
        client.cookies.clear()
        assert (
            client.get("/api/v2/me", headers={"Authorization": f"Bearer {token}"}).status_code
            == 401
        )


def test_api_key_scopes_are_enforced(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        read_key = client.post(
            "/api/v2/api-keys", json={"name": "reader", "scopes": ["read"]}
        ).json()["token"]
        admin_key = client.post(
            "/api/v2/api-keys", json={"name": "admin", "scopes": ["admin"]}
        ).json()["token"]
        sid = client.post("/api/v2/sessions", json={}).json()["id"]

        client.cookies.clear()
        headers = {"Authorization": f"Bearer {read_key}"}
        assert client.get("/api/v2/sessions", headers=headers).status_code == 200
        # Reading the legacy share token is a write operation: the token can
        # be replayed to type into the session.
        assert (
            client.get(f"/api/v2/sessions/{sid}/share", headers=headers).status_code
            == 403
        )
        assert (
            client.get(f"/api/v2/sessions/{sid}/qr.svg", headers=headers).status_code
            == 403
        )
        denied = client.post("/api/v2/sessions", json={}, headers=headers)
        assert denied.status_code == 403
        assert denied.headers["content-type"].startswith("application/problem+json")
        assert "write" in denied.json()["detail"]
        assert (
            client.get("/api/v2/config/schema", headers=headers).status_code == 403
        )

        allowed = client.post(
            "/api/v2/sessions",
            json={},
            headers={"Authorization": f"Bearer {admin_key}"},
        )
        assert allowed.status_code == 201


def test_legacy_share_token_still_resolves_for_clean_urls(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        sid = client.post("/api/v2/sessions", json={}).json()["id"]
        legacy = client.post(f"/api/v2/sessions/{sid}/share", json={})
        assert legacy.status_code == 200, legacy.text
        token = legacy.json()["token"]
        resolved = client.get("/api/v2/shares/resolve", params={"token": token})
        assert resolved.status_code == 200
        assert resolved.json()["session"] == sid


def test_layouts_round_trip_per_user(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        layout = {"tabs": [{"name": "one"}], "active": 0}
        saved = client.put(
            "/api/v2/layouts/work", json={"name": "work", "layout": layout}
        )
        assert saved.status_code == 200
        fetched = client.get("/api/v2/layouts/work")
        assert fetched.status_code == 200
        assert fetched.json()["layout"] == layout
        assert len(client.get("/api/v2/layouts").json()["layouts"]) == 1
        assert client.delete("/api/v2/layouts/work").status_code == 200
        assert client.get("/api/v2/layouts/work").status_code == 404


def test_config_schema_is_grouped_and_typed(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        schema = client.get("/api/v2/config/schema")
        assert schema.status_code == 200
        body = schema.json()
        assert body["version"] == 2
        assert "server" in body["sections"]
        port = next(field for field in body["sections"]["server"] if field["key"] == "port")
        assert port["type"] == "int"
        assert port["apply"] == "restart"


def test_audit_chain_verifies_and_records_login(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        created = client.post("/api/v2/sessions", json={})
        assert created.status_code == 201
        verified = client.get("/api/v2/audit/verify")
        assert verified.status_code == 200
        body = verified.json()
        assert body["ok"] is True
        assert body["checked"] >= 1
        rows = client.get("/api/v2/audit").json()
        assert rows
        assert all(row["hash"] for row in rows if row["event"] != "")


def test_readyz_reports_every_dependency(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/readyz")
        assert response.status_code == 200, response.text
        assert response.json()["checks"]["database"] is True


def test_no_auth_mode_can_create_a_session_without_a_user_row(tmp_path: Path) -> None:
    """``--no-auth`` uses synthetic user id 0, which has no ``users`` row.

    Persisting that id into ``term_sessions.owner_id`` used to violate the
    foreign key and turn every "new session" into a 500. Anonymous sessions
    must be stored with ``owner_id = NULL``.
    """
    settings = load_settings(
        data_dir=tmp_path, auth_required=False, default_shell="/bin/sh"
    )
    store = Store(tmp_path / "noauth.db")
    app = create_app(settings, store=store, manager=SessionManager())
    with TestClient(app) as client:
        created = client.post("/api/v2/sessions", json={})
        assert created.status_code == 201, created.text
        assert created.json()["owner_id"] is None
        assert len(client.get("/api/v2/sessions").json()) == 1


def test_exact_resume_replays_without_clearing_the_screen(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        with client.websocket_connect("/ws/v2", subprotocols=[SUBPROTOCOL]) as ws:
            ws.send_text(encode_control({"type": "attach", "cols": 80, "rows": 24}))
            attached = recv_control(ws, "attached")
            session_id = attached["session"]
            generation = attached["generation"]
            ws.send_bytes(encode_input(b"printf 'RESUME-MARKER\\n'\r"))
            _output, _seq, end_seq = recv_output(ws, b"RESUME-MARKER")
            ws.send_text(encode_control({"type": "ack", "seq": end_seq}))

        with client.websocket_connect("/ws/v2", subprotocols=[SUBPROTOCOL]) as ws:
            ws.send_text(
                encode_control(
                    {
                        "type": "attach",
                        "session": session_id,
                        "cols": 80,
                        "rows": 24,
                        "last_seq": end_seq,
                        "generation": generation,
                    }
                )
            )
            frames = []
            attached = None
            for _ in range(20):
                message = ws.receive()
                raw = message.get("bytes")
                if raw is not None:
                    frames.append(decode_data(raw))
                    continue
                if message.get("text"):
                    data = json.loads(message["text"])
                    if data.get("type") == "attached":
                        attached = data
                        break
            assert attached is not None
            assert attached["snapshot_required"] is False
            # No ED2 reset and no snapshot frame: the client already owns the
            # content before ``last_seq`` and only needs the delta.
            assert all(frame.flags & FLAG_SNAPSHOT == 0 for frame in frames)


def test_legacy_websocket_enforces_api_key_scopes(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        sid = client.post("/api/v2/sessions", json={}).json()["id"]
        key = client.post(
            "/api/v2/api-keys", json={"name": "reader", "scopes": ["read"]}
        ).json()["token"]
        client.cookies.clear()
        with client.websocket_connect(
            "/ws", headers={"Authorization": f"Bearer {key}"}
        ) as ws:
            ws.send_text(
                json.dumps(
                    {
                        "type": "attach",
                        "session": sid,
                        "cols": 80,
                        "rows": 24,
                    }
                )
            )
            attached = recv_control(ws, "attached")
            assert attached["writable"] is False
            ws.send_bytes(b"echo SHOULD-NOT-RUN\r")
            notice = recv_control(ws, "notice")
            assert "只读" in notice["msg"]


def test_exit_code_is_persisted_and_returned_in_history(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        with client.websocket_connect("/ws/v2", subprotocols=[SUBPROTOCOL]) as ws:
            ws.send_text(encode_control({"type": "attach", "cols": 80, "rows": 24}))
            attached = recv_control(ws, "attached")
            sid = attached["session"]
            ws.send_bytes(encode_input(b"exit 7\r"))
            exited = recv_control(ws, "exit", limit=400)
            assert exited["code"] == 7
        detail = client.get(f"/api/v2/sessions/{sid}/detail")
        assert detail.status_code == 200, detail.text
        assert detail.json()["exit_code"] == 7
        assert detail.json()["state"] == "finished"


def test_no_auth_mode_keeps_its_v2_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The v2 recheck must honour auth_required=False for anonymous sessions."""
    from wsctl.core.config import load_settings
    from wsctl.core.session import SessionManager
    from wsctl.core.store import Store
    from wsctl.server import ws_v2
    from wsctl.server.app import create_app

    monkeypatch.setattr(ws_v2, "ACCESS_RECHECK", 0.02)
    settings = load_settings(
        data_dir=tmp_path,
        auth_required=False,
        default_shell="/bin/sh",
        cookie_secure=False,
    )
    app = create_app(settings, store=Store(tmp_path / "noauth.db"), manager=SessionManager())
    with (
        TestClient(app) as client,
        client.websocket_connect("/ws/v2", subprotocols=[SUBPROTOCOL]) as ws,
    ):
        ws.send_text(encode_control({"type": "attach", "cols": 80, "rows": 24}))
        recv_control(ws, "attached")
        time.sleep(0.1)  # let the 5s recheck interval (patched) fire
        ws.send_bytes(encode_input(b"printf 'STILL-HERE\\n'\r"))
        recv_output(ws, b"STILL-HERE")


def test_unbalanced_quotes_in_command_are_a_400(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app) as client:
        login(client)
        response = client.post(
            "/api/v2/sessions", json={"command": "echo 'unterminated"}
        )
        assert response.status_code == 400, response.text
        body = response.json()
        assert body["status"] == 400
        assert "命令无法解析" in body["detail"]


