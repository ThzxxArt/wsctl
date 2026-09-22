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
import socket
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


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


PORT = _free_port()
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
            console_errors: list[str] = []
            page.on(
                "console",
                lambda m: console_errors.append(m.text) if m.type == "error" else None,
            )
            page.goto(BASE)

            # login
            assert page.locator("#login-overlay").is_visible()
            page.fill("#username", "admin")
            page.fill("#password", "testpass123")
            page.click("#login-form button[type=submit]")

            # a terminal tab appears and connects
            page.wait_for_selector(".tab", timeout=15000)
            page.wait_for_function(
                "() => document.getElementById('connection').textContent === '已连接'",
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

            # closing a tab detaches (the session must survive on the server)
            page.click(".term-pane.active .xterm-screen")
            page.click(".tab.active .close")
            page.wait_for_function(
                "() => document.querySelectorAll('.tab').length === 2", timeout=15000
            )
            page.click("#sessions-btn")
            page.wait_for_selector("#sessions-overlay:not(.hidden)", timeout=10000)
            page.wait_for_function(
                "() => document.querySelectorAll('#sessions-list .session-row').length >= 3",
                timeout=10000,
            )
            # reopen the detached session from the session list
            page.locator("#sessions-list .session-row button:has-text('打开')").first.click()
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

            # reopening the share dialog must reuse the existing link
            page.click("#share-btn")
            page.wait_for_selector("#share-overlay:not(.hidden)", timeout=10000)
            assert page.input_value("#share-url") == share_url
            page.click("#share-close")

            # theme preference applies
            page.click("#settings-btn")
            page.select_option("#set-theme", "light")
            assert page.get_attribute("html", "data-theme") == "light"

            # terminal theme gallery (theme marketplace) applies a theme
            page.click(".theme-swatch:has-text('dracula')")
            stored = page.evaluate("() => JSON.parse(localStorage.getItem('wsctl-prefs'))")
            assert stored["termTheme"] == "dracula"
            # font family selection persists
            page.select_option("#set-font", '"JetBrains Mono", ui-monospace, monospace')
            stored = page.evaluate("() => JSON.parse(localStorage.getItem('wsctl-prefs'))")
            assert "JetBrains" in stored["fontFamily"]
            # "follow system" page theme
            page.select_option("#set-theme", "auto")
            assert page.get_attribute("html", "data-theme") in ("dark", "light")
            page.click("#settings-close")

            # Esc closes the topmost modal
            page.click("#settings-btn")
            page.wait_for_selector("#settings-overlay:not(.hidden)", timeout=5000)
            page.keyboard.press("Escape")
            page.wait_for_selector("#settings-overlay.hidden", state="attached", timeout=5000)

            # admin panel: users / audit / recordings tabs
            page.click("#admin-btn")
            page.wait_for_selector("#admin-overlay:not(.hidden)", timeout=10000)
            page.wait_for_function(
                "() => document.querySelectorAll('#admin-body .admin-table tbody tr').length >= 1",
                timeout=10000,
            )
            page.click(".tab2[data-tab='audit']")
            page.wait_for_selector("#admin-body .admin-table", timeout=10000)
            page.click(".tab2[data-tab='recordings']")
            page.wait_for_timeout(300)
            # enabling 2FA opens the shared modal with a QR code; Esc closes the
            # topmost modal (not the admin panel underneath it)
            page.click(".tab2[data-tab='users']")
            page.wait_for_function(
                "() => document.querySelectorAll('#admin-body .admin-table tbody tr').length >= 1",
                timeout=10000,
            )
            page.locator(
                "#admin-body .admin-table tbody tr"
            ).first.locator("button:has-text('启用 2FA')").click()
            page.wait_for_selector("#qr-overlay:not(.hidden)", timeout=10000)
            page.wait_for_selector("#qr-image svg", timeout=10000)
            page.keyboard.press("Escape")
            page.wait_for_selector("#qr-overlay.hidden", state="attached", timeout=5000)
            assert page.locator("#admin-overlay").is_visible()
            page.click("#admin-close")
            page.wait_for_selector("#admin-overlay.hidden", state="attached", timeout=5000)

            # terminal search over the buffer
            page.click(".term-pane.active .xterm-screen")
            page.click("#search-btn")
            page.wait_for_selector("#search-bar:not(.hidden)", timeout=5000)
            page.fill("#search-input", "BROWSER-OK")
            page.wait_for_function(
                "() => document.getElementById('search-count').textContent.includes('/')",
                timeout=10000,
            )
            page.click("#search-close")

            # inline rename dialog (double-click the tab label, not the close button)
            page.dblclick(".tab.active .label")
            page.wait_for_selector("#rename-overlay:not(.hidden)", timeout=5000)
            page.fill("#rename-input", "重命名标签")
            with page.expect_response(
                lambda r: r.request.method == "PATCH" and "/api/sessions/" in r.url,
                timeout=10000,
            ) as rename_response:
                page.click("#rename-form button[type=submit]")
            assert rename_response.value.status == 200, rename_response.value.status
            page.wait_for_selector("#rename-overlay.hidden", state="attached", timeout=5000)
            page.wait_for_function(
                "() => document.querySelector('.tab.active .label').textContent === '重命名标签'",
                timeout=10000,
            )

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
            # the cast must actually load (regression: CSP blocked blob: fetches)
            assert not any(
                "Content Security Policy" in e or "blob" in e.lower() for e in console_errors
            ), console_errors
            page.evaluate("() => document.getElementById('replay-close').click()")
            page.wait_for_selector("#replay-overlay.hidden", state="attached", timeout=5000)

            # open the share link in a fresh context: read-only, no login
            viewer = browser.new_context(viewport={"width": 1280, "height": 800})
            vpage = viewer.new_page()
            vpage.goto(share_url)
            vpage.wait_for_selector(".tab", timeout=15000)
            assert "shared" in (vpage.get_attribute("body", "class") or "")
            vpage.wait_for_selector(".readonly-badge", timeout=15000)

            # a read-only viewer cannot type into the session
            vpage.click(".term-pane.active .xterm-screen")
            vpage.keyboard.type("echo SHOULD-NOT-APPEAR")
            vpage.keyboard.press("Enter")
            vpage.wait_for_timeout(800)
            rows = vpage.eval_on_selector(
                ".term-pane.active .xterm-rows", "el => el.innerText"
            )
            assert "SHOULD-NOT-APPEAR" not in rows
            viewer.close()

            # session filter and "detach all" (keeps sessions on the server)
            page.click("#sessions-btn")
            page.wait_for_selector("#sessions-overlay:not(.hidden)", timeout=10000)
            page.fill("#session-filter", "重命名标签")
            page.wait_for_function(
                "() => document.querySelectorAll('#sessions-list .session-row').length >= 1",
                timeout=10000,
            )
            page.click("#sessions-detach-all")
            page.wait_for_function(
                "() => document.querySelectorAll('.tab').length === 0", timeout=10000
            )
            assert page.locator("#sessions-overlay").get_attribute("class") is not None

            browser.close()
    finally:
        server.send_signal(signal.SIGINT)
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(data, ignore_errors=True)
