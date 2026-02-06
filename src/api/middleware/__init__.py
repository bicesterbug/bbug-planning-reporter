# API Middleware
"""
Middleware components for API hardening.

Implements:
- [api-hardening:FR-001] - API key authentication
- [api-hardening:FR-003] - Rate limiting
- [api-hardening:FR-012] - API version header
- [api-hardening:FR-013] - Request ID tracking
"""

from src.api.middleware.api_version import APIVersionMiddleware
from src.api.middleware.auth import AuthMiddleware
from src.api.middleware.rate_limit import RateLimitMiddleware
from src.api.middleware.request_id import RequestIdMiddleware, get_request_id

__all__ = [
    "APIVersionMiddleware",
    "AuthMiddleware",
    "RateLimitMiddleware",
    "RequestIdMiddleware",
    "get_request_id",
]
