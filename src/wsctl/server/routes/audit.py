"""Audit log query (admin only)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from wsctl.core.store import User

from ..deps import require_admin
from ..pagination import decode_cursor, encode_cursor

router = APIRouter(tags=["audit"])


@router.get("/audit")
async def get_audit(
    request: Request,
    response: Response,
    _: User = Depends(require_admin),
    limit: int = 100,
    offset: int = 0,
    event: str | None = None,
    user_id: int | None = None,
    ip: str | None = None,
    cursor: str | None = None,
) -> list[dict[str, Any]]:
    # Flush the async writer so the read reflects events just produced.
    await request.app.state.audit.flush()
    before_id: int | None = None
    if cursor:
        try:
            before_id = int(decode_cursor(cursor)["id"])
        except (ValueError, KeyError, TypeError):
            raise HTTPException(status_code=400, detail="游标无效") from None
    store_ = request.app.state.store
    page_size = min(max(limit, 1), 1000)
    rows = await asyncio.to_thread(
        store_.recent_audit,
        page_size,
        offset=max(offset, 0),
        event=event,
        user_id=user_id,
        ip=ip,
        before_id=before_id,
    )
    if len(rows) == page_size and rows:
        response.headers["X-Next-Cursor"] = encode_cursor({"id": int(rows[-1]["id"])})
    return [dict(row) for row in rows]
