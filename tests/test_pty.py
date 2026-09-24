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


def test_a_dead_link_reports_the_drop_instead_of_claiming_delivery(
    tmp_path: Path,
) -> None:
    """``_flush``'s OSError path used to clear the buffer and return silently.

    ``write()`` had already returned ``True``, so input that never reached the
    child was recorded as delivered -- and the caller audits exactly "what
    actually reached the shell", which turned a vanished session into an audit
    entry implying a command had run.
    """
    import asyncio

    from wsctl.core.pty import PosixPty

    async def run() -> tuple[bool, int]:
        pty = PosixPty(["/bin/sh"], cols=80, rows=24, loop=asyncio.get_running_loop())
        try:
            os.close(pty._fd)  # the child is gone: writes now raise OSError
            pty._fd = os.open("/dev/null", os.O_WRONLY)  # a writeable stand-in
            os.close(pty._fd)
            # Point at a closed descriptor: the first os.write raises OSError.
            pty._fd = 10_000_000  # guaranteed-unowned fd number
            before = pty.dropped_input
            ok = pty.write(b"echo gone\n")
            return ok, pty.dropped_input - before
        finally:
            with contextlib.suppress(Exception):
                pty.close()

    import contextlib

    ok, dropped = asyncio.run(run())
    assert ok is False, "a write that never arrived must not report success"
    assert dropped == 1, "the loss must be counted, not swallowed"


def test_a_single_oversized_frame_cannot_blow_the_write_budget() -> None:
    """The hard cap is enforced on what would be buffered, not only at the door.

    ``if len(buf) >= MAX: drop`` then ``buf.extend(data)`` let one 16 MiB frame
    walk straight past a 1 MiB "limit" whenever the buffer happened to be empty.
    """
    import asyncio

    from wsctl.core.pty import MAX_WRITE_BUFFER, PosixPty

    async def run() -> tuple[bool, bool, int, int]:
        pty = PosixPty(["/bin/sh"], cols=80, rows=24, loop=asyncio.get_running_loop())
        try:
            big = b"x" * (MAX_WRITE_BUFFER + 1)
            first = pty.write(big)
            buffered = len(pty._write_buf)
            # The budget must be intact for real input: refusing the oversized
            # frame whole is what keeps it that way (the old code *accepted* it
            # and left the "1 MiB hard cap" exceeded by 64 KiB and more).
            second = pty.write(b"more")
            return first, second, buffered, pty.dropped_input
        finally:
            with contextlib.suppress(Exception):
                pty.close()

    import contextlib

    first, second, buffered, dropped = asyncio.run(run())
    assert first is False, "an oversized frame must be refused whole"
    assert buffered == 0, "a refused frame must not sit in the buffer"
    assert second is True, "the budget must still be intact for real input"
    assert dropped >= 1


def test_winpty_command_line_quoting_is_the_windows_rule() -> None:
    """``shlex.quote`` is POSIX (single quotes), which Windows does not group.

    A path with spaces was split into several arguments before the child ever
    started. The Windows rule is backslash-escaped double quotes.
    """
    from wsctl.core._winpty import _cmdline_quote

    assert _cmdline_quote("simple") == "simple"
    # Spaces force the quoted form; a backslash only needs doubling when it
    # sits in front of a quote (or the closing one).
    assert _cmdline_quote("C:\\Program Files\\app.exe") == '"C:\\Program Files\\app.exe"'
    assert _cmdline_quote('say "hi"') == '"say \\"hi\\""'
    assert _cmdline_quote("") == '""'
    for arg in ("plain", "with space", 'with "quote"', "back\\slash", ""):
        quoted = _cmdline_quote(arg)
        if arg and " " in arg:
            assert quoted.startswith('"') and quoted.endswith('"'), quoted


def test_winpty_reports_a_signalled_child_as_a_failure_not_a_clean_zero() -> None:
    """``exitstatus or 0`` turned "killed by a signal" into exit code 0.

    An admin's terminate then showed up in history as a normal exit.
    """

    class Proc:
        exitstatus = None
        signalstatus = 9

    class P:
        _proc = Proc()

    from wsctl.core._winpty import WinPty

    assert WinPty._exit_code(P()) == 128 + 9  # type: ignore[arg-type]

    class Proc2:
        exitstatus = 3
        signalstatus = None

    class P2:
        _proc = Proc2()

    assert WinPty._exit_code(P2()) == 3  # type: ignore[arg-type]

    class Proc3:
        exitstatus = None
        signalstatus = None

    class P3:
        _proc = Proc3()

    assert WinPty._exit_code(P3()) == -1  # type: ignore[arg-type]
