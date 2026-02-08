"""
Tests for ClaudeClient.

Implements test scenarios from [agent-integration:ClaudeClient/TS-01] through [TS-07]
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.claude_client import (
    CONTEXT_WINDOW_SIZE,
    ClaudeClient,
    ClaudeClientError,
    ClaudeResponse,
    TokenUsage,
    ToolCall,
)


@pytest.fixture
def mock_anthropic():
    """Create mock Anthropic client."""
    with patch("src.agent.claude_client.AsyncAnthropic") as mock_class:
        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_class.return_value = mock_client
        yield mock_client


@pytest.fixture
def client(mock_anthropic):
    """Create ClaudeClient with mocked Anthropic."""
    return ClaudeClient(api_key="test-api-key")


class TestSuccessfulCompletion:
    """
    Tests for successful message completion.

    Implements [agent-integration:ClaudeClient/TS-01] - Successful completion
    """

    @pytest.mark.asyncio
    async def test_successful_completion(self, client, mock_anthropic):
        """
        Verifies [agent-integration:ClaudeClient/TS-01] - Successful completion

        Given: Valid prompt
        When: Send message
        Then: Response returned; tokens tracked
        """
        # Mock response
        mock_usage = MagicMock()
        mock_usage.input_tokens = 100
        mock_usage.output_tokens = 50

        mock_content = MagicMock()
        mock_content.type = "text"
        mock_content.text = "This is the response"

        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_response.stop_reason = "end_turn"
        mock_response.usage = mock_usage

        mock_anthropic.messages.create = AsyncMock(return_value=mock_response)

        response = await client.send_message(
            messages=[{"role": "user", "content": "Hello"}],
        )

        assert response.content == "This is the response"
        assert response.stop_reason == "end_turn"
        assert response.input_tokens == 100
        assert response.output_tokens == 50

        # Verify tokens were tracked
        assert client.token_usage.input_tokens == 100
        assert client.token_usage.output_tokens == 50
        assert client.token_usage.total_tokens == 150


class TestToolUseHandling:
    """
    Tests for tool use handling.

    Implements [agent-integration:ClaudeClient/TS-02] - Tool use handling
    """

    @pytest.mark.asyncio
    async def test_tool_use_handling(self, client, mock_anthropic):
        """
        Verifies [agent-integration:ClaudeClient/TS-02] - Tool use handling

        Given: Response includes tool_use
        When: Process response
        Then: Tool calls extracted and returned
        """
        # Mock response with tool use
        mock_usage = MagicMock()
        mock_usage.input_tokens = 200
        mock_usage.output_tokens = 100

        mock_text_content = MagicMock()
        mock_text_content.type = "text"
        mock_text_content.text = "I'll search for that."

        mock_tool_content = MagicMock()
        mock_tool_content.type = "tool_use"
        mock_tool_content.id = "tool_123"
        mock_tool_content.name = "search_policy"
        mock_tool_content.input = {"query": "cycle lane width"}

        mock_response = MagicMock()
        mock_response.content = [mock_text_content, mock_tool_content]
        mock_response.stop_reason = "tool_use"
        mock_response.usage = mock_usage

        mock_anthropic.messages.create = AsyncMock(return_value=mock_response)

        response = await client.send_message(
            messages=[{"role": "user", "content": "Search policy"}],
            tools=[{
                "name": "search_policy",
                "description": "Search policy documents",
                "input_schema": {"type": "object"},
            }],
        )

        assert response.has_tool_calls is True
        assert len(response.tool_calls) == 1

        tool_call = response.tool_calls[0]
        assert tool_call.id == "tool_123"
        assert tool_call.name == "search_policy"
        assert tool_call.input == {"query": "cycle lane width"}

    def test_build_tool_result_message(self, client):
        """Test building tool result messages."""
        result = client.build_tool_result_message(
            tool_use_id="tool_123",
            result={"status": "success", "data": []},
        )

        assert result["role"] == "user"
        assert result["content"][0]["type"] == "tool_result"
        assert result["content"][0]["tool_use_id"] == "tool_123"
        assert result["content"][0]["is_error"] is False

    def test_build_tool_result_message_error(self, client):
        """Test building error tool result messages."""
        result = client.build_tool_result_message(
            tool_use_id="tool_123",
            result="Tool execution failed",
            is_error=True,
        )

        assert result["content"][0]["is_error"] is True


class TestRateLimitHandling:
    """
    Tests for rate limit handling.

    Implements [agent-integration:ClaudeClient/TS-04] - Rate limit handling
    """

    @pytest.mark.asyncio
    async def test_rate_limit_handling(self, client, mock_anthropic):
        """
        Verifies [agent-integration:ClaudeClient/TS-04] - Rate limit handling

        Given: 429 response
        When: Send message
        Then: Waits per retry-after; retries
        """
        from anthropic import RateLimitError

        # First call rate limited, second succeeds
        mock_error = RateLimitError.__new__(RateLimitError)
        mock_error.status_code = 429
        mock_error.response = MagicMock()
        mock_error.response.headers = {"retry-after": "1"}

        mock_usage = MagicMock()
        mock_usage.input_tokens = 100
        mock_usage.output_tokens = 50

        mock_content = MagicMock()
        mock_content.type = "text"
        mock_content.text = "Success after retry"

        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_response.stop_reason = "end_turn"
        mock_response.usage = mock_usage

        mock_anthropic.messages.create = AsyncMock(
            side_effect=[mock_error, mock_response]
        )

        sleep_times = []

        async def mock_sleep(seconds):
            sleep_times.append(seconds)

        with patch("asyncio.sleep", mock_sleep):
            response = await client.send_message(
                messages=[{"role": "user", "content": "Hello"}],
            )

        assert response.content == "Success after retry"
        assert len(sleep_times) >= 1
        assert sleep_times[0] == 1.0  # retry-after header value


class TestTransientErrorRetry:
    """
    Tests for transient error retry.

    Implements [agent-integration:ClaudeClient/TS-05] - Transient error retry
    """

    @pytest.mark.asyncio
    async def test_transient_error_retry(self, client, mock_anthropic):
        """
        Verifies [agent-integration:ClaudeClient/TS-05] - Transient error retry

        Given: 503 response
        When: Send message
        Then: Retries with backoff; succeeds
        """
        from anthropic import APIError

        # First call fails with 503, second succeeds
        mock_error = APIError.__new__(APIError)
        mock_error.status_code = 503
        mock_error.message = "Service unavailable"

        mock_usage = MagicMock()
        mock_usage.input_tokens = 100
        mock_usage.output_tokens = 50

        mock_content = MagicMock()
        mock_content.type = "text"
        mock_content.text = "Success after retry"

        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_response.stop_reason = "end_turn"
        mock_response.usage = mock_usage

        mock_anthropic.messages.create = AsyncMock(
            side_effect=[mock_error, mock_response]
        )

        sleep_times = []

        async def mock_sleep(seconds):
            sleep_times.append(seconds)

        with patch("asyncio.sleep", mock_sleep):
            response = await client.send_message(
                messages=[{"role": "user", "content": "Hello"}],
            )

        assert response.content == "Success after retry"
        assert len(sleep_times) >= 1

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self, client, mock_anthropic):
        """Test that max retries raises error."""
        from anthropic import APIStatusError

        # Create a proper exception that can be raised
        class MockAPIError(APIStatusError):
            def __init__(self):
                self.status_code = 503
                self.message = "Service unavailable"

            def __str__(self):
                return "Service unavailable"

        mock_anthropic.messages.create = AsyncMock(side_effect=MockAPIError())

        async def mock_sleep(seconds):
            pass

        with patch("asyncio.sleep", mock_sleep), pytest.raises(ClaudeClientError) as exc_info:
                await client.send_message(
                    messages=[{"role": "user", "content": "Hello"}],
                )

        # After exhausting retries on 5xx, the last error is raised
        assert "Service unavailable" in str(exc_info.value)


class TestTokenUsageTracking:
    """
    Tests for token usage tracking.

    Implements [agent-integration:ClaudeClient/TS-06] - Token usage tracking
    """

    @pytest.mark.asyncio
    async def test_token_usage_tracking(self, client, mock_anthropic):
        """
        Verifies [agent-integration:ClaudeClient/TS-06] - Token usage tracking

        Given: Multiple calls
        When: Track tokens
        Then: Cumulative count accurate
        """
        # First call
        mock_usage1 = MagicMock()
        mock_usage1.input_tokens = 100
        mock_usage1.output_tokens = 50

        mock_content1 = MagicMock()
        mock_content1.type = "text"
        mock_content1.text = "Response 1"

        mock_response1 = MagicMock()
        mock_response1.content = [mock_content1]
        mock_response1.stop_reason = "end_turn"
        mock_response1.usage = mock_usage1

        # Second call
        mock_usage2 = MagicMock()
        mock_usage2.input_tokens = 200
        mock_usage2.output_tokens = 100

        mock_content2 = MagicMock()
        mock_content2.type = "text"
        mock_content2.text = "Response 2"

        mock_response2 = MagicMock()
        mock_response2.content = [mock_content2]
        mock_response2.stop_reason = "end_turn"
        mock_response2.usage = mock_usage2

        mock_anthropic.messages.create = AsyncMock(
            side_effect=[mock_response1, mock_response2]
        )

        await client.send_message(messages=[{"role": "user", "content": "First"}])
        await client.send_message(messages=[{"role": "user", "content": "Second"}])

        # Verify cumulative tracking
        assert client.token_usage.input_tokens == 300
        assert client.token_usage.output_tokens == 150
        assert client.token_usage.total_tokens == 450

    def test_reset_token_usage(self, client):
        """Test resetting token usage counters."""
        client._token_usage.input_tokens = 1000
        client._token_usage.output_tokens = 500

        client.reset_token_usage()

        assert client.token_usage.input_tokens == 0
        assert client.token_usage.output_tokens == 0
        assert client.token_usage.total_tokens == 0


class TestContextWindowManagement:
    """
    Tests for context window management.

    Implements [agent-integration:ClaudeClient/TS-07] - Context window management
    """

    def test_check_context_window_fits(self, client):
        """
        Verifies [agent-integration:ClaudeClient/TS-07] - Context window management

        Given: Large document context
        When: Build prompt
        Then: Truncates if necessary; warns
        """
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]

        fits, estimated = client.check_context_window(messages, system="You are helpful")

        assert fits is True
        assert estimated > 0

    def test_check_context_window_exceeds(self, client):
        """Test detecting context window overflow."""
        # Create a very large message
        large_content = "x" * (CONTEXT_WINDOW_SIZE * 5)  # Definitely exceeds
        messages = [
            {"role": "user", "content": large_content},
        ]

        fits, estimated = client.check_context_window(messages)

        assert fits is False

    def test_truncate_context_if_needed(self, client):
        """Test context truncation."""
        # Create messages that would exceed context window
        # Each message is roughly 1000 characters
        messages = [
            {"role": "user", "content": "Initial context message " + "x" * 1000}
        ] + [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"Message {i} " + "x" * 1000}
            for i in range(20)
        ]

        # Force truncation by setting a low estimate
        with patch.object(client, "_estimate_tokens", return_value=CONTEXT_WINDOW_SIZE + 1000):
            truncated = client.truncate_context_if_needed(
                messages, keep_last_n=5
            )

        # Should keep first message and last 5
        assert len(truncated) == 6

    def test_truncate_preserves_when_fits(self, client):
        """Test that messages are preserved when they fit."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]

        truncated = client.truncate_context_if_needed(messages)

        assert truncated == messages


class TestTokenUsageDataclass:
    """Tests for TokenUsage dataclass."""

    def test_token_usage_total(self):
        """Test total tokens calculation."""
        usage = TokenUsage(input_tokens=100, output_tokens=50)
        assert usage.total_tokens == 150

    def test_token_usage_add(self):
        """Test adding usage from response."""
        usage = TokenUsage()

        mock_response_usage = MagicMock()
        mock_response_usage.input_tokens = 100
        mock_response_usage.output_tokens = 50
        mock_response_usage.cache_read_input_tokens = 20
        mock_response_usage.cache_creation_input_tokens = 10

        usage.add(mock_response_usage)

        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.cache_read_input_tokens == 20
        assert usage.cache_creation_input_tokens == 10


class TestClaudeResponse:
    """Tests for ClaudeResponse dataclass."""

    def test_has_tool_calls_true(self):
        """Test has_tool_calls when tools present."""
        response = ClaudeResponse(
            content="I'll search",
            tool_calls=[ToolCall(id="1", name="search", input={})],
        )
        assert response.has_tool_calls is True

    def test_has_tool_calls_false(self):
        """Test has_tool_calls when no tools."""
        response = ClaudeResponse(content="Just text")
        assert response.has_tool_calls is False


class TestClientInitialization:
    """Tests for client initialization."""

    def test_missing_api_key_raises(self):
        """Test that missing API key raises ValueError."""
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError) as exc_info:
                ClaudeClient(api_key=None)
            assert "ANTHROPIC_API_KEY" in str(exc_info.value)

    def test_model_from_env(self, mock_anthropic):
        """Test model can be set from environment."""
        with patch.dict("os.environ", {"CLAUDE_MODEL": "claude-3-opus"}):
            client = ClaudeClient(api_key="test-key")
            assert client.model == "claude-3-opus"
