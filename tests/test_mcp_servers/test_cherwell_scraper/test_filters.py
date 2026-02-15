"""
Tests for document filtering.

Verifies [document-filtering:DocumentFilter/TS-01] through [document-filtering:DocumentFilter/TS-10]
Verifies [document-filtering:FilteredDocumentInfo/TS-01] through [document-filtering:FilteredDocumentInfo/TS-02]
Verifies [review-output-fixes:DocumentFilter/TS-01] through [review-output-fixes:DocumentFilter/TS-06]
Verifies [review-output-fixes:ITS-01]
"""

from pathlib import Path

import pytest

from src.mcp_servers.cherwell_scraper.filters import DocumentFilter, FilteredDocumentInfo
from src.mcp_servers.cherwell_scraper.models import DocumentInfo
from src.mcp_servers.cherwell_scraper.parsers import CherwellParser


class TestFilteredDocumentInfo:
    """Tests for FilteredDocumentInfo data model."""

    def test_to_dict_with_all_fields(self):
        """
        Verifies [document-filtering:FilteredDocumentInfo/TS-01] - Convert to dict

        Given: A FilteredDocumentInfo instance with all fields populated
        When: to_dict() is called
        Then: Returns dict with all fields
        """
        info = FilteredDocumentInfo(
            document_id="abc123",
            description="Public objection letter",
            document_type="Public Comment",
            filter_reason="Public comment - not relevant for policy review",
        )

        result = info.to_dict()

        assert result == {
            "document_id": "abc123",
            "description": "Public objection letter",
            "document_type": "Public Comment",
            "filter_reason": "Public comment - not relevant for policy review",
        }

    def test_to_dict_with_none_document_type(self):
        """
        Verifies [document-filtering:FilteredDocumentInfo/TS-02] - None values handled

        Given: A FilteredDocumentInfo instance with document_type=None
        When: to_dict() is called
        Then: Dict includes None value (not omitted)
        """
        info = FilteredDocumentInfo(
            document_id="def456",
            description="Unknown document",
            document_type=None,
            filter_reason="Test reason",
        )

        result = info.to_dict()

        assert "document_type" in result
        assert result["document_type"] is None


class TestDocumentFilter:
    """Tests for DocumentFilter class."""

    @pytest.fixture
    def filter(self):
        """Create a DocumentFilter instance for testing."""
        return DocumentFilter()

    def test_core_documents_allowed_planning_statement(self, filter):
        """
        Verifies [document-filtering:DocumentFilter/TS-01] - Core documents allowed

        Given: Document with type "Planning Statement"
        When: filter_documents is called
        Then: Document is in allowed list, not in filtered list
        """
        docs = [
            DocumentInfo(
                document_id="doc1",
                description="Main Planning Statement",
                document_type="Planning Statement",
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 1
        assert len(filtered) == 0
        assert allowed[0].document_id == "doc1"

    def test_core_documents_allowed_design_and_access(self, filter):
        """
        Verifies [document-filtering:DocumentFilter/TS-01] - Core documents allowed

        Given: Document with type "Design and Access Statement"
        When: filter_documents is called
        Then: Document is in allowed list
        """
        docs = [
            DocumentInfo(
                document_id="doc2",
                description="Design and Access Statement",
                document_type="Design and Access Statement",
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 1
        assert len(filtered) == 0

    def test_core_documents_allowed_proposed_plans(self, filter):
        """
        Verifies [document-filtering:DocumentFilter/TS-01] - Core documents allowed

        Given: Document with type "Proposed Plans"
        When: filter_documents is called
        Then: Document is in allowed list
        """
        docs = [
            DocumentInfo(
                document_id="doc3",
                description="Site layout plans",
                document_type="Proposed Plans",
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 1
        assert len(filtered) == 0

    def test_technical_assessment_allowed_transport(self, filter):
        """
        Verifies [document-filtering:DocumentFilter/TS-02] - Technical assessments allowed

        Given: Document with type "Transport Assessment"
        When: filter_documents is called
        Then: Document is in allowed list
        """
        docs = [
            DocumentInfo(
                document_id="doc4",
                description="Transport Assessment Report",
                document_type="Transport Assessment",
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 1
        assert len(filtered) == 0

    def test_technical_assessment_denied_heritage(self, filter):
        """
        Verifies non-transport technical documents are filtered.

        Given: Document with type "Heritage Statement"
        When: filter_documents is called
        Then: Document is in filtered list (not transport-relevant)
        """
        docs = [
            DocumentInfo(
                document_id="doc5",
                description="Heritage Impact Assessment",
                document_type="Heritage Statement",
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 0
        assert len(filtered) == 1
        assert "Non-transport" in filtered[0].filter_reason

    def test_technical_assessment_denied_flood_risk(self, filter):
        """
        Verifies non-transport technical documents are filtered.

        Given: Document with type "Flood Risk Assessment"
        When: filter_documents is called
        Then: Document is in filtered list (not transport-relevant)
        """
        docs = [
            DocumentInfo(
                document_id="doc6",
                description="FRA for development",
                document_type="Flood Risk Assessment",
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 0
        assert len(filtered) == 1
        assert "Non-transport" in filtered[0].filter_reason

    def test_officer_report_allowed(self, filter):
        """
        Verifies [document-filtering:DocumentFilter/TS-03] - Officer reports allowed

        Given: Document with type "Officer Report"
        When: filter_documents is called
        Then: Document is in allowed list
        """
        docs = [
            DocumentInfo(
                document_id="doc7",
                description="Planning Officer's Report",
                document_type="Officer Report",
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 1
        assert len(filtered) == 0

    def test_committee_report_allowed(self, filter):
        """
        Verifies [document-filtering:DocumentFilter/TS-03] - Officer reports allowed

        Given: Document with type "Committee Report"
        When: filter_documents is called
        Then: Document is in allowed list
        """
        docs = [
            DocumentInfo(
                document_id="doc8",
                description="Planning Committee Report",
                document_type="Committee Report",
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 1
        assert len(filtered) == 0

    def test_decision_notice_allowed(self, filter):
        """
        Verifies [document-filtering:DocumentFilter/TS-03] - Officer reports allowed

        Given: Document with type "Decision Notice"
        When: filter_documents is called
        Then: Document is in allowed list
        """
        docs = [
            DocumentInfo(
                document_id="doc9",
                description="Approval notice with conditions",
                document_type="Decision Notice",
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 1
        assert len(filtered) == 0

    def test_public_comment_filtered(self, filter):
        """
        Verifies [document-filtering:DocumentFilter/TS-04] - Public comments filtered

        Given: Document with type "Public Comment"
        When: filter_documents is called
        Then: Document is in filtered list with reason
        """
        docs = [
            DocumentInfo(
                document_id="doc10",
                description="Comment from local resident",
                document_type="Public Comment",
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 0
        assert len(filtered) == 1
        assert filtered[0].document_id == "doc10"
        assert "Public comment" in filtered[0].filter_reason

    def test_objection_letter_filtered(self, filter):
        """
        Verifies [document-filtering:DocumentFilter/TS-05] - Objection letters filtered

        Given: Document with type "Objection Letter"
        When: filter_documents is called
        Then: Document is in filtered list
        """
        docs = [
            DocumentInfo(
                document_id="doc11",
                description="Letter of objection from resident",
                document_type="Letter of Objection",
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 0
        assert len(filtered) == 1
        assert filtered[0].document_id == "doc11"

    def test_representation_filtered(self, filter):
        """
        Verifies [document-filtering:DocumentFilter/TS-06] - Representations filtered

        Given: Document with type "Representation from resident"
        When: filter_documents is called
        Then: Document is in filtered list
        """
        docs = [
            DocumentInfo(
                document_id="doc12",
                description="Representation from nearby resident",
                document_type="Representation from resident",
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 0
        assert len(filtered) == 1

    def test_unknown_type_defaults_to_allow(self, filter):
        """
        Verifies [document-filtering:DocumentFilter/TS-07] - Unknown type defaults to allow

        Given: Document with type None
        When: filter_documents is called
        Then: Document is in allowed list (fail-safe behavior)
        """
        docs = [
            DocumentInfo(
                document_id="doc13",
                description="Some document",
                document_type=None,
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 1
        assert len(filtered) == 0

    def test_unknown_type_string_defaults_to_allow(self, filter):
        """
        Verifies [document-filtering:DocumentFilter/TS-07] - Unknown type defaults to allow

        Given: Document with unrecognized type "Misc Document"
        When: filter_documents is called
        Then: Document is in allowed list (fail-safe behavior)
        """
        docs = [
            DocumentInfo(
                document_id="doc14",
                description="Miscellaneous document",
                document_type="Misc Document",
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 1
        assert len(filtered) == 0

    def test_case_insensitive_matching(self, filter):
        """
        Verifies [document-filtering:DocumentFilter/TS-08] - Case insensitive matching

        Given: Document with type "PLANNING STATEMENT" (all caps)
        When: filter_documents is called
        Then: Document is in allowed list (case normalized)
        """
        docs = [
            DocumentInfo(
                document_id="doc15",
                description="Planning statement",
                document_type="PLANNING STATEMENT",
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 1
        assert len(filtered) == 0

    def test_partial_pattern_matching(self, filter):
        """
        Verifies [document-filtering:DocumentFilter/TS-09] - Partial pattern matching

        Given: Document with type "Supporting Planning Statement"
        When: filter_documents is called
        Then: Document is in allowed list (contains "planning statement")
        """
        docs = [
            DocumentInfo(
                document_id="doc16",
                description="Supporting planning docs",
                document_type="Supporting Planning Statement",
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 1
        assert len(filtered) == 0

    def test_skip_filter_override(self, filter):
        """
        Verifies [document-filtering:DocumentFilter/TS-10] - Skip filter override

        Given: Mix of allowed and filtered document types
        When: filter_documents is called with skip_filter=True
        Then: All documents are in allowed list, filtered list is empty
        """
        docs = [
            DocumentInfo(
                document_id="doc17",
                description="Planning statement",
                document_type="Planning Statement",
            ),
            DocumentInfo(
                document_id="doc18",
                description="Public objection",
                document_type="Public Comment",
            ),
            DocumentInfo(
                document_id="doc19",
                description="Unknown doc",
                document_type="Random Type",
            ),
        ]

        allowed, filtered = filter.filter_documents(docs, skip_filter=True)

        assert len(allowed) == 3
        assert len(filtered) == 0

    def test_mixed_documents_filtering(self, filter):
        """
        Integration test with mixed document types.

        Given: Application with 10 documents (5 core, 1 transport assessment,
               1 heritage statement, 3 public comments)
        When: filter_documents is called
        Then: 6 documents allowed, 4 filtered (heritage now filtered as non-transport)
        """
        docs = [
            # Core docs - should be allowed
            DocumentInfo(
                document_id="core1",
                description="Planning Statement",
                document_type="Planning Statement",
            ),
            DocumentInfo(
                document_id="core2",
                description="Design and Access",
                document_type="Design and Access Statement",
            ),
            DocumentInfo(
                document_id="core3",
                description="Proposed Plans",
                document_type="Proposed Plans",
            ),
            DocumentInfo(
                document_id="core4",
                description="Site Plan",
                document_type="Site Plan",
            ),
            DocumentInfo(
                document_id="core5",
                description="Elevations",
                document_type="Elevation Drawings",
            ),
            # Transport assessment - should be allowed
            DocumentInfo(
                document_id="assess1",
                description="Transport Assessment",
                document_type="Transport Assessment",
            ),
            # Heritage statement - should be filtered (non-transport)
            DocumentInfo(
                document_id="assess2",
                description="Heritage Statement",
                document_type="Heritage Statement",
            ),
            # Public comments - should be filtered
            DocumentInfo(
                document_id="comment1",
                description="Objection from resident",
                document_type="Public Comment",
            ),
            DocumentInfo(
                document_id="comment2",
                description="Letter of objection",
                document_type="Letter of Objection",
            ),
            DocumentInfo(
                document_id="comment3",
                description="Representation",
                document_type="Representation from resident",
            ),
        ]

        allowed, filtered = filter.filter_documents(
            docs, application_ref="25/01178/REM"
        )

        assert len(allowed) == 6, f"Expected 6 allowed, got {len(allowed)}"
        assert len(filtered) == 4, f"Expected 4 filtered, got {len(filtered)}"

        # Check that public comments and heritage were filtered
        filtered_ids = {f.document_id for f in filtered}
        assert filtered_ids == {"comment1", "comment2", "comment3", "assess2"}

        # Check that core and transport docs were allowed
        allowed_ids = {d.document_id for d in allowed}
        expected_allowed = {
            "core1",
            "core2",
            "core3",
            "core4",
            "core5",
            "assess1",
        }
        assert allowed_ids == expected_allowed

    def test_filter_reason_categories(self, filter):
        """
        Verify that filter reasons correctly categorize documents.

        Given: Documents from different categories
        When: _should_download is called
        Then: Reasons accurately describe the category
        """
        # Core document
        should_download, reason = filter._should_download("Planning Statement")
        assert should_download is True
        assert "Core application document" in reason

        # Technical assessment (transport)
        should_download, reason = filter._should_download("Transport Assessment")
        assert should_download is True
        assert "Technical assessment document" in reason

        # Officer document
        should_download, reason = filter._should_download("Officer Report")
        assert should_download is True
        assert "Officer/decision document" in reason

        # Public comment
        should_download, reason = filter._should_download("Public Comment")
        assert should_download is False
        assert "Public comment" in reason

        # Non-transport technical document
        should_download, reason = filter._should_download("Heritage Statement")
        assert should_download is False
        assert "Non-transport" in reason

        # Unknown type
        should_download, reason = filter._should_download("Unknown Type")
        assert should_download is True
        assert "fail-safe" in reason.lower()

        # None type
        should_download, reason = filter._should_download(None)
        assert should_download is True
        assert "fail-safe" in reason.lower()

    def test_description_based_filtering(self, filter):
        """
        Verify that description is used for filtering when document_type is None.

        Given: Document with no type but description containing denylist term
        When: filter_documents is called
        Then: Document is filtered based on description match
        """
        docs = [
            DocumentInfo(
                document_id="desc1",
                description="Arboricultural Impact Assessment",
                document_type=None,
            ),
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 0
        assert len(filtered) == 1
        assert "Non-transport" in filtered[0].filter_reason

    def test_description_allowlist_match(self, filter):
        """
        Verify that description allowlist match takes priority.

        Given: Document with no type but description containing transport term
        When: filter_documents is called
        Then: Document is allowed based on description
        """
        docs = [
            DocumentInfo(
                document_id="desc2",
                description="Transport Assessment Addendum",
                document_type=None,
            ),
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 1
        assert len(filtered) == 0

    def test_es_transport_chapter_allowed(self, filter):
        """
        Verify ES transport chapters are allowed (allowlist before denylist).

        Given: Document described as "ES Chapter 05 Transport"
        When: filter_documents is called
        Then: Document is allowed because "transport" hits the allowlist first
        """
        docs = [
            DocumentInfo(
                document_id="es_transport",
                description="ES Chapter 05 Transport",
                document_type=None,
            ),
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 1
        assert len(filtered) == 0

    def test_es_non_transport_chapter_filtered(self, filter):
        """
        Verify non-transport ES chapters are filtered.

        Given: Document described as "ES Chapter 08 Ecology"
        When: filter_documents is called
        Then: Document is filtered (ecology in denylist, no allowlist match)
        """
        docs = [
            DocumentInfo(
                document_id="es_ecology",
                description="ES Chapter 08 Ecology",
                document_type=None,
            ),
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 0
        assert len(filtered) == 1
        assert "Non-transport" in filtered[0].filter_reason


class TestReviewScopeControl:
    """
    Tests for review-scope-control feature.

    Verifies [review-scope-control:DocumentFilter/TS-01] through [review-scope-control:DocumentFilter/TS-10]
    """

    @pytest.fixture
    def filter(self):
        """Create a DocumentFilter instance for testing."""
        return DocumentFilter()

    def test_consultation_response_blocked_despite_allowlist_match(self, filter):
        """
        Verifies [review-scope-control:DocumentFilter/TS-01] - Consultation response blocked
        by default despite allowlist match.

        Given: A document with description "Consultation Response - OCC Highways"
               and both toggles at default (false)
        When: filter_documents is called
        Then: Document is filtered out with consultation response reason,
              NOT allowed through via "highway" allowlist match
        """
        docs = [
            DocumentInfo(
                document_id="cr1",
                description="Consultation Response - OCC Highways",
                document_type="Consultation Response",
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 0, "Consultation response should be blocked by default"
        assert len(filtered) == 1
        assert "Consultation response" in filtered[0].filter_reason

    def test_consultation_response_allowed_when_toggle_enabled(self, filter):
        """
        Verifies [review-scope-control:DocumentFilter/TS-02] - Consultation response
        allowed when toggle enabled.

        Given: A document with description "Consultation Response - OCC Highways"
               and include_consultation_responses=True
        When: filter_documents is called
        Then: Document is allowed through
        """
        docs = [
            DocumentInfo(
                document_id="cr2",
                description="Consultation Response - OCC Highways",
                document_type="Consultation Response",
            )
        ]

        allowed, filtered = filter.filter_documents(
            docs, include_consultation_responses=True
        )

        assert len(allowed) == 1, "Consultation response should be allowed when toggle is on"
        assert len(filtered) == 0

    def test_public_comment_blocked_by_default(self, filter):
        """
        Verifies [review-scope-control:DocumentFilter/TS-03] - Public comment
        blocked by default.

        Given: A document with type "letter from resident" and both toggles false
        When: filter_documents is called
        Then: Document is filtered out with public comment reason
        """
        docs = [
            DocumentInfo(
                document_id="pc1",
                description="Letter from Resident - J Smith",
                document_type="letter from resident",
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 0, "Public comment should be blocked by default"
        assert len(filtered) == 1
        assert "Public comment" in filtered[0].filter_reason

    def test_public_comment_allowed_when_toggle_enabled(self, filter):
        """
        Verifies [review-scope-control:DocumentFilter/TS-04] - Public comment
        allowed when toggle enabled.

        Given: A document with type "letter from resident" and include_public_comments=True
        When: filter_documents is called
        Then: Document is allowed through
        """
        docs = [
            DocumentInfo(
                document_id="pc2",
                description="Letter from Resident - J Smith",
                document_type="letter from resident",
            )
        ]

        allowed, filtered = filter.filter_documents(
            docs, include_public_comments=True
        )

        assert len(allowed) == 1, "Public comment should be allowed when toggle is on"
        assert len(filtered) == 0

    def test_both_toggles_enabled(self, filter):
        """
        Verifies [review-scope-control:DocumentFilter/TS-05] - Both toggles
        enabled simultaneously.

        Given: Documents including both consultation responses and public comments,
               with both toggles true
        When: filter_documents is called
        Then: Both document types are allowed through
        """
        docs = [
            DocumentInfo(
                document_id="cr3",
                description="Consultation Response - Environment Agency",
                document_type="Consultation Response",
            ),
            DocumentInfo(
                document_id="pc3",
                description="Letter of Objection from A Jones",
                document_type="Letter of Objection",
            ),
            DocumentInfo(
                document_id="core1",
                description="Planning Statement",
                document_type="Planning Statement",
            ),
        ]

        allowed, filtered = filter.filter_documents(
            docs,
            include_consultation_responses=True,
            include_public_comments=True,
        )

        assert len(allowed) == 3, "All documents should be allowed when both toggles are on"
        assert len(filtered) == 0

    def test_skip_filter_overrides_toggles(self, filter):
        """
        Verifies [review-scope-control:DocumentFilter/TS-06] - skip_filter
        overrides toggles.

        Given: Documents with skip_filter=True and both toggles false
        When: filter_documents is called
        Then: All documents are allowed through (skip_filter takes precedence)
        """
        docs = [
            DocumentInfo(
                document_id="cr4",
                description="Consultation Response - Parish Council",
                document_type="Consultation Response",
            ),
            DocumentInfo(
                document_id="pc4",
                description="Public Comment - B Williams",
                document_type="Public Comment",
            ),
        ]

        allowed, filtered = filter.filter_documents(
            docs,
            skip_filter=True,
            include_consultation_responses=False,
            include_public_comments=False,
        )

        assert len(allowed) == 2, "skip_filter should override all other filtering"
        assert len(filtered) == 0

    def test_core_documents_unaffected_by_toggles(self, filter):
        """
        Verifies [review-scope-control:DocumentFilter/TS-07] - Core documents
        unaffected by toggles.

        Given: A Transport Assessment document with toggles at default
        When: filter_documents is called
        Then: Document is allowed through via allowlist as before
        """
        docs = [
            DocumentInfo(
                document_id="ta1",
                description="Transport Assessment Report",
                document_type="Transport Assessment",
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 1
        assert len(filtered) == 0

    def test_non_transport_document_unaffected_by_toggles(self, filter):
        """
        Verifies [review-scope-control:DocumentFilter/TS-08] - Non-transport
        documents unaffected by toggles.

        Given: An ecology document with toggles at default
        When: filter_documents is called
        Then: Document is filtered out via non-transport denylist as before
        """
        docs = [
            DocumentInfo(
                document_id="eco1",
                description="Ecology Survey Report",
                document_type="Ecology",
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 0
        assert len(filtered) == 1
        assert "Non-transport" in filtered[0].filter_reason

    def test_consultation_response_case_insensitive(self, filter):
        """
        Verifies [review-scope-control:DocumentFilter/TS-09] - Consultation
        response pattern matching is case insensitive.

        Given: A document with description "CONSULTATION RESPONSE - Environment Agency"
        When: filter_documents is called with defaults
        Then: Document is filtered out
        """
        docs = [
            DocumentInfo(
                document_id="cr5",
                description="CONSULTATION RESPONSE - Environment Agency",
                document_type="CONSULTATION RESPONSE",
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 0, "Case-insensitive match should filter consultation response"
        assert len(filtered) == 1
        assert "Consultation response" in filtered[0].filter_reason

    def test_public_comment_toggle_does_not_affect_consultation_responses(self, filter):
        """
        Verifies [review-scope-control:DocumentFilter/TS-10] - Public comment
        toggle does not affect consultation responses.

        Given: A consultation response with include_public_comments=True
               but include_consultation_responses=False
        When: filter_documents is called
        Then: Consultation response is still filtered out
        """
        docs = [
            DocumentInfo(
                document_id="cr6",
                description="Consultation Response - Natural England",
                document_type="Consultation Response",
            )
        ]

        allowed, filtered = filter.filter_documents(
            docs,
            include_consultation_responses=False,
            include_public_comments=True,
        )

        assert len(allowed) == 0, "Consultation response should still be blocked"
        assert len(filtered) == 1
        assert "Consultation response" in filtered[0].filter_reason


class TestCategoryBasedFiltering:
    """
    Tests for portal category-based filtering.

    Verifies [review-output-fixes:DocumentFilter/TS-01] through [review-output-fixes:DocumentFilter/TS-06]
    """

    @pytest.fixture
    def filter(self):
        """Create a DocumentFilter instance for testing."""
        return DocumentFilter()

    def test_category_allowlist_hit(self, filter):
        """
        Verifies [review-output-fixes:DocumentFilter/TS-01] - Category allowlist takes
        precedence over title content.

        Given: Document with document_type="Supporting Documents" and
               description="Transport Response to Consultees"
        When: _should_download() is called
        Then: Returns (True, ...) — category takes precedence over title that
              would have matched consultation response denylist
        """
        should_download, reason = filter._should_download(
            document_type="Supporting Documents",
            description="Transport Response to Consultees",
        )

        assert should_download is True
        assert "Portal category" in reason

    def test_category_denylist_hit(self, filter):
        """
        Verifies [review-output-fixes:DocumentFilter/TS-02] - Category denylist denies
        regardless of title.

        Given: Document with document_type="Consultation Responses" and
               description="Transport Assessment"
        When: _should_download() is called with include_consultation_responses=False
        Then: Returns (False, ...) — category denylist denies regardless of
              title that would have matched transport allowlist
        """
        should_download, reason = filter._should_download(
            document_type="Consultation Responses",
            description="Transport Assessment",
            include_consultation_responses=False,
        )

        assert should_download is False
        assert "consultation responses" in reason.lower()

    def test_category_denylist_override(self, filter):
        """
        Verifies [review-output-fixes:DocumentFilter/TS-03] - Category denylist can be
        overridden with toggle.

        Given: Document with document_type="Consultation Responses"
        When: _should_download() is called with include_consultation_responses=True
        Then: Returns (True, ...) — override bypasses category denylist
        """
        should_download, reason = filter._should_download(
            document_type="Consultation Responses",
            description="OCC Highways Response",
            include_consultation_responses=True,
        )

        # With the toggle on, "Consultation Responses" category is not denied.
        # It's not in the category allowlist either, so it falls through to
        # title-based logic. "highway" matches the transport assessment allowlist.
        assert should_download is True

    def test_no_category_fallback(self, filter):
        """
        Verifies [review-output-fixes:DocumentFilter/TS-04] - No category falls through
        to title-based logic.

        Given: Document with document_type=None and description="Consultation Response"
        When: _should_download() is called
        Then: Returns (False, ...) — falls through to existing title-based
              consultation response denylist
        """
        should_download, reason = filter._should_download(
            document_type=None,
            description="Consultation Response",
        )

        assert should_download is False
        assert "Consultation response" in reason

    def test_unknown_category_fallback(self, filter):
        """
        Verifies [review-output-fixes:DocumentFilter/TS-05] - Unrecognised category
        falls through to title-based logic.

        Given: Document with document_type="Other Stuff" and
               description="Planning Statement"
        When: _should_download() is called
        Then: Falls through to title-based logic, returns (True, "Core application document")
        """
        should_download, reason = filter._should_download(
            document_type="Other Stuff",
            description="Planning Statement",
        )

        assert should_download is True
        assert "Core application document" in reason

    def test_comment_category_denied(self, filter):
        """
        Verifies [review-output-fixes:DocumentFilter/TS-06] - Public Comments category
        is denied.

        Given: Document with document_type="Public Comments"
        When: _should_download() is called with include_public_comments=False
        Then: Returns (False, ...)
        """
        should_download, reason = filter._should_download(
            document_type="Public Comments",
            description="Letter from resident",
            include_public_comments=False,
        )

        assert should_download is False
        assert "public comments" in reason.lower()

    def test_category_case_insensitive(self, filter):
        """
        Verifies category matching is case-insensitive.

        Given: Document with document_type="SUPPORTING DOCUMENTS"
        When: _should_download() is called
        Then: Matches category allowlist despite case difference
        """
        should_download, reason = filter._should_download(
            document_type="SUPPORTING DOCUMENTS",
            description="Some document",
        )

        assert should_download is True
        assert "Portal category" in reason

    def test_application_forms_category_allowed(self, filter):
        """
        Verifies Application Forms category is in allowlist.

        Given: Document with document_type="Application Forms"
        When: _should_download() is called
        Then: Returns (True, ...) with portal category reason
        """
        should_download, reason = filter._should_download(
            document_type="Application Forms",
            description="App Form.pdf",
        )

        assert should_download is True
        assert "Portal category" in reason

    def test_site_plans_category_allowed(self, filter):
        """
        Verifies Site Plans category is in allowlist.

        Given: Document with document_type="Site Plans"
        When: _should_download() is called
        Then: Returns (True, ...) with portal category reason
        """
        should_download, reason = filter._should_download(
            document_type="Site Plans",
            description="Masterplan.pdf",
        )

        assert should_download is True
        assert "Portal category" in reason


class TestReliableCategoryFiltering:
    """
    Tests for reliable category filtering with real portal category names.

    Verifies [reliable-category-filtering:DocumentFilter/TS-01] through TS-07.
    """

    @pytest.fixture
    def filter(self):
        return DocumentFilter()

    def test_consultee_responses_category_denied(self, filter):
        """
        Verifies [reliable-category-filtering:DocumentFilter/TS-01]

        Given: Document with document_type="Consultee Responses"
        When: _should_download() called with defaults
        Then: Returns (False, ...) — denied
        """
        should_download, reason = filter._should_download(
            document_type="Consultee Responses",
            description="Oxfordshire County Council",
        )

        assert should_download is False
        assert "consultation" in reason.lower()

    def test_consultation_responses_still_denied(self, filter):
        """
        Verifies [reliable-category-filtering:DocumentFilter/TS-02]

        Given: Document with document_type="Consultation Responses" (old name)
        When: _should_download() called with defaults
        Then: Returns (False, ...) — backward compat
        """
        should_download, reason = filter._should_download(
            document_type="Consultation Responses",
            description="Transport Response to Consultees",
        )

        assert should_download is False

    def test_consultee_responses_allowed_with_toggle(self, filter):
        """
        Verifies [reliable-category-filtering:DocumentFilter/TS-03]

        Given: Document with document_type="Consultee Responses"
        When: _should_download() called with include_consultation_responses=True
        Then: Returns (True, ...)
        """
        should_download, reason = filter._should_download(
            document_type="Consultee Responses",
            description="Oxfordshire County Council",
            include_consultation_responses=True,
        )

        assert should_download is True

    def test_proposed_plans_category_allowed(self, filter):
        """
        Verifies [reliable-category-filtering:DocumentFilter/TS-04]

        Given: Document with document_type="Proposed Plans"
        When: _should_download() called
        Then: Returns (True, ...)
        """
        should_download, reason = filter._should_download(
            document_type="Proposed Plans",
            description="Site Layout Rev B",
        )

        assert should_download is True
        assert "Portal category" in reason

    def test_officer_committee_consideration_allowed(self, filter):
        """
        Verifies [reliable-category-filtering:DocumentFilter/TS-05]

        Given: Document with document_type="Officer/Committee Consideration"
        When: _should_download() called
        Then: Returns (True, ...)
        """
        should_download, reason = filter._should_download(
            document_type="Officer/Committee Consideration",
            description="Committee Report",
        )

        assert should_download is True
        assert "Portal category" in reason

    def test_decision_and_legal_agreements_allowed(self, filter):
        """
        Verifies [reliable-category-filtering:DocumentFilter/TS-06]

        Given: Document with document_type="Decision and Legal Agreements"
        When: _should_download() called
        Then: Returns (True, ...)
        """
        should_download, reason = filter._should_download(
            document_type="Decision and Legal Agreements",
            description="Decision Notice",
        )

        assert should_download is True
        assert "Portal category" in reason

    def test_planning_application_documents_allowed(self, filter):
        """
        Verifies [reliable-category-filtering:DocumentFilter/TS-07]

        Given: Document with document_type="Planning Application Documents"
        When: _should_download() called
        Then: Returns (True, ...)
        """
        should_download, reason = filter._should_download(
            document_type="Planning Application Documents",
            description="Some miscellaneous doc",
        )

        assert should_download is True
        assert "Portal category" in reason


class TestThHeaderParserFilterIntegration:
    """
    Integration test for parser + filter pipeline with <th> section headers.

    Verifies [reliable-category-filtering:ITS-01]
    """

    def test_parser_filter_pipeline_th_headers(self):
        """
        Verifies [reliable-category-filtering:ITS-01]

        Given: HTML fixture with <th> section headers containing Application Forms (2),
               Supporting Documents (2), Site Plans (1), Consultee Responses (2),
               Public Comments (2)
        When: parse_document_list() then filter_documents()
        Then: Application Forms, Supporting Documents, Site Plans allowed (5 docs);
              Consultee Responses and Public Comments denied (4 docs)
        """
        fixture_path = (
            Path(__file__).parent.parent.parent
            / "fixtures"
            / "cherwell"
            / "document_table_th_headers.html"
        )
        html = fixture_path.read_text()

        # Parse documents from HTML
        parser = CherwellParser()
        documents = parser.parse_document_list(
            html=html,
            reference="21/03267/OUT",
            base_url="https://planningregister.cherwell.gov.uk",
        )

        assert len(documents) == 9

        # Every document must have a non-None document_type
        for doc in documents:
            assert doc.document_type is not None, (
                f"Document '{doc.description}' has document_type=None"
            )

        # Filter documents
        doc_filter = DocumentFilter()
        allowed, filtered = doc_filter.filter_documents(
            documents, application_ref="21/03267/OUT"
        )

        # Consultee Responses (2 docs) + Public Comments (2 docs) = 4 filtered
        assert len(filtered) == 4, (
            f"Expected 4 filtered, got {len(filtered)}: "
            f"{[f.description for f in filtered]}"
        )
        filtered_descriptions = {f.description for f in filtered}
        assert "Oxfordshire County Council" in filtered_descriptions
        assert "Thames Water Comments" in filtered_descriptions
        assert "Swift House, Street From Baynards Green" in filtered_descriptions
        assert "Garden Cottage, Swifts House Farm" in filtered_descriptions

        # Application Forms (2) + Supporting Documents (2) + Site Plans (1) = 5 allowed
        assert len(allowed) == 5, (
            f"Expected 5 allowed, got {len(allowed)}: "
            f"{[d.description for d in allowed]}"
        )
        allowed_descriptions = {d.description for d in allowed}
        assert "App Form" in allowed_descriptions
        assert "Cover Letter" in allowed_descriptions
        assert "Transport Assessment" in allowed_descriptions
        assert "Planning Statement" in allowed_descriptions
        assert "Masterplan" in allowed_descriptions


class TestSupersededDocumentExclusion:
    """Tests for superseded document filtering."""

    @pytest.fixture
    def filter(self):
        """Create a DocumentFilter instance for testing."""
        return DocumentFilter()

    def test_superseded_category_excluded(self, filter):
        """
        Document under superseded category is excluded.

        Given: A document with document_type "Superseded Documents"
        When: filter_documents is called
        Then: Document is filtered with superseded reason
        """
        docs = [
            DocumentInfo(
                document_id="sup1",
                description="Transport Assessment v1",
                document_type="Superseded Documents",
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 0
        assert len(filtered) == 1
        assert "Superseded" in filtered[0].filter_reason

    def test_superseded_category_case_insensitive(self, filter):
        """
        Superseded category matching is case-insensitive.

        Given: A document with document_type "SUPERSEDED DOCUMENTS"
        When: filter_documents is called
        Then: Document is filtered
        """
        docs = [
            DocumentInfo(
                document_id="sup2",
                description="Planning Statement v1",
                document_type="SUPERSEDED DOCUMENTS",
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 0
        assert len(filtered) == 1
        assert "Superseded" in filtered[0].filter_reason

    def test_superseded_in_title_excluded(self, filter):
        """
        Document with "superseded" in title is excluded.

        Given: A document with description "Superseded Transport Assessment v1"
        When: filter_documents is called
        Then: Document is filtered
        """
        docs = [
            DocumentInfo(
                document_id="sup3",
                description="Superseded Transport Assessment v1",
                document_type="Supporting Documents",
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 0
        assert len(filtered) == 1
        assert "Superseded" in filtered[0].filter_reason

    def test_superseded_in_title_case_insensitive(self, filter):
        """
        Superseded title matching is case-insensitive.

        Given: A document with description containing "SUPERSEDED" in uppercase
        When: filter_documents is called
        Then: Document is filtered
        """
        docs = [
            DocumentInfo(
                document_id="sup4",
                description="SUPERSEDED - Site Layout Plan Rev A",
                document_type=None,
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 0
        assert len(filtered) == 1

    def test_non_superseded_documents_unaffected(self, filter):
        """
        Non-superseded documents are unaffected by superseded filter.

        Given: A document with document_type "Supporting Documents" and
               description "Transport Assessment"
        When: filter_documents is called
        Then: Document is allowed (existing behaviour preserved)
        """
        docs = [
            DocumentInfo(
                document_id="nonsup1",
                description="Transport Assessment",
                document_type="Supporting Documents",
            )
        ]

        allowed, filtered = filter.filter_documents(docs)

        assert len(allowed) == 1
        assert len(filtered) == 0

    def test_superseded_not_overridden_by_skip_filter_false(self, filter):
        """
        Superseded documents are always denied — no toggle override.

        Given: A superseded document
        When: filter_documents called with all inclusion toggles enabled
        Then: Document is still filtered
        """
        docs = [
            DocumentInfo(
                document_id="sup5",
                description="Superseded Planning Statement",
                document_type="Superseded",
            )
        ]

        allowed, filtered = filter.filter_documents(
            docs,
            include_consultation_responses=True,
            include_public_comments=True,
        )

        assert len(allowed) == 0
        assert len(filtered) == 1
        assert "Superseded" in filtered[0].filter_reason

    def test_skip_filter_overrides_superseded(self, filter):
        """
        skip_filter=True overrides even superseded filtering.

        Given: A superseded document with skip_filter=True
        When: filter_documents is called
        Then: Document is allowed (skip_filter overrides everything)
        """
        docs = [
            DocumentInfo(
                document_id="sup6",
                description="Superseded Transport Assessment",
                document_type="Superseded Documents",
            )
        ]

        allowed, filtered = filter.filter_documents(docs, skip_filter=True)

        assert len(allowed) == 1
        assert len(filtered) == 0


class TestParserFilterIntegration:
    """
    Integration test for parser + filter pipeline.

    Verifies [review-output-fixes:ITS-01] - Category-based filter with real document set
    """

    def test_parser_filter_pipeline(self):
        """
        Verifies [review-output-fixes:ITS-01] - Category-based filter with real document set.

        Given: HTML fixture with section headers containing "Supporting Documents"
               and "Consultation Responses" groups, including documents that
               previously bypassed the filter ("Transport Response to Consultees",
               "Applicant's response to ATE comments")
        When: CherwellParser.parse_document_list() then DocumentFilter.filter_documents()
        Then: "Transport Response to Consultees" under "Consultation Responses" is
              filtered; "Transport Assessment" under "Supporting Documents" is allowed
        """
        fixture_path = Path(__file__).parent.parent.parent / "fixtures" / "cherwell" / "document_table_with_categories.html"
        html = fixture_path.read_text()

        # Parse documents from HTML
        parser = CherwellParser()
        documents = parser.parse_document_list(
            html=html,
            reference="25/00162/OUT",
            base_url="https://planningregister.cherwell.gov.uk",
        )

        assert len(documents) == 9, f"Expected 9 documents, got {len(documents)}"

        # Filter documents
        doc_filter = DocumentFilter()
        allowed, filtered = doc_filter.filter_documents(
            documents, application_ref="25/00162/OUT"
        )

        # Consultation Responses (3 docs) should all be filtered
        filtered_descriptions = {f.description for f in filtered}
        assert "Transport Response to Consultees" in filtered_descriptions, \
            "Transport Response to Consultees should be filtered (under Consultation Responses category)"
        assert "Applicant's response to ATE comments" in filtered_descriptions, \
            "Applicant's response to ATE comments should be filtered (under Consultation Responses category)"
        assert "Consultation Response" in filtered_descriptions, \
            "Consultation Response should be filtered (under Consultation Responses category)"

        # Supporting Documents should be allowed
        allowed_descriptions = {d.description for d in allowed}
        assert "ES Appendix 5.1 Transport Assessment (Part 1 of 7)" in allowed_descriptions, \
            "Transport Assessment under Supporting Documents should be allowed"
        assert "Planning Statement" in allowed_descriptions, \
            "Planning Statement under Supporting Documents should be allowed"
        assert "Travel Plan" in allowed_descriptions, \
            "Travel Plan under Supporting Documents should be allowed"

        # Application Forms should be allowed
        assert "App Form" in allowed_descriptions
        assert "Validation Checklist Form" in allowed_descriptions

        # Site Plans should be allowed
        assert "Masterplan" in allowed_descriptions

        # Total counts
        assert len(allowed) == 6, f"Expected 6 allowed, got {len(allowed)}: {allowed_descriptions}"
        assert len(filtered) == 3, f"Expected 3 filtered, got {len(filtered)}: {filtered_descriptions}"
