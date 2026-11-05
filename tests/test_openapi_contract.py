from __future__ import annotations

import json
from pathlib import Path

from wsctl.core.config import load_settings
from wsctl.core.session import SessionManager
from wsctl.core.store import Store
from wsctl.server.app import create_app

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"


def _app(tmp_path: Path):
    settings = load_settings(
        data_dir=tmp_path,
        auth_required=False,
        default_shell="/bin/sh",
        cookie_secure=False,
    )
    return create_app(
        settings, store=Store(tmp_path / "openapi.db"), manager=SessionManager()
    )


def test_openapi_is_31_and_every_operation_has_a_unique_id(tmp_path: Path) -> None:
    app = _app(tmp_path)
    document = app.openapi()
    assert document["openapi"].startswith("3.1")
    identifiers: list[str] = []
    for path, methods in document["paths"].items():
        assert path.startswith(("/api/", "/health", "/readyz", "/metrics")), path
        for method, operation in methods.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            assert operation.get("operationId"), f"{method.upper()} {path} has no operationId"
            identifiers.append(operation["operationId"])
    assert len(identifiers) == len(set(identifiers)), "operationIds must be unique"


def test_canonical_v2_and_auth_aliases_are_in_the_schema(tmp_path: Path) -> None:
    app = _app(tmp_path)
    paths = app.openapi()["paths"]
    for expected in (
        "/api/v2/sessions",
        "/api/v2/sessions/{sid}",
        "/api/v2/auth/login",
        "/api/v2/config",
        "/api/v2/events",
        "/api/v2/audit/verify",
    ):
        assert expected in paths, expected


def test_generated_typescript_schema_tracks_the_backend(tmp_path: Path) -> None:
    schema = (WEB / "src" / "api" / "schema.d.ts").read_text(encoding="utf-8")
    assert "export interface paths" in schema
    assert '"/api/v2/sessions"' in schema
    assert '"/api/v2/auth/login"' in schema
    assert '"/api/v2/audit/verify"' in schema


def test_committed_openapi_document_is_valid_json(tmp_path: Path) -> None:
    document = json.loads((WEB / "openapi.json").read_text(encoding="utf-8"))
    assert document["openapi"].startswith("3.1")
    assert "/api/v2/sessions" in document["paths"]
