#!/usr/bin/env python3
"""Repeatable 0.2.0 performance smoke.

Measures the paths the roadmap calls T1/T2/T3 against a real FastAPI app and a
real PTY, then prints one JSON object suitable for `docs/benchmarks/`.

Run::

    python scripts/benchmark.py --sessions 8 --lines 200000
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import statistics
import tempfile
import time
from pathlib import Path

import psutil
from fastapi.testclient import TestClient

from wsctl.core.config import load_settings
from wsctl.core.protocol import SUBPROTOCOL, decode_data, encode_control, encode_input
from wsctl.core.session import SessionManager
from wsctl.core.store import Store
from wsctl.server.app import create_app

ROOT = Path(__file__).resolve().parent.parent


def build_app(data_dir: Path) -> object:
    settings = load_settings(
        data_dir=data_dir,
        auth_required=False,
        default_shell="/bin/sh",
        scrollback_bytes=8 * 1024 * 1024,
    )
    return create_app(settings, store=Store(data_dir / "bench.db"), manager=SessionManager())


def wait_for_marker(ws, marker: bytes) -> tuple[int, int]:
    output = bytearray()
    frames = 0
    end_seq = 0
    while True:
        message = ws.receive()
        raw = message.get("bytes")
        if raw is None:
            continue
        frame = decode_data(raw)
        frames += 1
        output.extend(frame.payload)
        end_seq = max(end_seq, frame.seq + len(frame.payload))
        if marker in output:
            return frames, end_seq


def run_session(
    client: TestClient, lines: int, *, measure_reconnect: bool = True
) -> dict[str, float]:
    timings: dict[str, float] = {}
    with client.websocket_connect("/ws/v2", subprotocols=[SUBPROTOCOL]) as ws:
        started = time.perf_counter()
        ws.send_text(encode_control({"type": "attach", "cols": 120, "rows": 40}))
        generation = None
        while True:
            message = ws.receive()
            if message.get("text"):
                control = json.loads(message["text"])
                if control.get("type") == "attached":
                    generation = control.get("generation")
                    break
        timings["attach_ms"] = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        ws.send_bytes(
            encode_input(f"seq 1 {lines}; printf 'BENCH-''DONE\\n'\r".encode())
        )
        frames, end_seq = wait_for_marker(ws, b"BENCH-DONE")
        timings["seq_ms"] = (time.perf_counter() - started) * 1000
        timings["frames"] = float(frames)
        ws.send_text(encode_control({"type": "ack", "seq": end_seq}))
    if measure_reconnect:
        with client.websocket_connect("/ws/v2", subprotocols=[SUBPROTOCOL]) as ws:
            started = time.perf_counter()
            ws.send_text(
                encode_control(
                    {
                        "type": "attach",
                        "cols": 120,
                        "rows": 40,
                        "last_seq": end_seq,
                        "generation": generation,
                    }
                )
            )
            while True:
                message = ws.receive()
                if message.get("text") and json.loads(message["text"]).get("type") == "attached":
                    break
            timings["reconnect_ms"] = (time.perf_counter() - started) * 1000
    return timings


def bundle_size() -> float:
    files = sorted((ROOT / "src" / "wsctl" / "static" / "assets").glob("*.js"))
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
        for path in files
        if not path.name.startswith(lazy)
    )
    return total / 1024


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sessions", type=int, default=1)
    parser.add_argument("--lines", type=int, default=200000)
    parser.add_argument("--idle-sessions", type=int, default=0)
    args = parser.parse_args()
    results: dict[str, object] = {
        "version": "0.2.0",
        "date": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "python": os.sys.version.split()[0],
        "first_load_gzip_kb": round(bundle_size(), 1),
    }
    single: dict[str, float] = {}
    with tempfile.TemporaryDirectory() as tmp:
        app = build_app(Path(tmp))
        with TestClient(app) as client:
            single = run_session(client, args.lines)
            results["t1_seq_200000_ms"] = round(single.get("seq_ms", 0), 1)
            results["attach_ms"] = round(single.get("attach_ms", 0), 1)
            results["reconnect_ms"] = round(single.get("reconnect_ms", 0), 1)
            results["frames"] = int(single.get("frames", 0))
    if args.sessions > 1:
        # Independent sessions in one app; sequential TestClient sockets keep
        # the measurement about the server, not the test client's scheduler.
        with tempfile.TemporaryDirectory() as tmp:
            app = build_app(Path(tmp))
            with TestClient(app) as client:
                samples = [
                    run_session(client, args.lines, measure_reconnect=False)["seq_ms"]
                    for _ in range(args.sessions)
                ]
            results["parallel_sessions"] = args.sessions
            results["parallel_total_ms"] = round(sum(samples), 1)
            results["parallel_p50_ms"] = round(statistics.median(samples), 1)
    if args.idle_sessions > 0:
        results["idle"] = asyncio.run(_idle_memory(args.idle_sessions))
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


async def _idle_memory(count: int) -> dict[str, float]:
    from wsctl.core.session import SessionManager, SessionSpec

    process = psutil.Process(os.getpid())
    before = process.memory_info().rss
    manager = SessionManager()
    sessions = []
    try:
        for index in range(count):
            sessions.append(
                await manager.create(
                    SessionSpec(name=f"idle-{index}", argv=["/bin/sh"])
                )
            )
        await asyncio.sleep(0.5)
        after = process.memory_info().rss
    finally:
        await manager.shutdown()
    delta = max(0, after - before)
    return {
        "sessions": count,
        "rss_delta_mb": round(delta / 1024 / 1024, 1),
        "per_session_kib": round(delta / 1024 / max(1, count), 1),
    }


if __name__ == "__main__":
    raise SystemExit(main())
