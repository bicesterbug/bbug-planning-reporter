"""
Claude API Client wrapper for review agent.

Implements [agent-integration:NFR-001] - Performance within time limits
Implements [agent-integration:NFR-004] - Token efficiency

Implements:
- [agent-integration:ClaudeClient/TS-01] Successful completion
- [agent-integration:ClaudeClient/TS-02] Tool use handling
- [agent-integration:ClaudeClient/TS-03] Streaming response
- [agent-integration:ClaudeClient/TS-04] Rate limit handling
- [agent-integration:ClaudeClient/TS-05] Transient error retry
- [agent-integration:ClaudeClient/TS-06] Token usage tracking
- [agent-integration:ClaudeClient/TS-07] Context window management
"""

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any

import structlog
from anthropic import APIError, AsyncAnthropic, RateLimitError

logger = structlog.get_logger(__name__)


# Default model
DEFAULT_MODEL = "claude-sonnet-4-5-20250514"

# Context window limits
MAX_TOKENS_OUTPUT = 8192
CONTEXT_WINDOW_SIZE = 200000
CONTEXT_WINDOW_WARNING_THRESHOLD = 180000


@dataclass
class TokenUsage:
    """Tracks token usage across API calls."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """Get total tokens used."""
        return self.input_tokens + self.output_tokens

    def add(self, response_usage: Any) -> None:
        """Add usage from a response."""
        if response_usage:
            self.input_tokens += getattr(response_usage, "input_tokens", 0)
            self.output_tokens += getattr(response_usage, "output_tokens", 0)
            self.cache_read_input_tokens += getattr(response_usage, "cache_read_input_tokens", 0)
            self.cache_creation_input_tokens += getattr(response_usage, "cache_creation_input_tokens", 0)


@dataclass
class ToolCall:
    """Represents a tool call from Claude."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ClaudeResponse:
    """Response from Claude API."""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def has_tool_calls(self) -> bool:
        """Check if response contains tool calls."""
        return len(self.tool_calls) > 0


class ClaudeClientError(Exception):
    """Error from Claude API client."""

    def __init__(self, message: str, retryable: bool = False):
        self.message = message
        self.retryable = retryable
        super().__init__(message)


class ClaudeClient:
    """
    Wrapper for Anthropic Claude API.

    Implements [agent-integration:ClaudeClient/TS-01] through [TS-07]

    Handles:
    - Message construction and tool use
    - Streaming responses for faster first-token
    - Token tracking across calls
    - Retry logic for transient errors
    - Rate limit handling
    """

    # Retry configuration
    MAX_RETRIES = 3
    INITIAL_BACKOFF_SECONDS = 1.0
    MAX_BACKOFF_SECONDS = 60.0
    BACKOFF_MULTIPLIER = 2.0

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        """
        Initialize the Claude client.

        Args:
            api_key: Anthropic API key. Defaults to ANTHROPIC_API_KEY env var.
            model: Model to use. Defaults to claude-sonnet-4-5.
        """
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self._model = model or os.getenv("CLAUDE_MODEL", DEFAULT_MODEL)

        if not self._api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        self._client = AsyncAnthropic(api_key=self._api_key)
        self._token_usage = TokenUsage()

    @property
    def token_usage(self) -> TokenUsage:
        """Get cumulative token usage."""
        return self._token_usage

    @property
    def model(self) -> str:
        """Get the model being used."""
        return self._model

    def _estimate_tokens(self, text: str) -> int:
        """
        Estimate token count for text.

        Uses a rough approximation of ~4 characters per token.
        """
        return len(text) // 4

    def _build_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build messages list for API call."""
        return messages

    async def send_message(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
    ) -> ClaudeResponse:
        """
        Send a message to Claude.

        Implements [agent-integration:ClaudeClient/TS-01] - Successful completion
        Implements [agent-integration:ClaudeClient/TS-02] - Tool use handling
        Implements [agent-integration:ClaudeClient/TS-04] - Rate limit handling
        Implements [agent-integration:ClaudeClient/TS-05] - Transient error retry

        Args:
            messages: Conversation messages.
            system: System prompt.
            tools: Available tools for Claude to use.
            max_tokens: Maximum output tokens.
            stream: Whether to stream the response.

        Returns:
            ClaudeResponse with content and tool calls.

        Raises:
            ClaudeClientError: If the request fails after retries.
        """
        effective_max_tokens = max_tokens or MAX_TOKENS_OUTPUT

        # Context window check
        estimated_input = self._estimate_tokens(system or "") + sum(
            self._estimate_tokens(str(m)) for m in messages
        )
        if estimated_input > CONTEXT_WINDOW_WARNING_THRESHOLD:
            logger.warning(
                "Context window approaching limit",
                estimated_tokens=estimated_input,
                limit=CONTEXT_WINDOW_SIZE,
            )

        # Build request parameters
        request_params: dict[str, Any] = {
            "model": self._model,
            "max_tokens": effective_max_tokens,
            "messages": messages,
        }

        if system:
            request_params["system"] = system

        if tools:
            request_params["tools"] = tools

        # Retry loop
        backoff = self.INITIAL_BACKOFF_SECONDS

        for attempt in range(self.MAX_RETRIES):
            try:
                if stream:
                    return await self._send_streaming(request_params)
                else:
                    return await self._send_non_streaming(request_params)

            except RateLimitError as e:
                # Handle rate limiting
                retry_after = self._get_retry_after(e)
                if retry_after:
                    logger.warning(
                        "Rate limited, waiting",
                        retry_after_seconds=retry_after,
                        attempt=attempt + 1,
                    )
                    await asyncio.sleep(retry_after)
                else:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * self.BACKOFF_MULTIPLIER, self.MAX_BACKOFF_SECONDS)

            except APIError as e:
                # Retry on 5xx errors
                if e.status_code and e.status_code >= 500:
                    if attempt < self.MAX_RETRIES - 1:
                        logger.warning(
                            "Transient API error, retrying",
                            status_code=e.status_code,
                            attempt=attempt + 1,
                            backoff_seconds=backoff,
                        )
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * self.BACKOFF_MULTIPLIER, self.MAX_BACKOFF_SECONDS)
                        continue

                logger.error(
                    "Claude API error",
                    status_code=e.status_code,
                    message=str(e),
                )
                raise ClaudeClientError(str(e), retryable=False)

            except Exception as e:
                logger.exception("Unexpected error calling Claude API")
                raise ClaudeClientError(str(e), retryable=False)

        raise ClaudeClientError("Max retries exceeded", retryable=True)

    def _get_retry_after(self, error: RateLimitError) -> float | None:
        """Extract retry-after value from rate limit error."""
        # Check headers for retry-after
        try:
            if hasattr(error, "response") and error.response:
                headers = getattr(error.response, "headers", {})
                retry_after = headers.get("retry-after")
                if retry_after:
                    return float(retry_after)
        except (ValueError, AttributeError):
            pass
        return None

    async def _send_non_streaming(
        self,
        request_params: dict[str, Any],
    ) -> ClaudeResponse:
        """Send a non-streaming request."""
        response = await self._client.messages.create(**request_params)

        # Track token usage
        self._token_usage.add(response.usage)

        # Extract content and tool calls
        content_parts = []
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                content_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        input=block.input,
                    )
                )

        return ClaudeResponse(
            content="\n".join(content_parts),
            tool_calls=tool_calls,
            stop_reason=response.stop_reason,
            input_tokens=response.usage.input_tokens if response.usage else 0,
            output_tokens=response.usage.output_tokens if response.usage else 0,
        )

    async def _send_streaming(
        self,
        request_params: dict[str, Any],
    ) -> ClaudeResponse:
        """
        Send a streaming request.

        Implements [agent-integration:ClaudeClient/TS-03] - Streaming response
        """
        content_parts = []
        tool_calls = []
        current_tool_call: dict[str, Any] | None = None
        input_tokens = 0
        output_tokens = 0
        stop_reason = None

        async with self._client.messages.stream(**request_params) as stream:
            async for event in stream:
                if event.type == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        current_tool_call = {
                            "id": block.id,
                            "name": block.name,
                            "input_json": "",
                        }

                elif event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        content_parts.append(delta.text)
                    elif delta.type == "input_json_delta" and current_tool_call:
                        current_tool_call["input_json"] += delta.partial_json

                elif event.type == "content_block_stop":
                    if current_tool_call:
                        import json
                        tool_calls.append(
                            ToolCall(
                                id=current_tool_call["id"],
                                name=current_tool_call["name"],
                                input=json.loads(current_tool_call["input_json"]) if current_tool_call["input_json"] else {},
                            )
                        )
                        current_tool_call = None

                elif event.type == "message_delta":
                    stop_reason = event.delta.stop_reason

                elif event.type == "message_start":
                    if event.message.usage:
                        input_tokens = event.message.usage.input_tokens

            # Get final message for complete usage stats
            final_message = await stream.get_final_message()
            if final_message.usage:
                output_tokens = final_message.usage.output_tokens
                self._token_usage.add(final_message.usage)

        return ClaudeResponse(
            content="".join(content_parts),
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def build_tool_result_message(
        self,
        tool_use_id: str,
        result: str | dict[str, Any],
        is_error: bool = False,
    ) -> dict[str, Any]:
        """
        Build a tool result message.

        Args:
            tool_use_id: The tool_use block id.
            result: The tool result content.
            is_error: Whether the result is an error.

        Returns:
            Message dict for tool result.
        """
        content = result if isinstance(result, str) else str(result)

        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": content,
                    "is_error": is_error,
                }
            ],
        }

    def reset_token_usage(self) -> None:
        """Reset token usage counters."""
        self._token_usage = TokenUsage()

    def check_context_window(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
    ) -> tuple[bool, int]:
        """
        Check if messages fit within context window.

        Implements [agent-integration:ClaudeClient/TS-07] - Context window management

        Args:
            messages: Messages to check.
            system: System prompt.

        Returns:
            Tuple of (fits_in_window, estimated_tokens).
        """
        estimated = self._estimate_tokens(system or "")
        for msg in messages:
            estimated += self._estimate_tokens(str(msg))

        fits = estimated < CONTEXT_WINDOW_SIZE
        return fits, estimated

    def truncate_context_if_needed(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        keep_last_n: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Truncate conversation history if context window is exceeded.

        Implements [agent-integration:ClaudeClient/TS-07] - Context window management

        Args:
            messages: Messages to potentially truncate.
            system: System prompt (not truncated).
            keep_last_n: Minimum number of recent messages to keep.

        Returns:
            Potentially truncated message list.
        """
        fits, estimated = self.check_context_window(messages, system)

        if fits:
            return messages

        logger.warning(
            "Truncating context window",
            original_tokens=estimated,
            message_count=len(messages),
        )

        # Keep the first message (often contains important context)
        # and the last N messages
        if len(messages) <= keep_last_n + 1:
            return messages

        truncated = [messages[0]] + messages[-(keep_last_n):]

        return truncated
