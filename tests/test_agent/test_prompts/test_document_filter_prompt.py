"""
Tests for document filter prompt builder.

Verifies [review-workflow-redesign:document_filter_prompt/TS-01] through [TS-03]
"""

from src.agent.prompts.document_filter_prompt import build_document_filter_prompt

SAMPLE_METADATA = {
    "reference": "21/03268/OUT",
    "address": "Land North of Railway, Bicester",
    "proposal": "Outline application for 200 dwellings with new access road",
    "type": "Major - Dwellings",
}

SAMPLE_DOCUMENTS = [
    {"id": "doc_001", "description": "Transport Assessment", "document_type": "Transport Assessment", "date_published": "2021-09-15"},
    {"id": "doc_002", "description": "Ecology Report", "document_type": "Ecology Report", "date_published": "2021-09-15"},
    {"id": "doc_003", "description": "Design and Access Statement", "document_type": "Design & Access Statement", "date_published": "2021-09-10"},
    {"id": "doc_004", "description": "Drainage Strategy", "document_type": "Drainage", "date_published": "2021-09-10"},
    {"id": "doc_005", "description": "Site Layout Plan", "document_type": "Plans - Layout", "date_published": "2021-09-08"},
]


class TestDocumentFilterPromptMetadata:
    """
    Verifies [review-workflow-redesign:document_filter_prompt/TS-01]
    """

    def test_prompt_includes_application_metadata(self):
        """
        Given: Application with proposal "200 dwellings"
        When: build_document_filter_prompt() called
        Then: User prompt contains proposal text, address, and reference
        """
        system, user = build_document_filter_prompt(SAMPLE_METADATA, SAMPLE_DOCUMENTS)

        assert "21/03268/OUT" in user
        assert "Land North of Railway, Bicester" in user
        assert "200 dwellings" in user
        assert "Major - Dwellings" in user

    def test_prompt_includes_address_and_type(self):
        """
        Given: Application metadata with address and type
        When: build_document_filter_prompt() called
        Then: Both appear in user prompt
        """
        _, user = build_document_filter_prompt(SAMPLE_METADATA, SAMPLE_DOCUMENTS)

        assert "Address:" in user
        assert "Type:" in user


class TestDocumentFilterPromptDocumentList:
    """
    Verifies [review-workflow-redesign:document_filter_prompt/TS-02]
    """

    def test_prompt_includes_full_document_list(self):
        """
        Given: 5 documents with IDs, descriptions, types
        When: build_document_filter_prompt() called
        Then: User prompt lists all 5 documents with their metadata
        """
        _, user = build_document_filter_prompt(SAMPLE_METADATA, SAMPLE_DOCUMENTS)

        for doc in SAMPLE_DOCUMENTS:
            assert doc["id"] in user, f"Document ID {doc['id']} not found in prompt"
            assert doc["description"] in user, f"Description {doc['description']} not found"

    def test_prompt_shows_document_count(self):
        """
        Given: 5 documents
        When: build_document_filter_prompt() called
        Then: Prompt mentions the count
        """
        _, user = build_document_filter_prompt(SAMPLE_METADATA, SAMPLE_DOCUMENTS)

        assert "5 documents" in user

    def test_prompt_includes_document_types(self):
        """
        Given: Documents with various types
        When: build_document_filter_prompt() called
        Then: Document types appear in the prompt
        """
        _, user = build_document_filter_prompt(SAMPLE_METADATA, SAMPLE_DOCUMENTS)

        assert "Transport Assessment" in user
        assert "Ecology Report" in user

    def test_empty_document_list(self):
        """
        Given: Empty document list
        When: build_document_filter_prompt() called
        Then: Prompt handles gracefully
        """
        _, user = build_document_filter_prompt(SAMPLE_METADATA, [])

        assert "0 documents" in user
        assert "No documents listed" in user


class TestDocumentFilterPromptOutputFormat:
    """
    Verifies [review-workflow-redesign:document_filter_prompt/TS-03]
    """

    def test_system_prompt_specifies_json_array_output(self):
        """
        Given: N/A
        When: System prompt inspected
        Then: Instructs LLM to return only a JSON array of document ID strings
        """
        system, _ = build_document_filter_prompt(SAMPLE_METADATA, SAMPLE_DOCUMENTS)

        assert "JSON array" in system
        assert "document ID" in system

    def test_system_prompt_includes_relevant_categories(self):
        """
        Given: N/A
        When: System prompt inspected
        Then: Lists transport-relevant document categories
        """
        system, _ = build_document_filter_prompt(SAMPLE_METADATA, SAMPLE_DOCUMENTS)

        assert "Transport Assessment" in system
        assert "Travel Plan" in system
        assert "Design and Access" in system

    def test_system_prompt_includes_exclusion_categories(self):
        """
        Given: N/A
        When: System prompt inspected
        Then: Lists categories to exclude
        """
        system, _ = build_document_filter_prompt(SAMPLE_METADATA, SAMPLE_DOCUMENTS)

        assert "Ecology" in system
        assert "Heritage" in system
        assert "Arboricultural" in system

    def test_returns_tuple_of_strings(self):
        """
        Given: Valid inputs
        When: build_document_filter_prompt() called
        Then: Returns tuple of (system_prompt, user_prompt) both strings
        """
        result = build_document_filter_prompt(SAMPLE_METADATA, SAMPLE_DOCUMENTS)

        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], str)
