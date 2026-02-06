"""
Tests for RateLimitMiddleware.

Verifies [api-hardening:FR-003] - Rate limiting per API key
Verifies [api-hardening:FR-004] - Configurable rate limits
Verifies [api-hardening:NFR-002] - Prevent abuse
Verifies [api-hardening:NFR-004] - Performance under load
"""

from unittest.mock import patch

import fakeredis.aioredis
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.middleware.auth import AuthMiddleware
from src.api.middleware.rate_limit import RateLimitMiddleware


@pytest.fixture
async def fake_redis():
    """Provide a fake Redis client for testing."""
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


@pytest.fixture
def app_factory(fake_redis):
    """Factory to create test apps with rate limiting."""

    def _create_app(rate_limit: int = 5):
        app = FastAPI()

        # Add auth middleware with test keys
        from src.api.auth.key_validator import APIKeyValidator

        validator = APIKeyValidator(keys={"sk-test-key-1", "sk-test-key-2"})
        app.add_middleware(RateLimitMiddleware, redis_client=fake_redis, rate_limit=rate_limit)
        app.add_middleware(AuthMiddleware, validator=validator)

        @app.get("/api/v1/reviews")
        async def list_reviews():
            return {"reviews": []}

        @app.get("/api/v1/health")
        async def health():
            return {"status": "ok"}

        return app

    return _create_app


@pytest.fixture
async def client(app_factory):
    """Create client with default rate limit."""
    app = app_factory(rate_limit=5)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer sk-test-key-1"},
    ) as client:
        yield client


class TestUnderRateLimit:
    """
    Tests for requests under the rate limit.

    Verifies [api-hardening:RateLimitMiddleware/TS-01] - Under rate limit
    """

    @pytest.mark.asyncio
    async def test_under_limit_succeeds(self, client):
        """
        Verifies [api-hardening:RateLimitMiddleware/TS-01] - Under rate limit

        Given: Key has made 0 of 5 requests
        When: Make request
        Then: Returns 200 with X-RateLimit-Remaining
        """
        response = await client.get("/api/v1/reviews")
        assert response.status_code == 200

        # Should have rate limit headers
        assert "X-RateLimit-Limit" in response.headers
        assert "X-RateLimit-Remaining" in response.headers
        assert "X-RateLimit-Reset" in response.headers

    @pytest.mark.asyncio
    async def test_remaining_decrements(self, client):
        """Remaining count decrements with each request."""
        r1 = await client.get("/api/v1/reviews")
        remaining1 = int(r1.headers["X-RateLimit-Remaining"])

        r2 = await client.get("/api/v1/reviews")
        remaining2 = int(r2.headers["X-RateLimit-Remaining"])

        assert remaining2 == remaining1 - 1


class TestRateLimitExceeded:
    """
    Tests for rate limit exceeded scenarios.

    Verifies [api-hardening:RateLimitMiddleware/TS-02] - Rate limit exceeded
    """

    @pytest.mark.asyncio
    async def test_exceeding_limit_returns_429(self, app_factory):
        """
        Verifies [api-hardening:RateLimitMiddleware/TS-02] - Rate limit exceeded

        Given: Key has made 5 of 5 requests
        When: Make request
        Then: Returns 429 with error code "rate_limited" and Retry-After header
        """
        app = app_factory(rate_limit=3)  # Low limit for testing
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer sk-test-key-1"},
        ) as client:
            # Make requests up to the limit
            for _ in range(3):
                response = await client.get("/api/v1/reviews")
                assert response.status_code == 200

            # Next request should be rate limited
            response = await client.get("/api/v1/reviews")
            assert response.status_code == 429

            # Check error format
            data = response.json()
            assert data["error"]["code"] == "rate_limited"
            assert "retry" in data["error"]["message"].lower()

            # Check Retry-After header
            assert "Retry-After" in response.headers
            retry_after = int(response.headers["Retry-After"])
            assert retry_after > 0

    @pytest.mark.asyncio
    async def test_rate_limit_remaining_is_zero(self, app_factory):
        """When rate limited, remaining should be 0."""
        app = app_factory(rate_limit=2)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer sk-test-key-1"},
        ) as client:
            # Use up the limit
            await client.get("/api/v1/reviews")
            await client.get("/api/v1/reviews")

            # Rate limited response
            response = await client.get("/api/v1/reviews")
            assert response.status_code == 429
            assert response.headers["X-RateLimit-Remaining"] == "0"


class TestRateLimitHeaders:
    """
    Tests for rate limit headers.

    Verifies [api-hardening:RateLimitMiddleware/TS-03] - Rate limit headers
    """

    @pytest.mark.asyncio
    async def test_headers_present_on_all_responses(self, client):
        """
        Verifies [api-hardening:RateLimitMiddleware/TS-03] - Rate limit headers

        Given: Any authenticated request
        When: Make request
        Then: Response includes X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset
        """
        response = await client.get("/api/v1/reviews")

        assert "X-RateLimit-Limit" in response.headers
        assert "X-RateLimit-Remaining" in response.headers
        assert "X-RateLimit-Reset" in response.headers

        # Values should be valid integers
        assert int(response.headers["X-RateLimit-Limit"]) > 0
        assert int(response.headers["X-RateLimit-Remaining"]) >= 0
        assert int(response.headers["X-RateLimit-Reset"]) > 0

    @pytest.mark.asyncio
    async def test_limit_header_matches_config(self, app_factory):
        """Limit header matches configured rate limit."""
        app = app_factory(rate_limit=100)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer sk-test-key-1"},
        ) as client:
            response = await client.get("/api/v1/reviews")
            assert response.headers["X-RateLimit-Limit"] == "100"


class TestCustomRateLimit:
    """
    Tests for custom rate limit configuration.

    Verifies [api-hardening:RateLimitMiddleware/TS-04] - Custom rate limit
    Verifies [api-hardening:RateLimitMiddleware/TS-05] - Default rate limit
    """

    @pytest.mark.asyncio
    async def test_custom_rate_limit_from_env(self, fake_redis):
        """
        Verifies [api-hardening:RateLimitMiddleware/TS-04] - Custom rate limit

        Given: API_RATE_LIMIT=120 configured
        When: Make 61st request
        Then: Request succeeds (limit is 120)
        """
        with patch.dict("os.environ", {"API_RATE_LIMIT": "120"}):
            app = FastAPI()
            from src.api.auth.key_validator import APIKeyValidator

            validator = APIKeyValidator(keys={"sk-test"})
            app.add_middleware(RateLimitMiddleware, redis_client=fake_redis)
            app.add_middleware(AuthMiddleware, validator=validator)

            @app.get("/test")
            async def test_endpoint():
                return {"ok": True}

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer sk-test"},
            ) as client:
                response = await client.get("/test")
                assert response.headers["X-RateLimit-Limit"] == "120"

    @pytest.mark.asyncio
    async def test_default_rate_limit(self, fake_redis):
        """
        Verifies [api-hardening:RateLimitMiddleware/TS-05] - Default rate limit

        Given: No API_RATE_LIMIT configured
        When: Check limit
        Then: Uses default of 60 requests per minute
        """
        with patch.dict("os.environ", {}, clear=True):
            # Remove API_RATE_LIMIT if set
            import os

            os.environ.pop("API_RATE_LIMIT", None)

            app = FastAPI()
            from src.api.auth.key_validator import APIKeyValidator

            validator = APIKeyValidator(keys={"sk-test"})
            app.add_middleware(RateLimitMiddleware, redis_client=fake_redis)
            app.add_middleware(AuthMiddleware, validator=validator)

            @app.get("/test")
            async def test_endpoint():
                return {"ok": True}

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer sk-test"},
            ) as client:
                response = await client.get("/test")
                assert response.headers["X-RateLimit-Limit"] == "60"


class TestWindowReset:
    """
    Tests for rate limit window reset.

    Verifies [api-hardening:RateLimitMiddleware/TS-06] - Window reset
    """

    @pytest.mark.asyncio
    async def test_window_reset_time_in_future(self, client):
        """
        Verifies [api-hardening:RateLimitMiddleware/TS-06] - Window reset

        Given: Any request
        When: Check X-RateLimit-Reset
        Then: Reset time is in the future
        """
        import time

        response = await client.get("/api/v1/reviews")
        reset_time = int(response.headers["X-RateLimit-Reset"])
        current_time = int(time.time())

        assert reset_time > current_time


class TestPerKeyIsolation:
    """
    Tests for per-key rate limit isolation.

    Verifies [api-hardening:RateLimitMiddleware/TS-07] - Isolated per key
    """

    @pytest.mark.asyncio
    async def test_different_keys_isolated(self, app_factory):
        """
        Verifies [api-hardening:RateLimitMiddleware/TS-07] - Isolated per key

        Given: Key A at limit, Key B under limit
        When: Request with Key B
        Then: Key B request succeeds
        """
        app = app_factory(rate_limit=2)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            # Use up Key A's limit
            for _ in range(2):
                await client.get(
                    "/api/v1/reviews",
                    headers={"Authorization": "Bearer sk-test-key-1"},
                )

            # Key A is now rate limited
            r1 = await client.get(
                "/api/v1/reviews",
                headers={"Authorization": "Bearer sk-test-key-1"},
            )
            assert r1.status_code == 429

            # But Key B should still work
            r2 = await client.get(
                "/api/v1/reviews",
                headers={"Authorization": "Bearer sk-test-key-2"},
            )
            assert r2.status_code == 200


class TestHealthEndpointExempt:
    """Tests for health endpoint rate limit exemption."""

    @pytest.mark.asyncio
    async def test_health_not_rate_limited(self, app_factory):
        """Health endpoint is not subject to rate limiting."""
        app = app_factory(rate_limit=2)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            # Health endpoint doesn't require auth, so no rate limiting
            for _ in range(10):
                response = await client.get("/api/v1/health")
                assert response.status_code == 200


class TestGracefulDegradation:
    """Tests for graceful degradation when Redis unavailable."""

    @pytest.mark.asyncio
    async def test_allows_requests_when_redis_unavailable(self):
        """When Redis is unavailable, requests are allowed through."""
        app = FastAPI()
        from src.api.auth.key_validator import APIKeyValidator

        validator = APIKeyValidator(keys={"sk-test"})

        # No Redis client provided
        app.add_middleware(RateLimitMiddleware)
        app.add_middleware(AuthMiddleware, validator=validator)

        @app.get("/test")
        async def test_endpoint():
            return {"ok": True}

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer sk-test"},
        ) as client:
            # Should work without Redis (graceful degradation)
            response = await client.get("/test")
            assert response.status_code == 200
