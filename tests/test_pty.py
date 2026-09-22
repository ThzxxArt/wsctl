from __future__ import annotations

import os
from pathlib import Path

import pytest

from wsctl.core.pty import PosixPty, PtyError

pytestmark = pytest.mark.skipif(
    not Path("/proc/self/fd").is_dir(), reason="requires /proc (Linux)"
)


def _fd_count() -> int:
    return len(os.listdir("/proc/self/fd"))


async def test_failed_spawn_does_not_leak_fds() -> None:
    before = _fd_count()
    for _ in range(10):
        with pytest.raises(FileNotFoundError):
            PosixPty(["/nonexistent-binary-xyz"])
    assert _fd_count() - before <= 1


async def test_empty_argv_is_rejected() -> None:
    with pytest.raises(PtyError):
        PosixPty([])


async def test_out_of_range_dimensions_are_clamped() -> None:
    pty = PosixPty(["/bin/sh"], cols=100000, rows=100000)
    try:
        assert pty.poll() is None
    finally:
        pty.terminate()
        pty.close()


async def test_dropped_input_counter_is_monotonic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``_total`` metric must never go down when a session goes away.

    Driven with the drain stubbed out rather than via SIGSTOP: the kernel PTY
    buffer size is platform-specific, so filling it for real makes the test
    flaky rather than authoritative.
    """
    import asyncio

    from wsctl.core import pty as pty_mod
    from wsctl.core.pty import MAX_WRITE_BUFFER, PosixPty

    before = pty_mod.dropped_input_total()
    pty = PosixPty(["/bin/sh"], cols=80, rows=24, loop=asyncio.get_running_loop())
    try:
        monkeypatch.setattr(pty, "_flush", lambda: None)  # the child never reads
        pty.write(b"x" * MAX_WRITE_BUFFER)
        assert pty.write(b"more") is False
    finally:
        pty.close()

    after = pty_mod.dropped_input_total()
    assert after > before
    # The process-wide counter keeps the value even though `pty` is gone.
    assert pty_mod.dropped_input_total() == after
