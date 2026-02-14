"""
MCP Client Manager for orchestrating connections to MCP servers.

Implements [agent-integration:FR-001] - Connect to MCP servers
Implements [agent-integration:NFR-005] - Reconnection on transient failure

Uses the official MCP SDK client for SSE transport.
"""

import ast
import asyncio
import json
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client

logger = structlog.get_logger(__name__)


class MCPServerType(Enum):
    """Types of MCP servers in the system."""

    CHERWELL_SCRAPER = "cherwell-scraper"
    DOCUMENT_STORE = "document-store"
    POLICY_KB = "policy-kb"
    CYCLE_ROUTE = "cycle-route"  # [cycle-route-assessment:FR-001]


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server connection."""

    server_type: MCPServerType
    base_url: str
    tools: list[str] = field(default_factory=list)

    @property
    def sse_url(self) -> str:
        """Get the SSE endpoint URL."""
        return f"{self.base_url}/sse"


@dataclass
class ConnectionState:
    """State of a server connection."""

    server_type: MCPServerType
    connected: bool = False
    last_error: str | None = None
    consecutive_failures: int = 0
    available_tools: list[str] = field(default_factory=list)


class MCPConnectionError(Exception):
    """Error connecting to an MCP server."""

    def __init__(self, server_type: MCPServerType, message: str):
        self.server_type = server_type
        self.message = message
        super().__init__(f"{server_type.value}: {message}")


class MCPToolError(Exception):
    """Error calling an MCP tool."""

    def __init__(self, tool_name: str, message: str, details: dict[str, Any] | None = None):
        self.tool_name = tool_name
        self.message = message
        self.details = details or {}
        super().__init__(f"Tool '{tool_name}' failed: {message}")


# Tool routing configuration - maps tool names to server types
TOOL_ROUTING: dict[str, MCPServerType] = {
    # Cherwell scraper tools
    "get_application_details": MCPServerType.CHERWELL_SCRAPER,
    "list_application_documents": MCPServerType.CHERWELL_SCRAPER,
    "download_document": MCPServerType.CHERWELL_SCRAPER,
    "download_all_documents": MCPServerType.CHERWELL_SCRAPER,
    # Document store tools
    "ingest_document": MCPServerType.DOCUMENT_STORE,
    "search_application_docs": MCPServerType.DOCUMENT_STORE,
    "get_document_text": MCPServerType.DOCUMENT_STORE,
    "list_ingested_documents": MCPServerType.DOCUMENT_STORE,
    # Policy KB tools
    "search_policy": MCPServerType.POLICY_KB,
    "get_policy_section": MCPServerType.POLICY_KB,
    "list_policy_documents": MCPServerType.POLICY_KB,
    "list_policy_revisions": MCPServerType.POLICY_KB,
    "ingest_policy_revision": MCPServerType.POLICY_KB,
    "remove_policy_revision": MCPServerType.POLICY_KB,
    # Cycle route tools [cycle-route-assessment:FR-001]
    "get_site_boundary": MCPServerType.CYCLE_ROUTE,
    "assess_cycle_route": MCPServerType.CYCLE_ROUTE,
}


class MCPClientManager:
    """
    Manages connections to all MCP servers using the official MCP SDK.

    Each tool call establishes a fresh SSE session for reliability.
    """

    MAX_RETRIES = 3
    INITIAL_BACKOFF_SECONDS = 1.0
    BACKOFF_MULTIPLIER = 2.0

    def __init__(
        self,
        cherwell_scraper_url: str | None = None,
        document_store_url: str | None = None,
        policy_kb_url: str | None = None,
        cycle_route_url: str | None = None,
    ) -> None:
        self._servers: dict[MCPServerType, MCPServerConfig] = {
            MCPServerType.CHERWELL_SCRAPER: MCPServerConfig(
                server_type=MCPServerType.CHERWELL_SCRAPER,
                base_url=cherwell_scraper_url or os.getenv("CHERWELL_SCRAPER_URL", "http://cherwell-scraper:3001"),
                tools=["get_application_details", "list_application_documents", "download_document", "download_all_documents"],
            ),
            MCPServerType.DOCUMENT_STORE: MCPServerConfig(
                server_type=MCPServerType.DOCUMENT_STORE,
                base_url=document_store_url or os.getenv("DOCUMENT_STORE_URL", "http://document-store:3002"),
                tools=["ingest_document", "search_application_docs", "get_document_text", "list_ingested_documents"],
            ),
            MCPServerType.POLICY_KB: MCPServerConfig(
                server_type=MCPServerType.POLICY_KB,
                base_url=policy_kb_url or os.getenv("POLICY_KB_URL", "http://policy-kb:3003"),
                tools=[
                    "search_policy", "get_policy_section", "list_policy_documents",
                    "list_policy_revisions", "ingest_policy_revision", "remove_policy_revision",
                ],
            ),
            # Implements [cycle-route-assessment:FR-001] - Cycle route MCP server
            MCPServerType.CYCLE_ROUTE: MCPServerConfig(
                server_type=MCPServerType.CYCLE_ROUTE,
                base_url=cycle_route_url or os.getenv("CYCLE_ROUTE_URL", "http://cycle-route-mcp:3004"),
                tools=["get_site_boundary", "assess_cycle_route"],
            ),
        }

        self._states: dict[MCPServerType, ConnectionState] = {
            server_type: ConnectionState(server_type=server_type) for server_type in MCPServerType
        }

        # Auth headers for MCP servers (shared MCP_API_KEY)
        mcp_api_key = os.getenv("MCP_API_KEY", "")
        self._headers: dict[str, str] | None = (
            {"Authorization": f"Bearer {mcp_api_key}"} if mcp_api_key else None
        )

    async def initialize(self) -> None:
        """Test connectivity to all MCP servers."""
        errors: list[MCPConnectionError] = []

        for server_type in MCPServerType:
            try:
                await self._check_server(server_type)
            except MCPConnectionError as e:
                errors.append(e)

        if len(errors) == len(MCPServerType):
            raise MCPConnectionError(
                MCPServerType.CHERWELL_SCRAPER,
                "All MCP servers are unavailable",
            )

        for error in errors:
            logger.warning(
                "MCP server connection failed (will retry on use)",
                server=error.server_type.value,
                error=error.message,
            )

        connected = [s.value for s in MCPServerType if self._states[s].connected]
        logger.info("MCPClientManager initialized", connected_servers=connected)

    async def _check_server(self, server_type: MCPServerType) -> None:
        """Check server connectivity by doing a quick SSE handshake."""
        config = self._servers[server_type]
        state = self._states[server_type]

        try:
            async with sse_client(config.sse_url, headers=self._headers) as (read, write), ClientSession(read, write) as session:
                    await session.initialize()
                    tools_result = await session.list_tools()
                    state.connected = True
                    state.consecutive_failures = 0
                    state.last_error = None
                    state.available_tools = [t.name for t in tools_result.tools]
                    logger.info(
                        "Connected to MCP server",
                        server=server_type.value,
                        tools=state.available_tools,
                    )
        except Exception as e:
            state.connected = False
            state.last_error = str(e)
            state.consecutive_failures += 1
            raise MCPConnectionError(server_type, str(e))

    @staticmethod
    def _parse_text_content(text: str) -> dict[str, Any]:
        """Parse text content that may be JSON or Python repr."""
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, SyntaxError):
            pass
        return {"text": text}

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """
        Call an MCP tool by name.

        Each call establishes a fresh SSE session for reliability.
        """
        server_type = TOOL_ROUTING.get(tool_name)
        if server_type is None:
            raise MCPToolError(tool_name, f"Unknown tool: {tool_name}")

        config = self._servers[server_type]
        state = self._states[server_type]
        effective_timeout = timeout or 300.0

        try:
            async with asyncio.timeout(effective_timeout):
                async with sse_client(config.sse_url, headers=self._headers) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(tool_name, arguments)

            # Parse the result content
            if result.content:
                first = result.content[0]
                if hasattr(first, "text"):
                    parsed = self._parse_text_content(first.text)
                    state.connected = True
                    state.consecutive_failures = 0
                    return parsed

            logger.warning(
                "MCP tool returned empty content",
                tool=tool_name,
                server=server_type.value,
            )
            state.connected = True
            state.consecutive_failures = 0
            return {}

        except TimeoutError:
            state.consecutive_failures += 1
            raise MCPToolError(tool_name, f"Timeout after {effective_timeout}s")
        except MCPToolError:
            raise
        except Exception as e:
            state.consecutive_failures += 1
            error_msg = str(e)
            if "connection" in error_msg.lower() or "connect" in error_msg.lower():
                state.connected = False
                raise MCPConnectionError(server_type, error_msg)
            logger.exception("MCP tool call failed", tool=tool_name, server=server_type.value)
            raise MCPToolError(tool_name, error_msg)

    async def check_health(self, server_type: MCPServerType) -> bool:
        """Check health of a specific server."""
        try:
            await self._check_server(server_type)
            return True
        except MCPConnectionError:
            return False

    async def check_all_health(self) -> dict[MCPServerType, bool]:
        """Check health of all servers."""
        return {st: await self.check_health(st) for st in MCPServerType}

    def get_available_tools(self) -> list[dict[str, Any]]:
        """Get list of all available tools across connected servers."""
        tools = []
        for server_type, state in self._states.items():
            if state.connected:
                for tool_name in state.available_tools:
                    tools.append({"name": tool_name, "server": server_type.value})
        return tools

    def is_connected(self, server_type: MCPServerType) -> bool:
        """Check if a specific server is connected."""
        return self._states[server_type].connected

    def get_connection_state(self, server_type: MCPServerType) -> ConnectionState:
        """Get the connection state for a server."""
        return self._states[server_type]

    async def close(self) -> None:
        """Close all connections gracefully."""
        for state in self._states.values():
            state.connected = False
        logger.info("MCPClientManager closed")

    async def __aenter__(self) -> "MCPClientManager":
        """Async context manager entry."""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.close()
