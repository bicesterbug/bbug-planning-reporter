"""
Request ID middleware for request tracing.

Implements [api-hardening:FR-013] - Request ID tracking with X-Request-ID header
Implements [api-hardening:NFR-002] - Audit trails (security)

Implements test scenarios:
- [api-hardening:RequestIdMiddleware/TS-01] Auto-generate request ID
- [api-hardening:RequestIdMiddleware/TS-02] Preserve client request ID
- [api-hardening:RequestIdMiddleware/TS-03] Request ID in logs
- [api-hardening:RequestIdMiddleware/TS-04] Unique IDs per request
"""

import uuid
from contextvars import ContextVar

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger(__name__)

# Context variable for request ID (thread-safe)
request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)

# Header name for request ID
REQUEST_ID_HEADER = "X-Request-ID"


def get_request_id() -> str | None:
    """Get the current request ID from context."""
    return request_id_context.get()


class RequestIdMiddleware(BaseHTTPMiddleware):
    """
    Middleware that assigns a unique X-Request-ID to each request.

    If the client provides X-Request-ID, it is preserved.
    The ID is added to response headers and logging context.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Process request and assign/preserve request ID."""
        # Get client-provided ID or generate new one
        request_id = request.headers.get(REQUEST_ID_HEADER)
        if not request_id:
            request_id = str(uuid.uuid4())

        # Store in context for logging
        token = request_id_context.set(request_id)

        try:
            # Store in request state for downstream access
            request.state.request_id = request_id

            # Bind request ID to structlog context for this request
            with structlog.contextvars.bound_contextvars(request_id=request_id):
                # Process the request
                response = await call_next(request)

            # Add request ID to response headers
            response.headers[REQUEST_ID_HEADER] = request_id

            return response
        finally:
            # Reset context variable
            request_id_context.reset(token)
