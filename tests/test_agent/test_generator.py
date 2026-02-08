"""
Tests for review generator.

Implements [agent-integration:ReviewGenerator/TS-01] through [TS-10]
"""

import pytest

from src.agent.assessor import AspectAssessment, AspectName, AspectRating, AssessmentResult
from src.agent.generator import (
    ApplicationSummary,
    ReviewGenerator,
    ReviewMetadata,
)
from src.agent.policy_comparer import ComplianceItem, PolicyComparisonResult, PolicyRevision


@pytest.fixture
def application_summary():
    """Create a sample application summary."""
    return ApplicationSummary(
        reference="25/01178/REM",
        address="Land at Test Site, Bicester",
        proposal="Reserved matters for residential development",
        applicant="Test Developments Ltd",
        status="Under consideration",
        date_validated="2025-01-20",
        consultation_end="2025-02-15",
    )


@pytest.fixture
def generator(application_summary):
    """Create a ReviewGenerator instance."""
    return ReviewGenerator(
        review_id="rev_test123",
        application=application_summary,
    )


def make_assessment(
    name: AspectName,
    rating: AspectRating,
    key_issue: str = "Test issue",
    detail: str = "Test detail",
    policy_refs: list[str] = None,
) -> AspectAssessment:
    """Helper to create an AspectAssessment."""
    return AspectAssessment(
        name=name,
        rating=rating,
        key_issue=key_issue,
        detail=detail,
        policy_refs=policy_refs or [],
    )


def make_compliance_item(
    requirement: str = "Test requirement",
    compliant: bool = True,
) -> ComplianceItem:
    """Helper to create a ComplianceItem."""
    return ComplianceItem(
        requirement=requirement,
        policy_source="LTN 1/20",
        policy_revision="rev_LTN120_2020_07",
        compliant=compliant,
        notes="Test notes",
    )


class TestJSONOutput:
    """
    Tests for JSON output generation.

    Implements [agent-integration:ReviewGenerator/TS-01], [TS-10]
    """

    def test_generate_json_output(self, generator):
        """
        Verifies [agent-integration:ReviewGenerator/TS-01] - Generate JSON output

        Given: Complete assessment results
        When: Generate review
        Then: JSON validates against schema; all required fields present
        """
        assessment_result = AssessmentResult(
            aspects=[
                make_assessment(AspectName.CYCLE_PARKING, AspectRating.GREEN),
                make_assessment(AspectName.CYCLE_ROUTES, AspectRating.AMBER),
            ],
            missing_documents=[],
            human_review_flags=[],
        )

        policy_result = PolicyComparisonResult(
            compliance_matrix=[make_compliance_item()],
            revisions_used=[
                PolicyRevision(
                    source="LTN_1_20",
                    revision_id="rev_LTN120_2020_07",
                    version_label="July 2020",
                )
            ],
        )

        output = generator.generate(assessment_result, policy_result)
        json_output = generator.to_json(output)

        # Verify required fields
        assert "review_id" in json_output
        assert "application" in json_output
        assert "review" in json_output
        assert "metadata" in json_output

        # Verify review structure
        review = json_output["review"]
        assert "overall_rating" in review
        assert "aspects" in review
        assert "policy_compliance" in review
        assert "recommendations" in review
        assert "full_markdown" in review

    def test_handle_optional_fields(self, generator):
        """
        Verifies [agent-integration:ReviewGenerator/TS-10] - Handle optional fields

        Given: Some aspects not applicable
        When: Generate JSON
        Then: Optional fields cleanly omitted
        """
        assessment_result = AssessmentResult(
            aspects=[
                make_assessment(AspectName.CYCLE_PARKING, AspectRating.GREEN),
                make_assessment(AspectName.JUNCTION_DESIGN, AspectRating.NOT_APPLICABLE),
                make_assessment(AspectName.PERMEABILITY, AspectRating.NOT_APPLICABLE),
            ],
        )

        policy_result = PolicyComparisonResult()

        output = generator.generate(assessment_result, policy_result)

        # N/A aspects should be omitted from aspects list
        aspect_names = [a["name"] for a in output.aspects]
        assert "Cycle Parking" in aspect_names
        assert "Junction Design" not in aspect_names
        assert "Permeability" not in aspect_names


class TestOverallRatingCalculation:
    """
    Tests for overall rating calculation.

    Implements [agent-integration:ReviewGenerator/TS-03], [TS-04], [TS-05]
    """

    def test_calculate_red_overall_rating(self, generator):
        """
        Verifies [agent-integration:ReviewGenerator/TS-03] - Calculate red overall rating

        Given: One aspect rated red
        When: Calculate rating
        Then: Overall rating is red
        """
        aspects = [
            make_assessment(AspectName.CYCLE_PARKING, AspectRating.GREEN),
            make_assessment(AspectName.CYCLE_ROUTES, AspectRating.RED),
            make_assessment(AspectName.JUNCTION_DESIGN, AspectRating.AMBER),
        ]

        rating = generator._calculate_overall_rating(aspects)
        assert rating == "red"

    def test_calculate_amber_overall_rating(self, generator):
        """
        Verifies [agent-integration:ReviewGenerator/TS-04] - Calculate amber overall rating

        Given: No red, one amber aspect
        When: Calculate rating
        Then: Overall rating is amber
        """
        aspects = [
            make_assessment(AspectName.CYCLE_PARKING, AspectRating.GREEN),
            make_assessment(AspectName.CYCLE_ROUTES, AspectRating.AMBER),
            make_assessment(AspectName.JUNCTION_DESIGN, AspectRating.GREEN),
        ]

        rating = generator._calculate_overall_rating(aspects)
        assert rating == "amber"

    def test_calculate_green_overall_rating(self, generator):
        """
        Verifies [agent-integration:ReviewGenerator/TS-05] - Calculate green overall rating

        Given: All aspects green
        When: Calculate rating
        Then: Overall rating is green
        """
        aspects = [
            make_assessment(AspectName.CYCLE_PARKING, AspectRating.GREEN),
            make_assessment(AspectName.CYCLE_ROUTES, AspectRating.GREEN),
            make_assessment(AspectName.JUNCTION_DESIGN, AspectRating.GREEN),
        ]

        rating = generator._calculate_overall_rating(aspects)
        assert rating == "green"

    def test_na_aspects_ignored_in_rating(self, generator):
        """Test that N/A aspects don't affect overall rating."""
        aspects = [
            make_assessment(AspectName.CYCLE_PARKING, AspectRating.GREEN),
            make_assessment(AspectName.JUNCTION_DESIGN, AspectRating.NOT_APPLICABLE),
            make_assessment(AspectName.PERMEABILITY, AspectRating.NOT_APPLICABLE),
        ]

        rating = generator._calculate_overall_rating(aspects)
        assert rating == "green"


class TestRecommendationGeneration:
    """
    Tests for recommendation generation.

    Implements [agent-integration:ReviewGenerator/TS-06], [TS-09]
    """

    def test_generate_actionable_recommendations(self, generator):
        """
        Verifies [agent-integration:ReviewGenerator/TS-06] - Generate actionable recommendations

        Given: Issues identified
        When: Generate recommendations
        Then: Specific actions with policy justification
        """
        assessment_result = AssessmentResult(
            aspects=[
                make_assessment(
                    AspectName.CYCLE_PARKING,
                    AspectRating.AMBER,
                    key_issue="Missing cargo bike provision",
                    policy_refs=["LTN 1/20 Chapter 11"],
                ),
            ],
        )

        policy_result = PolicyComparisonResult(
            compliance_matrix=[make_compliance_item(compliant=False)],
        )

        output = generator.generate(assessment_result, policy_result)

        # Should have recommendations
        assert len(output.recommendations) > 0

        # Recommendations should reference the issue
        rec_text = " ".join(output.recommendations)
        assert "cargo" in rec_text.lower() or "parking" in rec_text.lower()

    def test_positive_acknowledgment_for_compliance(self, generator):
        """
        Verifies [agent-integration:ReviewGenerator/TS-09] - Positive acknowledgment for compliance

        Given: Fully compliant application
        When: Generate review
        Then: Positive tone; acknowledges good practice
        """
        assessment_result = AssessmentResult(
            aspects=[
                make_assessment(AspectName.CYCLE_PARKING, AspectRating.GREEN),
                make_assessment(AspectName.CYCLE_ROUTES, AspectRating.GREEN),
            ],
        )

        policy_result = PolicyComparisonResult()

        output = generator.generate(assessment_result, policy_result)

        # Should have positive recommendations
        rec_text = " ".join(output.recommendations)
        assert "commended" in rec_text.lower() or "meets" in rec_text.lower()


class TestConditionGeneration:
    """
    Tests for planning condition generation.

    Implements [agent-integration:ReviewGenerator/TS-07], [TS-08]
    """

    def test_generate_conditions_for_amber(self, generator):
        """
        Verifies [agent-integration:ReviewGenerator/TS-07] - Generate conditions for approval with mods

        Given: Amber rating application
        When: Generate conditions
        Then: Conditions address identified issues
        """
        assessment_result = AssessmentResult(
            aspects=[
                make_assessment(AspectName.CYCLE_PARKING, AspectRating.AMBER),
                make_assessment(AspectName.CYCLE_ROUTES, AspectRating.GREEN),
            ],
        )

        policy_result = PolicyComparisonResult()

        output = generator.generate(assessment_result, policy_result)

        # Should have suggested conditions
        assert len(output.suggested_conditions) > 0

        # Conditions should be about cycle parking
        condition_text = " ".join(output.suggested_conditions)
        assert "cycle" in condition_text.lower() or "parking" in condition_text.lower()

    def test_omit_conditions_for_refusal(self, generator):
        """
        Verifies [agent-integration:ReviewGenerator/TS-08] - Omit conditions for refusal

        Given: Red rating with fundamental issues
        When: Generate conditions
        Then: Conditions section omitted or notes refusal recommended
        """
        assessment_result = AssessmentResult(
            aspects=[
                make_assessment(AspectName.CYCLE_PARKING, AspectRating.RED),
                make_assessment(AspectName.CYCLE_ROUTES, AspectRating.RED),
            ],
        )

        policy_result = PolicyComparisonResult()

        output = generator.generate(assessment_result, policy_result)

        # Should NOT have suggested conditions for red rating
        assert len(output.suggested_conditions) == 0

    def test_no_conditions_for_green(self, generator):
        """Test that fully compliant applications don't get conditions."""
        assessment_result = AssessmentResult(
            aspects=[
                make_assessment(AspectName.CYCLE_PARKING, AspectRating.GREEN),
                make_assessment(AspectName.CYCLE_ROUTES, AspectRating.GREEN),
            ],
        )

        policy_result = PolicyComparisonResult()

        output = generator.generate(assessment_result, policy_result)

        # Green applications don't need conditions
        assert len(output.suggested_conditions) == 0


class TestMetadataHandling:
    """Tests for metadata handling."""

    def test_metadata_includes_policy_revisions(self, generator):
        """Test that metadata includes policy revision tracking."""
        assessment_result = AssessmentResult(
            aspects=[make_assessment(AspectName.CYCLE_PARKING, AspectRating.GREEN)],
        )

        policy_result = PolicyComparisonResult(
            revisions_used=[
                PolicyRevision(
                    source="LTN_1_20",
                    revision_id="rev_LTN120_2020_07",
                    version_label="July 2020",
                ),
                PolicyRevision(
                    source="NPPF",
                    revision_id="rev_NPPF_2024_12",
                    version_label="December 2024",
                ),
            ],
        )

        output = generator.generate(assessment_result, policy_result)

        assert len(output.metadata.policy_revisions_used) == 2
        sources = [r["source"] for r in output.metadata.policy_revisions_used]
        assert "LTN_1_20" in sources
        assert "NPPF" in sources

    def test_custom_metadata_preserved(self, generator):
        """Test that custom metadata is preserved."""
        assessment_result = AssessmentResult(
            aspects=[make_assessment(AspectName.CYCLE_PARKING, AspectRating.GREEN)],
        )

        policy_result = PolicyComparisonResult()

        custom_metadata = ReviewMetadata(
            total_tokens_used=50000,
            processing_time_seconds=120.5,
            documents_analysed=15,
        )

        output = generator.generate(assessment_result, policy_result, metadata=custom_metadata)

        assert output.metadata.total_tokens_used == 50000
        assert output.metadata.processing_time_seconds == 120.5
        assert output.metadata.documents_analysed == 15
