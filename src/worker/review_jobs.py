"""
Worker job functions for review processing.

Implements [agent-integration:FR-002] - Orchestrates complete review workflow
Implements [agent-integration:ITS-01] - Complete workflow with mocked MCP

Wires the AgentOrchestrator to the arq worker job handler.
"""

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import redis.asyncio as redis
import structlog

from src.agent.orchestrator import AgentOrchestrator, ReviewResult
from src.shared.models import ReviewStatus
from src.shared.redis_client import RedisClient
from src.shared.storage import StorageBackend, create_storage_backend
from src.worker.webhook import fire_webhook

logger = structlog.get_logger(__name__)


async def process_review(
    ctx: dict[str, Any],
    review_id: str,
    application_ref: str,
    previous_review_id: str | None = None,
) -> dict[str, Any]:
    """
    Process a review job using the agent orchestrator.

    Implements [agent-integration:ITS-01] - Complete workflow with mocked MCP
    Implements [global-webhooks:FR-001] - No per-request webhook config

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

    # Implements [s3-document-storage:FR-001] - Create storage backend from environment
    storage = create_storage_backend()

    try:
        # Create and run orchestrator
        async with AgentOrchestrator(
            review_id=review_id,
            application_ref=application_ref,
            redis_client=redis_client,
            options=options,
            storage_backend=storage,
            previous_review_id=previous_review_id,
        ) as orchestrator:
            result = await orchestrator.run()

        # Store result
        if result.success:
            return await _handle_success(result, redis_wrapper, storage)
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

        # Implements [global-webhooks:FR-006] - Structured error in webhook
        fire_webhook("review.failed", review_id, {
            "application_ref": application_ref,
            "error": {"code": "internal_error", "message": str(e)},
        })

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
    storage: StorageBackend | None = None,
) -> dict[str, Any]:
    """Handle successful review completion.

    Implements [global-webhooks:FR-003] - review.completed with full data
    Implements [global-webhooks:FR-004] - review.completed.markdown with markdown
    """
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

    # Upload output files and record URLs (both local and S3)
    output_urls: dict[str, str | None] = {
        "review_json": None, "review_md": None, "routes_json": None,
    }
    if storage:
        output_urls = _upload_review_output(result, review_data, storage)
    review_data["output_urls"] = output_urls

    # Store result in Redis (includes output_urls)
    if redis_wrapper:
        await redis_wrapper.store_result(result.review_id, review_data)

    # Implements [global-webhooks:FR-003] - review.completed with full review data
    overall_rating = (result.review or {}).get("overall_rating")
    fire_webhook("review.completed", result.review_id, {
        "application_ref": result.application_ref,
        "overall_rating": overall_rating,
        "review_url": f"/api/reviews/{result.review_id}",
        "application": review_data.get("application"),
        "review": review_data.get("review"),
        "metadata": review_data.get("metadata"),
    })

    # Implements [global-webhooks:FR-004] - review.completed.markdown
    full_markdown = (result.review or {}).get("full_markdown")
    fire_webhook("review.completed.markdown", result.review_id, {
        "application_ref": result.application_ref,
        "full_markdown": full_markdown,
    })

    return review_data


def _upload_review_output(
    result: ReviewResult,
    review_data: dict[str, Any],
    storage: StorageBackend,
) -> dict[str, str | None]:
    """Upload review JSON, markdown, and routes JSON. Non-fatal on failure.

    Returns a dict of public URLs for each output artefact.
    """
    safe_ref = result.application_ref.replace("/", "_")
    prefix = f"{safe_ref}/output"
    urls: dict[str, str | None] = {
        "review_json": None, "review_md": None, "routes_json": None,
    }

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Review JSON
            key = f"{prefix}/{result.review_id}_review.json"
            json_path = Path(tmpdir) / f"{result.review_id}_review.json"
            json_path.write_text(json.dumps(review_data, indent=2, default=str))
            storage.upload(json_path, key)
            urls["review_json"] = storage.public_url(key)

            # Review markdown
            full_markdown = (review_data.get("review") or {}).get("full_markdown")
            if full_markdown:
                key = f"{prefix}/{result.review_id}_review.md"
                md_path = Path(tmpdir) / f"{result.review_id}_review.md"
                md_path.write_text(full_markdown)
                storage.upload(md_path, key)
                urls["review_md"] = storage.public_url(key)

            # Routes JSON
            route_assessments = (review_data.get("review") or {}).get(
                "route_assessments", []
            )
            key = f"{prefix}/{result.review_id}_routes.json"
            routes_path = Path(tmpdir) / f"{result.review_id}_routes.json"
            routes_path.write_text(
                json.dumps(route_assessments or [], indent=2, default=str)
            )
            storage.upload(routes_path, key)
            urls["routes_json"] = storage.public_url(key)

        logger.info(
            "Review output uploaded",
            review_id=result.review_id,
            application_ref=result.application_ref,
        )
    except Exception as e:
        logger.warning(
            "Failed to upload review output",
            review_id=result.review_id,
            error=str(e),
        )

    return urls


async def _handle_failure(
    result: ReviewResult,
    redis_wrapper: RedisClient | None,
) -> dict[str, Any]:
    """Handle failed review.

    Implements [global-webhooks:FR-006] - review.failed with structured error
    """
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

    # Implements [global-webhooks:FR-006] - Structured error in webhook
    fire_webhook("review.failed", result.review_id, {
        "application_ref": result.application_ref,
        "error": error_data,
    })

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
async def review_job(
    ctx: dict[str, Any],
    review_id: str,
    application_ref: str,
    previous_review_id: str | None = None,
) -> dict[str, Any]:
    """
    arq-compatible job function for review processing.

    This wrapper exists to provide the arq-expected signature and logging.
    """
    return await process_review(ctx, review_id, application_ref, previous_review_id=previous_review_id)
