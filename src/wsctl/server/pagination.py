"""Opaque cursor helpers for list endpoints.

Offset pagination silently skips or repeats rows when new records arrive
between pages -- audit is exactly that kind of append-heavy log. A cursor is
the id (or timestamp + id) of the last row the caller saw; the next page asks
for rows strictly older than it. The value is base64url JSON so clients treat
it as opaque and cannot grow a dependency on the internal shape.
"""

from __future__ import annotations

import base64
import json
import math
from typing import Any


def encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_cursor(cursor: str) -> dict[str, Any]:
    if not cursor or len(cursor) > 512:
        raise ValueError("游标无效")
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("游标无效") from exc
    if not isinstance(value, dict):
        raise ValueError("游标无效")

    def finite(item: Any) -> bool:
        if isinstance(item, float):
            return math.isfinite(item)
        if isinstance(item, dict):
            return all(finite(v) for v in item.values())
        if isinstance(item, list):
            return all(finite(v) for v in item)
        return True

    if not finite(value):
        raise ValueError("游标无效")
    return value


__all__ = ["decode_cursor", "encode_cursor"]
