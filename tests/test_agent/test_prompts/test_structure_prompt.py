"""
Tests for structure call prompt builder.

Verifies [structured-review-output:StructureCallPrompt/TS-01] through [TS-03]
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


class TestStructurePromptSchema:
    """
    Verifies [structured-review-output:StructureCallPrompt/TS-01] - Prompt includes schema
    """

    def test_system_prompt_contains_json_schema(self):
        """
        Given: Application metadata and evidence
        When: build_structure_prompt() called
        Then: System prompt contains JSON schema with all required fields
        """
        system, user = build_structure_prompt(
            APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        assert "overall_rating" in system
        assert "summary" in system
        assert "aspects" in system
        assert "policy_compliance" in system
        assert "recommendations" in system
        assert "suggested_conditions" in system
        assert "key_documents" in system
        assert '"rating"' in system
        assert '"key_issue"' in system
        assert '"analysis"' in system
        assert '"compliant"' in system
        assert '"requirement"' in system
        assert '"policy_source"' in system

    def test_system_prompt_summary_field_description(self):
        """
        Verifies [review-workflow-redesign:build_structure_prompt/TS-01] - Summary field in schema
        Verifies [review-workflow-redesign:build_structure_prompt/TS-02] - Summary description

        Given: Any input
        When: build_structure_prompt() called
        Then: System prompt describes summary as 2-4 sentence summary including overall rating
        """
        system, _ = build_structure_prompt(
            APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        assert "summary" in system
        assert "2-4 sentence" in system
        assert "overall rating" in system

    def test_system_prompt_specifies_json_only(self):
        """
        Given: Any input
        When: build_structure_prompt() called
        Then: System prompt instructs Claude to respond with JSON only
        """
        system, _ = build_structure_prompt(
            APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        assert "JSON" in system
        assert "No markdown" in system

    def test_system_prompt_defines_rating_values(self):
        """
        Given: Any input
        When: build_structure_prompt() called
        Then: System prompt defines valid rating values
        """
        system, _ = build_structure_prompt(
            APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        assert '"red"' in system
        assert '"amber"' in system
        assert '"green"' in system

    def test_system_prompt_defines_categories(self):
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
