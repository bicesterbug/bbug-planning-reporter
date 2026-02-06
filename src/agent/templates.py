"""
Review Templates for Markdown output generation.

Implements [agent-integration:FR-012] - Produce Markdown output

Implements:
- [agent-integration:ReviewTemplates/TS-01] Format application summary
- [agent-integration:ReviewTemplates/TS-02] Format assessment table
- [agent-integration:ReviewTemplates/TS-03] Format policy compliance matrix
- [agent-integration:ReviewTemplates/TS-04] Format recommendations list
- [agent-integration:ReviewTemplates/TS-05] Render in common viewers
- [agent-integration:ReviewGenerator/TS-02] Generate Markdown output
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.agent.assessor import AspectAssessment
    from src.agent.generator import ApplicationSummary
    from src.agent.policy_comparer import ComplianceItem


class ReviewTemplates:
    """
    Template strings and formatters for review output.

    Implements [agent-integration:ReviewTemplates/TS-01] through [TS-05]
    """

    # Rating emoji mapping for visual distinction
    RATING_EMOJI = {
        "green": ":white_check_mark:",
        "amber": ":warning:",
        "red": ":x:",
        "n/a": ":heavy_minus_sign:",
    }

    # Rating text for non-emoji contexts
    RATING_TEXT = {
        "green": "GREEN - Compliant",
        "amber": "AMBER - Minor Issues",
        "red": "RED - Non-Compliant",
        "n/a": "N/A",
    }

    @classmethod
    def render_full_review(
        cls,
        application: "ApplicationSummary",
        overall_rating: str,
        aspects: list["AspectAssessment"],
        compliance_matrix: list["ComplianceItem"],
        recommendations: list[str],
        suggested_conditions: list[str],
        human_review_flags: list[str],
    ) -> str:
        """
        Render the complete review as Markdown.

        Implements [agent-integration:ReviewGenerator/TS-02] - Generate Markdown output
        """
        sections = [
            cls.format_header(application.reference, overall_rating),
            cls.format_application_summary(application),
            cls.format_overall_rating(overall_rating),
            cls.format_assessment_table(aspects),
            cls.format_aspect_details(aspects),
            cls.format_policy_compliance_matrix(compliance_matrix),
            cls.format_recommendations(recommendations),
        ]

        # Only include conditions for amber-rated applications
        if suggested_conditions:
            sections.append(cls.format_suggested_conditions(suggested_conditions))

        # Include human review flags if present
        if human_review_flags:
            sections.append(cls.format_human_review_flags(human_review_flags))

        return "\n\n".join(sections)

    @classmethod
    def format_header(cls, reference: str, overall_rating: str) -> str:
        """Format the document header."""
        rating_upper = overall_rating.upper()
        return f"""# Cycle Advocacy Review: {reference}

**Overall Rating: {rating_upper}**"""

    @classmethod
    def format_application_summary(cls, application: "ApplicationSummary") -> str:
        """
        Format the application summary section.

        Implements [agent-integration:ReviewTemplates/TS-01] - Format application summary
        """
        lines = ["## Application Summary", ""]

        if application.address:
            lines.append(f"**Site Address:** {application.address}")
        if application.proposal:
            lines.append(f"**Proposal:** {application.proposal}")
        if application.applicant:
            lines.append(f"**Applicant:** {application.applicant}")
        if application.status:
            lines.append(f"**Status:** {application.status}")
        if application.date_validated:
            lines.append(f"**Date Validated:** {application.date_validated}")
        if application.consultation_end:
            lines.append(f"**Consultation End:** {application.consultation_end}")

        return "\n".join(lines)

    @classmethod
    def format_overall_rating(cls, rating: str) -> str:
        """Format the overall rating section."""
        rating_text = cls.RATING_TEXT.get(rating, rating.upper())

        explanation = {
            "green": "This application meets cycling infrastructure requirements.",
            "amber": "This application has minor issues that should be addressed before approval.",
            "red": "This application has significant cycling infrastructure deficiencies that require revision.",
        }

        return f"""## Overall Assessment

**Rating:** {rating_text}

{explanation.get(rating, '')}"""

    @classmethod
    def format_assessment_table(cls, aspects: list["AspectAssessment"]) -> str:
        """
        Format the assessment summary table.

        Implements [agent-integration:ReviewTemplates/TS-02] - Format assessment table
        """
        from src.agent.assessor import AspectRating

        lines = [
            "## Assessment Summary",
            "",
            "| Aspect | Rating | Key Issue |",
            "|--------|--------|-----------|",
        ]

        for aspect in aspects:
            rating_str = aspect.rating.value if aspect.rating != AspectRating.NOT_APPLICABLE else "n/a"
            rating_display = rating_str.upper()
            lines.append(f"| {aspect.name.value} | {rating_display} | {aspect.key_issue} |")

        return "\n".join(lines)

    @classmethod
    def format_aspect_details(cls, aspects: list["AspectAssessment"]) -> str:
        """Format detailed assessment for each aspect."""
        from src.agent.assessor import AspectRating

        sections = ["## Detailed Assessment"]

        for aspect in aspects:
            if aspect.rating == AspectRating.NOT_APPLICABLE:
                continue

            rating_str = aspect.rating.value.upper()
            policy_refs = ", ".join(aspect.policy_refs) if aspect.policy_refs else "N/A"

            section = f"""
### {aspect.name.value}

**Rating:** {rating_str}

**Key Issue:** {aspect.key_issue}

{aspect.detail}

**Policy References:** {policy_refs}"""

            sections.append(section)

        return "\n".join(sections)

    @classmethod
    def format_policy_compliance_matrix(
        cls,
        compliance_matrix: list["ComplianceItem"],
    ) -> str:
        """
        Format the policy compliance matrix.

        Implements [agent-integration:ReviewTemplates/TS-03] - Format policy compliance matrix
        """
        if not compliance_matrix:
            return """## Policy Compliance

No policy compliance items to report."""

        lines = [
            "## Policy Compliance",
            "",
            "| Requirement | Policy Source | Compliant | Notes |",
            "|-------------|---------------|-----------|-------|",
        ]

        for item in compliance_matrix:
            compliant_str = "Yes" if item.compliant else "No"
            # Truncate long requirements for table display
            req = item.requirement[:60] + "..." if len(item.requirement) > 60 else item.requirement
            notes = item.notes[:50] + "..." if len(item.notes) > 50 else item.notes
            lines.append(f"| {req} | {item.policy_source} | {compliant_str} | {notes} |")

        return "\n".join(lines)

    @classmethod
    def format_recommendations(cls, recommendations: list[str]) -> str:
        """
        Format the recommendations list.

        Implements [agent-integration:ReviewTemplates/TS-04] - Format recommendations list
        """
        if not recommendations:
            return """## Recommendations

No specific recommendations."""

        lines = ["## Recommendations", ""]

        for i, rec in enumerate(recommendations, 1):
            lines.append(f"{i}. {rec}")

        return "\n".join(lines)

    @classmethod
    def format_suggested_conditions(cls, conditions: list[str]) -> str:
        """Format suggested planning conditions."""
        if not conditions:
            return ""

        lines = ["## Suggested Conditions", ""]

        for i, condition in enumerate(conditions, 1):
            lines.append(f"{i}. {condition}")

        return "\n".join(lines)

    @classmethod
    def format_human_review_flags(cls, flags: list[str]) -> str:
        """Format human review flags."""
        if not flags:
            return ""

        lines = [
            "## Items Requiring Human Review",
            "",
            "The following items could not be fully assessed automatically and require manual review:",
            "",
        ]

        for flag in flags:
            lines.append(f"- {flag}")

        return "\n".join(lines)
