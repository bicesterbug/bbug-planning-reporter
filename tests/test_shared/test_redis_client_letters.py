"""
Tests for RedisClient letter operations.

Implements test scenarios from [response-letter:LetterRedis/TS-01] through [TS-04]
"""

from datetime import UTC, datetime

import fakeredis.aioredis
import pytest

from src.shared.redis_client import RedisClient


@pytest.fixture
async def redis_client(fake_redis: fakeredis.aioredis.FakeRedis) -> RedisClient:
    """Create a RedisClient with fake Redis backend."""
    client = RedisClient()
    client._client = fake_redis
    return client


@pytest.fixture
def sample_letter() -> dict:
    """Create a sample letter record."""
    return {
        "letter_id": "ltr_01HQXK7V3WNPB8MTJF2R5ADGX9",
        "review_id": "rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
        "application_ref": "25/01178/REM",
        "stance": "object",
        "tone": "formal",
        "case_officer": None,
        "letter_date": "2026-02-07",
        "status": "generating",
        "content": None,
        "metadata": None,
        "error": None,
        "created_at": datetime.now(UTC).isoformat(),
        "completed_at": None,
    }


class TestStoreAndRetrieveLetter:
    """Tests for storing and retrieving letters."""

    @pytest.mark.asyncio
    async def test_store_and_retrieve_letter(
        self, redis_client: RedisClient, sample_letter: dict
    ) -> None:
        """
        Verifies [response-letter:LetterRedis/TS-01]

        Given: A letter dict is stored
        When: get_letter is called with the same letter_id
        Then: Returns the stored letter dict
        """
        letter_id = sample_letter["letter_id"]
        await redis_client.store_letter(letter_id, sample_letter)
        retrieved = await redis_client.get_letter(letter_id)

        assert retrieved is not None
        assert retrieved["letter_id"] == letter_id
        assert retrieved["review_id"] == sample_letter["review_id"]
        assert retrieved["stance"] == "object"
        assert retrieved["status"] == "generating"

    @pytest.mark.asyncio
    async def test_letter_not_found(self, redis_client: RedisClient) -> None:
        """
        Verifies [response-letter:LetterRedis/TS-02]

        Given: No letter exists
        When: get_letter is called
        Then: Returns None
        """
        result = await redis_client.get_letter("ltr_nonexistent")
        assert result is None


class TestUpdateLetterStatus:
    """Tests for updating letter status."""

    @pytest.mark.asyncio
    async def test_update_to_completed(
        self, redis_client: RedisClient, sample_letter: dict
    ) -> None:
        """
        Verifies [response-letter:LetterRedis/TS-03]

        Given: A letter record exists with status=generating
        When: update_letter_status is called with status=completed and content
        Then: Letter record has status=completed and content field populated
        """
        letter_id = sample_letter["letter_id"]
        await redis_client.store_letter(letter_id, sample_letter)

        now = datetime.now(UTC)
        result = await redis_client.update_letter_status(
            letter_id,
            status="completed",
            content="# Response Letter\n\nDear Sir/Madam...",
            metadata={"model": "claude-sonnet-4-5-20250929", "input_tokens": 5000, "output_tokens": 2000},
            completed_at=now,
        )

        assert result is True

        updated = await redis_client.get_letter(letter_id)
        assert updated is not None
        assert updated["status"] == "completed"
        assert updated["content"] == "# Response Letter\n\nDear Sir/Madam..."
        assert updated["metadata"]["model"] == "claude-sonnet-4-5-20250929"
        assert updated["metadata"]["input_tokens"] == 5000
        assert updated["completed_at"] == now.isoformat()

    @pytest.mark.asyncio
    async def test_update_to_failed(
        self, redis_client: RedisClient, sample_letter: dict
    ) -> None:
        """
        Verifies [response-letter:LetterRedis/TS-03] (failure path)

        Given: A letter record exists with status=generating
        When: update_letter_status is called with status=failed and error
        Then: Letter record has status=failed and error details
        """
        letter_id = sample_letter["letter_id"]
        await redis_client.store_letter(letter_id, sample_letter)

        result = await redis_client.update_letter_status(
            letter_id,
            status="failed",
            error={"code": "letter_generation_failed", "message": "Claude API error"},
            completed_at=datetime.now(UTC),
        )

        assert result is True

        updated = await redis_client.get_letter(letter_id)
        assert updated is not None
        assert updated["status"] == "failed"
        assert updated["error"]["code"] == "letter_generation_failed"

    @pytest.mark.asyncio
    async def test_update_nonexistent_letter(self, redis_client: RedisClient) -> None:
        """
        Given: No letter exists
        When: update_letter_status is called
        Then: Returns False
        """
        result = await redis_client.update_letter_status(
            "ltr_nonexistent", status="completed"
        )
        assert result is False


class TestLetterTTL:
    """Tests for letter TTL."""

    @pytest.mark.asyncio
    async def test_letter_has_ttl(
        self, redis_client: RedisClient, sample_letter: dict, fake_redis: fakeredis.aioredis.FakeRedis
    ) -> None:
        """
        Verifies [response-letter:LetterRedis/TS-04]

        Given: A letter is stored
        When: TTL is checked
        Then: Key expires after 30 days
        """
        letter_id = sample_letter["letter_id"]
        await redis_client.store_letter(letter_id, sample_letter)

        ttl = await fake_redis.ttl(f"letter:{letter_id}")
        expected_ttl = 30 * 24 * 60 * 60  # 30 days in seconds
        # Allow 5 seconds of tolerance
        assert abs(ttl - expected_ttl) < 5, f"Expected TTL ~{expected_ttl}, got {ttl}"

    @pytest.mark.asyncio
    async def test_update_preserves_ttl(
        self, redis_client: RedisClient, sample_letter: dict, fake_redis: fakeredis.aioredis.FakeRedis
    ) -> None:
        """
        Given: A letter is stored with TTL
        When: Status is updated
        Then: TTL is preserved (not reset to full 30 days)
        """
        letter_id = sample_letter["letter_id"]
        await redis_client.store_letter(letter_id, sample_letter, ttl_days=1)

        original_ttl = await fake_redis.ttl(f"letter:{letter_id}")

        await redis_client.update_letter_status(
            letter_id, status="completed", content="Letter content"
        )

        new_ttl = await fake_redis.ttl(f"letter:{letter_id}")
        # Should be close to original (within a few seconds), not reset to 30 days
        assert new_ttl <= original_ttl, f"TTL should not increase: was {original_ttl}, now {new_ttl}"
        assert new_ttl > 0, "TTL should still be positive"
