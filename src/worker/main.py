"""
Worker entry point for arq job processing.

Implements [foundation-api:NFR-002] - Job queue reliability
"""

import asyncio
import logging
import os

import structlog
from arq import create_pool
from arq.connections import RedisSettings

from src.worker.jobs import ingest_application_documents, ingest_directory, search_documents
from src.worker.letter_jobs import letter_job
from src.worker.review_jobs import review_job

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)


def get_redis_settings() -> RedisSettings:
    """Get Redis connection settings from environment."""
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    # Parse redis URL
    # Format: redis://host:port/db
    if redis_url.startswith("redis://"):
        redis_url = redis_url[8:]
    parts = redis_url.split("/")
    host_port = parts[0]
    database = int(parts[1]) if len(parts) > 1 else 0

    if ":" in host_port:
        host, port = host_port.split(":")
        port = int(port)
    else:
        host = host_port
        port = 6379

    return RedisSettings(host=host, port=port, database=database)


async def startup(ctx: dict) -> None:
    """Called when worker starts."""
    import redis.asyncio as aioredis

    from src.shared.redis_client import RedisClient

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    raw_redis = aioredis.from_url(redis_url, decode_responses=True)
    redis_client = RedisClient(redis_url)
    await redis_client.connect()

    ctx["redis"] = raw_redis
    ctx["redis_client"] = redis_client
    logger.info("Worker starting up", component="worker")


async def shutdown(ctx: dict) -> None:
    """Called when worker shuts down."""
    redis_client = ctx.get("redis_client")
    if redis_client:
        await redis_client.close()
    raw_redis = ctx.get("redis")
    if raw_redis:
        await raw_redis.aclose()
    logger.info("Worker shutting down", component="worker")


# Worker class configuration
class WorkerSettings:
    """arq worker settings."""

    redis_settings = get_redis_settings()
    on_startup = startup
    on_shutdown = shutdown
    functions = [
        ingest_application_documents,
        ingest_directory,
        search_documents,
        review_job,
        letter_job,
    ]
    queue_name = "review_jobs"
    max_jobs = 10
    job_timeout = 1800  # 30 minutes (large applications can have 300+ documents)


if __name__ == "__main__":
    # Run worker directly for development
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting worker in development mode", component="worker")

    async def run_worker():
        redis_settings = get_redis_settings()
        pool = await create_pool(redis_settings)
        logger.info("Connected to Redis", component="worker", host=redis_settings.host)
        # Keep running until interrupted
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("Worker interrupted", component="worker")
        finally:
            await pool.close()

    asyncio.run(run_worker())
