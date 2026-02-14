"""
Destination management for cycle route assessments.

Implements [cycle-route-assessment:FR-005] - Configurable destinations with defaults
Implements [cycle-route-assessment:FR-006] - Per-review destination selection

Implements:
- [cycle-route-assessment:DestinationManagement/TS-01] Default destinations seeded
- [cycle-route-assessment:DestinationManagement/TS-02] Add destination
- [cycle-route-assessment:DestinationManagement/TS-03] Delete destination
- [cycle-route-assessment:DestinationManagement/TS-04] List destinations via API
"""

import json
from typing import Any

import structlog

from src.shared.redis_client import RedisClient

logger = structlog.get_logger(__name__)

DESTINATIONS_KEY = "cycle_route:destinations"

# Default destinations for Bicester area
# Implements [cycle-route-assessment:FR-005] - Configurable destinations with defaults
DEFAULT_DESTINATIONS: list[dict[str, Any]] = [
    {
        "id": "dest_001",
        "name": "Bicester North Station",
        "lat": 51.9054,
        "lon": -1.1512,
        "category": "rail",
    },
    {
        "id": "dest_002",
        "name": "Bicester Village Station",
        "lat": 51.8899,
        "lon": -1.1467,
        "category": "rail",
    },
    {
        "id": "dest_003",
        "name": "Manorsfield Road (Pioneer Bus S5)",
        "lat": 51.8936,
        "lon": -1.1538,
        "category": "bus",
    },
]


async def _ensure_seeded(redis: RedisClient) -> None:
    """Seed default destinations if none exist."""
    client = await redis._ensure_connected()
    exists = await client.exists(DESTINATIONS_KEY)
    if not exists:
        for dest in DEFAULT_DESTINATIONS:
            await client.hset(DESTINATIONS_KEY, dest["id"], json.dumps(dest))
        logger.info("Seeded default destinations", count=len(DEFAULT_DESTINATIONS))


async def list_destinations(redis: RedisClient) -> list[dict[str, Any]]:
    """
    List all configured destinations.

    Seeds defaults on first access if none exist.

    Returns:
        List of destination dicts.
    """
    await _ensure_seeded(redis)
    client = await redis._ensure_connected()
    raw = await client.hgetall(DESTINATIONS_KEY)
    destinations = [json.loads(v) for v in raw.values()]
    # Sort by ID for stable ordering
    destinations.sort(key=lambda d: d["id"])
    return destinations


async def get_destination(redis: RedisClient, destination_id: str) -> dict[str, Any] | None:
    """
    Get a single destination by ID.

    Args:
        redis: RedisClient instance.
        destination_id: The destination ID.

    Returns:
        Destination dict or None.
    """
    client = await redis._ensure_connected()
    raw = await client.hget(DESTINATIONS_KEY, destination_id)
    if raw is None:
        return None
    return json.loads(raw)


async def add_destination(
    redis: RedisClient,
    name: str,
    lat: float,
    lon: float,
    category: str = "other",
) -> dict[str, Any]:
    """
    Add a new destination.

    Implements [cycle-route-assessment:DestinationManagement/TS-02]

    Args:
        redis: RedisClient instance.
        name: Destination name.
        lat: Latitude.
        lon: Longitude.
        category: Category (rail, bus, other).

    Returns:
        The created destination dict with generated ID.
    """
    await _ensure_seeded(redis)
    client = await redis._ensure_connected()

    # Generate next ID
    raw = await client.hgetall(DESTINATIONS_KEY)
    existing_ids = list(raw.keys())
    max_num = 0
    for did in existing_ids:
        if did.startswith("dest_"):
            try:
                num = int(did.split("_")[1])
                max_num = max(max_num, num)
            except (ValueError, IndexError):
                pass
    new_id = f"dest_{max_num + 1:03d}"

    dest = {
        "id": new_id,
        "name": name,
        "lat": lat,
        "lon": lon,
        "category": category,
    }
    await client.hset(DESTINATIONS_KEY, new_id, json.dumps(dest))
    logger.info("Destination added", destination_id=new_id, name=name)
    return dest


async def delete_destination(redis: RedisClient, destination_id: str) -> bool:
    """
    Delete a destination by ID.

    Implements [cycle-route-assessment:DestinationManagement/TS-03]

    Args:
        redis: RedisClient instance.
        destination_id: The destination ID to delete.

    Returns:
        True if deleted, False if not found.
    """
    client = await redis._ensure_connected()
    removed = await client.hdel(DESTINATIONS_KEY, destination_id)
    if removed:
        logger.info("Destination deleted", destination_id=destination_id)
    return bool(removed)
