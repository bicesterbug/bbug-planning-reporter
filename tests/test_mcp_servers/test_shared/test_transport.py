"""
Tests for shared MCP transport helper.

Verifies [cycle-route-assessment:FR-012] - Streamable HTTP transport
Verifies [cycle-route-assessment:FR-011] - Auth middleware integration
Verifies [cycle-route-assessment:NFR-005] - Backward compatible SSE support
"""

from mcp.server.lowlevel.server import Server as MCPServer
from starlette.testclient import TestClient

from src.mcp_servers.shared.transport import create_mcp_app


def _make_mcp_server(name: str = "test-server") -> MCPServer:
    """Create a minimal MCP server for testing."""
    server = MCPServer(name)

    @server.list_tools()
    async def list_tools():
        return []

    return server


class TestCreateMCPAppHealth:
    """Tests for /health endpoint."""

    def setup_method(self):
        mcp_server = _make_mcp_server()
        self.app = create_mcp_app(mcp_server, api_key=None)
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def test_health_endpoint_returns_200(self):
        """Verifies [cycle-route-assessment:create_mcp_app/TS-03].

        Given: create_mcp_app(server)
        When: GET /health
        Then: 200 OK with {"status": "ok"}
        """
        response = self.client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_health_exempt_when_auth_enabled(self):
        """Health endpoint is accessible even with auth enabled.

        Given: create_mcp_app(server, api_key="secret")
        When: GET /health without auth
        Then: 200 OK
        """
        mcp_server = _make_mcp_server()
        app = create_mcp_app(mcp_server, api_key="secret")
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/health")
        assert response.status_code == 200


class TestCreateMCPAppRoutes:
    """Tests that all expected routes exist in the app."""

    def test_sse_route_registered(self):
        """Verifies [cycle-route-assessment:create_mcp_app/TS-01].

        Given: create_mcp_app(server)
        When: Inspecting app routes
        Then: /sse route exists
        """
        mcp_server = _make_mcp_server()
        app = create_mcp_app(mcp_server, api_key=None)
        route_paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/sse" in route_paths

    def test_streamable_http_route_registered(self):
        """Verifies [cycle-route-assessment:create_mcp_app/TS-02].

        Given: create_mcp_app(server)
        When: Inspecting app routes
        Then: /mcp route exists
        """
        mcp_server = _make_mcp_server()
        app = create_mcp_app(mcp_server, api_key=None)
        route_paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/mcp" in route_paths

    def test_messages_route_registered(self):
        """Messages mount point for SSE transport exists.

        Given: create_mcp_app(server)
        When: Inspecting app routes
        Then: /messages mount exists
        """
        mcp_server = _make_mcp_server()
        app = create_mcp_app(mcp_server, api_key=None)
        route_paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/messages" in route_paths

    def test_all_four_routes_present(self):
        """App has exactly the 4 expected routes: /health, /sse, /messages, /mcp.

        Given: create_mcp_app(server)
        When: Inspecting app routes
        Then: All 4 routes present
        """
        mcp_server = _make_mcp_server()
        app = create_mcp_app(mcp_server, api_key=None)
        route_paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert route_paths == {"/health", "/sse", "/messages", "/mcp"}


class TestCreateMCPAppAuth:
    """Tests for auth middleware integration."""

    def test_auth_enforced_on_health_exempt(self):
        """Verifies [cycle-route-assessment:create_mcp_app/TS-04].

        Given: create_mcp_app(server, api_key="secret")
        When: GET /health without auth
        Then: 200 OK (health is exempt)
        """
        mcp_server = _make_mcp_server()
        app = create_mcp_app(mcp_server, api_key="secret")
        client = TestClient(app, raise_server_exceptions=False)

        # Health is exempt
        response = client.get("/health")
        assert response.status_code == 200

    def test_auth_rejects_unauthenticated_messages(self):
        """Auth blocks /messages/ when key is set.

        Given: create_mcp_app(server, api_key="secret")
        When: POST /messages/ without auth
        Then: 401 Unauthorized
        """
        mcp_server = _make_mcp_server()
        app = create_mcp_app(mcp_server, api_key="secret")
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post("/messages/")
        assert response.status_code == 401

    def test_no_auth_when_key_not_set(self):
        """No auth required when api_key is None.

        Given: create_mcp_app(server, api_key=None)
        When: GET /health, POST /messages/ without auth
        Then: Not 401 for any endpoint
        """
        mcp_server = _make_mcp_server()
        app = create_mcp_app(mcp_server, api_key=None)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/health")
        assert response.status_code != 401

        # Messages endpoint without auth should not return 401
        response = client.post("/messages/")
        assert response.status_code != 401


class TestCreateMCPAppCustomHealth:
    """Tests for custom health handler."""

    def test_custom_health_handler(self):
        """Custom health handler is used when provided.

        Given: create_mcp_app with custom health handler
        When: GET /health
        Then: Custom handler response returned
        """
        from starlette.requests import Request
        from starlette.responses import JSONResponse

        async def custom_health(request: Request) -> JSONResponse:
            return JSONResponse({"status": "ok", "service": "test-mcp"})

        mcp_server = _make_mcp_server()
        app = create_mcp_app(mcp_server, health_handler=custom_health, api_key=None)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "test-mcp"
