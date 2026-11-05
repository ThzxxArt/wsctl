"""RFC 9457 Problem Details for every HTTP error.

0.1.x returned ``{"detail": "..."}`` for everything: a validation error, a
missing session and a permission failure were distinguishable only by the
status code. That is enough for a human clicking around and not enough for a
client: there was no stable ``type`` to branch on and no request id to quote in
a bug report.

This module installs three handlers:

* ``HTTPException`` -- the shape every route already raises;
* ``RequestValidationError`` -- 422 with the offending fields;
* unhandled exceptions -- 500 with a request id, never a traceback.

``detail`` is preserved verbatim, so a 0.1.x script that reads it keeps working.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("wsctl.problems")

PROBLEM_JSON = "application/problem+json"

_TITLES = {
    400: "请求无效",
    401: "需要登录",
    403: "没有权限",
    404: "资源不存在",
    405: "方法不允许",
    409: "状态冲突",
    410: "资源已移除",
    413: "请求体过大",
    415: "不支持的媒体类型",
    422: "请求内容无法处理",
    429: "请求过于频繁",
    500: "服务器内部错误",
    503: "服务暂不可用",
}


def _request_id(request: Request) -> str | None:
    try:
        from ..core.observability import request_id_var

        return request_id_var.get() or None
    except Exception:  # pragma: no cover - optional import
        return None


def problem(
    request: Request,
    *,
    status: int,
    detail: str,
    title: str | None = None,
    type_: str = "about:blank",
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": type_,
        "title": title or _TITLES.get(status, "请求失败"),
        "status": status,
        "detail": detail,
        "instance": request.url.path,
    }
    request_id = _request_id(request)
    if request_id:
        body["request_id"] = request_id
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


def install_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        if not isinstance(detail, str):
            detail = str(detail)
        headers = dict(getattr(exc, "headers", None) or {})
        return problem(
            request,
            status=exc.status_code,
            detail=detail,
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
            fields.append({"field": location, "message": error.get("msg", "无效")})
        return problem(
            request,
            status=422,
            detail="请求参数校验失败",
            extra={"errors": fields},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error serving %s", request.url.path)
        return problem(
            request,
            status=500,
            detail="服务器内部错误；请带上 request_id 反馈",
            type_="https://wsctl.dev/problems/internal",
        )


__all__ = ["PROBLEM_JSON", "install_problem_handlers", "problem"]
