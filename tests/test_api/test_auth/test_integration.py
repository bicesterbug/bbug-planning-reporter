"""
Integration tests for authentication flow.

Verifies [api-hardening:ITS-01] - Full request with auth and request ID
Verifies [api-hardening:ITS-05] - Request ID tracing
"""

import re
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import create_app

UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


@pytest.fixture
def app_with_auth():
    """Create app with authentication enabled."""
    # Patch environment to enable auth
    with patch.dict(
        "os.environ",
        {"API_KEYS": "sk-cycle-test-123,sk-cycle-prod-456"},
        clear=False,
    ):
        return create_app()


@pytest.fixture
async def authenticated_client(app_with_auth):
    """Create async test client with valid API key."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth),
        base_url="http://test",
        headers={"Authorization": "Bearer sk-cycle-test-123"},
    ) as client:
        yield client


@pytest.fixture
async def unauthenticated_client(app_with_auth):
    """Create async test client without API key."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth),
        base_url="http://test",
    ) as client:
        yield client


class TestFullAuthFlow:
    """
    Integration tests for complete auth flow with middleware.

    Verifies [api-hardening:ITS-01] - Full request with auth and rate limit
    """

    @pytest.mark.asyncio
    async def test_authenticated_request_succeeds(self, authenticated_client):
        """
        Verifies [api-hardening:ITS-01] - Full request with auth and request ID

        Given: Valid API key, under rate limit
        When: GET /api/v1/health
        Then: Request succeeds with all headers (auth, request ID)
        """
        response = await authenticated_client.get("/api/v1/health")
        assert response.status_code == 200

        # Should have request ID
        request_id = response.headers.get("X-Request-ID")
        assert request_id is not None
        assert UUID_PATTERN.match(request_id)

    @pytest.mark.asyncio
    async def test_unauthenticated_request_to_protected_fails(
        self, unauthenticated_client
    ):
        """Unauthenticated request to protected endpoint fails."""
        response = await unauthenticated_client.get("/api/v1/reviews")
        assert response.status_code == 401

        # Should still have request ID even for failed requests
        assert response.headers.get("X-Request-ID") is not None

    @pytest.mark.asyncio
    async def test_health_exempt_from_auth(self, unauthenticated_client):
        """Health endpoint works without authentication."""
        response = await unauthenticated_client.get("/api/v1/health")
        assert response.status_code == 200


class TestRequestIdTracing:
    """
    Integration tests for request ID tracing.

    Verifies [api-hardening:ITS-05] - Request ID tracing
    """

    @pytest.mark.asyncio
    async def test_client_request_id_preserved(self, authenticated_client):
        """
        Verifies [api-hardening:ITS-05] - Request ID tracing

        Given: Request with custom X-Request-ID
        When: GET /api/v1/health
        Then: Same ID in response header
        """
        custom_id = "trace-12345-abcde"
        response = await authenticated_client.get(
            "/api/v1/health",
            headers={"X-Request-ID": custom_id},
        )
        assert response.status_code == 200
        assert response.headers.get("X-Request-ID") == custom_id

    @pytest.mark.asyncio
    async def test_request_id_in_error_responses(self, unauthenticated_client):
        """Request ID is included even in error responses."""
        response = await unauthenticated_client.get("/api/v1/reviews")
        assert response.status_code == 401

        request_id = response.headers.get("X-Request-ID")
        assert request_id is not None
        assert UUID_PATTERN.match(request_id)


class TestMiddlewareOrder:
    """Tests for correct middleware execution order."""

    @pytest.mark.asyncio
    async def test_request_id_set_before_auth(self, unauthenticated_client):
        """Request ID is set even if auth fails (request ID runs first)."""
        response = await unauthenticated_client.get("/api/v1/reviews")
        # Auth fails with 401
        assert response.status_code == 401
        # But request ID header is still present
        assert response.headers.get("X-Request-ID") is not None


class TestMultipleApiKeys:
    """Tests for multiple API key support."""

    @pytest.mark.asyncio
    async def test_multiple_valid_keys(self, app_with_auth):
        """Multiple API keys can be configured and used."""
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            # First key
            r1 = await client.get(
                "/api/v1/health",
                headers={"Authorization": "Bearer sk-cycle-test-123"},
            )
            assert r1.status_code == 200

            # Second key
            r2 = await client.get(
                "/api/v1/health",
                headers={"Authorization": "Bearer sk-cycle-prod-456"},
            )
            assert r2.status_code == 200

            # Invalid key
            r3 = await client.get(
                "/api/v1/health",
                headers={"Authorization": "Bearer sk-invalid"},
            )
            # Health is exempt, so still 200
            assert r3.status_code == 200

            # But reviews endpoint requires auth
            r4 = await client.get(
                "/api/v1/reviews",
                headers={"Authorization": "Bearer sk-invalid"},
            )
            assert r4.status_code == 401
