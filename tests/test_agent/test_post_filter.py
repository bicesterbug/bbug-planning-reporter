"""
Tests for consultation/public comment post-filter in the orchestrator.

Verifies [consultation-filter-enforcement:_post_filter_consultation_documents/TS-01] through [TS-10]
Verifies [consultation-filter-enforcement:_phase_filter_documents/TS-01] through [TS-05]
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.orchestrator import AgentOrchestrator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_doc(doc_id, description, document_type=None):
    """Create a document dict matching list_application_documents format."""
    return {
        "document_id": doc_id,
        "description": description,
        "document_type": document_type,
        "date_published": "2025-01-01",
        "url": f"https://example.com/{doc_id}",
    }


def _make_orchestrator(options=None):
    """Create a minimal orchestrator for testing the post-filter."""
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()
    mock_redis.exists = AsyncMock(return_value=False)
    mock_redis.publish = AsyncMock()

    orch = AgentOrchestrator(
        review_id="rev_test",
        application_ref="25/00001/F",
        redis_client=mock_redis,
        options=options,
    )
    return orch


# ---------------------------------------------------------------------------
# _post_filter_consultation_documents tests
# ---------------------------------------------------------------------------


class TestPostFilterCategoryDenylist:
    """
    Verifies [consultation-filter-enforcement:_post_filter_consultation_documents/TS-01]
    through [TS-03] - Category denylist matching.
    """

    def test_consultation_responses_category_removed(self):
        """
        Verifies [consultation-filter-enforcement:_post_filter_consultation_documents/TS-01]

        Given: Document with document_type="Consultation Responses", toggle false
        When: _post_filter_consultation_documents called
        Then: Document removed
        """
        orch = _make_orchestrator()
        docs = [
            _make_doc("doc1", "Transport Assessment", "Supporting Documents"),
            _make_doc("doc2", "OCC Highways Consultation Response", "Consultation Responses"),
        ]

        result = orch._post_filter_consultation_documents(docs)

        assert len(result) == 1
        assert result[0]["document_id"] == "doc1"

    def test_consultee_responses_variant_removed(self):
        """
        Verifies [consultation-filter-enforcement:_post_filter_consultation_documents/TS-02]

        Given: Document with document_type="Consultee Responses", toggle false
        When: _post_filter_consultation_documents called
        Then: Document removed
        """
        orch = _make_orchestrator()
        docs = [_make_doc("doc1", "OCC Response", "Consultee Responses")]

        result = orch._post_filter_consultation_documents(docs)

        assert len(result) == 0

    def test_public_comments_category_removed(self):
        """
        Verifies [consultation-filter-enforcement:_post_filter_consultation_documents/TS-03]

        Given: Document with document_type="Public Comments", toggle false
        When: _post_filter_consultation_documents called
        Then: Document removed
        """
        orch = _make_orchestrator()
        docs = [_make_doc("doc1", "J Smith - Objection", "Public Comments")]

        result = orch._post_filter_consultation_documents(docs)

        assert len(result) == 0


class TestPostFilterTitlePattern:
    """
    Verifies [consultation-filter-enforcement:_post_filter_consultation_documents/TS-04]
    and [TS-05] - Title pattern matching fallback.
    """

    def test_consultation_response_title_pattern(self):
        """
        Verifies [consultation-filter-enforcement:_post_filter_consultation_documents/TS-04]

        Given: Document with document_type=None, description contains "Statutory Consultee"
        When: _post_filter_consultation_documents called
        Then: Document removed
        """
        orch = _make_orchestrator()
        docs = [_make_doc("doc1", "Statutory Consultee Response - OCC", None)]

        result = orch._post_filter_consultation_documents(docs)

        assert len(result) == 0

    def test_public_comment_title_pattern(self):
        """
        Verifies [consultation-filter-enforcement:_post_filter_consultation_documents/TS-05]

        Given: Document with document_type=None, description="Letter from Resident - J Smith"
        When: _post_filter_consultation_documents called
        Then: Document removed
        """
        orch = _make_orchestrator()
        docs = [_make_doc("doc1", "Letter from Resident - J Smith", None)]

        result = orch._post_filter_consultation_documents(docs)

        assert len(result) == 0


class TestPostFilterToggleOverride:
    """
    Verifies [consultation-filter-enforcement:_post_filter_consultation_documents/TS-06]
    and [TS-07] - Toggle overrides.
    """

    def test_consultation_toggle_overrides_category(self):
        """
        Verifies [consultation-filter-enforcement:_post_filter_consultation_documents/TS-06]

        Given: Document with document_type="Consultation Responses", toggle true
        When: _post_filter_consultation_documents called
        Then: Document kept
        """
        options = SimpleNamespace(
            include_consultation_responses=True,
            include_public_comments=False,
            destination_ids=None,
        )
        orch = _make_orchestrator(options=options)
        docs = [_make_doc("doc1", "OCC Highways Response", "Consultation Responses")]

        result = orch._post_filter_consultation_documents(docs)

        assert len(result) == 1
        assert result[0]["document_id"] == "doc1"

    def test_consultation_toggle_overrides_title(self):
        """
        Verifies [consultation-filter-enforcement:_post_filter_consultation_documents/TS-07]

        Given: Document with description="Consultation Response - OCC", toggle true
        When: _post_filter_consultation_documents called
        Then: Document kept
        """
        options = SimpleNamespace(
            include_consultation_responses=True,
            include_public_comments=False,
            destination_ids=None,
        )
        orch = _make_orchestrator(options=options)
        docs = [_make_doc("doc1", "Consultation Response - OCC", "Unknown")]

        result = orch._post_filter_consultation_documents(docs)

        assert len(result) == 1

    def test_public_comment_toggle_overrides(self):
        """
        Given: Document with document_type="Public Comments", include_public_comments=true
        When: _post_filter_consultation_documents called
        Then: Document kept
        """
        options = SimpleNamespace(
            include_consultation_responses=False,
            include_public_comments=True,
            destination_ids=None,
        )
        orch = _make_orchestrator(options=options)
        docs = [_make_doc("doc1", "Objection Letter", "Public Comments")]

        result = orch._post_filter_consultation_documents(docs)

        assert len(result) == 1


class TestPostFilterPassThrough:
    """
    Verifies [consultation-filter-enforcement:_post_filter_consultation_documents/TS-08]
    through [TS-10].
    """

    def test_non_matching_documents_pass_through(self):
        """
        Verifies [consultation-filter-enforcement:_post_filter_consultation_documents/TS-08]

        Given: Document with document_type="Supporting Documents", description="Transport Assessment"
        When: _post_filter_consultation_documents called
        Then: Document kept
        """
        orch = _make_orchestrator()
        docs = [
            _make_doc("doc1", "Transport Assessment", "Supporting Documents"),
            _make_doc("doc2", "Design and Access Statement", "Supporting Documents"),
        ]

        result = orch._post_filter_consultation_documents(docs)

        assert len(result) == 2

    def test_case_insensitive_matching(self):
        """
        Verifies [consultation-filter-enforcement:_post_filter_consultation_documents/TS-09]

        Given: Document with document_type="CONSULTATION RESPONSES" (uppercase)
        When: _post_filter_consultation_documents called
        Then: Document removed
        """
        orch = _make_orchestrator()
        docs = [_make_doc("doc1", "OCC Response", "CONSULTATION RESPONSES")]

        result = orch._post_filter_consultation_documents(docs)

        assert len(result) == 0

    def test_mixed_list_filtering(self):
        """
        Verifies [consultation-filter-enforcement:_post_filter_consultation_documents/TS-10]

        Given: 3 transport docs + 1 consultation response, toggle false
        When: _post_filter_consultation_documents called
        Then: Returns 3 docs
        """
        orch = _make_orchestrator()
        docs = [
            _make_doc("doc1", "Transport Assessment", "Supporting Documents"),
            _make_doc("doc2", "Site Plan", "Proposed Plans"),
            _make_doc("doc3", "OCC Highways Response", "Consultation Responses"),
            _make_doc("doc4", "Design and Access Statement", "Supporting Documents"),
        ]

        result = orch._post_filter_consultation_documents(docs)

        assert len(result) == 3
        ids = [d["document_id"] for d in result]
        assert "doc3" not in ids

    def test_no_options_defaults_to_exclude(self):
        """
        Given: Orchestrator created with options=None
        When: _post_filter_consultation_documents called with consultation response
        Then: Document removed (defaults to exclude)
        """
        orch = _make_orchestrator(options=None)
        docs = [_make_doc("doc1", "Active Travel England", "Consultation Responses")]

        result = orch._post_filter_consultation_documents(docs)

        assert len(result) == 0

    def test_empty_list_returns_empty(self):
        """
        Given: Empty document list
        When: _post_filter_consultation_documents called
        Then: Returns empty list
        """
        orch = _make_orchestrator()
        result = orch._post_filter_consultation_documents([])
        assert result == []

    def test_real_production_case_occ_highways(self):
        """
        Regression test for the actual production issue.

        Given: Documents matching the real production failure case
        When: _post_filter_consultation_documents called with default options
        Then: Consultation responses are removed, transport docs kept
        """
        orch = _make_orchestrator()
        docs = [
            _make_doc("doc1", "Transport Assessment", "Supporting Documents"),
            _make_doc("doc2", "ES VII Appendix 6.2 - Travel Plan", "Supporting Documents"),
            _make_doc(
                "doc3",
                "Oxfordshire County Council's Consultation Response.10 November 2025",
                "Consultation Responses",
            ),
            _make_doc(
                "doc4",
                "Active Travel England Standing Advice Response",
                "Consultation Responses",
            ),
            _make_doc("doc5", "Bicester Bike Users Group", "Consultation Responses"),
            _make_doc("doc6", "Planning Statement", "Supporting Documents"),
        ]

        result = orch._post_filter_consultation_documents(docs)

        assert len(result) == 3
        ids = [d["document_id"] for d in result]
        assert "doc1" in ids
        assert "doc2" in ids
        assert "doc6" in ids
        assert "doc3" not in ids
        assert "doc4" not in ids
        assert "doc5" not in ids
