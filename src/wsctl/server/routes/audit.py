"""Audit log query (admin only)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request

from wsctl.core.store import User

from ..deps import require_admin

router = APIRouter(prefix="/api", tags=["audit"])


@router.get("/audit")
async def get_audit(
    request: Request,
    _: User = Depends(require_admin),
    limit: int = 100,
    offset: int = 0,
    event: str | None = None,
    user_id: int | None = None,
    ip: str | None = None,
) -> list[dict[str, Any]]:
    # Flush the async writer so the read reflects events just produced.
    await request.app.state.audit.flush()
    store_ = request.app.state.store
    return await asyncio.to_thread(
        store_.recent_audit,
        min(max(limit, 1), 1000),
        offset=max(offset, 0),
        event=event,
        user_id=user_id,
        ip=ip,
    )
