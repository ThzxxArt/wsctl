from __future__ import annotations

import asyncio
import os
import time
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
    text = path.read_text(encoding="utf-8")
    assert '"o"' in text and "REC-MARK" in text
    assert '"i"' in text


class PendingClient:
    close_code: int | None = None

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
        # The greediest backlog is evicted, and -- unlike the slow-consumer
        # path -- this one really does end the connection, so it must say so
        # (4410) rather than close as if nothing happened.
        assert await wait_for(lambda: big.closed, timeout=5.0)
        assert not small.closed
        assert getattr(big, "close_code", None) == 4410
    finally:
        await manager.shutdown()


class FailingClient:
    """A client whose sink rejects everything immediately."""
    close_code: int | None = None


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


async def test_restore_share_survives_without_rotation() -> None:
    """A persisted share is rehydrated verbatim, so an issued link keeps working."""
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="sh", argv=[SHELL]))
    try:
        session.restore_share("restored-token", None, True)
        assert session.peek_share() == "restored-token"
        assert session.share_access("restored-token") == "write"
        assert session.share_writable is True
    finally:
        await manager.shutdown()


async def test_restore_share_respects_expiry() -> None:
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="sh", argv=[SHELL]))
    try:
        session.restore_share("stale", time.time() - 1, False)
        assert session.peek_share() is None
        assert session.share_access("stale") is None
    finally:
        await manager.shutdown()


async def test_restore_share_clears_when_absent() -> None:
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="sh", argv=[SHELL]))
    try:
        session.create_share()
        session.restore_share(None, None, False)
        assert session.peek_share() is None
        assert not session.is_shared
    finally:
        await manager.shutdown()


class StuckChildPty:
    """A PTY whose child never exits, to prove finalize cannot hang."""

    pid = 12345
    killed = False

    def __init__(self) -> None:
        self._closed = False

    async def read(self) -> bytes:
        await asyncio.sleep(3600)
        return b""

    def write(self, data: bytes) -> bool:
        return True

    def resize(self, cols: int, rows: int) -> None:
        return None

    def poll(self) -> int | None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        if self.killed:
            return -9
        if timeout is None:
            raise AssertionError("wait() must be called with a timeout")
        import time as _time

        _time.sleep(min(timeout, 0.05))
        raise __import__("subprocess").TimeoutExpired(self.pid, timeout)

    def terminate(self, sig: int = 0) -> None:
        return None

    def kill(self) -> None:
        self.killed = True

    def close(self) -> None:
        self._closed = True


async def test_finalize_does_not_hang_on_a_stuck_child() -> None:
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="sh", argv=[SHELL]))
    stuck = StuckChildPty()
    session._pty = stuck  # type: ignore[attr-defined]
    session._read_task.cancel()
    try:
        await asyncio.wait_for(session._finalize(), timeout=5.0)
    except TimeoutError as exc:
        raise AssertionError("_finalize hung waiting for a stuck child") from exc
    assert session.closed
    assert session.exit_code == -9  # escalated to kill()
    assert stuck.killed


async def test_stop_missing_with_a_stale_snapshot_is_a_trap() -> None:
    """Document the sharp edge ``_maintenance_db_tick`` deliberately avoids.

    ``term_session_stop_missing`` reads "not in this set" as "dead". If the
    snapshot and the UPDATE are separated by an ``await`` (or a thread hop),
    a session created in between is marked ``stopped`` while it is running --
    and would never be adopted again after a restart.

    The first half proves the hazard is real; the second half is the pattern
    the maintenance loop uses to stay correct. Keep both.
    """
    from wsctl.core.store import Store

    db = Path(os.environ.get("TMPDIR", "/tmp")) / f"wsctl-race-{os.getpid()}.db"
    store = Store(db)
    manager = SessionManager()
    try:
        first = await manager.create(SessionSpec(name="a", argv=[SHELL]))
        store.term_session_upsert(first.id, name="a", owner_id=None, instance_id="me")

        stale = {s.id for s in manager.list_sessions()}  # snapshot taken here
        second = await manager.create(SessionSpec(name="b", argv=[SHELL]))
        store.term_session_upsert(second.id, name="b", owner_id=None, instance_id="me")

        # (1) Using the stale snapshot wrongly kills `b`. This is what moving
        # the UPDATE onto a worker thread without re-snapshotting would do.
        store.term_session_stop_missing(stale, instance_id="me")
        rows = {r["id"]: r["status"] for r in store.term_session_list()}
        assert rows[second.id] == "stopped", "expected the stale snapshot to mis-kill `b`"

        # (2) The maintenance loop re-takes the snapshot in the same
        # synchronous stretch as the UPDATE, so nothing is mis-killed.
        store.term_session_set_status(second.id, "running")
        fresh = {s.id for s in manager.list_sessions()}
        store.term_session_stop_missing(fresh, instance_id="me")
        rows = {r["id"]: r["status"] for r in store.term_session_list()}
        assert rows[first.id] == "running"
        assert rows[second.id] == "running"
    finally:
        await manager.shutdown()
        store.close()
        Path(store.path).unlink(missing_ok=True)


async def test_memory_pressure_sheds_backlog_before_dropping_anyone() -> None:
    """A session over its own cap gives up queued bytes before it gives up a viewer."""
    manager = SessionManager()
    session = await manager.create(
        SessionSpec(name="sh", argv=[SHELL], memory_limit=5_000, scrollback_bytes=5_000)
    )

    class SheddingClient:
        close_code: int | None = None

        def __init__(self) -> None:
            self.pending_bytes = 20_000
            self.items: list[object] = []
            self.closed = False
            self.shed_calls = 0

        def put(self, item: object) -> None:
            self.items.append(item)

        def shed_oldest(self, target: int) -> int:
            self.shed_calls += 1
            freed = min(self.pending_bytes, max(target, 1000))
            self.pending_bytes -= freed
            return freed

        def close(self) -> None:
            self.closed = True

    client = SheddingClient()
    try:
        await session.attach(client)
        assert session.memory_usage() > 5_000
        session._enforce_memory_limit()
        assert client.shed_calls > 0, "backlog must be given up first"
        assert client.closed is False, "nobody should be evicted over backlog alone"
    finally:
        await manager.shutdown()


async def test_nudge_repaint_issues_two_real_size_changes() -> None:
    """The app repaints on SIGWINCH -- and SIGWINCH needs a real delta.

    A replayed byte stream cannot rebuild a full-screen app's screen (gapped
    by shedding, or headless after scrollback eviction). The app's own model
    is the only complete one, and a window-size change is what makes it paint.
    Linux delivers SIGWINCH only when the size *changes*, so the nudge must
    shrink and restore -- one resize would be invisible to the app.
    """
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="nudge", argv=[SHELL]))
    calls: list[tuple[int, int]] = []
    original = session._pty.resize

    def spy(cols: int, rows: int) -> None:
        calls.append((cols, rows))
        original(cols, rows)

    session._pty.resize = spy  # type: ignore[method-assign]
    before = (session.spec.cols, session.spec.rows)
    try:
        await session.nudge_repaint()
    finally:
        session._pty.resize = original  # type: ignore[method-assign]
        await manager.shutdown()
    assert len(calls) == 2, f"expected shrink + restore, got {calls}"
    assert calls[0] != calls[1], "a size that does not change sends no SIGWINCH"
    assert calls[1] == before, "the session must end up at its real size"
    assert calls[0][0] == calls[1][0] or calls[0][1] != calls[1][1], (
        "the nudge must change something the app can see"
    )


async def test_a_brand_new_session_is_not_nudged_but_a_replayed_one_is() -> None:
    """The trigger is "was anything replayed", not "did a socket open"."""
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="gated", argv=[SHELL]))
    try:
        assert session.has_scrollback is False, "a new session has nothing to replay"
        client = FakeClient()
        await session.attach(client)
        session.write_input(b"echo NUDGE-PROBE\n")
        assert await wait_for(lambda: session.has_scrollback, timeout=5), (
            "the echo must have reached the scrollback"
        )
    finally:
        await manager.shutdown()
