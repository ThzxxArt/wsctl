"""Concurrency and volume checks for the session manager.

These exercise many sessions/clients and large output without any HTTP layer,
so they are fast and deterministic. Heavier scenarios are marked ``slow`` and
run only when selected (``pytest -m slow``).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

import pytest

from conftest import FakeClient
from wsctl.core.session import SessionManager, SessionSpec

SHELL = "/bin/sh"
pytestmark = pytest.mark.skipif(not os.path.exists(SHELL), reason="requires /bin/sh")

SESSIONS = 20
CLIENTS_PER_SESSION = 3


async def wait_for(predicate: Callable[[], object], timeout: float = 15.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


async def test_many_sessions_and_clients() -> None:
    manager = SessionManager()
    sessions = []
    clients: list[list[FakeClient]] = []
    try:
        for index in range(SESSIONS):
            session = await manager.create(SessionSpec(name=f"s{index}", argv=[SHELL]))
            sessions.append(session)
            attached = []
            for _ in range(CLIENTS_PER_SESSION):
                client = FakeClient()
                await session.attach(client)
                attached.append(client)
            clients.append(attached)

        assert len(manager.list_sessions()) == SESSIONS

        for session in sessions:
            session.write_input(b"echo LOAD-MARK\n")

        ok = await wait_for(
            lambda: all(
                b"LOAD-MARK" in c.output() for group in clients for c in group
            )
        )
        assert ok, "not every client received the echoed output"
    finally:
        await manager.shutdown()


async def test_short_lived_sessions_do_not_leak() -> None:
    manager = SessionManager()
    try:
        for _ in range(30):
            session = await manager.create(SessionSpec(name="tmp", argv=[SHELL]))
            session.write_input(b"exit\n")
        assert await wait_for(lambda: len(manager.list_sessions()) == 0, timeout=20)
    finally:
        await manager.shutdown()


@pytest.mark.slow
async def test_large_output_stream() -> None:
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="sh", argv=[SHELL]))
    client = FakeClient()
    try:
        await session.attach(client)
        session.write_input(b"seq 1 50000; echo BIG-OUTPUT-DONE\n")
        assert await wait_for(
            lambda: b"BIG-OUTPUT-DONE" in client.output(), timeout=30
        )
    finally:
        await manager.shutdown()
