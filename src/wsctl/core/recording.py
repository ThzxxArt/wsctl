"""Terminal session recording in asciinema cast v2 format.

The cast format is a JSON header line followed by one JSON array per event:

    {"version": 2, "width": 80, "height": 24, "timestamp": 1700000000}
    [0.123456, "o", "output text"]
    [0.234567, "i", "input text"]

Output is always recorded; input is optional (it may contain secrets).
"""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path

FLUSH_INTERVAL = 1.0
MAX_QUEUE = 10000


class Recorder:
    """Streams terminal events to a ``.cast`` file.

    Events are handed to a background writer thread so disk I/O never blocks the
    event loop. ``close`` drains the queue and joins the writer.
    """

    def __init__(
        self,
        path: Path,
        *,
        width: int = 80,
        height: int = 24,
        env: dict[str, str] | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w", encoding="utf-8")
        header: dict[str, object] = {
            "version": 2,
            "width": width,
            "height": height,
            "timestamp": int(time.time()),
        }
        if env:
            header["env"] = env
        self._fh.write(json.dumps(header) + "\n")
        self._start = time.monotonic()
        self._last_flush = self._start
        self._closed = False
        self._dropped = 0
        self._stop = threading.Event()
        self._queue: queue.Queue[str] = queue.Queue(maxsize=MAX_QUEUE)
        self._thread = threading.Thread(target=self._run, name="wsctl-recorder", daemon=True)
        self._thread.start()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def dropped(self) -> int:
        return self._dropped

    def _event(self, kind: str, text: str) -> None:
        if self._closed or not text:
            return
        elapsed = round(time.monotonic() - self._start, 6)
        line = json.dumps([elapsed, kind, text]) + "\n"
        try:
            self._queue.put_nowait(line)
        except queue.Full:
            # Disk cannot keep up: drop rather than block the terminal.
            self._dropped += 1

    def output(self, data: bytes) -> None:
        self._event("o", data.decode("utf-8", "replace"))

    def input(self, data: bytes) -> None:
        self._event("i", data.decode("utf-8", "replace"))

    def _run(self) -> None:
        while True:
            try:
                line = self._queue.get(timeout=0.5)
            except queue.Empty:
                if self._stop.is_set():
                    break
                continue
            try:
                self._fh.write(line)
                now = time.monotonic()
                if now - self._last_flush >= FLUSH_INTERVAL:
                    self._fh.flush()
                    self._last_flush = now
            except Exception:
                # Never let the writer thread crash with items still queued.
                break

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        self._thread.join(timeout=5)
        self._fh.flush()
        self._fh.close()


def recordings_usage(directory: Path) -> tuple[int, int]:
    """Return ``(total_bytes, count)`` of ``*.cast`` files in ``directory``."""
    total = 0
    count = 0
    if not directory.is_dir():
        return 0, 0
    for entry in directory.glob("*.cast"):
        try:
            total += entry.stat().st_size
            count += 1
        except OSError:
            continue
    return total, count


def has_room(directory: Path, max_bytes: int) -> bool:
    """Whether total recording size is still below ``max_bytes`` (0 = unlimited)."""
    if max_bytes <= 0:
        return True
    total, _ = recordings_usage(directory)
    return total < max_bytes
