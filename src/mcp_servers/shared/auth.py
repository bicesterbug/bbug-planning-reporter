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
import json
import os

import structlog

logger = structlog.get_logger(__name__)

# Paths exempt from authentication
EXEMPT_PATHS = {"/health"}


class MCPAuthMiddleware:
    """
    Pure ASGI middleware that validates bearer tokens on MCP server endpoints.

    Uses raw ASGI protocol instead of BaseHTTPMiddleware to avoid
    incompatibility with streaming transports (SSE, Streamable HTTP).

    When MCP_API_KEY is set, requires Authorization: Bearer <token> on all
    requests except /health. When MCP_API_KEY is not set, passes all requests
    through (no-op for backward compatibility).
    """

    def __init__(self, app, api_key: str | None = None) -> None:
        self.app = app
        self._api_key = api_key if api_key is not None else os.getenv("MCP_API_KEY")

    @property
    def auth_enabled(self) -> bool:
        """Whether authentication is active."""
        return self._api_key is not None and len(self._api_key) > 0

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if not self.auth_enabled:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        # Extract Authorization header from raw ASGI scope
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode()

        if not auth_header:
            self._log_auth_failure(scope, "Missing Authorization header")
            await self._send_unauthorized(send, "Missing Authorization header")
            return

        token = self._extract_bearer_token(auth_header)
        if token is None:
            self._log_auth_failure(scope, "Invalid auth scheme")
            await self._send_unauthorized(
                send,
                "Invalid Authorization header format. Expected: Bearer <token>",
            )
            return

        if not hmac.compare_digest(token.encode(), self._api_key.encode()):
            self._log_auth_failure(scope, "Invalid token")
            await self._send_unauthorized(send, "Invalid bearer token")
            return

        await self.app(scope, receive, send)

    def _extract_bearer_token(self, auth_header: str) -> str | None:
        """Extract bearer token from Authorization header."""
        parts = auth_header.split(" ", 1)
        if len(parts) != 2:
            return None

        scheme, token = parts
        if scheme.lower() != "bearer":
            return None

        return token.strip()

    def _log_auth_failure(self, scope: dict, reason: str) -> None:
        """Log failed auth attempt at WARNING with client IP."""
        client = scope.get("client")
        client_ip = client[0] if client else "unknown"
        method = scope.get("method", "?")
        path = scope.get("path", "?")
        logger.warning(
            "MCP auth failed",
            reason=reason,
            client_ip=client_ip,
            endpoint=path,
            method=method,
        )

    async def _send_unauthorized(self, send, message: str) -> None:
        """Send a 401 Unauthorized JSON response via raw ASGI."""
        body = json.dumps({
            "error": {
                "code": "unauthorized",
                "message": message,
            }
        }).encode()
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                [b"content-type", b"application/json"],
                [b"content-length", str(len(body)).encode()],
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
        })
