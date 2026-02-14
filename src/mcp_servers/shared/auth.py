"""
Authentication middleware for MCP servers.

Implements [cycle-route-assessment:FR-011] - Bearer token authentication on all MCP servers
Implements [cycle-route-assessment:NFR-005] - Opt-in via MCP_API_KEY, backward compatible
Implements [cycle-route-assessment:NFR-006] - Constant-time comparison, logging, no bypass

Implements test scenarios:
- [cycle-route-assessment:MCPAuthMiddleware/TS-01] Valid bearer token accepted
- [cycle-route-assessment:MCPAuthMiddleware/TS-02] Missing auth rejected
- [cycle-route-assessment:MCPAuthMiddleware/TS-03] Invalid token rejected
- [cycle-route-assessment:MCPAuthMiddleware/TS-04] Health exempt from auth
- [cycle-route-assessment:MCPAuthMiddleware/TS-05] No-op when key not configured
- [cycle-route-assessment:MCPAuthMiddleware/TS-06] Basic auth scheme rejected
"""

import hmac
import os

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = structlog.get_logger(__name__)

# Paths exempt from authentication
EXEMPT_PATHS = {"/health"}


class MCPAuthMiddleware(BaseHTTPMiddleware):
    """
    Starlette middleware that validates bearer tokens on MCP server endpoints.

    When MCP_API_KEY is set, requires Authorization: Bearer <token> on all
    requests except /health. When MCP_API_KEY is not set, passes all requests
    through (no-op for backward compatibility).
    """

    def __init__(self, app, api_key: str | None = None) -> None:
        """
        Initialize the auth middleware.

        Args:
            app: The ASGI application.
            api_key: The expected bearer token. If None, reads from MCP_API_KEY
                     environment variable. If still None, auth is disabled.
        """
        super().__init__(app)
        self._api_key = api_key if api_key is not None else os.getenv("MCP_API_KEY")

    @property
    def auth_enabled(self) -> bool:
        """Whether authentication is active."""
        return self._api_key is not None and len(self._api_key) > 0

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Process request and validate bearer token if auth is enabled."""
        # [cycle-route-assessment:NFR-005] No-op when key not configured
        if not self.auth_enabled:
            return await call_next(request)

        # [cycle-route-assessment:MCPAuthMiddleware/TS-04] Health exempt
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        # Extract Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            self._log_auth_failure(request, "Missing Authorization header")
            return self._unauthorized_response("Missing Authorization header")

        # Parse Bearer token
        token = self._extract_bearer_token(auth_header)
        if token is None:
            self._log_auth_failure(request, "Invalid auth scheme")
            return self._unauthorized_response(
                "Invalid Authorization header format. Expected: Bearer <token>"
            )

        # [cycle-route-assessment:NFR-006] Constant-time comparison
        if not hmac.compare_digest(token.encode(), self._api_key.encode()):
            self._log_auth_failure(request, "Invalid token")
            return self._unauthorized_response("Invalid bearer token")

        return await call_next(request)

    def _extract_bearer_token(self, auth_header: str) -> str | None:
        """Extract bearer token from Authorization header.

        Returns None if header is not in "Bearer <token>" format.
        """
        parts = auth_header.split(" ", 1)
        if len(parts) != 2:
            return None

        scheme, token = parts
        if scheme.lower() != "bearer":
            return None

        return token.strip()

    def _log_auth_failure(self, request: Request, reason: str) -> None:
        """Log failed auth attempt at WARNING with client IP."""
        # [cycle-route-assessment:NFR-006] Log failed attempts
        client_ip = request.client.host if request.client else "unknown"
        logger.warning(
            "MCP auth failed",
            reason=reason,
            client_ip=client_ip,
            endpoint=request.url.path,
            method=request.method,
        )

    def _unauthorized_response(self, message: str) -> JSONResponse:
        """Create a 401 Unauthorized response."""
        return JSONResponse(
            status_code=401,
            content={
                "error": {
                    "code": "unauthorized",
                    "message": message,
                }
            },
        )
