"""
Tests for OpenAPI documentation configuration.

Verifies [api-hardening:FR-008] - OpenAPI documentation
Verifies [api-hardening:NFR-003] - Swagger UI availability
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import API_VERSION, create_app


@pytest.fixture
def app():
    """Create test app."""
    return create_app()


@pytest.fixture
async def client(app):
    """Create async test client."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


class TestSecurityDefinitions:
    """
    Tests for security definitions in OpenAPI spec.

    Verifies [api-hardening:OpenAPIConfiguration/TS-01] - Security definitions
    """

    @pytest.mark.asyncio
    async def test_bearer_auth_scheme_defined(self, client):
        """
        Verifies [api-hardening:OpenAPIConfiguration/TS-01] - Security definitions

        Given: API running
        When: GET /openapi.json
        Then: Spec includes BearerAuth security scheme
        """
        response = await client.get("/openapi.json")

        assert response.status_code == 200
        spec = response.json()

        assert "components" in spec
        assert "securitySchemes" in spec["components"]
        assert "BearerAuth" in spec["components"]["securitySchemes"]

        bearer_scheme = spec["components"]["securitySchemes"]["BearerAuth"]
        assert bearer_scheme["type"] == "http"
        assert bearer_scheme["scheme"] == "bearer"

    @pytest.mark.asyncio
    async def test_global_security_requirement(self, client):
        """Global security requirement is set."""
        response = await client.get("/openapi.json")

        spec = response.json()
        assert "security" in spec
        assert {"BearerAuth": []} in spec["security"]


class TestSwaggerUI:
    """
    Tests for Swagger UI availability.

    Verifies [api-hardening:OpenAPIConfiguration/TS-02] - Swagger UI available
    """

    @pytest.mark.asyncio
    async def test_swagger_ui_available(self, client):
        """
        Verifies [api-hardening:OpenAPIConfiguration/TS-02] - Swagger UI available

        Given: API running
        When: GET /docs
        Then: Swagger UI page returned
        """
        response = await client.get("/docs")

        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        assert "swagger" in response.text.lower()

    @pytest.mark.asyncio
    async def test_redoc_available(self, client):
        """ReDoc documentation page is available."""
        response = await client.get("/redoc")

        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")


class TestOpenAPIEndpoints:
    """
    Tests for endpoint documentation.

    Verifies [api-hardening:OpenAPIConfiguration/TS-03] - Endpoints documented
    """

    @pytest.mark.asyncio
    async def test_all_endpoints_documented(self, client):
        """
        Verifies [api-hardening:OpenAPIConfiguration/TS-03] - Endpoints documented

        Given: API running
        When: GET /openapi.json
        Then: All endpoints documented with descriptions
        """
        response = await client.get("/openapi.json")

        spec = response.json()
        paths = spec.get("paths", {})

        # Check key endpoints are documented
        assert "/api/v1/health" in paths
        assert "/api/v1/reviews" in paths
        assert "/api/v1/reviews/{review_id}" in paths
        assert "/api/v1/reviews/{review_id}/download" in paths
        assert "/api/v1/policies" in paths

    @pytest.mark.asyncio
    async def test_endpoints_have_tags(self, client):
        """Endpoints are organized with tags."""
        response = await client.get("/openapi.json")

        spec = response.json()
        paths = spec.get("paths", {})

        # Check that endpoints have tags
        health_endpoint = paths.get("/api/v1/health", {}).get("get", {})
        assert "tags" in health_endpoint
        assert "health" in health_endpoint["tags"]


class TestOpenAPIVersion:
    """
    Tests for API version in OpenAPI spec.

    Verifies [api-hardening:OpenAPIConfiguration/TS-04] - Version in spec
    """

    @pytest.mark.asyncio
    async def test_version_in_spec(self, client):
        """
        Verifies [api-hardening:OpenAPIConfiguration/TS-04] - Version in spec

        Given: API running
        When: GET /openapi.json
        Then: Spec version matches API_VERSION constant
        """
        response = await client.get("/openapi.json")

        spec = response.json()
        assert spec["info"]["version"] == API_VERSION

    @pytest.mark.asyncio
    async def test_custom_headers_documented(self, client):
        """Custom headers are documented in spec info."""
        response = await client.get("/openapi.json")

        spec = response.json()

        # Check for custom headers documentation
        assert "x-custom-headers" in spec["info"]
        headers = spec["info"]["x-custom-headers"]

        assert "X-Request-ID" in headers
        assert "X-API-Version" in headers
        assert "X-RateLimit-Limit" in headers


class TestOpenAPIResponses:
    """Tests for response documentation."""

    @pytest.mark.asyncio
    async def test_health_endpoint_response_documented(self, client):
        """Health endpoint has response schema documented."""
        response = await client.get("/openapi.json")

        spec = response.json()
        health = spec["paths"]["/api/v1/health"]["get"]

        assert "responses" in health
        assert "200" in health["responses"]
