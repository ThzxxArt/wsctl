"""Platform PTY abstraction.

POSIX systems use :func:`os.openpty` plus :mod:`subprocess`; Windows support
is provided by the optional ``pywinpty`` dependency (``wsctl[win]``).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import struct
import subprocess
import sys
from typing import Protocol, runtime_checkable

if sys.platform != "win32":  # pragma: no cover - platform specific
    import fcntl
    import termios

READ_SIZE = 65536
MAX_WRITE_BUFFER = 1024 * 1024
MAX_DIMENSION = 65535


class PtyError(RuntimeError):
    """Raised when a PTY cannot be created or operated on."""


@runtime_checkable
class Pty(Protocol):
    """A pseudo-terminal backing a single terminal session."""

    @property
    def pid(self) -> int:
        ...

    async def read(self) -> bytes:
        """Read the next chunk of output; ``b""`` signals EOF."""
        ...

    def write(self, data: bytes) -> None:
        ...

    def resize(self, cols: int, rows: int) -> None:
        ...

    def poll(self) -> int | None:
        ...

    def wait(self) -> int:
        ...

    def terminate(self, sig: int = signal.SIGHUP) -> None:
        ...

    def close(self) -> None:
        ...


class PosixPty:
    """A PTY backed by a forked child process on POSIX systems."""

    def __init__(
        self,
        argv: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        cols: int = 80,
        rows: int = 24,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        if sys.platform == "win32":  # pragma: no cover - platform specific
            raise PtyError("PosixPty is not available on Windows")
        if not argv:
            raise PtyError("cannot spawn an empty command")

        self._loop = loop or asyncio.get_event_loop()
        master_fd, slave_fd = os.openpty()
        try:
            self._proc = subprocess.Popen(
                argv,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=cwd,
                env=env,
                start_new_session=True,
                close_fds=True,
            )
        except BaseException:
            # Never leak the master fd (or a partially started child) on failure.
            os.close(slave_fd)
            os.close(master_fd)
            raise
        os.close(slave_fd)

        self._fd = master_fd
        os.set_blocking(master_fd, False)
        self._write_buf = bytearray()
        self._writer_registered = False
        self._reader_fut: asyncio.Future[bytes] | None = None
        self.resize(cols, rows)

    @property
    def pid(self) -> int:
        return self._proc.pid

    async def read(self) -> bytes:
        if self._reader_fut is not None:
            raise PtyError("concurrent read() is not supported")
        fut: asyncio.Future[bytes] = self._loop.create_future()
        self._reader_fut = fut
        self._loop.add_reader(self._fd, self._on_readable)
        try:
            return await fut
        finally:
            if self._reader_fut is fut:
                self._loop.remove_reader(self._fd)
                self._reader_fut = None

    def _on_readable(self) -> None:
        fut = self._reader_fut
        if fut is None or fut.done():
            return
        try:
            data = os.read(self._fd, READ_SIZE)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        self._loop.remove_reader(self._fd)
        self._reader_fut = None
        fut.set_result(data)

    def write(self, data: bytes) -> None:
        if not data:
            return
        if len(self._write_buf) >= MAX_WRITE_BUFFER:
            # The child is not draining its stdin; drop input rather than grow
            # without bound (memory hard limit for the write path).
            return
        self._write_buf.extend(data)
        self._flush()

    def _flush(self) -> None:
        while self._write_buf:
            try:
                written = os.write(self._fd, self._write_buf)
            except BlockingIOError:
                if not self._writer_registered:
                    self._loop.add_writer(self._fd, self._on_writable)
                    self._writer_registered = True
                return
            except OSError:
                self._write_buf.clear()
                return
            del self._write_buf[:written]
        self._unregister_writer()

    def _on_writable(self) -> None:
        self._flush()

    def _unregister_writer(self) -> None:
        if self._writer_registered:
            self._loop.remove_writer(self._fd)
            self._writer_registered = False

    def resize(self, cols: int, rows: int) -> None:
        cols = min(max(1, int(cols)), MAX_DIMENSION)
        rows = min(max(1, int(rows)), MAX_DIMENSION)
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        with contextlib.suppress(OSError):
            fcntl.ioctl(self._fd, termios.TIOCSWINSZ, winsize)

    def poll(self) -> int | None:
        return self._proc.poll()

    def wait(self) -> int:
        return self._proc.wait()

    def terminate(self, sig: int = signal.SIGHUP) -> None:
        if self._proc.poll() is not None:
            return
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(self._proc.pid), sig)

    def signal_process(self, sig: int) -> None:
        """Signal only the direct child, not its whole process group.

        Used to detach a tmux client without risking the tmux server that the
        client may have just forked (and which may not be daemonized yet).
        """
        if self._proc.poll() is not None:
            return
        with contextlib.suppress(ProcessLookupError, PermissionError):
            self._proc.send_signal(sig)

    def kill(self) -> None:
        self.terminate(signal.SIGKILL)

    def close(self) -> None:
        self._unregister_writer()
        fut = self._reader_fut
        if fut is not None and not fut.done():
            self._loop.remove_reader(self._fd)
            self._reader_fut = None
            fut.cancel()
        with contextlib.suppress(OSError):
            os.close(self._fd)
        if self._proc.poll() is None:
            self.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2)


def create_pty(
    argv: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    cols: int = 80,
    rows: int = 24,
    loop: asyncio.AbstractEventLoop | None = None,
) -> Pty:
    """Create a platform-appropriate PTY for ``argv``."""
    if sys.platform == "win32":  # pragma: no cover - platform specific
        from ._winpty import WinPty

        return WinPty(argv, cwd=cwd, env=env, cols=cols, rows=rows)
    return PosixPty(argv, cwd=cwd, env=env, cols=cols, rows=rows, loop=loop)
