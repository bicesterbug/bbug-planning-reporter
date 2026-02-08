"""
Letters API router.

Implements [response-letter:FR-001] - Generate response letter
Implements [response-letter:FR-004] - Case officer addressing
Implements [response-letter:FR-006] - Letter date
Implements [response-letter:FR-008] - Retrieve generated letter
Implements [response-letter:NFR-004] - Authentication (via middleware)
"""

from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from ulid import ULID

from src.api.dependencies import ArqPoolDep, RedisClientDep
from src.api.schemas.letter import (
    LetterRequest,
    LetterResponse,
    LetterStatus,
    LetterSubmitResponse,
)

logger = structlog.get_logger(__name__)

router = APIRouter()


def _generate_letter_id() -> str:
    """Generate a new letter ID with ltr_ prefix."""
    return f"ltr_{ULID()}"


def _make_error_response(code: str, message: str, details: dict[str, Any] | None = None) -> dict:
    """Create a standard error response dict."""
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
        }
    }


@router.post(
    "/reviews/{review_id}/letter",
    response_model=LetterSubmitResponse,
    status_code=202,
    responses={
        400: {"description": "Review incomplete"},
        404: {"description": "Review not found"},
        422: {"description": "Validation error"},
    },
)
async def generate_letter(
    review_id: str,
    request: LetterRequest,
    redis: RedisClientDep,
    arq_pool: ArqPoolDep,
) -> LetterSubmitResponse:
    """
    Generate a response letter for a completed review.

    Implements [response-letter:LettersRouter/TS-01] through [TS-03], [TS-08], [TS-09]
    """
    logger.info(
        "Letter generation requested",
        review_id=review_id,
        stance=request.stance,
        tone=request.tone,
    )

    # Validate review exists
    job = await redis.get_job(review_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=_make_error_response(
                code="review_not_found",
                message=f"No review found with ID {review_id}",
                details={"review_id": review_id},
            ),
        )

    # Validate review is completed
    status_value = job.status.value if hasattr(job.status, "value") else job.status
    if status_value != "completed":
        raise HTTPException(
            status_code=400,
            detail=_make_error_response(
                code="review_incomplete",
                message=f"Review {review_id} has status '{status_value}', must be 'completed'",
                details={"review_id": review_id, "current_status": status_value},
            ),
        )

    # Generate letter ID and timestamp
    letter_id = _generate_letter_id()
    now = datetime.now(UTC)

    # Build initial letter record
    letter_record: dict[str, Any] = {
        "letter_id": letter_id,
        "review_id": review_id,
        "application_ref": job.application_ref,
        "stance": request.stance.value if hasattr(request.stance, "value") else request.stance,
        "tone": request.tone.value if hasattr(request.tone, "value") else request.tone,
        "case_officer": request.case_officer,
        "letter_date": request.letter_date.isoformat() if request.letter_date else None,
        "status": LetterStatus.GENERATING.value,
        "content": None,
        "metadata": None,
        "error": None,
        "created_at": now.isoformat(),
        "completed_at": None,
    }

    # Store in Redis
    await redis.store_letter(letter_id, letter_record)

    # Enqueue letter generation job
    await arq_pool.enqueue_job(
        "letter_job",
        letter_id=letter_id,
        review_id=review_id,
        _queue_name="review_jobs",
    )

    logger.info(
        "Letter job enqueued",
        letter_id=letter_id,
        review_id=review_id,
    )

    return LetterSubmitResponse(
        letter_id=letter_id,
        review_id=review_id,
        status=LetterStatus.GENERATING,
        created_at=now,
        links={"self": f"/api/v1/letters/{letter_id}"},
    )


@router.get(
    "/letters/{letter_id}",
    response_model=LetterResponse,
    responses={
        404: {"description": "Letter not found"},
    },
)
async def get_letter(
    letter_id: str,
    redis: RedisClientDep,
) -> LetterResponse:
    """
    Retrieve a letter by ID.

    Implements [response-letter:LettersRouter/TS-04] through [TS-07]
    """
    letter = await redis.get_letter(letter_id)
    if letter is None:
        raise HTTPException(
            status_code=404,
            detail=_make_error_response(
                code="letter_not_found",
                message=f"No letter found with ID {letter_id}",
                details={"letter_id": letter_id},
            ),
        )

    # Parse metadata if present
    metadata = letter.get("metadata")

    return LetterResponse(
        letter_id=letter["letter_id"],
        review_id=letter["review_id"],
        application_ref=letter["application_ref"],
        status=letter["status"],
        stance=letter["stance"],
        tone=letter["tone"],
        case_officer=letter.get("case_officer"),
        letter_date=letter.get("letter_date"),
        content=letter.get("content"),
        metadata=metadata,
        error=letter.get("error"),
        created_at=letter["created_at"],
        completed_at=letter.get("completed_at"),
    )
