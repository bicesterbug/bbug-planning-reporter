"""
API Version middleware for adding X-API-Version header.

Implements [api-hardening:FR-012] - X-API-Version header in all responses
"""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# API Version (matches the version in main.py)
API_VERSION = "1.0.0"


class APIVersionMiddleware(BaseHTTPMiddleware):
    """
    Middleware that adds X-API-Version header to all responses.
    """

    def __init__(self, app, version: str = API_VERSION) -> None:
        """
        Initialize API version middleware.

        Args:
            app: The ASGI application.
            version: The API version string.
        """
        super().__init__(app)
        self.version = version

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Add X-API-Version header to response."""
        response = await call_next(request)
        response.headers["X-API-Version"] = self.version
        return response
