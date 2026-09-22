"""Terminal session recording in asciinema cast v2 format.

The cast format is a JSON header line followed by one JSON array per event:

    {"version": 2, "width": 80, "height": 24, "timestamp": 1700000000}
    [0.123456, "o", "output text"]
    [0.234567, "i", "input text"]

Output is always recorded; input is optional (it may contain secrets).

The writer runs on its own thread so disk I/O never blocks the event loop, but
that thread must never lie about the result: if a write fails the recorder
closes itself so ``is_recording`` turns false instead of showing a red dot for
a file that is not being written.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from pathlib import Path
from typing import TextIO

log = logging.getLogger("wsctl.recording")

FLUSH_INTERVAL = 1.0
MAX_QUEUE = 10000

# Process-wide count of recorders stopped by a write failure. Exposed as the
# ``wsctl_recording_failures_total`` gauge; it is monotonic so a scrape shows
# the trend even after the affected session is long gone.
_failures = 0


def failure_count() -> int:
    return _failures


class Recorder:
    """Streams terminal events to a ``.cast`` file.

    Events are handed to a background writer thread so disk I/O never blocks the
    event loop. ``close`` drains the queue and joins the writer; all file
    access is serialised through ``_io_lock`` so closing can never race a write.
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
        self._io_lock = threading.Lock()
        self._fh: TextIO | None = self.path.open("w", encoding="utf-8")
        header: dict[str, object] = {
            "version": 2,
            "width": width,
            "height": height,
            "timestamp": int(time.time()),
        }
        if env:
            header["env"] = env
        try:
            with self._io_lock:
                assert self._fh is not None
                self._fh.write(json.dumps(header) + "\n")
        except Exception:
            # Do not leak the handle if the header cannot be written.
            with self._io_lock:
                fh, self._fh = self._fh, None
            if fh is not None:
                fh.close()
            raise
        self._start = time.monotonic()
        self._last_flush = self._start
        self._closed = False
        self._failed = False
        self._incomplete = False
        self._error: str | None = None
        self._dropped = 0
        self._stop = threading.Event()
        self._queue: queue.Queue[str] = queue.Queue(maxsize=MAX_QUEUE)
        self._thread = threading.Thread(target=self._run, name="wsctl-recorder", daemon=True)
        self._thread.start()

    @property
    def closed(self) -> bool:
        """True once the recorder is finished -- including after a write failure."""
        return self._closed

    @property
    def failed(self) -> bool:
        return self._failed

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def incomplete(self) -> bool:
        """True when ``close`` could not join the writer within its budget."""
        return self._incomplete

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
            with self._io_lock:
                if self._fh is None:
                    return
                try:
                    self._fh.write(line)
                    now = time.monotonic()
                    if now - self._last_flush >= FLUSH_INTERVAL:
                        self._fh.flush()
                        self._last_flush = now
                except Exception as exc:
                    global _failures
                    _failures += 1
                    self._failed = True
                    self._error = str(exc)
                    self._closed = True
                    log.warning("recording write failed, stopping recorder: %s", exc)
                    return

    def close(self) -> None:
        if self._closed and self._fh is None:
            return
        self._closed = True
        self._stop.set()
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            # The writer is wedged (a blocked NFS write, say). Report it rather
            # than pretending the cast is complete, and stop touching the file
            # from this thread so we cannot race it.
            self._incomplete = True
            self._error = self._error or "录制写入线程未能及时结束"
            log.warning("recording writer did not stop; cast may be truncated: %s", self.path)
            return
        with self._io_lock:
            fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            if not self._failed:
                fh.flush()
        except Exception:
            self._failed = True
        finally:
            fh.close()


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
