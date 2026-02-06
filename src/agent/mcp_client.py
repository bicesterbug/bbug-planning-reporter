"""
MCP Client Manager for orchestrating connections to MCP servers.

Implements [agent-integration:FR-001] - Connect to MCP servers
Implements [agent-integration:NFR-005] - Reconnection on transient failure

Implements:
- [agent-integration:MCPClientManager/TS-01] Connect to all servers
- [agent-integration:MCPClientManager/TS-02] Health check detection
- [agent-integration:MCPClientManager/TS-03] Automatic reconnection
- [agent-integration:MCPClientManager/TS-04] Reconnection backoff
- [agent-integration:MCPClientManager/TS-05] Tool call routing
- [agent-integration:MCPClientManager/TS-06] Clean shutdown
- [agent-integration:MCPClientManager/TS-07] Timeout handling
"""

import asyncio
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)


class MCPServerType(Enum):
    """Types of MCP servers in the system."""

    CHERWELL_SCRAPER = "cherwell-scraper"
    DOCUMENT_STORE = "document-store"
    POLICY_KB = "policy-kb"


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

    @property
    def messages_url(self) -> str:
        """Get the messages endpoint URL."""
        return f"{self.base_url}/messages/"


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
    "download_all_documents": MCPServerType.CHERWELL_SCRAPER,
    # Document store tools
    "ingest_document": MCPServerType.DOCUMENT_STORE,
    "search_application_docs": MCPServerType.DOCUMENT_STORE,
    "get_document_text": MCPServerType.DOCUMENT_STORE,
    # Policy KB tools
    "search_policy": MCPServerType.POLICY_KB,
    "get_policy_section": MCPServerType.POLICY_KB,
    "list_policy_documents": MCPServerType.POLICY_KB,
    "list_policy_revisions": MCPServerType.POLICY_KB,
    "ingest_policy_revision": MCPServerType.POLICY_KB,
    "remove_policy_revision": MCPServerType.POLICY_KB,
}


class MCPClientManager:
    """
    Manages connections to all MCP servers.

    Implements [agent-integration:MCPClientManager/TS-01] through [TS-07]

    Handles:
    - Connection establishment to multiple servers
    - Health monitoring and automatic reconnection
    - Exponential backoff on failures
    - Tool call routing to appropriate servers
    - Graceful shutdown
    """

    # Backoff configuration
    MAX_RETRIES = 3
    INITIAL_BACKOFF_SECONDS = 1.0
    MAX_BACKOFF_SECONDS = 8.0
    BACKOFF_MULTIPLIER = 2.0

    # Timeout configuration
    DEFAULT_TIMEOUT_SECONDS = 30.0
    CONNECT_TIMEOUT_SECONDS = 10.0

    def __init__(
        self,
        cherwell_scraper_url: str | None = None,
        document_store_url: str | None = None,
        policy_kb_url: str | None = None,
    ) -> None:
        """
        Initialize the MCP client manager.

        Args:
            cherwell_scraper_url: URL for cherwell-scraper MCP server.
            document_store_url: URL for document-store MCP server.
            policy_kb_url: URL for policy-kb MCP server.
        """
        # Server configurations from env vars or parameters
        self._servers: dict[MCPServerType, MCPServerConfig] = {
            MCPServerType.CHERWELL_SCRAPER: MCPServerConfig(
                server_type=MCPServerType.CHERWELL_SCRAPER,
                base_url=cherwell_scraper_url or os.getenv("CHERWELL_SCRAPER_URL", "http://cherwell-scraper:3001"),
                tools=["get_application_details", "download_all_documents"],
            ),
            MCPServerType.DOCUMENT_STORE: MCPServerConfig(
                server_type=MCPServerType.DOCUMENT_STORE,
                base_url=document_store_url or os.getenv("DOCUMENT_STORE_URL", "http://document-store:3002"),
                tools=["ingest_document", "search_application_docs", "get_document_text"],
            ),
            MCPServerType.POLICY_KB: MCPServerConfig(
                server_type=MCPServerType.POLICY_KB,
                base_url=policy_kb_url or os.getenv("POLICY_KB_URL", "http://policy-kb:3003"),
                tools=[
                    "search_policy",
                    "get_policy_section",
                    "list_policy_documents",
                    "list_policy_revisions",
                    "ingest_policy_revision",
                    "remove_policy_revision",
                ],
            ),
        }

        # Connection states
        self._states: dict[MCPServerType, ConnectionState] = {
            server_type: ConnectionState(server_type=server_type) for server_type in MCPServerType
        }

        # HTTP client for MCP calls
        self._client: httpx.AsyncClient | None = None

        # Session IDs for each server (obtained from SSE handshake)
        self._session_ids: dict[MCPServerType, str | None] = {s: None for s in MCPServerType}

    async def initialize(self) -> None:
        """
        Initialize connections to all MCP servers.

        Implements [agent-integration:MCPClientManager/TS-01] - Connect to all servers

        Raises:
            MCPConnectionError: If unable to connect to any required server after retries.
        """
        # Create HTTP client
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=self.CONNECT_TIMEOUT_SECONDS,
                read=self.DEFAULT_TIMEOUT_SECONDS,
                write=self.DEFAULT_TIMEOUT_SECONDS,
                pool=self.DEFAULT_TIMEOUT_SECONDS,
            ),
        )

        # Attempt to connect to all servers
        errors: list[MCPConnectionError] = []

        for server_type in MCPServerType:
            try:
                await self._connect_server(server_type)
            except MCPConnectionError as e:
                errors.append(e)

        # If all servers failed, raise an error
        if len(errors) == len(MCPServerType):
            logger.error(
                "All MCP servers unavailable",
                errors=[str(e) for e in errors],
            )
            raise MCPConnectionError(
                MCPServerType.CHERWELL_SCRAPER,  # Arbitrary choice for the error
                "All MCP servers are unavailable",
            )

        # Log partial failures as warnings
        for error in errors:
            logger.warning(
                "MCP server connection failed (will retry on use)",
                server=error.server_type.value,
                error=error.message,
            )

        connected_servers = [s.value for s in MCPServerType if self._states[s].connected]
        logger.info(
            "MCPClientManager initialized",
            connected_servers=connected_servers,
            failed_servers=[e.server_type.value for e in errors],
        )

    async def _connect_server(self, server_type: MCPServerType) -> None:
        """
        Connect to a single MCP server.

        Implements [agent-integration:MCPClientManager/TS-01] - Connect to all servers
        Implements [agent-integration:MCPClientManager/TS-04] - Reconnection backoff

        Args:
            server_type: The server type to connect to.

        Raises:
            MCPConnectionError: If connection fails after retries.
        """
        config = self._servers[server_type]
        state = self._states[server_type]

        backoff = self.INITIAL_BACKOFF_SECONDS

        for attempt in range(self.MAX_RETRIES):
            try:
                # Health check via HTTP GET to base URL
                assert self._client is not None

                # Try to reach the SSE endpoint (it should accept connections)
                # We do a simple GET to check server is responding
                response = await self._client.get(
                    config.sse_url,
                    headers={"Accept": "text/event-stream"},
                    timeout=self.CONNECT_TIMEOUT_SECONDS,
                )

                # SSE endpoint returns 200 and starts streaming
                # We just check it's reachable (any 2xx is good)
                if response.status_code < 300:
                    state.connected = True
                    state.consecutive_failures = 0
                    state.last_error = None
                    state.available_tools = config.tools

                    logger.info(
                        "Connected to MCP server",
                        server=server_type.value,
                        url=config.base_url,
                        tools=config.tools,
                    )
                    return

                # Non-success status
                state.last_error = f"HTTP {response.status_code}"

            except httpx.TimeoutException:
                state.last_error = "Connection timeout"
            except httpx.ConnectError as e:
                state.last_error = f"Connection error: {e}"
            except Exception as e:
                state.last_error = str(e)

            state.consecutive_failures += 1

            if attempt < self.MAX_RETRIES - 1:
                logger.debug(
                    "MCP server connection attempt failed, retrying",
                    server=server_type.value,
                    attempt=attempt + 1,
                    backoff_seconds=backoff,
                    error=state.last_error,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * self.BACKOFF_MULTIPLIER, self.MAX_BACKOFF_SECONDS)

        # All retries exhausted
        state.connected = False
        raise MCPConnectionError(server_type, state.last_error or "Unknown error")

    async def _ensure_connected(self, server_type: MCPServerType) -> None:
        """
        Ensure a server is connected, reconnecting if necessary.

        Implements [agent-integration:MCPClientManager/TS-03] - Automatic reconnection

        Args:
            server_type: The server type to ensure connection for.

        Raises:
            MCPConnectionError: If reconnection fails.
        """
        state = self._states[server_type]

        if state.connected:
            return

        logger.info(
            "Reconnecting to MCP server",
            server=server_type.value,
        )
        await self._connect_server(server_type)

    async def check_health(self, server_type: MCPServerType) -> bool:
        """
        Check health of a specific server.

        Implements [agent-integration:MCPClientManager/TS-02] - Health check detection

        Args:
            server_type: The server type to check.

        Returns:
            True if server is healthy, False otherwise.
        """
        config = self._servers[server_type]
        state = self._states[server_type]

        try:
            assert self._client is not None
            response = await self._client.get(
                config.base_url,
                timeout=self.CONNECT_TIMEOUT_SECONDS,
            )
            healthy = response.status_code < 500
            state.connected = healthy
            if not healthy:
                state.last_error = f"HTTP {response.status_code}"
            return healthy

        except Exception as e:
            state.connected = False
            state.last_error = str(e)
            return False

    async def check_all_health(self) -> dict[MCPServerType, bool]:
        """
        Check health of all servers.

        Returns:
            Dict mapping server types to health status.
        """
        results = {}
        for server_type in MCPServerType:
            results[server_type] = await self.check_health(server_type)
        return results

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """
        Call an MCP tool by name.

        Implements [agent-integration:MCPClientManager/TS-05] - Tool call routing
        Implements [agent-integration:MCPClientManager/TS-07] - Timeout handling

        Args:
            tool_name: Name of the tool to call.
            arguments: Tool arguments.
            timeout: Optional timeout override.

        Returns:
            Tool result as a dictionary.

        Raises:
            MCPToolError: If the tool call fails.
            MCPConnectionError: If unable to reach the server.
        """
        # Route tool to appropriate server
        server_type = TOOL_ROUTING.get(tool_name)
        if server_type is None:
            raise MCPToolError(tool_name, f"Unknown tool: {tool_name}")

        # Ensure connection
        await self._ensure_connected(server_type)

        config = self._servers[server_type]
        effective_timeout = timeout or self.DEFAULT_TIMEOUT_SECONDS

        try:
            assert self._client is not None

            # Call tool via MCP messages endpoint
            response = await self._client.post(
                config.messages_url,
                json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {
                        "name": tool_name,
                        "arguments": arguments,
                    },
                    "id": 1,
                },
                timeout=effective_timeout,
            )

            if response.status_code != 200:
                # Server error - mark as potentially disconnected
                if response.status_code >= 500:
                    self._states[server_type].connected = False
                raise MCPToolError(
                    tool_name,
                    f"HTTP {response.status_code}",
                    {"response_body": response.text[:500]},
                )

            result = response.json()

            # Check for JSON-RPC error
            if "error" in result:
                error_data = result["error"]
                raise MCPToolError(
                    tool_name,
                    error_data.get("message", "Unknown error"),
                    error_data.get("data"),
                )

            # Extract result
            tool_result = result.get("result", {})

            # Parse TextContent if present
            if isinstance(tool_result, list) and tool_result:
                # MCP tools return list of TextContent
                content = tool_result[0]
                if isinstance(content, dict) and content.get("type") == "text":
                    import ast

                    try:
                        return ast.literal_eval(content["text"])
                    except (ValueError, SyntaxError):
                        return {"text": content["text"]}

            return tool_result

        except httpx.TimeoutException:
            logger.warning(
                "MCP tool call timeout",
                tool=tool_name,
                server=server_type.value,
                timeout=effective_timeout,
            )
            raise MCPToolError(
                tool_name,
                f"Timeout after {effective_timeout}s",
                {"server": server_type.value},
            )
        except httpx.ConnectError as e:
            self._states[server_type].connected = False
            raise MCPConnectionError(server_type, str(e))
        except MCPToolError:
            raise
        except MCPConnectionError:
            raise
        except Exception as e:
            logger.exception(
                "Unexpected error calling MCP tool",
                tool=tool_name,
                server=server_type.value,
            )
            raise MCPToolError(tool_name, str(e))

    def get_available_tools(self) -> list[dict[str, Any]]:
        """
        Get list of all available tools across connected servers.

        Returns:
            List of tool definitions.
        """
        tools = []
        for server_type, state in self._states.items():
            if state.connected:
                config = self._servers[server_type]
                for tool_name in config.tools:
                    tools.append({
                        "name": tool_name,
                        "server": server_type.value,
                    })
        return tools

    def is_connected(self, server_type: MCPServerType) -> bool:
        """Check if a specific server is connected."""
        return self._states[server_type].connected

    def get_connection_state(self, server_type: MCPServerType) -> ConnectionState:
        """Get the connection state for a server."""
        return self._states[server_type]

    async def close(self) -> None:
        """
        Close all connections gracefully.

        Implements [agent-integration:MCPClientManager/TS-06] - Clean shutdown
        """
        if self._client:
            await self._client.aclose()
            self._client = None

        # Reset all states
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
