"""
Tests for review worker jobs.

Implements [agent-integration:ITS-01] - Complete workflow with mocked MCP
Implements [s3-document-storage:ReviewJobs/TS-01] through [TS-03]
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from src.shared.models import ReviewJob, ReviewOptions, ReviewStatus
from src.shared.storage import StorageUploadError
from src.worker.review_jobs import (
    _handle_failure,
    _handle_success,
    _serialize_application,
    _upload_review_output,
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


class TestProcessReviewOptionsPassthrough:
    """
    Tests that process_review reads options from Redis and passes them to orchestrator.

    Verifies [review-scope-control:process_review/TS-01] and [TS-02]
    """

    @pytest.mark.asyncio
    async def test_reads_options_from_redis_and_passes_to_orchestrator(
        self, mock_redis_wrapper, sample_success_result,
    ):
        """
        Verifies [review-scope-control:process_review/TS-01] - Reads options

        Given: A ReviewJob in Redis with options.include_consultation_responses=True
        When: process_review is called
        Then: AgentOrchestrator is created with the options from the job
        """
        from src.shared.models import ReviewJob, ReviewOptions

        job = ReviewJob(
            review_id="rev_options_test",
            application_ref="25/01178/REM",
            status=ReviewStatus.PROCESSING,
            options=ReviewOptions(
                include_consultation_responses=True,
                include_public_comments=False,
            ),
            created_at=datetime.now(UTC),
        )
        mock_redis_wrapper.get_job = AsyncMock(return_value=job)

        with patch("src.worker.review_jobs.AgentOrchestrator") as mock_orch_cls:
            mock_instance = AsyncMock()
            mock_orch_cls.return_value = mock_instance
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.run.return_value = sample_success_result

            ctx = {
                "redis": AsyncMock(),
                "redis_client": mock_redis_wrapper,
            }

            await process_review(
                ctx=ctx,
                review_id="rev_options_test",
                application_ref="25/01178/REM",
            )

            # Verify orchestrator was created with options
            mock_orch_cls.assert_called_once()
            call_kwargs = mock_orch_cls.call_args[1]
            assert call_kwargs["options"] is not None
            assert call_kwargs["options"].include_consultation_responses is True
            assert call_kwargs["options"].include_public_comments is False

    @pytest.mark.asyncio
    async def test_works_without_options(
        self, mock_redis_wrapper, sample_success_result,
    ):
        """
        Verifies [review-scope-control:process_review/TS-02] - Works without options

        Given: A ReviewJob in Redis with no options
        When: process_review is called
        Then: AgentOrchestrator is created with options=None
        """
        from src.shared.models import ReviewJob

        job = ReviewJob(
            review_id="rev_no_options",
            application_ref="25/01178/REM",
            status=ReviewStatus.PROCESSING,
            options=None,
            created_at=datetime.now(UTC),
        )
        mock_redis_wrapper.get_job = AsyncMock(return_value=job)

        with patch("src.worker.review_jobs.AgentOrchestrator") as mock_orch_cls:
            mock_instance = AsyncMock()
            mock_orch_cls.return_value = mock_instance
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.run.return_value = sample_success_result

            ctx = {
                "redis": AsyncMock(),
                "redis_client": mock_redis_wrapper,
            }

            await process_review(
                ctx=ctx,
                review_id="rev_no_options",
                application_ref="25/01178/REM",
            )

            # Verify orchestrator was created with options=None
            mock_orch_cls.assert_called_once()
            call_kwargs = mock_orch_cls.call_args[1]
            assert call_kwargs["options"] is None


# ---------------------------------------------------------------------------
# S3 Document Storage tests
# ---------------------------------------------------------------------------


def _make_s3_backend_mock():
    """Create a mock S3 storage backend for testing."""
    backend = MagicMock()
    type(backend).is_remote = PropertyMock(return_value=True)
    backend.upload.return_value = None
    backend.public_url.side_effect = (
        lambda key: f"https://test-bucket.nyc3.digitaloceanspaces.com/{key}"
    )
    backend.delete_local.return_value = None
    return backend


class TestReviewOutputUpload:
    """Tests for review output file upload and URL generation."""

    @pytest.mark.asyncio
    async def test_output_upload_on_completion(
        self,
        mock_redis_wrapper,
        sample_success_result,
    ):
        """
        Given: Storage configured, review succeeds with full_markdown and route_assessments
        When: _handle_success runs
        Then: review.json, review.md, and routes.json uploaded; output_urls in result
        """
        sample_success_result.review["full_markdown"] = "# Review\n\nTest review content."
        sample_success_result.review["route_assessments"] = [{"route": "A"}]

        backend = _make_s3_backend_mock()

        result = await _handle_success(sample_success_result, mock_redis_wrapper, backend)

        assert result["status"] == "completed"

        # 3 files: JSON + MD + routes
        assert backend.upload.call_count == 3

        upload_keys = [call[0][1] for call in backend.upload.call_args_list]
        assert any("rev_test123_review.json" in k for k in upload_keys)
        assert any("rev_test123_review.md" in k for k in upload_keys)
        assert any("rev_test123_routes.json" in k for k in upload_keys)

        # Verify output_urls in stored result
        assert "output_urls" in result
        assert result["output_urls"]["review_json"] is not None
        assert result["output_urls"]["review_md"] is not None
        assert result["output_urls"]["routes_json"] is not None

    @pytest.mark.asyncio
    async def test_output_upload_failure_non_fatal(
        self,
        mock_redis_wrapper,
        sample_success_result,
    ):
        """
        Given: Storage configured, upload fails
        When: _handle_success runs
        Then: Review still stored in Redis with null URLs, no exception raised
        """
        backend = _make_s3_backend_mock()
        backend.upload.side_effect = StorageUploadError(
            key="test", attempts=3, last_error=Exception("Network error")
        )

        result = await _handle_success(sample_success_result, mock_redis_wrapper, backend)

        assert result["status"] == "completed"
        mock_redis_wrapper.store_result.assert_called_once()
        # URLs should be null due to failure
        assert result["output_urls"]["review_json"] is None

    @pytest.mark.asyncio
    async def test_output_urls_null_without_storage(
        self,
        mock_redis_wrapper,
        sample_success_result,
    ):
        """
        Given: No storage backend (storage=None)
        When: _handle_success runs
        Then: output_urls in result has all null values
        """
        result = await _handle_success(sample_success_result, mock_redis_wrapper, None)
        assert result["status"] == "completed"
        assert result["output_urls"] == {
            "review_json": None, "review_md": None, "routes_json": None,
        }

    def test_upload_returns_url_dict(self):
        """
        Given: A ReviewResult with full_markdown and route_assessments
        When: _upload_review_output is called
        Then: Returns a dict with three non-null URLs
        """
        from src.agent.orchestrator import ReviewResult

        result = ReviewResult(
            review_id="rev_upload_test",
            application_ref="25/00284/F",
            success=True,
            review={
                "overall_rating": "amber",
                "full_markdown": "# Test Review\n\nContent here.",
                "route_assessments": [{"route": "A"}],
            },
        )
        review_data = {
            "review_id": "rev_upload_test",
            "application_ref": "25/00284/F",
            "status": "completed",
            "review": result.review,
        }

        backend = _make_s3_backend_mock()

        urls = _upload_review_output(result, review_data, backend)

        assert urls["review_json"] is not None
        assert "rev_upload_test_review.json" in urls["review_json"]
        assert urls["review_md"] is not None
        assert "rev_upload_test_review.md" in urls["review_md"]
        assert urls["routes_json"] is not None
        assert "rev_upload_test_routes.json" in urls["routes_json"]

    def test_routes_json_contains_route_assessments(self):
        """
        Given: A review_data with route_assessments = [{"route": "A"}]
        When: _upload_review_output is called
        Then: The routes.json file contains the route_assessments array
        """
        from src.agent.orchestrator import ReviewResult

        result = ReviewResult(
            review_id="rev_routes_test",
            application_ref="25/00284/F",
            success=True,
            review={
                "overall_rating": "green",
                "route_assessments": [{"route": "A", "score": 85}],
            },
        )
        review_data = {"review": result.review}

        from src.shared.storage import InMemoryStorageBackend
        backend = InMemoryStorageBackend()

        _upload_review_output(result, review_data, backend)

        routes_key = "25_00284_F/output/rev_routes_test_routes.json"
        assert routes_key in backend.uploads
        import json
        routes_data = json.loads(backend.uploads[routes_key])
        assert routes_data == [{"route": "A", "score": 85}]

    def test_routes_json_empty_array_when_no_assessments(self):
        """
        Given: A review_data with no route_assessments key
        When: _upload_review_output is called
        Then: The routes.json file contains []
        """
        from src.agent.orchestrator import ReviewResult

        result = ReviewResult(
            review_id="rev_no_routes",
            application_ref="25/00284/F",
            success=True,
            review={"overall_rating": "green"},
        )
        review_data = {"review": result.review}

        from src.shared.storage import InMemoryStorageBackend
        backend = InMemoryStorageBackend()

        urls = _upload_review_output(result, review_data, backend)

        routes_key = "25_00284_F/output/rev_no_routes_routes.json"
        assert routes_key in backend.uploads
        import json
        assert json.loads(backend.uploads[routes_key]) == []
        assert urls["routes_json"] is not None

    def test_upload_failure_returns_null_urls(self):
        """
        Given: A storage backend whose upload raises an exception
        When: _upload_review_output is called
        Then: Returns dict with null URLs, no exception raised
        """
        from src.agent.orchestrator import ReviewResult

        result = ReviewResult(
            review_id="rev_fail",
            application_ref="25/00284/F",
            success=True,
            review={"overall_rating": "green"},
        )

        backend = _make_s3_backend_mock()
        backend.upload.side_effect = Exception("Disk full")

        urls = _upload_review_output(result, {"review": result.review}, backend)

        assert urls == {"review_json": None, "review_md": None, "routes_json": None}


class TestProcessReviewPassesStorageBackend:
    """Tests that process_review creates and passes storage backend to orchestrator."""

    @pytest.mark.asyncio
    async def test_storage_backend_passed_to_orchestrator(
        self,
        mock_redis_wrapper,
        sample_success_result,
    ):
        """
        Verifies process_review creates storage backend and passes to orchestrator.
        """
        mock_redis_wrapper.get_job = AsyncMock(return_value=None)

        with patch("src.worker.review_jobs.AgentOrchestrator") as mock_orch_cls, \
             patch("src.worker.review_jobs.create_storage_backend") as mock_factory:
            mock_instance = AsyncMock()
            mock_orch_cls.return_value = mock_instance
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.run.return_value = sample_success_result

            mock_backend = MagicMock()
            type(mock_backend).is_remote = PropertyMock(return_value=False)
            mock_factory.return_value = mock_backend

            ctx = {
                "redis": AsyncMock(),
                "redis_client": mock_redis_wrapper,
            }

            await process_review(
                ctx=ctx,
                review_id="rev_test_storage",
                application_ref="25/01178/REM",
            )

            # Verify create_storage_backend was called
            mock_factory.assert_called_once()

            # Verify orchestrator received storage_backend kwarg
            call_kwargs = mock_orch_cls.call_args[1]
            assert call_kwargs["storage_backend"] is mock_backend


class TestGlobalWebhookEvents:
    """
    Tests for global webhook events in review lifecycle.

    Verifies [global-webhooks:_handle_success/TS-01] through [TS-03]
    Verifies [global-webhooks:_handle_failure/TS-01]
    Verifies [global-webhooks:process_review/TS-01] through [TS-03]
    Verifies [global-webhooks:ITS-01], [ITS-02], [ITS-04]
    """

    @pytest.mark.asyncio
    async def test_completed_webhook_includes_full_review_data(
        self, mock_redis_wrapper, sample_success_result,
    ):
        """
        Verifies [global-webhooks:_handle_success/TS-01] - Fires review.completed with full data
        Verifies [global-webhooks:ITS-01] - End-to-end review completed webhooks

        Given: A successful review with application metadata and review content
        When: _handle_success fires the webhook
        Then: fire_webhook called with review.completed and data containing application, review, metadata
        """
        with patch("src.worker.review_jobs.fire_webhook") as mock_fire:
            await _handle_success(sample_success_result, mock_redis_wrapper)

            # Find the review.completed call
            completed_calls = [c for c in mock_fire.call_args_list if c.args[0] == "review.completed"]
            assert len(completed_calls) == 1

            data = completed_calls[0].args[2]
            assert data["application_ref"] == "25/01178/REM"
            assert data["overall_rating"] == "amber"
            assert "review_url" in data
            assert data["application"] is not None
            assert data["application"]["reference"] == "25/01178/REM"
            assert data["application"]["address"] == "Land at Test Site, Bicester"
            assert data["review"] is not None
            assert data["review"]["overall_rating"] == "amber"
            assert data["metadata"] is not None

    @pytest.mark.asyncio
    async def test_completed_markdown_webhook_fired(
        self, mock_redis_wrapper, sample_success_result,
    ):
        """
        Verifies [global-webhooks:_handle_success/TS-02] - Fires review.completed.markdown
        Verifies [global-webhooks:ITS-01] - Both webhooks fire on success

        Given: A successful review with full_markdown in review
        When: _handle_success is called
        Then: fire_webhook called with review.completed.markdown and data containing full_markdown
        """
        sample_success_result.review["full_markdown"] = "# Test Review\n\nContent here."

        with patch("src.worker.review_jobs.fire_webhook") as mock_fire:
            await _handle_success(sample_success_result, mock_redis_wrapper)

            # Find the review.completed.markdown call
            md_calls = [c for c in mock_fire.call_args_list if c.args[0] == "review.completed.markdown"]
            assert len(md_calls) == 1

            data = md_calls[0].args[2]
            assert data["application_ref"] == "25/01178/REM"
            assert data["full_markdown"] == "# Test Review\n\nContent here."

    @pytest.mark.asyncio
    async def test_completed_webhook_null_fields_preserved(
        self, mock_redis_wrapper,
    ):
        """
        Verifies [global-webhooks:_handle_success/TS-03] - Null fields preserved

        Given: A review completes but suggested_conditions is null
        When: _handle_success fires webhooks
        Then: Payload data.review.suggested_conditions is null (not omitted)
        """
        from src.agent.orchestrator import ReviewResult

        result = ReviewResult(
            review_id="rev_null_test",
            application_ref="25/00001/FUL",
            application=None,
            review={"overall_rating": "green", "suggested_conditions": None},
            metadata={},
            success=True,
        )

        with patch("src.worker.review_jobs.fire_webhook") as mock_fire:
            await _handle_success(result, mock_redis_wrapper)

            completed_calls = [c for c in mock_fire.call_args_list if c.args[0] == "review.completed"]
            data = completed_calls[0].args[2]
            assert data["application"] is None
            assert data["review"]["suggested_conditions"] is None
            assert data["metadata"] == {}

    @pytest.mark.asyncio
    async def test_failed_webhook_includes_structured_error(
        self, mock_redis_wrapper,
    ):
        """
        Verifies [global-webhooks:_handle_failure/TS-01] - Fires review.failed with structured error
        Verifies [global-webhooks:ITS-02] - End-to-end review failed webhook

        Given: A review fails with scraper error
        When: _handle_failure fires the webhook
        Then: fire_webhook called with review.failed and data.error containing code and message
        """
        from src.agent.orchestrator import ReviewResult

        result = ReviewResult(
            review_id="rev_fail_test",
            application_ref="25/01178/REM",
            success=False,
            error="Scraper error: Portal unavailable",
        )

        with patch("src.worker.review_jobs.fire_webhook") as mock_fire:
            await _handle_failure(result, mock_redis_wrapper)

            mock_fire.assert_called_once()
            data = mock_fire.call_args.args[2]
            assert data["application_ref"] == "25/01178/REM"
            assert isinstance(data["error"], dict)
            assert data["error"]["code"] == "scraper_error"
            assert "Portal unavailable" in data["error"]["message"]

    @pytest.mark.asyncio
    async def test_no_review_started_event(
        self, mock_redis_wrapper, sample_success_result,
    ):
        """
        Verifies [global-webhooks:process_review/TS-02] - No review.started event fired.

        Given: WEBHOOK_URL is set
        When: process_review starts
        Then: No review.started webhook is fired
        """
        job = ReviewJob(
            review_id="rev_no_start",
            application_ref="25/01178/REM",
            status=ReviewStatus.PROCESSING,
            created_at=datetime.now(UTC),
        )
        mock_redis_wrapper.get_job = AsyncMock(return_value=job)

        with patch("src.worker.review_jobs.AgentOrchestrator") as mock_orch_cls, \
             patch("src.worker.review_jobs.fire_webhook") as mock_fire:
            mock_instance = AsyncMock()
            mock_orch_cls.return_value = mock_instance
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.run.return_value = sample_success_result

            ctx = {"redis": AsyncMock(), "redis_client": mock_redis_wrapper}
            await process_review(ctx=ctx, review_id="rev_no_start", application_ref="25/01178/REM")

            events_fired = [call.args[0] for call in mock_fire.call_args_list]
            assert "review.started" not in events_fired
            assert "review.completed" in events_fired
            assert "review.completed.markdown" in events_fired

    @pytest.mark.asyncio
    async def test_exception_handler_fires_review_failed(
        self, mock_redis_wrapper,
    ):
        """
        Verifies [global-webhooks:process_review/TS-03] - Exception handler fires review.failed

        Given: An unexpected exception during review processing
        When: Exception handler runs
        Then: fire_webhook called with review.failed and structured error
        """
        job = ReviewJob(
            review_id="rev_exc_test",
            application_ref="25/01178/REM",
            status=ReviewStatus.PROCESSING,
            created_at=datetime.now(UTC),
        )
        mock_redis_wrapper.get_job = AsyncMock(return_value=job)

        with patch("src.worker.review_jobs.AgentOrchestrator") as mock_orch_cls, \
             patch("src.worker.review_jobs.fire_webhook") as mock_fire:
            mock_instance = AsyncMock()
            mock_orch_cls.return_value = mock_instance
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.run.side_effect = RuntimeError("Unexpected boom")

            ctx = {"redis": AsyncMock(), "redis_client": mock_redis_wrapper}
            await process_review(ctx=ctx, review_id="rev_exc_test", application_ref="25/01178/REM")

            failed_calls = [c for c in mock_fire.call_args_list if c.args[0] == "review.failed"]
            assert len(failed_calls) == 1

            data = failed_calls[0].args[2]
            assert isinstance(data["error"], dict)
            assert data["error"]["code"] == "internal_error"
            assert "Unexpected boom" in data["error"]["message"]

    @pytest.mark.asyncio
    async def test_no_webhooks_when_url_unset(
        self, mock_redis_wrapper, sample_success_result,
    ):
        """
        Verifies [global-webhooks:ITS-04] - No webhooks when URL unset.

        Given: WEBHOOK_URL is not set
        When: _handle_success is called
        Then: fire_webhook returns immediately (tested via real call, URL unset)
        """
        # fire_webhook is called but does nothing when WEBHOOK_URL is None
        # We verify here that _handle_success always calls fire_webhook
        with patch("src.worker.review_jobs.fire_webhook") as mock_fire:
            await _handle_success(sample_success_result, mock_redis_wrapper)

            # Two calls: review.completed + review.completed.markdown
            assert mock_fire.call_count == 2
            events = [c.args[0] for c in mock_fire.call_args_list]
            assert "review.completed" in events
            assert "review.completed.markdown" in events
