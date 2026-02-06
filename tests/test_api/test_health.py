"""
Tests for health check endpoint.

Implements [foundation-api:HealthRouter/TS-01] - Healthy system
Implements [foundation-api:HealthRouter/TS-02] - Degraded system
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app


@pytest.fixture
async def client() -> AsyncClient:
    """Create test client."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


class TestHealthEndpoint:
    """Tests for GET /api/v1/health."""

    @pytest.mark.asyncio
    async def test_healthy_system_returns_healthy_status(self, client: AsyncClient) -> None:
        """
        Verifies [foundation-api:HealthRouter/TS-01]

        Given: Redis is connected
        When: GET /api/v1/health
        Then: Returns 200 with status "healthy"
        """
        with patch(
            "src.api.routes.health.check_redis_connection",
            new_callable=AsyncMock,
            return_value="connected",
        ):
            response = await client.get("/api/v1/health")

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert data["status"] == "healthy"
        assert data["services"]["redis"] == "connected"
        assert "version" in data

    @pytest.mark.asyncio
    async def test_degraded_system_reports_redis_disconnected(
        self, client: AsyncClient
    ) -> None:
        """
        Verifies [foundation-api:HealthRouter/TS-02]

        Given: Redis is disconnected
        When: GET /api/v1/health
        Then: Returns 200 with status "degraded" and redis "disconnected"
        """
        with patch(
            "src.api.routes.health.check_redis_connection",
            new_callable=AsyncMock,
            return_value="disconnected",
        ):
            response = await client.get("/api/v1/health")

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert data["status"] == "degraded"
        assert data["services"]["redis"] == "disconnected"
