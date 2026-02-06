"""
Authentication middleware for API key validation.

Implements [api-hardening:FR-001] - API key authentication on all endpoints except /health
Implements [api-hardening:FR-002] - Validate API keys against configured list
Implements [api-hardening:NFR-002] - No OWASP vulnerabilities (authentication)

Implements test scenarios:
- [api-hardening:AuthMiddleware/TS-01] Valid API key
- [api-hardening:AuthMiddleware/TS-02] Missing Authorization header
- [api-hardening:AuthMiddleware/TS-03] Invalid API key
- [api-hardening:AuthMiddleware/TS-04] Malformed Authorization header
- [api-hardening:AuthMiddleware/TS-05] Health endpoint bypass
- [api-hardening:AuthMiddleware/TS-06] Revoked API key
"""

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.api.auth.key_validator import APIKeyValidator

logger = structlog.get_logger(__name__)

# Paths that don't require authentication
EXEMPT_PATHS = {
    "/api/v1/health",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
}


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware that validates API keys in the Authorization header.

    Skips validation for health check and documentation endpoints.
    Returns 401 for missing or invalid keys.
    """

    def __init__(self, app, validator: APIKeyValidator | None = None) -> None:
        """
        Initialize auth middleware.

        Args:
            app: The ASGI application.
            validator: Optional API key validator. If not provided, creates one.
        """
        super().__init__(app)
        self.validator = validator or APIKeyValidator()

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Process request and validate API key."""
        # Check if path is exempt from authentication
        if self._is_exempt(request.url.path):
            return await call_next(request)

        # Extract and validate API key
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            logger.warning(
                "Missing Authorization header",
                path=request.url.path,
                method=request.method,
            )
            return self._unauthorized_response("Missing Authorization header")

        # Parse Bearer token
        api_key = self._extract_bearer_token(auth_header)
        if api_key is None:
            logger.warning(
                "Malformed Authorization header",
                path=request.url.path,
                method=request.method,
            )
            return self._unauthorized_response(
                "Invalid Authorization header format. Expected: Bearer <token>"
            )

        # Validate API key
        if not self.validator.validate(api_key):
            logger.warning(
                "Invalid API key",
                path=request.url.path,
                method=request.method,
                key_prefix=api_key[:8] + "..." if len(api_key) > 8 else "***",
            )
            return self._unauthorized_response("Invalid API key")

        # Store validated key in request state for downstream use
        request.state.api_key = api_key

        return await call_next(request)

    def _is_exempt(self, path: str) -> bool:
        """Check if the path is exempt from authentication."""
        # Exact match
        if path in EXEMPT_PATHS:
            return True

        # Check for path prefixes (e.g., /docs/oauth2-redirect)
        return any(path.startswith(exempt_path + "/") for exempt_path in EXEMPT_PATHS)

    def _extract_bearer_token(self, auth_header: str) -> str | None:
        """
        Extract Bearer token from Authorization header.

        Returns None if header is not in "Bearer <token>" format.
        """
        parts = auth_header.split(" ", 1)
        if len(parts) != 2:
            return None

        scheme, token = parts
        if scheme.lower() != "bearer":
            return None

        return token.strip()

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
