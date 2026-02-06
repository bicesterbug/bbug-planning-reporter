"""
Tests for RedisClient.

Implements test scenarios from [foundation-api:RedisClient/TS-01] through [TS-05]
"""

from datetime import UTC, datetime

import fakeredis.aioredis
import pytest

from src.shared.models import (
    ProcessingPhase,
    ReviewJob,
    ReviewStatus,
)
from src.shared.redis_client import RedisClient


@pytest.fixture
async def redis_client(fake_redis: fakeredis.aioredis.FakeRedis) -> RedisClient:
    """Create a RedisClient with fake Redis backend."""
    client = RedisClient()
    client._client = fake_redis
    return client


@pytest.fixture
def sample_job() -> ReviewJob:
    """Create a sample review job."""
    return ReviewJob(
        review_id="rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
        application_ref="25/01178/REM",
        status=ReviewStatus.QUEUED,
        created_at=datetime.now(UTC),
    )


class TestStoreAndRetrieveJob:
    """Tests for storing and retrieving jobs."""

    @pytest.mark.asyncio
    async def test_store_and_retrieve_job(
        self, redis_client: RedisClient, sample_job: ReviewJob
    ) -> None:
        """
        Verifies [foundation-api:RedisClient/TS-01]

        Given: Job data
        When: Store then retrieve by review_id
        Then: Data matches
        """
        await redis_client.store_job(sample_job)
        retrieved = await redis_client.get_job(sample_job.review_id)

        assert retrieved is not None
        assert retrieved.review_id == sample_job.review_id
        assert retrieved.application_ref == sample_job.application_ref
        assert retrieved.status == sample_job.status

    @pytest.mark.asyncio
    async def test_get_nonexistent_job_returns_none(
        self, redis_client: RedisClient
    ) -> None:
        """
        Given: No job exists
        When: Get by review_id
        Then: Returns None
        """
        result = await redis_client.get_job("rev_nonexistent")
        assert result is None


class TestUpdateJobStatus:
    """Tests for updating job status."""

    @pytest.mark.asyncio
    async def test_update_job_status(
        self, redis_client: RedisClient, sample_job: ReviewJob
    ) -> None:
        """
        Verifies [foundation-api:RedisClient/TS-02]

        Given: Existing job
        When: Update status to "processing"
        Then: Status persisted
        """
        await redis_client.store_job(sample_job)

        success = await redis_client.update_job_status(
            sample_job.review_id,
            ReviewStatus.PROCESSING,
            progress={
                "phase": "fetching_metadata",
                "phase_number": 1,
                "total_phases": 5,
                "percent_complete": 10,
            },
            started_at=datetime.now(UTC),
        )

        assert success is True

        updated = await redis_client.get_job(sample_job.review_id)
        assert updated is not None
        assert updated.status == ReviewStatus.PROCESSING
        assert updated.progress is not None
        assert updated.progress.phase == ProcessingPhase.FETCHING_METADATA
        assert updated.started_at is not None

    @pytest.mark.asyncio
    async def test_update_nonexistent_job_returns_false(
        self, redis_client: RedisClient
    ) -> None:
        """
        Given: No job exists
        When: Update status
        Then: Returns False
        """
        success = await redis_client.update_job_status(
            "rev_nonexistent",
            ReviewStatus.PROCESSING,
        )
        assert success is False


class TestCheckForExistingJob:
    """Tests for checking existing active jobs."""

    @pytest.mark.asyncio
    async def test_has_active_job_when_processing(
        self, redis_client: RedisClient, sample_job: ReviewJob
    ) -> None:
        """
        Verifies [foundation-api:RedisClient/TS-03]

        Given: Job for ref exists with status "processing"
        When: Query active jobs for ref
        Then: Returns True
        """
        sample_job.status = ReviewStatus.PROCESSING
        await redis_client.store_job(sample_job)

        has_active = await redis_client.has_active_job_for_ref(sample_job.application_ref)
        assert has_active is True

    @pytest.mark.asyncio
    async def test_has_active_job_when_queued(
        self, redis_client: RedisClient, sample_job: ReviewJob
    ) -> None:
        """
        Given: Job for ref exists with status "queued"
        When: Query active jobs for ref
        Then: Returns True
        """
        await redis_client.store_job(sample_job)

        has_active = await redis_client.has_active_job_for_ref(sample_job.application_ref)
        assert has_active is True

    @pytest.mark.asyncio
    async def test_no_active_job_when_completed(
        self, redis_client: RedisClient, sample_job: ReviewJob
    ) -> None:
        """
        Given: Job for ref exists with status "completed"
        When: Query active jobs for ref
        Then: Returns False
        """
        sample_job.status = ReviewStatus.COMPLETED
        await redis_client.store_job(sample_job)

        has_active = await redis_client.has_active_job_for_ref(sample_job.application_ref)
        assert has_active is False

    @pytest.mark.asyncio
    async def test_no_active_job_when_none_exist(
        self, redis_client: RedisClient
    ) -> None:
        """
        Given: No jobs exist for ref
        When: Query active jobs for ref
        Then: Returns False
        """
        has_active = await redis_client.has_active_job_for_ref("99/99999/XXX")
        assert has_active is False


class TestListJobs:
    """Tests for listing jobs."""

    @pytest.mark.asyncio
    async def test_list_jobs_by_status(
        self, redis_client: RedisClient
    ) -> None:
        """
        Verifies [foundation-api:RedisClient/TS-04]

        Given: Multiple jobs exist
        When: List with status filter
        Then: Returns filtered list
        """
        # Create jobs with different statuses
        job1 = ReviewJob(
            review_id="rev_1",
            application_ref="25/00001/F",
            status=ReviewStatus.COMPLETED,
            created_at=datetime.now(UTC),
        )
        job2 = ReviewJob(
            review_id="rev_2",
            application_ref="25/00002/F",
            status=ReviewStatus.QUEUED,
            created_at=datetime.now(UTC),
        )
        job3 = ReviewJob(
            review_id="rev_3",
            application_ref="25/00003/F",
            status=ReviewStatus.COMPLETED,
            created_at=datetime.now(UTC),
        )

        await redis_client.store_job(job1)
        await redis_client.store_job(job2)
        await redis_client.store_job(job3)

        # List completed jobs
        summaries, total = await redis_client.list_jobs(status=ReviewStatus.COMPLETED)

        assert total == 2
        assert len(summaries) == 2
        assert all(s.status == ReviewStatus.COMPLETED for s in summaries)

    @pytest.mark.asyncio
    async def test_list_jobs_pagination(
        self, redis_client: RedisClient
    ) -> None:
        """
        Given: Multiple jobs exist
        When: List with pagination
        Then: Returns correct page
        """
        # Create 5 jobs
        for i in range(5):
            job = ReviewJob(
                review_id=f"rev_{i}",
                application_ref=f"25/0000{i}/F",
                status=ReviewStatus.QUEUED,
                created_at=datetime.now(UTC),
            )
            await redis_client.store_job(job)

        # Get page 1 (first 2)
        summaries, total = await redis_client.list_jobs(
            status=ReviewStatus.QUEUED, limit=2, offset=0
        )
        assert total == 5
        assert len(summaries) == 2

        # Get page 2 (next 2)
        summaries, total = await redis_client.list_jobs(
            status=ReviewStatus.QUEUED, limit=2, offset=2
        )
        assert total == 5
        assert len(summaries) == 2


class TestConnectionRecovery:
    """Tests for connection recovery."""

    @pytest.mark.asyncio
    async def test_connection_recovery(
        self, redis_client: RedisClient, sample_job: ReviewJob
    ) -> None:
        """
        Verifies [foundation-api:RedisClient/TS-05]

        Given: Redis temporarily unavailable
        When: Operation after reconnect
        Then: Succeeds
        """
        # Store job
        await redis_client.store_job(sample_job)

        # Simulate disconnect (set client to None)
        original_client = redis_client._client
        redis_client._client = None

        # Restore and verify auto-reconnect works
        redis_client._client = original_client

        # Operation should succeed
        retrieved = await redis_client.get_job(sample_job.review_id)
        assert retrieved is not None
        assert retrieved.review_id == sample_job.review_id


class TestPing:
    """Tests for ping/health check."""

    @pytest.mark.asyncio
    async def test_ping_when_connected(
        self, redis_client: RedisClient
    ) -> None:
        """
        Given: Redis connected
        When: Ping
        Then: Returns True
        """
        result = await redis_client.ping()
        assert result is True
