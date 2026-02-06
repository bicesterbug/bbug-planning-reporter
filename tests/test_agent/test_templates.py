"""
Tests for review templates.

Implements [agent-integration:ReviewTemplates/TS-01] through [TS-05]
"""

import pytest

from src.agent.assessor import AspectAssessment, AspectName, AspectRating
from src.agent.generator import ApplicationSummary
from src.agent.policy_comparer import ComplianceItem
from src.agent.templates import ReviewTemplates


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


class TestApplicationSummaryFormatting:
    """
    Tests for application summary formatting.

    Implements [agent-integration:ReviewTemplates/TS-01]
    """

    def test_format_application_summary(self, application_summary):
        """
        Verifies [agent-integration:ReviewTemplates/TS-01] - Format application summary

        Given: Application metadata
        When: Format summary section
        Then: Correct Markdown with all fields
        """
        result = ReviewTemplates.format_application_summary(application_summary)

        # Should have header
        assert "## Application Summary" in result

        # Should have all fields
        assert "Land at Test Site, Bicester" in result
        assert "Reserved matters for residential development" in result
        assert "Test Developments Ltd" in result
        assert "Under consideration" in result
        assert "2025-01-20" in result
        assert "2025-02-15" in result

    def test_format_summary_with_missing_fields(self):
        """Test formatting with some fields missing."""
        app = ApplicationSummary(
            reference="25/00001/FUL",
            address="Test Address",
            # Other fields None
        )

        result = ReviewTemplates.format_application_summary(app)

        assert "## Application Summary" in result
        assert "Test Address" in result
        # Should not have undefined fields
        assert "None" not in result


class TestAssessmentTableFormatting:
    """
    Tests for assessment table formatting.

    Implements [agent-integration:ReviewTemplates/TS-02]
    """

    def test_format_assessment_table(self):
        """
        Verifies [agent-integration:ReviewTemplates/TS-02] - Format assessment table

        Given: Aspect ratings
        When: Format table
        Then: Markdown table with ratings and key issues
        """
        aspects = [
            make_assessment(AspectName.CYCLE_PARKING, AspectRating.GREEN, "Good provision"),
            make_assessment(AspectName.CYCLE_ROUTES, AspectRating.AMBER, "Minor issues"),
            make_assessment(AspectName.JUNCTION_DESIGN, AspectRating.RED, "Non-compliant"),
        ]

        result = ReviewTemplates.format_assessment_table(aspects)

        # Should have header
        assert "## Assessment Summary" in result

        # Should have table structure
        assert "| Aspect | Rating | Key Issue |" in result
        assert "|--------|--------|-----------|" in result

        # Should have all aspects
        assert "Cycle Parking" in result
        assert "Cycle Routes" in result
        assert "Junction Design" in result

        # Should have ratings
        assert "GREEN" in result
        assert "AMBER" in result
        assert "RED" in result

        # Should have key issues
        assert "Good provision" in result
        assert "Minor issues" in result
        assert "Non-compliant" in result

    def test_format_table_with_na_aspects(self):
        """Test table formatting includes N/A aspects."""
        aspects = [
            make_assessment(AspectName.CYCLE_PARKING, AspectRating.GREEN),
            make_assessment(AspectName.JUNCTION_DESIGN, AspectRating.NOT_APPLICABLE, "N/A for this type"),
        ]

        result = ReviewTemplates.format_assessment_table(aspects)

        assert "Junction Design" in result
        assert "N/A" in result


class TestPolicyComplianceMatrixFormatting:
    """
    Tests for policy compliance matrix formatting.

    Implements [agent-integration:ReviewTemplates/TS-03]
    """

    def test_format_policy_compliance_matrix(self):
        """
        Verifies [agent-integration:ReviewTemplates/TS-03] - Format policy compliance matrix

        Given: Compliance items
        When: Format matrix
        Then: Markdown table with requirement, source, status
        """
        compliance_matrix = [
            ComplianceItem(
                requirement="Segregated cycle track where traffic > 2500 PCU/day",
                policy_source="LTN 1/20",
                policy_revision="rev_LTN120_2020_07",
                compliant=False,
                notes="Shared-use path proposed instead",
            ),
            ComplianceItem(
                requirement="Cycle parking to minimum standards",
                policy_source="Cherwell Local Plan",
                policy_revision="rev_CLP_2015_07",
                compliant=True,
                notes="Exceeds minimum requirement",
            ),
        ]

        result = ReviewTemplates.format_policy_compliance_matrix(compliance_matrix)

        # Should have header
        assert "## Policy Compliance" in result

        # Should have table structure
        assert "| Requirement | Policy Source | Compliant | Notes |" in result

        # Should have compliance data
        assert "LTN 1/20" in result
        assert "Cherwell Local Plan" in result
        assert "Yes" in result  # compliant=True
        assert "No" in result  # compliant=False

    def test_format_empty_matrix(self):
        """Test formatting empty compliance matrix."""
        result = ReviewTemplates.format_policy_compliance_matrix([])

        assert "## Policy Compliance" in result
        assert "No policy compliance items" in result


class TestRecommendationsFormatting:
    """
    Tests for recommendations list formatting.

    Implements [agent-integration:ReviewTemplates/TS-04]
    """

    def test_format_recommendations(self):
        """
        Verifies [agent-integration:ReviewTemplates/TS-04] - Format recommendations list

        Given: List of recommendations
        When: Format section
        Then: Numbered list with policy citations
        """
        recommendations = [
            "Provide segregated cycle track in accordance with LTN 1/20 Table 5-2",
            "Add cargo bike parking spaces per LTN 1/20 Chapter 11",
            "Include protected cycle crossing at main junction",
        ]

        result = ReviewTemplates.format_recommendations(recommendations)

        # Should have header
        assert "## Recommendations" in result

        # Should have numbered list
        assert "1." in result
        assert "2." in result
        assert "3." in result

        # Should have recommendation text
        assert "segregated cycle track" in result
        assert "cargo bike" in result
        assert "protected cycle crossing" in result

    def test_format_empty_recommendations(self):
        """Test formatting with no recommendations."""
        result = ReviewTemplates.format_recommendations([])

        assert "## Recommendations" in result
        assert "No specific recommendations" in result


class TestFullReviewRendering:
    """
    Tests for full review rendering.

    Implements [agent-integration:ReviewTemplates/TS-05]
    Implements [agent-integration:ReviewGenerator/TS-02]
    """

    def test_render_full_review(self, application_summary):
        """
        Verifies [agent-integration:ReviewGenerator/TS-02] - Generate Markdown output

        Given: Complete assessment results
        When: Generate review
        Then: Well-formatted Markdown with headers, tables, citations
        """
        aspects = [
            make_assessment(
                AspectName.CYCLE_PARKING,
                AspectRating.AMBER,
                "Missing cargo bike provision",
                "The proposed 48 Sheffield stands meet minimum requirements but no cargo bike provision.",
                ["LTN 1/20 Chapter 11"],
            ),
            make_assessment(
                AspectName.CYCLE_ROUTES,
                AspectRating.GREEN,
                "Compliant segregated track",
                "3m segregated cycle track meets LTN 1/20 requirements.",
                ["LTN 1/20 Table 5-2"],
            ),
        ]

        compliance_matrix = [
            ComplianceItem(
                requirement="Cargo bike parking required",
                policy_source="LTN 1/20",
                policy_revision="rev_LTN120_2020_07",
                compliant=False,
                notes="Not provided",
            ),
        ]

        recommendations = [
            "Add cargo bike parking spaces per LTN 1/20 Chapter 11",
        ]

        suggested_conditions = [
            "Prior to occupation, submit detailed cycle parking layout for approval.",
        ]

        result = ReviewTemplates.render_full_review(
            application=application_summary,
            overall_rating="amber",
            aspects=aspects,
            compliance_matrix=compliance_matrix,
            recommendations=recommendations,
            suggested_conditions=suggested_conditions,
            human_review_flags=[],
        )

        # Should have all sections
        assert "# Cycle Advocacy Review: 25/01178/REM" in result
        assert "## Application Summary" in result
        assert "## Overall Assessment" in result
        assert "## Assessment Summary" in result
        assert "## Detailed Assessment" in result
        assert "## Policy Compliance" in result
        assert "## Recommendations" in result
        assert "## Suggested Conditions" in result

        # Should have overall rating
        assert "AMBER" in result

    def test_render_without_conditions_for_green(self, application_summary):
        """Test that green rating doesn't include conditions section."""
        aspects = [
            make_assessment(AspectName.CYCLE_PARKING, AspectRating.GREEN),
        ]

        result = ReviewTemplates.render_full_review(
            application=application_summary,
            overall_rating="green",
            aspects=aspects,
            compliance_matrix=[],
            recommendations=["Good provision commended."],
            suggested_conditions=[],  # Empty
            human_review_flags=[],
        )

        # Should NOT have conditions section
        assert "## Suggested Conditions" not in result

    def test_render_with_human_review_flags(self, application_summary):
        """Test rendering with human review flags."""
        aspects = [
            make_assessment(AspectName.CYCLE_PARKING, AspectRating.AMBER),
        ]

        result = ReviewTemplates.render_full_review(
            application=application_summary,
            overall_rating="amber",
            aspects=aspects,
            compliance_matrix=[],
            recommendations=[],
            suggested_conditions=[],
            human_review_flags=["Transport Assessment missing", "Site plan unclear"],
        )

        assert "## Items Requiring Human Review" in result
        assert "Transport Assessment missing" in result
        assert "Site plan unclear" in result

    def test_markdown_renders_correctly(self, application_summary):
        """
        Verifies [agent-integration:ReviewTemplates/TS-05] - Render in common viewers

        Given: Generated Markdown
        When: View in GitHub, VS Code
        Then: Renders correctly with tables and formatting

        This test verifies the Markdown structure is valid.
        """
        aspects = [
            make_assessment(AspectName.CYCLE_PARKING, AspectRating.AMBER, "Test issue"),
        ]

        result = ReviewTemplates.render_full_review(
            application=application_summary,
            overall_rating="amber",
            aspects=aspects,
            compliance_matrix=[],
            recommendations=["Test recommendation"],
            suggested_conditions=["Test condition"],
            human_review_flags=[],
        )

        # Verify valid Markdown structure
        # Headers should be proper Markdown
        assert result.count("# ") >= 1  # At least one h1
        assert result.count("## ") >= 3  # Multiple h2 sections

        # Tables should have proper structure
        lines = result.split("\n")
        table_lines = [l for l in lines if l.startswith("|")]
        for table_line in table_lines:
            # Table lines should have matching pipes
            assert table_line.count("|") >= 2

        # No raw Python objects in output
        assert "AspectRating." not in result
        assert "AspectName." not in result
        assert "<" not in result or ">" not in result  # No object representations
