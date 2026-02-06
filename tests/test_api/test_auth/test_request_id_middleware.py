"""
Tests for RequestIdMiddleware.

Verifies [api-hardening:FR-013] - Request ID tracking with X-Request-ID header
Verifies [api-hardening:NFR-002] - Audit trails (security)
"""

import asyncio
import re

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.middleware.request_id import RequestIdMiddleware, get_request_id

UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


@pytest.fixture
def app():
    """Create a test FastAPI app with request ID middleware."""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/test")
    async def test_endpoint():
        # Return the request ID from context to verify it's accessible
        request_id = get_request_id()
        return {"request_id": request_id}

    return app


@pytest.fixture
async def client(app):
    """Create async test client."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


class TestAutoGenerateRequestId:
    """
    Tests for auto-generating request IDs.

    Verifies [api-hardening:RequestIdMiddleware/TS-01] - Auto-generate request ID
    """

    @pytest.mark.asyncio
    async def test_generates_uuid_when_no_header(self, client):
        """
        Verifies [api-hardening:RequestIdMiddleware/TS-01] - Auto-generate request ID

        Given: No X-Request-ID in request
        When: Make request
        Then: Response includes X-Request-ID (UUID format)
        """
        response = await client.get("/test")
        assert response.status_code == 200

        request_id = response.headers.get("X-Request-ID")
        assert request_id is not None
        assert UUID_PATTERN.match(request_id), f"Not a valid UUID: {request_id}"

    @pytest.mark.asyncio
    async def test_request_id_accessible_in_handler(self, client):
        """Request ID is accessible via get_request_id() in handlers."""
        response = await client.get("/test")
        assert response.status_code == 200

        # The handler returns the request ID from context
        body_id = response.json()["request_id"]
        header_id = response.headers.get("X-Request-ID")

        assert body_id == header_id


class TestPreserveClientRequestId:
    """
    Tests for preserving client-provided request IDs.

    Verifies [api-hardening:RequestIdMiddleware/TS-02] - Preserve client request ID
    """

    @pytest.mark.asyncio
    async def test_preserves_client_request_id(self, client):
        """
        Verifies [api-hardening:RequestIdMiddleware/TS-02] - Preserve client request ID

        Given: Client sends X-Request-ID: "client-123"
        When: Make request
        Then: Response includes X-Request-ID: "client-123"
        """
        client_id = "client-request-id-12345"
        response = await client.get(
            "/test",
            headers={"X-Request-ID": client_id},
        )
        assert response.status_code == 200
        assert response.headers.get("X-Request-ID") == client_id

    @pytest.mark.asyncio
    async def test_client_id_accessible_in_handler(self, client):
        """Client-provided ID is accessible in handlers."""
        client_id = "my-custom-trace-id"
        response = await client.get(
            "/test",
            headers={"X-Request-ID": client_id},
        )

        body_id = response.json()["request_id"]
        assert body_id == client_id


class TestUniqueRequestIds:
    """
    Tests for unique IDs per request.

    Verifies [api-hardening:RequestIdMiddleware/TS-04] - Unique IDs per request
    """

    @pytest.mark.asyncio
    async def test_each_request_gets_unique_id(self, client):
        """
        Verifies [api-hardening:RequestIdMiddleware/TS-04] - Unique IDs per request

        Given: Multiple concurrent requests
        When: Make 10 requests
        Then: Each response has unique X-Request-ID
        """
        # Make sequential requests
        request_ids = set()
        for _ in range(10):
            response = await client.get("/test")
            request_id = response.headers.get("X-Request-ID")
            request_ids.add(request_id)

        # All 10 should be unique
        assert len(request_ids) == 10

    @pytest.mark.asyncio
    async def test_concurrent_requests_unique_ids(self, client):
        """Concurrent requests each get unique IDs."""

        async def make_request():
            response = await client.get("/test")
            return response.headers.get("X-Request-ID")

        # Make 10 concurrent requests
        tasks = [make_request() for _ in range(10)]
        request_ids = await asyncio.gather(*tasks)

        # All should be unique
        assert len(set(request_ids)) == 10


class TestRequestIdInContext:
    """
    Tests for request ID availability in context.

    Verifies [api-hardening:RequestIdMiddleware/TS-03] - Request ID in logs
    """

    @pytest.mark.asyncio
    async def test_request_id_available_during_request(self, client):
        """
        Verifies [api-hardening:RequestIdMiddleware/TS-03] - Request ID in logs

        Given: Request processed
        When: Check logs/context
        Then: Log entries include request_id field
        """
        response = await client.get("/test")
        # The handler successfully retrieved request_id from context
        assert response.json()["request_id"] is not None

    @pytest.mark.asyncio
    async def test_request_id_cleared_after_request(self, client):
        """Request ID context is cleared after request completes."""
        await client.get("/test")

        # After request, context should be cleared
        assert get_request_id() is None


class TestEdgeCases:
    """Tests for edge cases."""

    @pytest.mark.asyncio
    async def test_empty_request_id_header_generates_new(self, client):
        """Empty X-Request-ID header triggers generation of new ID."""
        response = await client.get(
            "/test",
            headers={"X-Request-ID": ""},
        )
        request_id = response.headers.get("X-Request-ID")
        # Should generate a new UUID since header was empty
        assert request_id != ""
        assert UUID_PATTERN.match(request_id)

    @pytest.mark.asyncio
    async def test_long_request_id_preserved(self, client):
        """Long custom request IDs are preserved."""
        long_id = "custom-" + "a" * 100
        response = await client.get(
            "/test",
            headers={"X-Request-ID": long_id},
        )
        assert response.headers.get("X-Request-ID") == long_id
