"""
Health check endpoint.

Implements [foundation-api:FR-013] - Health check endpoint
Implements [foundation-api:HealthRouter/TS-01] - Healthy system returns status
Implements [foundation-api:HealthRouter/TS-02] - Degraded system reports issues
"""

import os
from typing import Any

import redis.asyncio as redis
from fastapi import APIRouter

router = APIRouter()


async def check_redis_connection() -> str:
    """Check if Redis is reachable."""
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        client = redis.from_url(redis_url, decode_responses=True)
        await client.ping()
        await client.aclose()
        return "connected"
    except Exception:
        return "disconnected"


@router.get("/health")
async def health_check() -> dict[str, Any]:
    """
    Health check endpoint reporting service connectivity.

    Returns:
        Health status with service connection states.
    """
    redis_status = await check_redis_connection()

    # Determine overall health
    all_healthy = redis_status == "connected"
    status = "healthy" if all_healthy else "degraded"

    return {
        "status": status,
        "services": {
            "redis": redis_status,
            # MCP servers will be added as they're implemented
            # "cherwell_scraper_mcp": "not_implemented",
            # "document_store_mcp": "not_implemented",
            # "policy_kb_mcp": "not_implemented",
        },
        "version": "0.1.0",
    }
