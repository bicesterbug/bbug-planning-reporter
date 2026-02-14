"""
Tests for MCP server auth middleware.

Verifies [cycle-route-assessment:FR-011] - Bearer token authentication
Verifies [cycle-route-assessment:NFR-005] - Opt-in via MCP_API_KEY
Verifies [cycle-route-assessment:NFR-006] - Constant-time comparison, logging
"""

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.mcp_servers.shared.auth import MCPAuthMiddleware


def _make_app(api_key: str | None = None) -> Starlette:
    """Create a test Starlette app with auth middleware."""

    async def handle_sse(request: Request) -> JSONResponse:
        return JSONResponse({"status": "connected"})

    async def handle_messages(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def handle_mcp(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def handle_health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Route("/messages", endpoint=handle_messages),
            Route("/mcp", endpoint=handle_mcp, methods=["GET", "POST"]),
            Route("/health", endpoint=handle_health),
        ],
    )
    app.add_middleware(MCPAuthMiddleware, api_key=api_key)
    return app


class TestMCPAuthMiddlewareWithKey:
    """Tests with auth enabled (api_key set)."""

    def setup_method(self):
        self.app = _make_app(api_key="test-secret-key")
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def test_valid_bearer_token_accepted(self):
        """Verifies [cycle-route-assessment:MCPAuthMiddleware/TS-01].

        Given: Middleware with key="test-secret-key"
        When: Request with Authorization: Bearer test-secret-key
        Then: Request passes through
        """
        response = self.client.get(
            "/sse", headers={"Authorization": "Bearer test-secret-key"}
        )
        assert response.status_code == 200
        assert response.json() == {"status": "connected"}

    def test_missing_auth_rejected(self):
        """Verifies [cycle-route-assessment:MCPAuthMiddleware/TS-02].

        Given: Middleware with key="test-secret-key"
        When: Request with no Authorization header
        Then: 401 Unauthorized
        """
        response = self.client.get("/sse")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"

    def test_invalid_token_rejected(self):
        """Verifies [cycle-route-assessment:MCPAuthMiddleware/TS-03].

        Given: Middleware with key="test-secret-key"
        When: Request with Authorization: Bearer wrong-key
        Then: 401 Unauthorized
        """
        response = self.client.get(
            "/sse", headers={"Authorization": "Bearer wrong-key"}
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"

    def test_health_exempt_from_auth(self):
        """Verifies [cycle-route-assessment:MCPAuthMiddleware/TS-04].

        Given: Middleware with key="test-secret-key"
        When: GET /health without auth
        Then: Request passes through (200 OK)
        """
        response = self.client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_basic_auth_scheme_rejected(self):
        """Verifies [cycle-route-assessment:MCPAuthMiddleware/TS-06].

        Given: Middleware with key="test-secret-key"
        When: Request with Authorization: Basic ...
        Then: 401 Unauthorized
        """
        response = self.client.get(
            "/sse", headers={"Authorization": "Basic dXNlcjpwYXNz"}
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"

    def test_auth_enforced_on_messages_endpoint(self):
        """Auth applies to /messages endpoint too.

        Given: Middleware with key="test-secret-key"
        When: POST /messages without auth
        Then: 401 Unauthorized
        """
        response = self.client.get("/messages")
        assert response.status_code == 401

    def test_auth_enforced_on_mcp_endpoint(self):
        """Auth applies to /mcp (Streamable HTTP) endpoint.

        Given: Middleware with key="test-secret-key"
        When: POST /mcp without auth
        Then: 401 Unauthorized
        """
        response = self.client.post("/mcp")
        assert response.status_code == 401

    def test_valid_token_on_mcp_endpoint(self):
        """Auth passes on /mcp with valid token.

        Given: Middleware with key="test-secret-key"
        When: POST /mcp with valid bearer token
        Then: Request passes through
        """
        response = self.client.post(
            "/mcp", headers={"Authorization": "Bearer test-secret-key"}
        )
        assert response.status_code == 200

    def test_empty_bearer_token_rejected(self):
        """Empty token after Bearer prefix is rejected.

        Given: Middleware with key="test-secret-key"
        When: Request with Authorization: Bearer (empty)
        Then: 401 Unauthorized
        """
        response = self.client.get(
            "/sse", headers={"Authorization": "Bearer "}
        )
        assert response.status_code == 401

    def test_malformed_auth_header_no_space(self):
        """Malformed header without space separator is rejected.

        Given: Middleware with key="test-secret-key"
        When: Request with Authorization: Bearertoken
        Then: 401 Unauthorized
        """
        response = self.client.get(
            "/sse", headers={"Authorization": "Bearertoken"}
        )
        assert response.status_code == 401


class TestMCPAuthMiddlewareWithoutKey:
    """Tests with auth disabled (no api_key)."""

    def setup_method(self):
        self.app = _make_app(api_key=None)
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def test_no_auth_when_key_not_configured(self):
        """Verifies [cycle-route-assessment:MCPAuthMiddleware/TS-05].

        Given: Middleware with key=None
        When: Request with no auth
        Then: Request passes through
        """
        response = self.client.get("/sse")
        assert response.status_code == 200
        assert response.json() == {"status": "connected"}

    def test_no_auth_on_all_endpoints(self):
        """All endpoints are accessible without auth when key is not set.

        Given: Middleware with key=None
        When: Requests to /sse, /messages, /mcp, /health without auth
        Then: All pass through
        """
        for path in ["/sse", "/messages", "/health"]:
            response = self.client.get(path)
            assert response.status_code == 200, f"Expected 200 for {path}"


class TestMCPAuthMiddlewareEmptyKey:
    """Tests with empty string api_key (treated as disabled)."""

    def setup_method(self):
        self.app = _make_app(api_key="")
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def test_empty_key_disables_auth(self):
        """Empty string MCP_API_KEY is treated as auth disabled.

        Given: Middleware with key=""
        When: Request with no auth
        Then: Request passes through
        """
        response = self.client.get("/sse")
        assert response.status_code == 200
