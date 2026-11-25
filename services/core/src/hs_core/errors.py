"""One error shape: {"error": {"code", "message", "request_id"}}."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .logging_setup import request_id_var

log = logging.getLogger("hs_core.errors")

_CODES = {
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    429: "rate_limited",
}
_MSG = {404: "Not found", 405: "Method not allowed"}


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None):
        self.status, self.code, self.message, self.headers = status, code, message, headers
        super().__init__(message)


def body(code: str, message: str, **extra) -> dict:
    return {
        "error": {"code": code, "message": message, "request_id": request_id_var.get(), **extra}
    }


def install(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app(_: Request, e: AppError):
        return JSONResponse(body(e.code, e.message), e.status, headers=e.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException):
        code = _CODES.get(e.status_code, "http_error")
        msg = _MSG.get(e.status_code) or (e.detail if isinstance(e.detail, str) else code)
        return JSONResponse(body(code, msg), e.status_code, headers=getattr(e, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _val(_: Request, e: RequestValidationError):
        fields = [".".join(str(p) for p in err["loc"]) for err in e.errors()]
        return JSONResponse(body("validation_error", "Invalid request", fields=fields), 422)

    @app.exception_handler(Exception)
    async def _any(_: Request, e: Exception):
        log.error("unhandled exception", exc_info=e)
        return JSONResponse(body("internal_error", "Internal server error"), 500)
