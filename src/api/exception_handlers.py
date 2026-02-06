"""
Global exception handlers for consistent error responses.

Implements [api-hardening:FR-011] - Error response consistency
Implements [api-hardening:FR-009] - Request validation with Pydantic
Implements [api-hardening:NFR-002] - No stack traces exposed in production

Implements test scenarios:
- [api-hardening:GlobalExceptionHandler/TS-01] Pydantic validation error
- [api-hardening:GlobalExceptionHandler/TS-02] Unhandled exception
- [api-hardening:GlobalExceptionHandler/TS-03] HTTP exception
- [api-hardening:GlobalExceptionHandler/TS-04] Error includes request ID
"""

import os
import traceback

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api.middleware.request_id import get_request_id

logger = structlog.get_logger(__name__)


def is_production_mode() -> bool:
    """Check if running in production mode."""
    env = os.getenv("ENVIRONMENT", "development").lower()
    return env in ("production", "prod")


def register_exception_handlers(app: FastAPI) -> None:
    """
    Register global exception handlers on the FastAPI app.

    Args:
        app: The FastAPI application.
    """

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """
        Handle Pydantic validation errors.

        Implements [api-hardening:GlobalExceptionHandler/TS-01]
        """
        # Extract field-level errors
        field_errors = []
        for error in exc.errors():
            loc = ".".join(str(part) for part in error["loc"])
            field_errors.append(
                {
                    "field": loc,
                    "message": error["msg"],
                    "type": error["type"],
                }
            )

        logger.warning(
            "Request validation error",
            path=request.url.path,
            errors=field_errors,
        )

        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed",
                    "details": {
                        "errors": field_errors,
                    },
                }
            },
            headers=_get_error_headers(request),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """
        Handle HTTP exceptions (404, 401, etc.).

        Implements [api-hardening:GlobalExceptionHandler/TS-03]
        """
        # Check if detail is already in our error format
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            error_content = exc.detail
        else:
            # Convert to standard format
            error_code = _status_to_code(exc.status_code)
            error_content = {
                "error": {
                    "code": error_code,
                    "message": str(exc.detail) if exc.detail else _status_to_message(exc.status_code),
                }
            }

        if exc.status_code >= 500:
            logger.error(
                "HTTP error",
                status_code=exc.status_code,
                path=request.url.path,
                detail=exc.detail,
            )
        else:
            logger.warning(
                "HTTP error",
                status_code=exc.status_code,
                path=request.url.path,
            )

        return JSONResponse(
            status_code=exc.status_code,
            content=error_content,
            headers=_get_error_headers(request),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """
        Handle all unhandled exceptions.

        Implements [api-hardening:GlobalExceptionHandler/TS-02]

        Never exposes stack traces in production.
        """
        logger.error(
            "Unhandled exception",
            path=request.url.path,
            method=request.method,
            error=str(exc),
            exc_info=exc,
        )

        # Build error response
        error_content = {
            "error": {
                "code": "internal_error",
                "message": "An internal error occurred. Please try again later.",
            }
        }

        # Add details only in development mode
        if not is_production_mode():
            error_content["error"]["details"] = {
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
                "traceback": traceback.format_exc().split("\n"),
            }

        return JSONResponse(
            status_code=500,
            content=error_content,
            headers=_get_error_headers(request),
        )


def _get_error_headers(request: Request) -> dict[str, str]:
    """Get headers to include in error responses.

    Args:
        request: The request object containing request_id in state.
    """
    headers = {}
    # Try to get request ID from request state first (more reliable during exceptions)
    request_id = getattr(request.state, "request_id", None)
    # Fall back to context variable
    if not request_id:
        request_id = get_request_id()
    if request_id:
        headers["X-Request-ID"] = request_id
    return headers


def _status_to_code(status_code: int) -> str:
    """Convert HTTP status code to error code."""
    codes = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        422: "validation_error",
        429: "rate_limited",
        500: "internal_error",
        502: "bad_gateway",
        503: "service_unavailable",
    }
    return codes.get(status_code, f"error_{status_code}")


def _status_to_message(status_code: int) -> str:
    """Convert HTTP status code to default message."""
    messages = {
        400: "Bad request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not found",
        405: "Method not allowed",
        409: "Conflict",
        422: "Unprocessable entity",
        429: "Too many requests",
        500: "Internal server error",
        502: "Bad gateway",
        503: "Service unavailable",
    }
    return messages.get(status_code, f"Error {status_code}")
