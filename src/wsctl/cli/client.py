"""Minimal HTTP client for talking to a running wsctl server.

Uses only the standard library so the CLI stays dependency-free. Credentials
(base URL + bearer token) are cached under the config directory.
"""

from __future__ import annotations

import contextlib
import http.cookiejar
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from wsctl.core.config import default_config_dir, load_settings
from wsctl.core.net import opener_for


class ApiError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


def credentials_path() -> Path:
    return default_config_dir() / "credentials.json"


def ca_bundle_for(base_url: str, *, ca_cert: str | Path | None = None) -> Path | None:
    """The certificate to verify ``base_url``'s server against, if we know one.

    Self-signed is the *normal* case for this product -- ``--tls-self-signed``
    exists to make HTTPS one command -- and the CLI is usually talking to an
    instance on this machine whose certificate it can read: the instance
    record names the file. Pinning exactly that certificate is *verification*,
    not a bypass, and it is strictly better than the two alternatives that
    shipped first: failing with ``CERTIFICATE_VERIFY_FAILED`` while ``doctor``
    reports the same server healthy, or quietly not verifying at all.

    ``--ca-cert`` / ``WSCTL_CA_CERT`` cover the remote case, where there is no
    local record to read. With neither, ``None`` means the normal system trust
    store -- never "do not verify".
    """
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme != "https":
        return None
    explicit = ca_cert or os.environ.get("WSCTL_CA_CERT")
    if explicit:
        return Path(str(explicit))
    if parsed.port is None:
        # Nothing to look a record up by, and guessing is exactly the bug this
        # shape avoids: ``resolve_instance`` *falls back* to whatever instance
        # happens to be running, so asking about ``https://host:19999``
        # returned the certificate of a local instance on a different port.
        return None
    with contextlib.suppress(Exception):
        from wsctl.cli import daemon

        settings = load_settings().model_copy(update={"port": parsed.port})
        # ``port_explicit`` is what stops the fallback: this must be the
        # instance on *that* port or nothing.
        instance = daemon.resolve_instance(settings, port_explicit=True).instance
        if instance is not None and instance.ssl_cert:
            return Path(instance.ssl_cert)
    return None


def load_credentials() -> dict[str, Any]:
    path = credentials_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_credentials(url: str, token: str) -> None:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Created 0600 from the start. Writing first and chmod-ing afterwards left
    # a world-readable window -- and a crash in between left the file world-
    # readable *forever* -- with a long-lived session token inside.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"url": url, "token": token}))
    except BaseException:
        with contextlib.suppress(OSError):
            path.unlink()
        raise


def clear_credentials() -> None:
    path = credentials_path()
    if path.exists():
        path.unlink()


class ApiClient:
    """Synchronous JSON client bound to a base URL and optional bearer token."""

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        timeout: float = 15.0,
        ca_cert: str | Path | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        # Verification stays on: the context *adds* the pinned certificate to
        # the system trust store rather than replacing it. See ca_bundle_for.
        bundle = ca_bundle_for(self.base_url, ca_cert=ca_cert)
        self.ssl_context = (
            ssl.create_default_context(cafile=str(bundle)) if bundle is not None else None
        )

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        auth: bool = True,
    ) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=headers, method=method
        )
        try:
            # Not urlopen(): that honours http_proxy and would send even a local
            # wsctl through the proxy, which answers 502 and looks like a dead
            # server. See core.net.opener_for.
            with opener_for(self.base_url, ssl_context=self.ssl_context).open(
                req, timeout=self.timeout
            ) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(detail)
                detail = str(parsed.get("detail", detail))
            except json.JSONDecodeError:
                pass
            raise ApiError(exc.code, detail) from exc
        except urllib.error.URLError as exc:
            raise ApiError(0, f"cannot reach {self.base_url}: {exc.reason}") from exc
        if not payload:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            # A reverse proxy in trouble answers 200 with an HTML error page.
            # Letting the traceback out looked like a wsctl crash.
            raise ApiError(0, "响应不是 JSON（是否被反向代理拦截？）") from exc

    def download(self, path: str) -> bytes:
        """Fetch a raw (non-JSON) resource such as a recording."""
        headers = {"Accept": "*/*"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(f"{self.base_url}{path}", headers=headers, method="GET")
        try:
            with opener_for(self.base_url, ssl_context=self.ssl_context).open(
                req, timeout=self.timeout
            ) as resp:
                data: bytes = resp.read()
                return data
        except urllib.error.HTTPError as exc:
            raise ApiError(exc.code, exc.read().decode("utf-8", "replace")) from exc
        except urllib.error.URLError as exc:
            raise ApiError(0, f"cannot reach {self.base_url}: {exc.reason}") from exc


def login(
    base_url: str,
    username: str,
    password: str,
    *,
    totp: str | None = None,
    timeout: float = 15.0,
) -> str:
    """Authenticate and return the opaque session token from the cookie.

    ``totp`` is required for accounts that have two-factor authentication
    enabled; omitting it for such an account fails with a "一次性验证码" error
    rather than a password error, which is how the caller knows to prompt.
    """
    jar = http.cookiejar.CookieJar()
    bundle = ca_bundle_for(base_url)
    context = (
        ssl.create_default_context(cafile=str(bundle)) if bundle is not None else None
    )
    opener = opener_for(base_url, ssl_context=context)
    opener.add_handler(urllib.request.HTTPCookieProcessor(jar))
    payload: dict[str, Any] = {"username": username, "password": password}
    if totp:
        payload["totp"] = totp
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/login",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener.open(req, timeout=timeout) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        with contextlib.suppress(json.JSONDecodeError):
            detail = str(json.loads(detail).get("detail", detail))
        raise ApiError(exc.code, detail) from exc
    except urllib.error.URLError as exc:
        raise ApiError(0, f"cannot reach {base_url}: {exc.reason}") from exc
    for cookie in jar:
        if cookie.name == "wsctl_session":
            return str(cookie.value)
    raise ApiError(0, "server did not return a session cookie")
