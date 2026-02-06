"""
Tests for PolicyRegistry Redis operations.

Implements test scenarios [policy-knowledge-base:PolicyRegistry/TS-01] through [TS-12]
"""

from datetime import date, datetime

import fakeredis.aioredis
import pytest

from src.api.schemas.policy import PolicyCategory, RevisionStatus
from src.shared.policy_registry import (
    CannotDeleteSoleRevisionError,
    PolicyAlreadyExistsError,
    PolicyNotFoundError,
    PolicyRegistry,
    RevisionNotFoundError,
    RevisionOverlapError,
)


@pytest.fixture
async def registry(fake_redis: fakeredis.aioredis.FakeRedis) -> PolicyRegistry:
    """Create a PolicyRegistry with fake Redis backend."""
    return PolicyRegistry(fake_redis)


class TestCreatePolicy:
    """Tests for creating policy documents."""

    async def test_create_policy_success(
        self, registry: PolicyRegistry
    ) -> None:
        """
        Verifies [policy-knowledge-base:PolicyRegistry/TS-01]

        Given: Valid source "LTN_1_20"
        When: Call create_policy
        Then: Policy stored in Redis, added to policies_all set
        """
        record = await registry.create_policy(
            source="LTN_1_20",
            title="Cycle Infrastructure Design (LTN 1/20)",
            category=PolicyCategory.NATIONAL_GUIDANCE,
            description="DfT guidance on cycle infrastructure",
        )

        assert record.source == "LTN_1_20"
        assert record.title == "Cycle Infrastructure Design (LTN 1/20)"
        assert record.category == PolicyCategory.NATIONAL_GUIDANCE
        assert record.description == "DfT guidance on cycle infrastructure"
        assert record.created_at is not None

        # Verify can retrieve
        retrieved = await registry.get_policy("LTN_1_20")
        assert retrieved is not None
        assert retrieved.source == "LTN_1_20"

    async def test_duplicate_policy_prevention(
        self, registry: PolicyRegistry
    ) -> None:
        """
        Verifies [policy-knowledge-base:PolicyRegistry/TS-02]

        Given: Policy "LTN_1_20" exists
        When: Call create_policy with same source
        Then: Raises PolicyAlreadyExistsError
        """
        await registry.create_policy(
            source="LTN_1_20",
            title="Original",
            category=PolicyCategory.NATIONAL_GUIDANCE,
        )

        with pytest.raises(PolicyAlreadyExistsError) as exc_info:
            await registry.create_policy(
                source="LTN_1_20",
                title="Duplicate",
                category=PolicyCategory.NATIONAL_GUIDANCE,
            )

        assert exc_info.value.source == "LTN_1_20"
        assert "LTN_1_20" in str(exc_info.value)


class TestCreateRevision:
    """Tests for creating policy revisions."""

    async def test_create_revision_success(
        self, registry: PolicyRegistry
    ) -> None:
        """
        Verifies [policy-knowledge-base:PolicyRegistry/TS-03]

        Given: Policy exists, valid dates
        When: Call create_revision
        Then: Revision stored, added to sorted set
        """
        await registry.create_policy(
            source="LTN_1_20",
            title="Cycle Infrastructure Design",
            category=PolicyCategory.NATIONAL_GUIDANCE,
        )

        record = await registry.create_revision(
            source="LTN_1_20",
            revision_id="rev_LTN120_2020_07",
            version_label="July 2020",
            effective_from=date(2020, 7, 27),
            effective_to=None,
            file_path="/data/policy/LTN_1_20/ltn-1-20.pdf",
            file_size_bytes=1024000,
            page_count=100,
        )

        assert record.revision_id == "rev_LTN120_2020_07"
        assert record.source == "LTN_1_20"
        assert record.version_label == "July 2020"
        assert record.effective_from == date(2020, 7, 27)
        assert record.effective_to is None
        assert record.status == RevisionStatus.PROCESSING
        assert record.file_path == "/data/policy/LTN_1_20/ltn-1-20.pdf"

        # Verify can retrieve
        retrieved = await registry.get_revision("LTN_1_20", "rev_LTN120_2020_07")
        assert retrieved is not None
        assert retrieved.revision_id == "rev_LTN120_2020_07"

    async def test_create_revision_policy_not_found(
        self, registry: PolicyRegistry
    ) -> None:
        """
        Given: Policy does not exist
        When: Call create_revision
        Then: Raises PolicyNotFoundError
        """
        with pytest.raises(PolicyNotFoundError) as exc_info:
            await registry.create_revision(
                source="NONEXISTENT",
                revision_id="rev_test",
                version_label="Test",
                effective_from=date(2024, 1, 1),
            )

        assert exc_info.value.source == "NONEXISTENT"

    async def test_overlapping_dates_rejected(
        self, registry: PolicyRegistry
    ) -> None:
        """
        Verifies [policy-knowledge-base:PolicyRegistry/TS-04]

        Given: Revision with effective_from 2024-01-01 to 2024-12-31 exists
        When: Create revision with overlapping dates (2024-06-01 to 2024-08-31)
        Then: Raises RevisionOverlapError
        """
        await registry.create_policy(
            source="NPPF",
            title="NPPF",
            category=PolicyCategory.NATIONAL_POLICY,
        )

        # Create existing revision
        await registry.create_revision(
            source="NPPF",
            revision_id="rev_existing",
            version_label="2024",
            effective_from=date(2024, 1, 1),
            effective_to=date(2024, 12, 31),
        )

        # Mark it as active so it's considered in overlap check
        await registry.update_revision(
            "NPPF", "rev_existing", status=RevisionStatus.ACTIVE
        )

        with pytest.raises(RevisionOverlapError) as exc_info:
            await registry.create_revision(
                source="NPPF",
                revision_id="rev_new",
                version_label="Mid 2024",
                effective_from=date(2024, 6, 1),
                effective_to=date(2024, 8, 31),
            )

        assert exc_info.value.source == "NPPF"
        assert exc_info.value.conflicting_id == "rev_existing"

    async def test_auto_supersession(
        self, registry: PolicyRegistry
    ) -> None:
        """
        Verifies [policy-knowledge-base:PolicyRegistry/TS-12]

        Given: Revision A active (no effective_to)
        When: Create Revision B with effective_from 2025-01-01
        Then: Revision A's effective_to set to 2024-12-31
        """
        await registry.create_policy(
            source="NPPF",
            title="NPPF",
            category=PolicyCategory.NATIONAL_POLICY,
        )

        # Create revision A with no effective_to (currently in force)
        await registry.create_revision(
            source="NPPF",
            revision_id="rev_A",
            version_label="2023",
            effective_from=date(2023, 9, 5),
            effective_to=None,
        )

        # Mark as active
        await registry.update_revision("NPPF", "rev_A", status=RevisionStatus.ACTIVE)

        # Create revision B - should auto-supersede A
        record = await registry.create_revision(
            source="NPPF",
            revision_id="rev_B",
            version_label="2025",
            effective_from=date(2025, 1, 1),
            effective_to=None,
        )

        assert record.revision_id == "rev_B"
        assert record.effective_from == date(2025, 1, 1)

        # Verify rev_A's effective_to was updated
        rev_a = await registry.get_revision("NPPF", "rev_A")
        assert rev_a is not None
        assert rev_a.effective_to == date(2024, 12, 31)


class TestGetPolicyWithRevisions:
    """Tests for getting policy with all revisions."""

    async def test_get_policy_with_revisions(
        self, registry: PolicyRegistry
    ) -> None:
        """
        Verifies [policy-knowledge-base:PolicyRegistry/TS-05]

        Given: Policy with 3 revisions
        When: Call get_policy_with_revisions
        Then: Returns policy with all revisions sorted by date
        """
        await registry.create_policy(
            source="NPPF",
            title="National Planning Policy Framework",
            category=PolicyCategory.NATIONAL_POLICY,
            description="National planning policy",
        )

        # Create 3 revisions
        await registry.create_revision(
            source="NPPF",
            revision_id="rev_NPPF_2021_07",
            version_label="July 2021",
            effective_from=date(2021, 7, 20),
            effective_to=date(2023, 9, 4),
        )
        await registry.update_revision(
            "NPPF", "rev_NPPF_2021_07", status=RevisionStatus.SUPERSEDED
        )

        await registry.create_revision(
            source="NPPF",
            revision_id="rev_NPPF_2023_09",
            version_label="September 2023",
            effective_from=date(2023, 9, 5),
            effective_to=date(2024, 12, 11),
        )
        await registry.update_revision(
            "NPPF", "rev_NPPF_2023_09", status=RevisionStatus.SUPERSEDED
        )

        await registry.create_revision(
            source="NPPF",
            revision_id="rev_NPPF_2024_12",
            version_label="December 2024",
            effective_from=date(2024, 12, 12),
            effective_to=None,
        )
        await registry.update_revision(
            "NPPF", "rev_NPPF_2024_12", status=RevisionStatus.ACTIVE
        )

        result = await registry.get_policy_with_revisions("NPPF")

        assert result is not None
        assert result.source == "NPPF"
        assert result.title == "National Planning Policy Framework"
        assert len(result.revisions) == 3
        assert result.revision_count == 3
        # Revisions are ordered by effective_from DESC
        assert result.revisions[0].revision_id == "rev_NPPF_2024_12"
        assert result.revisions[1].revision_id == "rev_NPPF_2023_09"
        assert result.revisions[2].revision_id == "rev_NPPF_2021_07"


class TestListPolicies:
    """Tests for listing policies."""

    async def test_list_all_policies(
        self, registry: PolicyRegistry
    ) -> None:
        """
        Verifies [policy-knowledge-base:PolicyRegistry/TS-06]

        Given: 3 policies registered
        When: Call list_policies
        Then: Returns all 3 with current revision info
        """
        await registry.create_policy(
            source="NPPF",
            title="NPPF",
            category=PolicyCategory.NATIONAL_POLICY,
        )
        await registry.create_policy(
            source="LTN_1_20",
            title="Cycle Infrastructure Design",
            category=PolicyCategory.NATIONAL_GUIDANCE,
        )
        await registry.create_policy(
            source="CHERWELL_LP",
            title="Cherwell Local Plan",
            category=PolicyCategory.LOCAL_PLAN,
        )

        result = await registry.list_policies()

        assert len(result) == 3
        sources = [p.source for p in result]
        assert "NPPF" in sources
        assert "LTN_1_20" in sources
        assert "CHERWELL_LP" in sources

    async def test_list_policies_by_category(
        self, registry: PolicyRegistry
    ) -> None:
        """
        Given: Policies with different categories
        When: List with category filter
        Then: Returns only matching policies
        """
        await registry.create_policy(
            source="NPPF",
            title="NPPF",
            category=PolicyCategory.NATIONAL_POLICY,
        )
        await registry.create_policy(
            source="LTN_1_20",
            title="LTN 1/20",
            category=PolicyCategory.NATIONAL_GUIDANCE,
        )
        await registry.create_policy(
            source="MFS",
            title="Manual for Streets",
            category=PolicyCategory.NATIONAL_GUIDANCE,
        )

        result = await registry.list_policies(category=PolicyCategory.NATIONAL_GUIDANCE)

        assert len(result) == 2
        sources = [p.source for p in result]
        assert "LTN_1_20" in sources
        assert "MFS" in sources
        assert "NPPF" not in sources


class TestUpdateRevision:
    """Tests for updating revision metadata."""

    async def test_update_revision_metadata(
        self, registry: PolicyRegistry
    ) -> None:
        """
        Verifies [policy-knowledge-base:PolicyRegistry/TS-07]

        Given: Existing revision
        When: Call update_revision with new effective_to
        Then: Metadata updated
        """
        await registry.create_policy(
            source="NPPF",
            title="NPPF",
            category=PolicyCategory.NATIONAL_POLICY,
        )
        await registry.create_revision(
            source="NPPF",
            revision_id="rev_NPPF_2023_09",
            version_label="September 2023",
            effective_from=date(2023, 9, 5),
            effective_to=None,
        )

        result = await registry.update_revision(
            source="NPPF",
            revision_id="rev_NPPF_2023_09",
            effective_to=date(2024, 12, 11),
            status=RevisionStatus.SUPERSEDED,
        )

        assert result.effective_to == date(2024, 12, 11)
        assert result.status == RevisionStatus.SUPERSEDED

        # Verify persisted
        retrieved = await registry.get_revision("NPPF", "rev_NPPF_2023_09")
        assert retrieved is not None
        assert retrieved.effective_to == date(2024, 12, 11)
        assert retrieved.status == RevisionStatus.SUPERSEDED

    async def test_update_revision_not_found(
        self, registry: PolicyRegistry
    ) -> None:
        """
        Given: Revision does not exist
        When: Call update_revision
        Then: Raises RevisionNotFoundError
        """
        with pytest.raises(RevisionNotFoundError) as exc_info:
            await registry.update_revision(
                source="NPPF",
                revision_id="nonexistent",
                status=RevisionStatus.ACTIVE,
            )

        assert exc_info.value.source == "NPPF"
        assert exc_info.value.revision_id == "nonexistent"


class TestDeleteRevision:
    """Tests for deleting revisions."""

    async def test_delete_revision_success(
        self, registry: PolicyRegistry
    ) -> None:
        """
        Verifies [policy-knowledge-base:PolicyRegistry/TS-08]

        Given: Revision with multiple active revisions for policy
        When: Call delete_revision
        Then: Revision removed from Redis
        """
        await registry.create_policy(
            source="NPPF",
            title="NPPF",
            category=PolicyCategory.NATIONAL_POLICY,
        )

        # Create two revisions
        await registry.create_revision(
            source="NPPF",
            revision_id="rev_NPPF_2021_07",
            version_label="July 2021",
            effective_from=date(2021, 7, 20),
            effective_to=date(2023, 9, 4),
        )
        await registry.update_revision(
            "NPPF", "rev_NPPF_2021_07", status=RevisionStatus.SUPERSEDED
        )

        await registry.create_revision(
            source="NPPF",
            revision_id="rev_NPPF_2023_09",
            version_label="September 2023",
            effective_from=date(2023, 9, 5),
            effective_to=None,
        )
        await registry.update_revision(
            "NPPF", "rev_NPPF_2023_09", status=RevisionStatus.ACTIVE
        )

        # Delete the superseded revision
        result = await registry.delete_revision("NPPF", "rev_NPPF_2021_07")

        assert result is True

        # Verify deleted
        retrieved = await registry.get_revision("NPPF", "rev_NPPF_2021_07")
        assert retrieved is None

        # Verify other revision still exists
        other = await registry.get_revision("NPPF", "rev_NPPF_2023_09")
        assert other is not None

    async def test_cannot_delete_sole_active_revision(
        self, registry: PolicyRegistry
    ) -> None:
        """
        Verifies [policy-knowledge-base:PolicyRegistry/TS-09]

        Given: Policy with one active revision
        When: Call delete_revision
        Then: Raises CannotDeleteSoleRevisionError
        """
        await registry.create_policy(
            source="NPPF",
            title="NPPF",
            category=PolicyCategory.NATIONAL_POLICY,
        )
        await registry.create_revision(
            source="NPPF",
            revision_id="rev_NPPF_2024_12",
            version_label="December 2024",
            effective_from=date(2024, 12, 12),
            effective_to=None,
        )
        await registry.update_revision(
            "NPPF", "rev_NPPF_2024_12", status=RevisionStatus.ACTIVE
        )

        with pytest.raises(CannotDeleteSoleRevisionError) as exc_info:
            await registry.delete_revision("NPPF", "rev_NPPF_2024_12")

        assert exc_info.value.source == "NPPF"
        assert exc_info.value.revision_id == "rev_NPPF_2024_12"


class TestEffectiveDateResolution:
    """Tests for effective date resolution."""

    async def test_get_effective_revision_for_date(
        self, registry: PolicyRegistry
    ) -> None:
        """
        Verifies [policy-knowledge-base:PolicyRegistry/TS-10]

        Given: Revisions: 2021-07-20, 2023-09-05, 2024-12-12
        When: Call get_effective_revision_for_date("2024-03-15")
        Then: Returns revision from 2023-09-05
        """
        await registry.create_policy(
            source="NPPF",
            title="NPPF",
            category=PolicyCategory.NATIONAL_POLICY,
        )

        # Create 3 revisions
        await registry.create_revision(
            source="NPPF",
            revision_id="rev_NPPF_2021_07",
            version_label="July 2021",
            effective_from=date(2021, 7, 20),
            effective_to=date(2023, 9, 4),
        )
        await registry.update_revision(
            "NPPF", "rev_NPPF_2021_07", status=RevisionStatus.SUPERSEDED
        )

        await registry.create_revision(
            source="NPPF",
            revision_id="rev_NPPF_2023_09",
            version_label="September 2023",
            effective_from=date(2023, 9, 5),
            effective_to=date(2024, 12, 11),
        )
        await registry.update_revision(
            "NPPF", "rev_NPPF_2023_09", status=RevisionStatus.SUPERSEDED
        )

        await registry.create_revision(
            source="NPPF",
            revision_id="rev_NPPF_2024_12",
            version_label="December 2024",
            effective_from=date(2024, 12, 12),
            effective_to=None,
        )
        await registry.update_revision(
            "NPPF", "rev_NPPF_2024_12", status=RevisionStatus.ACTIVE
        )

        result = await registry.get_effective_revision_for_date("NPPF", date(2024, 3, 15))

        assert result is not None
        assert result.revision_id == "rev_NPPF_2023_09"
        assert result.effective_from == date(2023, 9, 5)
        assert result.effective_to == date(2024, 12, 11)

    async def test_no_revision_for_early_date(
        self, registry: PolicyRegistry
    ) -> None:
        """
        Verifies [policy-knowledge-base:PolicyRegistry/TS-11]

        Given: First revision effective 2020-07-27
        When: Call get_effective_revision_for_date("2019-01-01")
        Then: Returns None
        """
        await registry.create_policy(
            source="NPPF",
            title="NPPF",
            category=PolicyCategory.NATIONAL_POLICY,
        )
        await registry.create_revision(
            source="NPPF",
            revision_id="rev_NPPF_2020_07",
            version_label="July 2020",
            effective_from=date(2020, 7, 27),
            effective_to=None,
        )
        await registry.update_revision(
            "NPPF", "rev_NPPF_2020_07", status=RevisionStatus.ACTIVE
        )

        result = await registry.get_effective_revision_for_date("NPPF", date(2019, 1, 1))

        assert result is None

    async def test_effective_revision_on_exact_date(
        self, registry: PolicyRegistry
    ) -> None:
        """
        Given: Revision effective from 2020-07-27
        When: Query for 2020-07-27
        Then: Returns that revision
        """
        await registry.create_policy(
            source="NPPF",
            title="NPPF",
            category=PolicyCategory.NATIONAL_POLICY,
        )
        await registry.create_revision(
            source="NPPF",
            revision_id="rev_NPPF_2020_07",
            version_label="July 2020",
            effective_from=date(2020, 7, 27),
            effective_to=None,
        )
        await registry.update_revision(
            "NPPF", "rev_NPPF_2020_07", status=RevisionStatus.ACTIVE
        )

        result = await registry.get_effective_revision_for_date("NPPF", date(2020, 7, 27))

        assert result is not None
        assert result.revision_id == "rev_NPPF_2020_07"


class TestKeyGeneration:
    """Tests for Redis key generation methods."""

    def test_policy_key(self, registry: PolicyRegistry) -> None:
        """Test policy key generation."""
        assert registry._policy_key("LTN_1_20") == "policy:LTN_1_20"

    def test_revision_key(self, registry: PolicyRegistry) -> None:
        """Test revision key generation."""
        assert (
            registry._revision_key("LTN_1_20", "rev_LTN120_2020_07")
            == "policy_revision:LTN_1_20:rev_LTN120_2020_07"
        )

    def test_revisions_set_key(self, registry: PolicyRegistry) -> None:
        """Test revisions sorted set key generation."""
        assert registry._revisions_set_key("LTN_1_20") == "policy_revisions:LTN_1_20"

    def test_policies_all_key(self, registry: PolicyRegistry) -> None:
        """Test policies_all key generation."""
        assert registry._policies_all_key() == "policies_all"


class TestSerialization:
    """Tests for serialization/deserialization."""

    def test_serialize_policy(self, registry: PolicyRegistry) -> None:
        """Test policy serialization."""
        from src.api.schemas.policy import PolicyDocumentRecord

        record = PolicyDocumentRecord(
            source="LTN_1_20",
            title="Cycle Infrastructure Design",
            description="DfT guidance",
            category=PolicyCategory.NATIONAL_GUIDANCE,
            created_at=datetime(2024, 1, 15, 10, 30, 0),
        )

        data = registry._serialize_policy(record)

        assert data["source"] == "LTN_1_20"
        assert data["title"] == "Cycle Infrastructure Design"
        assert data["description"] == "DfT guidance"
        assert data["category"] == "national_guidance"
        assert data["created_at"] == "2024-01-15T10:30:00"
        assert data["updated_at"] == ""

    def test_deserialize_policy(self, registry: PolicyRegistry) -> None:
        """Test policy deserialization."""
        data = {
            "source": "LTN_1_20",
            "title": "Cycle Infrastructure Design",
            "description": "DfT guidance",
            "category": "national_guidance",
            "created_at": "2024-01-15T10:30:00",
            "updated_at": "",
        }

        record = registry._deserialize_policy(data)

        assert record.source == "LTN_1_20"
        assert record.title == "Cycle Infrastructure Design"
        assert record.description == "DfT guidance"
        assert record.category == PolicyCategory.NATIONAL_GUIDANCE
        assert record.created_at == datetime(2024, 1, 15, 10, 30, 0)
        assert record.updated_at is None

    def test_serialize_revision(self, registry: PolicyRegistry) -> None:
        """Test revision serialization."""
        from src.api.schemas.policy import PolicyRevisionRecord

        record = PolicyRevisionRecord(
            revision_id="rev_LTN120_2020_07",
            source="LTN_1_20",
            version_label="July 2020",
            effective_from=date(2020, 7, 27),
            effective_to=None,
            status=RevisionStatus.ACTIVE,
            file_path="/data/policy/ltn.pdf",
            chunk_count=150,
            created_at=datetime(2024, 1, 15, 10, 30, 0),
            ingested_at=datetime(2024, 1, 15, 10, 32, 0),
        )

        data = registry._serialize_revision(record)

        assert data["revision_id"] == "rev_LTN120_2020_07"
        assert data["effective_from"] == "2020-07-27"
        assert data["effective_to"] == ""
        assert data["status"] == "active"
        assert data["chunk_count"] == "150"

    def test_deserialize_revision(self, registry: PolicyRegistry) -> None:
        """Test revision deserialization."""
        data = {
            "revision_id": "rev_LTN120_2020_07",
            "source": "LTN_1_20",
            "version_label": "July 2020",
            "effective_from": "2020-07-27",
            "effective_to": "",
            "status": "active",
            "file_path": "/data/policy/ltn.pdf",
            "file_size_bytes": "",
            "page_count": "",
            "chunk_count": "150",
            "notes": "",
            "created_at": "2024-01-15T10:30:00",
            "ingested_at": "2024-01-15T10:32:00",
            "error": "",
        }

        record = registry._deserialize_revision(data)

        assert record.revision_id == "rev_LTN120_2020_07"
        assert record.effective_from == date(2020, 7, 27)
        assert record.effective_to is None
        assert record.status == RevisionStatus.ACTIVE
        assert record.chunk_count == 150
