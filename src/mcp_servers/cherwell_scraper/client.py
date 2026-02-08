"""
HTTP client for Cherwell planning portal.

Implements [foundation-api:FR-012] - Rate limiting and User-Agent
Implements [foundation-api:NFR-004] - Retry on transient errors

Implements:
- [foundation-api:CherwellScraperMCP/TS-06] Rate limiting
- [foundation-api:CherwellScraperMCP/TS-07] Transient error retry
"""

import asyncio
import os
import time
from pathlib import Path

import httpx
import structlog

logger = structlog.get_logger(__name__)


class CherwellClientError(Exception):
    """Base error for Cherwell client operations."""

    def __init__(self, message: str, error_code: str, details: dict | None = None):
        self.message = message
        self.error_code = error_code
        self.details = details or {}
        super().__init__(message)


class ApplicationNotFoundError(CherwellClientError):
    """Application reference not found on the portal."""

    def __init__(self, reference: str):
        super().__init__(
            message=f"Application not found: {reference}",
            error_code="application_not_found",
            details={"reference": reference},
        )


class RateLimitedError(CherwellClientError):
    """Request was rate limited by the portal."""

    def __init__(self, retry_after: int | None = None):
        super().__init__(
            message="Request rate limited by portal",
            error_code="rate_limited",
            details={"retry_after": retry_after},
        )


class CherwellClient:
    """
    Async HTTP client for Cherwell planning portal with rate limiting.

    Implements [foundation-api:FR-012] - Polite scraping with rate limits
    Implements [foundation-api:NFR-004] - Retry on transient errors

    Features:
    - Configurable rate limiting (default 1 req/sec)
    - Automatic retry on 5xx errors and timeouts
    - Exponential backoff
    - Descriptive User-Agent header
    """

    DEFAULT_RATE_LIMIT = 1.0  # seconds between requests
    DEFAULT_TIMEOUT = 30.0  # seconds
    MAX_RETRIES = 3
    INITIAL_BACKOFF = 1.0
    BACKOFF_MULTIPLIER = 2.0

    # User agent identifying the scraper
    USER_AGENT = "BBug-Planning-Reporter/1.0 (Planning Application Review Bot; +https://github.com/example/bbug)"

    def __init__(
        self,
        base_url: str | None = None,
        rate_limit: float | None = None,
        timeout: float | None = None,
    ) -> None:
        """
        Initialize the Cherwell client.

        Args:
            base_url: Base URL of the Cherwell portal. Defaults to env var.
            rate_limit: Minimum seconds between requests.
            timeout: Request timeout in seconds.
        """
        self._base_url = base_url or os.getenv(
            "CHERWELL_PORTAL_URL",
            "https://planningregister.cherwell.gov.uk",
        )
        self._rate_limit = rate_limit or float(
            os.getenv("SCRAPER_RATE_LIMIT", str(self.DEFAULT_RATE_LIMIT))
        )
        self._timeout = timeout or float(
            os.getenv("SCRAPER_TIMEOUT", str(self.DEFAULT_TIMEOUT))
        )

        self._last_request_time: float = 0.0
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "CherwellClient":
        """Async context manager entry."""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout),
            headers={
                "User-Agent": self.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-GB,en;q=0.9",
            },
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _wait_for_rate_limit(self) -> None:
        """Wait until we can make another request within rate limits."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            wait_time = self._rate_limit - elapsed

            if wait_time > 0:
                logger.debug(
                    "Rate limiting, waiting",
                    wait_seconds=round(wait_time, 2),
                )
                await asyncio.sleep(wait_time)

            self._last_request_time = time.monotonic()

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        """
        Make an HTTP request with retry logic.

        Implements [foundation-api:CherwellScraperMCP/TS-07] - Transient error retry

        Args:
            method: HTTP method (GET, POST, etc.)
            url: URL to request
            **kwargs: Additional arguments for httpx

        Returns:
            HTTP response

        Raises:
            CherwellClientError: If request fails after all retries
        """
        assert self._client is not None, "Client not initialized. Use async context manager."

        backoff = self.INITIAL_BACKOFF
        last_error: Exception | None = None

        for attempt in range(self.MAX_RETRIES):
            await self._wait_for_rate_limit()

            try:
                response = await self._client.request(method, url, **kwargs)

                # Check for rate limiting
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    wait = int(retry_after) if retry_after and retry_after.isdigit() else backoff
                    logger.warning(
                        "Rate limited by portal",
                        url=url,
                        retry_after=wait,
                        attempt=attempt + 1,
                    )
                    await asyncio.sleep(wait)
                    backoff = min(backoff * self.BACKOFF_MULTIPLIER, 60.0)
                    continue

                # Retry on 5xx errors
                if response.status_code >= 500:
                    logger.warning(
                        "Server error, retrying",
                        url=url,
                        status_code=response.status_code,
                        attempt=attempt + 1,
                        backoff=backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff *= self.BACKOFF_MULTIPLIER
                    continue

                return response

            except httpx.TimeoutException as e:
                logger.warning(
                    "Request timeout, retrying",
                    url=url,
                    attempt=attempt + 1,
                    backoff=backoff,
                )
                last_error = e
                await asyncio.sleep(backoff)
                backoff *= self.BACKOFF_MULTIPLIER

            except httpx.ConnectError as e:
                logger.warning(
                    "Connection error, retrying",
                    url=url,
                    attempt=attempt + 1,
                    error=str(e),
                )
                last_error = e
                await asyncio.sleep(backoff)
                backoff *= self.BACKOFF_MULTIPLIER

        # All retries exhausted
        raise CherwellClientError(
            message=f"Request failed after {self.MAX_RETRIES} attempts: {last_error}",
            error_code="request_failed",
            details={"url": url, "last_error": str(last_error)},
        )

    async def get_application_page(self, reference: str) -> str:
        """
        Fetch the application details page.

        Args:
            reference: Application reference (e.g., '25/01178/REM')

        Returns:
            HTML content of the application page

        Raises:
            ApplicationNotFoundError: If application not found
            CherwellClientError: If request fails
        """
        # Cherwell planning register URL format
        url = f"{self._base_url}/Planning/Display/{reference}"

        logger.info(
            "Fetching application page",
            reference=reference,
            url=url,
        )

        response = await self._request_with_retry("GET", url)

        # Check for 404 or "not found" page
        if response.status_code == 404:
            raise ApplicationNotFoundError(reference)

        # Some portals return 200 but with an error page
        html = response.text
        if self._is_not_found_page(html):
            raise ApplicationNotFoundError(reference)

        return html

    async def get_documents_page(self, reference: str, page: int = 1) -> str:
        """
        Fetch the documents list page for an application.

        Args:
            reference: Application reference
            page: Page number for pagination

        Returns:
            HTML content of the documents page

        Raises:
            ApplicationNotFoundError: If application not found
            CherwellClientError: If request fails
        """
        # Documents are on the same page as the application details (in a tab)
        url = f"{self._base_url}/Planning/Display/{reference}"

        logger.info(
            "Fetching documents page",
            reference=reference,
            page=page,
        )

        response = await self._request_with_retry("GET", url)

        if response.status_code == 404:
            raise ApplicationNotFoundError(reference)

        return response.text

    async def get_page(self, url: str) -> str:
        """
        Fetch a page by URL.

        Args:
            url: Full URL to fetch

        Returns:
            HTML content

        Raises:
            CherwellClientError: If request fails
        """
        logger.debug("Fetching page", url=url)
        response = await self._request_with_retry("GET", url)
        return response.text

    async def download_document(
        self,
        url: str,
        output_path: Path,
    ) -> int:
        """
        Download a document to local storage.

        Implements [foundation-api:FR-011] - Download documents

        Args:
            url: Document URL to download
            output_path: Local path to save the file

        Returns:
            File size in bytes

        Raises:
            CherwellClientError: If download fails
        """
        assert self._client is not None, "Client not initialized"

        await self._wait_for_rate_limit()

        logger.info(
            "Downloading document",
            url=url,
            output_path=str(output_path),
        )

        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Stream download to handle large files
        try:
            async with self._client.stream("GET", url) as response:
                response.raise_for_status()

                total_size = 0
                with open(output_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        f.write(chunk)
                        total_size += len(chunk)

                logger.info(
                    "Document downloaded",
                    url=url,
                    output_path=str(output_path),
                    size_bytes=total_size,
                )
                return total_size

        except httpx.HTTPStatusError as e:
            raise CherwellClientError(
                message=f"Download failed: HTTP {e.response.status_code}",
                error_code="download_failed",
                details={"url": url, "status_code": e.response.status_code},
            )
        except Exception as e:
            raise CherwellClientError(
                message=f"Download failed: {e}",
                error_code="download_failed",
                details={"url": url, "error": str(e)},
            )

    def _encode_reference(self, reference: str) -> str:
        """Encode reference for URL parameter."""
        # Replace slashes which are common in references
        return reference.replace("/", "%2F")

    def _is_not_found_page(self, html: str) -> bool:
        """Check if HTML indicates an application not found."""
        lower_html = html.lower()
        not_found_indicators = [
            "application not found",
            "no application found",
            "does not exist",
            "invalid reference",
            "we couldn't find",
            "no results found",
        ]
        if any(indicator in lower_html for indicator in not_found_indicators):
            return True
        # New portal redirects to search page for invalid references
        return "planning application search" in lower_html and "summaryTbl" not in lower_html

    @property
    def base_url(self) -> str:
        """Get the base URL of the portal."""
        return self._base_url
