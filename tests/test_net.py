from __future__ import annotations

import socket

import pytest

from wsctl.core.net import make_reuse_socket


def test_binds_and_listens() -> None:
    sock = make_reuse_socket("127.0.0.1", 0)
    try:
        assert sock.getsockname()[1] > 0
    finally:
        sock.close()


@pytest.mark.skipif(not hasattr(socket, "SO_REUSEPORT"), reason="requires SO_REUSEPORT")
def test_two_bindings_on_same_port() -> None:
    first = make_reuse_socket("127.0.0.1", 0)
    port = first.getsockname()[1]
    second = None
    try:
        second = make_reuse_socket("127.0.0.1", port)
        assert second.getsockname()[1] == port
    finally:
        first.close()
        if second is not None:
            second.close()
