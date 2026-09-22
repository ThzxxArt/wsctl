from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import FakeClient
from wsctl.core.session import (
    ClientGone,
    SessionManager,
    SessionSpec,
    within_user_quota,
)

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


async def test_owner_id_is_recorded() -> None:
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="sh", argv=[SHELL]), owner_id=42)
    try:
        assert session.owner_id == 42
    finally:
        await manager.shutdown()


async def test_idle_expiry() -> None:
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="sh", argv=[SHELL], idle_timeout=3600))
    try:
        assert not session.is_expired()
        assert session.is_expired(now=session.last_active + 3601)
    finally:
        await manager.shutdown()


async def test_max_life_expiry() -> None:
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="sh", argv=[SHELL], max_life=60))
    try:
        assert session.is_expired(now=session.created_at + 61)
    finally:
        await manager.shutdown()


async def test_reap_expired() -> None:
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="sh", argv=[SHELL], idle_timeout=1))
    try:
        reaped = await manager.reap_expired(now=session.last_active + 3600)
        assert session.id in reaped
        assert manager.get(session.id) is None
    finally:
        await manager.shutdown()


async def test_rename() -> None:
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="sh", argv=[SHELL]))
    try:
        assert session.rename("  build ") == "build"
        assert session.spec.name == "build"
        # blank names are ignored
        assert session.rename("   ") == "build"
    finally:
        await manager.shutdown()


async def test_max_clients() -> None:
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="sh", argv=[SHELL], max_clients=1))
    first = FakeClient()
    try:
        await session.attach(first)
        assert session.client_count == 1
        with pytest.raises(ClientGone):
            await session.attach(FakeClient())
    finally:
        await manager.shutdown()


async def test_share_lifecycle() -> None:
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="sh", argv=[SHELL]))
    try:
        assert not session.is_shared
        token = session.create_share()
        assert session.is_shared
        assert session.peek_share() == token
        assert session.share_valid(token)
        assert session.share_access(token) == "read"
        assert not session.share_writable
        assert not session.share_valid("nope")
        assert not session.share_valid(None)
        session.revoke_share()
        assert not session.share_valid(token)
    finally:
        await manager.shutdown()


async def test_share_writable() -> None:
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="sh", argv=[SHELL]))
    try:
        token = session.create_share(writable=True)
        assert session.share_access(token) == "write"
        assert session.share_writable
    finally:
        await manager.shutdown()


async def test_revoking_share_detaches_clients() -> None:
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="sh", argv=[SHELL]))
    token = session.create_share()
    client = FakeClient()
    try:
        await session.attach(client, writable=False, share=token)
        assert session.client_count == 1
        session.revoke_share()
        session.write_input(b"echo x\n")  # triggers a broadcast
        assert await wait_for(lambda: session.client_count == 0)
    finally:
        await manager.shutdown()


async def test_share_expiry() -> None:
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="sh", argv=[SHELL]))
    try:
        token = session.create_share(ttl=-1)
        assert not session.is_shared
        assert not session.share_valid(token)
    finally:
        await manager.shutdown()


async def test_recording_captures_output(tmp_path: Path) -> None:
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="sh", argv=[SHELL]))
    path = tmp_path / "s.cast"
    client = FakeClient()
    try:
        await session.start_recording(path, record_input=True)
        assert session.is_recording
        await session.attach(client)
        session.write_input(b"echo REC-MARK\n")
        assert await wait_for(lambda: b"REC-MARK" in client.output())
    finally:
        await session.stop_recording()
        await manager.shutdown()
    text = path.read_text()
    assert '"o"' in text and "REC-MARK" in text
    assert '"i"' in text


class PendingClient:
    def __init__(self, pending: int = 0) -> None:
        self.pending_bytes = pending
        self.items: list[object] = []
        self.closed = False

    def put(self, item: object) -> None:
        self.items.append(item)

    def close(self) -> None:
        self.closed = True


async def test_memory_limit_drops_largest_backlog() -> None:
    manager = SessionManager()
    session = await manager.create(
        SessionSpec(name="sh", argv=[SHELL], memory_limit=1000, scrollback_bytes=1000)
    )
    big = PendingClient(pending=5000)
    small = PendingClient(pending=10)
    try:
        await session.attach(big)
        await session.attach(small)
        session.write_input(b"echo mem\n")
        assert await wait_for(lambda: big.closed, timeout=5.0)
        assert not small.closed
    finally:
        await manager.shutdown()


class FailingClient:
    """A client whose sink rejects everything immediately."""

    def put(self, item: object) -> None:
        raise ClientGone("sink is gone")


async def test_attach_rolls_back_on_client_gone() -> None:
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="sh", argv=[SHELL]))
    try:
        with pytest.raises(ClientGone):
            await session.attach(FailingClient())
        # A client that could not accept the replay must not be left attached.
        assert session.client_count == 0
    finally:
        await manager.shutdown()


async def test_within_user_quota() -> None:
    manager = SessionManager()
    await manager.create(SessionSpec(name="a", argv=[SHELL]), owner_id=1)
    await manager.create(SessionSpec(name="b", argv=[SHELL]), owner_id=2)
    try:
        assert within_user_quota(manager, 0, 1)  # 0 = unlimited
        assert not within_user_quota(manager, 1, 1)
        assert within_user_quota(manager, 2, 1)
        assert not within_user_quota(manager, 1, 2)
        assert within_user_quota(manager, 2, 2)
        assert within_user_quota(manager, 1, 99)
    finally:
        await manager.shutdown()
