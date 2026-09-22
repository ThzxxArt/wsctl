"""The async audit writer must not block and must flush deterministically."""

from __future__ import annotations

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
