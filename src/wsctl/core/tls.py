"""Self-signed TLS material, generated once and reused.

Serving over HTTPS is what makes an origin a **secure context**, and a whole
class of browser APIs only exists there: ``navigator.clipboard`` (so copy and
paste), the Notification API (so 后台动静可见), Service Workers, and more.
Reached over plain HTTP at a LAN address, ``navigator.clipboard`` is not
*denied* -- it is absent, and the page cannot even tell you so.

The fix is HTTPS, and the obstacle is that HTTPS normally means learning
``openssl``. This module removes the obstacle: point wsctl at a directory and
it makes a certificate for every name the machine is plausibly reached by,
then keeps reusing it so the browser's one-time trust decision stays made.

Two backends, in order of preference:

* ``cryptography`` when it is importable -- no external process, and this is
  the only path that works on a Windows box with no ``openssl`` on PATH;
* the ``openssl`` command line otherwise -- present on essentially every
  Linux and macOS host.
"""

from __future__ import annotations

import contextlib
import datetime
import ipaddress
import json
import logging
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

log = logging.getLogger("wsctl.tls")

CERT_NAME = "cert.pem"
KEY_NAME = "key.pem"
META_NAME = "cert.meta.json"
#: How long a generated certificate is valid for.
DEFAULT_DAYS = 365
#: Renew this long before expiry, so a long-running instance never serves a
#: certificate that lapses underneath it.
RENEW_MARGIN_SECONDS = 14 * 24 * 3600


class TlsError(RuntimeError):
    """Self-signed material could not be produced or reused."""


def local_names(*, extra: object = None) -> list[str]:
    """Every name and address this machine is plausibly reached by.

    A certificate that omits the address in the URL produces a *second*
    browser warning (name mismatch) on top of the untrusted-issuer one, so the
    list errs towards inclusion. ``extra`` may be a single name or an iterable,
    used for the ``--host`` the operator actually bound.
    """
    names: set[str] = {"localhost", "127.0.0.1", "::1"}
    with contextlib.suppress(OSError):
        names.add(socket.gethostname())
    with contextlib.suppress(OSError):
        names.add(socket.getfqdn())
    with contextlib.suppress(OSError):
        for info in socket.getaddrinfo(socket.gethostname(), None):
            names.add(str(info[4][0]))
    if extra is not None:
        candidates = extra if isinstance(extra, (list, tuple, set, frozenset)) else [extra]
        for candidate in candidates:
            names.add(str(candidate).strip())
    # ``0.0.0.0`` is a bind address, not somewhere a browser can go. Filtered at
    # the *end* rather than only on ``extra``: a container whose hostname
    # resolves to ``0.0.0.0`` (a common ``/etc/hosts`` setup) would otherwise
    # sneak it in through ``getaddrinfo``, producing a certificate that looks
    # like it covers every address and in fact covers none.
    return sorted(n for n in names if n and n not in {"0.0.0.0", "::"})


def split_names(names: list[str]) -> tuple[list[str], list[str]]:
    """Separate DNS names from IP addresses; a SAN entry must say which it is."""
    dns: list[str] = []
    ips: list[str] = []
    for name in names:
        try:
            ipaddress.ip_address(name)
        except ValueError:
            dns.append(name)
        else:
            ips.append(name)
    return dns, ips


def _write_private(path: Path, data: bytes) -> None:
    """Create a file that is private *from the moment it exists*.

    Written with ``O_EXCL`` and mode ``0600`` so there is no window in which
    the private key is readable by anyone else -- the same rule the credentials
    file already follows.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    except BaseException:
        with contextlib.suppress(OSError):
            path.unlink()
        raise


def _generate_with_cryptography(
    cert_path: Path, key_path: Path, names: list[str], days: int
) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    dns, ips = split_names(names)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "wsctl")])
    alt: list[x509.GeneralName] = [x509.DNSName(name) for name in dns]
    alt += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(hours=1))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(alt), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    _write_private(
        key_path,
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ),
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _openssl_detail(proc: subprocess.CompletedProcess[bytes]) -> str:
    detail = proc.stderr or proc.stdout or b""
    return detail.decode("utf-8", "replace").strip()[:400]


def _generate_with_openssl(
    cert_path: Path, key_path: Path, names: list[str], days: int
) -> None:
    dns, ips = split_names(names)
    san = ",".join([f"DNS:{n}" for n in dns] + [f"IP:{n}" for n in ips])

    # The key is produced on *stdout* and written by :func:`_write_private` --
    # never by openssl. ``openssl req -newkey`` creates the key itself with the
    # process umask, which on a default ``022`` is world-readable for the whole
    # duration of the call, and a private key is not something to leave to a
    # umask. (The ``cryptography`` backend has no such window at all.)
    gen = subprocess.run(
        ["openssl", "genrsa", "2048"], capture_output=True, check=False
    )
    if gen.returncode != 0:
        raise TlsError("openssl 未能生成私钥：" + _openssl_detail(gen))
    _write_private(key_path, gen.stdout)

    cmd = [
        "openssl", "req", "-x509", "-key", str(key_path), "-out", str(cert_path),
        "-days", str(days), "-subj", "/CN=wsctl",
        "-addext", f"subjectAltName={san}",
    ]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        raise TlsError("openssl 未能生成自签证书：" + _openssl_detail(proc))


def _backend() -> str:
    try:
        import cryptography  # noqa: F401
    except Exception:
        if shutil.which("openssl"):
            return "openssl"
        raise TlsError(
            "生成自签证书需要 cryptography 或 openssl，两者都没有找到。"
            "安装其一（pip install 'cryptography>=42'），"
            "或自己提供证书：--ssl-cert / --ssl-key。"
        ) from None
    return "cryptography"


def ensure_self_signed(
    directory: Path, names: list[str], *, days: int = DEFAULT_DAYS
) -> tuple[Path, Path]:
    """Return ``(cert, key)`` under ``directory``, generating them if needed.

    Material is reused while it is valid, not near expiry, and covers exactly
    the names asked for. Regenerating on every start would make the browser
    ask the operator to trust it again every time, which is worse than the
    HTTP problem this solves.
    """
    cert_path = directory / CERT_NAME
    key_path = directory / KEY_NAME
    meta_path = directory / META_NAME
    wanted = sorted(set(names))

    if cert_path.is_file() and key_path.is_file() and meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        same_names = meta.get("names") == wanted
        age = time.time() - float(meta.get("created", 0) or 0)
        left = float(meta.get("days", 0) or 0) * 86400 - age
        if same_names and left > RENEW_MARGIN_SECONDS:
            log.debug("reusing self-signed certificate at %s", cert_path)
            return cert_path, key_path
        log.info(
            "regenerating self-signed certificate (%s)",
            "names changed" if not same_names else "nearing expiry",
        )

    directory.mkdir(parents=True, exist_ok=True)
    for stale in (cert_path, key_path):
        with contextlib.suppress(OSError):
            stale.unlink()

    backend = _backend()
    try:
        if backend == "cryptography":
            _generate_with_cryptography(cert_path, key_path, wanted, days)
        else:
            _generate_with_openssl(cert_path, key_path, wanted, days)
    except TlsError:
        raise
    except Exception as exc:
        raise TlsError(f"生成自签证书失败：{exc}") from exc

    meta_path.write_text(
        json.dumps(
            {"names": wanted, "created": time.time(), "days": days, "backend": backend},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    log.info("generated a self-signed certificate for %s", ", ".join(wanted))
    return cert_path, key_path


__all__ = [
    "DEFAULT_DAYS",
    "RENEW_MARGIN_SECONDS",
    "TlsError",
    "ensure_self_signed",
    "local_names",
    "split_names",
]
