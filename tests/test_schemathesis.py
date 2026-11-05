from __future__ import annotations

from pathlib import Path

import schemathesis

from wsctl.core.config import load_settings
from wsctl.core.session import SessionManager
from wsctl.core.store import Store
from wsctl.server.app import create_app

#: Public GET endpoints: no login, no side effects, stable status contract.
#: This is deliberately a small set -- Schemathesis is here to prove the
#: OpenAPI document matches the running app, not to fuzz authenticated writes
#: in a unit suite (the e2e scenarios own that).
PUBLIC_GETS = {"/healthz", "/readyz", "/api/v2/push/vapid"}


def test_public_operations_accept_generated_cases(tmp_path: Path) -> None:
    settings = load_settings(
        data_dir=tmp_path,
        auth_required=False,
        default_shell="/bin/sh",
        cookie_secure=False,
    )
    app = create_app(
        settings, store=Store(tmp_path / "schema.db"), manager=SessionManager()
    )
    schema = schemathesis.openapi.from_asgi("/openapi.json", app)
    checked = 0
    for result in schema.get_all_operations():
        operation = result.ok()
        if operation.method != "get" or operation.path not in PUBLIC_GETS:
            continue
        case = operation.as_strategy().example()
        response = case.call()
        assert response.status_code < 500, (
            f"{operation.method.upper()} {operation.path} returned {response.status_code}"
        )
        checked += 1
    assert checked == len(PUBLIC_GETS), f"only checked {checked} operations"
