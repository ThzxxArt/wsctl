"""Web Push notifications (opt-in, VAPID).

Notifications tell a user that a command finished or a session went quiet while
they were in another tab. They are **never** written into the terminal stream:
that would corrupt a screen replay, which is the same rule 0.1.30 established
for banners.

The feature is opt-in and requires a VAPID key pair. Without one, the API
reports ``enabled=false`` and subscription attempts are refused with an
explanation -- a button that silently does nothing would be worse.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from wsctl.core.database import Database

log = logging.getLogger("wsctl.push")

#: A user needs a handful of browsers/devices, not an unbounded table. Every
#: notify then fans out to all of them.
MAX_SUBSCRIPTIONS = 20


def _endpoint_allowed(endpoint: str) -> bool:
    """Restrict push endpoints to public HTTPS origins.

    The server makes an outbound POST to whatever URL is stored here. Allowing
    ``http://`` made the subscription API a cleartext request forwarder, and
    allowing loopback/private addresses made it a blind SSRF primitive against
    the host's own services. DNS that resolves to a private address later is
    still possible; pywebpush has no resolver hook, so the literal-IP and
    obvious local-name checks are the boundary we can enforce at subscribe
    time.
    """
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit(endpoint)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    host = parsed.hostname.lower().rstrip(".")
    if host in ("localhost",) or host.endswith(".localhost"):
        return False
    if host.endswith(".local") or host.endswith(".internal"):
        return False
    import ipaddress

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return address.is_global


class PushService:
    def __init__(self, db: Database, settings: Any) -> None:
        self._db = db
        self._settings = settings

    @property
    def enabled(self) -> bool:
        return bool(
            getattr(self._settings, "web_push_enabled", False)
            and getattr(self._settings, "web_push_vapid_public", "")
            and getattr(self._settings, "web_push_vapid_private", "")
        )

    def public_key(self) -> str:
        return str(getattr(self._settings, "web_push_vapid_public", "") or "")

    def subscribe(self, user_id: int, subscription: dict[str, Any]) -> dict[str, Any]:
        endpoint = subscription.get("endpoint")
        keys = subscription.get("keys") or {}
        if not isinstance(endpoint, str) or not _endpoint_allowed(endpoint):
            raise ValueError("无效的 push endpoint（仅支持公网 https 地址）")
        if not isinstance(keys, dict):
            raise ValueError("无效的 push keys")
        p256dh = keys.get("p256dh")
        auth = keys.get("auth")
        if not isinstance(p256dh, str) or not p256dh or not isinstance(auth, str) or not auth:
            raise ValueError("push keys 缺少 p256dh/auth")
        existing = self._db.push_list(user_id)
        # Endpoint is globally unique. Reassigning another user's row from a
        # guessable endpoint let one account silently steal/mute another's
        # notifications; require the owner to unsubscribe first.
        record = self._db.push_find(endpoint)
        if record is not None and int(record["user_id"]) != user_id:
            raise ValueError("该推送 endpoint 已被其他用户订阅")
        if len(existing) >= MAX_SUBSCRIPTIONS and not any(
            item["endpoint"] == endpoint for item in existing
        ):
            raise ValueError(f"每个用户最多 {MAX_SUBSCRIPTIONS} 个推送订阅")
        return self._db.push_subscribe(user_id, endpoint, keys)

    def unsubscribe(self, user_id: int, endpoint: str) -> bool:
        return self._db.push_unsubscribe(endpoint, user_id=user_id)

    async def notify(
        self, user_id: int, payload: dict[str, Any], *, ttl: int = 60
    ) -> dict[str, int]:
        """Send to every subscription of ``user_id``.

        A dead endpoint (404/410) is removed; any other failure is logged and
        counted. Push is best-effort by nature: a notification is not a
        transaction, and a failed one must never affect the terminal.
        """
        if not self.enabled:
            return {"sent": 0, "failed": 0, "removed": 0}
        try:
            from pywebpush import webpush  # type: ignore[import-untyped]
        except ImportError:  # pragma: no cover - optional dependency
            log.warning("pywebpush 未安装，Web Push 已禁用")
            return {"sent": 0, "failed": 0, "removed": 0}
        sent = failed = removed = 0
        for sub in await asyncio.to_thread(self._db.push_list, user_id):
            info = {"endpoint": sub["endpoint"], "keys": sub["keys"]}
            try:
                await asyncio.to_thread(
                    webpush,
                    subscription_info=info,
                    data=json.dumps(payload, ensure_ascii=False),
                    vapid_private_key=self._settings.web_push_vapid_private,
                    vapid_claims={"sub": self._settings.web_push_subject},
                    ttl=ttl,
                )
                sent += 1
            except Exception as exc:  # WebPushException and transport errors
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in (404, 410):
                    await asyncio.to_thread(
                        self._db.push_unsubscribe, sub["endpoint"], user_id=user_id
                    )
                    removed += 1
                else:
                    failed += 1
                    log.warning("push notification failed: %s", exc)
        return {"sent": sent, "failed": failed, "removed": removed}


__all__ = ["PushService"]
