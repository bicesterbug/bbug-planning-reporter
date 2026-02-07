"""
Tests for CherwellClient HTTP client.

Implements:
- [foundation-api:CherwellScraperMCP/TS-06] Rate limiting
- [foundation-api:CherwellScraperMCP/TS-07] Transient error retry
"""

import asyncio
import time

import httpx
import pytest
import respx

from src.mcp_servers.cherwell_scraper.client import (
    ApplicationNotFoundError,
    CherwellClient,
    CherwellClientError,
)


@pytest.fixture
def base_url() -> str:
    """Base URL for mock server."""
    return "https://planning.test.gov.uk"


class TestRateLimiting:
    """Tests for rate limiting behavior."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_rate_limiting_delays_requests(self, base_url: str):
        """
        Verifies [foundation-api:CherwellScraperMCP/TS-06] - Rate limiting

        Given: Rate limit of 0.5 seconds
        When: Make multiple rapid requests
        Then: Requests are spaced by rate limit delay
        """
        # Mock endpoints
        route = respx.get(f"{base_url}/test").mock(return_value=httpx.Response(200, text="OK"))

        request_times = []

        async with CherwellClient(base_url=base_url, rate_limit=0.5) as client:
            # Make 3 rapid requests
            for _ in range(3):
                await client.get_page(f"{base_url}/test")
                request_times.append(time.monotonic())

        # Check that requests were spaced
        assert len(request_times) == 3

        # First two requests should have ~0.5s gap
        gap1 = request_times[1] - request_times[0]
        gap2 = request_times[2] - request_times[1]

        # Allow some tolerance for timing
        assert gap1 >= 0.4, f"Gap between requests should be ~0.5s, was {gap1}"
        assert gap2 >= 0.4, f"Gap between requests should be ~0.5s, was {gap2}"

    @pytest.mark.asyncio
    @respx.mock
    async def test_respects_retry_after_header(self, base_url: str):
        """
        Verifies [foundation-api:CherwellScraperMCP/TS-06] - Rate limiting

        Given: Server returns 429 with Retry-After header
        When: Make request
        Then: Waits and retries
        """
        call_count = 0

        def rate_limited_response(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(
                    429,
                    headers={"Retry-After": "1"},
                    text="Rate limited",
                )
            return httpx.Response(200, text="OK")

        respx.get(f"{base_url}/test").mock(side_effect=rate_limited_response)

        async with CherwellClient(base_url=base_url, rate_limit=0.1) as client:
            start = time.monotonic()
            await client.get_page(f"{base_url}/test")
            elapsed = time.monotonic() - start

        assert call_count == 2
        assert elapsed >= 0.9, "Should have waited for Retry-After"


class TestRetryBehavior:
    """Tests for retry on transient errors."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_retries_on_5xx_error(self, base_url: str):
        """
        Verifies [foundation-api:CherwellScraperMCP/TS-07] - Transient error retry

        Given: Server returns 503 on first attempt
        When: Make request
        Then: Retries and eventually succeeds
        """
        call_count = 0

        def server_error_then_ok(request):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return httpx.Response(503, text="Service Unavailable")
            return httpx.Response(200, text="<html><body>Success</body></html>")

        respx.get(f"{base_url}/online-applications/applicationDetails.do").mock(
            side_effect=server_error_then_ok
        )

        async with CherwellClient(base_url=base_url, rate_limit=0.1) as client:
            html = await client.get_application_page("25/00001/FUL")

        assert call_count == 3
        assert "Success" in html

    @pytest.mark.asyncio
    @respx.mock
    async def test_fails_after_max_retries(self, base_url: str):
        """
        Verifies [foundation-api:CherwellScraperMCP/TS-07] - Transient error retry

        Given: Server always returns 503
        When: Make request
        Then: Fails after max retries with descriptive error
        """
        respx.get(f"{base_url}/online-applications/applicationDetails.do").mock(
            return_value=httpx.Response(503, text="Service Unavailable")
        )

        async with CherwellClient(base_url=base_url, rate_limit=0.1) as client:
            with pytest.raises(CherwellClientError) as exc_info:
                await client.get_application_page("25/00001/FUL")

        assert exc_info.value.error_code == "request_failed"
        assert "3 attempts" in exc_info.value.message

    @pytest.mark.asyncio
    @respx.mock
    async def test_retries_on_timeout(self, base_url: str):
        """
        Verifies [foundation-api:CherwellScraperMCP/TS-07] - Transient error retry

        Given: Server times out on first attempt
        When: Make request
        Then: Retries with backoff
        """
        call_count = 0

        def timeout_then_ok(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ReadTimeout("Read timed out")
            return httpx.Response(200, text="OK")

        respx.get(f"{base_url}/test").mock(side_effect=timeout_then_ok)

        async with CherwellClient(base_url=base_url, rate_limit=0.1, timeout=1.0) as client:
            await client.get_page(f"{base_url}/test")

        assert call_count == 2


class TestApplicationNotFound:
    """Tests for application not found handling."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_404_raises_application_not_found(self, base_url: str):
        """
        Verifies [foundation-api:CherwellScraperMCP/TS-02] - Non-existent application

        Given: Server returns 404
        When: Fetch application
        Then: Raises ApplicationNotFoundError
        """
        respx.get(f"{base_url}/online-applications/applicationDetails.do").mock(
            return_value=httpx.Response(404, text="Not Found")
        )

        async with CherwellClient(base_url=base_url, rate_limit=0.1) as client:
            with pytest.raises(ApplicationNotFoundError) as exc_info:
                await client.get_application_page("99/99999/XXX")

        assert exc_info.value.error_code == "application_not_found"
        assert "99/99999/XXX" in exc_info.value.details["reference"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_not_found_page_content_detected(self, base_url: str):
        """
        Verifies [foundation-api:CherwellScraperMCP/TS-02] - Non-existent application

        Given: Server returns 200 but with "application not found" content
        When: Fetch application
        Then: Raises ApplicationNotFoundError
        """
        respx.get(f"{base_url}/online-applications/applicationDetails.do").mock(
            return_value=httpx.Response(
                200,
                text="<html><body>Application not found. Please check the reference.</body></html>",
            )
        )

        async with CherwellClient(base_url=base_url, rate_limit=0.1) as client:
            with pytest.raises(ApplicationNotFoundError):
                await client.get_application_page("99/99999/XXX")


class TestUserAgent:
    """Tests for User-Agent header."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_sets_descriptive_user_agent(self, base_url: str):
        """
        Verifies [foundation-api:FR-012] - Polite scraping with User-Agent

        Given: Client configured
        When: Make request
        Then: Request includes descriptive User-Agent
        """
        captured_headers = {}

        def capture_headers(request):
            captured_headers.update(dict(request.headers))
            return httpx.Response(200, text="OK")

        respx.get(f"{base_url}/test").mock(side_effect=capture_headers)

        async with CherwellClient(base_url=base_url, rate_limit=0.1) as client:
            await client.get_page(f"{base_url}/test")

        assert "user-agent" in captured_headers
        assert "BBug-Planning-Reporter" in captured_headers["user-agent"]
