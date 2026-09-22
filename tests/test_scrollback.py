from __future__ import annotations

import pytest

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
