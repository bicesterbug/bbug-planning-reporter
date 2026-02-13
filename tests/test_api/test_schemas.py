"""
Tests for API schemas.

Implements test scenarios from [foundation-api:ReviewRequestModels/TS-01] through [TS-04]
Implements test scenarios from [key-documents:KeyDocument/TS-01] through [TS-02]
Implements test scenarios from [key-documents:ReviewContent/TS-01] through [TS-03]
"""

import pytest
from pydantic import ValidationError

from src.api.schemas import (
    KeyDocument,
    ReviewContent,
    ReviewOptionsRequest,
    ReviewRequest,
)
from src.shared.models import ReviewOptions


class TestApplicationReferenceValidation:
    """Tests for application reference validation."""

    @pytest.mark.parametrize(
        "ref",
        [
            "25/01178/REM",
            "08/00707/F",
            "23/01421/TCA",
            "24/12345/OUT",
            "99/0001/F",
        ],
    )
    def test_valid_reference_patterns(self, ref: str) -> None:
        """
        Verifies [foundation-api:ReviewRequestModels/TS-01]

        Given: Various valid refs
        When: Validate against pattern
        Then: All pass
        """
        request = ReviewRequest(application_ref=ref)
        assert request.application_ref == ref

    @pytest.mark.parametrize(
        "ref",
        [
            "INVALID",
            "25-01178-REM",
            "25/178/REM",  # Only 3 digits (minimum is 4)
            "2025/01178/REM",  # 4-digit year
            "25/01178/TOOLONG",  # Type code too long
            "25/01178/rem",  # Lowercase
            "",
            "25//REM",
            "/01178/REM",
        ],
    )
    def test_invalid_reference_patterns(self, ref: str) -> None:
        """
        Verifies [foundation-api:ReviewRequestModels/TS-02]

        Given: Various invalid refs
        When: Validate against pattern
        Then: All fail
        """
        with pytest.raises(ValidationError) as exc_info:
            ReviewRequest(application_ref=ref)

        # Check that the error is about the application_ref
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("application_ref",) for e in errors)


class TestReviewRequestOptionalFields:
    """Tests for optional fields handling."""

    def test_minimal_request(self) -> None:
        """
        Verifies [foundation-api:ReviewRequestModels/TS-04]
        Verifies [global-webhooks:ReviewRequest/TS-01] - No webhook field

        Given: Minimal request with only required fields
        When: Parse request body
        Then: Defaults applied, optional fields None
        """
        request = ReviewRequest(application_ref="25/01178/REM")

        assert request.application_ref == "25/01178/REM"
        assert request.options is None
        assert not hasattr(request, "webhook")

    def test_full_request(self) -> None:
        """
        Given: Request with all optional fields
        When: Parse request body
        Then: All fields populated
        """
        request = ReviewRequest(
            application_ref="25/01178/REM",
            options={
                "focus_areas": ["cycle_parking", "cycle_routes"],
                "output_format": "markdown",
            },
        )

        assert request.application_ref == "25/01178/REM"
        assert request.options is not None
        assert request.options.focus_areas == ["cycle_parking", "cycle_routes"]

    def test_webhook_field_silently_ignored(self) -> None:
        """
        Verifies [global-webhooks:ReviewRequest/TS-02] - Unknown webhook field silently ignored.

        Given: A review submitted with a webhook field (old client)
        When: Request is parsed
        Then: Request is accepted; webhook is ignored
        """
        request = ReviewRequest(
            application_ref="25/01178/REM",
            webhook={
                "url": "https://example.com/hooks",
                "secret": "test_secret",
            },
        )

        assert request.application_ref == "25/01178/REM"
        assert not hasattr(request, "webhook")


class TestKeyDocument:
    """
    Tests for KeyDocument schema.

    Implements [key-documents:KeyDocument/TS-01] and [key-documents:KeyDocument/TS-02]
    """

    def test_valid_key_document(self) -> None:
        """
        Verifies [key-documents:KeyDocument/TS-01] - Valid KeyDocument

        Given: title="Transport Assessment", category="Transport & Access", summary="...", url="https://..."
        When: Create KeyDocument
        Then: All fields accessible, serializes to JSON correctly
        """
        doc = KeyDocument(
            title="Transport Assessment",
            category="Transport & Access",
            summary="Analyses traffic impacts of the proposed development including junction capacity modelling.",
            url="https://planningregister.cherwell.gov.uk/Document/Download?id=123",
        )

        assert doc.title == "Transport Assessment"
        assert doc.category == "Transport & Access"
        assert doc.summary.startswith("Analyses traffic impacts")
        assert doc.url == "https://planningregister.cherwell.gov.uk/Document/Download?id=123"

        # Verify JSON serialization
        data = doc.model_dump()
        assert data["title"] == "Transport Assessment"
        assert data["category"] == "Transport & Access"
        assert data["summary"] is not None
        assert data["url"] is not None

    def test_key_document_with_null_url(self) -> None:
        """
        Verifies [key-documents:KeyDocument/TS-02] - KeyDocument with null url

        Given: url is None
        When: Create KeyDocument
        Then: Serializes with url: null
        """
        doc = KeyDocument(
            title="Planning Statement",
            category="Application Core",
            summary="Sets out the planning justification for the proposed development.",
        )

        assert doc.url is None

        data = doc.model_dump()
        assert data["url"] is None


class TestReviewContentKeyDocuments:
    """
    Tests for key_documents field on ReviewContent.

    Implements [key-documents:ReviewContent/TS-01] through [TS-03]
    """

    def test_key_documents_in_review_content(self) -> None:
        """
        Verifies [key-documents:ReviewContent/TS-01] - Key documents in API response

        Given: A completed review with key_documents data
        When: GET /api/v1/reviews/{id}
        Then: Response includes review.key_documents array
        """
        content = ReviewContent(
            overall_rating="amber",
            summary="Test review summary",
            key_documents=[
                KeyDocument(
                    title="Transport Assessment",
                    category="Transport & Access",
                    summary="Analyses traffic impacts.",
                    url="https://example.com/doc.pdf",
                ),
                KeyDocument(
                    title="Design and Access Statement",
                    category="Design & Layout",
                    summary="Describes site layout.",
                    url="https://example.com/das.pdf",
                ),
            ],
        )

        assert content.key_documents is not None
        assert len(content.key_documents) == 2
        assert content.key_documents[0].title == "Transport Assessment"
        assert content.key_documents[1].category == "Design & Layout"

    def test_null_key_documents_for_old_reviews(self) -> None:
        """
        Verifies [key-documents:ReviewContent/TS-02] - Null for old reviews

        Given: A review completed before this feature
        When: GET /api/v1/reviews/{id}
        Then: review.key_documents is null
        """
        content = ReviewContent(
            overall_rating="green",
            summary="Old review without key documents",
        )

        assert content.key_documents is None

        data = content.model_dump()
        assert data["key_documents"] is None

    def test_key_document_schema_serialization(self) -> None:
        """
        Verifies [key-documents:ReviewContent/TS-03] - Schema serialization

        Given: KeyDocument with all fields populated
        When: Serialize to JSON
        Then: All fields present: title, category, summary, url
        """
        doc = KeyDocument(
            title="Travel Plan Framework",
            category="Transport & Access",
            summary="Outlines sustainable travel targets and monitoring strategy.",
            url="https://planningregister.cherwell.gov.uk/Document/Download?id=456",
        )

        data = doc.model_dump()
        assert set(data.keys()) == {"title", "category", "summary", "url"}
        assert all(v is not None for v in data.values())


class TestReviewOptionsRequestToggles:
    """
    Tests for review-scope-control toggle fields on ReviewOptionsRequest.

    Verifies [review-scope-control:ReviewOptionsRequest/TS-01] through [TS-03]
    """

    def test_defaults_both_toggles_to_false(self) -> None:
        """
        Verifies [review-scope-control:ReviewOptionsRequest/TS-01] - Defaults

        Given: No toggle fields in request JSON
        When: ReviewOptionsRequest is parsed
        Then: Both fields are False
        """
        options = ReviewOptionsRequest()

        assert options.include_consultation_responses is False
        assert options.include_public_comments is False

    def test_accepts_explicit_true_values(self) -> None:
        """
        Verifies [review-scope-control:ReviewOptionsRequest/TS-02] - Accepts true

        Given: Request JSON includes both toggles as true
        When: ReviewOptionsRequest is parsed
        Then: Both fields are True
        """
        options = ReviewOptionsRequest(
            include_consultation_responses=True,
            include_public_comments=True,
        )

        assert options.include_consultation_responses is True
        assert options.include_public_comments is True

    def test_existing_fields_unaffected(self) -> None:
        """
        Verifies [review-scope-control:ReviewOptionsRequest/TS-03] - Backward compatibility

        Given: Request with only existing fields (focus_areas, etc.)
        When: ReviewOptionsRequest is parsed
        Then: Existing fields parsed correctly, new fields default to false
        """
        options = ReviewOptionsRequest(
            focus_areas=["cycle_parking", "cycle_routes"],
            output_format="json",
            include_policy_matrix=False,
        )

        assert options.focus_areas == ["cycle_parking", "cycle_routes"]
        assert options.output_format == "json"
        assert options.include_policy_matrix is False
        assert options.include_consultation_responses is False
        assert options.include_public_comments is False


class TestReviewOptionsToggles:
    """
    Tests for review-scope-control toggle fields on internal ReviewOptions model.

    Verifies [review-scope-control:ReviewOptions/TS-01]
    """

    def test_defaults_both_toggles_to_false(self) -> None:
        """
        Verifies [review-scope-control:ReviewOptions/TS-01] - Defaults

        Given: ReviewOptions created with no toggle fields
        When: Model is instantiated
        Then: Both fields are False
        """
        options = ReviewOptions()

        assert options.include_consultation_responses is False
        assert options.include_public_comments is False
