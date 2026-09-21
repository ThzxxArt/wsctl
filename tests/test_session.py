from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

import pytest

from conftest import FakeClient
from wsctl.core.session import SessionManager, SessionSpec

SHELL = "/bin/sh"
pytestmark = pytest.mark.skipif(not os.path.exists(SHELL), reason="requires /bin/sh")


async def wait_for(predicate: Callable[[], object], timeout: float = 3.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


async def test_echo_round_trip() -> None:
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="sh", argv=[SHELL]))
    client = FakeClient()
    try:
        await session.attach(client)
        session.write_input(b"echo wsctl-marker\n")
        assert await wait_for(lambda: b"wsctl-marker" in client.output())
    finally:
        await manager.shutdown()


async def test_detach_keeps_session_alive() -> None:
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="sh", argv=[SHELL]))
    client = FakeClient()
    try:
        await session.attach(client)
        assert session.client_count == 1
        await session.detach(client)
        assert session.client_count == 0
        assert not session.closed
        assert session.is_alive
    finally:
        await manager.shutdown()


async def test_reconnect_replays_scrollback() -> None:
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="sh", argv=[SHELL]))
    first = FakeClient()
    try:
        await session.attach(first)
        session.write_input(b"echo replay-me\n")
        assert await wait_for(lambda: b"replay-me" in first.output())

        await session.detach(first)
        second = FakeClient()
        await session.attach(second)
        assert second.controls("attached"), "attach should announce the session"
        assert b"replay-me" in second.output(), "buffered output must be replayed"
    finally:
        await manager.shutdown()


async def test_exit_notifies_clients() -> None:
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="sh", argv=[SHELL]))
    client = FakeClient()
    try:
        await session.attach(client)
        session.write_input(b"exit\n")
        assert await wait_for(lambda: client.controls("exit"), timeout=3.0)
        assert session.closed
    finally:
        await manager.shutdown()


async def test_manager_remove() -> None:
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="sh", argv=[SHELL]))
    assert manager.get(session.id) is session
    assert await manager.remove(session.id)
    assert manager.get(session.id) is None
    assert not await manager.remove("does-not-exist")
