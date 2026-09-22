#!/usr/bin/env python3
"""Backend end-to-end scenarios for wsctl.

Runs five scenarios, each against a freshly started server on its own port and
data directory:

1. server   — health, login, WebSocket attach/exec, reconnect replay, metrics,
              file panel (list/upload/download), traversal guard, auth guard
2. cli      — wsctl login / session new|list|kill
3. connect  — the CLI thin client over a real pseudo-terminal
4. tmux     — a tmux-backed session survives a full server restart
5. restart  — two instances share a port via SO_REUSEPORT (zero downtime)

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
from websockets.sync.client import connect

from wsctl.core import tmux as tmux_mod

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
ADMIN = "admin"
PASSWORD = "testpass123"


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def spawn(port: int, data: Path, *, extra_env: dict[str, str] | None = None,
          reuse_port: bool = False) -> subprocess.Popen[bytes]:
    env = {**os.environ, "WSCTL_DATA_DIR": str(data), **(extra_env or {})}
    cmd = [PY, "-m", "wsctl", "serve", "--port", str(port), "--host", "127.0.0.1",
           "--admin-password", PASSWORD, "--log-level", "warning"]
    if reuse_port:
        cmd.append("--reuse-port")
    return subprocess.Popen(cmd, cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)


def stop(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def wait_health(base: str, proc: subprocess.Popen[bytes] | None = None,
                timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            raise SystemExit(f"server exited early ({proc.returncode})")
        try:
            if httpx.get(f"{base}/healthz", timeout=1).status_code == 200:
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
    out = b""
    end = time.time() + timeout
    while time.time() < end and marker not in out:
        try:
            msg = ws.recv(timeout=5)  # type: ignore[attr-defined]
        except TimeoutError:
            break
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


def scenario_server() -> None:
    files = Path(tempfile.mkdtemp(prefix="wsctl-files-"))
    (files / "seed.txt").write_text("seeded")
    try:
        with server(extra_env={"WSCTL_FILE_ROOT": str(files)}) as (base, _, _):
            token = login(base)
            headers = {"Cookie": f"wsctl_session={token}"}
            ws_url = base.replace("http", "ws") + "/ws"

            with connect(ws_url, additional_headers=headers, open_timeout=10) as ws:
                ws.send(json.dumps({"type": "attach", "cols": 100, "rows": 30}))
                ws.send(b"echo E2E-MARK\r")
                assert b"E2E-MARK" in ws_recv_until(ws, b"E2E-MARK")

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
        with server() as (base, _, _):
            env = {**os.environ, "XDG_CONFIG_HOME": str(conf)}

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
            os.write(master, b"echo CONNECT-MARK\r")
            for _ in range(50):
                drain(0.2)
                if b"CONNECT-MARK" in output:
                    break
            assert b"CONNECT-MARK" in output, output[-200:]
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
    env = {"WSCTL_DEFAULT_BACKEND": "tmux"}
    first = spawn(port, data, extra_env=env)
    second: subprocess.Popen[bytes] | None = None
    try:
        wait_health(base, first)
        token = login(base)
        headers = {"Cookie": f"wsctl_session={token}"}
        ws_url = base.replace("http", "ws") + "/ws"
        sid = httpx.post(f"{base}/api/sessions", headers=headers, json={}, timeout=10).json()["id"]

        with connect(ws_url, additional_headers=headers, open_timeout=10) as ws:
            ws.send(json.dumps({"type": "attach", "session": sid, "cols": 100, "rows": 30}))
            ws.send(b"echo TMUX-MARK\r")
            assert b"TMUX-MARK" in ws_recv_until(ws, b"TMUX-MARK")

        assert _wait_tmux(name(sid)), (
            "tmux session missing while the server is running: " + " ".join(_tmux_ls())
        )
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
            assert b"TMUX-MARK" in ws_recv_until(ws, b"TMUX-MARK"), "screen not restored"
    finally:
        if second is not None:
            stop(second)
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
    env = {"WSCTL_DEFAULT_BACKEND": "tmux"}
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
            ws.send(b"echo CRASH-MARK\r")
            assert b"CRASH-MARK" in ws_recv_until(ws, b"CRASH-MARK")
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
            assert b"CRASH-MARK" in ws_recv_until(ws, b"CRASH-MARK"), "screen not restored"
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


SCENARIOS = {
    "server": scenario_server,
    "cli": scenario_cli,
    "connect": scenario_connect,
    "tmux": scenario_tmux,
    "crash": scenario_crash,
    "multiplex": scenario_multiplex,
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
