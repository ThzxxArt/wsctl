from __future__ import annotations

import asyncio
import os
import secrets
from collections.abc import Callable

import pytest

from conftest import FakeClient
from wsctl.core import tmux
from wsctl.core.session import SessionManager, SessionSpec

SHELL = "/bin/sh"
pytestmark = pytest.mark.skipif(
    not tmux.is_available() or not os.path.exists(SHELL), reason="requires tmux and /bin/sh"
)

ENV = {**os.environ, "TERM": "xterm-256color"}


async def wait_for(predicate: Callable[[], object], timeout: float = 10.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


def unique_sid() -> str:
    return f"t{secrets.token_hex(4)}"


def test_session_name_prefix() -> None:
    assert tmux.session_name("abc") == "wsctl-abc"


def test_wrap_argv_contains_attach_flags() -> None:
    argv = tmux.wrap_argv("wsctl-x", ["/bin/sh"])
    assert argv[1:4] == ["new-session", "-A", "-s"]
    assert "wsctl-x" in argv
    assert argv[-1] == "/bin/sh"


async def test_tmux_session_survives_restart() -> None:
    sid = unique_sid()
    name = tmux.session_name(sid)
    spec = SessionSpec(name="tmux", argv=[SHELL], backend="tmux", env=ENV)

    manager = SessionManager()
    manager2 = SessionManager()
    try:
        session = await manager.create(spec, sid=sid)
        first = FakeClient()
        await session.attach(first)
        session.write_input(b"echo TMUX-MARK\n")
        assert await wait_for(lambda: b"TMUX-MARK" in first.output())

        # Simulate a server restart: keep the tmux session, drop the local state.
        await session.stop(preserve=True)
        assert tmux.has_session(name), "tmux session should survive a preserved stop"

        restored = await manager2.create(spec, sid=sid)
        second = FakeClient()
        await restored.attach(second)
        assert await wait_for(lambda: b"TMUX-MARK" in second.output()), "screen not restored"
        assert restored.backend == "tmux"

        # A real kill tears the tmux session down.
        await restored.stop()
        assert not tmux.has_session(name)
    finally:
        tmux.kill_session(name)
        await manager.shutdown()
        await manager2.shutdown()


async def test_local_backend_untouched() -> None:
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="local", argv=[SHELL]))
    try:
        assert session.backend == "local"
    finally:
        await manager.shutdown()
