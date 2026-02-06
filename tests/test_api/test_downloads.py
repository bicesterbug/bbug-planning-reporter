"""
Tests for ReviewDownloadRouter.

Verifies [api-hardening:FR-005] - Download review as Markdown
Verifies [api-hardening:FR-006] - Download review as JSON
Verifies [api-hardening:FR-007] - Download review as PDF
Verifies [api-hardening:NFR-006] - PDF generation quality
"""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.exception_handlers import register_exception_handlers
from src.api.routes.downloads import router
from src.shared.models import ReviewJob, ReviewStatus


@pytest.fixture
def mock_redis():
    """Create mock Redis client."""
    return AsyncMock()


@pytest.fixture
def app(mock_redis):
    """Create test app with downloads router."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    # Register exception handlers for consistent error format
    register_exception_handlers(app)

    # Override dependency
    from src.api.dependencies import get_redis_client

    app.dependency_overrides[get_redis_client] = lambda: mock_redis

    return app


@pytest.fixture
async def client(app):
    """Create async test client."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


@pytest.fixture
def completed_review_job():
    """Create a completed review job."""
    return ReviewJob(
        review_id="rev_test123",
        application_ref="25/01178/REM",
        status=ReviewStatus.COMPLETED,
        created_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )


@pytest.fixture
def review_result():
    """Create a sample review result."""
    return {
        "review": {
            "overall_rating": "amber",
            "summary": "Overall assessment of the planning application.",
            "aspects": [
                {
                    "name": "Cycle Parking",
                    "rating": "green",
                    "key_issue": "Adequate provision",
                    "detail": "20 spaces provided for 10 units.",
                },
                {
                    "name": "Cycle Routes",
                    "rating": "red",
                    "key_issue": "No safe route to town centre",
                    "detail": "Proposed shared-use path on busy road.",
                },
            ],
            "recommendations": [
                "Increase cycle parking to 25 spaces",
                "Provide segregated cycle track",
            ],
            "full_markdown": """# Cycle Advocacy Review: 25/01178/REM

## Overall Rating: AMBER

## Summary

Overall assessment of the planning application.

## Aspect Assessments

### Cycle Parking: GREEN

**Key Issue:** Adequate provision

20 spaces provided for 10 units.

### Cycle Routes: RED

**Key Issue:** No safe route to town centre

Proposed shared-use path on busy road.

## Recommendations

- Increase cycle parking to 25 spaces
- Provide segregated cycle track
""",
        },
        "metadata": {
            "processing_time_seconds": 45,
        },
    }


class TestDownloadAsMarkdown:
    """
    Tests for Markdown download.

    Verifies [api-hardening:ReviewDownloadRouter/TS-01] - Download as Markdown
    Verifies [api-hardening:ReviewDownloadRouter/TS-04] - Default format
    """

    @pytest.mark.asyncio
    async def test_download_as_markdown(
        self, client, mock_redis, completed_review_job, review_result
    ):
        """
        Verifies [api-hardening:ReviewDownloadRouter/TS-01] - Download as Markdown

        Given: Completed review exists
        When: GET /download?format=markdown
        Then: Returns .md file with Content-Type text/markdown
        """
        mock_redis.get_job.return_value = completed_review_job
        mock_redis.get_result.return_value = review_result

        response = await client.get("/api/v1/reviews/rev_test123/download?format=markdown")

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/markdown; charset=utf-8"
        assert 'filename="review-rev_test123.md"' in response.headers["content-disposition"]

        # Content should be valid markdown
        content = response.text
        assert "# Cycle Advocacy Review" in content
        assert "AMBER" in content

    @pytest.mark.asyncio
    async def test_default_format_is_markdown(
        self, client, mock_redis, completed_review_job, review_result
    ):
        """
        Verifies [api-hardening:ReviewDownloadRouter/TS-04] - Default format

        Given: No format specified
        When: GET /download
        Then: Returns markdown format
        """
        mock_redis.get_job.return_value = completed_review_job
        mock_redis.get_result.return_value = review_result

        response = await client.get("/api/v1/reviews/rev_test123/download")

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/markdown; charset=utf-8"


class TestDownloadAsJSON:
    """
    Tests for JSON download.

    Verifies [api-hardening:ReviewDownloadRouter/TS-02] - Download as JSON
    """

    @pytest.mark.asyncio
    async def test_download_as_json(
        self, client, mock_redis, completed_review_job, review_result
    ):
        """
        Verifies [api-hardening:ReviewDownloadRouter/TS-02] - Download as JSON

        Given: Completed review exists
        When: GET /download?format=json
        Then: Returns .json file with Content-Type application/json
        """
        mock_redis.get_job.return_value = completed_review_job
        mock_redis.get_result.return_value = review_result

        response = await client.get("/api/v1/reviews/rev_test123/download?format=json")

        assert response.status_code == 200
        assert "application/json" in response.headers["content-type"]
        assert 'filename="review-rev_test123.json"' in response.headers["content-disposition"]

        # Content should be valid JSON
        data = json.loads(response.text)
        assert "review" in data
        assert data["review"]["overall_rating"] == "amber"


class TestDownloadAsPDF:
    """
    Tests for PDF download.

    Verifies [api-hardening:ReviewDownloadRouter/TS-03] - Download as PDF
    """

    @pytest.mark.asyncio
    async def test_download_as_pdf(
        self, client, mock_redis, completed_review_job, review_result
    ):
        """
        Verifies [api-hardening:ReviewDownloadRouter/TS-03] - Download as PDF

        Given: Completed review exists
        When: GET /download?format=pdf
        Then: Returns .pdf file with Content-Type application/pdf
        """
        mock_redis.get_job.return_value = completed_review_job
        mock_redis.get_result.return_value = review_result

        response = await client.get("/api/v1/reviews/rev_test123/download?format=pdf")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert 'filename="review-rev_test123.pdf"' in response.headers["content-disposition"]

        # Content should be valid PDF
        assert response.content[:4] == b"%PDF"


class TestInvalidFormat:
    """
    Tests for invalid format handling.

    Verifies [api-hardening:ReviewDownloadRouter/TS-05] - Invalid format
    """

    @pytest.mark.asyncio
    async def test_invalid_format_returns_422(
        self, client, mock_redis, completed_review_job, review_result
    ):
        """
        Verifies [api-hardening:ReviewDownloadRouter/TS-05] - Invalid format

        Given: Unsupported format
        When: GET /download?format=docx
        Then: Returns 422 with validation error
        """
        mock_redis.get_job.return_value = completed_review_job
        mock_redis.get_result.return_value = review_result

        response = await client.get("/api/v1/reviews/rev_test123/download?format=docx")

        # FastAPI returns 422 for enum validation failures
        assert response.status_code == 422


class TestIncompleteReview:
    """
    Tests for incomplete review handling.

    Verifies [api-hardening:ReviewDownloadRouter/TS-06] - Incomplete review
    """

    @pytest.mark.asyncio
    async def test_incomplete_review_returns_400(self, client, mock_redis):
        """
        Verifies [api-hardening:ReviewDownloadRouter/TS-06] - Incomplete review

        Given: Review in "processing" status
        When: GET /download
        Then: Returns 400 with error code "review_incomplete"
        """
        processing_job = ReviewJob(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            status=ReviewStatus.PROCESSING,
            created_at=datetime.now(UTC),
        )
        mock_redis.get_job.return_value = processing_job

        response = await client.get("/api/v1/reviews/rev_test123/download")

        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "review_incomplete"

    @pytest.mark.asyncio
    async def test_queued_review_returns_400(self, client, mock_redis):
        """Queued review cannot be downloaded."""
        queued_job = ReviewJob(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            status=ReviewStatus.QUEUED,
            created_at=datetime.now(UTC),
        )
        mock_redis.get_job.return_value = queued_job

        response = await client.get("/api/v1/reviews/rev_test123/download")

        assert response.status_code == 400
        assert "review_incomplete" in response.text


class TestNonExistentReview:
    """
    Tests for non-existent review handling.

    Verifies [api-hardening:ReviewDownloadRouter/TS-07] - Non-existent review
    """

    @pytest.mark.asyncio
    async def test_nonexistent_review_returns_404(self, client, mock_redis):
        """
        Verifies [api-hardening:ReviewDownloadRouter/TS-07] - Non-existent review

        Given: Invalid review_id
        When: GET /download
        Then: Returns 404 with error code "review_not_found"
        """
        mock_redis.get_job.return_value = None

        response = await client.get("/api/v1/reviews/rev_invalid/download")

        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "review_not_found"


class TestContentDisposition:
    """
    Tests for Content-Disposition header.

    Verifies [api-hardening:ReviewDownloadRouter/TS-08] - Content-Disposition header
    """

    @pytest.mark.asyncio
    async def test_content_disposition_on_all_formats(
        self, client, mock_redis, completed_review_job, review_result
    ):
        """
        Verifies [api-hardening:ReviewDownloadRouter/TS-08] - Content-Disposition header

        Given: Any format download
        When: GET /download
        Then: Includes filename in Content-Disposition
        """
        mock_redis.get_job.return_value = completed_review_job
        mock_redis.get_result.return_value = review_result

        formats = ["markdown", "json", "pdf"]
        extensions = [".md", ".json", ".pdf"]

        for fmt, ext in zip(formats, extensions, strict=True):
            response = await client.get(f"/api/v1/reviews/rev_test123/download?format={fmt}")
            assert response.status_code == 200
            assert "Content-Disposition" in response.headers
            assert f'filename="review-rev_test123{ext}"' in response.headers["content-disposition"]


class TestMarkdownGeneration:
    """Tests for Markdown generation from content."""

    @pytest.mark.asyncio
    async def test_generates_markdown_when_not_provided(
        self, client, mock_redis, completed_review_job
    ):
        """Markdown is generated from content if full_markdown not available."""
        result_without_markdown = {
            "review": {
                "overall_rating": "green",
                "summary": "Good application.",
                "aspects": [
                    {
                        "name": "Cycle Parking",
                        "rating": "green",
                        "key_issue": "Good provision",
                    }
                ],
            }
        }
        mock_redis.get_job.return_value = completed_review_job
        mock_redis.get_result.return_value = result_without_markdown

        response = await client.get("/api/v1/reviews/rev_test123/download?format=markdown")

        assert response.status_code == 200
        content = response.text
        assert "# Cycle Advocacy Review" in content
        assert "GREEN" in content
