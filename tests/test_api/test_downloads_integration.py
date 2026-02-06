"""
Integration tests for review download flow.

Verifies [api-hardening:ITS-03] - Download after review completion
"""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.auth.key_validator import APIKeyValidator
from src.api.middleware.auth import AuthMiddleware
from src.api.middleware.request_id import RequestIdMiddleware
from src.api.routes import downloads, health, reviews
from src.shared.models import ReviewJob, ReviewStatus


@pytest.fixture
def mock_redis():
    """Create mock Redis client with realistic behavior."""
    redis = AsyncMock()

    # Storage for jobs and results
    jobs = {}
    results = {}

    async def store_job(job):
        jobs[job.review_id] = job

    async def get_job(review_id):
        return jobs.get(review_id)

    async def get_result(review_id):
        return results.get(review_id)

    async def store_result(review_id, result, ttl_days=30):  # noqa: ARG001
        results[review_id] = result

    async def has_active_job_for_ref(ref):
        for job in jobs.values():
            if job.application_ref == ref and job.status in (
                ReviewStatus.QUEUED,
                ReviewStatus.PROCESSING,
            ):
                return True
        return False

    redis.store_job = store_job
    redis.get_job = get_job
    redis.get_result = get_result
    redis.store_result = store_result
    redis.has_active_job_for_ref = has_active_job_for_ref

    # Seed with a completed review
    completed_job = ReviewJob(
        review_id="rev_completed_123",
        application_ref="25/01178/REM",
        status=ReviewStatus.COMPLETED,
        created_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    jobs["rev_completed_123"] = completed_job
    results["rev_completed_123"] = {
        "review": {
            "overall_rating": "amber",
            "summary": "Test review summary.",
            "aspects": [
                {
                    "name": "Cycle Parking",
                    "rating": "green",
                    "key_issue": "Adequate provision",
                    "detail": "20 spaces provided.",
                },
            ],
            "recommendations": ["Add covered storage"],
            "full_markdown": """# Cycle Advocacy Review: 25/01178/REM

## Overall Rating: AMBER

| Aspect | Rating |
|--------|--------|
| Cycle Parking | GREEN |

## Recommendations

- Add covered storage
""",
        },
        "metadata": {"processing_time_seconds": 30},
    }

    return redis


@pytest.fixture
def app(mock_redis):
    """Create full app with middleware and routes."""
    app = FastAPI()

    # Add middleware
    validator = APIKeyValidator(keys={"sk-test-key"})
    app.add_middleware(AuthMiddleware, validator=validator)
    app.add_middleware(RequestIdMiddleware)

    # Override dependency
    from src.api.dependencies import get_redis_client

    app.dependency_overrides[get_redis_client] = lambda: mock_redis

    # Include routers
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(reviews.router, prefix="/api/v1")
    app.include_router(downloads.router, prefix="/api/v1")

    return app


@pytest.fixture
async def client(app):
    """Create authenticated async test client."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer sk-test-key"},
    ) as client:
        yield client


class TestDownloadAfterCompletion:
    """
    Integration tests for download after review completion.

    Verifies [api-hardening:ITS-03] - Download after review completion
    """

    @pytest.mark.asyncio
    async def test_pdf_download_with_tables_and_formatting(self, client):
        """
        Verifies [api-hardening:ITS-03] - Download after review completion

        Given: Review completed
        When: GET /download?format=pdf
        Then: PDF file returned with correct headers
        """
        response = await client.get("/api/v1/reviews/rev_completed_123/download?format=pdf")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert "Content-Disposition" in response.headers
        assert 'filename="review-rev_completed_123.pdf"' in response.headers["content-disposition"]

        # Request ID should be present
        assert "X-Request-ID" in response.headers

        # PDF should be valid
        assert response.content[:4] == b"%PDF"

    @pytest.mark.asyncio
    async def test_json_download_contains_full_result(self, client):
        """JSON download contains complete review data."""
        response = await client.get("/api/v1/reviews/rev_completed_123/download?format=json")

        assert response.status_code == 200

        data = json.loads(response.text)
        assert "review" in data
        assert data["review"]["overall_rating"] == "amber"
        assert len(data["review"]["aspects"]) > 0
        assert "metadata" in data

    @pytest.mark.asyncio
    async def test_markdown_download_contains_formatted_content(self, client):
        """Markdown download contains properly formatted content."""
        response = await client.get("/api/v1/reviews/rev_completed_123/download?format=markdown")

        assert response.status_code == 200

        content = response.text
        assert "# Cycle Advocacy Review" in content
        assert "25/01178/REM" in content
        assert "AMBER" in content
        assert "Recommendations" in content


class TestDownloadWithAuth:
    """Tests for download with authentication."""

    @pytest.mark.asyncio
    async def test_download_requires_auth(self, app):
        """Download endpoints require authentication."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            # No auth header
        ) as client:
            response = await client.get("/api/v1/reviews/rev_completed_123/download")
            assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_download_with_invalid_key(self, app):
        """Download fails with invalid API key."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer invalid-key"},
        ) as client:
            response = await client.get("/api/v1/reviews/rev_completed_123/download")
            assert response.status_code == 401


class TestDownloadRequestId:
    """Tests for request ID in download responses."""

    @pytest.mark.asyncio
    async def test_request_id_on_success(self, client):
        """Successful downloads include request ID."""
        response = await client.get("/api/v1/reviews/rev_completed_123/download")
        assert "X-Request-ID" in response.headers

    @pytest.mark.asyncio
    async def test_request_id_on_error(self, client):
        """Error responses also include request ID."""
        response = await client.get("/api/v1/reviews/nonexistent/download")
        assert response.status_code == 404
        assert "X-Request-ID" in response.headers

    @pytest.mark.asyncio
    async def test_client_request_id_preserved(self, client):
        """Client-provided request ID is preserved."""
        custom_id = "custom-download-123"
        response = await client.get(
            "/api/v1/reviews/rev_completed_123/download",
            headers={"X-Request-ID": custom_id, "Authorization": "Bearer sk-test-key"},
        )
        assert response.headers["X-Request-ID"] == custom_id
