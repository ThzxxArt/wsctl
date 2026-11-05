"""Idempotent retries for create/upload endpoints.

A terminal session is a process and an upload is a file: retrying either after
a network timeout must not create a second one. The contract is the standard
``Idempotency-Key`` header. Keys are stored in SQLite (not process memory)
because the documented rolling-restart mode runs two servers against one data
directory, and a retry may land on the new process.

A key is bound to one ``method + path`` and, when the caller supplies one, one
request hash. Reusing a key for a different request is a 409, not a replay of
the wrong result -- that would be worse than doing the work twice.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse

MAX_KEY_LENGTH = 160

log = logging.getLogger("wsctl.idempotency")


def _db(request: Request) -> Any:
    return request.app.state.db


def _canonical_path(path: str) -> str:
    """The same endpoint under ``/api`` and ``/api/v2`` is one operation.

    Idempotency keys are scoped by path; without this, retrying a create on the
    compatibility alias looked like a different request and made a second
    session.
    """
    for prefix in ("/api/v2", "/api"):
        if path == prefix or path.startswith(prefix + "/"):
            return path[len(prefix) :] or "/"
    return path


def _key(request: Request) -> str | None:
    key = request.headers.get("idempotency-key")
    if key is None:
        return None
    key = key.strip()
    if not key or len(key) > MAX_KEY_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Idempotency-Key 必须为 1–{MAX_KEY_LENGTH} 个字符",
        )
    # Scope by principal. The table is keyed by the header alone, so without
    # this a user who guessed (or deliberately reused) another account's key
    # could have that account's stored response replayed to them -- including
    # a created session's payload. Hashing keeps the stored key within the
    # column bound regardless of the original length.
    principal = getattr(getattr(request.state, "user", None), "id", 0) or 0
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"u{principal}:{digest}"


async def replay(request: Request, *, request_hash: str = "") -> JSONResponse | None:
    """Return the stored response for this key, or ``None`` on first use."""
    key = _key(request)
    if key is None:
        return None
    record = await asyncio.to_thread(
        _db(request).idempotency_get,
        key,
        method=request.method,
        path=_canonical_path(request.url.path),
        request_hash=request_hash,
    )
    if record is None:
        return None
    if record.get("pending"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该 Idempotency-Key 的请求正在处理中，请稍后重试",
            headers={"Retry-After": "1"},
        )
    if record.get("conflict"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(record.get("reason") or "Idempotency-Key 冲突"),
        )
    return JSONResponse(
        record["body"],
        status_code=int(record.get("status") or 200),
        headers={"Idempotent-Replayed": "true"},
    )


async def remember(
    request: Request,
    payload: Any,
    *,
    status_code: int = 200,
    request_hash: str = "",
) -> None:
    """Store a successful response so a retry can be replayed."""
    key = _key(request)
    if key is None:
        return
    try:
        await asyncio.to_thread(
            _db(request).idempotency_put,
            key,
            method=request.method,
            path=_canonical_path(request.url.path),
            status=status_code,
            body=payload,
            request_hash=request_hash,
        )
    except Exception:
        # The operation itself succeeded; failing the response because the
        # replay record could not be written would make the client retry and
        # create a second session/upload. Log and keep the success.
        log.exception("failed to persist Idempotency-Key %s", key)


async def claim(request: Request, *, request_hash: str = "") -> JSONResponse | None:
    """Claim an idempotency key before a side effect, or replay the old answer.

    Returns a response to send when the key is finished/pending, and ``None``
    when the caller now owns the key and must call :func:`remember` on success
    or :func:`abort` on failure.
    """
    key = _key(request)
    if key is None:
        return None
    record = await asyncio.to_thread(
        _db(request).idempotency_claim,
        key,
        method=request.method,
        path=_canonical_path(request.url.path),
        request_hash=request_hash,
    )
    state = record.get("state")
    if state == "done":
        return JSONResponse(
            record["body"],
            status_code=int(record.get("status") or 200),
            headers={"Idempotent-Replayed": "true"},
        )
    if state == "conflict":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(record.get("reason") or "Idempotency-Key 冲突"),
        )
    if state == "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该 Idempotency-Key 的请求正在处理中，请稍后重试",
            headers={"Retry-After": "1"},
        )
    return None


async def abort(request: Request) -> None:
    """Release a claim after the side effect failed.

    A claim left behind would block the same key for two minutes; the caller
    is expected to have made no externally visible change at this point.
    """
    key = _key(request)
    if key is None:
        return
    with contextlib.suppress(Exception):
        await asyncio.to_thread(_db(request).idempotency_abort, key)


__all__ = ["MAX_KEY_LENGTH", "abort", "claim", "remember", "replay"]
