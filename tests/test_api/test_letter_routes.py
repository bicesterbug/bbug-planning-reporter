"""
Tests for letters API endpoints.

Implements test scenarios from [response-letter:LettersRouter/TS-01] through [TS-09]
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_arq_pool, get_redis_client
from src.api.main import app
from src.shared.models import ReviewJob, ReviewStatus


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def mock_redis():
    """Create mock Redis client."""
    mock = AsyncMock()
    mock.get_job = AsyncMock(return_value=None)
    mock.get_result = AsyncMock(return_value=None)
    mock.store_letter = AsyncMock()
    mock.get_letter = AsyncMock(return_value=None)
    mock.update_letter_status = AsyncMock(return_value=True)
    return mock


@pytest.fixture
def mock_arq_pool():
    """Create mock arq pool."""
    mock = AsyncMock()
    mock.enqueue_job = AsyncMock()
    return mock


@pytest.fixture
def completed_review_job() -> ReviewJob:
    """A completed review job."""
    return ReviewJob(
        review_id="rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
        application_ref="25/01178/REM",
        status=ReviewStatus.COMPLETED,
        created_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )


@pytest.fixture
def processing_review_job() -> ReviewJob:
    """A still-processing review job."""
    return ReviewJob(
        review_id="rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
        application_ref="25/01178/REM",
        status=ReviewStatus.PROCESSING,
        created_at=datetime.now(UTC),
    )


class TestGenerateLetter:
    """Tests for POST /api/v1/reviews/{review_id}/letter."""

    def test_generate_letter_for_completed_review(
        self, client, mock_redis, mock_arq_pool, completed_review_job
    ) -> None:
        """
        Verifies [response-letter:LettersRouter/TS-01]

        Given: A completed review exists in Redis
        When: POST /reviews/{id}/letter with stance=object
        Then: Returns 202 with letter_id, status=generating
        """
        mock_redis.get_job.return_value = completed_review_job
        app.dependency_overrides[get_redis_client] = lambda: mock_redis
        app.dependency_overrides[get_arq_pool] = lambda: mock_arq_pool

        response = client.post(
            "/api/v1/reviews/rev_01HQXK7V3WNPB8MTJF2R5ADGX9/letter",
            json={"stance": "object"},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 202
        data = response.json()
        assert data["letter_id"].startswith("ltr_")
        assert data["review_id"] == "rev_01HQXK7V3WNPB8MTJF2R5ADGX9"
        assert data["status"] == "generating"
        assert "links" in data
        assert "self" in data["links"]

        # Verify letter was stored and job enqueued
        mock_redis.store_letter.assert_called_once()
        mock_arq_pool.enqueue_job.assert_called_once()

    def test_reject_incomplete_review(
        self, client, mock_redis, mock_arq_pool, processing_review_job
    ) -> None:
        """
        Verifies [response-letter:LettersRouter/TS-02]

        Given: A processing review exists
        When: POST /reviews/{id}/letter
        Then: Returns 400 with review_incomplete
        """
        mock_redis.get_job.return_value = processing_review_job
        app.dependency_overrides[get_redis_client] = lambda: mock_redis
        app.dependency_overrides[get_arq_pool] = lambda: mock_arq_pool

        response = client.post(
            "/api/v1/reviews/rev_01HQXK7V3WNPB8MTJF2R5ADGX9/letter",
            json={"stance": "object"},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "review_incomplete"

    def test_reject_nonexistent_review(
        self, client, mock_redis, mock_arq_pool
    ) -> None:
        """
        Verifies [response-letter:LettersRouter/TS-03]

        Given: No review exists with given ID
        When: POST /reviews/{id}/letter
        Then: Returns 404 with review_not_found
        """
        mock_redis.get_job.return_value = None
        app.dependency_overrides[get_redis_client] = lambda: mock_redis
        app.dependency_overrides[get_arq_pool] = lambda: mock_arq_pool

        response = client.post(
            "/api/v1/reviews/rev_nonexistent/letter",
            json={"stance": "object"},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "review_not_found"

    def test_case_officer_from_request(
        self, client, mock_redis, mock_arq_pool, completed_review_job
    ) -> None:
        """
        Verifies [response-letter:LettersRouter/TS-08]

        Given: Request includes case_officer field
        When: POST with case_officer="Mr Jones"
        Then: Letter record stores case_officer="Mr Jones"
        """
        mock_redis.get_job.return_value = completed_review_job
        app.dependency_overrides[get_redis_client] = lambda: mock_redis
        app.dependency_overrides[get_arq_pool] = lambda: mock_arq_pool

        response = client.post(
            "/api/v1/reviews/rev_01HQXK7V3WNPB8MTJF2R5ADGX9/letter",
            json={"stance": "object", "case_officer": "Mr Jones"},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 202

        # Check the letter record stored in Redis has the case officer
        stored_record = mock_redis.store_letter.call_args[0][1]
        assert stored_record["case_officer"] == "Mr Jones"

    def test_custom_letter_date(
        self, client, mock_redis, mock_arq_pool, completed_review_job
    ) -> None:
        """
        Verifies [response-letter:LettersRouter/TS-09]

        Given: Request includes letter_date field
        When: POST with letter_date=2026-03-01
        Then: Letter record stores letter_date=2026-03-01
        """
        mock_redis.get_job.return_value = completed_review_job
        app.dependency_overrides[get_redis_client] = lambda: mock_redis
        app.dependency_overrides[get_arq_pool] = lambda: mock_arq_pool

        response = client.post(
            "/api/v1/reviews/rev_01HQXK7V3WNPB8MTJF2R5ADGX9/letter",
            json={"stance": "conditional", "letter_date": "2026-03-01"},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 202

        stored_record = mock_redis.store_letter.call_args[0][1]
        assert stored_record["letter_date"] == "2026-03-01"
        assert stored_record["stance"] == "conditional"

    def test_invalid_stance_rejected(
        self, client, mock_redis, mock_arq_pool
    ) -> None:
        """Pydantic validation rejects invalid stance."""
        app.dependency_overrides[get_redis_client] = lambda: mock_redis
        app.dependency_overrides[get_arq_pool] = lambda: mock_arq_pool

        response = client.post(
            "/api/v1/reviews/rev_test/letter",
            json={"stance": "invalid"},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 422


class TestGetLetter:
    """Tests for GET /api/v1/letters/{letter_id}."""

    def test_retrieve_completed_letter(self, client, mock_redis) -> None:
        """
        Verifies [response-letter:LettersRouter/TS-04]

        Given: A completed letter record exists in Redis
        When: GET /letters/{letter_id}
        Then: Returns 200 with content as Markdown and metadata
        """
        mock_redis.get_letter.return_value = {
            "letter_id": "ltr_01HQXK7V3WNPB8MTJF2R5ADGX9",
            "review_id": "rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
            "application_ref": "25/01178/REM",
            "stance": "object",
            "tone": "formal",
            "case_officer": "Ms J. Smith",
            "letter_date": "2026-02-07",
            "status": "completed",
            "content": "# Response Letter\n\nDear Ms J. Smith...",
            "metadata": {
                "model": "claude-sonnet-4-5-20250929",
                "input_tokens": 3000,
                "output_tokens": 1500,
                "processing_time_seconds": 12.5,
            },
            "error": None,
            "created_at": "2026-02-07T10:00:00+00:00",
            "completed_at": "2026-02-07T10:00:12+00:00",
        }
        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.get("/api/v1/letters/ltr_01HQXK7V3WNPB8MTJF2R5ADGX9")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["letter_id"] == "ltr_01HQXK7V3WNPB8MTJF2R5ADGX9"
        assert data["status"] == "completed"
        assert data["content"].startswith("# Response Letter")
        assert data["metadata"]["model"] == "claude-sonnet-4-5-20250929"
        assert data["metadata"]["input_tokens"] == 3000
        assert data["stance"] == "object"
        assert data["tone"] == "formal"

    def test_retrieve_generating_letter(self, client, mock_redis) -> None:
        """
        Verifies [response-letter:LettersRouter/TS-05]

        Given: A generating letter record exists
        When: GET /letters/{letter_id}
        Then: Returns 200 with status=generating, no content
        """
        mock_redis.get_letter.return_value = {
            "letter_id": "ltr_01HQXK7V3WNPB8MTJF2R5ADGX9",
            "review_id": "rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
            "application_ref": "25/01178/REM",
            "stance": "object",
            "tone": "formal",
            "case_officer": None,
            "letter_date": None,
            "status": "generating",
            "content": None,
            "metadata": None,
            "error": None,
            "created_at": "2026-02-07T10:00:00+00:00",
            "completed_at": None,
        }
        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.get("/api/v1/letters/ltr_01HQXK7V3WNPB8MTJF2R5ADGX9")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "generating"
        assert data["content"] is None

    def test_retrieve_failed_letter(self, client, mock_redis) -> None:
        """
        Verifies [response-letter:LettersRouter/TS-06]

        Given: A failed letter record exists
        When: GET /letters/{letter_id}
        Then: Returns 200 with status=failed and error details
        """
        mock_redis.get_letter.return_value = {
            "letter_id": "ltr_01HQXK7V3WNPB8MTJF2R5ADGX9",
            "review_id": "rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
            "application_ref": "25/01178/REM",
            "stance": "object",
            "tone": "formal",
            "case_officer": None,
            "letter_date": None,
            "status": "failed",
            "content": None,
            "metadata": None,
            "error": {"code": "letter_generation_failed", "message": "API error"},
            "created_at": "2026-02-07T10:00:00+00:00",
            "completed_at": "2026-02-07T10:00:05+00:00",
        }
        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.get("/api/v1/letters/ltr_01HQXK7V3WNPB8MTJF2R5ADGX9")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert data["error"]["code"] == "letter_generation_failed"

    def test_nonexistent_letter(self, client, mock_redis) -> None:
        """
        Verifies [response-letter:LettersRouter/TS-07]

        Given: No letter exists with given ID
        When: GET /letters/{letter_id}
        Then: Returns 404 with letter_not_found
        """
        mock_redis.get_letter.return_value = None
        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.get("/api/v1/letters/ltr_nonexistent")

        app.dependency_overrides.clear()

        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "letter_not_found"
