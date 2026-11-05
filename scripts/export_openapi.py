#!/usr/bin/env python3
"""Export the FastAPI OpenAPI document for the generated TypeScript client.

Run from the repository root (or via ``pnpm api:generate`` in ``web/``)::

    python scripts/export_openapi.py --output web/openapi.json
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from wsctl.core.config import load_settings
from wsctl.core.session import SessionManager
from wsctl.core.store import Store
from wsctl.server.app import create_app

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="wsctl-openapi-") as tmp:
        data = Path(tmp)
        settings = load_settings(
            data_dir=data,
            auth_required=False,
            default_shell="/bin/sh",
            cookie_secure=False,
        )
        app = create_app(
            settings,
            store=Store(data / "openapi.db"),
            manager=SessionManager(),
        )
        document = app.openapi()
    text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(text, end="")
    else:
        target = args.output if args.output.is_absolute() else ROOT / args.output
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"wrote {target} ({len(document.get('paths', {}))} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
