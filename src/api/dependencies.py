"""
FastAPI dependencies for dependency injection.

Provides Redis client and other shared dependencies.
"""

import os
from typing import Annotated

from fastapi import Depends

from src.shared.redis_client import RedisClient

# Global Redis client instance
_redis_client: RedisClient | None = None


async def get_redis_client() -> RedisClient:
    """
    Get the Redis client instance.

    Creates and connects the client on first call.
    """
    global _redis_client
    if _redis_client is None:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        _redis_client = RedisClient(redis_url)
        await _redis_client.connect()
    return _redis_client


# Type alias for dependency injection
RedisClientDep = Annotated[RedisClient, Depends(get_redis_client)]
