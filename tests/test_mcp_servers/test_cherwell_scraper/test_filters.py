"""
Tests for document filtering.

Verifies [document-filtering:DocumentFilter/TS-01] through [document-filtering:DocumentFilter/TS-10]
Verifies [document-filtering:FilteredDocumentInfo/TS-01] through [document-filtering:FilteredDocumentInfo/TS-02]
"""

from datetime import date

import pytest

from src.mcp_servers.cherwell_scraper.filters import DocumentFilter, FilteredDocumentInfo
from src.mcp_servers.cherwell_scraper.models import DocumentInfo


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
