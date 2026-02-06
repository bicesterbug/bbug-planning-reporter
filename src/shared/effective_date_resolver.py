"""
Effective date resolution for policy revisions.

Implements [policy-knowledge-base:FR-005] - Automatic selection based on date
Implements [policy-knowledge-base:FR-009] - Get effective snapshot for date
Implements [policy-knowledge-base:NFR-002] - 100% correct revision selection
"""

from dataclasses import dataclass
from datetime import date

import structlog

from src.api.schemas.policy import PolicyCategory, PolicyRevisionSummary
from src.shared.policy_registry import PolicyRegistry

logger = structlog.get_logger(__name__)


@dataclass
class EffectivePolicyResult:
    """Result of resolving effective revision for a single policy."""

    source: str
    title: str
    category: PolicyCategory
    effective_revision: PolicyRevisionSummary | None
    reason: str | None = None  # Reason if no revision (e.g., "date_before_first_revision")


@dataclass
class EffectiveSnapshotResult:
    """Result of resolving effective revisions for all policies on a date."""

    effective_date: date
    policies: list[EffectivePolicyResult]
    policies_with_revision: list[str]  # Sources that have an effective revision
    policies_not_yet_effective: list[str]  # Sources where date is before first revision
    policies_in_gap: list[str]  # Sources where date falls in a gap between revisions


class EffectiveDateResolver:
    """
    Temporal query logic for determining which revision was in force on a given date.

    Used by PolicyKBMCP for temporal search filtering and PolicyRouter for
    effective snapshot endpoints.

    Implements:
    - [policy-knowledge-base:EffectiveDateResolver/TS-01] Single revision, date in range
    - [policy-knowledge-base:EffectiveDateResolver/TS-02] Multiple revisions, middle date
    - [policy-knowledge-base:EffectiveDateResolver/TS-03] Date before first revision
    - [policy-knowledge-base:EffectiveDateResolver/TS-04] Date on exact effective_from
    - [policy-knowledge-base:EffectiveDateResolver/TS-05] Date on effective_to
    - [policy-knowledge-base:EffectiveDateResolver/TS-06] Date in gap between revisions
    - [policy-knowledge-base:EffectiveDateResolver/TS-07] Resolve all policies for date
    - [policy-knowledge-base:EffectiveDateResolver/TS-08] Policy with no revision for date
    """

    def __init__(self, registry: PolicyRegistry) -> None:
        """
        Initialize EffectiveDateResolver.

        Args:
            registry: PolicyRegistry instance for accessing policy data.
        """
        self._registry = registry

    async def resolve_for_policy(
        self, source: str, effective_date: date
    ) -> EffectivePolicyResult | None:
        """
        Resolve the effective revision for a single policy on a given date.

        Implements [policy-knowledge-base:EffectiveDateResolver/TS-01] through [TS-06]

        The algorithm finds the revision where:
        - effective_from <= effective_date AND
        - (effective_to is None OR effective_to >= effective_date)

        Args:
            source: Policy source slug.
            effective_date: The date to resolve for.

        Returns:
            EffectivePolicyResult with the effective revision, or None if policy not found.
        """
        policy = await self._registry.get_policy(source)
        if policy is None:
            return None

        revision = await self._registry.get_effective_revision_for_date(source, effective_date)

        if revision is not None:
            return EffectivePolicyResult(
                source=policy.source,
                title=policy.title,
                category=policy.category,
                effective_revision=self._registry._revision_to_summary(revision),
            )

        # Determine why no revision was found
        reason = await self._determine_no_revision_reason(source, effective_date)

        return EffectivePolicyResult(
            source=policy.source,
            title=policy.title,
            category=policy.category,
            effective_revision=None,
            reason=reason,
        )

    async def _determine_no_revision_reason(
        self, source: str, effective_date: date
    ) -> str:
        """
        Determine why no revision was found for the given date.

        Returns:
            Reason string: "date_before_first_revision", "date_in_gap", or "no_revisions"
        """
        revisions = await self._registry.list_revisions(source)

        if not revisions:
            return "no_revisions"

        # Check if date is before the first revision
        # Revisions are sorted by effective_from DESC, so last one is earliest
        earliest_revision = revisions[-1]
        if effective_date < earliest_revision.effective_from:
            return "date_before_first_revision"

        # If we get here and no revision was found, it must be a gap
        return "date_in_gap"

    async def resolve_snapshot(self, effective_date: date) -> EffectiveSnapshotResult:
        """
        Resolve effective revisions for all policies on a given date.

        Implements [policy-knowledge-base:EffectiveDateResolver/TS-07]
        Implements [policy-knowledge-base:EffectiveDateResolver/TS-08]

        Args:
            effective_date: The date to resolve for.

        Returns:
            EffectiveSnapshotResult with effective revisions for each policy.
        """
        policies = await self._registry.list_policies()

        results: list[EffectivePolicyResult] = []
        policies_with_revision: list[str] = []
        policies_not_yet_effective: list[str] = []
        policies_in_gap: list[str] = []

        for policy_summary in policies:
            result = await self.resolve_for_policy(policy_summary.source, effective_date)
            if result is None:
                continue

            results.append(result)

            if result.effective_revision is not None:
                policies_with_revision.append(result.source)
            elif result.reason == "date_before_first_revision":
                policies_not_yet_effective.append(result.source)
            elif result.reason == "date_in_gap":
                policies_in_gap.append(result.source)

        return EffectiveSnapshotResult(
            effective_date=effective_date,
            policies=results,
            policies_with_revision=policies_with_revision,
            policies_not_yet_effective=policies_not_yet_effective,
            policies_in_gap=policies_in_gap,
        )

    async def get_revision_ids_for_date(
        self, effective_date: date, sources: list[str] | None = None
    ) -> dict[str, str | None]:
        """
        Get revision IDs for policies on a given date.

        This is a convenience method for MCP search to quickly get revision IDs
        for filtering ChromaDB queries.

        Args:
            effective_date: The date to resolve for.
            sources: Optional list of source slugs to filter. If None, resolves all.

        Returns:
            Dict mapping source slug to revision_id (or None if no revision effective).
        """
        result: dict[str, str | None] = {}

        if sources is None:
            # Resolve all policies
            snapshot = await self.resolve_snapshot(effective_date)
            for policy_result in snapshot.policies:
                if policy_result.effective_revision is not None:
                    result[policy_result.source] = policy_result.effective_revision.revision_id
                else:
                    result[policy_result.source] = None
        else:
            # Resolve only specified sources
            for source in sources:
                policy_result = await self.resolve_for_policy(source, effective_date)
                if policy_result is not None:
                    if policy_result.effective_revision is not None:
                        result[source] = policy_result.effective_revision.revision_id
                    else:
                        result[source] = None

        return result

    async def validate_revision_for_date(
        self, source: str, revision_id: str, effective_date: date
    ) -> bool:
        """
        Validate that a specific revision was in force on a given date.

        This is useful for verifying that a revision cited in a review was
        actually effective on the application's validation date.

        Args:
            source: Policy source slug.
            revision_id: Revision ID to validate.
            effective_date: The date to check.

        Returns:
            True if the revision was in force on that date, False otherwise.
        """
        revision = await self._registry.get_revision(source, revision_id)
        if revision is None:
            return False

        # Check if revision was in force on the date
        if revision.effective_from > effective_date:
            return False

        return not (revision.effective_to is not None and revision.effective_to < effective_date)
