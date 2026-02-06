"""
Tests for Policy Knowledge Base schemas.

Implements test scenarios [policy-knowledge-base:PolicyModels/TS-01] through [TS-07]
"""

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from src.api.schemas.policy import (
    SOURCE_SLUG_PATTERN,
    CreatePolicyRequest,
    CreateRevisionRequest,
    PolicyCategory,
    PolicyDocumentDetail,
    PolicyDocumentRecord,
    PolicyRevisionRecord,
    PolicyRevisionSummary,
    RevisionStatus,
)


class TestSourceSlugValidation:
    """Tests for source slug pattern validation."""

    def test_valid_source_simple(self) -> None:
        """
        Verifies [policy-knowledge-base:PolicyModels/TS-01]

        Given: Source "NPPF"
        When: Validate
        Then: Passes
        """
        request = CreatePolicyRequest(
            source="NPPF",
            title="National Planning Policy Framework",
            category=PolicyCategory.NATIONAL_POLICY,
        )
        assert request.source == "NPPF"

    def test_valid_source_with_underscores(self) -> None:
        """
        Verifies [policy-knowledge-base:PolicyModels/TS-01]

        Given: Source "LTN_1_20"
        When: Validate
        Then: Passes
        """
        request = CreatePolicyRequest(
            source="LTN_1_20",
            title="Cycle Infrastructure Design",
            category=PolicyCategory.NATIONAL_GUIDANCE,
        )
        assert request.source == "LTN_1_20"

    def test_valid_source_with_numbers(self) -> None:
        """
        Verifies [policy-knowledge-base:PolicyModels/TS-01]

        Given: Source "LOCAL_PLAN_2015"
        When: Validate
        Then: Passes
        """
        request = CreatePolicyRequest(
            source="LOCAL_PLAN_2015",
            title="Cherwell Local Plan 2015",
            category=PolicyCategory.LOCAL_PLAN,
        )
        assert request.source == "LOCAL_PLAN_2015"

    def test_invalid_source_lowercase(self) -> None:
        """
        Verifies [policy-knowledge-base:PolicyModels/TS-02]

        Given: Source "invalid_format"
        When: Validate
        Then: Fails with pattern error
        """
        with pytest.raises(ValidationError) as exc_info:
            CreatePolicyRequest(
                source="invalid_format",
                title="Test",
                category=PolicyCategory.NATIONAL_POLICY,
            )
        assert "Invalid source format" in str(exc_info.value)

    def test_invalid_source_with_hyphens(self) -> None:
        """
        Verifies [policy-knowledge-base:PolicyModels/TS-02]

        Given: Source "LTN-1-20"
        When: Validate
        Then: Fails with pattern error
        """
        with pytest.raises(ValidationError) as exc_info:
            CreatePolicyRequest(
                source="LTN-1-20",
                title="Test",
                category=PolicyCategory.NATIONAL_POLICY,
            )
        assert "Invalid source format" in str(exc_info.value)

    def test_invalid_source_starting_with_number(self) -> None:
        """
        Verifies [policy-knowledge-base:PolicyModels/TS-02]

        Given: Source "1_LTN_20"
        When: Validate
        Then: Fails with pattern error
        """
        with pytest.raises(ValidationError) as exc_info:
            CreatePolicyRequest(
                source="1_LTN_20",
                title="Test",
                category=PolicyCategory.NATIONAL_POLICY,
            )
        assert "Invalid source format" in str(exc_info.value)

    def test_invalid_source_with_spaces(self) -> None:
        """
        Verifies [policy-knowledge-base:PolicyModels/TS-02]

        Given: Source "LTN 1 20"
        When: Validate
        Then: Fails with pattern error
        """
        with pytest.raises(ValidationError) as exc_info:
            CreatePolicyRequest(
                source="LTN 1 20",
                title="Test",
                category=PolicyCategory.NATIONAL_POLICY,
            )
        assert "Invalid source format" in str(exc_info.value)


class TestEffectiveDateValidation:
    """Tests for effective date range validation."""

    def test_valid_date_range(self) -> None:
        """
        Verifies [policy-knowledge-base:PolicyModels/TS-03]

        Given: effective_from < effective_to
        When: Validate
        Then: Passes
        """
        request = CreateRevisionRequest(
            version_label="July 2020",
            effective_from=date(2020, 7, 27),
            effective_to=date(2023, 9, 4),
        )
        assert request.effective_from == date(2020, 7, 27)
        assert request.effective_to == date(2023, 9, 4)

    def test_same_date_range(self) -> None:
        """
        Verifies [policy-knowledge-base:PolicyModels/TS-03]

        Given: effective_from == effective_to
        When: Validate
        Then: Passes (valid edge case)
        """
        request = CreateRevisionRequest(
            version_label="Single Day",
            effective_from=date(2024, 1, 1),
            effective_to=date(2024, 1, 1),
        )
        assert request.effective_from == request.effective_to

    def test_invalid_date_range(self) -> None:
        """
        Verifies [policy-knowledge-base:PolicyModels/TS-04]

        Given: effective_to < effective_from
        When: Validate
        Then: Fails with validation error
        """
        with pytest.raises(ValidationError) as exc_info:
            CreateRevisionRequest(
                version_label="Invalid",
                effective_from=date(2024, 12, 31),
                effective_to=date(2024, 1, 1),
            )
        assert "effective_to" in str(exc_info.value)
        assert "effective_from" in str(exc_info.value)

    def test_optional_effective_to(self) -> None:
        """
        Verifies [policy-knowledge-base:PolicyModels/TS-05]

        Given: effective_to None
        When: Validate
        Then: Passes (currently in force)
        """
        request = CreateRevisionRequest(
            version_label="December 2024",
            effective_from=date(2024, 12, 12),
            effective_to=None,
        )
        assert request.effective_from == date(2024, 12, 12)
        assert request.effective_to is None


class TestCategoryValidation:
    """Tests for policy category enum validation."""

    def test_valid_category_national_policy(self) -> None:
        """
        Verifies [policy-knowledge-base:PolicyModels/TS-06]

        Given: category "national_policy"
        When: Validate
        Then: Passes
        """
        request = CreatePolicyRequest(
            source="NPPF",
            title="NPPF",
            category=PolicyCategory.NATIONAL_POLICY,
        )
        assert request.category == PolicyCategory.NATIONAL_POLICY

    def test_valid_category_national_guidance(self) -> None:
        """
        Verifies [policy-knowledge-base:PolicyModels/TS-06]

        Given: category "national_guidance"
        When: Validate
        Then: Passes
        """
        request = CreatePolicyRequest(
            source="LTN_1_20",
            title="LTN 1/20",
            category=PolicyCategory.NATIONAL_GUIDANCE,
        )
        assert request.category == PolicyCategory.NATIONAL_GUIDANCE

    def test_valid_category_local_plan(self) -> None:
        """
        Verifies [policy-knowledge-base:PolicyModels/TS-06]
        """
        request = CreatePolicyRequest(
            source="CHERWELL_LP",
            title="Cherwell Local Plan",
            category=PolicyCategory.LOCAL_PLAN,
        )
        assert request.category == PolicyCategory.LOCAL_PLAN

    def test_valid_category_all_types(self) -> None:
        """
        Verifies [policy-knowledge-base:PolicyModels/TS-06]

        Test all category values are valid.
        """
        categories = [
            PolicyCategory.NATIONAL_POLICY,
            PolicyCategory.NATIONAL_GUIDANCE,
            PolicyCategory.LOCAL_PLAN,
            PolicyCategory.LOCAL_GUIDANCE,
            PolicyCategory.COUNTY_STRATEGY,
            PolicyCategory.SUPPLEMENTARY,
        ]
        for category in categories:
            request = CreatePolicyRequest(
                source="TEST",
                title="Test",
                category=category,
            )
            assert request.category == category

    def test_invalid_category(self) -> None:
        """
        Verifies [policy-knowledge-base:PolicyModels/TS-07]

        Given: category "invalid"
        When: Validate
        Then: Fails
        """
        with pytest.raises(ValidationError) as exc_info:
            CreatePolicyRequest(
                source="TEST",
                title="Test",
                category="invalid",  # type: ignore[arg-type]
            )
        assert "category" in str(exc_info.value).lower()


class TestPolicyDocumentRecord:
    """Tests for internal PolicyDocumentRecord model."""

    def test_create_policy_document_record(self) -> None:
        """Test creating a policy document record."""
        record = PolicyDocumentRecord(
            source="LTN_1_20",
            title="Cycle Infrastructure Design (LTN 1/20)",
            description="DfT guidance on cycle infrastructure",
            category=PolicyCategory.NATIONAL_GUIDANCE,
            created_at=datetime(2024, 1, 15, 10, 30, 0),
        )
        assert record.source == "LTN_1_20"
        assert record.title == "Cycle Infrastructure Design (LTN 1/20)"
        assert record.category == PolicyCategory.NATIONAL_GUIDANCE
        assert record.updated_at is None


class TestPolicyRevisionRecord:
    """Tests for internal PolicyRevisionRecord model."""

    def test_create_revision_record(self) -> None:
        """Test creating a revision record."""
        record = PolicyRevisionRecord(
            revision_id="rev_LTN120_2020_07",
            source="LTN_1_20",
            version_label="July 2020",
            effective_from=date(2020, 7, 27),
            effective_to=None,
            status=RevisionStatus.ACTIVE,
            file_path="/data/policy/LTN_1_20/ltn-1-20.pdf",
            chunk_count=150,
            created_at=datetime(2024, 1, 15, 10, 30, 0),
            ingested_at=datetime(2024, 1, 15, 10, 32, 0),
        )
        assert record.revision_id == "rev_LTN120_2020_07"
        assert record.status == RevisionStatus.ACTIVE
        assert record.chunk_count == 150

    def test_revision_status_values(self) -> None:
        """Test all revision status values."""
        statuses = [
            RevisionStatus.PROCESSING,
            RevisionStatus.ACTIVE,
            RevisionStatus.FAILED,
            RevisionStatus.SUPERSEDED,
        ]
        for status in statuses:
            record = PolicyRevisionRecord(
                revision_id="test",
                source="TEST",
                version_label="Test",
                effective_from=date(2024, 1, 1),
                status=status,
                created_at=datetime.now(),
            )
            assert record.status == status


class TestPolicyRevisionSummary:
    """Tests for PolicyRevisionSummary model."""

    def test_create_revision_summary(self) -> None:
        """Test creating a revision summary."""
        summary = PolicyRevisionSummary(
            revision_id="rev_NPPF_2023_09",
            version_label="September 2023",
            effective_from=date(2023, 9, 5),
            effective_to=date(2024, 12, 11),
            status=RevisionStatus.SUPERSEDED,
            chunk_count=200,
            ingested_at=datetime(2023, 9, 10, 14, 0, 0),
        )
        assert summary.revision_id == "rev_NPPF_2023_09"
        assert summary.effective_to == date(2024, 12, 11)


class TestPolicyDocumentDetail:
    """Tests for PolicyDocumentDetail model."""

    def test_create_policy_with_revisions(self) -> None:
        """Test creating a policy with multiple revisions."""
        revisions = [
            PolicyRevisionSummary(
                revision_id="rev_NPPF_2024_12",
                version_label="December 2024",
                effective_from=date(2024, 12, 12),
                effective_to=None,
                status=RevisionStatus.ACTIVE,
            ),
            PolicyRevisionSummary(
                revision_id="rev_NPPF_2023_09",
                version_label="September 2023",
                effective_from=date(2023, 9, 5),
                effective_to=date(2024, 12, 11),
                status=RevisionStatus.SUPERSEDED,
            ),
        ]

        policy = PolicyDocumentDetail(
            source="NPPF",
            title="National Planning Policy Framework",
            description="National planning policy for England",
            category=PolicyCategory.NATIONAL_POLICY,
            revisions=revisions,
            current_revision=revisions[0],
            revision_count=2,
            created_at=datetime(2024, 1, 1, 0, 0, 0),
        )

        assert policy.source == "NPPF"
        assert len(policy.revisions) == 2
        assert policy.current_revision.revision_id == "rev_NPPF_2024_12"
        assert policy.revision_count == 2


class TestSourceSlugPattern:
    """Additional tests for the SOURCE_SLUG_PATTERN regex."""

    @pytest.mark.parametrize(
        "source,expected",
        [
            ("NPPF", True),
            ("LTN_1_20", True),
            ("MANUAL_FOR_STREETS", True),
            ("LCWIP2024", True),
            ("A", True),
            ("A1", True),
            ("A_B", True),
            ("A1_B2_C3", True),
            ("nppf", False),
            ("Nppf", False),
            ("LTN-1-20", False),
            ("LTN 1 20", False),
            ("1LTN", False),
            ("_LTN", False),
            ("LTN_", False),
            ("LTN__20", False),
            ("", False),
        ],
    )
    def test_source_slug_pattern(self, source: str, expected: bool) -> None:
        """Test source slug pattern matching."""
        result = SOURCE_SLUG_PATTERN.match(source) is not None
        assert result == expected, f"Expected {source} to {'match' if expected else 'not match'}"
