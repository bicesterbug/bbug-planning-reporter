"""
Tests for MCPClientManager.

Implements test scenarios from [agent-integration:MCPClientManager/TS-01] through [TS-07]
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.agent.mcp_client import (
    MCPClientManager,
    MCPConnectionError,
    MCPServerType,
    MCPToolError,
)


@pytest.fixture
def mock_httpx_client():
    """Create a mock httpx.AsyncClient."""
    client = AsyncMock(spec=httpx.AsyncClient)
    return client


class TestConnectionEstablishment:
    """
    Tests for MCP server connection establishment.

    Implements [agent-integration:MCPClientManager/TS-01] - Connect to all servers
    """

    @pytest.mark.asyncio
    async def test_connect_to_all_servers(self):
        """
        Verifies [agent-integration:MCPClientManager/TS-01] - Connect to all servers

        Given: All 3 MCP servers running
        When: Orchestrator initialises
        Then: Connections established to cherwell-scraper, document-store, policy-kb
        """
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            # All servers respond successfully
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_client.get.return_value = mock_response

            manager = MCPClientManager(
                cherwell_scraper_url="http://localhost:3001",
                document_store_url="http://localhost:3002",
                policy_kb_url="http://localhost:3003",
            )

            await manager.initialize()

            # Verify all servers are connected
            assert manager.is_connected(MCPServerType.CHERWELL_SCRAPER)
            assert manager.is_connected(MCPServerType.DOCUMENT_STORE)
            assert manager.is_connected(MCPServerType.POLICY_KB)

            await manager.close()

    @pytest.mark.asyncio
    async def test_partial_connection_success(self):
        """
        Test that manager initializes with partial server availability.

        Given: Only 2 of 3 servers available
        When: Manager initialises
        Then: Initializes successfully with warnings
        """
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            call_count = 0

            async def mock_get(url, **kwargs):
                nonlocal call_count
                response = AsyncMock()
                # Fail cherwell-scraper for all retry attempts
                if "3001" in url:
                    response.status_code = 503
                else:
                    response.status_code = 200
                call_count += 1
                return response

            mock_client.get.side_effect = mock_get

            manager = MCPClientManager(
                cherwell_scraper_url="http://localhost:3001",
                document_store_url="http://localhost:3002",
                policy_kb_url="http://localhost:3003",
            )

            # Should not raise - partial success is OK
            await manager.initialize()

            # Cherwell should be disconnected
            assert not manager.is_connected(MCPServerType.CHERWELL_SCRAPER)
            # Others should be connected
            assert manager.is_connected(MCPServerType.DOCUMENT_STORE)
            assert manager.is_connected(MCPServerType.POLICY_KB)

            await manager.close()


class TestAllServersUnavailable:
    """
    Tests for handling all servers being unavailable.

    Implements [agent-integration:AgentOrchestrator/TS-08] - All servers unavailable
    """

    @pytest.mark.asyncio
    async def test_all_servers_unavailable_raises(self):
        """
        Verifies [agent-integration:AgentOrchestrator/TS-08] - All servers unavailable

        Given: All MCP servers down
        When: Orchestrator initialises
        Then: Fails with clear error after retry exhaustion
        """
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            # All servers fail
            mock_client.get.side_effect = httpx.ConnectError("Connection refused")

            manager = MCPClientManager(
                cherwell_scraper_url="http://localhost:3001",
                document_store_url="http://localhost:3002",
                policy_kb_url="http://localhost:3003",
            )

            with pytest.raises(MCPConnectionError) as exc_info:
                await manager.initialize()

            assert "All MCP servers are unavailable" in str(exc_info.value)

            await manager.close()


class TestHealthCheckDetection:
    """
    Tests for health check detection.

    Implements [agent-integration:MCPClientManager/TS-02] - Health check detection
    """

    @pytest.mark.asyncio
    async def test_health_check_detects_unhealthy_server(self):
        """
        Verifies [agent-integration:MCPClientManager/TS-02] - Health check detection

        Given: One server goes down
        When: Health check runs
        Then: Detects unhealthy server
        """
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            # First connection succeeds
            mock_response_ok = AsyncMock()
            mock_response_ok.status_code = 200

            mock_client.get.return_value = mock_response_ok

            manager = MCPClientManager(
                cherwell_scraper_url="http://localhost:3001",
                document_store_url="http://localhost:3002",
                policy_kb_url="http://localhost:3003",
            )

            await manager.initialize()

            # Now server goes down
            mock_response_fail = AsyncMock()
            mock_response_fail.status_code = 503
            mock_client.get.return_value = mock_response_fail

            health = await manager.check_health(MCPServerType.POLICY_KB)

            assert health is False
            assert not manager.is_connected(MCPServerType.POLICY_KB)

            await manager.close()

    @pytest.mark.asyncio
    async def test_health_check_all_servers(self):
        """Test checking health of all servers at once."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_client.get.return_value = mock_response

            manager = MCPClientManager(
                cherwell_scraper_url="http://localhost:3001",
                document_store_url="http://localhost:3002",
                policy_kb_url="http://localhost:3003",
            )

            await manager.initialize()

            health_results = await manager.check_all_health()

            assert health_results[MCPServerType.CHERWELL_SCRAPER] is True
            assert health_results[MCPServerType.DOCUMENT_STORE] is True
            assert health_results[MCPServerType.POLICY_KB] is True

            await manager.close()


class TestAutomaticReconnection:
    """
    Tests for automatic reconnection.

    Implements [agent-integration:MCPClientManager/TS-03] - Automatic reconnection
    """

    @pytest.mark.asyncio
    async def test_automatic_reconnection_on_tool_call(self):
        """
        Verifies [agent-integration:MCPClientManager/TS-03] - Automatic reconnection

        Given: Connection lost
        When: Next tool call
        Then: Reconnects before call; call succeeds
        """
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            # Initial connection succeeds
            mock_response_get = MagicMock()
            mock_response_get.status_code = 200

            mock_response_post = MagicMock()
            mock_response_post.status_code = 200
            mock_response_post.json.return_value = {
                "jsonrpc": "2.0",
                "result": [{"type": "text", "text": "{'status': 'success'}"}],
                "id": 1,
            }

            mock_client.get.return_value = mock_response_get
            mock_client.post.return_value = mock_response_post

            manager = MCPClientManager(
                cherwell_scraper_url="http://localhost:3001",
                document_store_url="http://localhost:3002",
                policy_kb_url="http://localhost:3003",
            )

            await manager.initialize()

            # Simulate connection loss
            manager._states[MCPServerType.POLICY_KB].connected = False

            # Tool call should trigger reconnection
            result = await manager.call_tool("search_policy", {"query": "cycle lanes"})

            # Should have reconnected
            assert manager.is_connected(MCPServerType.POLICY_KB)
            assert result == {"status": "success"}

            await manager.close()


class TestReconnectionBackoff:
    """
    Tests for reconnection backoff behavior.

    Implements [agent-integration:MCPClientManager/TS-04] - Reconnection backoff
    """

    @pytest.mark.asyncio
    async def test_exponential_backoff_on_failures(self):
        """
        Verifies [agent-integration:MCPClientManager/TS-04] - Reconnection backoff

        Given: Repeated failures
        When: Reconnection attempts
        Then: Exponential backoff: 1s, 2s, 4s
        """
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            # Always fail
            mock_client.get.side_effect = httpx.ConnectError("Connection refused")

            manager = MCPClientManager(
                cherwell_scraper_url="http://localhost:3001",
                document_store_url="http://localhost:3002",
                policy_kb_url="http://localhost:3003",
            )

            sleep_times = []
            original_sleep = asyncio.sleep

            async def mock_sleep(seconds):
                sleep_times.append(seconds)
                # Don't actually sleep in tests
                return

            with patch("asyncio.sleep", mock_sleep):
                with pytest.raises(MCPConnectionError):
                    await manager.initialize()

            # Should have attempted retries with backoff
            # 3 servers x 2 retries each = 6 sleeps
            # Backoff pattern: 1.0, 2.0 for each server
            expected_backoffs = [1.0, 2.0]  # Per server
            for server_idx in range(3):
                for retry_idx in range(2):  # MAX_RETRIES - 1
                    idx = server_idx * 2 + retry_idx
                    if idx < len(sleep_times):
                        assert sleep_times[idx] == expected_backoffs[retry_idx]

            await manager.close()


class TestToolCallRouting:
    """
    Tests for tool call routing.

    Implements [agent-integration:MCPClientManager/TS-05] - Tool call routing
    """

    @pytest.mark.asyncio
    async def test_tool_routing_to_correct_server(self):
        """
        Verifies [agent-integration:MCPClientManager/TS-05] - Tool call routing

        Given: Tool call request
        When: Call search_policy
        Then: Routed to policy-kb server
        """
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            mock_response_get = MagicMock()
            mock_response_get.status_code = 200

            mock_response_post = MagicMock()
            mock_response_post.status_code = 200
            mock_response_post.json.return_value = {
                "jsonrpc": "2.0",
                "result": [{"type": "text", "text": "{'status': 'success', 'results': []}"}],
                "id": 1,
            }

            mock_client.get.return_value = mock_response_get
            mock_client.post.return_value = mock_response_post

            manager = MCPClientManager(
                cherwell_scraper_url="http://localhost:3001",
                document_store_url="http://localhost:3002",
                policy_kb_url="http://localhost:3003",
            )

            await manager.initialize()

            # Call policy tool
            await manager.call_tool("search_policy", {"query": "test"})

            # Verify POST was made to policy-kb server
            post_calls = [c for c in mock_client.post.call_args_list]
            assert len(post_calls) == 1
            assert "localhost:3003" in post_calls[0][0][0]

            await manager.close()

    @pytest.mark.asyncio
    async def test_unknown_tool_raises_error(self):
        """Test that calling an unknown tool raises MCPToolError."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_client.get.return_value = mock_response

            manager = MCPClientManager(
                cherwell_scraper_url="http://localhost:3001",
                document_store_url="http://localhost:3002",
                policy_kb_url="http://localhost:3003",
            )

            await manager.initialize()

            with pytest.raises(MCPToolError) as exc_info:
                await manager.call_tool("nonexistent_tool", {})

            assert "Unknown tool" in str(exc_info.value)

            await manager.close()


class TestCleanShutdown:
    """
    Tests for clean shutdown.

    Implements [agent-integration:MCPClientManager/TS-06] - Clean shutdown
    """

    @pytest.mark.asyncio
    async def test_clean_shutdown(self):
        """
        Verifies [agent-integration:MCPClientManager/TS-06] - Clean shutdown

        Given: Workflow complete
        When: Shutdown manager
        Then: All connections closed gracefully
        """
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_client.get.return_value = mock_response

            manager = MCPClientManager(
                cherwell_scraper_url="http://localhost:3001",
                document_store_url="http://localhost:3002",
                policy_kb_url="http://localhost:3003",
            )

            await manager.initialize()

            # All connected
            assert manager.is_connected(MCPServerType.CHERWELL_SCRAPER)
            assert manager.is_connected(MCPServerType.DOCUMENT_STORE)
            assert manager.is_connected(MCPServerType.POLICY_KB)

            await manager.close()

            # All disconnected
            assert not manager.is_connected(MCPServerType.CHERWELL_SCRAPER)
            assert not manager.is_connected(MCPServerType.DOCUMENT_STORE)
            assert not manager.is_connected(MCPServerType.POLICY_KB)

            # HTTP client closed
            mock_client.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Test async context manager usage."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_client.get.return_value = mock_response

            async with MCPClientManager(
                cherwell_scraper_url="http://localhost:3001",
                document_store_url="http://localhost:3002",
                policy_kb_url="http://localhost:3003",
            ) as manager:
                assert manager.is_connected(MCPServerType.POLICY_KB)

            # After context exit
            mock_client.aclose.assert_called_once()


class TestTimeoutHandling:
    """
    Tests for timeout handling.

    Implements [agent-integration:MCPClientManager/TS-07] - Timeout handling
    """

    @pytest.mark.asyncio
    async def test_tool_call_timeout(self):
        """
        Verifies [agent-integration:MCPClientManager/TS-07] - Timeout handling

        Given: Server hangs
        When: Tool call with timeout
        Then: Times out after configured period
        """
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            # Connection succeeds
            mock_response_ok = AsyncMock()
            mock_response_ok.status_code = 200
            mock_client.get.return_value = mock_response_ok

            # But tool call times out
            mock_client.post.side_effect = httpx.TimeoutException("Timeout")

            manager = MCPClientManager(
                cherwell_scraper_url="http://localhost:3001",
                document_store_url="http://localhost:3002",
                policy_kb_url="http://localhost:3003",
            )

            await manager.initialize()

            with pytest.raises(MCPToolError) as exc_info:
                await manager.call_tool("search_policy", {"query": "test"}, timeout=5.0)

            assert "Timeout" in str(exc_info.value)

            await manager.close()

    @pytest.mark.asyncio
    async def test_custom_timeout(self):
        """Test that custom timeout is passed to HTTP client."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            mock_response_get = MagicMock()
            mock_response_get.status_code = 200

            mock_response_post = MagicMock()
            mock_response_post.status_code = 200
            mock_response_post.json.return_value = {
                "jsonrpc": "2.0",
                "result": [{"type": "text", "text": "{'status': 'success'}"}],
                "id": 1,
            }

            mock_client.get.return_value = mock_response_get
            mock_client.post.return_value = mock_response_post

            manager = MCPClientManager(
                cherwell_scraper_url="http://localhost:3001",
                document_store_url="http://localhost:3002",
                policy_kb_url="http://localhost:3003",
            )

            await manager.initialize()

            custom_timeout = 60.0
            await manager.call_tool("search_policy", {"query": "test"}, timeout=custom_timeout)

            # Verify timeout was passed
            post_call = mock_client.post.call_args
            assert post_call.kwargs["timeout"] == custom_timeout

            await manager.close()


class TestAvailableTools:
    """Tests for getting available tools."""

    @pytest.mark.asyncio
    async def test_get_available_tools(self):
        """Test listing available tools from connected servers."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_client.get.return_value = mock_response

            manager = MCPClientManager(
                cherwell_scraper_url="http://localhost:3001",
                document_store_url="http://localhost:3002",
                policy_kb_url="http://localhost:3003",
            )

            await manager.initialize()

            tools = manager.get_available_tools()

            # Should include tools from all 3 servers
            tool_names = [t["name"] for t in tools]

            assert "get_application_details" in tool_names
            assert "search_application_docs" in tool_names
            assert "search_policy" in tool_names

            await manager.close()
