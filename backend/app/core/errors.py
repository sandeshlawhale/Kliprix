"""Application errors and the single handler set that renders the documented error shape.

Shape (docs/07-api-contract.md): `{"error": {"code", "message", "details"}}`.
Messages never contain keys, tokens, or full file-system paths.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    INVALID_URL = "INVALID_URL"
    UNSUPPORTED_HOST = "UNSUPPORTED_HOST"
    TOO_MANY_VIDEOS = "TOO_MANY_VIDEOS"
    VIDEO_PRIVATE = "VIDEO_PRIVATE"
    VIDEO_AGE_RESTRICTED = "VIDEO_AGE_RESTRICTED"
    VIDEO_UNAVAILABLE = "VIDEO_UNAVAILABLE"
    GEO_BLOCKED = "GEO_BLOCKED"
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
    DOWNLOADER_OUTDATED = "DOWNLOADER_OUTDATED"
    FFMPEG_MISSING = "FFMPEG_MISSING"
    LLM_NOT_CONFIGURED = "LLM_NOT_CONFIGURED"
    LLM_AUTH_FAILED = "LLM_AUTH_FAILED"
    LLM_RATE_LIMITED = "LLM_RATE_LIMITED"
    LLM_BAD_RESPONSE = "LLM_BAD_RESPONSE"
    TRANSCRIBE_FAILED = "TRANSCRIBE_FAILED"
    MODEL_DOWNLOAD_FAILED = "MODEL_DOWNLOAD_FAILED"
    RENDER_FAILED = "RENDER_FAILED"
    DISK_FULL = "DISK_FULL"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    CONFLICT = "CONFLICT"
    INTERNAL = "INTERNAL"


# HTTP status per code (AA-011): client mistakes 4xx, upstream failures 502, local failures 5xx.
HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_URL: 400,
    ErrorCode.UNSUPPORTED_HOST: 400,
    ErrorCode.TOO_MANY_VIDEOS: 400,
    ErrorCode.VIDEO_PRIVATE: 422,
    ErrorCode.VIDEO_AGE_RESTRICTED: 422,
    ErrorCode.VIDEO_UNAVAILABLE: 422,
    ErrorCode.GEO_BLOCKED: 422,
    ErrorCode.DOWNLOAD_FAILED: 502,
    ErrorCode.DOWNLOADER_OUTDATED: 502,
    ErrorCode.FFMPEG_MISSING: 503,
    ErrorCode.LLM_NOT_CONFIGURED: 409,
    ErrorCode.LLM_AUTH_FAILED: 401,
    ErrorCode.LLM_RATE_LIMITED: 429,
    ErrorCode.LLM_BAD_RESPONSE: 502,
    ErrorCode.TRANSCRIBE_FAILED: 500,
    ErrorCode.MODEL_DOWNLOAD_FAILED: 502,
    ErrorCode.RENDER_FAILED: 500,
    ErrorCode.DISK_FULL: 507,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.CONFLICT: 409,
    ErrorCode.INTERNAL: 500,
}


class AppError(Exception):
    """An expected failure with a documented code and a human-readable message."""

    def __init__(
        self, code: ErrorCode, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    @property
    def status_code(self) -> int:
        return HTTP_STATUS[self.code]


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """The body of every non-2xx response."""

    error: ErrorBody


# Attached to routers so OpenAPI documents the real error shape, not FastAPI's default 422.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    "default": {"model": ErrorResponse, "description": "Error"}
}


def _render(code: ErrorCode, message: str, details: dict[str, Any] | None = None) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details or {}))
    return JSONResponse(status_code=HTTP_STATUS[code], content=body.model_dump(mode="json"))


async def _handle_app_error(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AppError):
        raise exc
    return _render(exc.code, exc.message, exc.details)


async def _handle_validation_error(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise exc
    # Keep only location and message: the rejected input may contain an API key.
    errors = [
        {"loc": [str(part) for part in err.get("loc", ())], "msg": str(err.get("msg", ""))}
        for err in exc.errors()
    ]
    return _render(ErrorCode.VALIDATION_ERROR, "The request is not valid.", {"errors": errors})


async def _handle_http_error(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    if exc.status_code == 404:
        return _render(ErrorCode.NOT_FOUND, "Not found.")
    if exc.status_code == 405:
        return _render(ErrorCode.NOT_FOUND, "This method is not available for this path.")
    if exc.status_code < 500:
        return _render(ErrorCode.VALIDATION_ERROR, "The request is not valid.")
    return _render(ErrorCode.INTERNAL, "Something went wrong in the backend.")


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "unhandled error method=%s path=%s", request.method, request.url.path, exc_info=exc
    )
    return _render(ErrorCode.INTERNAL, "Something went wrong in the backend. See the log file.")


def install_error_handlers(app: FastAPI) -> None:
    """Route every error through the documented error shape."""
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_error)
    app.add_exception_handler(Exception, _handle_unexpected)
