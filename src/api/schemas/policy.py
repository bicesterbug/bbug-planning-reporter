"""
Pydantic schemas for Policy Knowledge Base API.

Implements [policy-knowledge-base:FR-001] - PolicyDocument structure
Implements [policy-knowledge-base:FR-002] - PolicyRevision structure
Implements [policy-knowledge-base:FR-004] - Temporal metadata fields
"""

import re
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

# Source slug pattern: UPPER_SNAKE_CASE (e.g., LTN_1_20, NPPF, LOCAL_PLAN_2015)
SOURCE_SLUG_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$")


class PolicyCategory(StrEnum):
    """
    Policy document categories.

    Verifies [policy-knowledge-base:PolicyModels/TS-06]
    """

    NATIONAL_POLICY = "national_policy"
    NATIONAL_GUIDANCE = "national_guidance"
    LOCAL_PLAN = "local_plan"
    LOCAL_GUIDANCE = "local_guidance"
    COUNTY_STRATEGY = "county_strategy"
    SUPPLEMENTARY = "supplementary"


class RevisionStatus(StrEnum):
    """Status of a policy revision."""

    PROCESSING = "processing"
    ACTIVE = "active"
    FAILED = "failed"
    SUPERSEDED = "superseded"


# =============================================================================
# Request Models
# =============================================================================


class CreatePolicyRequest(BaseModel):
    """
    Request body for POST /api/v1/policies.

    Verifies [policy-knowledge-base:PolicyModels/TS-01] - Valid source format
    Verifies [policy-knowledge-base:PolicyModels/TS-02] - Invalid source format
    """

    source: str = Field(
        ...,
        description="Unique slug in UPPER_SNAKE_CASE (e.g., LTN_1_20)",
        examples=["LTN_1_20", "NPPF", "CHERWELL_LOCAL_PLAN"],
    )
    title: str = Field(
        ...,
        description="Human-readable title",
        examples=["Cycle Infrastructure Design (LTN 1/20)"],
    )
    description: str | None = Field(
        default=None,
        description="Description of the policy",
    )
    category: PolicyCategory = Field(
        ...,
        description="Policy category",
    )

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        """
        Validate source slug format.

        Verifies [policy-knowledge-base:PolicyModels/TS-01] - Valid source
        Verifies [policy-knowledge-base:PolicyModels/TS-02] - Invalid source
        """
        if not SOURCE_SLUG_PATTERN.match(v):
            raise ValueError(
                f"Invalid source format: {v}. "
                "Expected UPPER_SNAKE_CASE (e.g., LTN_1_20, NPPF)"
            )
        return v


class UpdatePolicyRequest(BaseModel):
    """Request body for PATCH /api/v1/policies/{source}."""

    title: str | None = Field(default=None, description="New title")
    description: str | None = Field(default=None, description="New description")
    category: PolicyCategory | None = Field(default=None, description="New category")


class CreateRevisionRequest(BaseModel):
    """
    Request body for POST /api/v1/policies/{source}/revisions.

    Note: File is uploaded separately as multipart form data.

    Verifies [policy-knowledge-base:PolicyModels/TS-03] - Valid date range
    Verifies [policy-knowledge-base:PolicyModels/TS-04] - Invalid date range
    Verifies [policy-knowledge-base:PolicyModels/TS-05] - Optional effective_to
    """

    version_label: str = Field(
        ...,
        description="Human-readable version (e.g., 'December 2024')",
        examples=["July 2020", "December 2024", "September 2023"],
    )
    effective_from: date = Field(
        ...,
        description="Date from which this revision is in force",
    )
    effective_to: date | None = Field(
        default=None,
        description="Date until which this revision is in force (null = currently in force)",
    )
    notes: str | None = Field(
        default=None,
        description="Notes about this revision",
    )

    @model_validator(mode="after")
    def validate_date_range(self) -> "CreateRevisionRequest":
        """
        Validate effective date range.

        Verifies [policy-knowledge-base:PolicyModels/TS-03] - Valid range
        Verifies [policy-knowledge-base:PolicyModels/TS-04] - Invalid range
        """
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError(
                f"effective_to ({self.effective_to}) must be >= effective_from ({self.effective_from})"
            )
        return self


class UpdateRevisionRequest(BaseModel):
    """Request body for PATCH /api/v1/policies/{source}/revisions/{revision_id}."""

    version_label: str | None = Field(default=None, description="New version label")
    effective_from: date | None = Field(default=None, description="New effective_from date")
    effective_to: date | None = Field(default=None, description="New effective_to date")
    notes: str | None = Field(default=None, description="New notes")


# =============================================================================
# Response Models
# =============================================================================


class PolicyRevisionSummary(BaseModel):
    """Summary of a policy revision for list responses."""

    revision_id: str = Field(..., description="Unique revision identifier")
    version_label: str = Field(..., description="Human-readable version")
    effective_from: date = Field(..., description="Effective from date")
    effective_to: date | None = Field(default=None, description="Effective to date")
    status: RevisionStatus = Field(..., description="Revision status")
    chunk_count: int | None = Field(default=None, description="Number of chunks in ChromaDB")
    ingested_at: datetime | None = Field(default=None, description="When ingestion completed")


class PolicyRevisionDetail(PolicyRevisionSummary):
    """Full policy revision details."""

    source: str = Field(..., description="Parent policy source slug")
    file_path: str | None = Field(default=None, description="Path to source PDF")
    file_size_bytes: int | None = Field(default=None, description="File size")
    page_count: int | None = Field(default=None, description="Number of pages")
    notes: str | None = Field(default=None, description="Revision notes")
    created_at: datetime = Field(..., description="When revision was created")
    error: str | None = Field(default=None, description="Error message if failed")


class PolicyDocumentSummary(BaseModel):
    """Summary of a policy document for list responses."""

    source: str = Field(..., description="Unique source slug")
    title: str = Field(..., description="Human-readable title")
    category: PolicyCategory = Field(..., description="Policy category")
    current_revision: PolicyRevisionSummary | None = Field(
        default=None, description="Currently active revision"
    )
    revision_count: int = Field(default=0, description="Total number of revisions")


class PolicyDocumentDetail(BaseModel):
    """Full policy document details with all revisions."""

    source: str = Field(..., description="Unique source slug")
    title: str = Field(..., description="Human-readable title")
    description: str | None = Field(default=None, description="Description")
    category: PolicyCategory = Field(..., description="Policy category")
    revisions: list[PolicyRevisionSummary] = Field(
        default_factory=list, description="All revisions ordered by effective_from DESC"
    )
    current_revision: PolicyRevisionSummary | None = Field(
        default=None, description="Currently active revision"
    )
    revision_count: int = Field(default=0, description="Total number of revisions")
    created_at: datetime = Field(..., description="When policy was registered")
    updated_at: datetime | None = Field(default=None, description="Last update timestamp")


class PolicyListResponse(BaseModel):
    """Response for GET /api/v1/policies."""

    policies: list[PolicyDocumentSummary] = Field(
        default_factory=list, description="List of policies"
    )
    total: int = Field(default=0, description="Total count")


class RevisionCreateResponse(BaseModel):
    """Response for POST /api/v1/policies/{source}/revisions (202 Accepted)."""

    source: str = Field(..., description="Policy source slug")
    revision_id: str = Field(..., description="Unique revision identifier")
    version_label: str = Field(..., description="Human-readable version")
    effective_from: date = Field(..., description="Effective from date")
    effective_to: date | None = Field(default=None, description="Effective to date")
    status: RevisionStatus = Field(default=RevisionStatus.PROCESSING)
    ingestion_job_id: str = Field(..., description="Job ID for status tracking")
    links: dict[str, str] = Field(default_factory=dict, description="Related URLs")
    side_effects: dict[str, Any] | None = Field(
        default=None, description="Info about superseded revision"
    )


class RevisionStatusResponse(BaseModel):
    """Response for GET /api/v1/policies/{source}/revisions/{revision_id}/status."""

    revision_id: str
    status: RevisionStatus
    progress: dict[str, Any] | None = Field(default=None, description="Processing progress")


class RevisionDeleteResponse(BaseModel):
    """Response for DELETE /api/v1/policies/{source}/revisions/{revision_id}."""

    source: str
    revision_id: str
    status: str = "deleted"
    chunks_removed: int


class EffectivePolicySnapshot(BaseModel):
    """A policy with its effective revision for a given date."""

    source: str
    title: str
    category: PolicyCategory
    effective_revision: PolicyRevisionSummary | None


class EffectiveSnapshotResponse(BaseModel):
    """Response for GET /api/v1/policies/effective."""

    effective_date: date
    policies: list[EffectivePolicySnapshot] = Field(
        default_factory=list, description="Policies with their effective revision"
    )
    policies_not_yet_effective: list[str] = Field(
        default_factory=list, description="Policy sources with no revision for this date (too early)"
    )


# =============================================================================
# Internal Models (for Redis storage)
# =============================================================================


class PolicyDocumentRecord(BaseModel):
    """Internal model for Redis storage of policy document."""

    source: str
    title: str
    description: str | None = None
    category: PolicyCategory
    created_at: datetime
    updated_at: datetime | None = None


class PolicyRevisionRecord(BaseModel):
    """Internal model for Redis storage of policy revision."""

    revision_id: str
    source: str
    version_label: str
    effective_from: date
    effective_to: date | None = None
    status: RevisionStatus = RevisionStatus.PROCESSING
    file_path: str | None = None
    file_size_bytes: int | None = None
    page_count: int | None = None
    chunk_count: int | None = None
    notes: str | None = None
    created_at: datetime
    ingested_at: datetime | None = None
    error: str | None = None
