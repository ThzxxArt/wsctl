"""Networking helpers for graceful restarts and proxy-aware local calls."""

from __future__ import annotations

import ipaddress
import socket
import urllib.request
from typing import Any
from urllib.parse import urlparse


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


def is_loopback(url: str) -> bool:
    """Whether ``url`` targets this machine (an IP in 127.0.0.0/8, ::1, …)."""
    host = urlparse(url).hostname or ""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def opener_for(url: str, *, ssl_context: Any = None) -> urllib.request.OpenerDirector:
    """An opener that never routes a local target through a proxy.

    ``urllib`` honours ``http_proxy``/``https_proxy`` unconditionally, so a
    shell that happens to export a proxy (WSL, corporate VPN, a Clash-style
    local proxy) sends even ``http://127.0.0.1:7682/healthz`` to the proxy,
    which answers ``502`` and makes a perfectly healthy local wsctl look dead.
    Anything on this machine goes direct; remote targets keep the proxy.

    ``ssl_context`` is forwarded for callers that probe a self-signed cert.
    """
    handlers: list[Any] = []
    if ssl_context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=ssl_context))
    if is_loopback(url):
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)
