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
