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


def test_loopback_detection() -> None:
    from wsctl.core.net import is_loopback

    assert is_loopback("http://127.0.0.1:7681")
    assert is_loopback("http://127.0.0.5:1/x")
    assert is_loopback("http://localhost:7682")
    assert is_loopback("http://[::1]:1")
    assert not is_loopback("http://10.0.0.5:7681")
    assert not is_loopback("https://wsctl.example.com")
    assert not is_loopback("not-a-url")


def test_local_targets_bypass_the_proxy(monkeypatch) -> None:
    """A shell that exports http_proxy must not break a local wsctl.

    ``urlopen`` honours the proxy unconditionally, so even
    ``http://127.0.0.1:7682/healthz`` went to the proxy, which answered 502 and
    made a perfectly healthy instance look dead.

    Asserted behaviourally: a bogus proxy is configured and a real local server
    must still answer. Inspecting the opener's handler list instead would only
    restate the implementation.
    """
    import http.server
    import threading

    from wsctl.core.net import opener_for

    class Quiet(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Quiet)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}/healthz"
    try:
        # Nothing is listening on this proxy: any attempt to use it fails.
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
        monkeypatch.setenv("https_proxy", "http://127.0.0.1:9")
        with opener_for(url).open(url, timeout=5) as resp:
            assert resp.status == 200
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_remote_targets_keep_the_proxy(monkeypatch) -> None:
    """A remote wsctl is still allowed to sit behind a proxy."""
    import urllib.request

    from wsctl.core.net import opener_for

    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9")
    opener = opener_for("http://10.0.0.5:7681/healthz")
    proxies = {}
    for handler in opener.handlers:
        if isinstance(handler, urllib.request.ProxyHandler):
            proxies.update(handler.proxies)
    assert proxies, "a non-local target must keep whatever proxy the environment set"
