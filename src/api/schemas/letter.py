"""
Pydantic schemas for Response Letter API.

Implements [response-letter:FR-002] - Stance selection enum
Implements [response-letter:FR-006] - Letter date validation
Implements [response-letter:FR-007] - Tone selection enum
Implements [response-letter:FR-008] - Letter response models
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class LetterStance(StrEnum):
    """
    Advocacy group's position on the planning application.

    Implements [response-letter:FR-002] - Stance selection
    """

    OBJECT = "object"
    SUPPORT = "support"
    CONDITIONAL = "conditional"
    NEUTRAL = "neutral"


class LetterTone(StrEnum):
    """
    Tone for the generated letter.

    Implements [response-letter:FR-007] - Tone selection
    """

    FORMAL = "formal"
    ACCESSIBLE = "accessible"


class LetterStatus(StrEnum):
    """Status of a letter generation job."""

    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


class LetterRequest(BaseModel):
    """
    Request body for POST /api/v1/reviews/{review_id}/letter.

    Implements [response-letter:FR-001] - Generate letter request
    Implements [response-letter:FR-002] - Stance is required
    Implements [response-letter:FR-006] - Optional letter date
    Implements [response-letter:FR-007] - Optional tone
    Implements [response-letter:FR-004] - Optional case officer override
    """

    stance: LetterStance = Field(
        ...,
        description="The group's position: object, support, conditional, or neutral",
    )
    tone: LetterTone = Field(
        default=LetterTone.FORMAL,
        description="Letter tone: formal (default) or accessible",
    )
    case_officer: str | None = Field(
        default=None,
        description="Override the case officer name (e.g. 'Ms J. Smith')",
    )
    letter_date: date | None = Field(
        default=None,
        description="Date for the letter (YYYY-MM-DD). Defaults to generation date.",
    )


class LetterSubmitResponse(BaseModel):
    """
    Response for POST /api/v1/reviews/{review_id}/letter (202 Accepted).

    Implements [response-letter:FR-001] - Returns letter ID for retrieval
    """

    letter_id: str = Field(..., description="Unique letter identifier")
    review_id: str = Field(..., description="Source review ID")
    status: LetterStatus = Field(default=LetterStatus.GENERATING)
    created_at: datetime = Field(..., description="Creation timestamp")
    links: dict[str, str] = Field(default_factory=dict, description="Related URLs")


class LetterMetadata(BaseModel):
    """Metadata about letter generation."""

    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    processing_time_seconds: float | None = None


class LetterResponse(BaseModel):
    """
    Response for GET /api/v1/letters/{letter_id}.

    Implements [response-letter:FR-008] - Retrieve generated letter
    """

    letter_id: str
    review_id: str
    application_ref: str
    status: LetterStatus
    stance: LetterStance
    tone: LetterTone
    case_officer: str | None = None
    letter_date: date | None = None
    content: str | None = Field(
        default=None,
        description="Letter content as Markdown (populated when status=completed)",
    )
    metadata: LetterMetadata | None = None
    error: dict[str, Any] | None = None
    created_at: datetime
    completed_at: datetime | None = None
