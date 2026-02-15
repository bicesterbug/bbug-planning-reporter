"""
Pydantic schemas for API requests and responses.

Implements [foundation-api:FR-001] - Review request structure
Implements [foundation-api:FR-002] - Application reference validation
Implements [foundation-api:FR-004] - Review response structure
"""

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

# Application reference pattern: YY/NNNNN/XXX
# Examples: 25/01178/REM, 08/00707/F, 23/01421/TCA
APPLICATION_REF_PATTERN = re.compile(r"^\d{2}/\d{4,5}/[A-Z]{1,4}$")


class ReviewOptionsRequest(BaseModel):
    """Options for a review request."""

    focus_areas: list[str] | None = Field(
        default=None,
        description="Specific areas to focus on (cycle_parking, cycle_routes, junctions, permeability)",
    )
    output_format: str = Field(default="markdown", description="Output format (markdown, json)")
    include_policy_matrix: bool = Field(default=True, description="Include policy compliance matrix")
    include_suggested_conditions: bool = Field(
        default=True, description="Include suggested planning conditions"
    )
    # Implements [review-scope-control:FR-001] - Consultation response toggle
    include_consultation_responses: bool = Field(
        default=False,
        description="Include consultation responses from statutory consultees as evidence for LLM analysis",
    )
    # Implements [review-scope-control:FR-002] - Public comment toggle
    include_public_comments: bool = Field(
        default=False,
        description="Include public comments and objection letters as evidence for LLM analysis",
    )
    # Implements [cycle-route-assessment:FR-006] - Per-review destination selection
    destination_ids: list[str] | None = Field(
        default=None,
        description="Destination IDs for route assessment. None = all destinations, [] = skip assessment.",
    )


class ReviewRequest(BaseModel):
    """
    Request body for POST /api/v1/reviews.

    Implements [foundation-api:FR-001] - Submit review request
    Implements [foundation-api:FR-002] - Validate application reference
    Implements [foundation-api:ReviewRequestModels/TS-01] - Valid reference patterns
    Implements [foundation-api:ReviewRequestModels/TS-02] - Invalid reference patterns
    """

    application_ref: str = Field(
        ...,
        description="Cherwell planning application reference (e.g., 25/01178/REM)",
        examples=["25/01178/REM", "23/01421/TCA", "08/00707/F"],
    )
    options: ReviewOptionsRequest | None = Field(
        default=None, description="Optional review configuration"
    )

    @field_validator("application_ref")
    @classmethod
    def validate_application_ref(cls, v: str) -> str:
        """
        Validate application reference format.

        Format: YY/NNNNN/XXX where:
        - YY: 2-digit year
        - NNNNN: 4-5 digit sequence number
        - XXX: 1-4 letter type code (F, REM, TCA, OUT, etc.)
        """
        if not APPLICATION_REF_PATTERN.match(v):
            raise ValueError(
                f"Invalid application reference format: {v}. "
                "Expected format: YY/NNNNN/XXX (e.g., 25/01178/REM)"
            )
        return v


class ReviewLinks(BaseModel):
    """HATEOAS links for a review."""

    self: str
    status: str
    cancel: str


class ReviewSubmitResponse(BaseModel):
    """
    Response for POST /api/v1/reviews (202 Accepted).

    Implements [foundation-api:FR-001] - Return job ID
    """

    review_id: str = Field(..., description="Unique review identifier")
    application_ref: str = Field(..., description="Planning application reference")
    status: str = Field(default="queued", description="Initial status")
    created_at: datetime = Field(..., description="Creation timestamp")
    estimated_duration_seconds: int = Field(
        default=180, description="Estimated processing time in seconds"
    )
    links: ReviewLinks = Field(..., description="Related resource links")


class ReviewProgressResponse(BaseModel):
    """Progress information in status responses."""

    phase: str
    phase_number: int | None = None
    total_phases: int | None = None
    percent_complete: int
    detail: str | None = None


class ReviewStatusResponse(BaseModel):
    """
    Lightweight response for GET /api/v1/reviews/{id}/status.

    Implements [foundation-api:FR-003] - Get review status
    """

    review_id: str
    status: str
    progress: ReviewProgressResponse | None = None


class ApplicationInfo(BaseModel):
    """Application metadata in review response."""

    reference: str
    address: str | None = None
    proposal: str | None = None
    applicant: str | None = None
    status: str | None = None
    consultation_end: str | None = None
    documents_fetched: int | None = None
    documents_ingested: int | None = None


class ReviewAspect(BaseModel):
    """Assessment aspect in review response."""

    name: str
    rating: str
    key_issue: str | None = None
    detail: str | None = None
    policy_refs: list[str] | None = None


class PolicyCompliance(BaseModel):
    """Policy compliance item in review response."""

    requirement: str
    policy_source: str
    compliant: bool
    notes: str | None = None


class KeyDocument(BaseModel):
    """
    A key document listed in the review report.

    Implements [key-documents:FR-001] - Key documents array structure
    Implements [key-documents:FR-002] - Category assignment
    Implements [key-documents:FR-003] - LLM-generated summary
    """

    title: str = Field(..., description="Document title from the Cherwell portal")
    category: str = Field(
        ...,
        description="Document category: 'Transport & Access', 'Design & Layout', or 'Application Core'",
    )
    summary: str = Field(
        ...,
        description="1-2 sentence LLM-generated summary of the document's content and cycling relevance",
    )
    url: str | None = Field(
        default=None,
        description="Direct PDF download URL from the Cherwell portal",
    )


class RouteAssessment(BaseModel):
    """
    Cycling route assessment result.

    Implements [cycle-route-assessment:FR-008] - Route assessments in structured output
    """

    destination: str | None = None
    destination_id: str | None = None
    distance_m: float | None = None
    duration_minutes: float | None = None
    provision_breakdown: dict[str, float] | None = None
    score: dict[str, Any] | None = None
    issues: list[dict[str, Any]] | None = None
    s106_suggestions: list[dict[str, Any]] | None = None


class ReviewContent(BaseModel):
    """Review content in response."""

    overall_rating: str | None = None
    summary: str | None = None
    key_documents: list[KeyDocument] | None = None
    aspects: list[ReviewAspect] | None = None
    policy_compliance: list[PolicyCompliance] | None = None
    recommendations: list[str] | None = None
    suggested_conditions: list[str] | None = None
    full_markdown: str | None = None
    # Implements [cycle-route-assessment:FR-008] - Route assessments in review output
    route_assessments: list[RouteAssessment] | None = None


class PolicyRevisionUsed(BaseModel):
    """Policy revision used in review."""

    source: str
    revision_id: str
    version_label: str


class ReviewMetadata(BaseModel):
    """Metadata about review processing."""

    model: str | None = None
    total_tokens_used: int | None = None
    processing_time_seconds: int | None = None
    documents_analysed: int | None = None
    policy_sources_referenced: int | None = None
    policy_effective_date: str | None = None
    policy_revisions_used: list[PolicyRevisionUsed] | None = None


class OutputUrls(BaseModel):
    """URLs to review output artefact files."""

    review_json: str | None = None
    review_md: str | None = None
    routes_json: str | None = None
    letter_md: str | None = None


class ReviewResponse(BaseModel):
    """
    Full response for GET /api/v1/reviews/{id} when completed.

    Implements [foundation-api:FR-004] - Get review result
    """

    review_id: str
    application_ref: str
    status: str
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    progress: ReviewProgressResponse | None = None
    application: ApplicationInfo | None = None
    review: ReviewContent | None = None
    metadata: ReviewMetadata | None = None
    site_boundary: dict[str, Any] | None = None
    urls: OutputUrls | None = None
    error: dict[str, Any] | None = None


class ReviewSummary(BaseModel):
    """Summary of a review for list responses."""

    review_id: str
    application_ref: str
    status: str
    overall_rating: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class ReviewListResponse(BaseModel):
    """
    Response for GET /api/v1/reviews.

    Implements [foundation-api:FR-005] - List reviews
    """

    reviews: list[ReviewSummary]
    total: int
    limit: int
    offset: int


class ErrorDetail(BaseModel):
    """Error detail structure."""

    code: str = Field(..., description="Machine-readable error code")
    message: str = Field(..., description="Human-readable error message")
    details: dict[str, Any] | None = Field(default=None, description="Additional error context")


class ErrorResponse(BaseModel):
    """
    Standard error response format.

    All API errors follow this structure.
    """

    error: ErrorDetail
