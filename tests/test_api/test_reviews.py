"""
Tests for reviews API endpoints.

Implements test scenarios from [foundation-api:ReviewRouter/TS-01] through [TS-10]
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_arq_pool, get_redis_client
from src.api.main import app
from src.shared.models import (
    ProcessingPhase,
    ReviewJob,
    ReviewProgress,
    ReviewStatus,
)


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def mock_redis():
    """Create mock Redis client."""
    mock = AsyncMock()
    mock.has_active_job_for_ref = AsyncMock(return_value=False)
    mock.store_job = AsyncMock()
    mock.get_job = AsyncMock(return_value=None)
    mock.list_jobs = AsyncMock(return_value=([], 0))
    mock.update_job_status = AsyncMock(return_value=True)
    mock.get_result = AsyncMock(return_value=None)
    return mock


class TestSubmitReview:
    """Tests for POST /api/v1/reviews endpoint."""

    def test_valid_review_submission(self, client, mock_redis):
        """
        Verifies [foundation-api:ReviewRouter/TS-01] - Valid review submission

        Given: Valid application reference
        When: POST /reviews with valid data
        Then: Returns 202 with review_id and status links
        """
        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.post(
            "/api/v1/reviews",
            json={"application_ref": "25/01178/REM"},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 202
        data = response.json()
        assert "review_id" in data
        assert data["review_id"].startswith("rev_")
        assert data["application_ref"] == "25/01178/REM"
        assert data["status"] == "queued"
        assert "links" in data
        assert "self" in data["links"]
        assert "status" in data["links"]
        assert "cancel" in data["links"]

    def test_invalid_reference_format(self, client, mock_redis):
        """
        Verifies [foundation-api:ReviewRouter/TS-02] - Invalid reference format

        Given: Invalid application reference format
        When: POST /reviews
        Then: Returns 422 validation error
        """
        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.post(
            "/api/v1/reviews",
            json={"application_ref": "INVALID"},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 422
        data = response.json()
        assert "error" in data

    def test_duplicate_review_prevention(self, client, mock_redis):
        """
        Verifies [foundation-api:ReviewRouter/TS-03] - Duplicate review prevention

        Given: Application with existing active review
        When: POST /reviews for same application
        Then: Returns 409 with review_already_exists error
        """
        mock_redis.has_active_job_for_ref = AsyncMock(return_value=True)

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.post(
            "/api/v1/reviews",
            json={"application_ref": "25/01178/REM"},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 409
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == "review_already_exists"


class TestGetReview:
    """Tests for GET /api/v1/reviews/{review_id} endpoint."""

    def test_get_processing_review(self, client, mock_redis):
        """
        Verifies [foundation-api:ReviewRouter/TS-04] - Get processing review

        Given: Review in processing status
        When: GET /reviews/{id}
        Then: Returns job with progress info
        """
        job = ReviewJob(
            review_id="rev_123",
            application_ref="25/01178/REM",
            status=ReviewStatus.PROCESSING,
            created_at=datetime.now(UTC),
            progress=ReviewProgress(
                phase=ProcessingPhase.DOWNLOADING_DOCUMENTS,
                phase_number=2,
                total_phases=5,
                percent_complete=20,
            ),
        )
        mock_redis.get_job = AsyncMock(return_value=job)

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.get("/api/v1/reviews/rev_123")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["review_id"] == "rev_123"
        assert data["status"] == "processing"
        assert data["progress"] is not None
        assert data["progress"]["percent_complete"] == 20

    def test_get_completed_review(self, client, mock_redis):
        """
        Verifies [foundation-api:ReviewRouter/TS-05] - Get completed review

        Given: Completed review
        When: GET /reviews/{id}
        Then: Returns full review result
        """
        job = ReviewJob(
            review_id="rev_123",
            application_ref="25/01178/REM",
            status=ReviewStatus.COMPLETED,
            created_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        mock_redis.get_job = AsyncMock(return_value=job)
        mock_redis.get_result = AsyncMock(
            return_value={
                "review": {"overall_rating": "amber", "summary": "Needs improvement"},
                "application": {"reference": "25/01178/REM"},
                "metadata": {"model": "claude-3-sonnet"},
            }
        )

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.get("/api/v1/reviews/rev_123")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["review_id"] == "rev_123"
        assert data["status"] == "completed"
        assert data["review"] is not None
        assert data["review"]["overall_rating"] == "amber"

    def test_get_nonexistent_review(self, client, mock_redis):
        """
        Verifies [foundation-api:ReviewRouter/TS-06] - Get non-existent review

        Given: Non-existent review ID
        When: GET /reviews/{id}
        Then: Returns 404
        """
        mock_redis.get_job = AsyncMock(return_value=None)

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.get("/api/v1/reviews/rev_nonexistent")

        app.dependency_overrides.clear()

        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "review_not_found"


class TestListReviews:
    """Tests for GET /api/v1/reviews endpoint."""

    def test_list_reviews_with_filter(self, client, mock_redis):
        """
        Verifies [foundation-api:ReviewRouter/TS-07] - List reviews with filter

        Given: Multiple reviews with different statuses
        When: GET /reviews?status=completed
        Then: Returns only matching reviews
        """
        from src.shared.models import ReviewJobSummary

        summaries = [
            ReviewJobSummary(
                review_id="rev_1",
                application_ref="25/01178/REM",
                status=ReviewStatus.COMPLETED,
                created_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            ),
        ]
        mock_redis.list_jobs = AsyncMock(return_value=(summaries, 1))

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.get("/api/v1/reviews?status=completed")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["reviews"]) == 1
        assert data["reviews"][0]["status"] == "completed"


class TestCancelReview:
    """Tests for POST /api/v1/reviews/{review_id}/cancel endpoint."""

    def test_cancel_queued_review(self, client, mock_redis):
        """
        Verifies [foundation-api:ReviewRouter/TS-08] - Cancel queued review

        Given: Review in queued status
        When: POST /reviews/{id}/cancel
        Then: Returns cancelled status
        """
        job = ReviewJob(
            review_id="rev_123",
            application_ref="25/01178/REM",
            status=ReviewStatus.QUEUED,
            created_at=datetime.now(UTC),
        )
        mock_redis.get_job = AsyncMock(return_value=job)
        mock_redis.update_job_status = AsyncMock(return_value=True)

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.post("/api/v1/reviews/rev_123/cancel")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "cancelled"

    def test_cancel_completed_review(self, client, mock_redis):
        """
        Verifies [foundation-api:ReviewRouter/TS-09] - Cancel completed review

        Given: Review already completed
        When: POST /reviews/{id}/cancel
        Then: Returns 409 error
        """
        job = ReviewJob(
            review_id="rev_123",
            application_ref="25/01178/REM",
            status=ReviewStatus.COMPLETED,
            created_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        mock_redis.get_job = AsyncMock(return_value=job)

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.post("/api/v1/reviews/rev_123/cancel")

        app.dependency_overrides.clear()

        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "cannot_cancel"


class TestReviewStatus:
    """Tests for GET /api/v1/reviews/{review_id}/status endpoint."""

    def test_lightweight_status_check(self, client, mock_redis):
        """
        Verifies [foundation-api:ReviewRouter/TS-10] - Lightweight status check

        Given: Processing review
        When: GET /reviews/{id}/status
        Then: Returns minimal status response
        """
        job = ReviewJob(
            review_id="rev_123",
            application_ref="25/01178/REM",
            status=ReviewStatus.PROCESSING,
            created_at=datetime.now(UTC),
            progress=ReviewProgress(
                phase=ProcessingPhase.ANALYSING_APPLICATION,
                phase_number=4,
                total_phases=5,
                percent_complete=60,
            ),
        )
        mock_redis.get_job = AsyncMock(return_value=job)

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.get("/api/v1/reviews/rev_123/status")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["review_id"] == "rev_123"
        assert data["status"] == "processing"
        assert data["progress"]["percent_complete"] == 60
        # Lightweight response should not include full review data
        assert "review" not in data
        assert "application" not in data


class TestSubmitReviewScopeControl:
    """
    Tests for review-scope-control toggle fields flowing through submit_review.

    Verifies [review-scope-control:submit_review/TS-01]
    """

    def test_toggle_fields_mapped_to_internal_model(self, client, mock_redis):
        """
        Verifies [review-scope-control:submit_review/TS-01] - Toggle fields mapped

        Given: API request with include_consultation_responses=True
        When: submit_review is called
        Then: The ReviewOptions stored in Redis has include_consultation_responses=True
        """
        mock_arq = AsyncMock()
        mock_arq.enqueue_job = AsyncMock()
        app.dependency_overrides[get_redis_client] = lambda: mock_redis
        app.dependency_overrides[get_arq_pool] = lambda: mock_arq

        response = client.post(
            "/api/v1/reviews",
            json={
                "application_ref": "25/01178/REM",
                "options": {
                    "include_consultation_responses": True,
                    "include_public_comments": True,
                },
            },
        )

        app.dependency_overrides.clear()

        assert response.status_code == 202

        # Verify the store_job was called with correct options
        mock_redis.store_job.assert_called_once()
        stored_job = mock_redis.store_job.call_args[0][0]
        assert stored_job.options is not None
        assert stored_job.options.include_consultation_responses is True
        assert stored_job.options.include_public_comments is True


class TestReviewProgressIntegration:
    """
    Integration tests for progress visibility on status endpoint.

    Verifies [review-progress:ITS-01] and [review-progress:ITS-02]
    """

    def test_status_endpoint_returns_full_progress(self, client, mock_redis):
        """
        Verifies [review-progress:ITS-01] - Progress visible on status endpoint

        Given: Review job with progress data in Redis (as synced by ProgressTracker)
        When: GET /api/v1/reviews/{id}/status
        Then: Response contains full progress object
        """
        job = ReviewJob(
            review_id="rev_progress_test",
            application_ref="25/01178/REM",
            status=ReviewStatus.PROCESSING,
            created_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
            progress=ReviewProgress(
                phase=ProcessingPhase.DOWNLOADING_DOCUMENTS,
                phase_number=2,
                total_phases=5,
                percent_complete=27,
                detail="Downloaded 5 of 12 documents",
            ),
        )
        mock_redis.get_job = AsyncMock(return_value=job)

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.get("/api/v1/reviews/rev_progress_test/status")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "processing"
        assert data["progress"] is not None
        assert data["progress"]["phase"] == "downloading_documents"
        assert data["progress"]["phase_number"] == 2
        assert data["progress"]["total_phases"] == 5
        assert data["progress"]["percent_complete"] == 27
        assert data["progress"]["detail"] == "Downloaded 5 of 12 documents"

    def test_full_review_endpoint_returns_progress(self, client, mock_redis):
        """
        Verifies [review-progress:FR-002] - Progress on full review endpoint

        Given: Review job with progress data in Redis
        When: GET /api/v1/reviews/{id}
        Then: Response contains full progress object
        """
        job = ReviewJob(
            review_id="rev_progress_test",
            application_ref="25/01178/REM",
            status=ReviewStatus.PROCESSING,
            created_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
            progress=ReviewProgress(
                phase=ProcessingPhase.INGESTING_DOCUMENTS,
                phase_number=3,
                total_phases=5,
                percent_complete=42,
                detail="Ingesting document 8 of 22",
            ),
        )
        mock_redis.get_job = AsyncMock(return_value=job)

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.get("/api/v1/reviews/rev_progress_test")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "processing"
        assert data["progress"] is not None
        assert data["progress"]["phase"] == "ingesting_documents"
        assert data["progress"]["phase_number"] == 3
        assert data["progress"]["percent_complete"] == 42
        assert data["progress"]["detail"] == "Ingesting document 8 of 22"

    def test_progress_null_after_completion(self, client, mock_redis):
        """
        Verifies [review-progress:ITS-02] - Progress null after completion

        Given: Review job that has completed (progress cleared by ProgressTracker)
        When: GET /api/v1/reviews/{id}/status
        Then: Response contains progress: null
        """
        job = ReviewJob(
            review_id="rev_done_test",
            application_ref="25/01178/REM",
            status=ReviewStatus.COMPLETED,
            created_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            progress=None,
        )
        mock_redis.get_job = AsyncMock(return_value=job)

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.get("/api/v1/reviews/rev_done_test/status")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["progress"] is None

    def test_progress_null_for_queued_review(self, client, mock_redis):
        """
        Verifies [review-progress:FR-004] - Progress null for queued status

        Given: Review job with status queued (not yet started)
        When: GET /api/v1/reviews/{id}/status
        Then: Response contains progress: null
        """
        job = ReviewJob(
            review_id="rev_queued_test",
            application_ref="25/01178/REM",
            status=ReviewStatus.QUEUED,
            created_at=datetime.now(UTC),
            progress=None,
        )
        mock_redis.get_job = AsyncMock(return_value=job)

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.get("/api/v1/reviews/rev_queued_test/status")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"
        assert data["progress"] is None
