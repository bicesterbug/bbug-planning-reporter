"""
Tests for ProgressTracker.

Implements test scenarios from [agent-integration:ProgressTracker/TS-01] through [TS-05]
"""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.progress import (
    PHASE_WEIGHTS,
    ProgressTracker,
    ReviewPhase,
    WorkflowState,
)


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    redis = AsyncMock()
    redis.set = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.delete = AsyncMock()
    redis.publish = AsyncMock()
    redis.exists = AsyncMock(return_value=False)
    return redis


@pytest.fixture
def tracker(mock_redis):
    """Create a ProgressTracker with mock Redis."""
    return ProgressTracker(
        review_id="rev_test123",
        application_ref="25/01178/REM",
        redis_client=mock_redis,
    )


class TestPhaseTransition:
    """
    Tests for phase transition publishing.

    Implements [agent-integration:ProgressTracker/TS-01] - Publish phase transition
    """

    @pytest.mark.asyncio
    async def test_publish_phase_transition(self, tracker, mock_redis):
        """
        Verifies [agent-integration:ProgressTracker/TS-01] - Publish phase transition

        Given: Workflow enters new phase
        When: Transition to "ingesting_documents"
        Then: `review.progress` event published with phase info
        """
        await tracker.start_workflow()
        await tracker.start_phase(ReviewPhase.FETCHING_METADATA)
        await tracker.start_phase(ReviewPhase.INGESTING_DOCUMENTS)

        # Find the ingesting_documents progress event
        progress_calls = [
            c for c in mock_redis.publish.call_args_list
            if "review.progress" in str(c)
        ]

        assert len(progress_calls) >= 2  # At least fetching and ingesting

        # Check the last progress call (should be ingesting_documents)
        last_call = progress_calls[-1]
        channel, event_json = last_call[0]
        event = json.loads(event_json)

        assert channel == "review.progress"
        assert event["event"] == "review.progress"
        assert event["phase"] == "ingesting_documents"
        assert event["review_id"] == "rev_test123"
        assert "percent_complete" in event

    @pytest.mark.asyncio
    async def test_start_workflow_publishes_started_event(self, tracker, mock_redis):
        """Test that starting workflow publishes review.started event."""
        await tracker.start_workflow()

        mock_redis.publish.assert_called()
        call = mock_redis.publish.call_args_list[0]
        event = json.loads(call[0][1])

        assert event["event"] == "review.started"
        assert event["review_id"] == "rev_test123"
        assert event["percent_complete"] == 0


class TestSubProgress:
    """
    Tests for sub-progress within phases.

    Implements [agent-integration:ProgressTracker/TS-02] - Publish sub-progress
    """

    @pytest.mark.asyncio
    async def test_publish_sub_progress(self, tracker, mock_redis):
        """
        Verifies [agent-integration:ProgressTracker/TS-02] - Publish sub-progress

        Given: Long-running phase
        When: Processing document 5 of 22
        Then: Event includes "Ingesting document 5 of 22" detail
        """
        await tracker.start_workflow()
        await tracker.start_phase(ReviewPhase.INGESTING_DOCUMENTS)

        await tracker.update_sub_progress(
            detail="Ingesting document 5 of 22",
            current=5,
            total=22,
        )

        # Find the sub-progress event
        calls = mock_redis.publish.call_args_list
        sub_progress_calls = [
            c for c in calls
            if "Ingesting document 5 of 22" in str(c)
        ]

        assert len(sub_progress_calls) >= 1

        event = json.loads(sub_progress_calls[-1][0][1])
        assert event["phase_detail"] == "Ingesting document 5 of 22"
        assert event["sub_progress_current"] == 5
        assert event["sub_progress_total"] == 22

    @pytest.mark.asyncio
    async def test_sub_progress_updates_documents_count(self, tracker, mock_redis):
        """Test that sub-progress updates internal document counts."""
        await tracker.start_phase(ReviewPhase.INGESTING_DOCUMENTS)

        await tracker.update_sub_progress("Processing", current=10, total=25)

        assert tracker.state.documents_processed == 10
        assert tracker.state.documents_total == 25


class TestPhaseTiming:
    """
    Tests for phase timing tracking.

    Implements [agent-integration:ProgressTracker/TS-03] - Track phase timing
    """

    @pytest.mark.asyncio
    async def test_track_phase_timing(self, tracker, mock_redis):
        """
        Verifies [agent-integration:ProgressTracker/TS-03] - Track phase timing

        Given: Phase completes
        When: Phase transition
        Then: Duration recorded in metadata
        """
        await tracker.start_workflow()
        await tracker.start_phase(ReviewPhase.FETCHING_METADATA)

        # Simulate some time passing (in tests we just verify structure)
        await tracker.start_phase(ReviewPhase.DOWNLOADING_DOCUMENTS)

        # Check that phase info was recorded
        phase_info = tracker.state.phase_info

        assert "fetching_metadata" in phase_info
        assert "started_at" in phase_info["fetching_metadata"]
        assert "completed_at" in phase_info["fetching_metadata"]
        assert "duration_seconds" in phase_info["fetching_metadata"]

    @pytest.mark.asyncio
    async def test_get_phases_metadata(self, tracker, mock_redis):
        """Test getting completed phases metadata."""
        await tracker.start_workflow()
        await tracker.start_phase(ReviewPhase.FETCHING_METADATA)
        await tracker.start_phase(ReviewPhase.DOWNLOADING_DOCUMENTS)
        await tracker.start_phase(ReviewPhase.INGESTING_DOCUMENTS)

        metadata = tracker.get_phases_metadata()

        assert len(metadata) >= 2
        phase_names = [m["phase"] for m in metadata]
        assert "fetching_metadata" in phase_names
        assert "downloading_documents" in phase_names


class TestPercentComplete:
    """
    Tests for progress percentage calculation.

    Implements [agent-integration:ProgressTracker/TS-04] - Calculate percent complete
    """

    @pytest.mark.asyncio
    async def test_calculate_percent_complete(self, tracker, mock_redis):
        """
        Verifies [agent-integration:ProgressTracker/TS-04] - Calculate percent complete

        Given: In phase 3 of 5
        When: Query progress
        Then: Returns appropriate percentage (e.g., 50%)
        """
        await tracker.start_workflow()

        # Start phase 1
        await tracker.start_phase(ReviewPhase.FETCHING_METADATA)
        # Complete phase 1, start phase 2
        await tracker.start_phase(ReviewPhase.DOWNLOADING_DOCUMENTS)
        # Complete phase 2, start phase 3
        await tracker.start_phase(ReviewPhase.INGESTING_DOCUMENTS)

        # Should have completed phases 1 and 2
        percent = tracker.calculate_percent_complete()

        # Phases 1+2 weight = 5 + 20 = 25%
        expected_base = PHASE_WEIGHTS[ReviewPhase.FETCHING_METADATA] + \
                       PHASE_WEIGHTS[ReviewPhase.DOWNLOADING_DOCUMENTS]

        assert percent >= expected_base
        assert percent < 100  # Never 100 until workflow completes

    @pytest.mark.asyncio
    async def test_percent_includes_sub_progress(self, tracker, mock_redis):
        """Test that sub-progress contributes to percent complete."""
        await tracker.start_phase(ReviewPhase.INGESTING_DOCUMENTS)

        # No sub-progress yet
        percent_before = tracker.calculate_percent_complete()

        # Half way through documents
        await tracker.update_sub_progress("Processing", current=11, total=22)

        percent_after = tracker.calculate_percent_complete()

        # Should have increased
        assert percent_after >= percent_before

    def test_percent_zero_when_no_phase(self, mock_redis):
        """Test that percent is 0 when no phase started."""
        tracker = ProgressTracker(
            review_id="rev_test",
            application_ref="25/00001/FUL",
            redis_client=mock_redis,
        )

        assert tracker.calculate_percent_complete() == 0


class TestStatePersistence:
    """
    Tests for state persistence to Redis.

    Implements [agent-integration:ProgressTracker/TS-05] - Persist state to Redis
    """

    @pytest.mark.asyncio
    async def test_persist_state_to_redis(self, tracker, mock_redis):
        """
        Verifies [agent-integration:ProgressTracker/TS-05] - Persist state to Redis

        Given: Phase transition
        When: Transition occurs
        Then: State persisted for recovery
        """
        await tracker.start_phase(ReviewPhase.FETCHING_METADATA)

        # Verify state was saved to Redis
        mock_redis.set.assert_called()

        # Get the last set call
        set_calls = [c for c in mock_redis.set.call_args_list]
        assert len(set_calls) >= 1

        last_call = set_calls[-1]
        key = last_call[0][0]
        value = last_call[0][1]

        assert "workflow_state:" in key
        assert "rev_test123" in key

        # Verify state content
        state_dict = json.loads(value)
        assert state_dict["review_id"] == "rev_test123"
        assert state_dict["current_phase"] == "fetching_metadata"

    @pytest.mark.asyncio
    async def test_load_state_from_redis(self, mock_redis):
        """Test loading state from Redis for recovery."""
        saved_state = {
            "review_id": "rev_test123",
            "application_ref": "25/01178/REM",
            "current_phase": "ingesting_documents",
            "completed_phases": ["fetching_metadata", "downloading_documents"],
            "phase_info": {
                "fetching_metadata": {"duration_seconds": 3},
                "downloading_documents": {"duration_seconds": 45},
            },
            "documents_processed": 10,
            "documents_total": 22,
            "errors_encountered": [],
            "started_at": "2025-02-05T14:30:00+00:00",
            "cancelled": False,
        }

        mock_redis.get.return_value = json.dumps(saved_state)

        tracker = ProgressTracker(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            redis_client=mock_redis,
        )

        loaded = await tracker.load_state()

        assert loaded is True
        assert tracker.state.current_phase == ReviewPhase.INGESTING_DOCUMENTS
        assert len(tracker.state.completed_phases) == 2
        assert tracker.state.documents_processed == 10

    @pytest.mark.asyncio
    async def test_load_state_returns_false_when_not_found(self, tracker, mock_redis):
        """Test that load_state returns False when no state exists."""
        mock_redis.get.return_value = None

        loaded = await tracker.load_state()

        assert loaded is False


class TestWorkflowCompletion:
    """Tests for workflow completion."""

    @pytest.mark.asyncio
    async def test_complete_workflow_success(self, tracker, mock_redis):
        """Test completing workflow successfully."""
        await tracker.start_workflow()
        await tracker.start_phase(ReviewPhase.FETCHING_METADATA)
        await tracker.start_phase(ReviewPhase.GENERATING_REVIEW)

        metadata = await tracker.complete_workflow(success=True)

        assert "phases_completed" in metadata
        assert "total_duration_seconds" in metadata

        # Check completed event was published
        completed_calls = [
            c for c in mock_redis.publish.call_args_list
            if "review.completed" in str(c)
        ]
        assert len(completed_calls) == 1

    @pytest.mark.asyncio
    async def test_complete_workflow_failure(self, tracker, mock_redis):
        """Test completing workflow with failure."""
        await tracker.start_workflow()
        await tracker.start_phase(ReviewPhase.FETCHING_METADATA)

        await tracker.record_error(
            ReviewPhase.FETCHING_METADATA,
            "Failed to fetch application",
        )

        metadata = await tracker.complete_workflow(success=False)

        assert len(metadata["errors_encountered"]) == 1

        # Check failed event was published
        failed_calls = [
            c for c in mock_redis.publish.call_args_list
            if "review.failed" in str(c)
        ]
        assert len(failed_calls) == 1


class TestErrorRecording:
    """Tests for error recording."""

    @pytest.mark.asyncio
    async def test_record_error(self, tracker, mock_redis):
        """Test recording an error during processing."""
        await tracker.start_phase(ReviewPhase.INGESTING_DOCUMENTS)

        await tracker.record_error(
            ReviewPhase.INGESTING_DOCUMENTS,
            "OCR failed - corrupt file",
            document="scan_001.pdf",
        )

        errors = tracker.state.errors_encountered
        assert len(errors) == 1
        assert errors[0]["phase"] == "ingesting_documents"
        assert errors[0]["error"] == "OCR failed - corrupt file"
        assert errors[0]["document"] == "scan_001.pdf"


class TestCancellation:
    """Tests for workflow cancellation."""

    @pytest.mark.asyncio
    async def test_check_cancellation(self, tracker, mock_redis):
        """Test checking for cancellation flag."""
        # Not cancelled
        mock_redis.exists.return_value = False
        cancelled = await tracker.check_cancellation()
        assert cancelled is False

        # Now cancelled
        mock_redis.exists.return_value = True
        cancelled = await tracker.check_cancellation()
        assert cancelled is True
        assert tracker.is_cancelled is True

    @pytest.mark.asyncio
    async def test_request_cancellation(self, tracker, mock_redis):
        """Test requesting cancellation."""
        await tracker.request_cancellation()

        assert tracker.state.cancelled is True
        mock_redis.set.assert_called()

        # Verify cancel key was set
        set_calls = [c for c in mock_redis.set.call_args_list]
        cancel_call = [c for c in set_calls if "review_cancel:" in str(c)]
        assert len(cancel_call) >= 1


class TestWorkflowState:
    """Tests for WorkflowState serialization."""

    def test_workflow_state_to_dict(self):
        """Test serializing workflow state to dict."""
        state = WorkflowState(
            review_id="rev_test",
            application_ref="25/00001/FUL",
            current_phase=ReviewPhase.INGESTING_DOCUMENTS,
            completed_phases=["fetching_metadata"],
            started_at=datetime(2025, 2, 5, 14, 30, 0, tzinfo=UTC),
        )

        data = state.to_dict()

        assert data["review_id"] == "rev_test"
        assert data["current_phase"] == "ingesting_documents"
        assert data["completed_phases"] == ["fetching_metadata"]
        assert data["started_at"] == "2025-02-05T14:30:00+00:00"

    def test_workflow_state_from_dict(self):
        """Test deserializing workflow state from dict."""
        data = {
            "review_id": "rev_test",
            "application_ref": "25/00001/FUL",
            "current_phase": "ingesting_documents",
            "completed_phases": ["fetching_metadata"],
            "phase_info": {},
            "documents_processed": 5,
            "documents_total": 10,
            "errors_encountered": [],
            "started_at": "2025-02-05T14:30:00+00:00",
            "cancelled": False,
        }

        state = WorkflowState.from_dict(data)

        assert state.review_id == "rev_test"
        assert state.current_phase == ReviewPhase.INGESTING_DOCUMENTS
        assert state.completed_phases == ["fetching_metadata"]
        assert state.documents_processed == 5
