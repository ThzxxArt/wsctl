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

#: Default signal for "ask the child to wind down". ``signal.SIGHUP`` does not
#: exist on Windows, and a bare ``sig: int = signal.SIGHUP`` annotation default
#: is evaluated at *definition* time -- which would make ``import wsctl.core.pty``
#: fail outright there. Resolve it once, at import, to something that exists.
DEFAULT_TERM_SIGNAL: int = getattr(signal, "SIGHUP", signal.SIGTERM)

# Process-wide, monotonic count of dropped write() calls. Summed per-session
# counters would go *down* when a session ends, which is wrong for a metric
# named ``_total`` (Prometheus ``rate()`` would see resets).
_dropped_input_total = 0


def dropped_input_total() -> int:
    return _dropped_input_total


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

    def write(self, data: bytes) -> bool:
        """Queue ``data`` for the child; ``False`` means it was dropped."""
        ...

    def resize(self, cols: int, rows: int) -> None:
        ...

    def poll(self) -> int | None:
        ...

    def wait(self, timeout: float | None = None) -> int:
        """Wait for the child to exit.

        ``timeout`` bounds the wait so a child that outlives its terminal (a
        daemonized grandchild holding the slave open) cannot hang the caller.
        Raises :class:`subprocess.TimeoutExpired` on expiry.
        """
        ...

    def terminate(self, sig: int = DEFAULT_TERM_SIGNAL) -> None:
        ...

    def kill(self) -> None:
        """Force-kill the child (SIGKILL / TerminateProcess)."""
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
        self._kill_group = True
        self._dropped = 0
        self.resize(cols, rows)

    @property
    def dropped_input(self) -> int:
        """How many write() calls were dropped because the child stopped reading."""
        return self._dropped

    def set_detach_only(self) -> None:
        """Never signal the process group (used for tmux clients).

        The tmux server is forked by the client and may briefly share its
        process group before daemonizing; killing the group would take the
        server (and the preserved session) down with it.
        """
        self._kill_group = False

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

    def write(self, data: bytes) -> bool:
        if not data:
            return True
        # The hard cap is enforced on *what would be buffered*, not only at the
        # door: a single oversized frame used to walk straight in past a 1 MiB
        # "limit" because the check ran before ``extend``. Whole-or-nothing --
        # a half-delivered keystroke sequence is worse than a reported drop.
        room = MAX_WRITE_BUFFER - len(self._write_buf)
        if room <= 0 or len(data) > room:
            global _dropped_input_total
            _dropped_input_total += 1
            self._dropped += 1
            return False
        self._write_buf.extend(data)
        return self._flush()

    def _flush(self) -> bool:
        """Push buffered bytes toward the child. ``False`` = the link is gone.

        An ``OSError`` here is EIO on a master whose child has exited. Clearing
        the buffer and returning silently made ``write()`` report *success* for
        input that never arrived -- and the caller audits only what it believes
        reached the shell, so a vanished session came to look like a command
        that had run.
        """
        while self._write_buf:
            try:
                written = os.write(self._fd, self._write_buf)
            except BlockingIOError:
                if not self._writer_registered:
                    self._loop.add_writer(self._fd, self._on_writable)
                    self._writer_registered = True
                return True
            except OSError:
                global _dropped_input_total
                _dropped_input_total += 1
                self._dropped += 1
                self._write_buf.clear()
                return False
            del self._write_buf[:written]
        self._unregister_writer()
        return True

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

    def wait(self, timeout: float | None = None) -> int:
        return self._proc.wait(timeout=timeout)

    def terminate(self, sig: int = DEFAULT_TERM_SIGNAL) -> None:
        if self._proc.poll() is not None:
            return
        with contextlib.suppress(ProcessLookupError, PermissionError):
            if self._kill_group:
                os.killpg(os.getpgid(self._proc.pid), sig)
            else:
                self._proc.send_signal(sig)

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
                with contextlib.suppress(subprocess.TimeoutExpired):
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
