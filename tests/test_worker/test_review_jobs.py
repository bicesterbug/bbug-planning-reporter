"""
Tests for review worker jobs.

Implements [agent-integration:ITS-01] - Complete workflow with mocked MCP
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.shared.models import ReviewStatus
from src.worker.review_jobs import (
    _handle_failure,
    _handle_success,
    _serialize_application,
    process_review,
    review_job,
)


@pytest.fixture
def mock_redis_wrapper():
    """Create mock RedisClient."""
    wrapper = AsyncMock()
    wrapper.update_job_status = AsyncMock()
    wrapper.store_result = AsyncMock()
    return wrapper


@pytest.fixture
def mock_orchestrator():
    """Create mock AgentOrchestrator."""
    with patch("src.worker.review_jobs.AgentOrchestrator") as mock_class:
        mock_instance = AsyncMock()
        mock_class.return_value = mock_instance
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)
        yield mock_instance


@pytest.fixture
def sample_application():
    """Sample ApplicationMetadata-like object."""
    from src.agent.orchestrator import ApplicationMetadata

    return ApplicationMetadata(
        reference="25/01178/REM",
        address="Land at Test Site, Bicester",
        proposal="Reserved matters for residential development",
        applicant="Test Developments Ltd",
        status="Under consideration",
        date_validated="2025-01-20",
        consultation_end="2025-02-15",
        documents=[
            {"id": "doc1", "name": "Transport Assessment.pdf"},
            {"id": "doc2", "name": "Site Plan.pdf"},
        ],
    )


@pytest.fixture
def sample_success_result(sample_application):
    """Sample successful ReviewResult."""
    from src.agent.orchestrator import ReviewResult

    return ReviewResult(
        review_id="rev_test123",
        application_ref="25/01178/REM",
        application=sample_application,
        review={"overall_rating": "amber", "aspects": []},
        metadata={
            "phases_completed": [
                {"phase": "fetching_metadata", "duration_seconds": 2},
                {"phase": "generating_review", "duration_seconds": 10},
            ],
            "total_duration_seconds": 120,
        },
        success=True,
        error=None,
    )


@pytest.fixture
def sample_failure_result():
    """Sample failed ReviewResult."""
    from src.agent.orchestrator import ReviewResult

    return ReviewResult(
        review_id="rev_test123",
        application_ref="25/01178/REM",
        success=False,
        error="Scraper error: Application not found",
        metadata={
            "phases_completed": [{"phase": "fetching_metadata", "duration_seconds": 1}],
            "errors_encountered": [
                {"phase": "fetching_metadata", "error": "Application not found"}
            ],
        },
    )


class TestCompleteWorkflowWithMockedMCP:
    """
    Tests for complete workflow integration.

    Implements [agent-integration:ITS-01] - Complete workflow with mocked MCP
    """

    @pytest.mark.asyncio
    async def test_complete_workflow_success(
        self,
        mock_redis_wrapper,
        mock_orchestrator,
        sample_success_result,
    ):
        """
        Verifies [agent-integration:ITS-01] - Complete workflow with mocked MCP

        Given: Mock MCP servers with fixtures
        When: Submit review job
        Then: All phases complete; review produced
        """
        mock_orchestrator.run.return_value = sample_success_result

        ctx = {
            "redis": AsyncMock(),
            "redis_client": mock_redis_wrapper,
        }

        result = await process_review(
            ctx=ctx,
            review_id="rev_test123",
            application_ref="25/01178/REM",
        )

        # Verify successful completion
        assert result["status"] == "completed"
        assert result["review_id"] == "rev_test123"
        assert result["application"] is not None
        assert result["application"]["reference"] == "25/01178/REM"

        # Verify job status was updated to processing
        # Find the call with PROCESSING status
        processing_calls = [
            call
            for call in mock_redis_wrapper.update_job_status.call_args_list
            if call.kwargs.get("status") == ReviewStatus.PROCESSING
        ]
        assert len(processing_calls) == 1
        assert processing_calls[0].kwargs["review_id"] == "rev_test123"
        assert "started_at" in processing_calls[0].kwargs
        # Verify started_at is a recent datetime (within 5 seconds)
        started_at = processing_calls[0].kwargs["started_at"]
        assert (datetime.now(UTC) - started_at).total_seconds() < 5

        # Verify result was stored
        mock_redis_wrapper.store_result.assert_called_once()
        stored_data = mock_redis_wrapper.store_result.call_args[0][1]
        assert stored_data["status"] == "completed"

    @pytest.mark.asyncio
    async def test_workflow_failure(
        self,
        mock_redis_wrapper,
        mock_orchestrator,
        sample_failure_result,
    ):
        """Test handling of workflow failure."""
        mock_orchestrator.run.return_value = sample_failure_result

        ctx = {
            "redis": AsyncMock(),
            "redis_client": mock_redis_wrapper,
        }

        result = await process_review(
            ctx=ctx,
            review_id="rev_test123",
            application_ref="25/01178/REM",
        )

        # Verify failure handling
        assert result["status"] == "failed"
        assert result["error"]["code"] == "scraper_error"
        assert "not found" in result["error"]["message"].lower()

        # Verify job status was updated to failed
        mock_redis_wrapper.update_job_status.assert_called()
        last_call = mock_redis_wrapper.update_job_status.call_args_list[-1]
        assert last_call.kwargs["status"] == ReviewStatus.FAILED

    @pytest.mark.asyncio
    async def test_unexpected_exception(self, mock_redis_wrapper, mock_orchestrator):
        """Test handling of unexpected exceptions."""
        mock_orchestrator.run.side_effect = RuntimeError("Unexpected error")

        ctx = {
            "redis": AsyncMock(),
            "redis_client": mock_redis_wrapper,
        }

        result = await process_review(
            ctx=ctx,
            review_id="rev_test123",
            application_ref="25/01178/REM",
        )

        assert result["status"] == "failed"
        assert result["error"]["code"] == "internal_error"
        assert "Unexpected error" in result["error"]["message"]


class TestReviewJobFunction:
    """Tests for the arq-compatible review_job function."""

    @pytest.mark.asyncio
    async def test_review_job_delegates_to_process_review(
        self,
        mock_redis_wrapper,
        mock_orchestrator,
        sample_success_result,
    ):
        """Test that review_job correctly delegates to process_review."""
        mock_orchestrator.run.return_value = sample_success_result

        ctx = {
            "redis": AsyncMock(),
            "redis_client": mock_redis_wrapper,
        }

        result = await review_job(
            ctx=ctx,
            review_id="rev_test123",
            application_ref="25/01178/REM",
        )

        assert result["status"] == "completed"


class TestHandleSuccess:
    """Tests for _handle_success helper."""

    @pytest.mark.asyncio
    async def test_handle_success(self, mock_redis_wrapper, sample_success_result):
        """Test successful result handling."""
        result = await _handle_success(sample_success_result, mock_redis_wrapper)

        assert result["status"] == "completed"
        assert result["application"]["reference"] == "25/01178/REM"
        assert result["application"]["documents_fetched"] == 2

        mock_redis_wrapper.store_result.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_success_without_redis(self, sample_success_result):
        """Test success handling without Redis client."""
        result = await _handle_success(sample_success_result, None)

        assert result["status"] == "completed"


class TestHandleFailure:
    """Tests for _handle_failure helper."""

    @pytest.mark.asyncio
    async def test_handle_failure_scraper_error(self, mock_redis_wrapper):
        """Test failure handling with scraper error."""
        from src.agent.orchestrator import ReviewResult

        result = ReviewResult(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            success=False,
            error="Scraper error: Portal unavailable",
        )

        response = await _handle_failure(result, mock_redis_wrapper)

        assert response["status"] == "failed"
        assert response["error"]["code"] == "scraper_error"

    @pytest.mark.asyncio
    async def test_handle_failure_cancelled(self, mock_redis_wrapper):
        """Test failure handling for cancelled review."""
        from src.agent.orchestrator import ReviewResult

        result = ReviewResult(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            success=False,
            error="Workflow cancelled",
        )

        response = await _handle_failure(result, mock_redis_wrapper)

        assert response["error"]["code"] == "review_cancelled"

    @pytest.mark.asyncio
    async def test_handle_failure_ingestion_error(self, mock_redis_wrapper):
        """Test failure handling for ingestion error."""
        from src.agent.orchestrator import ReviewResult

        result = ReviewResult(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            success=False,
            error="No documents could be ingested",
        )

        response = await _handle_failure(result, mock_redis_wrapper)

        assert response["error"]["code"] == "ingestion_failed"


class TestSerializeApplication:
    """Tests for _serialize_application helper."""

    def test_serialize_application(self, sample_application):
        """Test application serialization."""
        result = _serialize_application(sample_application)

        assert result["reference"] == "25/01178/REM"
        assert result["address"] == "Land at Test Site, Bicester"
        assert result["documents_fetched"] == 2

    def test_serialize_none_application(self):
        """Test serialization of None application."""
        result = _serialize_application(None)
        assert result == {}

    def test_serialize_application_no_documents(self):
        """Test serialization with no documents."""
        from src.agent.orchestrator import ApplicationMetadata

        app = ApplicationMetadata(
            reference="25/00001/FUL",
            documents=None,
        )

        result = _serialize_application(app)
        assert result["documents_fetched"] == 0
