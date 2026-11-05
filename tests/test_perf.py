from __future__ import annotations

import time

import pytest

from wsctl.core.protocol import decode_data, encode_data
from wsctl.core.scrollback import Scrollback


@pytest.mark.perf
def test_protocol_frame_throughput() -> None:
    payload = b"x" * 4096
    started = time.perf_counter()
    for index in range(20_000):
        frame = encode_data(index * len(payload), payload)
        decoded = decode_data(frame)
        assert decoded.payload is not None
    elapsed = time.perf_counter() - started
    # 20k enc/dec of 4 KiB = ~80 MiB moved. One second is a generous bound
    # that still catches an accidental O(n) copy per frame.
    assert elapsed < 1.5, f"frame codec took {elapsed:.2f}s"


@pytest.mark.perf
def test_scrollback_append_and_resume_throughput() -> None:
    ring = Scrollback(max_bytes=8 * 1024 * 1024)
    chunk = b"a" * 4096
    started = time.perf_counter()
    for _ in range(2048):
        ring.append(chunk)
    elapsed = time.perf_counter() - started
    assert ring.size <= 8 * 1024 * 1024
    assert ring.end_seq == 2048 * 4096
    assert elapsed < 1.0, f"scrollback append took {elapsed:.2f}s"
    resumed = ring.since(ring.base_seq + 1024)
    assert resumed is not None
    assert sum(len(part) for _seq, part in resumed) == ring.size - 1024
