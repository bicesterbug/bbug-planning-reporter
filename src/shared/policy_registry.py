"""
Redis-backed registry for policy documents and revisions.

Implements [policy-knowledge-base:FR-001] - Create policy document entry
Implements [policy-knowledge-base:FR-002] - Store revision metadata
Implements [policy-knowledge-base:FR-007] - List all policies with current revision
Implements [policy-knowledge-base:FR-008] - List revisions for a policy
Implements [policy-knowledge-base:FR-010] - Update revision metadata
Implements [policy-knowledge-base:FR-014] - Redis as source of truth
"""

from datetime import date, datetime, timedelta

import redis.asyncio as redis
import structlog

from src.api.schemas.policy import (
    PolicyCategory,
    PolicyDocumentDetail,
    PolicyDocumentRecord,
    PolicyDocumentSummary,
    PolicyRevisionRecord,
    PolicyRevisionSummary,
    RevisionStatus,
)

logger = structlog.get_logger(__name__)


class PolicyRegistryError(Exception):
    """Base exception for policy registry errors."""

    pass


class PolicyAlreadyExistsError(PolicyRegistryError):
    """Raised when attempting to create a policy that already exists."""

    def __init__(self, source: str) -> None:
        self.source = source
        super().__init__(f"Policy already exists: {source}")


class PolicyNotFoundError(PolicyRegistryError):
    """Raised when a policy is not found."""

    def __init__(self, source: str) -> None:
        self.source = source
        super().__init__(f"Policy not found: {source}")


class RevisionNotFoundError(PolicyRegistryError):
    """Raised when a revision is not found."""

    def __init__(self, source: str, revision_id: str) -> None:
        self.source = source
        self.revision_id = revision_id
        super().__init__(f"Revision not found: {revision_id} for policy {source}")


class RevisionOverlapError(PolicyRegistryError):
    """Raised when a revision's effective dates overlap with an existing revision."""

    def __init__(
        self, source: str, effective_from: date, effective_to: date | None, conflicting_id: str
    ) -> None:
        self.source = source
        self.effective_from = effective_from
        self.effective_to = effective_to
        self.conflicting_id = conflicting_id
        super().__init__(
            f"Revision dates overlap with existing revision {conflicting_id} "
            f"(from {effective_from} to {effective_to})"
        )


class CannotDeleteSoleRevisionError(PolicyRegistryError):
    """Raised when attempting to delete the only active revision of a policy."""

    def __init__(self, source: str, revision_id: str) -> None:
        self.source = source
        self.revision_id = revision_id
        super().__init__(
            f"Cannot delete sole active revision {revision_id} for policy {source}"
        )


class PolicyRegistry:
    """
    Redis-backed registry for policy documents and revisions.

    Provides CRUD operations, effective date resolution, and consistency management.
    Acts as the source of truth for policy metadata.

    Redis Key Schema:
    - policy:{source} - Hash for policy document metadata
    - policy_revision:{source}:{revision_id} - Hash for revision metadata
    - policy_revisions:{source} - Sorted Set for revision IDs sorted by effective_from
    - policies_all - Set of all source slugs

    Implements:
    - [policy-knowledge-base:PolicyRegistry/TS-01] Create policy document
    - [policy-knowledge-base:PolicyRegistry/TS-02] Duplicate policy prevention
    - [policy-knowledge-base:PolicyRegistry/TS-03] Create revision
    - [policy-knowledge-base:PolicyRegistry/TS-04] Overlapping dates rejected
    - [policy-knowledge-base:PolicyRegistry/TS-05] Get policy with revisions
    - [policy-knowledge-base:PolicyRegistry/TS-06] List all policies
    - [policy-knowledge-base:PolicyRegistry/TS-07] Update revision metadata
    - [policy-knowledge-base:PolicyRegistry/TS-08] Delete revision
    - [policy-knowledge-base:PolicyRegistry/TS-09] Cannot delete sole active revision
    - [policy-knowledge-base:PolicyRegistry/TS-12] Auto-set previous revision effective_to
    """

    def __init__(self, redis_client: redis.Redis) -> None:
        """
        Initialize PolicyRegistry.

        Args:
            redis_client: Async Redis client instance.
        """
        self._client = redis_client

    # =========================================================================
    # Key Generation
    # =========================================================================

    def _policy_key(self, source: str) -> str:
        """Get Redis key for a policy document."""
        return f"policy:{source}"

    def _revision_key(self, source: str, revision_id: str) -> str:
        """Get Redis key for a revision."""
        return f"policy_revision:{source}:{revision_id}"

    def _revisions_set_key(self, source: str) -> str:
        """Get Redis key for a policy's revisions sorted set."""
        return f"policy_revisions:{source}"

    def _policies_all_key(self) -> str:
        """Get Redis key for the set of all policy source slugs."""
        return "policies_all"

    # =========================================================================
    # Serialization Helpers
    # =========================================================================

    def _serialize_policy(self, record: PolicyDocumentRecord) -> dict[str, str]:
        """Serialize policy record to Redis hash."""
        return {
            "source": record.source,
            "title": record.title,
            "description": record.description or "",
            "category": record.category.value,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat() if record.updated_at else "",
        }

    def _deserialize_policy(self, data: dict[str, str]) -> PolicyDocumentRecord:
        """Deserialize Redis hash to policy record."""
        return PolicyDocumentRecord(
            source=data["source"],
            title=data["title"],
            description=data["description"] or None,
            category=PolicyCategory(data["category"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else None,
        )

    def _serialize_revision(self, record: PolicyRevisionRecord) -> dict[str, str]:
        """Serialize revision record to Redis hash."""
        return {
            "revision_id": record.revision_id,
            "source": record.source,
            "version_label": record.version_label,
            "effective_from": record.effective_from.isoformat(),
            "effective_to": record.effective_to.isoformat() if record.effective_to else "",
            "status": record.status.value,
            "file_path": record.file_path or "",
            "file_size_bytes": str(record.file_size_bytes) if record.file_size_bytes else "",
            "page_count": str(record.page_count) if record.page_count else "",
            "chunk_count": str(record.chunk_count) if record.chunk_count else "",
            "notes": record.notes or "",
            "created_at": record.created_at.isoformat(),
            "ingested_at": record.ingested_at.isoformat() if record.ingested_at else "",
            "error": record.error or "",
        }

    def _deserialize_revision(self, data: dict[str, str]) -> PolicyRevisionRecord:
        """Deserialize Redis hash to revision record."""
        return PolicyRevisionRecord(
            revision_id=data["revision_id"],
            source=data["source"],
            version_label=data["version_label"],
            effective_from=date.fromisoformat(data["effective_from"]),
            effective_to=date.fromisoformat(data["effective_to"]) if data.get("effective_to") else None,
            status=RevisionStatus(data["status"]),
            file_path=data["file_path"] or None,
            file_size_bytes=int(data["file_size_bytes"]) if data.get("file_size_bytes") else None,
            page_count=int(data["page_count"]) if data.get("page_count") else None,
            chunk_count=int(data["chunk_count"]) if data.get("chunk_count") else None,
            notes=data["notes"] or None,
            created_at=datetime.fromisoformat(data["created_at"]),
            ingested_at=datetime.fromisoformat(data["ingested_at"]) if data.get("ingested_at") else None,
            error=data["error"] or None,
        )

    def _revision_to_summary(self, record: PolicyRevisionRecord) -> PolicyRevisionSummary:
        """Convert revision record to summary."""
        return PolicyRevisionSummary(
            revision_id=record.revision_id,
            version_label=record.version_label,
            effective_from=record.effective_from,
            effective_to=record.effective_to,
            status=record.status,
            chunk_count=record.chunk_count,
            ingested_at=record.ingested_at,
        )

    # =========================================================================
    # Policy Operations
    # =========================================================================

    async def create_policy(
        self,
        source: str,
        title: str,
        category: PolicyCategory,
        description: str | None = None,
    ) -> PolicyDocumentRecord:
        """
        Create a new policy document entry.

        Implements [policy-knowledge-base:PolicyRegistry/TS-01]
        Implements [policy-knowledge-base:PolicyRegistry/TS-02]

        Args:
            source: Unique source slug (e.g., "LTN_1_20").
            title: Human-readable title.
            category: Policy category.
            description: Optional description.

        Returns:
            Created policy record.

        Raises:
            PolicyAlreadyExistsError: If policy with source already exists.
        """
        # Check for duplicate
        exists = await self._client.sismember(self._policies_all_key(), source)
        if exists:
            raise PolicyAlreadyExistsError(source)

        record = PolicyDocumentRecord(
            source=source,
            title=title,
            description=description,
            category=category,
            created_at=datetime.utcnow(),
        )

        async with self._client.pipeline() as pipe:
            # Store policy hash
            await pipe.hset(self._policy_key(source), mapping=self._serialize_policy(record))
            # Add to policies_all set
            await pipe.sadd(self._policies_all_key(), source)
            await pipe.execute()

        logger.info(
            "Policy created",
            source=source,
            title=title,
            category=category.value,
        )
        return record

    async def get_policy(self, source: str) -> PolicyDocumentRecord | None:
        """
        Get a policy document by source slug.

        Args:
            source: Policy source slug.

        Returns:
            Policy record if found, None otherwise.
        """
        data = await self._client.hgetall(self._policy_key(source))
        if not data:
            return None
        return self._deserialize_policy(data)

    async def policy_exists(self, source: str) -> bool:
        """
        Check if a policy exists.

        Args:
            source: Policy source slug.

        Returns:
            True if policy exists, False otherwise.
        """
        return await self._client.sismember(self._policies_all_key(), source)

    async def update_policy(
        self,
        source: str,
        title: str | None = None,
        description: str | None = None,
        category: PolicyCategory | None = None,
    ) -> PolicyDocumentRecord:
        """
        Update policy document metadata.

        Args:
            source: Policy source slug.
            title: New title (optional).
            description: New description (optional).
            category: New category (optional).

        Returns:
            Updated policy record.

        Raises:
            PolicyNotFoundError: If policy not found.
        """
        record = await self.get_policy(source)
        if record is None:
            raise PolicyNotFoundError(source)

        if title is not None:
            record.title = title
        if description is not None:
            record.description = description
        if category is not None:
            record.category = category
        record.updated_at = datetime.utcnow()

        await self._client.hset(
            self._policy_key(source), mapping=self._serialize_policy(record)
        )

        logger.debug("Policy updated", source=source)
        return record

    async def get_policy_with_revisions(self, source: str) -> PolicyDocumentDetail | None:
        """
        Get policy with all revisions.

        Implements [policy-knowledge-base:PolicyRegistry/TS-05]

        Args:
            source: Policy source slug.

        Returns:
            Policy detail with revisions if found, None otherwise.
        """
        policy = await self.get_policy(source)
        if policy is None:
            return None

        revisions = await self.list_revisions(source)
        current_revision = await self.get_current_revision(source)

        return PolicyDocumentDetail(
            source=policy.source,
            title=policy.title,
            description=policy.description,
            category=policy.category,
            revisions=revisions,
            current_revision=current_revision,
            revision_count=len(revisions),
            created_at=policy.created_at,
            updated_at=policy.updated_at,
        )

    async def list_policies(
        self,
        category: PolicyCategory | None = None,
        source_filter: str | None = None,
    ) -> list[PolicyDocumentSummary]:
        """
        List all policies with current revision info.

        Implements [policy-knowledge-base:PolicyRegistry/TS-06]

        Args:
            category: Optional category filter.
            source_filter: Optional source slug filter (exact match).

        Returns:
            List of policy summaries.
        """
        sources = await self._client.smembers(self._policies_all_key())
        if not sources:
            return []

        summaries = []
        for source in sorted(sources):
            if source_filter and source != source_filter:
                continue

            policy = await self.get_policy(source)
            if policy is None:
                continue

            if category and policy.category != category:
                continue

            current_revision = await self.get_current_revision(source)
            revision_count = await self._client.zcard(self._revisions_set_key(source))

            summaries.append(
                PolicyDocumentSummary(
                    source=policy.source,
                    title=policy.title,
                    category=policy.category,
                    current_revision=current_revision,
                    revision_count=revision_count,
                )
            )

        return summaries

    async def delete_policy(self, source: str) -> bool:
        """
        Delete a policy and all its revisions.

        Args:
            source: Policy source slug.

        Returns:
            True if deleted, False if not found.
        """
        if not await self.policy_exists(source):
            return False

        # Get all revision IDs
        revision_ids = await self._client.zrange(self._revisions_set_key(source), 0, -1)

        async with self._client.pipeline() as pipe:
            # Delete policy hash
            await pipe.delete(self._policy_key(source))
            # Delete all revision hashes
            for rev_id in revision_ids:
                await pipe.delete(self._revision_key(source, rev_id))
            # Delete revisions sorted set
            await pipe.delete(self._revisions_set_key(source))
            # Remove from policies_all
            await pipe.srem(self._policies_all_key(), source)
            await pipe.execute()

        logger.info("Policy deleted", source=source, revisions_deleted=len(revision_ids))
        return True

    # =========================================================================
    # Revision Operations
    # =========================================================================

    async def create_revision(
        self,
        source: str,
        revision_id: str,
        version_label: str,
        effective_from: date,
        effective_to: date | None = None,
        file_path: str | None = None,
        file_size_bytes: int | None = None,
        page_count: int | None = None,
        notes: str | None = None,
    ) -> PolicyRevisionRecord:
        """
        Create a new policy revision.

        Implements [policy-knowledge-base:PolicyRegistry/TS-03]
        Implements [policy-knowledge-base:PolicyRegistry/TS-04]
        Implements [policy-knowledge-base:PolicyRegistry/TS-12]

        Args:
            source: Policy source slug.
            revision_id: Unique revision ID.
            version_label: Human-readable version.
            effective_from: Date from which revision is in force.
            effective_to: Optional end date.
            file_path: Path to source PDF.
            file_size_bytes: File size in bytes.
            page_count: Number of pages.
            notes: Optional notes.

        Returns:
            Created revision record.

        Raises:
            PolicyNotFoundError: If policy not found.
            RevisionOverlapError: If dates overlap with existing revision.
        """
        if not await self.policy_exists(source):
            raise PolicyNotFoundError(source)

        # Check for overlap and handle auto-supersession
        await self._check_and_handle_overlap(source, effective_from, effective_to)

        record = PolicyRevisionRecord(
            revision_id=revision_id,
            source=source,
            version_label=version_label,
            effective_from=effective_from,
            effective_to=effective_to,
            status=RevisionStatus.PROCESSING,
            file_path=file_path,
            file_size_bytes=file_size_bytes,
            page_count=page_count,
            notes=notes,
            created_at=datetime.utcnow(),
        )

        # Use effective_from as score for sorted set (as days since epoch)
        score = (effective_from - date(1970, 1, 1)).days

        async with self._client.pipeline() as pipe:
            # Store revision hash
            await pipe.hset(
                self._revision_key(source, revision_id),
                mapping=self._serialize_revision(record),
            )
            # Add to revisions sorted set
            await pipe.zadd(self._revisions_set_key(source), {revision_id: score})
            await pipe.execute()

        logger.info(
            "Revision created",
            source=source,
            revision_id=revision_id,
            version_label=version_label,
            effective_from=effective_from.isoformat(),
        )
        return record

    async def _check_and_handle_overlap(
        self, source: str, effective_from: date, effective_to: date | None
    ) -> None:
        """
        Check for date overlap and handle auto-supersession.

        Implements auto-supersession: if a new revision starts after an existing
        revision with no effective_to, the existing revision's effective_to is
        set to the day before the new revision's effective_from.

        Args:
            source: Policy source slug.
            effective_from: New revision's effective_from.
            effective_to: New revision's effective_to.

        Raises:
            RevisionOverlapError: If overlap is detected that can't be auto-resolved.
        """
        revisions = await self._get_all_revisions(source)

        for rev in revisions:
            # Skip failed revisions in overlap check
            if rev.status == RevisionStatus.FAILED:
                continue

            # Determine the effective ranges
            rev_start = rev.effective_from
            rev_end = rev.effective_to

            # Check for overlap
            # New revision overlaps if:
            # - new_from <= rev_end (or rev_end is None) AND
            # - new_end >= rev_start (or new_end is None)

            # Case 1: Existing revision has no end (currently in force)
            if rev_end is None:
                # If new revision has a defined end before the existing revision starts,
                # there's no overlap (inserting a historical revision)
                if effective_to is not None and effective_to < rev_start:
                    continue

                # New revision starts on or before existing revision
                if effective_from <= rev_start:
                    # This is an overlap error - can't insert before an open-ended revision
                    raise RevisionOverlapError(source, effective_from, effective_to, rev.revision_id)

                # New revision starts after existing - auto-supersession
                # Set existing revision's effective_to to day before new effective_from
                new_effective_to = effective_from - timedelta(days=1)
                await self._update_revision_effective_to(source, rev.revision_id, new_effective_to)
                logger.info(
                    "Auto-supersession applied",
                    source=source,
                    superseded_revision=rev.revision_id,
                    new_effective_to=new_effective_to.isoformat(),
                )
                continue

            # Case 2: Both revisions have defined ranges
            # Check for overlap
            new_end = effective_to or date(9999, 12, 31)  # Treat None as far future

            if effective_from <= rev_end and new_end >= rev_start:
                raise RevisionOverlapError(source, effective_from, effective_to, rev.revision_id)

    async def _update_revision_effective_to(
        self, source: str, revision_id: str, effective_to: date
    ) -> None:
        """Update a revision's effective_to date."""
        revision = await self.get_revision(source, revision_id)
        if revision is None:
            return

        revision.effective_to = effective_to
        await self._client.hset(
            self._revision_key(source, revision_id),
            mapping=self._serialize_revision(revision),
        )

    async def _get_all_revisions(self, source: str) -> list[PolicyRevisionRecord]:
        """Get all revisions for a policy."""
        revision_ids = await self._client.zrange(self._revisions_set_key(source), 0, -1)
        revisions = []
        for rev_id in revision_ids:
            rev = await self.get_revision(source, rev_id)
            if rev:
                revisions.append(rev)
        return revisions

    async def get_revision(self, source: str, revision_id: str) -> PolicyRevisionRecord | None:
        """
        Get a revision by ID.

        Args:
            source: Policy source slug.
            revision_id: Revision ID.

        Returns:
            Revision record if found, None otherwise.
        """
        data = await self._client.hgetall(self._revision_key(source, revision_id))
        if not data:
            return None
        return self._deserialize_revision(data)

    async def list_revisions(self, source: str) -> list[PolicyRevisionSummary]:
        """
        List all revisions for a policy.

        Implements [policy-knowledge-base:FR-008]

        Args:
            source: Policy source slug.

        Returns:
            List of revision summaries ordered by effective_from DESC.
        """
        # Get revision IDs in reverse order (newest first)
        revision_ids = await self._client.zrevrange(self._revisions_set_key(source), 0, -1)

        summaries = []
        for rev_id in revision_ids:
            rev = await self.get_revision(source, rev_id)
            if rev:
                summaries.append(self._revision_to_summary(rev))

        return summaries

    async def get_current_revision(self, source: str) -> PolicyRevisionSummary | None:
        """
        Get the currently active revision (no effective_to or effective_to in future).

        Args:
            source: Policy source slug.

        Returns:
            Current revision summary if one exists, None otherwise.
        """
        today = date.today()
        revisions = await self._get_all_revisions(source)

        for rev in revisions:
            # Skip non-active revisions
            if rev.status != RevisionStatus.ACTIVE:
                continue

            # Check if revision is currently in force
            if rev.effective_from <= today and (rev.effective_to is None or rev.effective_to >= today):
                return self._revision_to_summary(rev)

        return None

    async def update_revision(
        self,
        source: str,
        revision_id: str,
        version_label: str | None = None,
        effective_from: date | None = None,
        effective_to: date | None = None,
        notes: str | None = None,
        status: RevisionStatus | None = None,
        chunk_count: int | None = None,
        ingested_at: datetime | None = None,
        error: str | None = None,
    ) -> PolicyRevisionRecord:
        """
        Update revision metadata.

        Implements [policy-knowledge-base:PolicyRegistry/TS-07]

        Args:
            source: Policy source slug.
            revision_id: Revision ID.
            version_label: New version label (optional).
            effective_from: New effective_from date (optional).
            effective_to: New effective_to date (optional).
            notes: New notes (optional).
            status: New status (optional).
            chunk_count: Updated chunk count (optional).
            ingested_at: Updated ingestion timestamp (optional).
            error: Error message (optional).

        Returns:
            Updated revision record.

        Raises:
            RevisionNotFoundError: If revision not found.
        """
        revision = await self.get_revision(source, revision_id)
        if revision is None:
            raise RevisionNotFoundError(source, revision_id)

        if version_label is not None:
            revision.version_label = version_label
        if effective_from is not None:
            revision.effective_from = effective_from
        if effective_to is not None:
            revision.effective_to = effective_to
        if notes is not None:
            revision.notes = notes
        if status is not None:
            revision.status = status
        if chunk_count is not None:
            revision.chunk_count = chunk_count
        if ingested_at is not None:
            revision.ingested_at = ingested_at
        if error is not None:
            revision.error = error

        await self._client.hset(
            self._revision_key(source, revision_id),
            mapping=self._serialize_revision(revision),
        )

        # Update sorted set score if effective_from changed
        if effective_from is not None:
            score = (effective_from - date(1970, 1, 1)).days
            await self._client.zadd(self._revisions_set_key(source), {revision_id: score})

        logger.debug("Revision updated", source=source, revision_id=revision_id)
        return revision

    async def delete_revision(self, source: str, revision_id: str) -> bool:
        """
        Delete a revision.

        Implements [policy-knowledge-base:PolicyRegistry/TS-08]
        Implements [policy-knowledge-base:PolicyRegistry/TS-09]

        Args:
            source: Policy source slug.
            revision_id: Revision ID.

        Returns:
            True if deleted, False if not found.

        Raises:
            CannotDeleteSoleRevisionError: If this is the only active revision.
        """
        revision = await self.get_revision(source, revision_id)
        if revision is None:
            return False

        # Check if this is the sole active revision
        if revision.status == RevisionStatus.ACTIVE:
            active_count = await self._count_active_revisions(source)
            if active_count <= 1:
                raise CannotDeleteSoleRevisionError(source, revision_id)

        async with self._client.pipeline() as pipe:
            # Delete revision hash
            await pipe.delete(self._revision_key(source, revision_id))
            # Remove from sorted set
            await pipe.zrem(self._revisions_set_key(source), revision_id)
            await pipe.execute()

        logger.info("Revision deleted", source=source, revision_id=revision_id)
        return True

    async def _count_active_revisions(self, source: str) -> int:
        """Count active revisions for a policy."""
        revisions = await self._get_all_revisions(source)
        return sum(1 for rev in revisions if rev.status == RevisionStatus.ACTIVE)

    # =========================================================================
    # Effective Date Resolution
    # =========================================================================

    async def get_effective_revision_for_date(
        self, source: str, effective_date: date
    ) -> PolicyRevisionRecord | None:
        """
        Get the revision that was in force on a specific date.

        Implements [policy-knowledge-base:PolicyRegistry/TS-10]
        Implements [policy-knowledge-base:PolicyRegistry/TS-11]

        Uses the sorted set to efficiently find revisions where:
        - effective_from <= date AND
        - (effective_to is None OR effective_to >= date)

        Args:
            source: Policy source slug.
            effective_date: The date to check.

        Returns:
            Revision record if one was in force, None otherwise.
        """
        # Get all revision IDs up to and including the effective_date
        # Score is effective_from as days since epoch
        max_score = (effective_date - date(1970, 1, 1)).days

        # Get revisions with effective_from <= effective_date (in reverse order)
        revision_ids = await self._client.zrevrangebyscore(
            self._revisions_set_key(source),
            max_score,
            "-inf",
        )

        for rev_id in revision_ids:
            rev = await self.get_revision(source, rev_id)
            if rev is None:
                continue

            # Skip non-active revisions
            if rev.status not in (RevisionStatus.ACTIVE, RevisionStatus.SUPERSEDED):
                continue

            # Check effective_to
            if rev.effective_to is None or rev.effective_to >= effective_date:
                return rev

        return None

    async def get_all_effective_for_date(
        self, effective_date: date
    ) -> dict[str, PolicyRevisionRecord | None]:
        """
        Get effective revision for each policy on a given date.

        Args:
            effective_date: The date to check.

        Returns:
            Dict mapping source slug to effective revision (or None if none effective).
        """
        sources = await self._client.smembers(self._policies_all_key())
        result: dict[str, PolicyRevisionRecord | None] = {}

        for source in sources:
            rev = await self.get_effective_revision_for_date(source, effective_date)
            result[source] = rev

        return result
