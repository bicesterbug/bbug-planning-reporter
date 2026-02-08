"""
Tests for report call prompt builder.

Verifies [structured-review-output:ReportCallPrompt/TS-01] through [TS-03]
"""

import json

from src.agent.prompts.report_prompt import build_report_prompt

SAMPLE_STRUCTURE = {
    "overall_rating": "red",
    "aspects": [
        {"name": "Cycle Parking", "rating": "amber", "key_issue": "Design unverified",
         "analysis": "The application provides minimum spaces."},
        {"name": "Cycle Routes", "rating": "red", "key_issue": "No connections",
         "analysis": "No off-site cycle infrastructure."},
        {"name": "Junctions", "rating": "red", "key_issue": "No cycle priority",
         "analysis": "Junctions designed for cars only."},
        {"name": "Permeability", "rating": "red", "key_issue": "Car-only access",
         "analysis": "No filtered permeability."},
        {"name": "Policy Compliance", "rating": "red", "key_issue": "Fails NPPF",
         "analysis": "Fails key policy requirements."},
    ],
    "policy_compliance": [
        {"requirement": "Prioritise sustainable transport", "policy_source": "NPPF para 115",
         "compliant": False, "notes": "Car-based design"},
    ],
    "recommendations": ["Provide segregated cycle track"],
    "suggested_conditions": ["Submit cycle parking details"],
    "key_documents": [
        {"title": "Transport Assessment", "category": "Transport & Access",
         "summary": "Traffic analysis.", "url": "https://example.com/ta.pdf"},
    ],
}

APP_SUMMARY = "Reference: 25/01178/REM\nAddress: Test Site"
INGESTED_DOCS = "- Transport Assessment (type: TA, url: https://example.com/ta.pdf)"
APP_EVIDENCE = "[ta.pdf] The site includes cycle parking."
POLICY_EVIDENCE = "[LTN_1_20] Table 5-2 on segregation."


class TestReportPromptJSONEmbedded:
    """
    Verifies [structured-review-output:ReportCallPrompt/TS-01] - JSON embedded in prompt
    """

    def test_user_prompt_contains_json(self):
        """
        Given: Structured JSON with 5 aspects
        When: build_report_prompt() called
        Then: User prompt contains the full JSON data
        """
        structure_json = json.dumps(SAMPLE_STRUCTURE, indent=2)
        _, user = build_report_prompt(
            structure_json, APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        assert "Cycle Parking" in user
        assert "Cycle Routes" in user
        assert '"overall_rating": "red"' in user
        assert "Provide segregated cycle track" in user
        assert "Submit cycle parking details" in user

    def test_user_prompt_contains_evidence(self):
        """
        Given: Evidence text
        When: build_report_prompt() called
        Then: User prompt contains application and policy evidence
        """
        structure_json = json.dumps(SAMPLE_STRUCTURE)
        _, user = build_report_prompt(
            structure_json, APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        assert "cycle parking" in user
        assert "segregation" in user


class TestReportPromptFormat:
    """
    Verifies [structured-review-output:ReportCallPrompt/TS-02] - Report format specified
    """

    def test_system_prompt_specifies_all_sections(self):
        """
        Given: Any input
        When: build_report_prompt() called
        Then: System prompt specifies all 8 report sections in order
        """
        structure_json = json.dumps(SAMPLE_STRUCTURE)
        system, _ = build_report_prompt(
            structure_json, APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        sections = [
            "Cycle Advocacy Review",
            "Application Summary",
            "Key Documents",
            "Assessment Summary",
            "Detailed Assessment",
            "Policy Compliance Matrix",
            "Recommendations",
            "Suggested Conditions",
        ]
        for section in sections:
            assert section in system, f"Missing section: {section}"

    def test_system_prompt_specifies_table_format(self):
        """
        Given: Any input
        When: build_report_prompt() called
        Then: System prompt specifies Assessment Summary table and Policy Compliance table
        """
        structure_json = json.dumps(SAMPLE_STRUCTURE)
        system, _ = build_report_prompt(
            structure_json, APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        assert "| Aspect | Rating | Key Issue |" in system
        assert "| Requirement | Policy Source | Compliant? | Notes |" in system


class TestReportPromptBinding:
    """
    Verifies [structured-review-output:ReportCallPrompt/TS-03] - Binding language
    """

    def test_system_prompt_binding_language(self):
        """
        Given: Any input
        When: build_report_prompt() called
        Then: System prompt explicitly states Claude MUST use JSON data verbatim
        """
        structure_json = json.dumps(SAMPLE_STRUCTURE)
        system, _ = build_report_prompt(
            structure_json, APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        assert "MUST use the EXACT ratings" in system
        assert "MUST use the EXACT compliance verdicts" in system
        assert "EXACT recommendations" in system
        assert "MUST NOT add" in system
        assert "MUST NOT omit" in system

    def test_system_prompt_references_json_as_authoritative(self):
        """
        Given: Any input
        When: build_report_prompt() called
        Then: System prompt calls JSON the authoritative source
        """
        structure_json = json.dumps(SAMPLE_STRUCTURE)
        system, _ = build_report_prompt(
            structure_json, APP_SUMMARY, INGESTED_DOCS, APP_EVIDENCE, POLICY_EVIDENCE
        )

        assert "authoritative" in system.lower()
