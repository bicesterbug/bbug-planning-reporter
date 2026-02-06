"""
Tests for EffectiveDateResolver.

Implements test scenarios [policy-knowledge-base:EffectiveDateResolver/TS-01] through [TS-08]
"""

from datetime import date

import fakeredis.aioredis
import pytest

from src.api.schemas.policy import PolicyCategory, RevisionStatus
from src.shared.effective_date_resolver import EffectiveDateResolver
from src.shared.policy_registry import PolicyRegistry


@pytest.fixture
async def registry(fake_redis: fakeredis.aioredis.FakeRedis) -> PolicyRegistry:
    """Create a PolicyRegistry with fake Redis backend."""
    return PolicyRegistry(fake_redis)


@pytest.fixture
async def resolver(registry: PolicyRegistry) -> EffectiveDateResolver:
    """Create an EffectiveDateResolver with the registry."""
    return EffectiveDateResolver(registry)


class TestSingleRevisionDateInRange:
    """Test scenarios for single revision with date in range."""

    async def test_single_revision_date_in_range(
        self, registry: PolicyRegistry, resolver: EffectiveDateResolver
    ) -> None:
        """
        Verifies [policy-knowledge-base:EffectiveDateResolver/TS-01]

        Given: Rev A: 2020-07-27 to None
        When: Resolve 2024-01-01
        Then: Returns Rev A
        """
        await registry.create_policy(
            source="LTN_1_20",
            title="Cycle Infrastructure Design",
            category=PolicyCategory.NATIONAL_GUIDANCE,
        )
        await registry.create_revision(
            source="LTN_1_20",
            revision_id="rev_LTN120_2020_07",
            version_label="July 2020",
            effective_from=date(2020, 7, 27),
            effective_to=None,
        )
        await registry.update_revision(
            "LTN_1_20", "rev_LTN120_2020_07", status=RevisionStatus.ACTIVE
        )

        result = await resolver.resolve_for_policy("LTN_1_20", date(2024, 1, 1))

        assert result is not None
        assert result.source == "LTN_1_20"
        assert result.effective_revision is not None
        assert result.effective_revision.revision_id == "rev_LTN120_2020_07"
        assert result.reason is None


class TestMultipleRevisionsMiddleDate:
    """Test scenarios for multiple revisions with date in middle."""

    async def test_multiple_revisions_middle_date(
        self, registry: PolicyRegistry, resolver: EffectiveDateResolver
    ) -> None:
        """
        Verifies [policy-knowledge-base:EffectiveDateResolver/TS-02]

        Given: Rev A: 2020-07-27 to 2023-09-04,
               Rev B: 2023-09-05 to 2024-12-11,
               Rev C: 2024-12-12 to None
        When: Resolve 2024-03-15
        Then: Returns Rev B
        """
        await registry.create_policy(
            source="NPPF",
            title="NPPF",
            category=PolicyCategory.NATIONAL_POLICY,
        )

        # Create Rev A
        await registry.create_revision(
            source="NPPF",
            revision_id="rev_NPPF_2020_07",
            version_label="July 2020",
            effective_from=date(2020, 7, 27),
            effective_to=date(2023, 9, 4),
        )
        await registry.update_revision(
            "NPPF", "rev_NPPF_2020_07", status=RevisionStatus.SUPERSEDED
        )

        # Create Rev B
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

        # Create Rev C
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

        result = await resolver.resolve_for_policy("NPPF", date(2024, 3, 15))

        assert result is not None
        assert result.effective_revision is not None
        assert result.effective_revision.revision_id == "rev_NPPF_2023_09"
        assert result.effective_revision.version_label == "September 2023"


class TestDateBeforeFirstRevision:
    """Test scenarios for date before first revision."""

    async def test_date_before_first_revision(
        self, registry: PolicyRegistry, resolver: EffectiveDateResolver
    ) -> None:
        """
        Verifies [policy-knowledge-base:EffectiveDateResolver/TS-03]

        Given: Rev A: 2020-07-27
        When: Resolve 2019-01-01
        Then: Returns None with reason "date_before_first_revision"
        """
        await registry.create_policy(
            source="LTN_1_20",
            title="LTN 1/20",
            category=PolicyCategory.NATIONAL_GUIDANCE,
        )
        await registry.create_revision(
            source="LTN_1_20",
            revision_id="rev_LTN120_2020_07",
            version_label="July 2020",
            effective_from=date(2020, 7, 27),
            effective_to=None,
        )
        await registry.update_revision(
            "LTN_1_20", "rev_LTN120_2020_07", status=RevisionStatus.ACTIVE
        )

        result = await resolver.resolve_for_policy("LTN_1_20", date(2019, 1, 1))

        assert result is not None
        assert result.effective_revision is None
        assert result.reason == "date_before_first_revision"


class TestDateOnExactEffectiveFrom:
    """Test scenarios for date exactly on effective_from."""

    async def test_date_on_exact_effective_from(
        self, registry: PolicyRegistry, resolver: EffectiveDateResolver
    ) -> None:
        """
        Verifies [policy-knowledge-base:EffectiveDateResolver/TS-04]

        Given: Rev A: 2020-07-27
        When: Resolve 2020-07-27
        Then: Returns Rev A
        """
        await registry.create_policy(
            source="LTN_1_20",
            title="LTN 1/20",
            category=PolicyCategory.NATIONAL_GUIDANCE,
        )
        await registry.create_revision(
            source="LTN_1_20",
            revision_id="rev_LTN120_2020_07",
            version_label="July 2020",
            effective_from=date(2020, 7, 27),
            effective_to=None,
        )
        await registry.update_revision(
            "LTN_1_20", "rev_LTN120_2020_07", status=RevisionStatus.ACTIVE
        )

        result = await resolver.resolve_for_policy("LTN_1_20", date(2020, 7, 27))

        assert result is not None
        assert result.effective_revision is not None
        assert result.effective_revision.revision_id == "rev_LTN120_2020_07"


class TestDateOnEffectiveTo:
    """Test scenarios for date exactly on effective_to."""

    async def test_date_on_effective_to(
        self, registry: PolicyRegistry, resolver: EffectiveDateResolver
    ) -> None:
        """
        Verifies [policy-knowledge-base:EffectiveDateResolver/TS-05]

        Given: Rev A: 2020-07-27 to 2023-09-04
        When: Resolve 2023-09-04
        Then: Returns Rev A
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
            effective_to=date(2023, 9, 4),
        )
        await registry.update_revision(
            "NPPF", "rev_NPPF_2020_07", status=RevisionStatus.SUPERSEDED
        )

        result = await resolver.resolve_for_policy("NPPF", date(2023, 9, 4))

        assert result is not None
        assert result.effective_revision is not None
        assert result.effective_revision.revision_id == "rev_NPPF_2020_07"


class TestDateInGapBetweenRevisions:
    """Test scenarios for date in gap between revisions."""

    async def test_date_in_gap_between_revisions(
        self, registry: PolicyRegistry, resolver: EffectiveDateResolver
    ) -> None:
        """
        Verifies [policy-knowledge-base:EffectiveDateResolver/TS-06]

        Given: Rev A: 2020-01-01 to 2020-06-30, Rev B: 2020-08-01 to None
        When: Resolve 2020-07-15
        Then: Returns None with reason "date_in_gap"
        """
        await registry.create_policy(
            source="TEST_POLICY",
            title="Test Policy",
            category=PolicyCategory.NATIONAL_POLICY,
        )

        # Rev A with gap before Rev B
        await registry.create_revision(
            source="TEST_POLICY",
            revision_id="rev_A",
            version_label="A",
            effective_from=date(2020, 1, 1),
            effective_to=date(2020, 6, 30),
        )
        await registry.update_revision(
            "TEST_POLICY", "rev_A", status=RevisionStatus.SUPERSEDED
        )

        # Rev B starts after a gap
        await registry.create_revision(
            source="TEST_POLICY",
            revision_id="rev_B",
            version_label="B",
            effective_from=date(2020, 8, 1),
            effective_to=None,
        )
        await registry.update_revision(
            "TEST_POLICY", "rev_B", status=RevisionStatus.ACTIVE
        )

        result = await resolver.resolve_for_policy("TEST_POLICY", date(2020, 7, 15))

        assert result is not None
        assert result.effective_revision is None
        assert result.reason == "date_in_gap"


class TestResolveAllPoliciesForDate:
    """Test scenarios for resolving all policies for a date."""

    async def test_resolve_all_policies_for_date(
        self, registry: PolicyRegistry, resolver: EffectiveDateResolver
    ) -> None:
        """
        Verifies [policy-knowledge-base:EffectiveDateResolver/TS-07]

        Given: 5 policies with various revisions
        When: Get snapshot for 2024-03-15
        Then: Returns correct revision for each
        """
        # Policy 1: NPPF with revision effective from 2023
        await registry.create_policy(
            source="NPPF",
            title="NPPF",
            category=PolicyCategory.NATIONAL_POLICY,
        )
        await registry.create_revision(
            source="NPPF",
            revision_id="rev_NPPF_2023",
            version_label="2023",
            effective_from=date(2023, 9, 5),
            effective_to=None,
        )
        await registry.update_revision("NPPF", "rev_NPPF_2023", status=RevisionStatus.ACTIVE)

        # Policy 2: LTN_1_20 with revision effective from 2020
        await registry.create_policy(
            source="LTN_1_20",
            title="LTN 1/20",
            category=PolicyCategory.NATIONAL_GUIDANCE,
        )
        await registry.create_revision(
            source="LTN_1_20",
            revision_id="rev_LTN120_2020",
            version_label="2020",
            effective_from=date(2020, 7, 27),
            effective_to=None,
        )
        await registry.update_revision("LTN_1_20", "rev_LTN120_2020", status=RevisionStatus.ACTIVE)

        # Policy 3: MFS with revision effective from 2007
        await registry.create_policy(
            source="MFS",
            title="Manual for Streets",
            category=PolicyCategory.NATIONAL_GUIDANCE,
        )
        await registry.create_revision(
            source="MFS",
            revision_id="rev_MFS_2007",
            version_label="2007",
            effective_from=date(2007, 3, 29),
            effective_to=None,
        )
        await registry.update_revision("MFS", "rev_MFS_2007", status=RevisionStatus.ACTIVE)

        # Policy 4: LOCAL_PLAN with no revision yet effective for test date
        await registry.create_policy(
            source="LOCAL_PLAN_2025",
            title="Local Plan 2025",
            category=PolicyCategory.LOCAL_PLAN,
        )
        await registry.create_revision(
            source="LOCAL_PLAN_2025",
            revision_id="rev_LP_2025",
            version_label="2025",
            effective_from=date(2025, 1, 1),  # After test date
            effective_to=None,
        )
        await registry.update_revision("LOCAL_PLAN_2025", "rev_LP_2025", status=RevisionStatus.ACTIVE)

        # Policy 5: LCWIP with revision in a gap
        await registry.create_policy(
            source="LCWIP",
            title="LCWIP",
            category=PolicyCategory.COUNTY_STRATEGY,
        )
        await registry.create_revision(
            source="LCWIP",
            revision_id="rev_LCWIP_2020",
            version_label="2020",
            effective_from=date(2020, 1, 1),
            effective_to=date(2023, 12, 31),
        )
        await registry.update_revision("LCWIP", "rev_LCWIP_2020", status=RevisionStatus.SUPERSEDED)
        await registry.create_revision(
            source="LCWIP",
            revision_id="rev_LCWIP_2025",
            version_label="2025",
            effective_from=date(2025, 1, 1),
            effective_to=None,
        )
        await registry.update_revision("LCWIP", "rev_LCWIP_2025", status=RevisionStatus.ACTIVE)

        # Resolve for 2024-03-15
        snapshot = await resolver.resolve_snapshot(date(2024, 3, 15))

        assert snapshot.effective_date == date(2024, 3, 15)
        assert len(snapshot.policies) == 5

        # Check policies_with_revision
        assert "NPPF" in snapshot.policies_with_revision
        assert "LTN_1_20" in snapshot.policies_with_revision
        assert "MFS" in snapshot.policies_with_revision
        assert len(snapshot.policies_with_revision) == 3

        # Check policies_not_yet_effective
        assert "LOCAL_PLAN_2025" in snapshot.policies_not_yet_effective

        # Check policies_in_gap
        assert "LCWIP" in snapshot.policies_in_gap

        # Verify specific revisions
        nppf_result = next(p for p in snapshot.policies if p.source == "NPPF")
        assert nppf_result.effective_revision is not None
        assert nppf_result.effective_revision.revision_id == "rev_NPPF_2023"


class TestPolicyWithNoRevisionForDate:
    """Test scenarios for policy with no revision for date."""

    async def test_policy_with_no_revisions(
        self, registry: PolicyRegistry, resolver: EffectiveDateResolver
    ) -> None:
        """
        Verifies [policy-knowledge-base:EffectiveDateResolver/TS-08]

        Given: Policy created but no revisions at all
        When: Resolve 2024-01-01
        Then: Returns None with reason "no_revisions"
        """
        await registry.create_policy(
            source="EMPTY_POLICY",
            title="Empty Policy",
            category=PolicyCategory.LOCAL_PLAN,
        )

        result = await resolver.resolve_for_policy("EMPTY_POLICY", date(2024, 1, 1))

        assert result is not None
        assert result.effective_revision is None
        assert result.reason == "no_revisions"


class TestGetRevisionIdsForDate:
    """Test scenarios for getting revision IDs for date."""

    async def test_get_revision_ids_for_all_policies(
        self, registry: PolicyRegistry, resolver: EffectiveDateResolver
    ) -> None:
        """Test getting revision IDs for all policies."""
        await registry.create_policy(
            source="NPPF",
            title="NPPF",
            category=PolicyCategory.NATIONAL_POLICY,
        )
        await registry.create_revision(
            source="NPPF",
            revision_id="rev_NPPF_2023",
            version_label="2023",
            effective_from=date(2023, 9, 5),
            effective_to=None,
        )
        await registry.update_revision("NPPF", "rev_NPPF_2023", status=RevisionStatus.ACTIVE)

        await registry.create_policy(
            source="LTN_1_20",
            title="LTN 1/20",
            category=PolicyCategory.NATIONAL_GUIDANCE,
        )
        await registry.create_revision(
            source="LTN_1_20",
            revision_id="rev_LTN120_2020",
            version_label="2020",
            effective_from=date(2020, 7, 27),
            effective_to=None,
        )
        await registry.update_revision("LTN_1_20", "rev_LTN120_2020", status=RevisionStatus.ACTIVE)

        result = await resolver.get_revision_ids_for_date(date(2024, 3, 15))

        assert result["NPPF"] == "rev_NPPF_2023"
        assert result["LTN_1_20"] == "rev_LTN120_2020"

    async def test_get_revision_ids_for_specific_sources(
        self, registry: PolicyRegistry, resolver: EffectiveDateResolver
    ) -> None:
        """Test getting revision IDs for specific sources."""
        await registry.create_policy(
            source="NPPF",
            title="NPPF",
            category=PolicyCategory.NATIONAL_POLICY,
        )
        await registry.create_revision(
            source="NPPF",
            revision_id="rev_NPPF_2023",
            version_label="2023",
            effective_from=date(2023, 9, 5),
            effective_to=None,
        )
        await registry.update_revision("NPPF", "rev_NPPF_2023", status=RevisionStatus.ACTIVE)

        await registry.create_policy(
            source="LTN_1_20",
            title="LTN 1/20",
            category=PolicyCategory.NATIONAL_GUIDANCE,
        )
        await registry.create_revision(
            source="LTN_1_20",
            revision_id="rev_LTN120_2020",
            version_label="2020",
            effective_from=date(2020, 7, 27),
            effective_to=None,
        )
        await registry.update_revision("LTN_1_20", "rev_LTN120_2020", status=RevisionStatus.ACTIVE)

        result = await resolver.get_revision_ids_for_date(
            date(2024, 3, 15), sources=["NPPF"]
        )

        assert "NPPF" in result
        assert result["NPPF"] == "rev_NPPF_2023"
        assert "LTN_1_20" not in result


class TestValidateRevisionForDate:
    """Test scenarios for validating revision for date."""

    async def test_validate_revision_in_range(
        self, registry: PolicyRegistry, resolver: EffectiveDateResolver
    ) -> None:
        """Test validating a revision that was in force on the date."""
        await registry.create_policy(
            source="NPPF",
            title="NPPF",
            category=PolicyCategory.NATIONAL_POLICY,
        )
        await registry.create_revision(
            source="NPPF",
            revision_id="rev_NPPF_2023",
            version_label="2023",
            effective_from=date(2023, 9, 5),
            effective_to=date(2024, 12, 11),
        )

        result = await resolver.validate_revision_for_date(
            "NPPF", "rev_NPPF_2023", date(2024, 3, 15)
        )

        assert result is True

    async def test_validate_revision_before_effective_from(
        self, registry: PolicyRegistry, resolver: EffectiveDateResolver
    ) -> None:
        """Test validating a revision before its effective_from date."""
        await registry.create_policy(
            source="NPPF",
            title="NPPF",
            category=PolicyCategory.NATIONAL_POLICY,
        )
        await registry.create_revision(
            source="NPPF",
            revision_id="rev_NPPF_2024",
            version_label="2024",
            effective_from=date(2024, 12, 12),
            effective_to=None,
        )

        result = await resolver.validate_revision_for_date(
            "NPPF", "rev_NPPF_2024", date(2024, 3, 15)
        )

        assert result is False

    async def test_validate_revision_after_effective_to(
        self, registry: PolicyRegistry, resolver: EffectiveDateResolver
    ) -> None:
        """Test validating a revision after its effective_to date."""
        await registry.create_policy(
            source="NPPF",
            title="NPPF",
            category=PolicyCategory.NATIONAL_POLICY,
        )
        await registry.create_revision(
            source="NPPF",
            revision_id="rev_NPPF_2021",
            version_label="2021",
            effective_from=date(2021, 7, 20),
            effective_to=date(2023, 9, 4),
        )

        result = await resolver.validate_revision_for_date(
            "NPPF", "rev_NPPF_2021", date(2024, 3, 15)
        )

        assert result is False

    async def test_validate_nonexistent_revision(
        self, resolver: EffectiveDateResolver
    ) -> None:
        """Test validating a nonexistent revision."""
        result = await resolver.validate_revision_for_date(
            "NPPF", "nonexistent", date(2024, 3, 15)
        )

        assert result is False


class TestPolicyNotFound:
    """Test scenarios for policy not found."""

    async def test_resolve_nonexistent_policy(
        self, resolver: EffectiveDateResolver
    ) -> None:
        """Test resolving for a policy that doesn't exist."""
        result = await resolver.resolve_for_policy("NONEXISTENT", date(2024, 1, 1))

        assert result is None
