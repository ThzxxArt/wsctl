"""The async audit writer must not block and must flush deterministically."""

from __future__ import annotations

import asyncio
from pathlib import Path

from wsctl.core.audit import AuditWriter
from wsctl.core.store import Store


async def test_flush_writes_queued_events(tmp_path: Path) -> None:
    store = Store(tmp_path / "audit.db")
    try:
        writer = AuditWriter(store)
        writer.enqueue("login", user_id=1)
        writer.enqueue("login_failed", ip="1.2.3.4")
        await writer.flush()
        events = {e["event"] for e in store.recent_audit()}
        assert {"login", "login_failed"} <= events
    finally:
        store.close()


async def test_start_stop_round_trip(tmp_path: Path) -> None:
    store = Store(tmp_path / "audit2.db")
    try:
        writer = AuditWriter(store)
        writer.start()
        writer.enqueue("session_create", term_session_id="abc")
        await writer.stop()  # flush + cancel
        events = [e["event"] for e in store.recent_audit()]
        assert "session_create" in events
    finally:
        store.close()


async def test_drops_when_queue_is_full(tmp_path: Path) -> None:
    store = Store(tmp_path / "audit3.db")
    try:
        writer = AuditWriter(store, max_queue=2)
        for _ in range(10):
            writer.enqueue("spam")
        assert writer.dropped >= 8
    finally:
        store.close()


class _BrokenStore:
    """A store whose write path always fails, to prove the writer survives."""

    def __init__(self) -> None:
        self.batches: list[list[dict[str, object]]] = []

    def write_events(self, batch: list[dict[str, object]]) -> None:
        raise RuntimeError("disk full")

    def dispatch_events(self, batch: list[dict[str, object]]) -> None:
        self.batches.append(batch)


async def test_writer_survives_write_failure() -> None:
    store = _BrokenStore()
    writer = AuditWriter(store)  # type: ignore[arg-type]
    writer.enqueue("one")
    await writer.flush()
    assert writer.errors == 1
    # The task must still be usable for subsequent events.
    writer.enqueue("two")
    await writer.flush()
    assert writer.errors == 2
    assert len(store.batches) == 2  # sink still notified


async def test_concurrent_flush_preserves_order(tmp_path: Path) -> None:
    store = Store(tmp_path / "audit4.db")
    try:
        writer = AuditWriter(store)
        writer.start()
        for index in range(50):
            writer.enqueue("event", payload=str(index))
        await asyncio.gather(writer.flush(), writer.flush())
        await writer.stop()
        rows = store.recent_audit(limit=100)
        payloads = [r["payload"] for r in rows]
        assert payloads == [str(i) for i in range(49, -1, -1)]
    finally:
        store.close()
