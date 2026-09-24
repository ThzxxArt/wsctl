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
    tmux.set_namespace(None)
    try:
        assert tmux.session_name("abc") == "wsctl-abc"
        assert tmux.owns("wsctl-abc")
        assert tmux.sid_from_name("wsctl-abc") == "abc"
    finally:
        tmux.set_namespace(None)


def test_session_name_is_namespaced_per_data_dir() -> None:
    tmux.set_namespace("/tmp/wsctl-data-a")
    try:
        name = tmux.session_name("abc")
        assert name.startswith("wsctl-") and name.endswith("-abc")
        assert tmux.owns(name)
        assert tmux.sid_from_name(name) == "abc"
        # A different deployment's un-namespaced session is not ours.
        assert not tmux.owns("wsctl-abc")
        assert tmux.sid_from_name("wsctl-abc") == ""
    finally:
        tmux.set_namespace(None)


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
        session.write_input(b"echo $((31*37))\n")
        # Bare product, not `1147\r\n`: what a tmux client emits is a
        # *rendered screen*, and `1147` is painted at a cursor position with
        # drawing commands after it -- never as a line ending in CRLF. Demanding
        # the CRLF made this wait fail on every runner (it passed locally only
        # by the shell echoing before tmux repainted).
        assert await wait_for(lambda: b"1147" in first.output())

        # Simulate a server restart: keep the tmux session, drop the local state.
        await session.stop(preserve=True)
        assert tmux.has_session(name), "tmux session should survive a preserved stop"

        restored = await manager2.create(spec, sid=sid)
        second = FakeClient()
        await restored.attach(second)
        assert await wait_for(lambda: b"1147" in second.output()), "screen not restored"
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
