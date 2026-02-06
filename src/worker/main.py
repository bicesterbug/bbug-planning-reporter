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


async def startup(_ctx: dict) -> None:
    """Called when worker starts."""
    logger.info("Worker starting up", component="worker")


async def shutdown(_ctx: dict) -> None:
    """Called when worker shuts down."""
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
    ]
    queue_name = "review_jobs"
    max_jobs = 10
    job_timeout = 600  # 10 minutes


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
