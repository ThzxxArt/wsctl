#!/usr/bin/env python3
"""Backend end-to-end scenarios for wsctl.

Runs ten scenarios, each against a freshly started server on its own port and
data directory:

1. server    — health, login, WebSocket attach/exec, reconnect replay, metrics,
               file panel (list/upload/download), traversal guard, auth guard
1b. files    — the file panel surface: mkdir, upload (atomic), rename, preview,
               edit, paging, type filter, delete guards
1c. selfrestart — restarting from inside your own session: `stop` is refused,
               `stop` of nothing is idempotent, `restart --rolling` never drops
               a /healthz probe
2. cli       — wsctl login / session new|list|kill
3. connect   — the CLI thin client over a real pseudo-terminal
4. tmux      — a tmux-backed session survives a full server restart
5. crash     — a SIGKILLed instance's tmux session is adopted by a new one
6. multiplex — two instances share one data directory without interfering
7. daemon    — background lifecycle (start/status/logs/reload/restart/stop)
8. restart   — two instances share a port via SO_REUSEPORT (zero downtime)

Usage:
    pip install -e ".[dev]"
    python scripts/e2e/run_all.py            # all scenarios
    python scripts/e2e/run_all.py tmux       # one scenario
"""

from __future__ import annotations

import json
import os
import pty
import select
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pyotp
from websockets.sync.client import connect

from wsctl.core import tmux as tmux_mod

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
ADMIN = "admin"
PASSWORD = "testpass123"
# A carriage return, spelled out. Escape sequences inside a shell heredoc
# that writes these tests have already bitten three times: b"\\r" lands in
# the source as a literal backslash and the shell never sees a line ending.
CR = bytes([13])


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def local_http():
    """An ``httpx`` client that cannot be hijacked by the developer's proxy.

    A shell that exports ``https_proxy`` makes ``httpx`` send even
    ``http://127.0.0.1:.../healthz`` to the proxy, which answers 502 -- and a
    perfectly healthy local instance looks dead. ``core/net.opener_for`` exists
    because wsctl already learned this lesson about its own CLI; the tests had
    not, and one of them failed with exactly those 502s.
    """
    import httpx as _httpx

    return _httpx.Client(trust_env=False, timeout=10)


def _clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """The subprocess environment, with any inherited ``WSCTL_*`` stripped.

    This suite once failed because the developer's shell happened to export
    ``WSCTL_DAEMON=1`` (a value ``wsctl`` itself sets for the children it
    spawns). Every server started here then believed it was a managed daemon
    and went for the pid file -- so the second of two ``--reuse-port``
    instances died with "已有实例在运行". Same class of defect as the test-suite
    isolation work: a test that reads the developer's shell is not a test.
    """
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("WSCTL_")
        # A developer's shell proxy must not reach these servers either -- it
        # turns a healthy 127.0.0.1 probe into a 502 from the proxy.
        and k.lower() not in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")
    }
    if extra:
        env.update(extra)
    return env


def spawn(port: int, data: Path, *, extra_env: dict[str, str] | None = None,
          reuse_port: bool = False) -> subprocess.Popen[bytes]:
    extra = dict(extra_env or {})
    extra["WSCTL_DATA_DIR"] = str(data)
    env = _clean_env(extra)
    cmd = [PY, "-m", "wsctl", "serve", "--port", str(port), "--host", "127.0.0.1",
           "--admin-password", PASSWORD, "--log-level", "warning"]
    if reuse_port:
        cmd.append("--reuse-port")
    # Keep the child's output in a per-port log so a startup failure can be
    # diagnosed instead of showing only "server exited early".
    log_path = data / f"server-{port}.log"
    SERVER_LOGS[port] = log_path
    # Deliberately not closed here: the descriptor is inherited by the child and
    # must stay open for the whole life of the server.
    log = open(log_path, "ab")  # noqa: SIM115
    return subprocess.Popen(cmd, cwd=str(ROOT), env=env,
                            stdout=log, stderr=subprocess.STDOUT)


def stop(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


SERVER_LOGS: dict[int, Path] = {}


def _server_log_tail(port: int) -> str:
    path = SERVER_LOGS.get(port)
    if path is None or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-2000:]
    except OSError:
        return ""


def wait_health(base: str, proc: subprocess.Popen[bytes] | None = None,
                timeout: float = 20.0) -> None:
    try:
        port = int(base.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        port = -1
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            raise SystemExit(
                f"server exited early ({proc.returncode})\n{_server_log_tail(port)}"
            )
        try:
            if local_http().get(f"{base}/healthz", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.2)
    raise SystemExit("server never became healthy")


@contextmanager
def server(*, extra_env: dict[str, str] | None = None) -> Iterator[tuple[str, Path, int]]:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    data = Path(tempfile.mkdtemp(prefix="wsctl-e2e-"))
    proc = spawn(port, data, extra_env=extra_env)
    try:
        wait_health(base, proc)
        yield base, data, port
    finally:
        stop(proc)
        shutil.rmtree(data, ignore_errors=True)


def login(base: str) -> str:
    r = httpx.post(f"{base}/api/login", json={"username": ADMIN, "password": PASSWORD}, timeout=10)
    r.raise_for_status()
    token = r.cookies.get("wsctl_session")
    assert token, "no session cookie"
    return token


def ws_recv_until(ws: object, marker: bytes, timeout: float = 10.0) -> bytes:
    """Collect terminal output until ``marker`` shows up or the deadline passes.

    A single ``recv`` timeout must not end the wait -- doing that meant any
    quiet gap (a command that takes a moment to start, say) truncated the
    capture and the caller only ever saw the echoed command line.
    """
    out = b""
    end = time.time() + timeout
    while time.time() < end and marker not in out:
        try:
            msg = ws.recv(timeout=2)  # type: ignore[attr-defined]
        except TimeoutError:
            continue
        if isinstance(msg, bytes):
            out += msg
    return out


def _tmux_has(name: str) -> bool:
    return subprocess.run(
        ["tmux", "has-session", "-t", name], check=False,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


def _wait_tmux(name: str, timeout: float = 10.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if _tmux_has(name):
            return True
        time.sleep(0.25)
    return False


def _tmux_ls() -> list[str]:
    result = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True, text=True, check=False,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


# -- scenarios --------------------------------------------------------


def ws_drain(ws: object, seconds: float = 5.0) -> bytes:
    """Collect terminal output for a fixed wall-clock, then stop.

    ``ws_recv_until`` stops at the first match of its marker -- but the marker
    is usually a substring of the *command we typed*, so the terminal's own echo
    satisfies it instantly and the caller tests nothing. Draining for a fixed
    duration and asserting on content afterwards cannot be fooled that way.
    """
    out = b""
    end = time.time() + seconds
    while time.time() < end:
        try:
            msg = ws.recv(timeout=1)  # type: ignore[attr-defined]
        except TimeoutError:
            continue
        if isinstance(msg, bytes):
            out += msg
    return out


def scenario_server() -> None:
    files = Path(tempfile.mkdtemp(prefix="wsctl-files-"))
    (files / "seed.txt").write_text("seeded")
    try:
        with server(extra_env={"WSCTL_FILE_ROOT": str(files)}) as (base, _, _):
            token = login(base)
            headers = {"Cookie": f"wsctl_session={token}"}
            ws_url = base.replace("http", "ws") + "/ws"

            # Produce output, drop the link, re-attach: the scrollback replay
            # must bring the content back. The scenario list has claimed
            # "reconnect replay" since day one while the body only ever opened
            # one socket -- the marker fix alone did not make that true.
            with connect(ws_url, additional_headers=headers, open_timeout=10) as ws:
                ws.send(json.dumps({"type": "attach", "cols": 100, "rows": 30}))
                ws.send(b"echo $((67*71))\r")
                assert b"4757\r\n" in ws_recv_until(ws, b"4757\r\n")
            # Reconnect to the same session and demand the replay.
            token2 = login(base)
            headers2 = {"Cookie": f"wsctl_session={token2}"}
            sessions = httpx.get(f"{base}/api/sessions", headers=headers2, timeout=10).json()
            assert sessions, "the session must outlive its connection"
            sid = sessions[0]["id"]
            with connect(ws_url, additional_headers=headers2, open_timeout=10) as ws:
                ws.send(json.dumps({
                    "type": "attach", "session": sid, "cols": 100, "rows": 30,
                }))
                # The *replayed* stream carries the arithmetic product; the
                # command line only ever spelled `67*71`, so this cannot be
                # satisfied by an echo of a command we type here (we type none).
                assert b"4757" in ws_recv_until(ws, b"4757"), (
                    "reconnect replay did not bring the output back"
                )

            with httpx.Client(base_url=base, timeout=10) as http:
                listing = http.get("/api/files", headers=headers).json()["entries"]
                assert "seed.txt" in {e["name"] for e in listing}
                up = http.post("/api/files/upload", headers=headers, data={"path": ""},
                               files={"file": ("up.txt", b"data")})
                assert up.status_code == 201
                assert http.get("/api/files/download", headers=headers,
                                params={"path": "up.txt"}).content == b"data"
                assert http.get("/api/files", headers=headers,
                                params={"path": "../.."}).status_code == 400
                assert "wsctl_up" in http.get("/metrics").text
                assert http.get("/api/me").status_code == 401  # no cookie
    finally:
        shutil.rmtree(files, ignore_errors=True)
    print("  server: ok")


def scenario_cli() -> None:
    conf = Path(tempfile.mkdtemp(prefix="wsctl-conf-"))
    try:
        with server() as (base, data, _):
            # `wsctl user` talks to the local store, so it must be pointed at the
            # same data directory the server under test is using.
            env = _clean_env(
                {"XDG_CONFIG_HOME": str(conf), "WSCTL_DATA_DIR": str(data)}
            )

            def run(*args: str) -> str:
                r = subprocess.run([PY, "-m", "wsctl", *args], cwd=str(ROOT), env=env,
                                   capture_output=True, text=True, timeout=30)
                assert r.returncode == 0, f"{args}: {r.stdout}\n{r.stderr}"
                return r.stdout

            run("login", base, "-u", ADMIN, "-p", PASSWORD)
            out = run("session", "new", "--command", "sleep 30", "--name", "clitest")
            sid = out.split("已创建会话")[1].split()[0].split("（")[0]
            assert "clitest" in run("session", "list")
            run("session", "kill", sid)

            # --json must be machine-readable.
            run("session", "new", "--command", "sleep 30", "--name", "jsoncase", "--json")
            payload = json.loads(run("session", "list", "--json"))
            names = {row["name"] for row in payload}
            assert "jsoncase" in names, payload
            json_sid = next(row["id"] for row in payload if row["name"] == "jsoncase")
            run("session", "kill", json_sid)

            # A two-factor account must still be usable from the CLI (0.1.3
            # lock-out regression): enable 2FA over the API, then log in with
            # a computed code.
            #
            # Enrolment is two-step everywhere (0.1.22): ``POST /totp`` only
            # creates a *pending* secret -- it becomes active when a code from
            # the authenticator verifies, so a scan that never happens cannot
            # lock the account out. The scenario must complete that second step
            # before it can demand a code at login.
            run("user", "add", "totpuser", "-p", PASSWORD)
            admin = login(base)
            info = httpx.post(
                f"{base}/api/users/totpuser/totp",
                headers={"Cookie": f"wsctl_session={admin}"},
                timeout=10,
            )
            assert info.status_code == 200, info.text
            secret = info.json()["secret"]
            code = pyotp.TOTP(secret).now()
            confirmed = httpx.post(
                f"{base}/api/users/totpuser/totp/confirm",
                headers={"Cookie": f"wsctl_session={admin}"},
                json={"code": code},
                timeout=10,
            )
            assert confirmed.status_code == 200, confirmed.text

            run("logout")
            no_code = subprocess.run(
                [PY, "-m", "wsctl", "login", base, "-u", "totpuser", "-p", PASSWORD],
                cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=30,
            )
            assert no_code.returncode != 0, "2FA account logged in without a code"
            assert "验证码" in (no_code.stdout + no_code.stderr)

            run("login", base, "-u", "totpuser", "-p", PASSWORD, "--totp", code)
            run("session", "list")
    finally:
        shutil.rmtree(conf, ignore_errors=True)
    print("  cli: ok")


def scenario_connect() -> None:
    with server() as (base, _, _):
        token = login(base)
        master, slave = pty.openpty()
        client = subprocess.Popen(
            [PY, "-m", "wsctl", "connect", base, "--token", token],
            cwd=str(ROOT), stdin=slave, stdout=slave, stderr=slave,
            env={**os.environ, "TERM": "xterm-256color"},
        )
        os.close(slave)
        output = bytearray()

        def drain(seconds: float) -> None:
            end = time.time() + seconds
            while time.time() < end:
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    try:
                        output.extend(os.read(master, 4096))
                    except OSError:
                        return

        try:
            drain(2.0)
            os.write(master, b"echo $((73*79))\r")
            for _ in range(50):
                drain(0.2)
                if b"5767\r\n" in output:
                    break
            assert b"5767\r\n" in output, output[-200:]
            os.write(master, b"exit\r")
            client.wait(timeout=5)
        finally:
            if client.poll() is None:
                client.kill()
            os.close(master)
    print("  connect: ok")


def scenario_tmux() -> None:
    if shutil.which("tmux") is None:
        print("  tmux: skipped (tmux not installed)")
        return
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    data = Path(tempfile.mkdtemp(prefix="wsctl-tmux-"))
    tmux_mod.set_namespace(data)  # the server namespaces by data dir
    name = tmux_mod.session_name  # bound after namespace is set
    env = _clean_env({"WSCTL_DATA_DIR": str(data), "WSCTL_DEFAULT_BACKEND": "tmux"})
    first = spawn(port, data, extra_env=env)
    second: subprocess.Popen[bytes] | None = None
    sid = ""
    try:
        wait_health(base, first)
        token = login(base)
        headers = {"Cookie": f"wsctl_session={token}"}
        ws_url = base.replace("http", "ws") + "/ws"
        sid = httpx.post(f"{base}/api/sessions", headers=headers, json={}, timeout=10).json()["id"]

        with connect(ws_url, additional_headers=headers, open_timeout=10) as ws:
            ws.send(json.dumps({"type": "attach", "session": sid, "cols": 100, "rows": 30}))
            ws.send(b"echo $((83*89))\r")
            assert b"7387\r\n" in ws_recv_until(ws, b"7387\r\n")

        assert _wait_tmux(name(sid)), (
            "tmux session missing while the server is running: " + " ".join(_tmux_ls())
        )
        share = httpx.post(
            f"{base}/api/sessions/{sid}/share", headers=headers, json={}, timeout=10
        )
        assert share.status_code == 200, share.text
        share_token = share.json()["token"]

        stop(first)  # graceful: preserves the tmux session
        if not _wait_tmux(name(sid)):
            raise AssertionError(
                "tmux session lost after server stop: " + " ".join(_tmux_ls())
            )
        second = spawn(port, data, extra_env=env)
        wait_health(base, second)

        token = login(base)
        headers = {"Cookie": f"wsctl_session={token}"}
        sessions = httpx.get(f"{base}/api/sessions", headers=headers, timeout=10).json()
        assert any(s["id"] == sid for s in sessions), sessions

        with connect(ws_url, additional_headers=headers, open_timeout=10) as ws:
            ws.send(json.dumps({"type": "attach", "session": sid, "cols": 100, "rows": 30}))
            assert b"7387" in ws_recv_until(ws, b"7387"), "screen not restored"

        # A share link must outlive the restart it was created before. The
        # anonymous viewer authenticates with the token in the query string,
        # exactly like the browser share link.
        share_url = f"{ws_url}?share={share_token}"
        with connect(share_url, open_timeout=10) as ws:
            ws.send(json.dumps({
                "type": "attach", "session": sid, "share": share_token,
                "cols": 100, "rows": 30,
            }))
            assert b"7387" in ws_recv_until(ws, b"7387"), (
                "share link did not survive the restart"
            )
    finally:
        if second is not None:
            stop(second)
        if sid:
            subprocess.run(["tmux", "kill-session", "-t", name(sid)],
                           check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tmux_mod.set_namespace(None)
        shutil.rmtree(data, ignore_errors=True)
    print("  tmux: ok")


def scenario_crash() -> None:
    """A SIGKILLed instance's tmux session is adopted immediately by a new one."""
    if shutil.which("tmux") is None:
        print("  crash: skipped (tmux not installed)")
        return
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    data = Path(tempfile.mkdtemp(prefix="wsctl-crash-"))
    tmux_mod.set_namespace(data)
    env = _clean_env({"WSCTL_DATA_DIR": str(data), "WSCTL_DEFAULT_BACKEND": "tmux"})
    first = spawn(port, data, extra_env=env)
    second: subprocess.Popen[bytes] | None = None
    sid = ""
    try:
        wait_health(base, first)
        token = login(base)
        headers = {"Cookie": f"wsctl_session={token}"}
        ws_url = base.replace("http", "ws") + "/ws"
        sid = httpx.post(
            f"{base}/api/sessions", headers=headers, json={}, timeout=10
        ).json()["id"]

        with connect(ws_url, additional_headers=headers, open_timeout=10) as ws:
            ws.send(json.dumps({"type": "attach", "session": sid, "cols": 100, "rows": 30}))
            ws.send(b"echo $((97*101))\r")
            assert b"9797\r\n" in ws_recv_until(ws, b"9797\r\n")
        assert _wait_tmux(tmux_mod.session_name(sid))

        # SIGKILL: the lease is NOT removed gracefully, unlike a clean shutdown.
        first.kill()
        first.wait(timeout=10)

        second = spawn(port, data, extra_env=env)
        wait_health(base, second)
        token = login(base)
        headers = {"Cookie": f"wsctl_session={token}"}
        sessions = httpx.get(f"{base}/api/sessions", headers=headers, timeout=10).json()
        assert any(s["id"] == sid for s in sessions), sessions

        with connect(ws_url, additional_headers=headers, open_timeout=10) as ws:
            ws.send(json.dumps({"type": "attach", "session": sid, "cols": 100, "rows": 30}))
            assert b"9797" in ws_recv_until(ws, b"9797"), "screen not restored"
    finally:
        if second is not None:
            stop(second)
        if sid:
            subprocess.run(["tmux", "kill-session", "-t", tmux_mod.session_name(sid)],
                           check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tmux_mod.set_namespace(None)
        shutil.rmtree(data, ignore_errors=True)
    print("  crash: ok")


def scenario_multiplex() -> None:
    """Two instances sharing one data_dir must never interfere with each other."""
    port_a = free_port()
    port_b = free_port()
    base_a = f"http://127.0.0.1:{port_a}"
    base_b = f"http://127.0.0.1:{port_b}"
    data = Path(tempfile.mkdtemp(prefix="wsctl-multi-"))
    first = spawn(port_a, data)
    second = spawn(port_b, data)
    try:
        wait_health(base_a, first)
        wait_health(base_b, second)
        token = login(base_a)
        headers = {"Cookie": f"wsctl_session={token}"}
        sid = httpx.post(
            f"{base_a}/api/sessions", headers=headers, json={"name": "owned-by-a"}, timeout=10
        ).json()["id"]

        # Let instance B's maintenance loop run at least once (interval is 5s).
        time.sleep(7)

        conn = sqlite3.connect(data / "wsctl.db")
        try:
            row = conn.execute(
                "SELECT status, instance_id FROM term_sessions WHERE id = ?", (sid,)
            ).fetchone()
        finally:
            conn.close()
        assert row is not None, "session row missing"
        assert row[0] == "running", f"peer instance marked our session as {row[0]}"
        assert row[1], "session row has no owning instance id"

        sessions = httpx.get(f"{base_a}/api/sessions", headers=headers, timeout=10).json()
        assert any(s["id"] == sid for s in sessions), sessions

        assert httpx.delete(
            f"{base_a}/api/sessions/{sid}", headers=headers, timeout=10
        ).status_code == 200
    finally:
        stop(first)
        stop(second)
        shutil.rmtree(data, ignore_errors=True)
    print("  multiplex: ok")


def _health_ok(base: str) -> bool:
    try:
        return httpx.get(f"{base}/healthz", timeout=1).status_code == 200
    except httpx.HTTPError:
        return False


def scenario_daemon() -> None:
    """Non-systemd background lifecycle: start/status/logs/reload/restart/stop."""
    if sys.platform == "win32":
        print("  daemon: skipped (POSIX only)")
        return
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    data = Path(tempfile.mkdtemp(prefix="wsctl-daemon-"))
    config = data / "config.toml"
    config.write_text(
        f'port = {port}\ndata_dir = "{data}"\nmax_sessions = 5\n', encoding="utf-8"
    )
    env = _clean_env({"WSCTL_CONFIG": str(config)})

    def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [PY, "-m", "wsctl", *args, "--config", str(config)],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if check:
            assert result.returncode == 0, f"{args}: {result.stdout}\n{result.stderr}"
        return result

    try:
        run("start", "--timeout", "90", "--admin-password", PASSWORD)
        wait_health(base)

        payload = json.loads(run("status", "--json").stdout)
        assert payload["running"] is True and payload["port"] == port, payload

        # A second start must be refused rather than silently duplicated.
        assert run("start", "--admin-password", PASSWORD, check=False).returncode != 0

        # SIGHUP reload picks up a config change.
        config.write_text(
            f'port = {port}\ndata_dir = "{data}"\nmax_sessions = 9\n', encoding="utf-8"
        )
        run("reload")
        deadline = time.time() + 12
        while time.time() < deadline:
            if "config reloaded" in run("logs", "-n", "60").stdout:
                break
            time.sleep(0.5)
        else:
            raise AssertionError("SIGHUP did not trigger a config reload")

        # restart keeps the instance reachable
        run("restart", "--timeout", "90", "--admin-password", PASSWORD)
        wait_health(base)

        # A SIGKILLed instance leaves a stale pid file that status prunes.
        pidfile = data / "run" / f"wsctl-{port}.pid"
        pid = int(json.loads(pidfile.read_text())["pid"])
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.5)
        assert run("status", check=False).returncode != 0, "stale pid file not pruned"

        run("start", "--timeout", "90", "--admin-password", PASSWORD)
        wait_health(base)
        run("stop")
        assert not _health_ok(base), "server still answering after stop"
    finally:
        subprocess.run(
            [PY, "-m", "wsctl", "stop", "--config", str(config)],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
        )
        shutil.rmtree(data, ignore_errors=True)
    print("  daemon: ok")


def scenario_restart() -> None:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    data = Path(tempfile.mkdtemp(prefix="wsctl-reuse-"))
    first = spawn(port, data, reuse_port=True)
    second: subprocess.Popen[bytes] | None = None
    try:
        wait_health(base, first)
        second = spawn(port, data, reuse_port=True)  # same port via SO_REUSEPORT
        time.sleep(2.5)  # let the second instance finish binding
        assert second.poll() is None, "second instance exited"
        stop(first)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                if httpx.get(f"{base}/healthz", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            raise AssertionError("second instance not serving after first stopped")
    finally:
        if second is not None:
            stop(second)
        shutil.rmtree(data, ignore_errors=True)
    print("  restart: ok")


def scenario_files() -> None:
    """The file panel's whole surface: mkdir -> upload -> rename -> preview -> edit -> delete.

    This is the acceptance scenario the 0.1.8 plan named. It exists because the
    panel's verbs used to be exactly three (list / download / upload) and a
    later "complete" rewrite could silently drop one of the new ones.
    """
    files = Path(tempfile.mkdtemp(prefix="wsctl-files-"))
    (files / "seed.txt").write_text("seeded 原文")
    try:
        with server(extra_env={"WSCTL_FILE_ROOT": str(files)}) as (base, _, _):
            token = login(base)
            headers = {"Cookie": f"wsctl_session={token}"}
            with httpx.Client(base_url=base, timeout=10, headers=headers) as http:
                # mkdir
                made = http.post("/api/files/mkdir", json={"name": "notes"})
                assert made.status_code == 201, made.text
                assert (files / "notes").is_dir()

                # an illegal name is refused (traversal / reserved prefix)
                for bad in ("..", "a/b", ".wsctl-upload"):
                    assert http.post("/api/files/mkdir", json={"name": bad}).status_code == 400

                # upload into the new directory (and drag-onto-folder is the
                # same call with a different ``path``)
                # Deliberately contains a NUL byte: an ASCII-only payload is
                # *text*, and the preview would (correctly) accept it -- so
                # "binary refuses to preview" would never be exercised below.
                payload = b"pay\x00load-1"
                up = http.post(
                    "/api/files/upload",
                    data={"path": "notes"},
                    files={"file": ("up.bin", payload)},
                )
                assert up.status_code == 201, up.text

                # atomic publish: a reader must see all-or-nothing
                got = http.get("/api/files/download", params={"path": "notes/up.bin"})
                assert got.content == payload

                # rename
                renamed = http.post(
                    "/api/files/rename", json={"path": "notes/up.bin", "name": "done.bin"}
                )
                assert renamed.status_code == 200, renamed.text
                assert (files / "notes" / "done.bin").is_file()

                # preview
                prev = http.get("/api/files/preview", params={"path": "seed.txt"})
                assert prev.status_code == 200, prev.text
                assert "原文" in prev.text

                # binary refuses to preview (with a reason, not a 500)
                binp = http.get("/api/files/preview", params={"path": "notes/done.bin"})
                assert binp.status_code == 400
                assert "二进制" in binp.text

                # edit in place
                saved = http.put(
                    "/api/files/content",
                    json={"path": "seed.txt", "content": "grown 成长"},
                )
                assert saved.status_code == 200, saved.text
                assert (files / "seed.txt").read_text() == "grown 成长"

                # paging reaches past the old 2000-entry hard stop
                for i in range(30):
                    (files / f"n{i:02d}.txt").write_text("x")
                page1 = http.get("/api/files", params={"limit": 25, "offset": 0}).json()
                page2 = http.get("/api/files", params={"limit": 25, "offset": 25}).json()
                # seed.txt + notes/ + 30 n*.txt
                assert page1["total"] == page2["total"] == 32
                seen = {e["name"] for e in page1["entries"]} | {e["name"] for e in page2["entries"]}
                assert len(seen) == 32

                # type filter
                only_dirs = http.get("/api/files", params={"kind": "dir"}).json()
                assert {e["name"] for e in only_dirs["entries"]} == {"notes"}

                # delete refuses a non-empty directory and the root
                refused = http.request(
                    "DELETE", "/api/files", params={"path": "", "name": "notes"}
                )
                assert refused.status_code == 400 and "非空" in refused.text
                root = http.request("DELETE", "/api/files", params={"path": "", "name": ""})
                assert root.status_code == 400

                # delete a file, then an empty directory
                assert http.request(
                    "DELETE", "/api/files", params={"path": "notes", "name": "done.bin"}
                ).status_code == 200
                assert http.request(
                    "DELETE", "/api/files", params={"path": "", "name": "notes"}
                ).status_code == 200
                assert not (files / "notes").exists()
    finally:
        shutil.rmtree(files, ignore_errors=True)
    print("  files: ok")


def scenario_selfrestart() -> None:
    """Restarting the terminal *from inside itself* must not saw off the branch.

    Three things, and the first is the exact accident that locked a remote user
    out of their own box: ``wsctl stop && wsctl start`` typed into a wsctl
    terminal. ``stop`` worked, ``manager.shutdown()`` tore down the session,
    ``killpg`` took the shell with it, and ``start`` never ran.

    1. ``wsctl stop`` issued from inside one of the instance's own sessions is
       **refused**, with an explanation and a way out.
    2. ``wsctl stop`` on a port with nothing running exits 0 (idempotent), so a
       hand-written ``stop && start`` chain cannot silently skip the start.
    3. ``wsctl restart --rolling`` replaces the instance with **no** gap in
       ``/healthz`` -- the operation that should have been used.
    """
    if sys.platform == "win32":
        print("  selfrestart: skipped (POSIX only)")
        return

    # ---- 1. stop from inside the session must be refused --------------------
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    data = Path(tempfile.mkdtemp(prefix="wsctl-self-"))
    proc = spawn(port, data)
    try:
        wait_health(base, proc)
        token = login(base)
        headers = {"Cookie": f"wsctl_session={token}"}
        ws_url = base.replace("http", "ws") + "/ws"
        sid = httpx.post(f"{base}/api/sessions", headers=headers, json={}, timeout=10).json()["id"]

        # A plain newline, not a raw CR: this file is written through a
        # shell heredoc and "\\r" would land in the source as a literal
        # backslash, leaving the shell waiting for the rest of the command.
        # Every marker below is un-echoable: it is produced by the shell
        # (`$?`, `$((6*7))`) and cannot appear in the command we typed, so a
        # passing assertion is proof of execution rather than of terminal echo.
        with connect(ws_url, additional_headers=headers, open_timeout=10) as ws:
            ws.send(json.dumps({"type": "attach", "session": sid, "cols": 160, "rows": 40}))
            ws.send(b"echo PROBE-$((6*7))" + CR)
            probe = ws_drain(ws, 3.0)
            assert b"PROBE-42" in probe, (
                "the shell never executed anything -- only echoed it: " + repr(probe[:200])
            )
            cmd = (
                f"echo GATE-$((2*2)); "
                f"{PY} -m wsctl stop --port {port} 2>&1; "
                f"echo SELFSTOP-EXIT-$?; echo TAIL-$((3*3))"
            )
            ws.send(cmd.encode() + CR)
            out = ws_drain(ws, 12.0)
            assert b"GATE-4" in out, "the real command never started: " + repr(out[:300])
        decoded = out.decode("utf-8", "replace")
        assert "wsctl restart --rolling" in decoded, (
            f"the refusal must name the way out:\n{decoded}"
        )
        assert "--i-know-this-drops-my-connection" in decoded

        # The instance is still serving: the refusal really did nothing.
        assert local_http().get(f"{base}/healthz", timeout=5).status_code == 200
    finally:
        stop(proc)
        shutil.rmtree(data, ignore_errors=True)
    print("  selfrestart/stop-in-own-session: ok")

    # ---- 2. stopping nothing is a success ------------------------------------
    conf = Path(tempfile.mkdtemp(prefix="wsctl-selfconf-"))
    empty = Path(tempfile.mkdtemp(prefix="wsctl-selfempty-"))
    try:
        env = _clean_env({"XDG_CONFIG_HOME": str(conf), "WSCTL_DATA_DIR": str(empty)})
        result = subprocess.run(
            [PY, "-m", "wsctl", "stop", "--port", str(free_port())],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, (
            f"`stop` of nothing must be idempotent (exit 0) so `stop && start` works: "
            f"{result.stdout}\n{result.stderr}"
        )
    finally:
        shutil.rmtree(conf, ignore_errors=True)
        shutil.rmtree(empty, ignore_errors=True)
    print("  selfrestart/stop-idempotent: ok")

    # ---- 3a. rolling restart is gap-free when the port is shareable ----------
    # SO_REUSEPORT only shares a port when *both* sockets set the option, so
    # this case is the one that can honestly be called zero-downtime.
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    data = Path(tempfile.mkdtemp(prefix="wsctl-roll-"))
    env = _clean_env({"WSCTL_DATA_DIR": str(data)})
    try:
        started = subprocess.run(
            [PY, "-m", "wsctl", "start", "--host", "127.0.0.1", "--port", str(port),
             "--reuse-port", "--log-level", "warning", "--timeout", "90",
             "--admin-password", PASSWORD],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=90,
        )
        assert started.returncode == 0, started.stdout + started.stderr
        wait_health(base)
        pidfile = data / "run" / f"wsctl-{port}.pid"
        old_pid = int(json.loads(pidfile.read_text())["pid"])
        assert json.loads(pidfile.read_text()).get("reuse_port") is True

        gaps = []
        polled = {"n": 0}

        def watch() -> None:
            end = time.time() + 60
            while time.time() < end:
                polled["n"] += 1
                try:
                    got = local_http().get(f"{base}/healthz", timeout=1)
                    if got.status_code != 200:
                        gaps.append(time.time())
                except httpx.HTTPError:
                    gaps.append(time.time())
                time.sleep(0.05)

        import threading

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        rolled = subprocess.run(
            [PY, "-m", "wsctl", "restart", "--rolling", "--log-level", "warning",
             "--timeout", "90", "--port", str(port)],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120,
        )
        watcher.join(timeout=70)

        assert rolled.returncode == 0, rolled.stdout + rolled.stderr
        assert polled["n"] > 10, f"the watcher barely ran ({polled['n']} probes)"
        assert not gaps, f"the swap dropped connections: {gaps[:5]}"
        new_pid = int(json.loads(pidfile.read_text())["pid"])
        assert new_pid != old_pid, "the successor must be a different process"
        assert local_http().get(f"{base}/healthz", timeout=5).json()["pid"] == new_pid
    finally:
        subprocess.run(
            [PY, "-m", "wsctl", "stop", "--port", str(port), "--i-know-this-drops-my-connection"],
            cwd=str(ROOT), env=env, capture_output=True, timeout=60,
        )
        shutil.rmtree(data, ignore_errors=True)
    print("  selfrestart/rolling-gapless: ok")

    # ---- 3b. migrating an instance that cannot share its port ----------------
    # A predecessor without --reuse-port owns the port alone (SO_REUSEPORT only
    # shares when both ends set it), so the first switch has a bounded -- and
    # *announced* -- window. Every rolling restart afterwards is gap-free.
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    data = Path(tempfile.mkdtemp(prefix="wsctl-mig-"))
    env = _clean_env({"WSCTL_DATA_DIR": str(data)})
    try:
        started = subprocess.run(
            [PY, "-m", "wsctl", "start", "--host", "127.0.0.1", "--port", str(port),
             "--log-level", "warning", "--timeout", "90",
             "--admin-password", PASSWORD],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=90,
        )
        assert started.returncode == 0, started.stdout + started.stderr
        wait_health(base)
        pidfile = data / "run" / f"wsctl-{port}.pid"
        old_pid = int(json.loads(pidfile.read_text())["pid"])

        gaps = []
        end = time.time() + 60
        import threading

        stop_watch = threading.Event()

        def watch() -> None:
            while not stop_watch.is_set():
                try:
                    got = local_http().get(f"{base}/healthz", timeout=1)
                    if got.status_code != 200:
                        gaps.append(time.time())
                except httpx.HTTPError:
                    gaps.append(time.time())
                time.sleep(0.05)

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        rolled = subprocess.run(
            [PY, "-m", "wsctl", "restart", "--rolling", "--log-level", "warning",
             "--timeout", "90", "--port", str(port)],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120,
        )
        stop_watch.set()
        watcher.join(timeout=5)
        del end

        assert rolled.returncode == 0, rolled.stdout + rolled.stderr
        # The gap is allowed here -- but it must be small, and the CLI must have
        # said so rather than presenting it as zero-downtime.
        # The bound is measured, not guessed: this window is exactly "how long
        # the predecessor holds the port while shutting down" plus one bind
        # retry (0.15s). A session gets 3s to wind down, so 8s is the honest
        # ceiling -- and the number is printed rather than assumed.
        window = (max(gaps) - min(gaps)) if gaps else 0.0
        print(f"      一次性迁移窗口实测: {window:.2f}s（{len(gaps)} 次探测未连通）")
        assert window <= 8.0, f"the one-off switch was not bounded: {window:.2f}s"
        assert gaps, "the migrate path must show a real (announced) window"
        assert "一次性迁移" in (rolled.stdout + rolled.stderr), (
            "a non-zero gap must be announced up front"
        )
        new_pid = int(json.loads(pidfile.read_text())["pid"])
        assert new_pid != old_pid
        assert json.loads(pidfile.read_text()).get("reuse_port") is True, (
            "after the migration the instance must be shareable"
        )
        assert local_http().get(f"{base}/healthz", timeout=5).json()["pid"] == new_pid

        # And now it is gap-free.
        gaps.clear()
        stop_watch.clear()
        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        rolled2 = subprocess.run(
            [PY, "-m", "wsctl", "restart", "--rolling", "--log-level", "warning",
             "--timeout", "90", "--port", str(port)],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120,
        )
        stop_watch.set()
        watcher.join(timeout=5)
        assert rolled2.returncode == 0, rolled2.stdout + rolled2.stderr
        assert not gaps, f"after the migration rolling restart must be gapless: {gaps[:5]}"
    finally:
        subprocess.run(
            [PY, "-m", "wsctl", "stop", "--port", str(port), "--i-know-this-drops-my-connection"],
            cwd=str(ROOT), env=env, capture_output=True, timeout=60,
        )
        shutil.rmtree(data, ignore_errors=True)
    print("  selfrestart/rolling-migrate: ok")


SCENARIOS = {
    "server": scenario_server,
    "files": scenario_files,
    "selfrestart": scenario_selfrestart,
    "cli": scenario_cli,
    "connect": scenario_connect,
    "tmux": scenario_tmux,
    "crash": scenario_crash,
    "multiplex": scenario_multiplex,
    "daemon": scenario_daemon,
    "restart": scenario_restart,
}


def main(argv: list[str]) -> int:
    selected = argv or list(SCENARIOS)
    for name in selected:
        if name not in SCENARIOS:
            raise SystemExit(f"unknown scenario: {name} (choose from {', '.join(SCENARIOS)})")
        print(f"[{name}]")
        SCENARIOS[name]()
    print("ALL E2E PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
