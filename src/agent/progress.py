"""
Progress Tracker for review workflow.

Implements [agent-integration:FR-015] - Publish progress events at each phase

Implements:
- [agent-integration:ProgressTracker/TS-01] Publish phase transition
- [agent-integration:ProgressTracker/TS-02] Publish sub-progress
- [agent-integration:ProgressTracker/TS-03] Track phase timing
- [agent-integration:ProgressTracker/TS-04] Calculate percent complete
- [agent-integration:ProgressTracker/TS-05] Persist state to Redis
"""

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import redis.asyncio as redis
import structlog

logger = structlog.get_logger(__name__)


class ReviewPhase(Enum):
    """Review workflow phases in execution order."""

    FETCHING_METADATA = "fetching_metadata"
    DOWNLOADING_DOCUMENTS = "downloading_documents"
    INGESTING_DOCUMENTS = "ingesting_documents"
    ANALYSING_APPLICATION = "analysing_application"
    GENERATING_REVIEW = "generating_review"


# Phase weights for progress calculation (must sum to 100)
PHASE_WEIGHTS: dict[ReviewPhase, int] = {
    ReviewPhase.FETCHING_METADATA: 5,
    ReviewPhase.DOWNLOADING_DOCUMENTS: 20,
    ReviewPhase.INGESTING_DOCUMENTS: 30,
    ReviewPhase.ANALYSING_APPLICATION: 30,
    ReviewPhase.GENERATING_REVIEW: 15,
}


@dataclass
class PhaseInfo:
    """Information about a workflow phase."""

    phase: ReviewPhase
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    sub_progress: str | None = None
    sub_progress_current: int | None = None
    sub_progress_total: int | None = None
    error: str | None = None


@dataclass
class WorkflowState:
    """Persistent state of the review workflow."""

    review_id: str
    application_ref: str
    current_phase: ReviewPhase | None = None
    completed_phases: list[str] = field(default_factory=list)
    phase_info: dict[str, dict[str, Any]] = field(default_factory=dict)
    documents_processed: int = 0
    documents_total: int = 0
    errors_encountered: list[dict[str, Any]] = field(default_factory=list)
    started_at: datetime | None = None
    cancelled: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "review_id": self.review_id,
            "application_ref": self.application_ref,
            "current_phase": self.current_phase.value if self.current_phase else None,
            "completed_phases": self.completed_phases,
            "phase_info": self.phase_info,
            "documents_processed": self.documents_processed,
            "documents_total": self.documents_total,
            "errors_encountered": self.errors_encountered,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "cancelled": self.cancelled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowState":
        """Create from dictionary."""
        return cls(
            review_id=data["review_id"],
            application_ref=data["application_ref"],
            current_phase=ReviewPhase(data["current_phase"]) if data.get("current_phase") else None,
            completed_phases=data.get("completed_phases", []),
            phase_info=data.get("phase_info", {}),
            documents_processed=data.get("documents_processed", 0),
            documents_total=data.get("documents_total", 0),
            errors_encountered=data.get("errors_encountered", []),
            started_at=datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None,
            cancelled=data.get("cancelled", False),
        )


class ProgressTracker:
    """
    Tracks and publishes progress for review workflows.

    Implements [agent-integration:ProgressTracker/TS-01] through [TS-05]

    Manages:
    - Phase transition tracking and events
    - Sub-progress within long-running phases
    - Phase timing for performance monitoring
    - State persistence to Redis for recovery
    """

    # Redis key patterns
    STATE_KEY_PREFIX = "workflow_state:"
    PROGRESS_CHANNEL = "review.progress"

    def __init__(
        self,
        review_id: str,
        application_ref: str,
        redis_client: redis.Redis | None = None,
    ) -> None:
        """
        Initialize the progress tracker.

        Args:
            review_id: The review job ID.
            application_ref: The application reference being reviewed.
            redis_client: Optional Redis client for state persistence and pub/sub.
        """
        self._review_id = review_id
        self._application_ref = application_ref
        self._redis = redis_client

        # Initialize state
        self._state = WorkflowState(
            review_id=review_id,
            application_ref=application_ref,
        )

        # Current phase info
        self._current_phase_info: PhaseInfo | None = None

    @property
    def state(self) -> WorkflowState:
        """Get the current workflow state."""
        return self._state

    @property
    def current_phase(self) -> ReviewPhase | None:
        """Get the current phase."""
        return self._state.current_phase

    @property
    def is_cancelled(self) -> bool:
        """Check if the workflow has been cancelled."""
        return self._state.cancelled

    def _state_key(self) -> str:
        """Get Redis key for state storage."""
        return f"{self.STATE_KEY_PREFIX}{self._review_id}"

    async def load_state(self) -> bool:
        """
        Load state from Redis for recovery.

        Returns:
            True if state was loaded, False if not found.
        """
        if self._redis is None:
            return False

        try:
            data = await self._redis.get(self._state_key())
            if data is None:
                return False

            self._state = WorkflowState.from_dict(json.loads(data))
            logger.info(
                "Workflow state loaded from Redis",
                review_id=self._review_id,
                current_phase=self._state.current_phase.value if self._state.current_phase else None,
                completed_phases=self._state.completed_phases,
            )
            return True

        except Exception as e:
            logger.warning(
                "Failed to load workflow state",
                review_id=self._review_id,
                error=str(e),
            )
            return False

    async def _save_state(self) -> None:
        """
        Persist state to Redis.

        Implements [agent-integration:ProgressTracker/TS-05] - Persist state to Redis
        """
        if self._redis is None:
            return

        try:
            await self._redis.set(
                self._state_key(),
                json.dumps(self._state.to_dict()),
                ex=86400,  # 24 hour TTL
            )
        except Exception as e:
            logger.warning(
                "Failed to save workflow state",
                review_id=self._review_id,
                error=str(e),
            )

    async def _publish_progress(self, event_type: str, data: dict[str, Any]) -> None:
        """
        Publish a progress event.

        Implements [agent-integration:ProgressTracker/TS-01] - Publish phase transition
        """
        if self._redis is None:
            return

        event = {
            "event": event_type,
            "review_id": self._review_id,
            "application_ref": self._application_ref,
            "timestamp": datetime.now(UTC).isoformat(),
            **data,
        }

        try:
            await self._redis.publish(self.PROGRESS_CHANNEL, json.dumps(event))
            logger.debug(
                "Progress event published",
                review_id=self._review_id,
                event_type=event_type,
            )
        except Exception as e:
            logger.warning(
                "Failed to publish progress event",
                review_id=self._review_id,
                event_type=event_type,
                error=str(e),
            )

    async def start_workflow(self) -> None:
        """Start tracking the workflow."""
        self._state.started_at = datetime.now(UTC)
        await self._save_state()

        await self._publish_progress(
            "review.started",
            {
                "phase": None,
                "percent_complete": 0,
            },
        )

    async def start_phase(self, phase: ReviewPhase) -> None:
        """
        Start a new phase.

        Implements [agent-integration:ProgressTracker/TS-01] - Publish phase transition
        Implements [agent-integration:ProgressTracker/TS-03] - Track phase timing

        Args:
            phase: The phase to start.
        """
        now = datetime.now(UTC)

        # Complete previous phase if any
        if self._current_phase_info is not None:
            await self._complete_current_phase()

        # Start new phase
        self._current_phase_info = PhaseInfo(
            phase=phase,
            started_at=now,
        )

        self._state.current_phase = phase
        self._state.phase_info[phase.value] = {
            "started_at": now.isoformat(),
        }

        await self._save_state()

        await self._publish_progress(
            "review.progress",
            {
                "phase": phase.value,
                "phase_detail": None,
                "percent_complete": self.calculate_percent_complete(),
            },
        )

        logger.info(
            "Phase started",
            review_id=self._review_id,
            phase=phase.value,
        )

    async def _complete_current_phase(self) -> None:
        """Complete the current phase and record timing."""
        if self._current_phase_info is None:
            return

        now = datetime.now(UTC)
        phase = self._current_phase_info.phase
        started_at = self._current_phase_info.started_at

        if started_at:
            duration = (now - started_at).total_seconds()
            self._current_phase_info.duration_seconds = duration
            self._current_phase_info.completed_at = now

            # Update phase info
            if phase.value in self._state.phase_info:
                self._state.phase_info[phase.value].update({
                    "completed_at": now.isoformat(),
                    "duration_seconds": duration,
                })

        # Add to completed phases
        if phase.value not in self._state.completed_phases:
            self._state.completed_phases.append(phase.value)

        logger.debug(
            "Phase completed",
            review_id=self._review_id,
            phase=phase.value,
            duration_seconds=self._current_phase_info.duration_seconds,
        )

    async def update_sub_progress(
        self,
        detail: str,
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        """
        Update sub-progress within a phase.

        Implements [agent-integration:ProgressTracker/TS-02] - Publish sub-progress

        Args:
            detail: Description of current sub-task.
            current: Current item number (e.g., 5 of 22).
            total: Total item count.
        """
        if self._current_phase_info is None:
            return

        self._current_phase_info.sub_progress = detail
        self._current_phase_info.sub_progress_current = current
        self._current_phase_info.sub_progress_total = total

        # Update documents processed tracking
        if current is not None:
            self._state.documents_processed = current
        if total is not None:
            self._state.documents_total = total

        phase = self._current_phase_info.phase

        await self._publish_progress(
            "review.progress",
            {
                "phase": phase.value,
                "phase_detail": detail,
                "sub_progress_current": current,
                "sub_progress_total": total,
                "percent_complete": self.calculate_percent_complete(),
            },
        )

    def calculate_percent_complete(self) -> int:
        """
        Calculate overall progress percentage.

        Implements [agent-integration:ProgressTracker/TS-04] - Calculate percent complete

        Returns:
            Percentage complete (0-100).
        """
        if self._state.current_phase is None:
            return 0

        total_percent = 0

        # Add completed phase weights
        for phase_value in self._state.completed_phases:
            try:
                phase = ReviewPhase(phase_value)
                total_percent += PHASE_WEIGHTS.get(phase, 0)
            except ValueError:
                pass

        # Add partial progress in current phase based on sub-progress
        current_phase = self._state.current_phase
        current_weight = PHASE_WEIGHTS.get(current_phase, 0)

        if self._current_phase_info and self._current_phase_info.sub_progress_total:
            sub_current = self._current_phase_info.sub_progress_current or 0
            sub_total = self._current_phase_info.sub_progress_total
            sub_percent = sub_current / sub_total
            total_percent += int(current_weight * sub_percent)

        return min(total_percent, 99)  # Never return 100 until workflow completes

    async def record_error(self, phase: ReviewPhase, error: str, document: str | None = None) -> None:
        """
        Record an error during processing.

        Args:
            phase: The phase where the error occurred.
            error: Error description.
            document: Optional document name if error is document-specific.
        """
        error_record = {
            "phase": phase.value,
            "error": error,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        if document:
            error_record["document"] = document

        self._state.errors_encountered.append(error_record)

        if self._current_phase_info and self._current_phase_info.phase == phase:
            self._current_phase_info.error = error

        await self._save_state()

        logger.warning(
            "Error recorded",
            review_id=self._review_id,
            phase=phase.value,
            error=error,
            document=document,
        )

    async def complete_workflow(self, success: bool = True) -> dict[str, Any]:
        """
        Complete the workflow and return final metadata.

        Args:
            success: Whether the workflow completed successfully.

        Returns:
            Workflow completion metadata.
        """
        # Complete current phase
        if self._current_phase_info is not None:
            await self._complete_current_phase()

        # Build phases completed list for output
        phases_completed = []
        for phase_value, info in self._state.phase_info.items():
            phases_completed.append({
                "phase": phase_value,
                "duration_seconds": info.get("duration_seconds", 0),
            })

        metadata = {
            "phases_completed": phases_completed,
            "errors_encountered": self._state.errors_encountered,
            "total_duration_seconds": (
                (datetime.now(UTC) - self._state.started_at).total_seconds()
                if self._state.started_at
                else 0
            ),
        }

        event_type = "review.completed" if success else "review.failed"
        await self._publish_progress(
            event_type,
            {
                "phase": None,
                "percent_complete": 100 if success else self.calculate_percent_complete(),
                **metadata,
            },
        )

        # Clean up Redis state
        if self._redis:
            try:
                await self._redis.delete(self._state_key())
            except Exception:
                pass

        logger.info(
            "Workflow completed",
            review_id=self._review_id,
            success=success,
            total_duration_seconds=metadata["total_duration_seconds"],
        )

        return metadata

    async def check_cancellation(self) -> bool:
        """
        Check if the workflow has been cancelled.

        This should be called between phases to support graceful cancellation.

        Returns:
            True if cancelled, False otherwise.
        """
        if self._redis is None:
            return self._state.cancelled

        try:
            # Check for cancellation flag in Redis
            cancel_key = f"review_cancel:{self._review_id}"
            cancelled = await self._redis.exists(cancel_key)
            if cancelled:
                self._state.cancelled = True
                await self._save_state()
            return bool(cancelled)

        except Exception:
            return self._state.cancelled

    async def request_cancellation(self) -> None:
        """Request cancellation of the workflow."""
        self._state.cancelled = True

        if self._redis:
            try:
                cancel_key = f"review_cancel:{self._review_id}"
                await self._redis.set(cancel_key, "1", ex=3600)
            except Exception:
                pass

    def get_phases_metadata(self) -> list[dict[str, Any]]:
        """
        Get metadata about completed phases.

        Implements [agent-integration:ProgressTracker/TS-03] - Track phase timing

        Returns:
            List of phase metadata dictionaries.
        """
        result = []
        for phase_value, info in self._state.phase_info.items():
            result.append({
                "phase": phase_value,
                "duration_seconds": info.get("duration_seconds"),
            })
        return result
