"""Self-signed TLS material: generated once, reused, and private.

Serving over HTTPS is what makes an origin a *secure context*, and a whole
class of browser APIs exists only there -- the Clipboard API (so copy and
paste) and the Notification API (so 后台动静可见) among them. Reached over
plain HTTP at a LAN address, ``navigator.clipboard`` is not denied, it is
absent. ``--tls-self-signed`` exists to make HTTPS one command, so these tests
are about the properties an operator relies on: the certificate covers the
addresses they actually use, it is not regenerated on every start (which would
make the browser ask for trust again every time), and the private key is never
readable by anyone else.
"""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from wsctl.core import tls

HAVE_OPENSSL = shutil.which("openssl") is not None


def _hide_cryptography(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``import cryptography`` fail, so the openssl path is exercised.

    Setting the module entry to ``None`` is the documented way to make an
    import raise ImportError; deleting it is not, because it is importable
    again straight away.
    """
    monkeypatch.setitem(sys.modules, "cryptography", None)


def test_only_a_name_or_address_becomes_a_san_entry() -> None:
    """A SAN entry must say whether it is a DNS name or an IP literal."""
    dns, ips = tls.split_names(["localhost", "127.0.0.1", "host.example", "::1"])
    assert dns == ["localhost", "host.example"]
    assert ips == ["127.0.0.1", "::1"]


def test_a_bind_address_is_never_put_in_the_certificate() -> None:
    """``0.0.0.0`` is where a server listens, not somewhere a browser can go.

    Including it produces a certificate that covers no reachable address while
    looking like it covers every address.
    """
    names = tls.local_names(extra=["0.0.0.0", "::", "192.168.7.7"])
    assert "0.0.0.0" not in names
    assert "::" not in names
    assert "192.168.7.7" in names
    assert "localhost" in names and "127.0.0.1" in names


def _openssl_san(cert: Path) -> str:
    out = subprocess.run(
        ["openssl", "x509", "-in", str(cert), "-noout", "-ext", "subjectAltName"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out


@pytest.mark.skipif(not HAVE_OPENSSL, reason="openssl not installed")
def test_openssl_backend_covers_every_name_it_was_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hide_cryptography(monkeypatch)
    assert tls._backend() == "openssl"
    cert, _key = tls.ensure_self_signed(tmp_path, ["localhost", "10.1.2.3"])
    san = _openssl_san(cert)
    assert "DNS:localhost" in san
    assert "IP Address:10.1.2.3" in san


def test_cryptography_backend_covers_every_name_it_was_given(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    cert, _key = tls.ensure_self_signed(tmp_path, ["localhost", "10.1.2.3"])
    # Read back with the library that wrote it, so this asserts the content and
    # not a round trip through the same external tool.
    from cryptography import x509

    parsed = x509.load_pem_x509_certificate(cert.read_bytes())
    names = parsed.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "localhost" in names.get_values_for_type(x509.DNSName)
    assert any(str(ip) == "10.1.2.3" for ip in names.get_values_for_type(x509.IPAddress))


def test_the_private_key_is_never_group_or_world_readable(tmp_path: Path) -> None:
    _cert, key = tls.ensure_self_signed(tmp_path, ["localhost"])
    mode = stat.S_IMODE(key.stat().st_mode)
    assert mode & 0o077 == 0, f"private key is {oct(mode)}: readable beyond its owner"


def test_a_second_start_reuses_the_certificate_instead_of_renewing_trust(tmp_path: Path) -> None:
    """Regenerating per start would make the browser ask for trust every time.

    That is worse than the HTTP problem the certificate exists to solve, so
    reuse is the contract, not an optimisation.
    """
    cert, key = tls.ensure_self_signed(tmp_path, ["localhost", "10.0.0.1"])
    before = (cert.read_bytes(), key.read_bytes(), cert.stat().st_mtime_ns)
    cert2, key2 = tls.ensure_self_signed(tmp_path, ["localhost", "10.0.0.1"])
    assert (cert2.read_bytes(), key2.read_bytes(), cert2.stat().st_mtime_ns) == before


def test_a_changed_address_set_regenerates_because_the_san_would_be_wrong(tmp_path: Path) -> None:
    cert, _key = tls.ensure_self_signed(tmp_path, ["localhost", "10.0.0.1"])
    before = cert.read_bytes()
    cert2, _key2 = tls.ensure_self_signed(tmp_path, ["localhost", "10.9.9.9"])
    assert cert2.read_bytes() != before, (
        "a certificate that does not cover the new address produces a second "
        "browser warning on top of the untrusted-issuer one"
    )


def test_an_expiring_certificate_is_replaced_rather_than_served(tmp_path: Path) -> None:
    cert, _key = tls.ensure_self_signed(tmp_path, ["localhost"])
    meta_path = tmp_path / tls.META_NAME
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    # Backdate it to just inside the renew margin.
    meta["created"] = meta["created"] - (meta["days"] * 86400) + (tls.RENEW_MARGIN_SECONDS / 2)
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    before = cert.read_bytes()
    cert2, _key2 = tls.ensure_self_signed(tmp_path, ["localhost"])
    assert cert2.read_bytes() != before, "a certificate near expiry must be replaced"


def test_no_backend_at_all_says_what_to_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hide_cryptography(monkeypatch)
    monkeypatch.setattr(tls.shutil, "which", lambda _name: None)
    with pytest.raises(tls.TlsError) as excinfo:
        tls.ensure_self_signed(tmp_path, ["localhost"])
    message = str(excinfo.value)
    assert "cryptography" in message and "openssl" in message
    assert "--ssl-cert" in message, "an operator needs to be told the way out"


def test_the_advertised_one_command_https_does_not_need_a_second_one() -> None:
    """``--tls-self-signed`` is promised as one command; it must not need two.

    ``cryptography`` lived in the ``dev`` extra only, so a plain
    ``pip install wsctl`` had neither it nor -- on Windows -- an ``openssl`` on
    PATH. Both backends missing, and HTTPS refused to start on exactly the
    machines the feature was written for. It is a normal dependency now, and
    this pins it: moving it back into an extra would silently restore the
    broken install, and nothing else in the suite would notice.
    """
    import tomllib

    root = Path(__file__).resolve().parent.parent
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    names = {d.split(">=")[0].split("[")[0].strip().lower() for d in deps}
    assert "cryptography" in names, (
        "cryptography must be a base dependency: without it, and without "
        "openssl on PATH (Windows), --tls-self-signed can generate nothing "
        "and the server refuses to start"
    )
