"""Networking helpers for graceful restarts."""

from __future__ import annotations

import socket


def make_reuse_socket(host: str, port: int) -> socket.socket:
    """Create a listening socket with ``SO_REUSEPORT`` where supported.

    Two processes can then bind the same port, so a new instance can start
    accepting connections before the old one exits — a restart without a
    "connection refused" window.
    """
    if not hasattr(socket, "SO_REUSEPORT"):
        raise OSError("SO_REUSEPORT is not supported on this platform")
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE)
    family, socktype, proto, _, sockaddr = infos[0]
    sock = socket.socket(family, socktype, proto)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Unlike SO_REUSEADDR this is not best-effort: the caller explicitly
        # asked for a zero-downtime handover, so a silent downgrade would be a
        # lie. Let a failure propagate.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.bind(sockaddr)
        sock.listen(2048)
        sock.set_inheritable(True)
    except BaseException:
        sock.close()
        raise
    return sock
