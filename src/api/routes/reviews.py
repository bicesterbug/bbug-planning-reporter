"""
Reviews API router.

Implements [foundation-api:FR-001] - Submit review request
Implements [foundation-api:FR-002] - Validate application reference
Implements [foundation-api:FR-003] - Get review status
Implements [foundation-api:FR-004] - Get review result
Implements [foundation-api:FR-005] - List reviews
Implements [foundation-api:FR-006] - Cancel review
Implements [foundation-api:FR-014] - Prevent duplicate reviews
"""

from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from ulid import ULID

from src.api.dependencies import ArqPoolDep, RedisClientDep
from src.api.schemas import (
    ErrorResponse,
    ReviewLinks,
    ReviewListResponse,
    ReviewProgressResponse,
    ReviewRequest,
    ReviewResponse,
    ReviewStatusResponse,
    ReviewSubmitResponse,
    ReviewSummary,
)
from src.shared.models import ReviewJob, ReviewOptions, ReviewStatus

logger = structlog.get_logger(__name__)

router = APIRouter()


def generate_review_id() -> str:
    """Generate a new review ID with rev_ prefix."""
    return f"rev_{ULID()}"


def make_error_response(code: str, message: str, details: dict[str, Any] | None = None) -> dict:
    """Create a standard error response dict."""
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
        }
    }


@router.post(
    "/reviews",
    response_model=ReviewSubmitResponse,
    status_code=202,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request"},
        409: {"model": ErrorResponse, "description": "Duplicate review"},
    },
)
async def submit_review(
    request: ReviewRequest,
    redis: RedisClientDep,
    arq_pool: ArqPoolDep,
) -> ReviewSubmitResponse:
    """
    Submit a new review request.

    Implements [foundation-api:FR-001] - Submit review request
    Implements [foundation-api:FR-014] - Prevent duplicate reviews
    Implements [foundation-api:ReviewRouter/TS-01] - Valid review submission
    Implements [foundation-api:ReviewRouter/TS-02] - Invalid reference format
    Implements [foundation-api:ReviewRouter/TS-03] - Duplicate review prevention
    """
    logger.info(
        "Review submission received",
        application_ref=request.application_ref,
    )

    # Check for existing active review (FR-014)
    if await redis.has_active_job_for_ref(request.application_ref):
        logger.warning(
            "Duplicate review rejected",
            application_ref=request.application_ref,
        )
        raise HTTPException(
            status_code=409,
            detail=make_error_response(
                code="review_already_exists",
                message=f"A review for application {request.application_ref} is already queued or processing",
                details={"application_ref": request.application_ref},
            ),
        )

    # Generate review ID
    review_id = generate_review_id()
    now = datetime.now(UTC)

    # Convert request models to internal models
    options = None
    if request.options:
        options = ReviewOptions(
            focus_areas=request.options.focus_areas,
            output_format=request.options.output_format,
            include_policy_matrix=request.options.include_policy_matrix,
            include_suggested_conditions=request.options.include_suggested_conditions,
            # Implements [review-scope-control:FR-004] - Map toggle fields
            include_consultation_responses=request.options.include_consultation_responses,
            include_public_comments=request.options.include_public_comments,
            # Implements [cycle-route-assessment:FR-006] - Per-review destination selection
            destination_ids=request.options.destination_ids,
        )

    # Create job record
    job = ReviewJob(
        review_id=review_id,
        application_ref=request.application_ref,
        status=ReviewStatus.QUEUED,
        options=options,
        created_at=now,
    )

    # Store in Redis
    await redis.store_job(job)

    logger.info(
        "Review job created",
        review_id=review_id,
        application_ref=request.application_ref,
    )

    # Enqueue job to arq worker queue
    await arq_pool.enqueue_job(
        "review_job",
        review_id=review_id,
        application_ref=request.application_ref,
        _queue_name="review_jobs",
    )

    logger.info(
        "Review job enqueued",
        review_id=review_id,
        application_ref=request.application_ref,
    )

    return ReviewSubmitResponse(
        review_id=review_id,
        application_ref=request.application_ref,
        status="queued",
        created_at=now,
        estimated_duration_seconds=180,
        links=ReviewLinks(
            self=f"/api/v1/reviews/{review_id}",
            status=f"/api/v1/reviews/{review_id}/status",
            cancel=f"/api/v1/reviews/{review_id}/cancel",
        ),
    )


@router.get(
    "/reviews/{review_id}",
    response_model=ReviewResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Review not found"},
    },
)
async def get_review(
    review_id: str,
    redis: RedisClientDep,
) -> ReviewResponse:
    """
    Get a review by ID.

    Returns full result if completed, or current status if processing.

    Implements [foundation-api:FR-004] - Get review result
    Implements [foundation-api:ReviewRouter/TS-04] - Get processing review
    Implements [foundation-api:ReviewRouter/TS-05] - Get completed review
    Implements [foundation-api:ReviewRouter/TS-06] - Get non-existent review
    """
    job = await redis.get_job(review_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=make_error_response(
                code="review_not_found",
                message=f"No review found with ID {review_id}",
                details={"review_id": review_id},
            ),
        )

    # Build progress if available
    progress = None
    if job.progress:
        progress = ReviewProgressResponse(
            phase=job.progress.phase.value if hasattr(job.progress.phase, 'value') else job.progress.phase,
            phase_number=job.progress.phase_number,
            total_phases=job.progress.total_phases,
            percent_complete=job.progress.percent_complete,
            detail=job.progress.detail,
        )

    # Get result if completed
    review_content = None
    application_info = None
    metadata = None
    site_boundary = None

    if job.status == ReviewStatus.COMPLETED or job.status == "completed":
        result = await redis.get_result(review_id)
        if result:
            review_content = result.get("review")
            application_info = result.get("application")
            metadata = result.get("metadata")
            # Implements [cycle-route-assessment:FR-010] - Site boundary from metadata
            if metadata:
                site_boundary = metadata.get("site_boundary")

    return ReviewResponse(
        review_id=job.review_id,
        application_ref=job.application_ref,
        status=job.status.value if hasattr(job.status, 'value') else job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        progress=progress,
        application=application_info,
        review=review_content,
        metadata=metadata,
        site_boundary=site_boundary,
        error=job.error,
    )


@router.get(
    "/reviews/{review_id}/status",
    response_model=ReviewStatusResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Review not found"},
    },
)
async def get_review_status(
    review_id: str,
    redis: RedisClientDep,
) -> ReviewStatusResponse:
    """
    Get lightweight status for a review.

    Implements [foundation-api:FR-003] - Get review status
    Implements [foundation-api:ReviewRouter/TS-10] - Lightweight status check
    """
    job = await redis.get_job(review_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=make_error_response(
                code="review_not_found",
                message=f"No review found with ID {review_id}",
                details={"review_id": review_id},
            ),
        )

    # Implements [review-progress:FR-001] - Return full progress on status endpoint
    progress = None
    if job.progress:
        progress = ReviewProgressResponse(
            phase=job.progress.phase.value if hasattr(job.progress.phase, 'value') else job.progress.phase,
            phase_number=job.progress.phase_number,
            total_phases=job.progress.total_phases,
            percent_complete=job.progress.percent_complete,
            detail=job.progress.detail,
        )

    return ReviewStatusResponse(
        review_id=job.review_id,
        status=job.status.value if hasattr(job.status, 'value') else job.status,
        progress=progress,
    )


@router.get(
    "/reviews",
    response_model=ReviewListResponse,
)
async def list_reviews(
    redis: RedisClientDep,
    status: str | None = Query(None, description="Filter by status"),
    application_ref: str | None = Query(None, description="Filter by application reference"),
    limit: int = Query(20, ge=1, le=100, description="Maximum results to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
) -> ReviewListResponse:
    """
    List reviews with optional filtering.

    Implements [foundation-api:FR-005] - List reviews
    Implements [foundation-api:ReviewRouter/TS-07] - List reviews with filter
    """
    # Convert status string to enum if provided
    status_filter = None
    if status:
        try:
            status_filter = ReviewStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=make_error_response(
                    code="invalid_status",
                    message=f"Invalid status: {status}",
                    details={"valid_statuses": [s.value for s in ReviewStatus]},
                ),
            )

    summaries, total = await redis.list_jobs(
        status=status_filter,
        application_ref=application_ref,
        limit=limit,
        offset=offset,
    )

    return ReviewListResponse(
        reviews=[
            ReviewSummary(
                review_id=s.review_id,
                application_ref=s.application_ref,
                status=s.status.value if hasattr(s.status, 'value') else s.status,
                overall_rating=s.overall_rating,
                created_at=s.created_at,
                completed_at=s.completed_at,
            )
            for s in summaries
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/reviews/{review_id}/cancel",
    response_model=ReviewStatusResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Review not found"},
        409: {"model": ErrorResponse, "description": "Cannot cancel"},
    },
)
async def cancel_review(
    review_id: str,
    redis: RedisClientDep,
) -> ReviewStatusResponse:
    """
    Cancel a queued or processing review.

    Implements [foundation-api:FR-006] - Cancel review
    Implements [foundation-api:ReviewRouter/TS-08] - Cancel queued review
    Implements [foundation-api:ReviewRouter/TS-09] - Cancel completed review
    """
    job = await redis.get_job(review_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=make_error_response(
                code="review_not_found",
                message=f"No review found with ID {review_id}",
                details={"review_id": review_id},
            ),
        )

    # Normalize status to string for comparison
    status_value = job.status.value if hasattr(job.status, 'value') else job.status

    # Check if review can be cancelled
    if status_value not in (ReviewStatus.QUEUED.value, ReviewStatus.PROCESSING.value):
        raise HTTPException(
            status_code=409,
            detail=make_error_response(
                code="cannot_cancel",
                message=f"Cannot cancel review with status '{status_value}'",
                details={"review_id": review_id, "current_status": status_value},
            ),
        )

    # Update status to cancelled
    await redis.update_job_status(review_id, ReviewStatus.CANCELLED)

    logger.info("Review cancelled", review_id=review_id)

    return ReviewStatusResponse(
        review_id=review_id,
        status="cancelled",
        progress=None,
    )


@router.get(
    "/reviews/{review_id}/site-boundary",
    responses={
        200: {"content": {"application/geo+json": {}}, "description": "GeoJSON site boundary"},
        404: {"model": ErrorResponse, "description": "Review or boundary not found"},
    },
)
async def get_site_boundary(
    review_id: str,
    redis: RedisClientDep,
) -> Any:
    """
    Get site boundary GeoJSON for a review.

    Returns the GeoJSON FeatureCollection with site polygon and centroid point.

    Implements [cycle-route-assessment:FR-010] - Site boundary from metadata
    Implements [cycle-route-assessment:SiteBoundaryEndpoint/TS-01] - Boundary returned
    Implements [cycle-route-assessment:SiteBoundaryEndpoint/TS-02] - No boundary
    Implements [cycle-route-assessment:SiteBoundaryEndpoint/TS-03] - Unknown review
    """
    job = await redis.get_job(review_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=make_error_response(
                code="review_not_found",
                message=f"No review found with ID {review_id}",
                details={"review_id": review_id},
            ),
        )

    # Get result to extract site_boundary from metadata
    result = await redis.get_result(review_id)
    site_boundary = None
    if result:
        metadata = result.get("metadata")
        if metadata:
            site_boundary = metadata.get("site_boundary")

    if site_boundary is None:
        raise HTTPException(
            status_code=404,
            detail=make_error_response(
                code="site_boundary_not_found",
                message=f"No site boundary data for review {review_id}",
                details={"review_id": review_id},
            ),
        )

    return JSONResponse(content=site_boundary, media_type="application/geo+json")
