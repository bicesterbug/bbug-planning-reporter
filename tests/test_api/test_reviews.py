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
    mock.get_active_review_id_for_ref = AsyncMock(return_value=None)
    mock.get_latest_completed_review_id_for_ref = AsyncMock(return_value=None)
    mock.store_job = AsyncMock()
    mock.get_job = AsyncMock(return_value=None)
    mock.list_jobs = AsyncMock(return_value=([], 0))
    mock.update_job_status = AsyncMock(return_value=True)
    mock.get_result = AsyncMock(return_value=None)
    mock.get_review_letter_url = AsyncMock(return_value=None)
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
        Given: Application with existing active review
        When: POST /reviews for same application (no force)
        Then: Returns 409 with review_in_progress error and active_review_id
        """
        mock_redis.get_active_review_id_for_ref = AsyncMock(return_value="rev_active_123")
        mock_redis.get_latest_completed_review_id_for_ref = AsyncMock(return_value=None)

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.post(
            "/api/v1/reviews",
            json={"application_ref": "25/01178/REM"},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 409
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == "review_in_progress"
        assert data["error"]["details"]["active_review_id"] == "rev_active_123"

    def test_resubmission_accepted_when_previous_completed(self, client, mock_redis):
        """
        Given: A completed review exists, no active review
        When: POST /reviews for same application
        Then: Returns 202 with a new review_id
        """
        mock_redis.get_active_review_id_for_ref = AsyncMock(return_value=None)
        mock_redis.get_latest_completed_review_id_for_ref = AsyncMock(return_value="rev_previous")
        mock_arq = AsyncMock()
        mock_arq.enqueue_job = AsyncMock()

        app.dependency_overrides[get_redis_client] = lambda: mock_redis
        app.dependency_overrides[get_arq_pool] = lambda: mock_arq

        response = client.post(
            "/api/v1/reviews",
            json={"application_ref": "25/01178/REM"},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 202
        data = response.json()
        assert data["review_id"].startswith("rev_")

    def test_force_cancels_active_review(self, client, mock_redis):
        """
        Given: An active review exists
        When: POST /reviews?force=true
        Then: Returns 202, active review is cancelled
        """
        mock_redis.get_active_review_id_for_ref = AsyncMock(return_value="rev_active_123")
        mock_redis.get_latest_completed_review_id_for_ref = AsyncMock(return_value=None)
        mock_arq = AsyncMock()
        mock_arq.enqueue_job = AsyncMock()

        app.dependency_overrides[get_redis_client] = lambda: mock_redis
        app.dependency_overrides[get_arq_pool] = lambda: mock_arq

        response = client.post(
            "/api/v1/reviews?force=true",
            json={"application_ref": "25/01178/REM"},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 202
        mock_redis.update_job_status.assert_any_call("rev_active_123", ReviewStatus.CANCELLED)

    def test_previous_review_id_passed_to_worker(self, client, mock_redis):
        """
        Given: A completed review exists
        When: POST /reviews
        Then: arq job enqueued with previous_review_id
        """
        mock_redis.get_active_review_id_for_ref = AsyncMock(return_value=None)
        mock_redis.get_latest_completed_review_id_for_ref = AsyncMock(return_value="rev_prev_456")
        mock_arq = AsyncMock()
        mock_arq.enqueue_job = AsyncMock()

        app.dependency_overrides[get_redis_client] = lambda: mock_redis
        app.dependency_overrides[get_arq_pool] = lambda: mock_arq

        response = client.post(
            "/api/v1/reviews",
            json={"application_ref": "25/01178/REM"},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 202
        mock_arq.enqueue_job.assert_called_once()
        _, kwargs = mock_arq.enqueue_job.call_args
        assert kwargs.get("previous_review_id") == "rev_prev_456"


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


class TestSiteBoundaryEndpoint:
    """
    Tests for GET /api/v1/reviews/{review_id}/site-boundary.

    Verifies [cycle-route-assessment:SiteBoundaryEndpoint/TS-01] through TS-03.
    """

    def _make_geojson(self):
        """Create a sample GeoJSON FeatureCollection."""
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[-1.15, 51.9], [-1.14, 51.9], [-1.14, 51.91], [-1.15, 51.91], [-1.15, 51.9]]],
                    },
                    "properties": {"application_ref": "21/03267/OUT"},
                },
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [-1.145, 51.905],
                    },
                    "properties": {"type": "centroid"},
                },
            ],
        }

    def test_boundary_returned_for_completed_review(self, client, mock_redis):
        """
        Verifies [cycle-route-assessment:SiteBoundaryEndpoint/TS-01]

        Given: Review with site_boundary in result
        When: GET /api/v1/reviews/{id}/site-boundary
        Then: 200 with GeoJSON FeatureCollection
        """
        job = ReviewJob(
            review_id="rev_boundary",
            application_ref="21/03267/OUT",
            status=ReviewStatus.COMPLETED,
            created_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        mock_redis.get_job = AsyncMock(return_value=job)
        mock_redis.get_result = AsyncMock(return_value={
            "review": {"overall_rating": "amber"},
            "metadata": {"site_boundary": self._make_geojson()},
        })

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.get("/api/v1/reviews/rev_boundary/site-boundary")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/geo+json"
        data = response.json()
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) == 2
        # First feature is polygon, second is centroid point
        assert data["features"][0]["geometry"]["type"] == "Polygon"
        assert data["features"][1]["geometry"]["type"] == "Point"

    def test_404_when_no_boundary(self, client, mock_redis):
        """
        Verifies [cycle-route-assessment:SiteBoundaryEndpoint/TS-02]

        Given: Review completed but boundary lookup failed
        When: GET /api/v1/reviews/{id}/site-boundary
        Then: 404 with site_boundary_not_found
        """
        job = ReviewJob(
            review_id="rev_no_boundary",
            application_ref="21/03267/OUT",
            status=ReviewStatus.COMPLETED,
            created_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        mock_redis.get_job = AsyncMock(return_value=job)
        mock_redis.get_result = AsyncMock(return_value={
            "review": {"overall_rating": "amber"},
            "metadata": {"model": "claude-3-sonnet"},
        })

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.get("/api/v1/reviews/rev_no_boundary/site-boundary")

        app.dependency_overrides.clear()

        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "site_boundary_not_found"

    def test_404_for_unknown_review(self, client, mock_redis):
        """
        Verifies [cycle-route-assessment:SiteBoundaryEndpoint/TS-03]

        Given: No review with this ID
        When: GET /api/v1/reviews/{id}/site-boundary
        Then: 404 with review_not_found
        """
        mock_redis.get_job = AsyncMock(return_value=None)

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.get("/api/v1/reviews/rev_nonexistent/site-boundary")

        app.dependency_overrides.clear()

        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "review_not_found"


class TestReviewContentRouteAssessments:
    """
    Tests for route_assessments in ReviewContent schema.

    Verifies [cycle-route-assessment:ReviewContent/TS-01] and TS-02.
    """

    def test_review_with_route_assessments(self, client, mock_redis):
        """
        Verifies [cycle-route-assessment:ReviewContent/TS-01]

        Given: Completed review with route_assessments array
        When: GET /api/v1/reviews/{id}
        Then: route_assessments populated in response
        """
        job = ReviewJob(
            review_id="rev_routes",
            application_ref="21/03267/OUT",
            status=ReviewStatus.COMPLETED,
            created_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        mock_redis.get_job = AsyncMock(return_value=job)
        mock_redis.get_result = AsyncMock(return_value={
            "review": {
                "overall_rating": "amber",
                "summary": "Needs cycle improvements",
                "route_assessments": [
                    {
                        "destination": "Bicester North",
                        "destination_id": "dest_001",
                        "distance_m": 2500,
                        "duration_minutes": 10.0,
                        "provision_breakdown": {"segregated": 1500, "none": 1000},
                        "score": {"score": 55, "rating": "amber"},
                        "issues": [{"severity": "high", "problem": "No cycle lane"}],
                        "s106_suggestions": [{"suggestion": "Fund cycleway"}],
                    }
                ],
            },
            "application": {"reference": "21/03267/OUT"},
            "metadata": {"model": "claude-3-sonnet"},
        })

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.get("/api/v1/reviews/rev_routes")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["review"]["route_assessments"] is not None
        assert len(data["review"]["route_assessments"]) == 1
        route = data["review"]["route_assessments"][0]
        assert route["destination"] == "Bicester North"
        assert route["destination_id"] == "dest_001"
        assert route["distance_m"] == 2500
        assert route["score"]["rating"] == "amber"

    def test_review_without_route_assessments(self, client, mock_redis):
        """
        Verifies [cycle-route-assessment:ReviewContent/TS-02]

        Given: Completed review without route_assessments
        When: GET /api/v1/reviews/{id}
        Then: route_assessments is None, no error
        """
        job = ReviewJob(
            review_id="rev_no_routes",
            application_ref="25/01178/REM",
            status=ReviewStatus.COMPLETED,
            created_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        mock_redis.get_job = AsyncMock(return_value=job)
        mock_redis.get_result = AsyncMock(return_value={
            "review": {"overall_rating": "green", "summary": "Good provision"},
            "application": {"reference": "25/01178/REM"},
            "metadata": {"model": "claude-3-sonnet"},
        })

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.get("/api/v1/reviews/rev_no_routes")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["review"]["overall_rating"] == "green"
        # route_assessments should be None/absent - not cause an error
        assert data["review"].get("route_assessments") is None


class TestSiteBoundaryIntegration:
    """
    Integration test for site boundary in full review response.

    Verifies [cycle-route-assessment:ITS-04] - Site boundary API with review data.
    """

    def test_completed_review_includes_site_boundary(self, client, mock_redis):
        """
        Verifies [cycle-route-assessment:ITS-04]

        Given: Completed review with site_boundary in Redis
        When: GET /api/v1/reviews/{id}
        Then: site_boundary field populated in review response
        """
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [[[-1.15, 51.9], [-1.14, 51.9], [-1.14, 51.91], [-1.15, 51.9]]]},
                    "properties": {},
                },
            ],
        }

        job = ReviewJob(
            review_id="rev_boundary_int",
            application_ref="21/03267/OUT",
            status=ReviewStatus.COMPLETED,
            created_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        mock_redis.get_job = AsyncMock(return_value=job)
        mock_redis.get_result = AsyncMock(return_value={
            "review": {"overall_rating": "amber"},
            "application": {"reference": "21/03267/OUT"},
            "metadata": {"site_boundary": geojson, "model": "claude-3-sonnet"},
        })

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.get("/api/v1/reviews/rev_boundary_int")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["site_boundary"] is not None
        assert data["site_boundary"]["type"] == "FeatureCollection"

    def test_completed_review_without_site_boundary(self, client, mock_redis):
        """
        Verifies site_boundary is None when metadata has no boundary.

        Given: Completed review without site_boundary in metadata
        When: GET /api/v1/reviews/{id}
        Then: site_boundary is None
        """
        job = ReviewJob(
            review_id="rev_no_boundary_int",
            application_ref="25/01178/REM",
            status=ReviewStatus.COMPLETED,
            created_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        mock_redis.get_job = AsyncMock(return_value=job)
        mock_redis.get_result = AsyncMock(return_value={
            "review": {"overall_rating": "green"},
            "application": {"reference": "25/01178/REM"},
            "metadata": {"model": "claude-3-sonnet"},
        })

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.get("/api/v1/reviews/rev_no_boundary_int")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["site_boundary"] is None


class TestUrlsOnlyParameter:
    """Tests for the urls_only query parameter on GET /reviews/{id}."""

    def test_urls_only_omits_review_and_metadata(self, client, mock_redis):
        """
        Given: A completed review with output_urls in result
        When: GET /reviews/{id}?urls_only=true
        Then: Response has urls object, review/metadata/site_boundary are null
        """
        job = ReviewJob(
            review_id="rev_urls_test",
            application_ref="25/01178/REM",
            status=ReviewStatus.COMPLETED,
            created_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        mock_redis.get_job = AsyncMock(return_value=job)
        mock_redis.get_result = AsyncMock(return_value={
            "review": {"overall_rating": "amber", "full_markdown": "# Review"},
            "application": {"reference": "25/01178/REM"},
            "metadata": {"model": "claude-3-sonnet", "site_boundary": {"type": "Feature"}},
            "output_urls": {
                "review_json": "https://s3.example.com/review.json",
                "review_md": "https://s3.example.com/review.md",
                "routes_json": "https://s3.example.com/routes.json",
            },
        })
        mock_redis.get_review_letter_url = AsyncMock(return_value=None)

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.get("/api/v1/reviews/rev_urls_test?urls_only=true")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["review"] is None
        assert data["metadata"] is None
        assert data["site_boundary"] is None
        assert data["urls"] is not None
        assert data["urls"]["review_json"] == "https://s3.example.com/review.json"
        assert data["urls"]["review_md"] == "https://s3.example.com/review.md"
        assert data["urls"]["routes_json"] == "https://s3.example.com/routes.json"
        assert data["urls"]["letter_md"] is None
        # Application info should still be present
        assert data["application"]["reference"] == "25/01178/REM"

    def test_default_includes_both_data_and_urls(self, client, mock_redis):
        """
        Given: A completed review with output_urls
        When: GET /reviews/{id} (no urls_only)
        Then: Response has review, metadata AND urls
        """
        job = ReviewJob(
            review_id="rev_both_test",
            application_ref="25/01178/REM",
            status=ReviewStatus.COMPLETED,
            created_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        mock_redis.get_job = AsyncMock(return_value=job)
        mock_redis.get_result = AsyncMock(return_value={
            "review": {"overall_rating": "amber"},
            "application": {"reference": "25/01178/REM"},
            "metadata": {"model": "claude-3-sonnet"},
            "output_urls": {
                "review_json": "/api/v1/files/review.json",
                "review_md": "/api/v1/files/review.md",
                "routes_json": "/api/v1/files/routes.json",
            },
        })
        mock_redis.get_review_letter_url = AsyncMock(return_value=None)

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.get("/api/v1/reviews/rev_both_test")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["review"] is not None
        assert data["metadata"] is not None
        assert data["urls"] is not None

    def test_urls_only_no_effect_on_processing_review(self, client, mock_redis):
        """
        Given: A review with status processing
        When: GET /reviews/{id}?urls_only=true
        Then: Response has status processing, urls is null
        """
        job = ReviewJob(
            review_id="rev_proc_test",
            application_ref="25/01178/REM",
            status=ReviewStatus.PROCESSING,
            created_at=datetime.now(UTC),
        )
        mock_redis.get_job = AsyncMock(return_value=job)

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.get("/api/v1/reviews/rev_proc_test?urls_only=true")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "processing"
        assert data["urls"] is None

    def test_older_review_without_output_urls(self, client, mock_redis):
        """
        Given: A completed review stored WITHOUT output_urls (pre-migration)
        When: GET /reviews/{id}?urls_only=true
        Then: urls object has all null values
        """
        job = ReviewJob(
            review_id="rev_old_test",
            application_ref="25/01178/REM",
            status=ReviewStatus.COMPLETED,
            created_at=datetime.now(UTC),
        )
        mock_redis.get_job = AsyncMock(return_value=job)
        mock_redis.get_result = AsyncMock(return_value={
            "review": {"overall_rating": "green"},
            "application": {"reference": "25/01178/REM"},
            "metadata": {},
        })
        mock_redis.get_review_letter_url = AsyncMock(return_value=None)

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.get("/api/v1/reviews/rev_old_test?urls_only=true")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["urls"]["review_json"] is None
        assert data["urls"]["review_md"] is None
        assert data["urls"]["routes_json"] is None
        assert data["urls"]["letter_md"] is None

    def test_letter_url_included_when_letter_exists(self, client, mock_redis):
        """
        Given: A completed review AND a letter URL stored
        When: GET /reviews/{id}?urls_only=true
        Then: urls.letter_md is non-null
        """
        job = ReviewJob(
            review_id="rev_letter_url_test",
            application_ref="25/01178/REM",
            status=ReviewStatus.COMPLETED,
            created_at=datetime.now(UTC),
        )
        mock_redis.get_job = AsyncMock(return_value=job)
        mock_redis.get_result = AsyncMock(return_value={
            "review": {"overall_rating": "amber"},
            "application": {"reference": "25/01178/REM"},
            "metadata": {},
            "output_urls": {
                "review_json": "/api/v1/files/review.json",
                "review_md": "/api/v1/files/review.md",
                "routes_json": "/api/v1/files/routes.json",
            },
        })
        mock_redis.get_review_letter_url = AsyncMock(
            return_value="/api/v1/files/25_01178_REM/output/ltr_01_letter.md"
        )

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.get("/api/v1/reviews/rev_letter_url_test?urls_only=true")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["urls"]["letter_md"] == "/api/v1/files/25_01178_REM/output/ltr_01_letter.md"
