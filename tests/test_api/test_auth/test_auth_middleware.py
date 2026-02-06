"""
Tests for AuthMiddleware.

Verifies [api-hardening:FR-001] - API key authentication on all endpoints except /health
Verifies [api-hardening:FR-002] - Validate API keys against configured list
Verifies [api-hardening:NFR-002] - No OWASP vulnerabilities (authentication)
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.auth.key_validator import APIKeyValidator
from src.api.middleware.auth import AuthMiddleware


@pytest.fixture
def test_validator():
    """Validator with known test keys."""
    return APIKeyValidator(keys={"sk-cycle-test-123", "sk-cycle-valid"})


@pytest.fixture
def app(test_validator):
    """Create a test FastAPI app with auth middleware."""
    app = FastAPI()
    app.add_middleware(AuthMiddleware, validator=test_validator)

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/v1/reviews")
    async def list_reviews():
        return {"reviews": []}

    @app.post("/api/v1/reviews")
    async def create_review():
        return {"review_id": "test123"}

    @app.get("/docs")
    async def docs():
        return {"docs": "swagger"}

    @app.get("/openapi.json")
    async def openapi():
        return {"openapi": "3.0.0"}

    return app


@pytest.fixture
async def client(app):
    """Create async test client."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


class TestValidAPIKey:
    """
    Tests for valid API key scenarios.

    Verifies [api-hardening:AuthMiddleware/TS-01] - Valid API key
    """

    @pytest.mark.asyncio
    async def test_valid_api_key_allows_request(self, client):
        """
        Verifies [api-hardening:AuthMiddleware/TS-01] - Valid API key

        Given: Valid key "sk-cycle-test-123" in env
        When: Request with `Authorization: Bearer sk-cycle-test-123`
        Then: Request proceeds to handler
        """
        response = await client.get(
            "/api/v1/reviews",
            headers={"Authorization": "Bearer sk-cycle-test-123"},
        )
        assert response.status_code == 200
        assert response.json() == {"reviews": []}

    @pytest.mark.asyncio
    async def test_valid_api_key_on_post_request(self, client):
        """Valid API key works on POST requests."""
        response = await client.post(
            "/api/v1/reviews",
            headers={"Authorization": "Bearer sk-cycle-valid"},
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_bearer_case_insensitive(self, client):
        """Bearer scheme matching is case-insensitive."""
        response = await client.get(
            "/api/v1/reviews",
            headers={"Authorization": "BEARER sk-cycle-test-123"},
        )
        assert response.status_code == 200


class TestMissingAuthorization:
    """
    Tests for missing Authorization header.

    Verifies [api-hardening:AuthMiddleware/TS-02] - Missing Authorization header
    """

    @pytest.mark.asyncio
    async def test_missing_auth_header_returns_401(self, client):
        """
        Verifies [api-hardening:AuthMiddleware/TS-02] - Missing Authorization header

        Given: API requires auth
        When: Request without Authorization header
        Then: Returns 401 with error code "unauthorized"
        """
        response = await client.get("/api/v1/reviews")
        assert response.status_code == 401
        assert response.json() == {
            "error": {
                "code": "unauthorized",
                "message": "Missing Authorization header",
            }
        }


class TestInvalidAPIKey:
    """
    Tests for invalid API key scenarios.

    Verifies [api-hardening:AuthMiddleware/TS-03] - Invalid API key
    Verifies [api-hardening:AuthMiddleware/TS-06] - Revoked API key
    """

    @pytest.mark.asyncio
    async def test_invalid_api_key_returns_401(self, client):
        """
        Verifies [api-hardening:AuthMiddleware/TS-03] - Invalid API key

        Given: Key "sk-invalid" not in configured list
        When: Request with `Authorization: Bearer sk-invalid`
        Then: Returns 401 with error code "unauthorized"
        """
        response = await client.get(
            "/api/v1/reviews",
            headers={"Authorization": "Bearer sk-invalid"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"
        assert "Invalid API key" in response.json()["error"]["message"]

    @pytest.mark.asyncio
    async def test_revoked_api_key_returns_401(self, client):
        """
        Verifies [api-hardening:AuthMiddleware/TS-06] - Revoked API key

        Given: Key was previously valid but removed
        When: Request with revoked key
        Then: Returns 401 with error code "unauthorized"
        """
        # Any key not in the configured set is effectively "revoked"
        response = await client.get(
            "/api/v1/reviews",
            headers={"Authorization": "Bearer sk-cycle-revoked-key"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"


class TestMalformedAuthorization:
    """
    Tests for malformed Authorization header.

    Verifies [api-hardening:AuthMiddleware/TS-04] - Malformed Authorization header
    """

    @pytest.mark.asyncio
    async def test_basic_auth_rejected(self, client):
        """
        Verifies [api-hardening:AuthMiddleware/TS-04] - Malformed Authorization header

        Given: Auth header present
        When: Request with `Authorization: Basic xxx`
        Then: Returns 401 with error code "unauthorized"
        """
        response = await client.get(
            "/api/v1/reviews",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert response.status_code == 401
        assert "Bearer" in response.json()["error"]["message"]

    @pytest.mark.asyncio
    async def test_missing_token_rejected(self, client):
        """Malformed header with just 'Bearer' and no token."""
        response = await client.get(
            "/api/v1/reviews",
            headers={"Authorization": "Bearer"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_no_scheme_rejected(self, client):
        """Header without scheme is rejected."""
        response = await client.get(
            "/api/v1/reviews",
            headers={"Authorization": "sk-cycle-test-123"},
        )
        assert response.status_code == 401


class TestHealthEndpointBypass:
    """
    Tests for health endpoint authentication bypass.

    Verifies [api-hardening:AuthMiddleware/TS-05] - Health endpoint bypass
    """

    @pytest.mark.asyncio
    async def test_health_endpoint_no_auth_required(self, client):
        """
        Verifies [api-hardening:AuthMiddleware/TS-05] - Health endpoint bypass

        Given: /health endpoint
        When: Request without Authorization
        Then: Request proceeds (no 401)
        """
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_docs_endpoint_no_auth_required(self, client):
        """Documentation endpoints don't require auth."""
        response = await client.get("/docs")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_openapi_endpoint_no_auth_required(self, client):
        """OpenAPI spec endpoint doesn't require auth."""
        response = await client.get("/openapi.json")
        assert response.status_code == 200


class TestSecurityBestPractices:
    """Tests for security best practices."""

    @pytest.mark.asyncio
    async def test_no_key_in_error_message(self, client):
        """API key should not be exposed in error messages."""
        response = await client.get(
            "/api/v1/reviews",
            headers={"Authorization": "Bearer sk-super-secret-key-12345"},
        )
        assert response.status_code == 401
        error_message = response.json()["error"]["message"]
        assert "sk-super-secret-key-12345" not in error_message

    @pytest.mark.asyncio
    async def test_consistent_error_format(self, client):
        """All auth errors follow consistent format."""
        # Missing header
        r1 = await client.get("/api/v1/reviews")
        assert "error" in r1.json()
        assert "code" in r1.json()["error"]
        assert "message" in r1.json()["error"]

        # Invalid key
        r2 = await client.get(
            "/api/v1/reviews",
            headers={"Authorization": "Bearer invalid"},
        )
        assert "error" in r2.json()
        assert "code" in r2.json()["error"]
        assert "message" in r2.json()["error"]
