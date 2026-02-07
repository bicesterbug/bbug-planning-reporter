"""
FastAPI dependencies for dependency injection.

Provides Redis client, PolicyRegistry, arq pool, and other shared dependencies.
"""

import os
from typing import Annotated

import redis.asyncio as aioredis
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from fastapi import Depends

from src.shared.effective_date_resolver import EffectiveDateResolver
from src.shared.policy_registry import PolicyRegistry
from src.shared.redis_client import RedisClient

# Global instances
_redis_client: RedisClient | None = None
_raw_redis_client: aioredis.Redis | None = None
_policy_registry: PolicyRegistry | None = None
_effective_date_resolver: EffectiveDateResolver | None = None
_arq_pool: ArqRedis | None = None


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


async def get_raw_redis_client() -> aioredis.Redis:
    """
    Get a raw async Redis client for PolicyRegistry.

    Creates and connects the client on first call.
    """
    global _raw_redis_client
    if _raw_redis_client is None:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        _raw_redis_client = aioredis.from_url(
            redis_url,
            decode_responses=True,
            max_connections=10,
        )
    return _raw_redis_client


async def get_policy_registry() -> PolicyRegistry:
    """
    Get the PolicyRegistry instance.

    Creates the registry on first call.
    """
    global _policy_registry
    if _policy_registry is None:
        redis = await get_raw_redis_client()
        _policy_registry = PolicyRegistry(redis)
    return _policy_registry


async def get_effective_date_resolver() -> EffectiveDateResolver:
    """
    Get the EffectiveDateResolver instance.

    Creates the resolver on first call.
    """
    global _effective_date_resolver
    if _effective_date_resolver is None:
        registry = await get_policy_registry()
        _effective_date_resolver = EffectiveDateResolver(registry)
    return _effective_date_resolver


async def get_arq_pool() -> ArqRedis:
    """
    Get an arq connection pool for job enqueueing.

    Creates the pool on first call.
    """
    global _arq_pool
    if _arq_pool is None:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        # Parse redis URL for arq RedisSettings
        url_part = redis_url.removeprefix("redis://")
        parts = url_part.split("/")
        host_port = parts[0]
        database = int(parts[1]) if len(parts) > 1 else 0
        if ":" in host_port:
            host, port_str = host_port.split(":")
            port = int(port_str)
        else:
            host = host_port
            port = 6379
        _arq_pool = await create_pool(RedisSettings(host=host, port=port, database=database))
    return _arq_pool


# Type aliases for dependency injection
RedisClientDep = Annotated[RedisClient, Depends(get_redis_client)]
PolicyRegistryDep = Annotated[PolicyRegistry, Depends(get_policy_registry)]
EffectiveDateResolverDep = Annotated[EffectiveDateResolver, Depends(get_effective_date_resolver)]
ArqPoolDep = Annotated[ArqRedis, Depends(get_arq_pool)]
