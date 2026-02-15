"""
Tests for structure call prompt builder.

Verifies [structured-review-output:StructureCallPrompt/TS-01] through [TS-03]
Verifies [reliable-structure-extraction:StructurePrompt/TS-01] through [TS-06]
"""

from src.agent.prompts.structure_prompt import build_structure_prompt

APP_SUMMARY = (
    "Reference: 25/01178/REM\n"
    "Address: Land at Test Site, Bicester\n"
    "Proposal: Reserved matters for residential development\n"
    "Applicant: Test Developments Ltd\n"
    "Status: Under consideration\n"
    "Date Validated: 2025-01-20\n"
    "Documents Fetched: 3"
)

INGESTED_DOCS = (
    "- Transport Assessment (type: Transport Assessment, url: https://example.com/ta.pdf)\n"
    "- Design and Access Statement (type: DAS, url: https://example.com/das.pdf)\n"
    "- Site Plan (type: Plans - Site Plan, url: no URL)"
)

APP_EVIDENCE = "[ta.pdf] The proposed development includes 50 cycle parking spaces."

POLICY_EVIDENCE = "[LTN_1_20] Table 5-2 sets out segregation triggers based on traffic speed and volume."


class TestStructurePromptToolUse:
    """
    Verifies [reliable-structure-extraction:StructurePrompt/TS-01] - No JSON-only instruction
    Verifies [reliable-structure-extraction:StructurePrompt/TS-02] - No inline schema
    Verifies [reliable-structure-extraction:StructurePrompt/TS-03] - Tool reference
    """

    def test_no_json_only_instruction(self):
        """
        Verifies [reliable-structure-extraction:StructurePrompt/TS-01]

        Given: Default arguments
        When: build_structure_prompt() called
        Then: System prompt does NOT contain "respond with a single JSON object"
        """
        system, _ = build_structure_prompt(
            APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        assert "respond with a single JSON object" not in system
        assert "No markdown" not in system

    def test_no_inline_schema(self):
        """
        Verifies [reliable-structure-extraction:StructurePrompt/TS-02]

        Given: Default arguments
        When: build_structure_prompt() called
        Then: System prompt does NOT contain inline JSON schema block
        """
        system, _ = build_structure_prompt(
            APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        # Old prompt had: "overall_rating": "red" | "amber" | "green",
        assert '"overall_rating": "red" | "amber" | "green"' not in system
        # Old prompt had JSON object with braces defining the schema
        assert "must conform to this schema" not in system

    def test_tool_reference(self):
        """
        Verifies [reliable-structure-extraction:StructurePrompt/TS-03]

        Given: Default arguments
        When: build_structure_prompt() called
        Then: System prompt contains "submit_review_structure"
        """
        system, user = build_structure_prompt(
            APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        assert "submit_review_structure" in system
        assert "submit_review_structure" in user


class TestStructurePromptFlexibleAspects:
    """
    Verifies [reliable-structure-extraction:StructurePrompt/TS-04] - Flexible aspects
    """

    def test_no_exactly_five_aspects(self):
        """
        Given: Default arguments
        When: build_structure_prompt() called
        Then: System prompt does NOT contain "Exactly 5 aspects"
        """
        system, _ = build_structure_prompt(
            APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        assert "Exactly 5 aspects" not in system
        assert "exactly 5" not in system.lower()

    def test_flexible_aspect_guidance(self):
        """
        Given: Default arguments
        When: build_structure_prompt() called
        Then: System prompt contains guidance about selecting relevant aspects
        """
        system, _ = build_structure_prompt(
            APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        assert "relevant" in system.lower()
        assert "Cycle Parking" in system
        assert "Cycle Routes" in system
        assert "at least one aspect" in system.lower()


class TestStructurePromptFieldGuidance:
    """
    Verifies [reliable-structure-extraction:StructurePrompt/TS-05] - Field guidance retained
    """

    def test_rating_meanings(self):
        """
        Given: Default arguments
        When: build_structure_prompt() called
        Then: System prompt contains rating meanings
        """
        system, _ = build_structure_prompt(
            APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        assert '"red"' in system
        assert '"amber"' in system
        assert '"green"' in system
        assert "Serious deficiencies" in system
        assert "Acceptable provision" in system

    def test_field_names_present(self):
        """
        Given: Default arguments
        When: build_structure_prompt() called
        Then: System prompt contains all field names
        """
        system, _ = build_structure_prompt(
            APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        assert "overall_rating" in system
        assert "summary" in system
        assert "aspects" in system
        assert "policy_compliance" in system
        assert "recommendations" in system
        assert "suggested_conditions" in system
        assert "key_documents" in system

    def test_summary_description(self):
        """
        Given: Any input
        When: build_structure_prompt() called
        Then: System prompt describes summary as 2-4 sentence summary
        """
        system, _ = build_structure_prompt(
            APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        assert "2-4 sentence" in system
        assert "overall rating" in system

    def test_categories_defined(self):
        """
        Given: Any input
        When: build_structure_prompt() called
        Then: System prompt defines valid category values
        """
        system, _ = build_structure_prompt(
            APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        assert "Transport & Access" in system
        assert "Design & Layout" in system
        assert "Application Core" in system


class TestStructurePromptEvidence:
    """
    Verifies [structured-review-output:StructureCallPrompt/TS-02] - Evidence included
    Verifies [reliable-structure-extraction:StructurePrompt/TS-06] - Route evidence in user prompt
    """

    def test_user_prompt_contains_app_evidence(self):
        """
        Given: Evidence chunks with application sources
        When: build_structure_prompt() called
        Then: User prompt contains application evidence
        """
        _, user = build_structure_prompt(
            APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        assert "50 cycle parking spaces" in user

    def test_user_prompt_contains_policy_evidence(self):
        """
        Given: Evidence chunks with policy sources
        When: build_structure_prompt() called
        Then: User prompt contains policy evidence
        """
        _, user = build_structure_prompt(
            APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        assert "segregation triggers" in user
        assert "LTN_1_20" in user

    def test_user_prompt_contains_app_summary(self):
        """
        Given: Application metadata
        When: build_structure_prompt() called
        Then: User prompt contains application details
        """
        _, user = build_structure_prompt(
            APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        assert "25/01178/REM" in user
        assert "Land at Test Site" in user

    def test_route_evidence_in_user_prompt(self):
        """
        Verifies [reliable-structure-extraction:StructurePrompt/TS-06]

        Given: Route evidence text provided
        When: build_structure_prompt() called
        Then: User prompt contains the route evidence
        """
        route_text = "### Route to Bicester North Station\n- Distance: 2300m, LTN 1/20 score: 45/100 (red)"
        _, user = build_structure_prompt(
            APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE,
            route_evidence_text=route_text,
        )

        assert "Bicester North Station" in user
        assert "45/100" in user


class TestStructurePromptDocuments:
    """
    Verifies [structured-review-output:StructureCallPrompt/TS-03] - Document metadata included
    """

    def test_user_prompt_contains_ingested_docs(self):
        """
        Given: Ingested document metadata
        When: build_structure_prompt() called
        Then: User prompt contains ingested document list
        """
        _, user = build_structure_prompt(
            APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        assert "Transport Assessment" in user
        assert "Design and Access Statement" in user
        assert "https://example.com/ta.pdf" in user
        assert "no URL" in user


class TestStructurePromptConciseness:
    """Verifies concise output guidance in the structure prompt."""

    def test_condition_format_guidance(self):
        """
        Given: Default arguments
        When: build_structure_prompt() called
        Then: System prompt contains LPA condition format guidance
        """
        system, _ = build_structure_prompt(
            APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        assert "standard LPA format" in system
        assert "Reason:" in system

    def test_concise_analysis_guidance(self):
        """
        Given: Default arguments
        When: build_structure_prompt() called
        Then: System prompt contains concise analysis guidance, not verbose
        """
        system, _ = build_structure_prompt(
            APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        assert "Concise analysis notes" in system
        assert "2-4 paragraphs" not in system

    def test_short_document_summary_guidance(self):
        """
        Given: Default arguments
        When: build_structure_prompt() called
        Then: System prompt contains short summary guidance
        """
        system, _ = build_structure_prompt(
            APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        assert "max ~15 words" in system


PLANS_SUBMITTED = (
    "- Site Plan (type: Plans - Site Plan, image ratio: 92%)\n"
    "- Elevations (type: Elevations, image ratio: 88%)"
)


class TestStructurePromptPlansSubmitted:
    """
    Verifies [document-type-detection:build_structure_prompt/TS-01] and [TS-02]
    """

    def test_plans_section_included_in_prompt(self):
        """
        Verifies [document-type-detection:build_structure_prompt/TS-01]

        Given: Non-empty plans_submitted_text
        When: build_structure_prompt() called
        Then: User prompt contains Plans & Drawings Submitted section
        """
        _, user = build_structure_prompt(
            APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE,
            PLANS_SUBMITTED,
        )

        assert "Plans & Drawings Submitted" in user
        assert "Site Plan" in user
        assert "92%" in user
        assert "Elevations" in user

    def test_plans_section_with_no_plans(self):
        """
        Verifies [document-type-detection:build_structure_prompt/TS-02]

        Given: plans_submitted_text is "No plans or drawings were detected."
        When: build_structure_prompt() called
        Then: User prompt still contains the section with the no-plans text
        """
        _, user = build_structure_prompt(
            APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE,
            "No plans or drawings were detected.",
        )

        assert "Plans & Drawings Submitted" in user
        assert "No plans or drawings were detected." in user

    def test_backward_compat_default_param(self):
        """
        Given: plans_submitted_text not provided
        When: build_structure_prompt() called without the parameter
        Then: User prompt still works with default value
        """
        _, user = build_structure_prompt(
            APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE,
        )

        assert "Plans & Drawings Submitted" in user
        assert "No plans or drawings were detected." in user
