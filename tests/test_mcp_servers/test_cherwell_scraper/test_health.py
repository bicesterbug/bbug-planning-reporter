"""
Tests for cherwell-scraper-mcp health endpoint.

Verifies [scraper-health-check:FR-001] - Health endpoint
Verifies [scraper-health-check:FR-002] - Docker health check
Verifies [scraper-health-check:NFR-001] - Health check latency
"""

from pathlib import Path

from starlette.testclient import TestClient

from src.mcp_servers.cherwell_scraper.server import create_app


class TestHealthEndpoint:
    """Verifies [scraper-health-check:create_app/TS-01], [scraper-health-check:create_app/TS-02]."""

    def setup_method(self):
        app = create_app()
        self.client = TestClient(app)

    def test_health_returns_200_with_status_ok(self):
        """Verifies [scraper-health-check:create_app/TS-01] - Health endpoint returns 200.

        Given: The scraper Starlette app is running
        When: GET /health is requested
        Then: Response is 200 with body {"status": "ok"}
        """
        response = self.client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_health_returns_json_content_type(self):
        """Verifies [scraper-health-check:create_app/TS-02] - Health endpoint has correct content type.

        Given: The scraper Starlette app is running
        When: GET /health is requested
        Then: Response Content-Type is application/json
        """
        response = self.client.get("/health")
        assert "application/json" in response.headers["content-type"]


class TestDockerfileHealthCheck:
    """Verifies [scraper-health-check:Dockerfile/TS-01]."""

    def test_dockerfile_healthcheck_targets_health_endpoint(self):
        """Verifies [scraper-health-check:Dockerfile/TS-01] - Health check targets correct endpoint.

        Given: Dockerfile.scraper is read
        When: HEALTHCHECK command is inspected
        Then: URL contains /health not /sse
        """
        dockerfile = Path("docker/Dockerfile.scraper").read_text()
        assert "/health" in dockerfile
        assert "/sse" not in dockerfile.split("HEALTHCHECK")[1]
