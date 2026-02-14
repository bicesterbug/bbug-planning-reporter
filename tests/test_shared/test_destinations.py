"""
Tests for destination management.

Verifies [cycle-route-assessment:FR-005] - Configurable destinations with defaults
Verifies [cycle-route-assessment:FR-006] - Per-review destination selection

Verifies test scenarios:
- [cycle-route-assessment:DestinationManagement/TS-01] Default destinations seeded
- [cycle-route-assessment:DestinationManagement/TS-02] Add destination
- [cycle-route-assessment:DestinationManagement/TS-03] Delete destination
"""

import pytest

from src.shared.destinations import (
    DEFAULT_DESTINATIONS,
    add_destination,
    delete_destination,
    get_destination,
    list_destinations,
)
from src.shared.redis_client import RedisClient


@pytest.fixture
async def redis_client(fake_redis):
    """Create a RedisClient backed by fake Redis."""
    client = RedisClient()
    client._client = fake_redis
    return client


class TestListDestinations:
    """Verifies [cycle-route-assessment:DestinationManagement/TS-01]."""

    @pytest.mark.anyio
    async def test_seeds_defaults_on_first_access(self, redis_client):
        """[DestinationManagement/TS-01] Default destinations seeded on first access."""
        destinations = await list_destinations(redis_client)

        assert len(destinations) == len(DEFAULT_DESTINATIONS)
        names = {d["name"] for d in destinations}
        assert "Bicester North Station" in names
        assert "Bicester Village Station" in names
        assert "Manorsfield Road (Pioneer Bus S5)" in names

    @pytest.mark.anyio
    async def test_returns_sorted_by_id(self, redis_client):
        """Destinations are returned sorted by ID."""
        destinations = await list_destinations(redis_client)
        ids = [d["id"] for d in destinations]
        assert ids == sorted(ids)

    @pytest.mark.anyio
    async def test_does_not_reseed_if_exists(self, redis_client):
        """Defaults are not re-seeded on subsequent calls."""
        await list_destinations(redis_client)
        await delete_destination(redis_client, "dest_001")

        destinations = await list_destinations(redis_client)
        assert len(destinations) == len(DEFAULT_DESTINATIONS) - 1

    @pytest.mark.anyio
    async def test_default_categories(self, redis_client):
        """Default destinations have correct categories."""
        destinations = await list_destinations(redis_client)
        by_id = {d["id"]: d for d in destinations}
        assert by_id["dest_001"]["category"] == "rail"
        assert by_id["dest_002"]["category"] == "rail"
        assert by_id["dest_003"]["category"] == "bus"


class TestAddDestination:
    """Verifies [cycle-route-assessment:DestinationManagement/TS-02]."""

    @pytest.mark.anyio
    async def test_add_destination(self, redis_client):
        """[DestinationManagement/TS-02] Add destination creates new entry."""
        dest = await add_destination(
            redis_client,
            name="Town Centre",
            lat=51.9,
            lon=-1.15,
            category="other",
        )

        assert dest["name"] == "Town Centre"
        assert dest["lat"] == 51.9
        assert dest["lon"] == -1.15
        assert dest["category"] == "other"
        assert dest["id"].startswith("dest_")

        destinations = await list_destinations(redis_client)
        assert len(destinations) == len(DEFAULT_DESTINATIONS) + 1

    @pytest.mark.anyio
    async def test_generated_id_increments(self, redis_client):
        """Generated IDs increment from highest existing."""
        dest1 = await add_destination(redis_client, name="A", lat=51.0, lon=-1.0)
        dest2 = await add_destination(redis_client, name="B", lat=51.1, lon=-1.1)

        # Defaults are dest_001..003, so next should be 004, 005
        assert dest1["id"] == "dest_004"
        assert dest2["id"] == "dest_005"


class TestDeleteDestination:
    """Verifies [cycle-route-assessment:DestinationManagement/TS-03]."""

    @pytest.mark.anyio
    async def test_delete_existing(self, redis_client):
        """[DestinationManagement/TS-03] Delete destination removes it."""
        await list_destinations(redis_client)  # seed

        result = await delete_destination(redis_client, "dest_001")
        assert result is True

        destinations = await list_destinations(redis_client)
        ids = {d["id"] for d in destinations}
        assert "dest_001" not in ids

    @pytest.mark.anyio
    async def test_delete_nonexistent(self, redis_client):
        """Delete returns False for nonexistent ID."""
        result = await delete_destination(redis_client, "dest_999")
        assert result is False


class TestGetDestination:
    """Tests for single destination lookup."""

    @pytest.mark.anyio
    async def test_get_existing(self, redis_client):
        """Get returns destination by ID after seeding."""
        await list_destinations(redis_client)  # seed
        dest = await get_destination(redis_client, "dest_001")
        assert dest is not None
        assert dest["name"] == "Bicester North Station"

    @pytest.mark.anyio
    async def test_get_nonexistent(self, redis_client):
        """Get returns None for nonexistent ID."""
        dest = await get_destination(redis_client, "dest_999")
        assert dest is None
