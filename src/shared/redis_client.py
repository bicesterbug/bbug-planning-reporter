"""
Async Redis client wrapper for job management.

Implements [foundation-api:NFR-002] - Job queue reliability
Implements [foundation-api:NFR-001] - Async operations for performance
"""

import json
import os
from datetime import datetime
from typing import Any

import redis.asyncio as redis
import structlog

from src.shared.models import ReviewJob, ReviewJobSummary, ReviewStatus

logger = structlog.get_logger(__name__)


class RedisClient:
    """
    Async Redis client for review job management.

    Provides typed methods for job storage, status updates, and queries.
    Uses connection pooling for efficient resource usage.

    Implements:
    - [foundation-api:RedisClient/TS-01] Store and retrieve job
    - [foundation-api:RedisClient/TS-02] Update job status
    - [foundation-api:RedisClient/TS-03] Check for existing job
    - [foundation-api:RedisClient/TS-04] List jobs by status
    - [foundation-api:RedisClient/TS-05] Connection recovery
    """

    def __init__(self, redis_url: str | None = None) -> None:
        """
        Initialize Redis client.

        Args:
            redis_url: Redis connection URL. Defaults to REDIS_URL env var.
        """
        self._redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._client: redis.Redis | None = None

    async def connect(self) -> None:
        """Establish connection to Redis with pooling."""
        if self._client is None:
            self._client = redis.from_url(
                self._redis_url,
                decode_responses=True,
                max_connections=10,
            )
            logger.info("Redis client connected", url=self._redis_url)

    async def close(self) -> None:
        """Close Redis connection."""
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("Redis client closed")

    async def _ensure_connected(self) -> redis.Redis:
        """Ensure client is connected, reconnecting if necessary."""
        if self._client is None:
            await self.connect()
        assert self._client is not None
        return self._client

    def _job_key(self, review_id: str) -> str:
        """Get Redis key for a job record."""
        return f"review:{review_id}"

    def _result_key(self, review_id: str) -> str:
        """Get Redis key for a job result."""
        return f"review_result:{review_id}"

    def _status_index_key(self, status: ReviewStatus | str) -> str:
        """Get Redis key for status index sorted set."""
        status_value = status.value if isinstance(status, ReviewStatus) else status
        return f"reviews_by_status:{status_value}"

    def _ref_index_key(self, application_ref: str) -> str:
        """Get Redis key for application reference index."""
        # Normalize ref for key: replace / with _
        safe_ref = application_ref.replace("/", "_")
        return f"reviews_by_ref:{safe_ref}"

    # =========================================================================
    # Job CRUD Operations
    # =========================================================================

    async def store_job(self, job: ReviewJob) -> None:
        """
        Store a review job.

        Implements [foundation-api:RedisClient/TS-01]

        Args:
            job: The review job to store.
        """
        client = await self._ensure_connected()

        job_data = job.model_dump_json()
        timestamp = job.created_at.timestamp()

        async with client.pipeline() as pipe:
            # Store job data
            await pipe.set(self._job_key(job.review_id), job_data)

            # Add to status index (sorted by created_at)
            await pipe.zadd(self._status_index_key(job.status), {job.review_id: timestamp})

            # Add to ref index for duplicate checking
            await pipe.sadd(self._ref_index_key(job.application_ref), job.review_id)

            await pipe.execute()

        logger.debug(
            "Job stored",
            review_id=job.review_id,
            application_ref=job.application_ref,
            status=job.status,
        )

    async def get_job(self, review_id: str) -> ReviewJob | None:
        """
        Retrieve a review job by ID.

        Implements [foundation-api:RedisClient/TS-01]

        Args:
            review_id: The review ID to look up.

        Returns:
            The job if found, None otherwise.
        """
        client = await self._ensure_connected()

        job_data = await client.get(self._job_key(review_id))
        if job_data is None:
            return None

        return ReviewJob.model_validate_json(job_data)

    async def update_job_status(
        self,
        review_id: str,
        status: ReviewStatus,
        progress: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> bool:
        """
        Update a job's status.

        Implements [foundation-api:RedisClient/TS-02]

        Args:
            review_id: The review ID to update.
            status: New status.
            progress: Optional progress info.
            error: Optional error info.
            started_at: Optional started timestamp.
            completed_at: Optional completed timestamp.

        Returns:
            True if job was found and updated, False otherwise.
        """
        client = await self._ensure_connected()

        job = await self.get_job(review_id)
        if job is None:
            return False

        old_status = job.status
        # Normalize to string for comparison
        old_status_value = old_status.value if isinstance(old_status, ReviewStatus) else old_status
        new_status_value = status.value if isinstance(status, ReviewStatus) else status

        # Update job fields
        job.status = status
        if progress is not None:
            from src.shared.models import ReviewProgress

            job.progress = ReviewProgress.model_validate(progress)
        if error is not None:
            job.error = error
        if started_at is not None:
            job.started_at = started_at
        if completed_at is not None:
            job.completed_at = completed_at

        timestamp = job.created_at.timestamp()

        async with client.pipeline() as pipe:
            # Update job data
            await pipe.set(self._job_key(review_id), job.model_dump_json())

            # Update status index if status changed
            if old_status_value != new_status_value:
                await pipe.zrem(self._status_index_key(old_status_value), review_id)
                await pipe.zadd(self._status_index_key(new_status_value), {review_id: timestamp})

            await pipe.execute()

        logger.debug(
            "Job status updated",
            review_id=review_id,
            old_status=old_status,
            new_status=status,
        )
        return True

    async def delete_job(self, review_id: str) -> bool:
        """
        Delete a review job.

        Args:
            review_id: The review ID to delete.

        Returns:
            True if job was found and deleted, False otherwise.
        """
        client = await self._ensure_connected()

        job = await self.get_job(review_id)
        if job is None:
            return False

        async with client.pipeline() as pipe:
            # Remove job data
            await pipe.delete(self._job_key(review_id))

            # Remove from status index
            await pipe.zrem(self._status_index_key(job.status), review_id)

            # Remove from ref index
            await pipe.srem(self._ref_index_key(job.application_ref), review_id)

            # Remove result if exists
            await pipe.delete(self._result_key(review_id))

            await pipe.execute()

        return True

    # =========================================================================
    # Query Operations
    # =========================================================================

    async def has_active_job_for_ref(self, application_ref: str) -> bool:
        """
        Check if there's an active (queued/processing) job for an application.

        Implements [foundation-api:RedisClient/TS-03]

        Args:
            application_ref: The application reference to check.

        Returns:
            True if an active job exists, False otherwise.
        """
        client = await self._ensure_connected()

        # Get all review IDs for this ref
        review_ids = await client.smembers(self._ref_index_key(application_ref))
        if not review_ids:
            return False

        # Check each job's status
        active_statuses = {ReviewStatus.QUEUED.value, ReviewStatus.PROCESSING.value}
        for review_id in review_ids:
            job = await self.get_job(review_id)
            if job:
                status_value = job.status.value if isinstance(job.status, ReviewStatus) else job.status
                if status_value in active_statuses:
                    return True

        return False

    async def list_jobs(
        self,
        status: ReviewStatus | None = None,
        application_ref: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[ReviewJobSummary], int]:
        """
        List jobs with optional filtering.

        Implements [foundation-api:RedisClient/TS-04]

        Args:
            status: Optional status filter.
            application_ref: Optional application reference filter.
            limit: Maximum results to return.
            offset: Offset for pagination.

        Returns:
            Tuple of (job summaries, total count).
        """
        client = await self._ensure_connected()

        # Determine which review IDs to fetch
        if status is not None:
            # Get from status index (sorted by created_at descending)
            review_ids = await client.zrevrange(
                self._status_index_key(status),
                0,
                -1,
            )
        elif application_ref is not None:
            # Get from ref index
            review_ids = list(await client.smembers(self._ref_index_key(application_ref)))
        else:
            # Get all jobs from all status indexes
            review_ids = []
            for s in ReviewStatus:
                ids = await client.zrevrange(self._status_index_key(s), 0, -1)
                review_ids.extend(ids)

        total = len(review_ids)

        # Apply pagination
        review_ids = review_ids[offset : offset + limit]

        # Fetch job summaries
        summaries = []
        for review_id in review_ids:
            job = await self.get_job(review_id)
            if job:
                summaries.append(
                    ReviewJobSummary(
                        review_id=job.review_id,
                        application_ref=job.application_ref,
                        status=job.status,
                        created_at=job.created_at,
                        completed_at=job.completed_at,
                    )
                )

        return summaries, total

    # =========================================================================
    # Result Operations
    # =========================================================================

    async def store_result(self, review_id: str, result: dict[str, Any], ttl_days: int = 30) -> None:
        """
        Store a review result.

        Args:
            review_id: The review ID.
            result: The result data.
            ttl_days: Time-to-live in days.
        """
        client = await self._ensure_connected()

        result_data = json.dumps(result)
        ttl_seconds = ttl_days * 24 * 60 * 60

        await client.setex(self._result_key(review_id), ttl_seconds, result_data)

        # Update job with result key
        await self.update_job_status(
            review_id,
            ReviewStatus.COMPLETED,
            completed_at=datetime.utcnow(),
        )

        # Store result key reference in job
        job = await self.get_job(review_id)
        if job:
            job.result_key = self._result_key(review_id)
            await client.set(self._job_key(review_id), job.model_dump_json())

    async def get_result(self, review_id: str) -> dict[str, Any] | None:
        """
        Retrieve a review result.

        Args:
            review_id: The review ID.

        Returns:
            The result data if found, None otherwise.
        """
        client = await self._ensure_connected()

        result_data = await client.get(self._result_key(review_id))
        if result_data is None:
            return None

        return json.loads(result_data)

    # =========================================================================
    # Letter Operations
    # Implements [response-letter:FR-001] - Store letter records
    # Implements [response-letter:FR-008] - Retrieve letter records
    # =========================================================================

    def _letter_key(self, letter_id: str) -> str:
        """Get Redis key for a letter record."""
        return f"letter:{letter_id}"

    async def store_letter(self, letter_id: str, letter: dict[str, Any], ttl_days: int = 30) -> None:
        """
        Store a letter record.

        Implements [response-letter:LetterRedis/TS-01]

        Args:
            letter_id: The letter ID.
            letter: The letter data dict.
            ttl_days: Time-to-live in days.
        """
        client = await self._ensure_connected()
        ttl_seconds = ttl_days * 24 * 60 * 60
        await client.setex(self._letter_key(letter_id), ttl_seconds, json.dumps(letter))
        logger.debug("Letter stored", letter_id=letter_id)

    async def get_letter(self, letter_id: str) -> dict[str, Any] | None:
        """
        Retrieve a letter record.

        Implements [response-letter:LetterRedis/TS-01]
        Implements [response-letter:LetterRedis/TS-02]

        Args:
            letter_id: The letter ID.

        Returns:
            The letter data if found, None otherwise.
        """
        client = await self._ensure_connected()
        data = await client.get(self._letter_key(letter_id))
        if data is None:
            return None
        return json.loads(data)

    async def update_letter_status(
        self,
        letter_id: str,
        status: str,
        content: str | None = None,
        metadata: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        completed_at: datetime | None = None,
        output_url: str | None = None,
    ) -> bool:
        """
        Update a letter record's status and optionally set content/metadata.

        Args:
            letter_id: The letter ID.
            status: New status string.
            content: Markdown letter content (on completion).
            metadata: Generation metadata (model, tokens, duration).
            error: Error details (on failure).
            completed_at: Completion timestamp.
            output_url: Public URL for the letter markdown file.

        Returns:
            True if letter was found and updated, False otherwise.
        """
        client = await self._ensure_connected()

        letter = await self.get_letter(letter_id)
        if letter is None:
            return False

        letter["status"] = status
        if content is not None:
            letter["content"] = content
        if metadata is not None:
            letter["metadata"] = metadata
        if error is not None:
            letter["error"] = error
        if completed_at is not None:
            letter["completed_at"] = completed_at.isoformat()
        if output_url is not None:
            letter["output_url"] = output_url

        # Preserve remaining TTL
        ttl = await client.ttl(self._letter_key(letter_id))
        if ttl > 0:
            await client.setex(self._letter_key(letter_id), ttl, json.dumps(letter))
        else:
            # Fallback: 30-day TTL
            await client.setex(
                self._letter_key(letter_id), 30 * 24 * 60 * 60, json.dumps(letter)
            )

        logger.debug("Letter status updated", letter_id=letter_id, status=status)
        return True

    # =========================================================================
    # Review Letter URL Lookup
    # =========================================================================

    _LETTER_URL_TTL = 30 * 24 * 60 * 60  # 30 days

    async def set_review_letter_url(self, review_id: str, url: str) -> None:
        """Store the latest letter URL for a review (reverse lookup)."""
        client = await self._ensure_connected()
        await client.setex(
            f"review_letter_url:{review_id}", self._LETTER_URL_TTL, url,
        )

    async def get_review_letter_url(self, review_id: str) -> str | None:
        """Get the latest letter URL for a review, or None if no letter exists."""
        client = await self._ensure_connected()
        val = await client.get(f"review_letter_url:{review_id}")
        if val is None:
            return None
        return val.decode() if isinstance(val, bytes) else val

    # =========================================================================
    # Health Check
    # =========================================================================

    async def ping(self) -> bool:
        """
        Check Redis connectivity.

        Returns:
            True if connected, False otherwise.
        """
        try:
            client = await self._ensure_connected()
            await client.ping()
            return True
        except Exception:
            return False
