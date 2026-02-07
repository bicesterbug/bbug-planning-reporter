"""
Shared data models.

Implements [foundation-api:FR-001] - Review job data structure
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ReviewStatus(StrEnum):
    """Status of a review job."""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProcessingPhase(StrEnum):
    """Processing phases for a review."""

    FETCHING_METADATA = "fetching_metadata"
    DOWNLOADING_DOCUMENTS = "downloading_documents"
    INGESTING_DOCUMENTS = "ingesting_documents"
    ANALYSING_APPLICATION = "analysing_application"
    GENERATING_REVIEW = "generating_review"


class ReviewProgress(BaseModel):
    """Progress information for a review job."""

    phase: ProcessingPhase
    phase_number: int = Field(ge=1, le=5)
    total_phases: int = 5
    percent_complete: int = Field(ge=0, le=100)
    detail: str | None = None


class WebhookConfig(BaseModel):
    """Webhook configuration for a review."""

    url: str
    secret: str
    events: list[str] = Field(
        default=["review.started", "review.progress", "review.completed", "review.failed"]
    )


class ReviewOptions(BaseModel):
    """Options for a review."""

    focus_areas: list[str] | None = None
    output_format: str = "markdown"
    include_policy_matrix: bool = True
    include_suggested_conditions: bool = True
    # Implements [review-scope-control:FR-004] - Toggle fields on internal model
    include_consultation_responses: bool = False
    include_public_comments: bool = False


class ReviewJob(BaseModel):
    """A review job stored in Redis."""

    review_id: str
    application_ref: str
    status: ReviewStatus = ReviewStatus.QUEUED
    options: ReviewOptions | None = None
    webhook: WebhookConfig | None = None
    progress: ReviewProgress | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: dict[str, Any] | None = None
    result_key: str | None = None

    class Config:
        use_enum_values = True


class ReviewJobSummary(BaseModel):
    """Summary of a review job for list responses."""

    review_id: str
    application_ref: str
    status: ReviewStatus
    overall_rating: str | None = None
    created_at: datetime
    completed_at: datetime | None = None

    class Config:
        use_enum_values = True
