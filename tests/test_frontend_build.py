"""Contract tests for the 0.2.0 frontend build.

0.1.x protected frontend design decisions by parsing ``static/app.js`` as text.
That made every component extraction a test failure and told you nothing about
whether the UI worked. 0.2.0 replaces those 59 string contracts with:

* Vitest unit tests (run in ``web/`` by ``pnpm test``);
* Playwright browser tests;
* these small *build* contracts, which catch the packaging mistakes a unit
  test cannot see (a stale bundle, a missing lockfile, an inline script that
  the CSP would block).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
STATIC = ROOT / "src" / "wsctl" / "static"


def test_the_old_hand_written_bundle_is_gone() -> None:
    assert not (STATIC / "app.js").exists()
    assert not (STATIC / "app.css").exists()
    assert not (STATIC / "vendor").exists()


def test_built_index_references_only_hashed_assets_and_has_no_inline_script() -> None:
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "/static/assets/" in index
    assert "app.js" not in index
    inline = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", index, re.S)
    assert inline == [], "an inline script would be blocked by script-src 'self'"
    for match in re.finditer(r'(?:src|href)="(/static/[^"]+)"', index):
        assert (STATIC / match.group(1)[len("/static/") :]).is_file()


def test_entry_document_carries_subresource_integrity() -> None:
    import base64
    import hashlib

    index = (STATIC / "index.html").read_text(encoding="utf-8")
    tags = re.findall(
        r'<(?:script|link)[^>]+(?:src|href)="(/static/assets/[^"]+)"[^>]+integrity="sha384-([^"]+)"',
        index,
    )
    assert tags, "no hashed asset has an integrity attribute"
    for relative, digest in tags:
        content = (STATIC / relative[len("/static/") :]).read_bytes()
        expected = base64.b64encode(hashlib.sha384(content).digest()).decode("ascii")
        assert digest == expected, f"SRI mismatch for {relative}"


def test_generated_api_schema_and_openapi_document_are_committed() -> None:
    assert (WEB / "openapi.json").is_file()
    schema = (WEB / "src" / "api" / "schema.d.ts").read_text(encoding="utf-8")
    assert "export interface paths" in schema
    assert "/api/v2/sessions" in schema
    assert (WEB / "scripts" / "generate-api.mjs").is_file()


def test_frontend_has_a_real_lint_and_format_pipeline() -> None:
    package = json.loads((WEB / "package.json").read_text(encoding="utf-8"))
    dev = package["devDependencies"]
    for dependency in ("eslint", "eslint-plugin-vue", "typescript-eslint", "prettier"):
        assert dependency in dev, dependency
    assert (WEB / "eslint.config.js").is_file()
    assert (WEB / ".prettierrc.json").is_file()


def test_built_css_contains_the_design_tokens() -> None:
    css_files = list((STATIC / "assets").glob("*.css"))
    assert css_files, "Vite produced no CSS"
    combined = "\n".join(path.read_text(encoding="utf-8") for path in css_files)
    assert "--border-input" in combined
    assert "--bg-elevated" in combined
    assert ".ws-shell" in combined


def test_frontend_dependencies_are_locked() -> None:
    package = json.loads((WEB / "package.json").read_text(encoding="utf-8"))
    assert package["dependencies"]["vue"].startswith("^3.5")
    assert package["dependencies"]["@xterm/xterm"].startswith("^6")
    assert package["devDependencies"]["vite"].startswith("^8")
    assert (WEB / "pnpm-lock.yaml").is_file()


def test_initial_bundle_stays_under_budget() -> None:
    import gzip

    lazy = (
        "editor-",
        "replay-",
        "zmodem.js-",
        "AdminDialog-",
        "SettingsDialog-",
        "FilePanel-",
        "ShareDialog-",
    )
    total = sum(
        len(gzip.compress(path.read_bytes()))
        for path in (STATIC / "assets").glob("*.js")
        if not path.name.startswith(lazy)
    )
    assert total < 500 * 1024, f"initial JS is {total / 1024:.1f} KiB gzip"


def test_source_does_not_reintroduce_the_old_monolith() -> None:
    js = list((WEB / "src").rglob("*.js"))
    assert js == [], "0.2.0 sources are TypeScript/Vue, not plain JS"
    total = sum(
        len(path.read_text(encoding="utf-8").splitlines())
        for path in (WEB / "src").rglob("*")
        if path.suffix in {".ts", ".vue", ".css"} and "schema.d.ts" not in path.name
    )
    # A single 5887-line IIFE was the 0.1.x problem. A *directory* this size is
    # fine; one file this size is not. The generated OpenAPI schema is machine
    # output and is excluded: it has no hand-maintained structure to protect.
    biggest = max(
        len(path.read_text(encoding="utf-8").splitlines())
        for path in (WEB / "src").rglob("*")
        if path.suffix in {".ts", ".vue", ".css"} and "schema.d.ts" not in path.name
    )
    # The terminal viewer is the one legitimately large component (xterm
    # lifecycle, input, clipboard, ZMODEM, search, commands). Keep the guard
    # meaningful; split it before it approaches the old monolith.
    assert biggest < 1800, f"a single source file grew to {biggest} lines"
    assert total > 2000
