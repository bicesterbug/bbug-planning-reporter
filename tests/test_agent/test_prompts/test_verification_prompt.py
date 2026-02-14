"""
Tests for verification prompt builder.

Verifies [review-workflow-redesign:verification_prompt/TS-01] through [TS-03]
"""

from src.agent.prompts.verification_prompt import build_verification_prompt

SAMPLE_REVIEW_MARKDOWN = """# Cycle Advocacy Review: 25/01178/REM

## Application Summary
This application proposes 200 dwellings with a new access road.

## Key Documents
- Transport Assessment
- Design and Access Statement

## Detailed Assessment
The Transport Assessment shows 50 cycle parking spaces are proposed.
The site has no connection to the existing cycle network on the A41.
NPPF paragraph 115 requires sustainable transport to be prioritised.
LTN 1/20 Table 5-2 requires segregation at this traffic volume.

## Recommendations
1. Provide segregated cycle track along A41 frontage.
"""

SAMPLE_STRUCTURE = {
    "overall_rating": "amber",
    "summary": "The application provides basic cycle parking but lacks network connections.",
    "aspects": [
        {"name": "Cycle Parking", "rating": "amber", "key_issue": "Quantity adequate, design unclear"},
        {"name": "Cycle Routes", "rating": "red", "key_issue": "No off-site connections"},
    ],
    "policy_compliance": [
        {"requirement": "Sustainable transport", "policy_source": "NPPF 115", "compliant": False, "notes": "Car-based"},
    ],
    "recommendations": ["Provide cycle track"],
    "suggested_conditions": [],
    "key_documents": [
        {"title": "Transport Assessment", "category": "Transport & Access", "summary": "Traffic analysis", "url": "https://example.com/ta.pdf"},
    ],
}

SAMPLE_INGESTED_DOCS = [
    {"description": "Transport Assessment", "document_type": "Transport Assessment", "url": "https://example.com/ta.pdf"},
    {"description": "Design and Access Statement", "document_type": "Design & Access Statement", "url": "https://example.com/das.pdf"},
    {"description": "Site Layout Plan", "document_type": "Plans - Layout", "url": "https://example.com/layout.pdf"},
    {"description": "Travel Plan", "document_type": "Travel Plan", "url": "https://example.com/tp.pdf"},
    {"description": "Flood Risk Assessment", "document_type": "Flood Risk Assessment", "url": "https://example.com/fra.pdf"},
    {"description": "Ecology Report", "document_type": "Ecology", "url": "https://example.com/eco.pdf"},
    {"description": "Noise Assessment", "document_type": "Noise Assessment", "url": "https://example.com/noise.pdf"},
    {"description": "Landscape Plan", "document_type": "Landscape", "url": "https://example.com/landscape.pdf"},
]

SAMPLE_EVIDENCE_CHUNKS = [
    {
        "source": "application",
        "query": "cycle parking provision",
        "text": "The proposed development includes 50 cycle parking spaces in covered shelters.",
        "metadata": {"source_file": "Transport Assessment.pdf"},
    },
    {
        "source": "policy",
        "query": "sustainable transport",
        "text": "Paragraph 115: Development should give priority to pedestrian and cycle movements.",
        "metadata": {"source": "NPPF"},
    },
    {
        "source": "policy",
        "query": "cycle infrastructure design",
        "text": "Table 5-2 sets out segregation triggers based on traffic speed and volume.",
        "metadata": {"source": "LTN_1_20"},
    },
]


class TestVerificationPromptReviewAndEvidence:
    """
    Verifies [review-workflow-redesign:verification_prompt/TS-01]
    """

    def test_user_prompt_includes_review_text(self):
        """
        Given: Review markdown with specific claims
        When: build_verification_prompt() called
        Then: User prompt contains the review text
        """
        _, user = build_verification_prompt(
            SAMPLE_REVIEW_MARKDOWN, SAMPLE_STRUCTURE, SAMPLE_INGESTED_DOCS, SAMPLE_EVIDENCE_CHUNKS
        )

        assert "50 cycle parking spaces" in user
        assert "25/01178/REM" in user
        assert "segregated cycle track" in user

    def test_user_prompt_includes_evidence_chunks(self):
        """
        Given: 3 evidence chunks
        When: build_verification_prompt() called
        Then: User prompt contains evidence text
        """
        _, user = build_verification_prompt(
            SAMPLE_REVIEW_MARKDOWN, SAMPLE_STRUCTURE, SAMPLE_INGESTED_DOCS, SAMPLE_EVIDENCE_CHUNKS
        )

        assert "covered shelters" in user
        assert "priority to pedestrian" in user
        assert "segregation triggers" in user
        assert "3 chunks" in user

    def test_user_prompt_includes_structure_summary(self):
        """
        Given: Structure with aspects
        When: build_verification_prompt() called
        Then: User prompt contains structured assessment
        """
        _, user = build_verification_prompt(
            SAMPLE_REVIEW_MARKDOWN, SAMPLE_STRUCTURE, SAMPLE_INGESTED_DOCS, SAMPLE_EVIDENCE_CHUNKS
        )

        assert "Overall rating: amber" in user
        assert "Cycle Parking" in user
        assert "Cycle Routes" in user

    def test_returns_tuple_of_strings(self):
        """
        Given: Valid inputs
        When: build_verification_prompt() called
        Then: Returns tuple of (system_prompt, user_prompt)
        """
        result = build_verification_prompt(
            SAMPLE_REVIEW_MARKDOWN, SAMPLE_STRUCTURE, SAMPLE_INGESTED_DOCS, SAMPLE_EVIDENCE_CHUNKS
        )

        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], str)


class TestVerificationPromptClaimSchema:
    """
    Verifies [review-workflow-redesign:verification_prompt/TS-02]
    """

    def test_system_prompt_specifies_json_schema(self):
        """
        Given: N/A
        When: System prompt inspected
        Then: Specifies JSON with claims array containing claim, verified, source
        """
        system, _ = build_verification_prompt(
            SAMPLE_REVIEW_MARKDOWN, SAMPLE_STRUCTURE, SAMPLE_INGESTED_DOCS, SAMPLE_EVIDENCE_CHUNKS
        )

        assert "claims" in system
        assert '"claim"' in system
        assert '"verified"' in system
        assert '"source"' in system
        assert "JSON object" in system

    def test_system_prompt_specifies_verification_criteria(self):
        """
        Given: N/A
        When: System prompt inspected
        Then: Describes what to verify (document references, factual claims, policy citations)
        """
        system, _ = build_verification_prompt(
            SAMPLE_REVIEW_MARKDOWN, SAMPLE_STRUCTURE, SAMPLE_INGESTED_DOCS, SAMPLE_EVIDENCE_CHUNKS
        )

        assert "Document references" in system or "document" in system.lower()
        assert "Policy citations" in system or "policy" in system.lower()
        assert "Factual claims" in system or "factual" in system.lower()


class TestVerificationPromptIngestedDocs:
    """
    Verifies [review-workflow-redesign:verification_prompt/TS-03]
    """

    def test_includes_ingested_document_list(self):
        """
        Given: 8 ingested documents
        When: build_verification_prompt() called
        Then: User prompt lists the ingested documents
        """
        _, user = build_verification_prompt(
            SAMPLE_REVIEW_MARKDOWN, SAMPLE_STRUCTURE, SAMPLE_INGESTED_DOCS, SAMPLE_EVIDENCE_CHUNKS
        )

        assert "Transport Assessment" in user
        assert "Design and Access Statement" in user
        assert "Flood Risk Assessment" in user
        assert "8 documents" in user

    def test_empty_ingested_docs(self):
        """
        Given: No ingested documents
        When: build_verification_prompt() called
        Then: Prompt handles gracefully
        """
        _, user = build_verification_prompt(
            SAMPLE_REVIEW_MARKDOWN, SAMPLE_STRUCTURE, [], SAMPLE_EVIDENCE_CHUNKS
        )

        assert "0 documents" in user
        assert "No documents ingested" in user

    def test_empty_evidence_chunks(self):
        """
        Given: No evidence chunks
        When: build_verification_prompt() called
        Then: Prompt handles gracefully
        """
        _, user = build_verification_prompt(
            SAMPLE_REVIEW_MARKDOWN, SAMPLE_STRUCTURE, SAMPLE_INGESTED_DOCS, []
        )

        assert "0 chunks" in user
        assert "No evidence available" in user
