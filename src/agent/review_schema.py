"""
Pydantic models for validating the structure call JSON response.

Implements [structured-review-output:FR-002] - Defines the structured JSON schema
Implements [structured-review-output:NFR-005] - Validation ensures all fields present

Implements:
- [structured-review-output:ReviewStructure/TS-01] Valid JSON parses
- [structured-review-output:ReviewStructure/TS-02] Missing required field rejected
- [structured-review-output:ReviewStructure/TS-03] Empty arrays accepted
- [structured-review-output:ReviewStructure/TS-04] Rating validation
- [structured-review-output:ReviewStructure/TS-05] Compliance boolean coercion
"""

from pydantic import BaseModel, Field, field_validator


class ReviewAspectItem(BaseModel):
    """An assessment aspect from the structure call."""

    name: str
    rating: str
    key_issue: str
    analysis: str = Field(
        ...,
        description="Markdown-formatted analysis notes for the report writer",
    )

    @field_validator("rating")
    @classmethod
    def validate_rating(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("red", "amber", "green"):
            raise ValueError(f"Rating must be red, amber, or green, got: {v}")
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
    category: str
    summary: str
    url: str | None = None

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        valid = {"Transport & Access", "Design & Layout", "Application Core"}
        if v not in valid:
            raise ValueError(f"Category must be one of {valid}, got: {v}")
        return v


class ReviewStructure(BaseModel):
    """
    Schema for the structure call JSON response.

    This model validates the complete structured assessment returned by Claude.
    All fields are required and must be non-null.
    """

    overall_rating: str
    # Implements [review-workflow-redesign:FR-006] - LLM-generated summary
    summary: str
    aspects: list[ReviewAspectItem]
    policy_compliance: list[ComplianceItem]
    recommendations: list[str]
    suggested_conditions: list[str]
    key_documents: list[KeyDocumentItem]

    @field_validator("overall_rating")
    @classmethod
    def validate_overall_rating(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("red", "amber", "green"):
            raise ValueError(f"overall_rating must be red, amber, or green, got: {v}")
        return v
