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
from wsctl.server.client import WsClient

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
            session.write_input(b"echo $((41*43))\n")

        ok = await wait_for(
            lambda: all(
                b"1763\r\n" in c.output() for group in clients for c in group
            )
        )
        assert ok, "not every client received the shell's computed output"
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
        # 492803 = 701*703 is outside `seq 1 50000` and is spelled as arithmetic
        # in the input, so the echo of the command cannot satisfy the wait.
        session.write_input(b"seq 1 50000; echo $((701*703))\n")
        assert await wait_for(
            lambda: b"492803\r\n" in client.output(), timeout=30
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
        assert b"199999\r\n" in out and b"200000\r\n" in out
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


@pytest.mark.slow
async def test_t4_sustained_flood_loses_no_viewers() -> None:
    """``yes`` for ten seconds: nobody is dropped, and it really did flood.

    The other gates measure a bounded burst. This one measures a *sustained*
    one -- the shape of a runaway build log -- where the pressure never lets
    up and every eviction decision is taken under load rather than at the end.

    The completion marker is arithmetic (see :func:`_done_marker`): a literal
    would match the terminal's echo of the command and the assertion would pass
    having proved nothing, which is exactly how the first version of T1 fooled
    itself.
    """
    from wsctl.core.session import evicted_clients_total, slow_consumer_drops

    marker = _done_marker(950)  # 902500
    factor = 950
    manager = SessionManager()
    drops_before = slow_consumer_drops()
    evict_before = evicted_clients_total()
    sessions = []
    clients: list[FakeClient] = []
    try:
        for index in range(8):
            s = await manager.create(SessionSpec(name=f"t4-{index}", argv=[SHELL]))
            sessions.append(s)
            c = FakeClient()
            await s.attach(c)
            clients.append(c)
        # Bounded twice over: `head -c` caps the *volume* (an unbounded `yes`
        # pushed a gigabyte into eight in-memory sinks and the test died of its
        # own success) and `timeout` caps the *wall clock*. Whichever trips
        # first ends the storm.
        #
        # The extra `echo` is load-bearing: `head -c` cuts `yes` mid-line, so
        # without it the marker is glued onto the flood -- ``WSCTL-FL902500`` --
        # and a marker that starts with a line break never matches. (Same family
        # as the echo-of-the-command trap in :func:`_done_marker`.)
        cmd = (
            f"timeout 10 {SHELL} -c 'yes WSCTL-FLOOD | head -c 2000000'; "
            f"echo; echo $(({factor}*{factor}))\n"
        ).encode("ascii")
        for s in sessions:
            s.write_input(cmd)

        def finished() -> bool:
            # Only the tail: ``c.output()`` joins the whole buffer, and calling
            # it every 50ms against megabytes of flood made the *test* the
            # bottleneck (O(n^2) copying) rather than the code under test.
            return all(
                any(isinstance(i, bytes) and marker in i for i in c.items[-4:])
                for c in clients
            )

        ok = await wait_for(finished, timeout=25.0)
        assert ok, "a sustained flood never finished inside the budget"
        assert all(s.client_count == 1 for s in sessions), "a viewer was dropped"
        assert slow_consumer_drops() == drops_before, "backpressure dropped a viewer"
        assert evicted_clients_total() == evict_before, "memory pressure evicted a viewer"
        # Proof it was a real storm rather than a quiet success: a megabyte of
        # output must have crossed the wire.
        assert sum(len(c.output()) for c in clients) >= 8 * 500_000, (
            "the flood produced almost no output; the gate proved nothing"
        )
    finally:
        await manager.shutdown()


@pytest.mark.slow
async def test_t5_colour_flood_sheds_on_a_sequence_boundary() -> None:
    """200KB of colour escapes through a *forced* shed stays a valid stream.

    This is the 花屏 contract at the byte level: dropping anywhere but a
    sequence boundary hands a terminal the tail of ``ESC[31m`` to read as text,
    and a full-screen program then draws garbage until its next full repaint.
    The budget here is deliberately tiny so eviction is certain to run.
    """
    from wsctl.core.ansi import AnsiTracker, is_boundary_aligned

    class Capture:
        def __init__(self) -> None:
            self.parts: list[bytes] = []

        async def send_bytes(self, data: bytes) -> None:
            self.parts.append(data)

        async def send_json(self, obj: object) -> None:  # pragma: no cover
            pass

    client = WsClient(Capture(), max_pending=64, max_bytes=4096)  # type: ignore[arg-type]
    cycle = b"\x1b[31mRED-TEXT\x1b[0m \x1b[32mGREEN\x1b[0m \x1b[1mBOLD\x1b[0m\r\n"
    payload = cycle * (200_000 // len(cycle) + 1)
    assert len(payload) >= 200_000, "the flood must actually be 200KB"
    # Split at arbitrary points so frames routinely begin mid-sequence, which
    # is the case a stateless scan gets wrong and a streaming tracker does not.
    offset = 0
    sizes = (3, 5, 7, 11, 13, 17, 19)
    i = 0
    while offset < len(payload):
        n = sizes[i % len(sizes)]
        client.put(payload[offset : offset + n])
        offset += n
        i += 1

    assert client.dropped_bytes > 0, "the point of the test is what survives a shed"
    kept = client.queued_binary()
    assert is_boundary_aligned(kept), "the surviving stream starts mid-escape"
    assert payload.endswith(kept), "shedding must only ever drop a prefix"

    # The strong form: where the kept stream begins, the discarded prefix must
    # have left the parser *outside* a sequence. Checking ``kept`` on its own
    # is the weaker property -- a stream that opens ``1mABC`` does not start
    # with ESC and so passes ``is_boundary_aligned`` while being exactly the
    # tail of a colour sequence about to be read as text.
    cut = len(payload) - len(kept)
    tracker = AnsiTracker()
    tracker.feed(payload[:cut])
    assert not tracker.inside, (
        f"the cut at offset {cut} landed inside a sequence; kept opens {kept[:12]!r}"
    )
