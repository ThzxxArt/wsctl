"""Minimal HTTP client for talking to a running wsctl server.

Uses only the standard library so the CLI stays dependency-free. Credentials
(base URL + bearer token) are cached under the config directory.
"""

from __future__ import annotations

import contextlib
import http.cookiejar
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from wsctl.core.config import default_config_dir
from wsctl.core.net import opener_for


class ApiError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


def credentials_path() -> Path:
    return default_config_dir() / "credentials.json"


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
    path.write_text(json.dumps({"url": url, "token": token}), encoding="utf-8")
    os.chmod(path, 0o600)


def clear_credentials() -> None:
    path = credentials_path()
    if path.exists():
        path.unlink()


class ApiClient:
    """Synchronous JSON client bound to a base URL and optional bearer token."""

    def __init__(self, base_url: str, token: str | None = None, *, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

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
            with opener_for(self.base_url).open(req, timeout=self.timeout) as resp:
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
        return json.loads(payload)

    def download(self, path: str) -> bytes:
        """Fetch a raw (non-JSON) resource such as a recording."""
        headers = {"Accept": "*/*"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(f"{self.base_url}{path}", headers=headers, method="GET")
        try:
            with opener_for(self.base_url).open(req, timeout=self.timeout) as resp:
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
    opener = opener_for(base_url)
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
        opener.open(req, timeout=timeout)
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
