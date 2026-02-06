"""
Review Generator for producing structured review output.

Implements [agent-integration:FR-009] - Generate specific recommendations
Implements [agent-integration:FR-010] - Generate suggested conditions
Implements [agent-integration:FR-011] - Produce structured JSON output
Implements [agent-integration:FR-013] - Calculate overall rating

Implements:
- [agent-integration:ReviewGenerator/TS-01] Generate JSON output
- [agent-integration:ReviewGenerator/TS-03] Calculate red overall rating
- [agent-integration:ReviewGenerator/TS-04] Calculate amber overall rating
- [agent-integration:ReviewGenerator/TS-05] Calculate green overall rating
- [agent-integration:ReviewGenerator/TS-06] Generate actionable recommendations
- [agent-integration:ReviewGenerator/TS-07] Generate conditions for approval with mods
- [agent-integration:ReviewGenerator/TS-08] Omit conditions for refusal
- [agent-integration:ReviewGenerator/TS-09] Positive acknowledgment for compliance
- [agent-integration:ReviewGenerator/TS-10] Handle optional fields
"""

from dataclasses import dataclass, field
from typing import Any

import structlog

from src.agent.assessor import AspectAssessment, AspectName, AspectRating, AssessmentResult
from src.agent.policy_comparer import ComplianceItem, PolicyComparisonResult, PolicyRevision

logger = structlog.get_logger(__name__)


@dataclass
class ApplicationSummary:
    """Summary of the application being reviewed."""

    reference: str
    address: str | None = None
    proposal: str | None = None
    applicant: str | None = None
    status: str | None = None
    date_validated: str | None = None
    consultation_end: str | None = None


@dataclass
class ReviewMetadata:
    """Metadata about the review process."""

    model: str = "claude-sonnet-4-5-20250929"
    total_tokens_used: int = 0
    processing_time_seconds: float = 0.0
    documents_analysed: int = 0
    policy_sources_referenced: int = 0
    policy_effective_date: str | None = None
    policy_revisions_used: list[dict[str, str]] = field(default_factory=list)
    phases_completed: list[dict[str, Any]] = field(default_factory=list)
    errors_encountered: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ReviewOutput:
    """Complete review output."""

    review_id: str
    application: ApplicationSummary
    overall_rating: str
    aspects: list[dict[str, Any]]
    policy_compliance: list[dict[str, Any]]
    recommendations: list[str]
    suggested_conditions: list[str]
    human_review_flags: list[str]
    full_markdown: str
    metadata: ReviewMetadata


class ReviewGenerator:
    """
    Generates structured review output from assessment and policy comparison results.

    Implements [agent-integration:ReviewGenerator/TS-01] through [TS-10]
    """

    # Rating display mapping
    RATING_DISPLAY = {
        AspectRating.GREEN: "green",
        AspectRating.AMBER: "amber",
        AspectRating.RED: "red",
        AspectRating.NOT_APPLICABLE: "n/a",
    }

    def __init__(
        self,
        review_id: str,
        application: ApplicationSummary,
    ) -> None:
        """
        Initialize the generator.

        Args:
            review_id: The review job ID.
            application: Application metadata.
        """
        self._review_id = review_id
        self._application = application

    def generate(
        self,
        assessment_result: AssessmentResult,
        policy_result: PolicyComparisonResult,
        metadata: ReviewMetadata | None = None,
    ) -> ReviewOutput:
        """
        Generate complete review output.

        Implements [agent-integration:ReviewGenerator/TS-01] - Generate JSON output
        Implements [agent-integration:ReviewGenerator/TS-10] - Handle optional fields

        Args:
            assessment_result: Results from ReviewAssessor.
            policy_result: Results from PolicyComparer.
            metadata: Optional review metadata.

        Returns:
            ReviewOutput with all review components.
        """
        logger.info(
            "Generating review output",
            review_id=self._review_id,
            num_aspects=len(assessment_result.aspects),
        )

        # Calculate overall rating
        overall_rating = self._calculate_overall_rating(assessment_result.aspects)

        # Format aspects for JSON output
        aspects_json = self._format_aspects(assessment_result.aspects)

        # Format policy compliance matrix
        compliance_json = self._format_compliance(policy_result.compliance_matrix)

        # Generate recommendations
        recommendations = self._generate_recommendations(
            assessment_result.aspects,
            policy_result.compliance_matrix,
        )

        # Generate suggested conditions (only for amber)
        suggested_conditions = self._generate_conditions(
            overall_rating,
            assessment_result.aspects,
            policy_result.compliance_matrix,
        )

        # Build metadata
        if metadata is None:
            metadata = ReviewMetadata()

        metadata.policy_revisions_used = [
            {
                "source": rev.source,
                "revision_id": rev.revision_id,
                "version_label": rev.version_label,
            }
            for rev in policy_result.revisions_used
        ]
        metadata.policy_sources_referenced = len(policy_result.revisions_used)
        metadata.policy_effective_date = self._application.date_validated

        # Import templates here to avoid circular imports
        from src.agent.templates import ReviewTemplates

        # Generate Markdown
        full_markdown = ReviewTemplates.render_full_review(
            application=self._application,
            overall_rating=overall_rating,
            aspects=assessment_result.aspects,
            compliance_matrix=policy_result.compliance_matrix,
            recommendations=recommendations,
            suggested_conditions=suggested_conditions,
            human_review_flags=assessment_result.human_review_flags,
        )

        return ReviewOutput(
            review_id=self._review_id,
            application=self._application,
            overall_rating=overall_rating,
            aspects=aspects_json,
            policy_compliance=compliance_json,
            recommendations=recommendations,
            suggested_conditions=suggested_conditions,
            human_review_flags=assessment_result.human_review_flags,
            full_markdown=full_markdown,
            metadata=metadata,
        )

    def _calculate_overall_rating(
        self,
        aspects: list[AspectAssessment],
    ) -> str:
        """
        Calculate overall rating from aspect ratings.

        Implements [agent-integration:ReviewGenerator/TS-03] - Red overall rating
        Implements [agent-integration:ReviewGenerator/TS-04] - Amber overall rating
        Implements [agent-integration:ReviewGenerator/TS-05] - Green overall rating

        Overall rating is the worst aspect rating:
        - Any RED = RED overall
        - No RED but any AMBER = AMBER overall
        - All GREEN (or N/A) = GREEN overall
        """
        ratings = [a.rating for a in aspects if a.rating != AspectRating.NOT_APPLICABLE]

        if not ratings:
            return "green"  # All N/A means no issues found

        if AspectRating.RED in ratings:
            return "red"
        elif AspectRating.AMBER in ratings:
            return "amber"
        return "green"

    def _format_aspects(
        self,
        aspects: list[AspectAssessment],
    ) -> list[dict[str, Any]]:
        """Format aspects for JSON output."""
        result = []
        for aspect in aspects:
            # Skip N/A aspects in JSON output (optional field handling)
            if aspect.rating == AspectRating.NOT_APPLICABLE:
                continue

            result.append({
                "name": aspect.name.value,
                "rating": self.RATING_DISPLAY[aspect.rating],
                "key_issue": aspect.key_issue,
                "detail": aspect.detail,
                "policy_refs": aspect.policy_refs,
            })
        return result

    def _format_compliance(
        self,
        compliance_matrix: list[ComplianceItem],
    ) -> list[dict[str, Any]]:
        """Format compliance matrix for JSON output."""
        return [
            {
                "requirement": item.requirement,
                "policy_source": item.policy_source,
                "policy_revision": item.policy_revision,
                "compliant": item.compliant,
                "notes": item.notes,
            }
            for item in compliance_matrix
        ]

    def _generate_recommendations(
        self,
        aspects: list[AspectAssessment],
        compliance_matrix: list[ComplianceItem],
    ) -> list[str]:
        """
        Generate actionable recommendations.

        Implements [agent-integration:ReviewGenerator/TS-06] - Generate actionable recommendations
        Implements [agent-integration:ReviewGenerator/TS-09] - Positive acknowledgment for compliance
        """
        recommendations = []

        for aspect in aspects:
            if aspect.rating == AspectRating.NOT_APPLICABLE:
                continue

            if aspect.rating == AspectRating.GREEN:
                # Positive acknowledgment
                recommendations.append(
                    f"The {aspect.name.value.lower()} provision is commended for meeting standards."
                )
            elif aspect.rating == AspectRating.AMBER:
                # Actionable improvement recommendation
                recommendations.append(
                    self._create_recommendation(aspect, compliance_matrix)
                )
            elif aspect.rating == AspectRating.RED:
                # Critical recommendation
                recommendations.append(
                    self._create_critical_recommendation(aspect, compliance_matrix)
                )

        return recommendations

    def _create_recommendation(
        self,
        aspect: AspectAssessment,
        compliance_matrix: list[ComplianceItem],
    ) -> str:
        """Create a recommendation for an amber-rated aspect."""
        # Find relevant policy reference
        policy_ref = self._find_policy_ref(aspect, compliance_matrix)

        recommendation = f"Address {aspect.name.value.lower()} concern: {aspect.key_issue}"
        if policy_ref:
            recommendation += f" (per {policy_ref})"

        return recommendation

    def _create_critical_recommendation(
        self,
        aspect: AspectAssessment,
        compliance_matrix: list[ComplianceItem],
    ) -> str:
        """Create a critical recommendation for a red-rated aspect."""
        policy_ref = self._find_policy_ref(aspect, compliance_matrix)

        recommendation = f"CRITICAL: {aspect.name.value} requires significant revision. {aspect.key_issue}"
        if policy_ref:
            recommendation += f" This is required by {policy_ref}."

        return recommendation

    def _find_policy_ref(
        self,
        aspect: AspectAssessment,
        compliance_matrix: list[ComplianceItem],
    ) -> str | None:
        """Find a relevant policy reference for an aspect."""
        # Use policy refs from assessment if available
        if aspect.policy_refs:
            return aspect.policy_refs[0]

        # Otherwise look in compliance matrix
        for item in compliance_matrix:
            if not item.compliant:
                return f"{item.policy_source}"

        return None

    def _generate_conditions(
        self,
        overall_rating: str,
        aspects: list[AspectAssessment],
        compliance_matrix: list[ComplianceItem],
    ) -> list[str]:
        """
        Generate suggested planning conditions.

        Implements [agent-integration:ReviewGenerator/TS-07] - Generate conditions for approval with mods
        Implements [agent-integration:ReviewGenerator/TS-08] - Omit conditions for refusal
        """
        # Don't suggest conditions for red-rated applications (refusal recommended)
        if overall_rating == "red":
            return []

        # Only suggest conditions for amber-rated applications
        if overall_rating != "amber":
            return []

        conditions = []

        for aspect in aspects:
            if aspect.rating == AspectRating.AMBER:
                condition = self._create_condition(aspect)
                if condition:
                    conditions.append(condition)

        return conditions

    def _create_condition(self, aspect: AspectAssessment) -> str | None:
        """Create a planning condition for an amber-rated aspect."""
        condition_templates = {
            AspectName.CYCLE_PARKING: (
                "Prior to occupation, a detailed cycle parking layout showing "
                "Sheffield stands, cargo bike spaces, and accessible spaces "
                "shall be submitted for approval."
            ),
            AspectName.CYCLE_ROUTES: (
                "Prior to commencement of development, details of cycle routes "
                "demonstrating compliance with LTN 1/20 shall be submitted for approval."
            ),
            AspectName.JUNCTION_DESIGN: (
                "Prior to construction of the site access, details of protected "
                "cycle crossing facilities shall be submitted for approval."
            ),
            AspectName.PERMEABILITY: (
                "Prior to commencement, details of pedestrian and cycle "
                "connections to adjacent areas shall be submitted for approval."
            ),
        }

        return condition_templates.get(aspect.name)

    def to_json(self, output: ReviewOutput) -> dict[str, Any]:
        """
        Convert ReviewOutput to JSON-serializable dict.

        Implements [agent-integration:ReviewGenerator/TS-01] - Generate JSON output
        """
        return {
            "review_id": output.review_id,
            "application": {
                "reference": output.application.reference,
                "address": output.application.address,
                "proposal": output.application.proposal,
                "applicant": output.application.applicant,
                "status": output.application.status,
                "date_validated": output.application.date_validated,
                "consultation_end": output.application.consultation_end,
            },
            "review": {
                "overall_rating": output.overall_rating,
                "aspects": output.aspects,
                "policy_compliance": output.policy_compliance,
                "recommendations": output.recommendations,
                "suggested_conditions": output.suggested_conditions,
                "human_review_flags": output.human_review_flags,
                "full_markdown": output.full_markdown,
            },
            "metadata": {
                "model": output.metadata.model,
                "total_tokens_used": output.metadata.total_tokens_used,
                "processing_time_seconds": output.metadata.processing_time_seconds,
                "documents_analysed": output.metadata.documents_analysed,
                "policy_sources_referenced": output.metadata.policy_sources_referenced,
                "policy_effective_date": output.metadata.policy_effective_date,
                "policy_revisions_used": output.metadata.policy_revisions_used,
                "phases_completed": output.metadata.phases_completed,
                "errors_encountered": output.metadata.errors_encountered,
            },
        }
