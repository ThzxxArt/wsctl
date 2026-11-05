from __future__ import annotations

import pytest

from wsctl.core.protocol import decode_data, encode_data
from wsctl.core.scrollback import Scrollback


@pytest.mark.perf
def test_protocol_codec_benchmark(benchmark) -> None:  # type: ignore[no-untyped-def]
    payload = b"x" * 4096
    benchmark(lambda: decode_data(encode_data(1234, payload)))


@pytest.mark.perf
def test_scrollback_append_benchmark(benchmark) -> None:  # type: ignore[no-untyped-def]
    ring = Scrollback(max_bytes=8 * 1024 * 1024)
    chunk = b"a" * 1024
    benchmark(lambda: [ring.append(chunk) for _ in range(256)])
