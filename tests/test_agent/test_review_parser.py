"""
Tests for ReviewMarkdownParser.

Verifies [review-output-fixes:ReviewMarkdownParser/TS-01] through [review-output-fixes:ReviewMarkdownParser/TS-12]
"""

from pathlib import Path

import pytest

from src.agent.review_parser import ReviewMarkdownParser


@pytest.fixture
def parser():
    """Create a ReviewMarkdownParser instance."""
    return ReviewMarkdownParser()


@pytest.fixture
def real_review_markdown():
    """Load the real 25/00284/F review markdown."""
    review_path = Path(__file__).parent.parent.parent / "output" / "25_00284_F_review.md"
    if not review_path.exists():
        pytest.skip("Real review output not available")
    return review_path.read_text()


ASPECTS_MARKDOWN = """\
## Assessment Summary
**Overall Rating:** RED

| Aspect | Rating | Key Issue |
|--------|--------|-----------|
| Cycle Parking | AMBER | Quantity adequate but design quality unverified |
| Cycle Routes | RED | No meaningful off-site connections |
| Junctions | RED | Internal junctions lack cycle priority |
| Permeability | RED | Site effectively car-only accessible |
| Policy Compliance | RED | Fails NPPF, LTN 1/20, LCWIP requirements |

## Detailed Assessment
"""

COMPLIANCE_MARKDOWN = """\
## Policy Compliance Matrix

| Requirement | Policy Source | Compliant? | Notes |
|---|---|---|---|
| Prioritise sustainable transport modes | NPPF para 115(a) | ❌ NO | Car-based design with token cycle provision |
| Safe and suitable access for all users | NPPF para 115(b) | ❌ NO | No safe cycle access |
| Facilitate high quality public transport | NPPF para 117(a) | ⚠️ PARTIAL | Bus contribution positive but insufficient |
| Sheffield stands with adequate spacing | LTN 1/20 Chapter 11 | ⚠️ UNCLEAR | No detailed design provided |
| Support low-carbon transition | Bicester LCWIP Outcome 6 | ❌ NO | Car-dominated design |
| Inclusive cycle parking | LTN 1/20 Chapter 11 | ✅ YES | Meets minimum standards |

## Summary
"""

RECOMMENDATIONS_MARKDOWN = """\
## Recommendations

To make this application acceptable:

### Off-site Infrastructure (Critical)

1. **A41 Cycle Route to Bicester**
   - Provide segregated cycle track
   - Minimum 2.5m one-way kerb-protected cycle track

2. **Green Lane Connection to Chesterton**
   - Remove bollards
   - Upgrade to 3.5m shared use path

### On-site Infrastructure (Critical)

3. **Internal Spine Road**
   - Provide 2.5m segregated cycle track on each side

4. **Filtered Permeability**
   - Create pedestrian/cycle through-routes

### Cycle Parking (Important)

5. **Parking Design**
   - Replace double-stacked spaces with Sheffield stands

## Suggested Conditions
"""

SUGGESTED_CONDITIONS_MARKDOWN = """\
## Suggested Conditions

The following conditions should be attached to any permission:

1. **Cycle Parking Details** - Prior to commencement, submit full cycle parking details
2. **Travel Plan** - Individual travel plans for each unit prior to occupation
3. **Off-site Infrastructure** - Completion of A41 cycle route prior to first occupation
4. Monitoring and reporting requirements for modal shift targets

## Appendix
"""


class TestParseAspects:
    """Tests for parse_aspects()."""

    def test_parse_aspects_table(self, parser):
        """
        Verifies [review-output-fixes:ReviewMarkdownParser/TS-01] - Parse aspects table.

        Given: Markdown containing Assessment Summary with Aspect/Rating/Key Issue table
        When: parse_aspects() called
        Then: Returns list of dicts with name, rating (lowercased), key_issue
        """
        result = parser.parse_aspects(ASPECTS_MARKDOWN)

        assert result is not None
        assert len(result) == 5
        assert result[0]["name"] == "Cycle Parking"
        assert result[0]["rating"] == "amber"
        assert result[0]["key_issue"] == "Quantity adequate but design quality unverified"

    def test_parse_aspects_varied_ratings(self, parser):
        """
        Verifies [review-output-fixes:ReviewMarkdownParser/TS-02] - Parse aspects with varied ratings.

        Given: Table rows with RED, AMBER, GREEN ratings
        When: parse_aspects() called
        Then: Each rating is lowercased
        """
        markdown = """\
## Assessment Summary

| Aspect | Rating | Key Issue |
|--------|--------|-----------|
| Parking | GREEN | Meets standards |
| Routes | AMBER | Some gaps |
| Junctions | RED | Major failures |
"""
        result = parser.parse_aspects(markdown)

        assert result is not None
        assert len(result) == 3
        assert result[0]["rating"] == "green"
        assert result[1]["rating"] == "amber"
        assert result[2]["rating"] == "red"

    def test_parse_aspects_missing_section(self, parser):
        """
        Verifies [review-output-fixes:ReviewMarkdownParser/TS-03] - Parse aspects missing table.

        Given: Markdown without Assessment Summary section
        When: parse_aspects() called
        Then: Returns None
        """
        result = parser.parse_aspects("## Some Other Section\n\nNo aspects here.")
        assert result is None

    def test_parse_aspects_whitespace_tolerance(self, parser):
        """
        Verifies [review-output-fixes:ReviewMarkdownParser/TS-12] - Whitespace tolerance.

        Given: Markdown table with extra spaces around pipe separators
        When: parse_aspects() called
        Then: Parses correctly, trimming whitespace
        """
        markdown = """\
## Assessment Summary

|  Aspect  |  Rating  |  Key Issue  |
|----------|----------|-------------|
|  Parking  |  GREEN  |  Meets all standards  |
|  Routes  |  RED  |  Major gaps in network  |
"""
        result = parser.parse_aspects(markdown)

        assert result is not None
        assert len(result) == 2
        assert result[0]["name"] == "Parking"
        assert result[0]["rating"] == "green"
        assert result[0]["key_issue"] == "Meets all standards"


class TestParsePolicyCompliance:
    """Tests for parse_policy_compliance()."""

    def test_parse_policy_compliance(self, parser):
        """
        Verifies [review-output-fixes:ReviewMarkdownParser/TS-04] - Parse policy compliance.

        Given: Markdown containing Policy Compliance Matrix with 4-column table
        When: parse_policy_compliance() called
        Then: Returns list of dicts with requirement, policy_source, compliant, notes
        """
        result = parser.parse_policy_compliance(COMPLIANCE_MARKDOWN)

        assert result is not None
        assert len(result) == 6
        assert result[0]["requirement"] == "Prioritise sustainable transport modes"
        assert result[0]["policy_source"] == "NPPF para 115(a)"
        assert result[0]["compliant"] is False
        assert result[0]["notes"] == "Car-based design with token cycle provision"

    def test_parse_compliance_emoji_indicators(self, parser):
        """
        Verifies [review-output-fixes:ReviewMarkdownParser/TS-05] - Parse compliance emoji indicators.

        Given: Rows with various emoji compliance indicators
        When: parse_policy_compliance() called
        Then: Correct boolean and notes for each indicator type
        """
        result = parser.parse_policy_compliance(COMPLIANCE_MARKDOWN)
        assert result is not None

        # ❌ NO → compliant=False
        assert result[0]["compliant"] is False

        # ⚠️ PARTIAL → compliant=False, notes includes "partial"
        partial = result[2]
        assert partial["compliant"] is False
        assert "Partial compliance" in partial["notes"]

        # ⚠️ UNCLEAR → compliant=False, notes includes "unclear"
        unclear = result[3]
        assert unclear["compliant"] is False
        assert "unclear" in unclear["notes"].lower()

        # ✅ YES → compliant=True
        yes_item = result[5]
        assert yes_item["compliant"] is True

    def test_parse_compliance_missing_section(self, parser):
        """
        Verifies [review-output-fixes:ReviewMarkdownParser/TS-06] - Parse compliance missing table.

        Given: Markdown without Policy Compliance Matrix
        When: parse_policy_compliance() called
        Then: Returns None
        """
        result = parser.parse_policy_compliance("## Other Section\n\nNo compliance here.")
        assert result is None


class TestParseRecommendations:
    """Tests for parse_recommendations()."""

    def test_parse_recommendations(self, parser):
        """
        Verifies [review-output-fixes:ReviewMarkdownParser/TS-07] - Parse recommendations.

        Given: Markdown with Recommendations section containing numbered bold items
        When: parse_recommendations() called
        Then: Returns list of recommendation title strings
        """
        result = parser.parse_recommendations(RECOMMENDATIONS_MARKDOWN)

        assert result is not None
        assert len(result) == 5
        assert result[0] == "A41 Cycle Route to Bicester"
        assert result[1] == "Green Lane Connection to Chesterton"
        assert result[2] == "Internal Spine Road"
        assert result[3] == "Filtered Permeability"
        assert result[4] == "Parking Design"

    def test_parse_recommendations_missing_section(self, parser):
        """
        Verifies [review-output-fixes:ReviewMarkdownParser/TS-08] - Parse recommendations missing section.

        Given: Markdown without Recommendations section
        When: parse_recommendations() called
        Then: Returns None
        """
        result = parser.parse_recommendations("## Other Section\n\nNo recommendations.")
        assert result is None


class TestParseSuggestedConditions:
    """Tests for parse_suggested_conditions()."""

    def test_parse_suggested_conditions(self, parser):
        """
        Verifies [review-output-fixes:ReviewMarkdownParser/TS-09] - Parse suggested conditions.

        Given: Markdown with Suggested Conditions section containing numbered items
        When: parse_suggested_conditions() called
        Then: Returns list of condition strings
        """
        result = parser.parse_suggested_conditions(SUGGESTED_CONDITIONS_MARKDOWN)

        assert result is not None
        assert len(result) == 4
        assert "Cycle Parking Details" in result[0]
        assert "Travel Plan" in result[1]
        assert "Off-site Infrastructure" in result[2]
        assert "Monitoring" in result[3]

    def test_parse_suggested_conditions_absent(self, parser):
        """
        Verifies [review-output-fixes:ReviewMarkdownParser/TS-10] - Parse suggested conditions absent.

        Given: Markdown without Suggested Conditions section
        When: parse_suggested_conditions() called
        Then: Returns None
        """
        result = parser.parse_suggested_conditions("## Recommendations\n\n1. **Do something**")
        assert result is None


class TestRealReviewOutput:
    """Tests against real review output."""

    def test_parse_real_review(self, parser, real_review_markdown):
        """
        Verifies [review-output-fixes:ReviewMarkdownParser/TS-11] - Parse real review output.

        Given: The actual 25_00284_F_review.md content
        When: All parse methods called
        Then: aspects has 5 items, policy_compliance has 26 items,
              recommendations has 12+ items, suggested_conditions is None
        """
        aspects = parser.parse_aspects(real_review_markdown)
        assert aspects is not None
        assert len(aspects) == 5
        assert aspects[0]["name"] == "Cycle Parking"
        assert aspects[0]["rating"] == "amber"
        assert aspects[1]["name"] == "Cycle Routes"
        assert aspects[1]["rating"] == "red"

        compliance = parser.parse_policy_compliance(real_review_markdown)
        assert compliance is not None
        assert len(compliance) == 27
        # Check first item
        assert compliance[0]["requirement"] == "Prioritise sustainable transport modes"
        assert compliance[0]["policy_source"] == "NPPF para 115(a)"
        assert compliance[0]["compliant"] is False

        recommendations = parser.parse_recommendations(real_review_markdown)
        assert recommendations is not None
        assert len(recommendations) >= 12
        assert recommendations[0] == "A41 Cycle Route to Bicester"
        assert recommendations[1] == "Green Lane Connection to Chesterton"

        # No standalone Suggested Conditions section in this review
        conditions = parser.parse_suggested_conditions(real_review_markdown)
        assert conditions is None
