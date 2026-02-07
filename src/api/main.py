"""
FastAPI application entry point.

Implements [foundation-api:FR-013] - Health check endpoint
Implements [policy-knowledge-base:FR-001] - Policy Knowledge Base API
Implements [api-hardening:FR-001] - API key authentication
Implements [api-hardening:FR-008] - OpenAPI documentation
Implements [api-hardening:FR-011] - Error response consistency
Implements [api-hardening:FR-012] - X-API-Version header
Implements [api-hardening:FR-013] - Request ID tracking
"""

import os

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from src.api.auth.key_validator import APIKeyValidator
from src.api.exception_handlers import register_exception_handlers
from src.api.middleware.api_version import APIVersionMiddleware
from src.api.middleware.auth import AuthMiddleware
from src.api.middleware.request_id import RequestIdMiddleware
from src.api.routes import downloads, health, letters, policies, reviews

# API Version for X-API-Version header
API_VERSION = "1.0.0"


def custom_openapi(app: FastAPI):
    """
    Generate custom OpenAPI schema with security definitions.

    Implements [api-hardening:FR-008] - OpenAPI documentation
    Implements [api-hardening:OpenAPIConfiguration/TS-01] - Security definitions
    """
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    # Add security schemes
    openapi_schema["components"] = openapi_schema.get("components", {})
    openapi_schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "description": "API key authentication. Pass your API key as a Bearer token.",
        }
    }

    # Add global security requirement (except for health check)
    openapi_schema["security"] = [{"BearerAuth": []}]

    # Add custom headers documentation
    openapi_schema["info"]["x-custom-headers"] = {
        "X-Request-ID": "Unique identifier for request tracing. Auto-generated if not provided.",
        "X-API-Version": f"API version number. Currently: {API_VERSION}",
        "X-RateLimit-Limit": "Maximum requests allowed per window.",
        "X-RateLimit-Remaining": "Requests remaining in current window.",
        "X-RateLimit-Reset": "Unix timestamp when the rate limit window resets.",
    }

    app.openapi_schema = openapi_schema
    return app.openapi_schema


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns configured app with middleware and routers.
    """
    app = FastAPI(
        title="Cherwell Cycle Advocacy Agent",
        description="""
AI-powered planning application review from a cycling advocacy perspective.

## Authentication

All endpoints (except `/health`) require Bearer token authentication.
Pass your API key in the Authorization header:

```
Authorization: Bearer your-api-key-here
```

## Rate Limiting

API requests are rate limited to 60 requests per minute per API key.
Rate limit information is included in response headers:
- `X-RateLimit-Limit`: Maximum requests per window
- `X-RateLimit-Remaining`: Requests remaining
- `X-RateLimit-Reset`: Window reset time (Unix timestamp)

## Request Tracing

Each request is assigned a unique `X-Request-ID` for tracing.
You can provide your own ID in the request header.

## Error Responses

All errors follow a consistent format:
```json
{
  "error": {
    "code": "error_code",
    "message": "Human-readable message",
    "details": {}
  }
}
```
""",
        version=API_VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # Override OpenAPI schema generation
    app.openapi = lambda: custom_openapi(app)

    # Configure middleware (order matters: first added = last to execute)
    _configure_middleware(app)

    # Register exception handlers
    register_exception_handlers(app)

    # Include routers
    app.include_router(health.router, prefix="/api/v1", tags=["health"])
    app.include_router(reviews.router, prefix="/api/v1", tags=["reviews"])
    app.include_router(downloads.router, prefix="/api/v1", tags=["downloads"])
    app.include_router(policies.router, prefix="/api/v1", tags=["policies"])
    app.include_router(letters.router, prefix="/api/v1", tags=["letters"])

    return app


def _configure_middleware(app: FastAPI) -> None:
    """
    Configure middleware for the application.

    Middleware execution order (from outermost to innermost):
    1. APIVersionMiddleware - Adds X-API-Version header
    2. RequestIdMiddleware - Assigns/preserves X-Request-ID
    3. AuthMiddleware - Validates API key (skipped in development if no keys configured)

    Response flows back in reverse order.
    """
    # Only enable auth middleware if API keys are configured
    # This allows running in development without setting up keys
    if os.getenv("API_KEYS") or os.getenv("API_KEYS_FILE"):
        validator = APIKeyValidator()
        app.add_middleware(AuthMiddleware, validator=validator)

    # Request ID middleware always runs
    app.add_middleware(RequestIdMiddleware)

    # API version header on all responses
    app.add_middleware(APIVersionMiddleware, version=API_VERSION)


# Create the application instance
app = create_app()
