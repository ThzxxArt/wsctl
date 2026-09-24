"""Windows PTY backend based on the optional ``pywinpty`` dependency.

Only imported on Windows; install with ``pip install "wsctl[win]"``.
"""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
import threading
import time
from typing import Any

from .pty import DEFAULT_TERM_SIGNAL, PtyError


def _load_pywinpty() -> Any:
    try:
        from winpty import PtyProcess  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - platform specific
        raise PtyError(
            "Windows support requires pywinpty; install with 'pip install wsctl[win]'"
        ) from exc
    return PtyProcess


def _cmdline_quote(arg: str) -> str:
    """Quote one argument for ``CommandLineToArgvW``.

    ``shlex.quote`` is the POSIX rule (single quotes), which is *not* a
    grouping character on Windows: a path with spaces was split into several
    arguments. The Windows rule is backslash-escaped double quotes.
    """
    if arg and not any(ch in arg for ch in ' \t\n\v"'):
        return arg
    out = ['"']
    backslashes = 0
    for ch in arg:
        if ch == "\\":
            backslashes += 1
            continue
        if ch == '"':
            out.append("\\" * (backslashes * 2 + 1))
            out.append('"')
        else:
            out.append("\\" * backslashes)
            out.append(ch)
        backslashes = 0
    out.append("\\" * (backslashes * 2))
    out.append('"')
    return "".join(out)


class WinPty:  # pragma: no cover - platform specific
    """Minimal pywinpty wrapper exposing the :class:`~wsctl.core.pty.Pty` API.

    Reads run on **one dedicated thread per PTY**, never on the shared
    default executor: a blocking ``read()`` that waits for output would
    otherwise hold a worker for the whole life of the session, and the same
    pool serves authentication, audit writes and the maintenance loop. A
    handful of idle Windows sessions was enough to starve all of them.
    """

    def __init__(
        self,
        argv: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        cols: int = 80,
        rows: int = 24,
    ) -> None:
        pty_process = _load_pywinpty()
        command = " ".join(_cmdline_quote(a) for a in argv)
        self._proc = pty_process.spawn(command, cwd=cwd, env=env, dimensions=(rows, cols))
        self._loop = asyncio.get_event_loop()
        self._dropped = 0
        self._reader_fut: asyncio.Future[bytes] | None = None
        self._pending: list[bytes] = []
        self._eof = False
        self._reader = threading.Thread(target=self._read_pump, daemon=True)
        self._reader.start()

    def _read_pump(self) -> None:
        """The dedicated reader: block here, hand results to the loop."""
        while True:
            try:
                data = self._proc.read(65536)
            except Exception:  # the console went away under us
                data = None
            if not data:
                self._loop.call_soon_threadsafe(self._on_reader_done)
                return
            chunk = data.encode("utf-8", "replace") if isinstance(data, str) else bytes(data)
            self._loop.call_soon_threadsafe(self._on_reader_data, chunk)

    def _on_reader_data(self, chunk: bytes) -> None:
        fut = self._reader_fut
        if fut is not None and not fut.done():
            self._reader_fut = None
            fut.set_result(chunk)
        else:
            self._pending.append(chunk)

    def _on_reader_done(self) -> None:
        self._eof = True
        fut = self._reader_fut
        if fut is not None and not fut.done():
            self._reader_fut = None
            fut.set_result(b"")

    @property
    def dropped_input(self) -> int:
        return self._dropped

    @property
    def pid(self) -> int:
        return int(self._proc.pid)

    async def read(self) -> bytes:
        if self._pending:
            return self._pending.pop(0)
        if self._eof or not self._proc.isalive():
            return b""
        if self._reader_fut is not None:
            raise PtyError("concurrent read() is not supported")
        fut: asyncio.Future[bytes] = self._loop.create_future()
        self._reader_fut = fut
        return await fut

    def write(self, data: bytes) -> bool:
        if not data:
            return True
        try:
            self._proc.write(data.decode("utf-8", "replace"))
        except Exception:
            self._dropped += 1
            return False
        return True

    def resize(self, cols: int, rows: int) -> None:
        self._proc.setwinsize(rows, cols)

    def _exit_code(self) -> int:
        """The child's exit code -- including "killed by a signal".

        ``exitstatus or 0`` reported a terminated process as a clean exit 0,
        so an admin's kill showed up in history as "normal". ``None`` with a
        ``signalstatus`` is exactly that case.
        """
        status = getattr(self._proc, "exitstatus", None)
        if status is not None:
            return int(status)
        signum = getattr(self._proc, "signalstatus", None)
        if signum is not None:
            return 128 + int(signum)
        return -1

    def poll(self) -> int | None:
        if self._proc.isalive():
            return None
        return self._exit_code()

    def wait(self, timeout: float | None = None) -> int:
        if timeout is None:
            self._proc.wait()
            return self._exit_code()
        deadline = time.monotonic() + timeout
        while self._proc.isalive():
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self._proc.pid, timeout)
            time.sleep(0.05)
        return self._exit_code()

    def terminate(self, sig: int = DEFAULT_TERM_SIGNAL) -> None:
        if self._proc.isalive():
            self._proc.terminate()

    def kill(self) -> None:
        if self._proc.isalive():
            self._proc.kill()

    def close(self) -> None:
        if self._proc.isalive():
            self._proc.terminate()
        with contextlib.suppress(Exception):
            self._proc.close()
