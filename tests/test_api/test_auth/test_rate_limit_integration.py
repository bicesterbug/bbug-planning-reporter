"""
Integration tests for rate limiting.

Verifies [api-hardening:ITS-02] - Rate limit enforced across requests
"""

import fakeredis.aioredis
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.auth.key_validator import APIKeyValidator
from src.api.middleware.auth import AuthMiddleware
from src.api.middleware.rate_limit import RateLimitMiddleware
from src.api.middleware.request_id import RequestIdMiddleware


@pytest.fixture
async def fake_redis():
    """Provide a fake Redis client for testing."""
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


@pytest.fixture
def full_app(fake_redis):
    """Create app with full middleware stack."""
    app = FastAPI()

    validator = APIKeyValidator(keys={"sk-cycle-test-123", "sk-cycle-test-456"})

    # Add middleware in correct order (first added = last to execute)
    app.add_middleware(RateLimitMiddleware, redis_client=fake_redis, rate_limit=10)
    app.add_middleware(AuthMiddleware, validator=validator)
    app.add_middleware(RequestIdMiddleware)

    @app.get("/api/v1/reviews")
    async def list_reviews():
        return {"reviews": []}

    @app.post("/api/v1/reviews")
    async def create_review():
        return {"review_id": "test123"}

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok"}

    return app


class TestRateLimitEnforcement:
    """
    Integration tests for rate limit enforcement across multiple requests.

    Verifies [api-hardening:ITS-02] - Rate limit enforced across requests
    """

    @pytest.mark.asyncio
    async def test_rate_limit_enforced_across_requests(self, full_app):
        """
        Verifies [api-hardening:ITS-02] - Rate limit enforced across requests

        Given: Valid API key
        When: Make 11 requests in 1 minute (limit is 10)
        Then: First 10 succeed, 11th returns 429
        """
        async with AsyncClient(
            transport=ASGITransport(app=full_app),
            base_url="http://test",
            headers={"Authorization": "Bearer sk-cycle-test-123"},
        ) as client:
            # First 10 requests should succeed
            for i in range(10):
                response = await client.get("/api/v1/reviews")
                assert response.status_code == 200, f"Request {i+1} failed unexpectedly"

                # Check headers on each request
                assert "X-RateLimit-Limit" in response.headers
                assert "X-RateLimit-Remaining" in response.headers
                remaining = int(response.headers["X-RateLimit-Remaining"])
                assert remaining == 10 - i - 1, f"Unexpected remaining on request {i+1}"

            # 11th request should be rate limited
            response = await client.get("/api/v1/reviews")
            assert response.status_code == 429
            assert response.json()["error"]["code"] == "rate_limited"
            assert "Retry-After" in response.headers

    @pytest.mark.asyncio
    async def test_rate_limit_per_key_isolation(self, full_app):
        """
        Test that rate limits are isolated per API key.

        Given: Key A at limit
        When: Make request with Key B
        Then: Key B request succeeds
        """
        async with AsyncClient(
            transport=ASGITransport(app=full_app),
            base_url="http://test",
        ) as client:
            # Exhaust Key A's limit
            for _ in range(10):
                await client.get(
                    "/api/v1/reviews",
                    headers={"Authorization": "Bearer sk-cycle-test-123"},
                )

            # Key A is now limited
            r1 = await client.get(
                "/api/v1/reviews",
                headers={"Authorization": "Bearer sk-cycle-test-123"},
            )
            assert r1.status_code == 429

            # Key B should have full quota
            r2 = await client.get(
                "/api/v1/reviews",
                headers={"Authorization": "Bearer sk-cycle-test-456"},
            )
            assert r2.status_code == 200
            assert int(r2.headers["X-RateLimit-Remaining"]) == 9  # 10 - 1

    @pytest.mark.asyncio
    async def test_rate_limit_with_request_id(self, full_app):
        """Request ID is present even on rate limited responses."""
        async with AsyncClient(
            transport=ASGITransport(app=full_app),
            base_url="http://test",
            headers={"Authorization": "Bearer sk-cycle-test-123"},
        ) as client:
            # Exhaust the limit
            for _ in range(10):
                await client.get("/api/v1/reviews")

            # Rate limited response should still have request ID
            response = await client.get("/api/v1/reviews")
            assert response.status_code == 429
            assert "X-Request-ID" in response.headers

    @pytest.mark.asyncio
    async def test_rate_limit_applies_to_all_methods(self, full_app):
        """Rate limit counts all HTTP methods."""
        async with AsyncClient(
            transport=ASGITransport(app=full_app),
            base_url="http://test",
            headers={"Authorization": "Bearer sk-cycle-test-123"},
        ) as client:
            # Mix of GET and POST requests
            for _ in range(5):
                await client.get("/api/v1/reviews")
            for _ in range(5):
                await client.post("/api/v1/reviews")

            # Next request should be limited regardless of method
            r1 = await client.get("/api/v1/reviews")
            assert r1.status_code == 429

            r2 = await client.post("/api/v1/reviews")
            assert r2.status_code == 429


class TestRateLimitRecovery:
    """Tests for rate limit recovery behavior."""

    @pytest.mark.asyncio
    async def test_retry_after_header_is_reasonable(self, full_app):
        """Retry-After header provides reasonable wait time."""
        async with AsyncClient(
            transport=ASGITransport(app=full_app),
            base_url="http://test",
            headers={"Authorization": "Bearer sk-cycle-test-123"},
        ) as client:
            # Exhaust the limit
            for _ in range(10):
                await client.get("/api/v1/reviews")

            response = await client.get("/api/v1/reviews")
            assert response.status_code == 429

            retry_after = int(response.headers["Retry-After"])
            # Should be between 1 and 60 seconds (window size)
            assert 1 <= retry_after <= 60


class TestMiddlewareStackIntegration:
    """Tests for middleware stack working together."""

    @pytest.mark.asyncio
    async def test_all_headers_present_on_success(self, full_app):
        """Successful requests have all expected headers."""
        async with AsyncClient(
            transport=ASGITransport(app=full_app),
            base_url="http://test",
            headers={"Authorization": "Bearer sk-cycle-test-123"},
        ) as client:
            response = await client.get("/api/v1/reviews")
            assert response.status_code == 200

            # Request ID from RequestIdMiddleware
            assert "X-Request-ID" in response.headers

            # Rate limit headers from RateLimitMiddleware
            assert "X-RateLimit-Limit" in response.headers
            assert "X-RateLimit-Remaining" in response.headers
            assert "X-RateLimit-Reset" in response.headers

    @pytest.mark.asyncio
    async def test_all_headers_present_on_rate_limit(self, full_app):
        """Rate limited responses have all expected headers."""
        async with AsyncClient(
            transport=ASGITransport(app=full_app),
            base_url="http://test",
            headers={"Authorization": "Bearer sk-cycle-test-123"},
        ) as client:
            # Exhaust limit
            for _ in range(10):
                await client.get("/api/v1/reviews")

            response = await client.get("/api/v1/reviews")
            assert response.status_code == 429

            # All headers should still be present
            assert "X-Request-ID" in response.headers
            assert "X-RateLimit-Limit" in response.headers
            assert "X-RateLimit-Remaining" in response.headers
            assert "X-RateLimit-Reset" in response.headers
            assert "Retry-After" in response.headers
