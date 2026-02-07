"""
Worker job functions for review processing.

Implements [agent-integration:FR-002] - Orchestrates complete review workflow
Implements [agent-integration:ITS-01] - Complete workflow with mocked MCP

Wires the AgentOrchestrator to the arq worker job handler.
"""

from datetime import UTC, datetime
from typing import Any

import redis.asyncio as redis
import structlog

from src.agent.orchestrator import AgentOrchestrator, ReviewResult
from src.shared.models import ReviewStatus
from src.shared.redis_client import RedisClient

logger = structlog.get_logger(__name__)


async def process_review(
    ctx: dict[str, Any],
    review_id: str,
    application_ref: str,
) -> dict[str, Any]:
    """
    Process a review job using the agent orchestrator.

    Implements [agent-integration:ITS-01] - Complete workflow with mocked MCP

    This is the main entry point for review processing from the arq worker.

    Args:
        ctx: arq context containing Redis pool and other dependencies.
        review_id: The review job ID.
        application_ref: The planning application reference.

    Returns:
        Dict with review results or error information.
    """
    logger.info(
        "Starting review job",
        review_id=review_id,
        application_ref=application_ref,
    )

    # Get Redis client from context
    redis_client: redis.Redis | None = ctx.get("redis")
    redis_wrapper: RedisClient | None = ctx.get("redis_client")

    # Update job status to processing
    if redis_wrapper:
        await redis_wrapper.update_job_status(
            review_id=review_id,
            status=ReviewStatus.PROCESSING,
            started_at=datetime.now(UTC),
        )

    # Implements [review-scope-control:FR-004] - Read options from Redis job
    options = None
    if redis_wrapper:
        job = await redis_wrapper.get_job(review_id)
        if job and job.options:
            options = job.options
            logger.info(
                "Review options loaded",
                review_id=review_id,
                include_consultation_responses=getattr(options, "include_consultation_responses", False),
                include_public_comments=getattr(options, "include_public_comments", False),
            )

    try:
        # Create and run orchestrator
        async with AgentOrchestrator(
            review_id=review_id,
            application_ref=application_ref,
            redis_client=redis_client,
            options=options,
        ) as orchestrator:
            result = await orchestrator.run()

        # Store result
        if result.success:
            return await _handle_success(result, redis_wrapper)
        else:
            return await _handle_failure(result, redis_wrapper)

    except Exception as e:
        logger.exception(
            "Review job failed with unexpected error",
            review_id=review_id,
        )

        # Update job status to failed
        if redis_wrapper:
            await redis_wrapper.update_job_status(
                review_id=review_id,
                status=ReviewStatus.FAILED,
                error={
                    "code": "internal_error",
                    "message": str(e),
                },
                completed_at=datetime.now(UTC),
            )

        return {
            "review_id": review_id,
            "status": "failed",
            "error": {
                "code": "internal_error",
                "message": str(e),
            },
        }


async def _handle_success(
    result: ReviewResult,
    redis_wrapper: RedisClient | None,
) -> dict[str, Any]:
    """Handle successful review completion."""
    logger.info(
        "Review completed successfully",
        review_id=result.review_id,
        application_ref=result.application_ref,
    )

    # Build the full review response
    review_data = {
        "review_id": result.review_id,
        "application_ref": result.application_ref,
        "status": "completed",
        "application": _serialize_application(result.application) if result.application else None,
        "review": result.review,
        "metadata": result.metadata,
    }

    # Store result in Redis
    if redis_wrapper:
        await redis_wrapper.store_result(result.review_id, review_data)

    return review_data


async def _handle_failure(
    result: ReviewResult,
    redis_wrapper: RedisClient | None,
) -> dict[str, Any]:
    """Handle failed review."""
    logger.warning(
        "Review failed",
        review_id=result.review_id,
        error=result.error,
    )

    error_data = {
        "code": "review_failed",
        "message": result.error or "Unknown error",
    }

    # Determine if it's a specific error type
    if result.error:
        if "Scraper error" in result.error:
            error_data["code"] = "scraper_error"
        elif "cancelled" in result.error.lower():
            error_data["code"] = "review_cancelled"
        elif "No documents" in result.error:
            error_data["code"] = "ingestion_failed"

    # Update job status
    if redis_wrapper:
        await redis_wrapper.update_job_status(
            review_id=result.review_id,
            status=ReviewStatus.FAILED,
            error=error_data,
            completed_at=datetime.now(UTC),
        )

    return {
        "review_id": result.review_id,
        "application_ref": result.application_ref,
        "status": "failed",
        "error": error_data,
        "metadata": result.metadata,
    }


def _serialize_application(app) -> dict[str, Any]:
    """Serialize ApplicationMetadata to dict."""
    if app is None:
        return {}

    return {
        "reference": app.reference,
        "address": app.address,
        "proposal": app.proposal,
        "applicant": app.applicant,
        "status": app.status,
        "date_validated": app.date_validated,
        "consultation_end": app.consultation_end,
        "documents_fetched": len(app.documents) if app.documents else 0,
    }


# arq worker function registration
# These are the functions that arq can call
async def review_job(ctx: dict[str, Any], review_id: str, application_ref: str) -> dict[str, Any]:
    """
    arq-compatible job function for review processing.

    This wrapper exists to provide the arq-expected signature and logging.
    """
    return await process_review(ctx, review_id, application_ref)
