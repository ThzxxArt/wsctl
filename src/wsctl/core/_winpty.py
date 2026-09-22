"""Windows PTY backend based on the optional ``pywinpty`` dependency.

Only imported on Windows; install with ``pip install "wsctl[win]"``.
"""

from __future__ import annotations

import asyncio
import signal
import subprocess
import time
from typing import Any

from .pty import PtyError


def _load_pywinpty() -> Any:
    try:
        from winpty import PtyProcess  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - platform specific
        raise PtyError(
            "Windows support requires pywinpty; install with 'pip install wsctl[win]'"
        ) from exc
    return PtyProcess


class WinPty:  # pragma: no cover - platform specific
    """Minimal pywinpty wrapper exposing the :class:`~wsctl.core.pty.Pty` API."""

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
        import shlex

        command = " ".join(shlex.quote(a) for a in argv)
        self._proc = pty_process.spawn(command, cwd=cwd, env=env, dimensions=(rows, cols))
        self._loop = asyncio.get_event_loop()

    @property
    def pid(self) -> int:
        return int(self._proc.pid)

    async def read(self) -> bytes:
        if not self._proc.isalive():
            return b""
        data = await self._loop.run_in_executor(None, self._proc.read, 65536)
        if data is None:
            return b""
        if isinstance(data, str):
            return data.encode("utf-8", "replace")
        return bytes(data)

    def write(self, data: bytes) -> bool:
        self._proc.write(data.decode("utf-8", "replace"))
        return True

    def resize(self, cols: int, rows: int) -> None:
        self._proc.setwinsize(rows, cols)

    def poll(self) -> int | None:
        if self._proc.isalive():
            return None
        return self._proc.exitstatus or 0

    def wait(self, timeout: float | None = None) -> int:
        if timeout is None:
            self._proc.wait()
            return self._proc.exitstatus or 0
        deadline = time.monotonic() + timeout
        while self._proc.isalive():
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self._proc.pid, timeout)
            time.sleep(0.05)
        return self._proc.exitstatus or 0

    def terminate(self, sig: int = signal.SIGHUP) -> None:
        if self._proc.isalive():
            self._proc.terminate()

    def kill(self) -> None:
        if self._proc.isalive():
            self._proc.kill()

    def close(self) -> None:
        if self._proc.isalive():
            self._proc.terminate()
