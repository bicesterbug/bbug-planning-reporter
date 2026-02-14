"""
Pydantic models for validating the structure call JSON response.

Implements [structured-review-output:FR-002] - Defines the structured JSON schema
Implements [structured-review-output:NFR-005] - Validation ensures all fields present
Implements [reliable-structure-extraction:FR-002] - Literal types for enum constraints
Implements [reliable-structure-extraction:FR-003] - Flexible aspect count (min 1)

Implements:
- [structured-review-output:ReviewStructure/TS-01] Valid JSON parses
- [structured-review-output:ReviewStructure/TS-02] Missing required field rejected
- [structured-review-output:ReviewStructure/TS-03] Empty arrays accepted
- [structured-review-output:ReviewStructure/TS-04] Rating validation
- [structured-review-output:ReviewStructure/TS-05] Compliance boolean coercion
- [reliable-structure-extraction:ReviewStructure/TS-01] Literal enum in schema
- [reliable-structure-extraction:ReviewStructure/TS-02] Rating case normalisation
- [reliable-structure-extraction:ReviewStructure/TS-03] Invalid rating rejected
- [reliable-structure-extraction:ReviewStructure/TS-04] Flexible aspect count
- [reliable-structure-extraction:ReviewStructure/TS-05] Empty aspects rejected
- [reliable-structure-extraction:ReviewStructure/TS-06] Category via Literal
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Implements [reliable-structure-extraction:FR-002] - Rating type
Rating = Literal["red", "amber", "green"]

# Implements [reliable-structure-extraction:FR-002] - Category type
DocumentCategory = Literal["Transport & Access", "Design & Layout", "Application Core"]


class ReviewAspectItem(BaseModel):
    """An assessment aspect from the structure call."""

    name: str
    rating: Rating
    key_issue: str
    analysis: str = Field(
        ...,
        description="Markdown-formatted analysis notes for the report writer",
    )

    # Implements [reliable-structure-extraction:FR-002] - mode="before" normalises casing
    @field_validator("rating", mode="before")
    @classmethod
    def validate_rating(cls, v: str) -> str:
        if isinstance(v, str):
            return v.strip().lower()
        return v


class ComplianceItem(BaseModel):
    """A policy compliance item from the structure call."""

    requirement: str
    policy_source: str
    compliant: bool
    notes: str | None = None

    @field_validator("compliant", mode="before")
    @classmethod
    def coerce_compliant(cls, v):
        """Coerce string booleans and None to actual booleans."""
        if v is None:
            return False
        if isinstance(v, str):
            if v.lower() in ("true", "yes", "1"):
                return True
            if v.lower() in ("false", "no", "0"):
                return False
            raise ValueError(f"Cannot interpret '{v}' as boolean")
        return v


class KeyDocumentItem(BaseModel):
    """A key document from the structure call."""

    title: str
    # Implements [reliable-structure-extraction:FR-002] - Literal for category
    category: DocumentCategory
    summary: str
    url: str | None = None


class ReviewStructure(BaseModel):
    """
    Schema for the structure call JSON response.

    This model validates the complete structured assessment returned by Claude.
    All fields are required and must be non-null.
    """

    # Implements [reliable-structure-extraction:FR-002] - Literal for overall_rating
    overall_rating: Rating
    # Implements [review-workflow-redesign:FR-006] - LLM-generated summary
    summary: str
    # Implements [reliable-structure-extraction:FR-003] - Flexible aspects (min 1)
    aspects: list[ReviewAspectItem] = Field(..., min_length=1)
    policy_compliance: list[ComplianceItem]
    recommendations: list[str]
    suggested_conditions: list[str]
    key_documents: list[KeyDocumentItem]

    # Implements [reliable-structure-extraction:FR-002] - mode="before" normalises casing
    @field_validator("overall_rating", mode="before")
    @classmethod
    def validate_overall_rating(cls, v: str) -> str:
        if isinstance(v, str):
            return v.strip().lower()
        return v
