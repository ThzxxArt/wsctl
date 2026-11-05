from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from wsctl.core.command_block import CommandBlockParser
from wsctl.core.protocol import (
    FLAG_REPLAY,
    FLAG_SNAPSHOT,
    ProtocolError,
    decode_control,
    decode_data,
    decode_input,
    encode_control,
    encode_data,
    encode_input,
)
from wsctl.core.scrollback import Scrollback
from wsctl.core.snapshot import PyteSnapshot, RawSnapshot


def test_data_frame_round_trip_with_large_sequence() -> None:
    frame = encode_data(2**40 + 9, b"hello", FLAG_REPLAY | FLAG_SNAPSHOT)
    decoded = decode_data(frame)
    assert decoded.seq == 2**40 + 9
    assert decoded.flags == FLAG_REPLAY | FLAG_SNAPSHOT
    assert decoded.payload == b"hello"


@pytest.mark.parametrize("seq", [-1, 2**64])
def test_data_frame_rejects_bad_sequence(seq: int) -> None:
    with pytest.raises(ProtocolError):
        encode_data(seq, b"x")


def test_data_frame_rejects_unknown_kind_and_flags() -> None:
    frame = bytearray(encode_data(0, b"x"))
    frame[0] = 9
    with pytest.raises(ProtocolError):
        decode_data(bytes(frame))
    with pytest.raises(ProtocolError):
        encode_data(0, b"x", 0x80)


def test_input_frame_round_trip_and_limit() -> None:
    decoded = decode_input(encode_input(b"ls\r"))
    assert decoded.payload == b"ls\r"
    with pytest.raises(ProtocolError):
        encode_input(b"x" * (256 * 1024 + 1))


@given(st.binary(max_size=1024), st.integers(min_value=0, max_value=2**60))
@settings(max_examples=60, deadline=None)
def test_data_frame_fuzz_round_trip(payload: bytes, seq: int) -> None:
    decoded = decode_data(encode_data(seq, payload))
    assert decoded.seq == seq
    assert decoded.payload == payload


def test_control_round_trip_and_version_check() -> None:
    message = decode_control(encode_control({"type": "ack", "seq": 7}))
    assert message["v"] == 2
    with pytest.raises(ProtocolError):
        decode_control('{"v":1,"type":"ack","seq":1}')
    with pytest.raises(ProtocolError):
        decode_control('{"v":2,"type":"nope"}')


def test_scrollback_sequence_offsets_survive_eviction() -> None:
    ring = Scrollback(max_bytes=32)
    assert ring.append(b"a" * 16) == 0
    assert ring.append(b"b" * 16) == 16
    assert ring.base_seq == 0
    assert ring.end_seq == 32
    ring.append(b"c" * 16)
    assert ring.end_seq == 48
    assert ring.base_seq == 16
    assert ring.since(0) is None
    resumed = ring.since(24)
    assert resumed is not None
    assert b"".join(chunk for _seq, chunk in resumed) == b"b" * 8 + b"c" * 16
    assert ring.since(48) == ()
    assert ring.since(49) is None


def test_scrollback_sequence_is_stable_across_clear() -> None:
    ring = Scrollback()
    ring.append(b"abc")
    ring.clear()
    assert ring.base_seq == ring.end_seq == 3


@pytest.mark.asyncio
async def test_pyte_snapshot_reconstructs_a_screen() -> None:
    snapshotter = PyteSnapshot(20, 3, mode="always")
    snapshotter.feed(b"hello\r\n\x1b[1;31mred\x1b[0m")
    snap = await snapshotter.capture(42)
    assert snap is not None
    assert snap.seq == 42
    assert snap.capable is True
    assert b"hello" in snap.data
    assert b"red" in snap.data
    assert b"\x1b[2J" in snap.data


@pytest.mark.asyncio
async def test_raw_snapshot_is_honest_about_being_a_replay() -> None:
    ring = Scrollback()
    ring.append(b"output")
    snap = await RawSnapshot(ring).capture(5)
    assert snap is not None
    assert snap.capable is False
    assert snap.data == b"output"


@pytest.mark.asyncio
async def test_pyte_snapshot_keeps_split_utf8_and_wide_characters() -> None:
    snapshotter = PyteSnapshot(20, 3, mode="always")
    encoded = "中文测试ABC".encode()
    # Split inside the first multi-byte character, as a PTY read can.
    snapshotter.feed(encoded[:1])
    snapshotter.feed(encoded[1:])
    snap = await snapshotter.capture(0)
    assert snap is not None and snap.capable
    assert "中文测试ABC" in snap.data.decode("utf-8")
    # A wide glyph must not be followed by an extra space stub.
    assert "中 文" not in snap.data.decode("utf-8")


@pytest.mark.asyncio
async def test_pyte_snapshot_preserves_truecolor() -> None:
    snapshotter = PyteSnapshot(20, 2, mode="always")
    snapshotter.feed(b"\x1b[38;2;255;0;0mred\x1b[0m")
    snap = await snapshotter.capture(0)
    assert snap is not None
    assert b"38;2;255;0;0" in snap.data


def test_command_block_parser_handles_split_osc_and_exit_code() -> None:
    events: list[dict] = []
    parser = CommandBlockParser("s1", on_event=events.append, clock=lambda: 10.0)
    parser.feed(b"\x1b]133;A\x07", seq=0)
    parser.feed(b"\x1b]133;B\x07", seq=1)
    parser.feed(b"\x1b]633;E;echo hi\x07", seq=2)
    parser.feed(b"\x1b]133;C\x07", seq=3)
    parser.feed(b"\x1b]13", seq=4)  # split introducer
    parser.feed(b"3;D;7\x07", seq=5)
    finished = [event for event in events if event.get("phase") == "finished"]
    assert len(finished) == 1
    assert finished[0]["command"] == "echo hi"
    assert finished[0]["exit_code"] == 7
    assert finished[0]["status"] == "failed"
    assert parser.blocks[0].duration_ms == 0


def test_command_block_parser_ignores_malformed_and_oversized_osc() -> None:
    parser = CommandBlockParser("s1")
    parser.feed(b"\x1b]133;D;not-an-int\x07")
    parser.feed(b"\x1b]633;P;Cwd=/tmp\x07")
    assert parser.blocks and parser.blocks[0].exit_code is None
    parser.feed(b"x" * 100_000)
    assert len(parser._buffer) <= 64 * 1024
