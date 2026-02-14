"""
Tests for search query prompt builder.

Verifies [review-workflow-redesign:search_query_prompt/TS-01] through [TS-03]
"""

from src.agent.prompts.search_query_prompt import build_search_query_prompt

SAMPLE_METADATA = {
    "reference": "25/01178/REM",
    "address": "Land at Test Site, Bicester",
    "proposal": "Residential development of 200 dwellings with new access road",
    "type": "Major - Dwellings",
}

SAMPLE_INGESTED_DOCS = [
    {"description": "Transport Assessment", "document_type": "Transport Assessment"},
    {"description": "Design and Access Statement", "document_type": "Design & Access Statement"},
    {"description": "Site Layout Plan", "document_type": "Plans - Layout"},
    {"description": "Travel Plan Framework", "document_type": "Travel Plan"},
    {"description": "Highway Design Drawing", "document_type": "Plans - Highway"},
]


class TestSearchQueryPromptProposalContext:
    """
    Verifies [review-workflow-redesign:search_query_prompt/TS-01]
    """

    def test_prompt_includes_proposal_context(self):
        """
        Given: Application for "residential development with new access"
        When: build_search_query_prompt() called
        Then: User prompt contains the proposal description
        """
        _, user = build_search_query_prompt(SAMPLE_METADATA, SAMPLE_INGESTED_DOCS)

        assert "200 dwellings" in user
        assert "new access road" in user
        assert "25/01178/REM" in user
        assert "Land at Test Site, Bicester" in user

    def test_prompt_includes_application_type(self):
        """
        Given: Application with type "Major - Dwellings"
        When: build_search_query_prompt() called
        Then: Type appears in user prompt
        """
        _, user = build_search_query_prompt(SAMPLE_METADATA, SAMPLE_INGESTED_DOCS)

        assert "Major - Dwellings" in user


class TestSearchQueryPromptJSONSchema:
    """
    Verifies [review-workflow-redesign:search_query_prompt/TS-02]
    """

    def test_returns_structured_json_schema(self):
        """
        Given: N/A
        When: System prompt inspected
        Then: Specifies JSON with application_queries and policy_queries
        """
        system, _ = build_search_query_prompt(SAMPLE_METADATA, SAMPLE_INGESTED_DOCS)

        assert "application_queries" in system
        assert "policy_queries" in system
        assert "JSON object" in system
        assert '"query"' in system
        assert '"sources"' in system

    def test_system_prompt_lists_valid_policy_sources(self):
        """
        Given: N/A
        When: System prompt inspected
        Then: Lists valid policy source identifiers
        """
        system, _ = build_search_query_prompt(SAMPLE_METADATA, SAMPLE_INGESTED_DOCS)

        assert "LTN_1_20" in system
        assert "NPPF" in system
        assert "CHERWELL_LP_2015" in system
        assert "OCC_LTCP" in system
        assert "BICESTER_LCWIP" in system

    def test_returns_tuple_of_strings(self):
        """
        Given: Valid inputs
        When: build_search_query_prompt() called
        Then: Returns tuple of (system_prompt, user_prompt)
        """
        result = build_search_query_prompt(SAMPLE_METADATA, SAMPLE_INGESTED_DOCS)

        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], str)


class TestSearchQueryPromptIngestedDocs:
    """
    Verifies [review-workflow-redesign:search_query_prompt/TS-03]
    """

    def test_includes_ingested_document_list(self):
        """
        Given: 5 ingested documents
        When: build_search_query_prompt() called
        Then: User prompt lists the ingested documents
        """
        _, user = build_search_query_prompt(SAMPLE_METADATA, SAMPLE_INGESTED_DOCS)

        assert "Transport Assessment" in user
        assert "Design and Access Statement" in user
        assert "Travel Plan Framework" in user
        assert "5 documents" in user

    def test_empty_ingested_docs(self):
        """
        Given: No ingested documents
        When: build_search_query_prompt() called
        Then: Prompt handles gracefully
        """
        _, user = build_search_query_prompt(SAMPLE_METADATA, [])

        assert "0 documents" in user
        assert "No documents ingested" in user
