"""
Shared transport setup for MCP servers.

Creates a Starlette app with SSE + Streamable HTTP dual transport,
health endpoint, and optional auth middleware.

Implements [cycle-route-assessment:FR-012] - Streamable HTTP transport
Implements [cycle-route-assessment:FR-011] - Auth middleware integration
Implements [cycle-route-assessment:NFR-005] - Backward compatible SSE support

Implements test scenarios:
- [cycle-route-assessment:create_mcp_app/TS-01] App has SSE endpoint
- [cycle-route-assessment:create_mcp_app/TS-02] App has Streamable HTTP endpoint
- [cycle-route-assessment:create_mcp_app/TS-03] App has health endpoint
- [cycle-route-assessment:create_mcp_app/TS-04] Auth middleware attached when key set
"""

import contextlib
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

import structlog
from mcp.server.lowlevel.server import Server as MCPServer
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from src.mcp_servers.shared.auth import MCPAuthMiddleware

logger = structlog.get_logger(__name__)


# Type for optional custom health handler
HealthHandler = Callable[[Request], Coroutine[Any, Any, Response]]


async def _default_health_handler(request: Request) -> JSONResponse:  # noqa: ARG001
    """Default health endpoint returning {"status": "ok"}."""
    return JSONResponse({"status": "ok"})


def create_mcp_app(
    mcp_server: MCPServer,
    *,
    health_handler: HealthHandler | None = None,
    api_key: str | None = None,
) -> Starlette:
    """
    Create a Starlette app with SSE + Streamable HTTP transport, health, and auth.

    Args:
        mcp_server: The MCP server instance (low-level Server).
        health_handler: Optional custom health check handler. Defaults to
                        returning {"status": "ok"}.
        api_key: Optional bearer token for auth. If None, reads MCP_API_KEY
                 from environment. Pass empty string to explicitly disable.

    Returns:
        Configured Starlette application with:
        - /health - Health endpoint (always unauthenticated)
        - /sse - SSE transport endpoint (legacy, for internal worker)
        - /messages/ - SSE message posting endpoint
        - /mcp - Streamable HTTP transport endpoint (current standard)
    """
    health = health_handler or _default_health_handler

    # SSE transport (legacy, backward compatible)
    sse = SseServerTransport("/messages/")

    # Streamable HTTP transport (current MCP standard)
    session_manager = StreamableHTTPSessionManager(mcp_server)

    async def handle_sse(request: Request) -> None:
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await mcp_server.run(
                streams[0], streams[1], mcp_server.create_initialization_options()
            )

    async def handle_streamable_http(request: Request) -> None:
        await session_manager.handle_request(
            request.scope, request.receive, request._send
        )

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:  # noqa: ARG001
        async with session_manager.run():
            yield

    routes = [
        Route("/health", endpoint=health),
        Route("/sse", endpoint=handle_sse),
        Mount("/messages", app=sse.handle_post_message),
        Route("/mcp", endpoint=handle_streamable_http, methods=["GET", "POST", "DELETE"]),
    ]

    app = Starlette(routes=routes, lifespan=lifespan)

    # Add auth middleware (no-op when api_key is None/empty or MCP_API_KEY unset)
    app.add_middleware(MCPAuthMiddleware, api_key=api_key)

    return app
