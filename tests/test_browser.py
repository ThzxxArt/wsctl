"""Browser-level end-to-end tests using Playwright.

Excluded from the default suite (marker ``browser``) and skipped when
Playwright is not installed. Run with::

    pip install "wsctl[e2e]"
    playwright install chromium
    pytest -m browser
"""

from __future__ import annotations

import contextlib
import os
import re
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

# Every browser wait uses this. It used to be a scatter of 5s/10s/15s/30s
# budgets, and on a cold CI runner one of them ran out while the page was
# still fetching assets -- the suite is green locally and red on CI.
# 60s is generous for a healthy machine and merely patient on a slow one.
WAIT_MS = 60000

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
    # Each test gets a fresh database, so any cached login token is invalid.
    global _TOKEN
    _TOKEN = None
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


def _login(page: object) -> None:
    page.goto(BASE)  # type: ignore[attr-defined]
    page.wait_for_selector("#login-overlay", timeout=15000)  # type: ignore[attr-defined]
    page.fill("#username", "admin")  # type: ignore[attr-defined]
    page.fill("#password", "testpass123")  # type: ignore[attr-defined]
    page.click("#login-form button[type=submit]")  # type: ignore[attr-defined]
    page.wait_for_selector(".tab", timeout=15000)  # type: ignore[attr-defined]


_TOKEN: str | None = None


def _admin_headers() -> dict[str, str]:
    """Log in once and reuse the token.

    Deliberately *not* a fresh login per call: the flow test turns on 2FA for
    the first user in the table (which is admin), after which every new login
    needs a TOTP code and would 401.
    """
    global _TOKEN
    if _TOKEN is None:
        r = httpx.post(
            f"{BASE}/api/login",
            json={"username": "admin", "password": "testpass123"},
            timeout=10,
        )
        r.raise_for_status()
        _TOKEN = r.cookies["wsctl_session"]
    return {"Cookie": f"wsctl_session={_TOKEN}"}


def _headers_from(page: object) -> dict[str, str]:
    """Build auth headers from the *page's* session, never a new login."""
    cookie = next(
        (c for c in page.context.cookies() if c["name"] == "wsctl_session"), None  # type: ignore[attr-defined]
    )
    assert cookie is not None, "browser is not logged in"
    return {"Cookie": f"wsctl_session={cookie['value']}"}





def _screen_text(page: object) -> str:
    """What the terminal says, on any renderer.

    WebGL and Canvas paint to a canvas and leave no ``.xterm-rows`` DOM behind,
    so reading the page cannot answer this; ``window.__wsctlScreen`` reads
    xterm's own buffer instead. Passing the marker in as an argument also keeps
    the call sites short enough for the linter.
    """
    return str(page.evaluate("() => window.__wsctlScreen ? window.__wsctlScreen() : ''"))  # type: ignore[attr-defined]


def _wait_screen_includes(page: object, marker: str) -> None:
    page.wait_for_function(  # type: ignore[attr-defined]
        "m => (window.__wsctlScreen ? window.__wsctlScreen() : '').includes(m)",
        arg=marker,
        timeout=WAIT_MS,
    )


def test_browser_flow(tmp_path: Path) -> None:
    data = tmp_path / "data"
    files = tmp_path / "files"
    files.mkdir(parents=True)
    (files / "seed.txt").write_text("hello from browser test", encoding="utf-8")
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
            page.wait_for_selector(".tab", timeout=WAIT_MS)
            page.wait_for_function(
                "() => document.getElementById('connection').textContent === '已连接'",
                timeout=WAIT_MS,
            )
            assert page.locator(".tab").count() == 1

            # type into the terminal and see the echoed output
            page.click(".term-pane.active .xterm-screen")
            page.keyboard.type("echo BROWSER-OK")
            page.keyboard.press("Enter")
            _wait_screen_includes(page, 'BROWSER-OK'),

            # bundled addons are available
            assert page.evaluate("() => typeof window.ImageAddon") != "undefined"
            assert page.evaluate("() => typeof ImageAddon.ImageAddon") == "function"
            assert page.evaluate("() => typeof window.Zmodem") != "undefined"

            # enabling ZMODEM must not break normal terminal I/O
            page.click("#zmodem-btn")
            page.click(".term-pane.active .xterm-screen")
            page.keyboard.type("echo ZMODEM-ON-OK")
            page.keyboard.press("Enter")
            _wait_screen_includes(page, 'ZMODEM-ON-OK'),
            page.click("#zmodem-btn")

            # create a second session and switch tabs. The "+" button now opens
            # a dialog (so a session can be named / pointed at ssh); Alt+N is
            # the one-keystroke path that still opens a default shell at once.
            page.click("#new-tab")
            page.wait_for_selector("#new-session-overlay:not(.hidden)", timeout=WAIT_MS)
            page.click("#new-session-quick")
            page.wait_for_selector("#new-session-overlay.hidden", state="attached", timeout=WAIT_MS)
            page.wait_for_function(
                "() => document.querySelectorAll('.tab').length === 2", timeout=WAIT_MS
            )
            assert page.locator(".tab").count() == 2
            page.locator(".tab").first.click()
            assert "active" in (page.locator(".tab").first.get_attribute("class") or "")

            # hotkey: Alt+N creates another session
            page.click(".term-pane.active .xterm-screen")
            page.keyboard.press("Alt+n")
            page.wait_for_function(
                "() => document.querySelectorAll('.tab').length === 3", timeout=WAIT_MS
            )

            # closing a tab detaches (the session must survive on the server)
            page.click(".term-pane.active .xterm-screen")
            page.click(".tab.active .close")
            page.wait_for_function(
                "() => document.querySelectorAll('.tab').length === 2", timeout=WAIT_MS
            )
            page.click("#sessions-btn")
            page.wait_for_selector("#sessions-overlay:not(.hidden)", timeout=WAIT_MS)
            page.wait_for_function(
                "() => document.querySelectorAll('#sessions-list .session-row').length >= 3",
                timeout=WAIT_MS,
            )
            # reopen the detached session from the session list
            page.locator("#sessions-list .session-row button:has-text('打开')").first.click()
            page.wait_for_function(
                "() => document.querySelectorAll('.tab').length === 3", timeout=WAIT_MS
            )

            # file panel lists the seed file
            page.click("#files-toggle")
            page.wait_for_selector("#file-list li", timeout=WAIT_MS)
            page.wait_for_function(
                "() => document.getElementById('file-list').innerText.includes('seed.txt')",
                timeout=WAIT_MS,
            )

            # share dialog shows a QR image and a link
            with page.expect_response(lambda r: "qr.svg" in r.url, timeout=WAIT_MS) as info:
                page.click("#share-btn")
            assert info.value.status == 200, info.value.status
            page.wait_for_selector("#share-overlay:not(.hidden)", timeout=WAIT_MS)
            share_url = page.input_value("#share-url")
            assert "share=" in share_url
            qr = page.locator("#share-qr")
            assert (qr.get_attribute("src") or "").startswith("/api/sessions/")
            page.wait_for_function(
                "() => { const i = document.getElementById('share-qr');"
                " return i && i.complete && i.naturalWidth > 0; }",
                timeout=WAIT_MS,
            )
            page.click("#share-close")

            # reopening the share dialog must reuse the existing link
            page.click("#share-btn")
            page.wait_for_selector("#share-overlay:not(.hidden)", timeout=WAIT_MS)
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
            # Select by *label*, not by the option's value string: 0.1.15 put
            # the CJK monospace fallbacks into every preset, so pinning the
            # value made this step fail the moment the font stack improved.
            page.select_option("#set-font", label="JetBrains Mono")
            stored = page.evaluate("() => JSON.parse(localStorage.getItem('wsctl-prefs'))")
            assert "JetBrains" in stored["fontFamily"]
            # "follow system" page theme
            page.select_option("#set-theme", "auto")
            assert page.get_attribute("html", "data-theme") in ("dark", "light")
            page.click("#settings-close")

            # Esc closes the topmost modal
            page.click("#settings-btn")
            page.wait_for_selector("#settings-overlay:not(.hidden)", timeout=WAIT_MS)
            page.keyboard.press("Escape")
            page.wait_for_selector("#settings-overlay.hidden", state="attached", timeout=WAIT_MS)

            # admin panel: users / audit / recordings tabs
            page.click("#admin-btn")
            page.wait_for_selector("#admin-overlay:not(.hidden)", timeout=WAIT_MS)
            page.wait_for_function(
                "() => document.querySelectorAll('#admin-body .admin-table tbody tr').length >= 1",
                timeout=WAIT_MS,
            )
            page.click(".tab2[data-tab='audit']")
            page.wait_for_selector("#admin-body .admin-table", timeout=WAIT_MS)
            page.click(".tab2[data-tab='recordings']")
            page.wait_for_timeout(300)
            # enabling 2FA opens the shared modal with a QR code; Esc closes the
            # topmost modal (not the admin panel underneath it)
            page.click(".tab2[data-tab='users']")
            page.wait_for_function(
                "() => document.querySelectorAll('#admin-body .admin-table tbody tr').length >= 1",
                timeout=WAIT_MS,
            )
            page.locator(
                "#admin-body .admin-table tbody tr"
            ).first.locator("button:has-text('启用 2FA')").click()
            page.wait_for_selector("#qr-overlay:not(.hidden)", timeout=WAIT_MS)
            page.wait_for_selector("#qr-image svg", timeout=WAIT_MS)
            page.keyboard.press("Escape")
            page.wait_for_selector("#qr-overlay.hidden", state="attached", timeout=WAIT_MS)
            assert page.locator("#admin-overlay").is_visible()
            # Turn the 2FA back off (the row above is admin); leaving it on
            # would lock every later login behind a code nobody has.
            page.locator(
                "#admin-body .admin-table tbody tr"
            ).first.locator("button:has-text('关闭 2FA')").click()
            page.wait_for_function(
                "() => {"
                " const row = document.querySelector('#admin-body .admin-table tbody tr');"
                " return row && !row.innerText.includes('已启用');"
                "}",
                timeout=WAIT_MS,
            )
            page.click("#admin-close")
            page.wait_for_selector("#admin-overlay.hidden", state="attached", timeout=WAIT_MS)

            # terminal search over the buffer
            page.click(".term-pane.active .xterm-screen")
            page.click("#search-btn")
            page.wait_for_selector("#search-bar:not(.hidden)", timeout=WAIT_MS)
            page.fill("#search-input", "BROWSER-OK")
            page.wait_for_function(
                "() => document.getElementById('search-count').textContent.includes('/')",
                timeout=WAIT_MS,
            )
            page.click("#search-close")

            # inline rename dialog (double-click the tab label, not the close button)
            page.dblclick(".tab.active .label")
            page.wait_for_selector("#rename-overlay:not(.hidden)", timeout=WAIT_MS)
            page.wait_for_selector("#rename-input", state="visible", timeout=WAIT_MS)
            page.fill("#rename-input", "重命名标签")
            page.click("#rename-form button[type=submit]")
            page.wait_for_selector("#rename-overlay.hidden", state="attached", timeout=WAIT_MS)
            # Assert the *outcome*, not the request that produced it: binding to
            # "a PATCH must be in flight while this context manager is open" is
            # timing-sensitive and went green on a fast box while failing on CI.
            # A stale `attached` message used to revert the label, and the round
            # trip could even send the *old* name back, so both ends are checked.
            try:
                page.wait_for_function(
                    "() => document.querySelector('.tab.active .label')"
                    ".textContent === '重命名标签'",
                    timeout=WAIT_MS,
                )
            except Exception:
                print("RENAME-DEBUG labels=", page.evaluate(
                    "() => [...document.querySelectorAll('.tab')].map(t => "
                    "({cls: t.className, label: t.querySelector('.label').textContent}))"))
                print("RENAME-DEBUG sessions=", page.evaluate(
                    "async () => (await (await fetch('/api/sessions')).json())"
                    ".map(s => ({id: s.id, name: s.name}))"))
                raise
            renamed = [
                s for s in httpx.get(
                    f"{BASE}/api/sessions", headers=_headers_from(page), timeout=WAIT_MS
                ).json()
                if s["name"] == "重命名标签"
            ]
            assert renamed, "rename never reached the server"

            # record the session, then replay it in the asciinema player
            page.click("#record-btn")
            page.click(".term-pane.active .xterm-screen")
            page.keyboard.type("echo REPLAY-OK")
            page.keyboard.press("Enter")
            _wait_screen_includes(page, 'REPLAY-OK'),
            page.click("#record-btn")  # stop
            page.wait_for_timeout(400)
            page.click("#replay-btn")
            page.wait_for_selector("#replay-overlay:not(.hidden)", timeout=WAIT_MS)
            page.wait_for_function(
                "() => document.getElementById('replay-host').children.length > 0",
                timeout=WAIT_MS,
            )
            # the cast must actually load (regression: CSP blocked blob: fetches)
            assert not any(
                "Content Security Policy" in e or "blob" in e.lower() for e in console_errors
            ), console_errors
            page.evaluate("() => document.getElementById('replay-close').click()")
            page.wait_for_selector("#replay-overlay.hidden", state="attached", timeout=WAIT_MS)

            # open the share link in a fresh context: read-only, no login
            viewer = browser.new_context(viewport={"width": 1280, "height": 800})
            vpage = viewer.new_page()
            vpage.goto(share_url)
            vpage.wait_for_selector(".tab", timeout=WAIT_MS)
            assert "shared" in (vpage.get_attribute("body", "class") or "")
            vpage.wait_for_selector(".readonly-badge", timeout=WAIT_MS)

            # a read-only viewer cannot type into the session
            vpage.click(".term-pane.active .xterm-screen")
            vpage.keyboard.type("echo SHOULD-NOT-APPEAR")
            vpage.keyboard.press("Enter")
            vpage.wait_for_timeout(800)
            rows = vpage.evaluate("() => window.__wsctlScreen()")
            assert "SHOULD-NOT-APPEAR" not in rows
            viewer.close()

            # session filter and "detach all" (keeps sessions on the server)
            page.click("#sessions-btn")
            page.wait_for_selector("#sessions-overlay:not(.hidden)", timeout=WAIT_MS)
            page.fill("#session-filter", "重命名标签")
            page.wait_for_function(
                "() => document.querySelectorAll('#sessions-list .session-row').length >= 1",
                timeout=WAIT_MS,
            )
            page.click("#sessions-detach-all")
            page.wait_for_function(
                "() => document.querySelectorAll('.tab').length === 0", timeout=WAIT_MS
            )
            assert page.locator("#sessions-overlay").get_attribute("class") is not None

            browser.close()
    finally:
        server.send_signal(signal.SIGINT)
        try:
            server.wait(timeout=WAIT_MS)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(data, ignore_errors=True)


def _stop_server(server: subprocess.Popen[bytes]) -> None:
    server.send_signal(signal.SIGINT)
    try:
        server.wait(timeout=WAIT_MS)
    except subprocess.TimeoutExpired:
        server.kill()


def test_browser_session_form_config_and_upload_guard(tmp_path: Path) -> None:
    """The new-session dialog, the config tab and the upload guard."""
    data = tmp_path / "data"
    files = tmp_path / "files"
    files.mkdir(parents=True)
    (files / "exists.txt").write_text("original-content", encoding="utf-8")
    server = _start_server(data, files)
    try:
        _wait_health()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
            _login(page)
            page.wait_for_function(
                "() => document.getElementById('connection').textContent === '已连接'",
                timeout=15000,
            )

            # -- the new-session dialog is reachable and drives the API ------
            before = page.locator(".tab").count()
            page.click("#new-tab")
            page.wait_for_selector("#new-session-overlay:not(.hidden)", timeout=5000)
            # SSH fields only appear for the ssh backend.
            assert page.locator("#ns-ssh-block").is_hidden()
            page.select_option("#ns-backend", "ssh")
            page.wait_for_selector("#ns-ssh-block:not(.hidden)", timeout=5000)
            page.select_option("#ns-backend", "local")
            assert page.locator("#ns-ssh-block").is_hidden()

            with page.expect_response(
                lambda r: r.request.method == "POST" and r.url.endswith("/api/sessions"),
                timeout=15000,
            ) as created:
                page.fill("#ns-name", "表单会话")
                page.fill("#ns-command", "sh")
                page.click("#new-session-form button[type=submit]")
            assert created.value.status == 201, created.value.status
            page.wait_for_selector("#new-session-overlay.hidden", state="attached", timeout=5000)
            page.wait_for_function(
                "n => document.querySelectorAll('.tab').length === n",
                arg=before + 1,
                timeout=15000,
            )
            page.wait_for_function(
                "() => [...document.querySelectorAll('.tab .label')]"
                ".some(el => el.textContent === '表单会话')",
                timeout=15000,
            )

            # -- SSH backend requires a host --------------------------------
            page.click("#new-tab")
            page.wait_for_selector("#new-session-overlay:not(.hidden)", timeout=5000)
            page.select_option("#ns-backend", "ssh")
            page.click("#new-session-form button[type=submit]")
            page.wait_for_selector("#new-session-error:not(.hidden)", timeout=5000)
            assert "主机" in page.inner_text("#new-session-error")
            page.keyboard.press("Escape")
            page.wait_for_selector("#new-session-overlay.hidden", state="attached", timeout=5000)

            # -- the config tab is read-only and labels restart-only keys ----
            page.click("#admin-btn")
            page.wait_for_selector("#admin-overlay:not(.hidden)", timeout=10000)
            page.click(".tab2[data-tab='config']")
            page.wait_for_selector("#admin-body .config-table", timeout=10000)
            rows = page.locator("#admin-body .config-table tbody tr")
            assert rows.count() > 10
            page.wait_for_function(
                "() => {"
                " const t = document.querySelector('#admin-body .config-table').innerText;"
                # All three tiers must be distinguishable at a glance. The
                # middle one used to be printed as 热更新, which promised an
                # immediate effect the server never delivered.
                " return t.includes('max_sessions')"
                "   && t.includes('立即生效') && t.includes('新建时生效')"
                "   && t.includes('需重启');"
                "}",
                timeout=10000,
            )
            page.click("#admin-close")

            # -- uploading over an existing file must ask first --------------
            page.click("#files-toggle")
            page.wait_for_selector("#file-list li", timeout=10000)
            page.wait_for_function(
                "() => document.getElementById('file-list').innerText.includes('exists.txt')",
                timeout=10000,
            )
            with page.expect_file_chooser(timeout=10000) as chooser:
                page.click("#file-upload-btn")
            chooser.value.set_files(
                {"name": "exists.txt", "mimeType": "text/plain", "buffer": b"replaced"}
            )
            # The guard dialog appears and cancelling keeps the original file.
            page.wait_for_selector("#confirm-overlay:not(.hidden)", timeout=10000)
            assert "覆盖" in page.inner_text("#confirm-message")
            page.click("#confirm-cancel")
            page.wait_for_selector("#confirm-overlay.hidden", state="attached", timeout=5000)
            current = httpx.get(
                f"{BASE}/api/files/download",
                params={"path": "exists.txt"},
                headers=_admin_headers(),
                timeout=10,
            ).content
            assert current == b"original-content"

            # Confirming the overwrite replaces the file.
            with page.expect_file_chooser(timeout=10000) as chooser:
                page.click("#file-upload-btn")
            chooser.value.set_files(
                {"name": "exists.txt", "mimeType": "text/plain", "buffer": b"replaced"}
            )
            page.wait_for_selector("#confirm-overlay:not(.hidden)", timeout=10000)
            page.click("#confirm-ok")
            # Wait on the *file*, not on a status string. The status element is
            # reused across uploads and across the "skip" path, so a stale
            # 上传完成 from an earlier declined upload satisfies a text check
            # before this one has landed -- which is exactly how this test
            # read b"original-content" and failed on CI.
            deadline = time.time() + 20
            current = b"original-content"
            while time.time() < deadline:
                current = httpx.get(
                    f"{BASE}/api/files/download",
                    params={"path": "exists.txt"},
                    headers=_admin_headers(),
                    timeout=10,
                ).content
                if current == b"replaced":
                    break
                page.wait_for_timeout(200)
            assert current == b"replaced", "the confirmed overwrite never landed"

            # And the status line must not claim success for the upload the
            # user *declined* earlier in this same dialog.
            page.wait_for_function(
                "() => document.getElementById('file-status')"
                ".textContent.includes('上传完成')",
                timeout=15000,
            )

            browser.close()
    finally:
        _stop_server(server)
        shutil.rmtree(data, ignore_errors=True)


def test_browser_lazy_connect_keeps_a_bounded_socket_set(tmp_path: Path) -> None:
    """More sessions than the auto-connect budget stay in the tab bar as standby."""
    data = tmp_path / "data"
    files = tmp_path / "files"
    files.mkdir(parents=True)
    server = _start_server(data, files)
    try:
        _wait_health()
        headers = _admin_headers()
        # Nine sessions: eight keep a live socket, the ninth waits to be opened.
        for index in range(9):
            r = httpx.post(
                f"{BASE}/api/sessions",
                headers=headers,
                json={"name": f"s{index}", "command": "sh"},
                timeout=10,
            )
            assert r.status_code == 201, r.text

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
            _login(page)
            page.wait_for_function(
                "() => document.querySelectorAll('.tab').length === 9", timeout=20000
            )
            # Wait for the sockets to actually open (a tab is only marked
            # `connected` in onopen), not just for the standby marker.
            page.wait_for_function(
                "() => document.querySelectorAll('.tab.connected').length === 8"
                " && document.querySelectorAll('.tab.standby').length === 1",
                timeout=20000,
            )
            standby_label = page.locator(".tab.standby .label").first.inner_text()

            # Opening the standby tab brings *it* online. The budget is a hard
            # cap, so the least-recently-used live tab is suspended instead of
            # growing the socket set past eight.
            page.locator(".tab.standby").first.click()
            page.wait_for_function(
                "() => document.querySelectorAll('.tab.connected').length === 8"
                " && document.querySelectorAll('.tab.standby').length === 1",
                timeout=20000,
            )
            assert page.locator(".tab.standby .label").first.inner_text() != standby_label
            # The tab we opened is now attached and marked connected.
            opened = page.locator(".tab", has_text=standby_label)
            assert opened.count() == 1
            assert "connected" in (opened.first.get_attribute("class") or "")
            browser.close()
    finally:
        _stop_server(server)
        shutil.rmtree(data, ignore_errors=True)


@pytest.mark.skipif(shutil.which("sz") is None or shutil.which("rz") is None,
                    reason="requires lrzsz (sz/rz)")
def test_browser_zmodem_roundtrip(tmp_path: Path) -> None:
    """Real sz (to the browser) and rz (from the browser) transfers."""
    data = tmp_path / "data"
    files = tmp_path / "files"
    files.mkdir(parents=True)
    payload = b"zmodem-round-trip-" + os.urandom(32)
    (files / "xfer.bin").write_bytes(payload)
    server = _start_server(data, files)
    try:
        _wait_health()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(
                viewport={"width": 1400, "height": 900}, accept_downloads=True
            ).new_page()
            _login(page)
            page.wait_for_function(
                "() => document.getElementById('connection').textContent === '已连接'",
                timeout=15000,
            )
            page.click("#zmodem-btn")  # arm the Sentry

            # -- sz: the remote sends a file, the browser saves it -----------
            # Start listening *before* the command runs: the transfer can finish
            # faster than the test enters the context manager.
            page.click(".term-pane.active .xterm-screen")
            with page.expect_download(timeout=60000) as info:
                page.keyboard.type(f"sz {files}/xfer.bin")
                page.keyboard.press("Enter")
            download = info.value
            saved = tmp_path / "downloaded.bin"
            download.save_as(saved)
            assert saved.read_bytes() == payload, "sz payload corrupted in transit"

            # Reset the transfer state before the next one: a leftover ZMODEM
            # session would keep swallowing keystrokes (which is exactly what
            # the input-lock watchdog exists for).
            page.wait_for_timeout(500)
            page.click("#zmodem-btn")
            page.wait_for_timeout(200)
            page.click("#zmodem-btn")
            page.click(".term-pane.active .xterm-screen")
            page.keyboard.press("Enter")
            page.wait_for_timeout(500)

            # -- rz: the browser sends a file, the remote saves it -----------
            # Same rule as above: the file chooser is created as soon as the
            # remote's `rz` handshake reaches the Sentry.
            upload_bytes = b"rz-upload-" + os.urandom(32)
            with page.expect_file_chooser(timeout=60000) as chooser:
                page.keyboard.type(f"cd {files} && rm -f uploaded.bin && rz")
                page.keyboard.press("Enter")
            chooser.value.set_files(
                {"name": "uploaded.bin", "mimeType": "application/octet-stream",
                 "buffer": upload_bytes}
            )
            deadline = time.time() + 30
            target = files / "uploaded.bin"
            while time.time() < deadline:
                if target.is_file() and target.read_bytes() == upload_bytes:
                    break
                time.sleep(0.3)
            assert target.is_file(), "rz never created the file"
            assert target.read_bytes() == upload_bytes, "rz payload corrupted in transit"

            browser.close()
    finally:
        _stop_server(server)
        shutil.rmtree(data, ignore_errors=True)


def test_browser_file_panel_organise_and_edit(tmp_path: Path) -> None:
    """The panel can now organise and read in place, not just list and download.

    Before this it offered three verbs (list / download / upload) and upload
    had neither progress nor cancel. Everything here is the new surface.
    """
    data = tmp_path / "data"
    files = tmp_path / "files"
    files.mkdir(parents=True)
    (files / "notes.txt").write_text("original 原文", encoding="utf-8")
    server = _start_server(data, files)
    try:
        _wait_health()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
            _login(page)
            page.wait_for_function(
                "() => document.getElementById('connection').textContent === '已连接'",
                timeout=15000,
            )

            page.click("#files-toggle")
            page.wait_for_selector("#file-list li", timeout=10000)

            # -- new directory ------------------------------------------------
            page.click("#file-mkdir-btn")
            page.wait_for_selector("#rename-overlay:not(.hidden)", timeout=5000)
            page.fill("#rename-input", "projects")
            page.click("#rename-form button[type=submit]")
            page.wait_for_function(
                "() => document.getElementById('file-list').innerText.includes('projects')",
                timeout=10000,
            )

            # -- preview and edit a text file in place ------------------------
            page.locator("#file-list li", has_text="notes.txt").click(button="right")
            page.wait_for_selector("#file-menu:not(.hidden)", timeout=5000)
            page.click("#file-menu button[data-faction='preview']")
            page.wait_for_selector("#preview-overlay:not(.hidden)", timeout=10000)
            page.wait_for_function(
                "() => document.getElementById('preview-body').value.includes('原文')",
                timeout=10000,
            )
            page.fill("#preview-body", "edited 已编辑")
            page.click("#preview-save")
            page.wait_for_selector("#preview-overlay.hidden", state="attached", timeout=10000)

            # the save must actually reach the disk (and be visible after reload)
            page.click("#file-refresh")
            page.wait_for_function(
                "() => document.getElementById('file-status').textContent.includes('项')",
                timeout=10000,
            )
            body = httpx.get(
                f"{BASE}/api/files/download",
                params={"path": "notes.txt"},
                headers=_headers_from(page),
                timeout=10,
            ).content
            assert body.decode("utf-8") == "edited 已编辑", body

            # -- rename through the context menu ------------------------------
            page.locator("#file-list li", has_text="notes.txt").click(button="right")
            page.wait_for_selector("#file-menu:not(.hidden)", timeout=5000)
            page.click("#file-menu button[data-faction='rename']")
            page.wait_for_selector("#rename-overlay:not(.hidden)", timeout=5000)
            page.fill("#rename-input", "renamed.txt")
            page.click("#rename-form button[type=submit]")
            page.wait_for_function(
                "() => document.getElementById('file-list').innerText.includes('renamed.txt')",
                timeout=10000,
            )

            # -- filter narrows the listing ------------------------------------
            page.fill("#file-filter", "renamed")
            page.wait_for_function(
                "() => { const t = document.getElementById('file-list').innerText;"
                " return t.includes('renamed.txt') && !t.includes('projects'); }",
                timeout=10000,
            )
            page.fill("#file-filter", "")

            # -- delete asks you to type the name back -------------------------
            # Irreversible and un-recursive: a plain "确定 / 取消" is one stray
            # click from deleting the wrong thing, so the entry name must be
            # typed out first.
            page.locator("#file-list li", has_text="renamed.txt").click(button="right")
            page.wait_for_selector("#file-menu:not(.hidden)", timeout=5000)
            page.click("#file-menu button[data-faction='delete']")
            page.wait_for_selector("#rename-overlay:not(.hidden)", timeout=10000)
            submit = page.locator("#rename-form button[type=submit]")
            assert submit.is_disabled(), "confirm button must start disabled"
            page.fill("#rename-input", "wrong-name")
            assert submit.is_disabled(), "a mismatched name must keep it disabled"
            page.fill("#rename-input", "renamed.txt")
            page.wait_for_function(
                "() => !document.querySelector('#rename-form button[type=submit]').disabled",
                timeout=5000,
            )
            submit.click()
            page.wait_for_function(
                "() => !document.getElementById('file-list').innerText.includes('renamed.txt')",
                timeout=10000,
            )

            # -- upload shows progress and can be cancelled --------------------
            with page.expect_file_chooser(timeout=10000) as chooser:
                page.click("#file-upload-btn")
            chooser.value.set_files(
                {"name": "big.bin", "mimeType": "application/octet-stream",
                 "buffer": os.urandom(256 * 1024)}
            )
            # Either the bar appears (progress events fired) or the upload is
            # already done on a fast box -- both are acceptable, what must not
            # happen is a silent hang with no feedback at all.
            page.wait_for_function(
                "() => {"
                " const p = document.getElementById('file-upload-progress');"
                " const s = document.getElementById('file-status').textContent;"
                " return (!p.classList.contains('hidden')) || s.includes('上传完成');"
                "}",
                timeout=15000,
            )

            browser.close()
    finally:
        _stop_server(server)
        shutil.rmtree(data, ignore_errors=True)


def test_browser_hotkey_help_and_search_options(tmp_path: Path) -> None:
    """`?` must tell the user what the shortcuts are; search must be precise."""
    data = tmp_path / "data"
    files = tmp_path / "files"
    files.mkdir(parents=True)
    server = _start_server(data, files)
    try:
        _wait_health()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
            _login(page)
            page.wait_for_function(
                "() => document.getElementById('connection').textContent === '已连接'",
                timeout=15000,
            )

            # -- `Alt+?` opens the cheatsheet from anywhere, Esc closes it ----
            # A bare `?` is deliberately *not* used here: while the terminal
            # has focus the keystroke must reach the shell (`ls ?`), which is
            # exactly the regression this asserts against below.
            page.keyboard.press("Alt+?")
            page.wait_for_selector("#hotkey-overlay:not(.hidden)", timeout=5000)
            page.wait_for_function(
                "() => document.getElementById('hotkey-help').innerText.length > 20",
                timeout=5000,
            )
            help_text = page.inner_text("#hotkey-help")
            # The table has to name the bindings it is advertising.
            for expected in ("新建会话", "搜索终端", "打开 / 关闭本帮助"):
                assert expected in help_text, expected
            # ...and it must not advertise a chord the browser steals.
            assert "❌" in help_text and "浏览器占用" in help_text
            page.keyboard.press("Escape")
            page.wait_for_selector("#hotkey-overlay.hidden", state="attached", timeout=5000)

            # -- search is case-sensitive only when asked ---------------------
            page.click(".term-pane.active .xterm-screen")
            page.keyboard.type("echo MiXeD-case")
            page.keyboard.press("Enter")
            _wait_screen_includes(page, 'MiXeD-case'),
            page.click("#search-btn")
            page.fill("#search-input", "mixed-case")
            page.wait_for_function(
                "() => document.getElementById('search-count').textContent.includes('/')",
                timeout=10000,
            )
            assert "0/0" not in page.inner_text("#search-count"), "default search must ignore case"

            page.click("#search-case")
            page.wait_for_function(
                "() => document.getElementById('search-count').textContent === '0/0'",
                timeout=10000,
            )

            # an invalid regex is reported, not silently matched as nothing
            page.click("#search-case")  # case-insensitive again
            page.click("#search-regex")
            page.fill("#search-input", "Mi[eD")
            page.wait_for_function(
                "() => document.getElementById('search-count').textContent === '无效模式'",
                timeout=10000,
            )
            page.fill("#search-input", "Mi[e]D")
            page.wait_for_function(
                "() => document.getElementById('search-count').textContent.includes('/')"
                " && document.getElementById('search-count').textContent !== '无效模式'",
                timeout=10000,
            )
            page.click("#search-close")

            # A bare `?` typed into the terminal reaches the shell.
            page.click(".term-pane.active .xterm-screen")
            page.keyboard.type("echo Q?MARK")
            page.keyboard.press("Enter")
            _wait_screen_includes(page, 'Q?MARK'),
            browser.close()
    finally:
        _stop_server(server)
        shutil.rmtree(data, ignore_errors=True)


def test_browser_upload_can_be_cancelled(tmp_path: Path) -> None:
    """A long upload must show progress *and* be cancellable.

    ``fetch`` has no upload progress, so the panel uses ``XMLHttpRequest``;
    without either affordance a 100 MB upload is indistinguishable from a hang.
    """
    data = tmp_path / "data"
    files = tmp_path / "files"
    files.mkdir(parents=True)
    server = _start_server(data, files)
    try:
        _wait_health()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(
                viewport={"width": 1400, "height": 900}, accept_downloads=False
            ).new_page()
            _login(page)
            page.wait_for_function(
                "() => document.getElementById('connection').textContent === '已连接'",
                timeout=15000,
            )
            page.click("#files-toggle")
            page.wait_for_selector("#file-list li, #file-list .empty", timeout=10000)

            with page.expect_file_chooser(timeout=10000) as chooser:
                page.click("#file-upload-btn")
            # Large enough that the progress bar has a chance to paint before
            # completion on a fast box.
            chooser.value.set_files(
                {"name": "huge.bin", "mimeType": "application/octet-stream",
                 "buffer": os.urandom(4 * 1024 * 1024)}
            )
            # Either the bar is up (progress events fired) or a local upload
            # this small finished first -- both are fine. What must not happen
            # is a silent hang with no feedback at all. Only try to cancel if
            # there is anything left to cancel.
            with contextlib.suppress(Exception):
                page.wait_for_selector("#file-upload-progress:not(.hidden)", timeout=4000)
            # Clicking is racy by nature here: on a fast runner the upload can
            # finish between the visibility check and the click, and the bar
            # hides itself. Either outcome is correct -- what must never happen
            # is a silent hang with no feedback at all.
            with contextlib.suppress(Exception):
                if page.locator("#file-upload-progress").is_visible():
                    assert page.locator("#up-cancel").is_enabled()
                    page.click("#up-cancel", timeout=3000)

            # Either the server already finished (nothing to cancel) or the
            # abort was reported. What must never happen is a silent hang.
            page.wait_for_function(
                "() => {"
                " const s = document.getElementById('file-status').textContent;"
                " const p = document.getElementById('file-upload-progress');"
                " return s.includes('已取消') || s.includes('上传完成')"
                "   || p.classList.contains('hidden');"
                "}",
                timeout=15000,
            )
            browser.close()
    finally:
        _stop_server(server)
        shutil.rmtree(data, ignore_errors=True)


def test_browser_session_history_tab_shows_finished_sessions(tmp_path: Path) -> None:
    """A finished session must be visible in the dialog's history half.

    The rows were written to ``term_sessions`` and pruned by the retention
    policy, but nothing displayed them -- the product remembered a session was
    killed and refused to tell anyone.
    """
    data = tmp_path / "data"
    files = tmp_path / "files"
    files.mkdir(parents=True)
    server = _start_server(data, files)
    try:
        _wait_health()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
            _login(page)
            page.wait_for_function(
                "() => document.getElementById('connection').textContent === '已连接'",
                timeout=15000,
            )
            headers = _headers_from(page)

            # Make a session, then end it via the API so the browser sees the
            # exact "died while I was not looking" case.
            created = httpx.post(
                f"{BASE}/api/sessions", headers=headers,
                json={"name": "doomed", "command": "sleep 60"}, timeout=10,
            )
            assert created.status_code == 201, created.text
            sid = created.json()["id"]
            assert httpx.delete(
                f"{BASE}/api/sessions/{sid}", headers=headers, timeout=10
            ).status_code == 200

            page.click("#sessions-btn")
            page.wait_for_selector("#sessions-overlay:not(.hidden)", timeout=10000)
            page.click("[data-stab='history']")
            page.wait_for_function(
                "() => document.getElementById('sessions-list').innerText.includes('doomed')",
                timeout=10000,
            )
            listing = page.inner_text("#sessions-list")
            # status + reason, not a bare enum
            assert "已终止" in listing, listing
            # the row offers a way back in
            assert "重新打开" in listing
            assert "详情" in listing
            browser.close()
    finally:
        _stop_server(server)
        shutil.rmtree(data, ignore_errors=True)


def test_browser_rate_limit_notice_is_a_toast_not_terminal_output(
    tmp_path: Path,
) -> None:
    """A server notice must surface as a Toast and never as terminal output.

    The trigger is the input rate limit: type past the token bucket and the
    server answers with a ``notice`` control message. That is browser-observable
    and doubles as an end-to-end proof of the 0.1.8 fix -- before it, the same
    input tore the whole WebSocket down and the client reconnected into a fresh
    bucket, forever.

    (A read-only viewer is *not* a usable trigger: the UI withholds keystrokes
    client-side before they are sent, so the server never has reason to refuse.)
    """
    data = tmp_path / "data"
    files = tmp_path / "files"
    files.mkdir(parents=True)
    # Tiny bucket so one long paste overflows it.
    os.environ["WSCTL_INPUT_RATE_LIMIT"] = "8"
    os.environ["WSCTL_INPUT_RATE_BURST"] = "8"
    try:
        server = _start_server(data, files)
        try:
            _wait_health()
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
                _login(page)
                page.wait_for_function(
                    "() => document.getElementById('connection').textContent === '已连接'",
                    timeout=15000,
                )
                page.click(".term-pane.active .xterm-screen")
                # ONE frame far past the 8-byte bucket. `keyboard.type` would
                # send 200 single-key frames -- an empty bucket then produces
                # three consecutive strikes and the server (correctly) closes
                # with 4429. `insertText` is a single input event, one frame.
                page.keyboard.insert_text("x" * 200)
                page.wait_for_selector(".toast", timeout=15000)
                toast_text = page.inner_text("#toasts")
                assert "速率超限" in toast_text, toast_text

                # The notice must not be sitting in the buffer where the
                # scrollback would replay it as if the shell had printed it.
                rows = page.evaluate("() => window.__wsctlScreen()")
                assert "速率超限" not in rows, rows[-400:]
                assert "[wsctl]" not in rows

                # And one over-limit frame must NOT kill the link. (A peer that
                # keeps flooding *is* disconnected -- with 4429 -- but that is
                # three strikes later, and covered server-side.)
                assert "已连接" in page.inner_text("#connection"), (
                    page.inner_text("#connection")
                )
                browser.close()
        finally:
            _stop_server(server)
            shutil.rmtree(data, ignore_errors=True)
    finally:
        os.environ.pop("WSCTL_INPUT_RATE_LIMIT", None)
        os.environ.pop("WSCTL_INPUT_RATE_BURST", None)


def test_browser_admin_table_sorts_and_pages_without_duplicating(tmp_path: Path) -> None:
    """Sorting or paging must *replace* the table, not stack another copy.

    ``buildTable`` appended into its host on every re-render, so one click on a
    column header left a second table underneath -- and there was no test at
    all for the sorting/paging feature.
    """
    data = tmp_path / "data"
    files = tmp_path / "files"
    files.mkdir(parents=True)
    server = _start_server(data, files)
    try:
        _wait_health()
        headers = _admin_headers()
        # More rows than one page (TABLE_PAGE is 25) so the paging controls are
        # actually reachable -- "next" is correctly disabled on a single page,
        # and clicking it then is a test bug, not a product one.
        for i in range(30):
            r = httpx.post(
                f"{BASE}/api/users", headers=headers, timeout=10,
                json={"username": f"u{i:02d}", "password": "testpass123", "role": "user"},
            )
            assert r.status_code == 201, r.text

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
            _login(page)
            page.wait_for_function(
                "() => document.getElementById('connection').textContent === '已连接'",
                timeout=15000,
            )
            page.click("#admin-btn")
            page.wait_for_selector("#admin-overlay:not(.hidden)", timeout=10000)
            # Since 0.1.15 the panel opens on 概览 (one source of truth for the
            # highlight and the content), which renders three tables -- instance
            # leases, recently finished sessions and recent audit events. This
            # test is about *the user table*, so switch to it explicitly rather
            # than relying on which tab happens to be selected.
            page.click("#admin-overlay .tab2[data-tab='users']")
            page.wait_for_selector("#admin-body .admin-table", timeout=10000)
            assert page.locator("#admin-body .admin-table").count() == 1

            # Click a sort header three times: still exactly one table.
            for _ in range(3):
                page.locator("#admin-body .admin-table th").first.click()
                page.wait_for_timeout(150)
                assert page.locator("#admin-body .admin-table").count() == 1, (
                    "sorting stacked a duplicate table"
                )
                assert page.locator("#admin-body .admin-toolbar").count() >= 1, (
                    "re-render wiped the paging toolbar"
                )

            # Paging moves through the rows without duplicating the table.
            before = page.locator("#admin-body .admin-table tbody tr").count()
            assert before > 0
            next_btn = page.locator("#admin-body .admin-toolbar button:has-text('下一页')")
            assert next_btn.is_enabled(), "a 30-row list must have a second page"
            next_btn.click()
            page.wait_for_timeout(250)
            assert page.locator("#admin-body .admin-table").count() == 1, (
                "paging stacked a duplicate table"
            )
            assert page.locator("#admin-body .admin-table tbody tr").count() > 0
            # The paging toolbar survives a re-render next to the table.
            assert page.locator("#admin-body .table-host .admin-toolbar").count() == 1
            browser.close()
    finally:
        _stop_server(server)
        shutil.rmtree(data, ignore_errors=True)


def test_browser_history_reopen_uses_argv_and_refuses_ssh(tmp_path: Path) -> None:
    """Reopening history must replay argv -- and never run a remote command here."""
    data = tmp_path / "data"
    files = tmp_path / "files"
    files.mkdir(parents=True)
    server = _start_server(data, files)
    try:
        _wait_health()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
            _login(page)
            page.wait_for_function(
                "() => document.getElementById('connection').textContent === '已连接'",
                timeout=15000,
            )
            headers = _headers_from(page)
            sid = httpx.post(
                f"{BASE}/api/sessions", headers=headers,
                json={"name": "redo", "command": "sleep 30"}, timeout=10,
            ).json()["id"]
            assert httpx.delete(
                f"{BASE}/api/sessions/{sid}", headers=headers, timeout=10
            ).status_code == 200

            page.click("#sessions-btn")
            page.wait_for_selector("#sessions-overlay:not(.hidden)", timeout=10000)
            page.click("[data-stab='history']")
            page.wait_for_function(
                "() => document.getElementById('sessions-list').innerText.includes('redo')",
                timeout=10000,
            )
            page.locator("#sessions-list button:has-text('重新打开')").first.click()
            # The reopen lands a *new* live tab with the same name.
            page.wait_for_function(
                "() => [...document.querySelectorAll('.tab .label')]"
                ".some(el => el.textContent === 'redo')",
                timeout=15000,
            )
            browser.close()
    finally:
        _stop_server(server)
        shutil.rmtree(data, ignore_errors=True)



def test_browser_upload_cancel_really_cancels(tmp_path: Path) -> None:
    """Cancelling must abort the transfer, deterministically.

    Racing a real upload against a click is flaky (the bar hides itself the
    moment the upload lands). Holding the request open with a route removes the
    race entirely: the progress UI is guaranteed to be up, and cancelling must
    actually abort rather than just hide the bar.
    """
    data = tmp_path / "data"
    files = tmp_path / "files"
    files.mkdir(parents=True)
    server = _start_server(data, files)
    try:
        _wait_health()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()

            held = []

            def hold(route):
                held.append(route)

            page.route("**/api/files/upload", hold)
            try:
                _login(page)
                page.wait_for_function(
                    "() => document.getElementById('connection').textContent === '已连接'",
                    timeout=15000,
                )
                page.click("#files-toggle")
                page.wait_for_selector("#file-list li, #file-list .empty", timeout=10000)

                with page.expect_file_chooser(timeout=10000) as chooser:
                    page.click("#file-upload-btn")
                chooser.value.set_files(
                    {"name": "held.bin", "mimeType": "application/octet-stream",
                     "buffer": os.urandom(64 * 1024)}
                )
                # The request is held open, so the progress UI cannot have
                # finished on us: no race left to lose.
                page.wait_for_selector("#file-upload-progress:not(.hidden)", timeout=15000)
                page.click("#up-cancel", timeout=5000)
                page.wait_for_function(
                    "() => document.getElementById('file-status')"
                    ".textContent.includes('已取消')",
                    timeout=15000,
                )
                # Nothing landed on disk.
                assert not (files / "held.bin").exists()
            finally:
                for route in held:
                    with contextlib.suppress(Exception):
                        route.abort()
                browser.close()
    finally:
        _stop_server(server)
        shutil.rmtree(data, ignore_errors=True)


def test_browser_terminal_right_click_menu(tmp_path: Path) -> None:
    """The terminal right-click menu, and the three right-click modes."""
    data = tmp_path / "data"
    files = tmp_path / "files"
    files.mkdir(parents=True)
    server = _start_server(data, files)
    try:
        _wait_health()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
            _login(page)
            page.wait_for_function(
                "() => document.getElementById('connection').textContent === '已连接'",
                timeout=15000,
            )

            # default mode: a real menu with the operations spelled out
            page.click(".term-pane.active .xterm-screen", button="right")
            page.wait_for_selector("#term-menu:not(.hidden)", timeout=5000)
            menu = page.inner_text("#term-menu")
            for expected in ("复制选区", "粘贴", "全选", "清屏", "断开", "终止会话"):
                assert expected in menu, expected
            # copy is disabled with no selection
            assert page.locator("#term-menu button[data-taction='copy']").is_disabled()
            page.keyboard.press("Escape")
            page.wait_for_selector("#term-menu.hidden", state="attached", timeout=5000)

            # switch to "quick" mode and confirm the menu no longer appears
            page.click("#settings-btn")
            page.wait_for_selector("#settings-overlay:not(.hidden)", timeout=5000)
            page.select_option("#set-rightclick", "quick")
            page.click("#settings-done")
            page.click(".term-pane.active .xterm-screen", button="right")
            page.wait_for_timeout(300)
            assert page.locator("#term-menu").is_hidden(), "quick mode must not open a menu"

            # and back to menu mode
            page.click("#settings-btn")
            page.select_option("#set-rightclick", "menu")
            page.click("#settings-done")
            page.click(".term-pane.active .xterm-screen", button="right")
            page.wait_for_selector("#term-menu:not(.hidden)", timeout=5000)
            browser.close()
    finally:
        _stop_server(server)
        shutil.rmtree(data, ignore_errors=True)


def test_browser_copy_shortcuts_and_help_legend(tmp_path: Path) -> None:
    """Alt+C / Alt+V are the real copy/paste; the help says which chords lie."""
    data = tmp_path / "data"
    files = tmp_path / "files"
    files.mkdir(parents=True)
    server = _start_server(data, files)
    try:
        _wait_health()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
            _login(page)
            page.wait_for_function(
                "() => document.getElementById('connection').textContent === '已连接'",
                timeout=15000,
            )
            page.click(".term-pane.active .xterm-screen")
            page.keyboard.type("echo ALTC-TEST")
            page.keyboard.press("Enter")
            _wait_screen_includes(page, 'ALTC-TEST'),

            # Alt+? opens the cheatsheet from inside the terminal (a bare `?`
            # must reach the shell instead).
            page.keyboard.press("Alt+Shift+/")
            try:
                page.wait_for_selector("#hotkey-overlay:not(.hidden)", timeout=4000)
            except Exception:
                page.keyboard.press("Alt+?")
                page.wait_for_selector("#hotkey-overlay:not(.hidden)", timeout=4000)
            help_text = page.inner_text("#hotkey-help")
            # The defaults must advertise the *reliable* chord...
            assert "复制选区" in help_text
            assert "Alt+C" in help_text, help_text
            # ...and must flag the one the browser steals.
            assert "❌" in help_text, "the help must say Ctrl+Shift+C is unreliable"
            assert "浏览器占用" in help_text
            page.keyboard.press("Escape")

            # a bare `?` typed into the terminal reaches the shell
            page.click(".term-pane.active .xterm-screen")
            page.keyboard.type("echo Q?MARK")
            page.keyboard.press("Enter")
            _wait_screen_includes(page, 'Q?MARK'),
            browser.close()
    finally:
        _stop_server(server)
        shutil.rmtree(data, ignore_errors=True)


def test_browser_ctrl_shift_c_is_either_ours_or_the_browsers(tmp_path: Path) -> None:
    """Press Ctrl+Shift+C and *report* which side won -- do not assume.

    Chrome, Edge and Firefox claim Ctrl+Shift+C for the DevTools inspector at
    the browser level, so `preventDefault()` in the page never gets the chance.
    That is why the defaults moved to Alt+C. This test does not pretend
    otherwise: where the browser wins it is **skipped with that reason**, and
    where the page wins it asserts the copy actually happened.
    """
    data = tmp_path / "data"
    files = tmp_path / "files"
    files.mkdir(parents=True)
    server = _start_server(data, files)
    try:
        _wait_health()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(
                viewport={"width": 1400, "height": 900},
                permissions=["clipboard-read", "clipboard-write"],
            )
            page = context.new_page()
            _login(page)
            page.wait_for_function(
                "() => document.getElementById('connection').textContent === '已连接'",
                timeout=15000,
            )
            page.click(".term-pane.active .xterm-screen")
            page.keyboard.type("echo SECRET-MARK")
            page.keyboard.press("Enter")
            _wait_screen_includes(page, 'SECRET-MARK'),
            # Bring the page's own chord handling into play first, so a
            # toast later is unambiguously ours.
            page.keyboard.press("Control+Shift+F")  # open search (ours)
            with contextlib.suppress(Exception):
                page.wait_for_selector("#search-bar:not(.hidden)", timeout=3000)
            page.keyboard.press("Escape")

            toasts_before = page.locator("#toasts .toast").count()
            page.keyboard.press("Control+Shift+C")
            page.wait_for_timeout(600)
            toasts_after = page.locator("#toasts .toast").count()
            help_open = page.locator("#hotkey-overlay").is_visible()

            if toasts_after > toasts_before:
                # The page handled it: Ctrl+Shift+C is ours in this browser.
                return

            # Otherwise the browser took the chord (DevTools). That is expected
            # on the mainstream desktop browsers and is exactly why the
            # defaults moved -- say so instead of reporting a product failure.
            pytest.skip(
                "this browser reserves Ctrl+Shift+C (DevTools inspect element) "
                "at the chrome level; the page cannot intercept it. "
                "Use Alt+C / Alt+V or the terminal right-click menu instead. "
                f"(help overlay visible after press: {help_open})"
            )
    finally:
        _stop_server(server)
        shutil.rmtree(data, ignore_errors=True)


def test_browser_alt_c_alt_v_are_the_working_copy_paste(tmp_path: Path) -> None:
    """The chords we *do* promise must work regardless of the browser."""
    data = tmp_path / "data"
    files = tmp_path / "files"
    files.mkdir(parents=True)
    server = _start_server(data, files)
    try:
        _wait_health()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(
                viewport={"width": 1400, "height": 900},
                permissions=["clipboard-read", "clipboard-write"],
            )
            page = context.new_page()
            _login(page)
            page.wait_for_function(
                "() => document.getElementById('connection').textContent === '已连接'",
                timeout=15000,
            )
            page.click(".term-pane.active .xterm-screen")
            page.keyboard.type("echo ALTC-MARK")
            page.keyboard.press("Enter")
            _wait_screen_includes(page, 'ALTC-MARK'),
            page.wait_for_timeout(200)
            toasts_before = page.locator("#toasts .toast").count()
            page.keyboard.press("Alt+c")
            page.wait_for_timeout(500)
            toasts_after = page.locator("#toasts .toast").count()
            # With no selection Alt+C must report "没有选中内容" rather than
            # silently do nothing -- that is what makes the chord discoverable.
            assert toasts_after > toasts_before, (
                "Alt+C must produce feedback (a copy or 'nothing selected')"
            )
            body = page.inner_text("#toasts")
            assert ("已复制" in body) or ("没有选中内容" in body), body
            browser.close()
    finally:
        _stop_server(server)
        shutil.rmtree(data, ignore_errors=True)


def test_browser_full_screen_app_survives_a_flood(tmp_path: Path) -> None:
    """`vi` must stay readable through a flood of colour sequences.

    This is the case that had zero coverage and is the honest limit of
    "shed frames to keep the connection": dropping bytes across an ``ESC[31m``
    boundary leaves the terminal parsing the tail of a colour as text, and a
    full-screen program then renders as garbage. Shedding is now cut on
    sequence boundaries, and this asserts the consequence end to end.
    """
    data = tmp_path / "data"
    files = tmp_path / "files"
    files.mkdir(parents=True)
    server = _start_server(data, files)
    try:
        _wait_health()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
            _login(page)
            page.wait_for_function(
                "() => document.getElementById('connection').textContent === '已连接'",
                timeout=15000,
            )
            page.click(".term-pane.active .xterm-screen")

            # A full-screen program: `vi` on a temp file, with a known banner.
            page.keyboard.type("vi /tmp/wsctl-fs-probe.txt")
            page.keyboard.press("Enter")
            page.wait_for_timeout(1200)
            rows = page.evaluate("() => window.__wsctlScreen()")
            assert rows.strip(), "vi never drew anything"

            # Now flood colour sequences from a second session -- the exact
            # input that used to cut `ESC[31m` in half and garble `vi`.
            headers = _headers_from(page)
            other = httpx.post(
                f"{BASE}/api/sessions", headers=headers,
                json={"name": "flood", "command": "sh"}, timeout=10,
            ).json()["id"]
            for _ in range(40):
                httpx.post(
                    f"{BASE}/api/sessions/{other}/recording/start", headers=headers,
                    json={}, timeout=5,
                )
                httpx.post(
                    f"{BASE}/api/sessions/{other}/recording/stop", headers=headers,
                    timeout=5,
                )

            # Back on the `vi` tab: the screen must still be *terminal output*,
            # not a wall of raw escape fragments or replacement glyphs.
            page.wait_for_timeout(800)
            rows_after = page.evaluate("() => window.__wsctlScreen()")
            assert "\ufffd" not in rows_after, "the terminal rendered replacement glyphs"
            assert "[31m" not in rows_after, "a colour escape leaked through as text"
            assert "\x1b" not in rows_after
            browser.close()
    finally:
        _stop_server(server)
        shutil.rmtree(data, ignore_errors=True)


# -- 0.1.15 行为验证 ----------------------------------------------------
#
# The structural contracts live in ``tests/test_ui_contracts.py``; these verify
# the same fixes where a user would notice them.


def test_browser_admin_tab_matches_its_content(tmp_path: Path) -> None:
    """The lit tab and the panel underneath must be the same tab.

    ``index.html`` marked 概览 as selected while ``adminTab`` still said
    ``"users"``, so the panel opened showing one tab highlighted and another's
    content. The selectors are also scoped to ``#admin-overlay``: a bare
    ``.tab2`` matches the session dialog's 运行中/已结束 tabs and used to
    clear their highlight whenever an admin tab was clicked.
    """
    data = tmp_path / "data"
    files = tmp_path / "files"
    files.mkdir(parents=True)
    server = _start_server(data, files)
    try:
        _wait_health()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
            _login(page)
            page.wait_for_function(
                "() => document.getElementById('connection').textContent === '已连接'",
                timeout=WAIT_MS,
            )
            # Open the session dialog first: its tabs must survive admin clicks.
            page.click("#sessions-btn")
            page.wait_for_selector("#sessions-overlay:not(.hidden)", timeout=WAIT_MS)
            page.wait_for_selector("[data-stab].active", timeout=WAIT_MS)
            assert page.inner_text("[data-stab].active").strip() == "运行中"
            # The session dialog is a full-screen overlay -- it would swallow
            # clicks on the toolbar underneath. Reopen the admin panel the way a
            # user would after closing it.
            page.keyboard.press("Escape")
            page.wait_for_selector("#sessions-overlay.hidden", state="attached", timeout=WAIT_MS)

            page.click("#admin-btn")
            page.wait_for_selector("#admin-overlay:not(.hidden)", timeout=WAIT_MS)
            page.wait_for_selector("#admin-body .overview-card", timeout=WAIT_MS)
            lit = page.inner_text("#admin-overlay .tab2.active").strip()
            body = page.inner_text("#admin-body")
            assert lit == "概览", f"lit tab is {lit!r} but the panel shows {body[:60]!r}"
            assert "运行中会话" in body, "the overview content must be under the overview tab"

            # Switching moves both halves together.
            page.click("#admin-overlay .tab2[data-tab='users']")
            page.wait_for_selector("#admin-body .admin-table", timeout=WAIT_MS)
            assert page.inner_text("#admin-overlay .tab2.active").strip() == "用户"
            assert "用户名" in page.inner_text("#admin-body")
            # ...and the session dialog's own tabs are untouched.
            assert page.inner_text("[data-stab].active").strip() == "运行中"
            browser.close()
    finally:
        _stop_server(server)
        shutil.rmtree(data, ignore_errors=True)


def test_browser_toasts_auto_dismiss_fold_and_dedupe(tmp_path: Path) -> None:
    """No toast is permanent, the stack folds past four, repeats count.

    Errors used to stay until clicked, which meant one batch operation left a
    dozen of them pinned over the terminal. Everything ages out now; hovering
    the stack pauses the clock so a message can still be read.
    """
    data = tmp_path / "data"
    files = tmp_path / "files"
    files.mkdir(parents=True)
    server = _start_server(data, files)
    try:
        _wait_health()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
            _login(page)
            page.wait_for_function(
                "() => document.getElementById('connection').textContent === '已连接'",
                timeout=WAIT_MS,
            )
            page.click(".term-pane.active .xterm-screen")

            # A distinct message per font step: 6 toasts, none repeated.
            for _ in range(6):
                page.keyboard.press("Alt+e")
                page.wait_for_timeout(60)
            page.wait_for_function(
                "() => document.querySelectorAll('#toasts .toast:not(.toast-more)').length >= 5",
                timeout=WAIT_MS,
            )
            visible = page.locator("#toasts .toast:not(.toast-more):not(.toast-folded)")
            assert visible.count() <= 4, f"{visible.count()} toasts on screen covers the terminal"
            more = page.locator("#toasts .toast-more")
            assert more.count() == 1, "the overflow must be folded behind one chip"
            assert "还有" in more.inner_text()

            # Unfolding brings them all back, still capped in the DOM.
            more.click()
            page.wait_for_timeout(120)
            assert page.locator("#toasts .toast-folded").count() == 0

            # Auto-dismiss: an `info` toast lives ~4s. Hover the stack first and
            # prove the clock pauses, then leave and let it expire.
            # `#toasts` itself is pointer-events:none (so it never eats clicks
            # on the terminal); hover a card -- the pause listeners are on the
            # container and fire for the whole stack.
            page.hover("#toasts .toast")
            page.wait_for_timeout(4500)
            assert page.locator("#toasts .toast:not(.toast-more)").count() > 0, (
                "hovering must pause the dismiss clock"
            )
            page.mouse.move(10, 10)
            page.wait_for_timeout(5000)
            assert page.locator("#toasts .toast:not(.toast-more)").count() == 0, (
                "toasts must age out; none may be permanent"
            )

            # Repeats collapse into one card with a count instead of stacking.
            page.click(".term-pane.active .xterm-screen")
            for _ in range(3):
                page.keyboard.press("Alt+c")  # copy with no selection
                page.wait_for_timeout(80)
            cards = page.locator("#toasts .toast:not(.toast-more)")
            assert cards.count() == 1, f"a repeated message stacked {cards.count()} cards"
            assert "×3" in cards.first.inner_text(), cards.first.inner_text()
            browser.close()
    finally:
        _stop_server(server)
        shutil.rmtree(data, ignore_errors=True)


def test_browser_settings_dialog_is_wide_grouped_and_reports_the_renderer(
    tmp_path: Path,
) -> None:
    """The settings dialog must fit its own content -- and say what it is using.

    It sat in the 360px card alongside a theme gallery and fifteen key
    bindings, which is a scroll inside a scroll with the labels squeezed. The
    renderer note is not decoration: WebGL silently falls back on machines
    without it, and "which one am I on" is the first question in any
    performance report.
    """
    data = tmp_path / "data"
    files = tmp_path / "files"
    files.mkdir(parents=True)
    server = _start_server(data, files)
    try:
        _wait_health()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
            _login(page)
            page.wait_for_function(
                "() => document.getElementById('connection').textContent === '已连接'",
                timeout=WAIT_MS,
            )
            page.click("#settings-btn")
            page.wait_for_selector("#settings-overlay:not(.hidden)", timeout=WAIT_MS)

            box = page.locator("#settings-overlay .modal-card").bounding_box()
            assert box is not None and box["width"] >= 500, (
                f"settings card is only {box and box['width']:.0f}px wide"
            )
            assert page.locator("#settings-overlay .field-group").count() >= 3, (
                "settings must be grouped, not one wall of labels"
            )
            assert page.locator("#hotkey-list.grid").count() == 1, (
                "the key-binding list must use the two-column grid"
            )
            note = page.inner_text("#renderer-note")
            assert "当前：" in note, note
            assert "Unicode" in note, note
            assert re.search(r"Unicode\s+11", note), (
                f"the Unicode 11 width table is not active: {note!r}"
            )
            assert re.search(r"当前：(WebGL|Canvas|DOM)", note), note

            # And the confirm dialog's weight follows the risk.
            page.click("#settings-done")
            browser.close()
    finally:
        _stop_server(server)
        shutil.rmtree(data, ignore_errors=True)
