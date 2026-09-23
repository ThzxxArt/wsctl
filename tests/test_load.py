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


# -- 0.1.15 性能验收（数值门，见 DESIGN.md M65）-----------------------------
#
# These are the numbers the 0.1.15 plan committed to. They are deliberately
# generous (a cold CI runner is not a benchmark rig) but they are *numbers*:
# "the UI does not stutter" is not testable, "seq 1 200000 completes in 8s with
# no evictions and no frame loss" is.
#
#   T1  `seq 1 200000` end to end                 <= 8s
#   T2  8 sessions x `seq 1 50000`, all complete  <= 20s
#       and nobody is evicted / backpressure-dropped
#   T3  event-loop lag under that flood           < 50ms

T1_BUDGET = 8.0
T2_BUDGET = 20.0
T3_LAG_LIMIT = 0.05


def _done_marker(factor: int) -> bytes:
    """A completion marker the *command line* cannot contain.

    The marker must be produced by the shell and never appear in the input,
    or the wait matches the terminal's **echo of the command** and returns
    before the work has even started. That is not a hypothetical: the first
    version of these tests used ``echo T1-DONE``, which appeared in the echo,
    and the "200,000 lines in 8 seconds" assertion passed in 50ms having
    verified nothing at all. ``scripts/e2e/run_all.py`` hit the identical trap
    in 0.1.11 and switched to ``$?`` / ``$((6*7))`` for the same reason.

    ``factor * factor`` lands outside every ``seq`` range used here and is
    spelled as arithmetic in the input, so neither half of the stream can
    forge it.
    """
    return b"\r\n" + str(factor * factor).encode("ascii") + b"\r\n"


async def _flood(session: object, factor: int, budget: float) -> FakeClient:
    """Attach one viewer, flood it with ``seq 1 200000``, and return the client.

    ``factor`` is the square root of the completion marker the shell computes
    (see :func:`_done_marker`); the marker itself is never written into the
    input, so the wait cannot match the terminal's echo of this command.
    """
    client = FakeClient()
    await session.attach(client)  # type: ignore[attr-defined]
    marker = _done_marker(factor)
    session.write_input(  # type: ignore[attr-defined]
        f"seq 1 200000; echo $(({factor}*{factor}))\n".encode("ascii")
    )
    await wait_for(lambda: marker in client.output(), timeout=budget)
    return client


@pytest.mark.slow
async def test_t1_large_output_completes_within_budget() -> None:
    """``seq 1 200000`` must come back whole, and quickly.

    Whole is the point: the alternative to being fast used to be being fast by
    losing the viewer, which is not the same thing. The viewer must still be
    attached at the end, the stream must be unbroken, and the timing must be
    measured against the *real* end of the output -- not the shell's echo of
    the command that started it (see :func:`_done_marker`).
    """
    from wsctl.core.session import slow_consumer_drops

    marker = _done_marker(700)  # 490000 -- outside `seq 1 200000`
    manager = SessionManager()
    session = await manager.create(SessionSpec(name="t1", argv=[SHELL]))
    before = slow_consumer_drops()
    try:
        loop = asyncio.get_running_loop()
        started = loop.time()
        client = await _flood(session, 700, T1_BUDGET)
        elapsed = loop.time() - started
        assert marker in client.output(), (
            f"seq 1 200000 did not finish in {T1_BUDGET}s (ran {elapsed:.2f}s)"
        )
        assert elapsed <= T1_BUDGET, f"seq 1 200000 took {elapsed:.2f}s > {T1_BUDGET}s"
        assert session.client_count == 1, "the viewer was dropped, not slowed"
        assert slow_consumer_drops() == before, "no viewer may be sacrificed here"
        # The stream is whole. Framed with line breaks so the shell's echo of
        # `seq 1 200000` cannot satisfy it.
        out = client.output()
        assert b"\r\n199999\r\n" in out and b"\r\n200000\r\n" in out
    finally:
        await manager.shutdown()


@pytest.mark.slow
async def test_t2_eight_sessions_flood_lose_no_viewers() -> None:
    """Eight concurrent floods: everyone finishes, nobody is evicted.

    The two counters are the whole assertion. ``wsctl_clients_evicted_total``
    rising here means a session over its own cap gave up on a person instead of
    on backlog -- the exact over-punishment 0.1.12 spent a release removing.
    """
    from wsctl.core.session import evicted_clients_total, slow_consumer_drops

    marker = _done_marker(750)  # 562500 -- outside `seq 1 50000`
    factor = 750
    manager = SessionManager()
    drops_before = slow_consumer_drops()
    evict_before = evicted_clients_total()
    sessions = []
    clients: list[FakeClient] = []
    try:
        for index in range(8):
            s = await manager.create(SessionSpec(name=f"t2-{index}", argv=[SHELL]))
            sessions.append(s)
            c = FakeClient()
            await s.attach(c)
            clients.append(c)
        loop = asyncio.get_running_loop()
        started = loop.time()
        cmd = f"seq 1 50000; echo $(({factor}*{factor}))\n".encode("ascii")
        for s in sessions:
            s.write_input(cmd)
        ok = await wait_for(
            lambda: all(marker in c.output() for c in clients), timeout=T2_BUDGET
        )
        elapsed = loop.time() - started
        assert ok, "at least one session never finished inside the budget"
        assert elapsed <= T2_BUDGET, f"eight floods took {elapsed:.2f}s > {T2_BUDGET}s"
        assert all(s.client_count == 1 for s in sessions), "a viewer was dropped"
        assert slow_consumer_drops() == drops_before, "backpressure dropped a viewer"
        assert evicted_clients_total() == evict_before, "memory pressure evicted a viewer"
    finally:
        await manager.shutdown()


@pytest.mark.slow
async def test_t3_event_loop_stays_responsive_under_a_flood() -> None:
    """The loop must not be the thing that freezes.

    This is the server-side counterpart of the client-side freeze: the shedding
    scan used to run inside ``_broadcast`` while the session lock was held, so a
    flood stalled the loop, the PTY backed up into the kernel, and the shell
    itself stopped writing. Measured here by a 5ms watchdog that reports its own
    scheduling delay while the flood runs.
    """
    marker = _done_marker(800)  # 640000
    factor = 800
    loop = asyncio.get_running_loop()
    worst = 0.0
    stop = False

    async def watchdog() -> None:
        nonlocal worst
        while not stop:
            due = loop.time() + 0.005
            await asyncio.sleep(0.005)
            worst = max(worst, max(0.0, loop.time() - due))

    manager = SessionManager()
    guard = asyncio.create_task(watchdog())
    try:
        sessions = []
        clients: list[FakeClient] = []
        for index in range(8):
            s = await manager.create(SessionSpec(name=f"t3-{index}", argv=[SHELL]))
            sessions.append(s)
            c = FakeClient()
            await s.attach(c)
            clients.append(c)
        cmd = f"seq 1 50000; echo $(({factor}*{factor}))\n".encode("ascii")
        for s in sessions:
            s.write_input(cmd)
        assert await wait_for(
            lambda: all(marker in c.output() for c in clients), timeout=T2_BUDGET
        )
    finally:
        stop = True
        await guard
        await manager.shutdown()
    assert worst < T3_LAG_LIMIT, f"event loop stalled for {worst * 1000:.0f}ms"
