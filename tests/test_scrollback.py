from __future__ import annotations

import pytest

from wsctl.core.ansi import AnsiTracker
from wsctl.core.scrollback import Scrollback


def test_append_and_snapshot() -> None:
    sb = Scrollback(1024)
    sb.append(b"hello ")
    sb.append(b"world")
    assert sb.snapshot() == b"hello world"
    assert sb.size == 11


def test_eviction_oldest_first() -> None:
    sb = Scrollback(10)
    sb.append(b"aaaaa")
    sb.append(b"bbbbb")
    sb.append(b"ccccc")
    assert sb.size <= 10
    assert sb.snapshot().endswith(b"ccccc")


def test_single_oversized_chunk_keeps_tail() -> None:
    sb = Scrollback(5)
    sb.append(b"0123456789")
    assert sb.snapshot() == b"56789"


def test_trim_does_not_leave_an_orphaned_utf8_continuation() -> None:
    """Eviction is byte-wise, but a replay must start on a character boundary."""
    sb = Scrollback(8)
    # "A" + a 3-byte character (€ = e2 82 ac) + filler, repeated so the cap cuts
    # inside one of the multi-byte sequences.
    payload = b"A" + "\u20ac".encode() + b"BBBBBBB"
    sb.append(payload * 3)
    snapshot = sb.snapshot()
    assert len(snapshot) <= 8
    assert snapshot.decode("utf-8", "strict")  # no orphaned continuation bytes


def test_invalid_capacity() -> None:
    with pytest.raises(ValueError):
        Scrollback(0)


def test_zero_pressure_split_sequence_is_kept_whole() -> None:
    """A chunk that merely *ends* mid-escape must not be dropped.

    PTY reads routinely split ``ESC[31m`` across two 64 KiB reads (``ESC[3``
    then ``1m``). Keeping both halves is what makes the replay start at the
    real beginning of the stream.

    The first version of ``_trim`` ran its re-anchor pass on *every* append and
    keyed it off "the head chunk ends inside a sequence", so the first half of
    every split colour vanished with **zero** memory pressure and the replay
    opened with literal ``1m`` -- the colour tail read as text. This test is
    the regression: under no pressure at all, the stream must come back whole.
    """
    sb = Scrollback(max_bytes=4096)
    sb.append(b"ok \x1b[3")  # ends inside the CSI
    sb.append(b"1mRED\x1b[0m")  # completes it
    assert sb.snapshot() == b"ok \x1b[31mRED\x1b[0m"

    # The same for an OSC split across two reads (``ESC]`` ... BEL).
    sb = Scrollback(max_bytes=4096)
    sb.append(b"\x1b]0;ti")
    sb.append(b"tle\x07after")
    assert sb.snapshot() == b"\x1b]0;title\x07after"


def test_replay_start_is_re_anchored_after_eviction() -> None:
    """A replay must not begin in the middle of a colour sequence.

    The head of the buffer becomes the head of the replay after eviction. If
    the evicted bytes ended mid-escape, a reconnecting client replays a broken
    stream into its terminal -- which is how a full-screen program ended up
    showing garbage instead of its screen.

    The **strong** form: feed the *evicted prefix* to a tracker and assert the
    cut lands outside a sequence. Checking the kept head on its own is the
    weak property -- a stream that opens ``1mABC`` does not start with ESC and
    so passes "not inside" while being exactly the tail of a colour sequence
    about to be read as text. (T5 in ``test_load.py`` learned this the hard
    way; scrollback previously had only the weak check and was wrong anyway.)
    """
    red = b"\x1b[31m"
    tail = b"VISIBLE"
    buf = Scrollback(max_bytes=40)
    appended = b""
    # Enough bytes that eviction cuts into the middle of `ESC[31m`.
    for _ in range(10):
        part1 = b"x" * 30 + red[:3]
        part2 = red[3:] + tail + b"\x1b[0m"
        buf.append(part1)
        buf.append(part2)
        appended += part1 + part2

    kept = b"".join(buf.chunks())
    assert kept, "eviction must not empty the buffer outright"
    assert appended.endswith(kept), "shedding must only ever drop a prefix"
    cut = len(appended) - len(kept)
    tracker = AnsiTracker()
    tracker.feed(appended[:cut])
    assert not tracker.inside, (
        f"the cut at offset {cut} landed inside a sequence; kept opens {kept[:12]!r}"
    )


def test_single_oversized_chunk_tail_cut_is_sequence_aligned() -> None:
    """A tail cut inside ``ESC[31m`` must not replay as literal ``1m...``.

    The tail-trim path is the same contract as eviction: whatever is kept is
    the start of a replay, so it must start where a terminal considers a fresh
    start. Two cases: the cut lands *before* the ESC (the tail opens with a
    complete sequence -- keep it) and the cut lands *inside* the sequence (the
    tail opens with ``31m...`` -- skip to after the ``m``).
    """
    full = b"AAAAA\x1b[31mXYZ"  # cut before ESC: tail = `\x1b[31mXYZ`
    sb = Scrollback(max_bytes=8)
    sb.append(full)
    kept = sb.snapshot()
    assert full.endswith(kept)
    cut = len(full) - len(kept)
    tracker = AnsiTracker()
    tracker.feed(full[:cut])
    assert not tracker.inside, f"cut {cut} inside a sequence, kept={kept!r}"

    full = b"AAAA\x1b[31mXYZ"  # 4 filler + ESC[3 + 1mXYZ; cap cuts inside
    sb = Scrollback(max_bytes=7)
    sb.append(full)
    kept = sb.snapshot()
    assert full.endswith(kept)
    cut = len(full) - len(kept)
    tracker = AnsiTracker()
    tracker.feed(full[:cut])
    assert not tracker.inside, f"cut {cut} inside a sequence, kept={kept!r}"
    # And the colour tail must not appear as text.
    assert not kept.startswith(b"1m") and not kept.startswith(b"[31m")


def test_replay_frames_coalesce_but_preserve_every_byte() -> None:
    """Wire frames are about 64 KiB each; the bytes are exactly the buffer.

    Stored chunk boundaries are PTY-read boundaries. A full-screen app writes
    in hundreds of tiny bursts, and a replay of those verbatim is thousands of
    outbound *frames* -- which is what the per-client frame cap measures. The
    cap then shreds the replay it is supposed to protect.
    """
    sb = Scrollback(max_bytes=1024 * 1024)
    payload = b"".join(b"line-%d\r\n" % i for i in range(5000))
    for i in range(0, len(payload), 17):  # deliberately ragged PTY-sized reads
        sb.append(payload[i : i + 17])
    frames = sb.replay_frames()
    assert b"".join(frames) == payload, "coalescing changed the stream"
    assert len(frames) < 20, f"{len(frames)} frames is still a frame-cap hazard"
    assert all(len(f) <= 64 * 1024 + 17 for f in frames)


def test_a_tui_sized_replay_must_not_be_shredded_by_the_frame_cap() -> None:
    """The user-visible failure, pinned: idle prompt, "lost sync", TUI comes back.

    A TUI's output is thousands of tiny PTY reads. An attach replay enqueues
    them all at once; the outbound queue's *frame* cap (512) then drops the
    head of the very replay in progress -- long before the multi-megabyte
    *byte* budget is anywhere near. The viewer is told it "lost sync" while
    sitting at an idle prompt, and the screen that comes back is a TUI they
    had already exited, drawn from a gapped stream.

    Two clients, one buffer: coalesced frames must survive the cap where raw
    chunks provably do not -- otherwise this test cannot tell the fix from the
    defect.
    """
    from wsctl.server.client import WsClient

    class Capture:
        def __init__(self) -> None:
            self.parts: list[bytes] = []

        async def send_bytes(self, data: bytes) -> None:
            self.parts.append(data)

        async def send_json(self, obj: object) -> None:  # pragma: no cover
            pass

    sb = Scrollback(max_bytes=4 * 1024 * 1024)
    payload = b""
    for i in range(2000):
        chunk = f"\x1b[3{i % 6}mTUI-{i}\x1b[0m\r\n".encode()
        sb.append(chunk)
        payload += chunk

    coalesced = WsClient(Capture(), max_pending=512, max_bytes=8 * 1024 * 1024)  # type: ignore[arg-type]
    for frame in sb.replay_frames():
        coalesced.put(frame)
    assert coalesced.dropped_bytes == 0, "the replay shed its own head"
    assert coalesced.queued_binary() == payload

    raw = WsClient(Capture(), max_pending=512, max_bytes=8 * 1024 * 1024)  # type: ignore[arg-type]
    for chunk in sb.chunks():
        raw.put(chunk)
    assert raw.dropped_bytes > 0, (
        "the raw-chunk path no longer outruns the frame cap, so this guard "
        "cannot distinguish coalescing from the defect it exists to prevent"
    )


def test_a_replay_frame_never_exceeds_the_client_byte_budget() -> None:
    """A frame larger than the budget is dropped *whole* -- sizing is not free.

    Coalescing to 64 KiB looked right until a 16 KiB ``client_max_bytes`` came
    along: every replay frame then failed to fit at all (``put`` sheds a frame
    that cannot ever enter the queue) and the entire replay vanished, where
    the raw (small) chunks used to fit. The frame size has to sit under the
    budget *and* keep the frame count low.

    A budget smaller than the replay is lossy *by design* -- only the tail
    fits in flight -- and that loss is what ``incomplete`` reports. What must
    never happen is a frame that could have fit being thrown away for its size.
    """
    from wsctl.core.session import replay_target_for
    from wsctl.server.client import WsClient

    class Capture:
        async def send_bytes(self, data: bytes) -> None:  # pragma: no cover
            return None

        async def send_json(self, obj: object) -> None:  # pragma: no cover
            return None

    small = WsClient(Capture(), max_pending=512, max_bytes=1024)  # type: ignore[arg-type]
    assert 0 < replay_target_for(small) <= 1024, "the target must fit a 1 KiB budget"

    sb = Scrollback(max_bytes=256 * 1024)
    payload = b"".join(b"row-%04d\r\n" % i for i in range(8000))  # ~80 KB
    for i in range(0, len(payload), 64):
        sb.append(payload[i : i + 64])
    frames = sb.replay_frames(replay_target_for(small))
    assert frames, "a non-empty buffer must produce frames"
    assert all(len(f) <= 1024 for f in frames), "a frame exceeds the budget"
    assert b"".join(frames) == payload, "sizing changed the stream"

    # Room for the whole replay: sized frames deliver it complete. (The
    # TUI/many-chunks case is the companion test; this is the sizing contract.)
    roomy = WsClient(Capture(), max_pending=512, max_bytes=len(payload) + 1024)  # type: ignore[arg-type]
    for frame in sb.replay_frames(replay_target_for(roomy)):
        roomy.put(frame)
    assert roomy.dropped_bytes == 0, "a replay that fits the budget must arrive whole"
    assert roomy.queued_binary() == payload

    # Budget smaller than the replay: the tail survives, the loss is real and
    # counted (that count is what turns into `incomplete`). What is forbidden
    # is a *whole* replay vanishing because every frame was born oversized.
    tiny = WsClient(Capture(), max_pending=512, max_bytes=1024)  # type: ignore[arg-type]
    for frame in frames:
        tiny.put(frame)
    assert tiny.queued_binary(), "even a tight budget must keep the tail"
    assert tiny.queued_binary() == payload[-len(tiny.queued_binary()) :]
    assert tiny.dropped_bytes > 0, "80 KB cannot fit in 1 KiB; pretend loss is a lie"

    # Unbounded clients keep the wide default: the frame count stays low for a
    # TUI flood (that is what the companion test's frame cap is about).
    wide = WsClient(Capture(), max_pending=512, max_bytes=0)  # type: ignore[arg-type]
    assert replay_target_for(wide) == 65536
