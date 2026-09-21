"""Browser-level end-to-end tests using Playwright.

Excluded from the default suite (marker ``browser``) and skipped when
Playwright is not installed. Run with::

    pip install "wsctl[e2e]"
    playwright install chromium
    pytest -m browser
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_api.sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
PORT = 7694
BASE = f"http://127.0.0.1:{PORT}"

pytestmark = pytest.mark.browser


def _start_server(data_dir: Path, files_dir: Path) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "WSCTL_DATA_DIR": str(data_dir),
        "WSCTL_FILE_ROOT": str(files_dir),
    }
    return subprocess.Popen(
        [
            PY, "-m", "wsctl", "serve",
            "--port", str(PORT), "--host", "127.0.0.1",
            "--admin-password", "testpass123", "--log-level", "warning",
        ],
        cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def _wait_health(timeout: float = 20.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get(f"{BASE}/healthz", timeout=1.0).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.2)
    raise AssertionError("server never became healthy")


def test_browser_flow(tmp_path: Path) -> None:
    data = tmp_path / "data"
    files = tmp_path / "files"
    files.mkdir(parents=True)
    (files / "seed.txt").write_text("hello from browser test")
    server = _start_server(data, files)
    try:
        _wait_health()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(viewport={"width": 1280, "height": 800})
            page = context.new_page()
            page.goto(BASE)

            # login
            assert page.locator("#login-overlay").is_visible()
            page.fill("#username", "admin")
            page.fill("#password", "testpass123")
            page.click("#login-form button[type=submit]")

            # a terminal tab appears and connects
            page.wait_for_selector(".tab", timeout=15000)
            page.wait_for_function(
                "() => document.getElementById('connection').textContent === 'connected'",
                timeout=15000,
            )
            assert page.locator(".tab").count() == 1

            # type into the terminal and see the echoed output
            page.click(".term-pane.active .xterm-screen")
            page.keyboard.type("echo BROWSER-OK")
            page.keyboard.press("Enter")
            page.wait_for_function(
                "() => { const r = document.querySelector('.term-pane.active .xterm-rows');"
                " return r && r.innerText.includes('BROWSER-OK'); }",
                timeout=15000,
            )

            # bundled addons are available
            assert page.evaluate("() => typeof window.ImageAddon") != "undefined"
            assert page.evaluate("() => typeof ImageAddon.ImageAddon") == "function"
            assert page.evaluate("() => typeof window.Zmodem") != "undefined"

            # enabling ZMODEM must not break normal terminal I/O
            page.click("#zmodem-btn")
            page.click(".term-pane.active .xterm-screen")
            page.keyboard.type("echo ZMODEM-ON-OK")
            page.keyboard.press("Enter")
            page.wait_for_function(
                "() => { const r = document.querySelector('.term-pane.active .xterm-rows');"
                " return r && r.innerText.includes('ZMODEM-ON-OK'); }",
                timeout=15000,
            )
            page.click("#zmodem-btn")

            # create a second session and switch tabs
            page.click("#new-tab")
            page.wait_for_function(
                "() => document.querySelectorAll('.tab').length === 2", timeout=15000
            )
            assert page.locator(".tab").count() == 2
            page.locator(".tab").first.click()
            assert "active" in (page.locator(".tab").first.get_attribute("class") or "")

            # hotkey: Alt+N creates another session
            page.click(".term-pane.active .xterm-screen")
            page.keyboard.press("Alt+n")
            page.wait_for_function(
                "() => document.querySelectorAll('.tab').length === 3", timeout=15000
            )

            # file panel lists the seed file
            page.click("#files-toggle")
            page.wait_for_selector("#file-list li", timeout=10000)
            page.wait_for_function(
                "() => document.getElementById('file-list').innerText.includes('seed.txt')",
                timeout=10000,
            )

            # share dialog shows a QR image and a link
            with page.expect_response(lambda r: "qr.svg" in r.url, timeout=10000) as info:
                page.click("#share-btn")
            assert info.value.status == 200, info.value.status
            page.wait_for_selector("#share-overlay:not(.hidden)", timeout=10000)
            share_url = page.input_value("#share-url")
            assert "share=" in share_url
            qr = page.locator("#share-qr")
            assert (qr.get_attribute("src") or "").startswith("/api/sessions/")
            page.wait_for_function(
                "() => { const i = document.getElementById('share-qr');"
                " return i && i.complete && i.naturalWidth > 0; }",
                timeout=10000,
            )
            page.click("#share-close")

            # theme preference applies
            page.click("#settings-btn")
            page.select_option("#set-theme", "light")
            assert page.get_attribute("html", "data-theme") == "light"
            page.click("#settings-close")

            # record the session, then replay it in the asciinema player
            page.click("#record-btn")
            page.click(".term-pane.active .xterm-screen")
            page.keyboard.type("echo REPLAY-OK")
            page.keyboard.press("Enter")
            page.wait_for_function(
                "() => { const r = document.querySelector('.term-pane.active .xterm-rows');"
                " return r && r.innerText.includes('REPLAY-OK'); }",
                timeout=15000,
            )
            page.click("#record-btn")  # stop
            page.wait_for_timeout(400)
            page.click("#replay-btn")
            page.wait_for_selector("#replay-overlay:not(.hidden)", timeout=10000)
            page.wait_for_function(
                "() => document.getElementById('replay-host').children.length > 0",
                timeout=15000,
            )
            page.evaluate("() => document.getElementById('replay-close').click()")
            page.wait_for_selector("#replay-overlay.hidden", state="attached", timeout=5000)

            # open the share link in a fresh context: read-only, no login
            viewer = browser.new_context(viewport={"width": 1280, "height": 800})
            vpage = viewer.new_page()
            vpage.goto(share_url)
            vpage.wait_for_selector(".tab", timeout=15000)
            assert "shared" in (vpage.get_attribute("body", "class") or "")
            vpage.wait_for_selector(".readonly-badge", timeout=15000)
            viewer.close()

            browser.close()
    finally:
        server.send_signal(signal.SIGINT)
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(data, ignore_errors=True)
