"""
Pytest configuration and shared fixtures.

Implements [foundation-api:NFR-005] - Test infrastructure setup
"""

import asyncio
from collections.abc import AsyncGenerator, Generator
from typing import Any

import fakeredis.aioredis
import pytest


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def fake_redis() -> AsyncGenerator[fakeredis.aioredis.FakeRedis, None]:
    """Provide a fake Redis client for testing."""
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


@pytest.fixture
def sample_application_ref() -> str:
    """Sample valid application reference for testing."""
    return "25/01178/REM"


@pytest.fixture
def sample_review_id() -> str:
    """Sample review ID for testing."""
    return "rev_01HQXK7V3WNPB8MTJF2R5ADGX9"


@pytest.fixture
def sample_webhook_config() -> dict[str, Any]:
    """Sample webhook configuration for testing."""
    return {
        "url": "https://example.com/hooks/cherwell",
        "secret": "whsec_test_secret_123",
        "events": ["review.started", "review.completed", "review.failed"],
    }


@pytest.fixture
def sample_review_request(sample_application_ref: str) -> dict[str, Any]:
    """Sample review request payload."""
    return {
        "application_ref": sample_application_ref,
        "options": {
            "focus_areas": ["cycle_parking", "cycle_routes"],
            "output_format": "markdown",
        },
    }


@pytest.fixture
def sample_review_request_with_webhook(
    sample_review_request: dict[str, Any],
    sample_webhook_config: dict[str, Any],
) -> dict[str, Any]:
    """Sample review request with webhook configuration."""
    return {**sample_review_request, "webhook": sample_webhook_config}
