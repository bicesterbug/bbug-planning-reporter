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
    FILTERING_DOCUMENTS = "filtering_documents"
    DOWNLOADING_DOCUMENTS = "downloading_documents"
    INGESTING_DOCUMENTS = "ingesting_documents"
    ANALYSING_APPLICATION = "analysing_application"
    ASSESSING_ROUTES = "assessing_routes"  # [cycle-route-assessment:FR-008]
    GENERATING_REVIEW = "generating_review"
    VERIFYING_REVIEW = "verifying_review"


class ReviewProgress(BaseModel):
    """Progress information for a review job."""

    phase: ProcessingPhase
    phase_number: int = Field(ge=1, le=8)
    total_phases: int = 8
    percent_complete: int = Field(ge=0, le=100)
    detail: str | None = None


class ReviewOptions(BaseModel):
    """Options for a review."""

    focus_areas: list[str] | None = None
    output_format: str = "markdown"
    include_policy_matrix: bool = True
    include_suggested_conditions: bool = True
    # Implements [review-scope-control:FR-004] - Toggle fields on internal model
    include_consultation_responses: bool = False
    include_public_comments: bool = False
    # Implements [cycle-route-assessment:FR-006] - Per-review destination selection
    destination_ids: list[str] | None = None


class ReviewJob(BaseModel):
    """A review job stored in Redis."""

    review_id: str
    application_ref: str
    status: ReviewStatus = ReviewStatus.QUEUED
    options: ReviewOptions | None = None
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
