"""
Worker job function for response letter generation.

Implements [response-letter:FR-001] - Orchestrates letter generation workflow
Implements [response-letter:FR-003] - Reads advocacy group config from environment
Implements [response-letter:FR-004] - Resolves case officer
Implements [response-letter:FR-010] - Calls Claude API for letter generation
Implements [response-letter:NFR-001] - Logs generation duration
Implements [response-letter:NFR-002] - Logs token counts
"""

import json
import os
import tempfile
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import anthropic
import structlog

from src.shared.redis_client import RedisClient
from src.shared.storage import StorageBackend, create_storage_backend
from src.worker.letter_prompt import build_letter_prompt
from src.worker.webhook import fire_webhook

logger = structlog.get_logger(__name__)

# Default advocacy group identity
_DEFAULT_GROUP_NAME = "Bicester Bike Users' Group"
_DEFAULT_GROUP_STYLISED = "Bicester BUG"
_DEFAULT_GROUP_SHORT = "BBUG"


def _get_group_config() -> tuple[str, str, str]:
    """Read advocacy group configuration from environment."""
    return (
        os.getenv("ADVOCACY_GROUP_NAME", _DEFAULT_GROUP_NAME),
        os.getenv("ADVOCACY_GROUP_STYLISED", _DEFAULT_GROUP_STYLISED),
        os.getenv("ADVOCACY_GROUP_SHORT", _DEFAULT_GROUP_SHORT),
    )


def _resolve_case_officer(
    request_case_officer: str | None,
    review_result: dict[str, Any],
) -> str | None:
    """
    Resolve case officer name from request override or review data.

    Priority: request override > review application data > None (fallback to generic)
    """
    if request_case_officer:
        return request_case_officer

    application = review_result.get("application") or {}
    return application.get("case_officer")


async def letter_job(
    ctx: dict[str, Any],
    letter_id: str,
    review_id: str,
) -> dict[str, Any]:
    """
    arq-compatible job function for letter generation.

    Implements [response-letter:LetterJob/TS-01] through [TS-07]

    Args:
        ctx: arq context containing Redis pool and other dependencies.
        letter_id: The letter record ID.
        review_id: The source review ID.

    Returns:
        Dict with letter generation results or error information.
    """
    logger.info("Starting letter job", letter_id=letter_id, review_id=review_id)

    redis_client: RedisClient | None = ctx.get("redis_client")
    start_time = time.monotonic()

    # Implements [s3-document-storage:FR-001] - Create storage backend from environment
    storage = create_storage_backend()

    # Fetch the letter record to get request parameters
    letter_record = None
    if redis_client:
        letter_record = await redis_client.get_letter(letter_id)

    if letter_record is None:
        logger.error("Letter record not found", letter_id=letter_id)
        return {"letter_id": letter_id, "status": "failed", "error": "letter_record_not_found"}

    # Fetch the review result
    review_result = None
    if redis_client:
        review_result = await redis_client.get_result(review_id)

    if review_result is None:
        logger.error("Review result not found", letter_id=letter_id, review_id=review_id)
        if redis_client:
            await redis_client.update_letter_status(
                letter_id,
                status="failed",
                error={"code": "review_result_not_found", "message": "Review result has expired or does not exist"},
                completed_at=datetime.now(UTC),
            )
        return {"letter_id": letter_id, "status": "failed", "error": "review_result_not_found"}

    try:
        # Read group config from environment
        group_name, group_stylised, group_short = _get_group_config()

        # Resolve case officer
        request_case_officer = letter_record.get("case_officer")
        case_officer = _resolve_case_officer(request_case_officer, review_result)

        # Parse letter_date from record
        letter_date_str = letter_record.get("letter_date")
        letter_date = date.fromisoformat(letter_date_str) if letter_date_str else None

        # Get stance and tone from letter record
        stance = letter_record.get("stance", "neutral")
        tone = letter_record.get("tone", "formal")

        # Build prompts
        system_prompt, user_prompt = build_letter_prompt(
            review_result=review_result,
            stance=stance,
            tone=tone,
            group_name=group_name,
            group_stylised=group_stylised,
            group_short=group_short,
            case_officer=case_officer,
            letter_date=letter_date,
        )

        # Call Claude API
        model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")
        api_key = os.getenv("ANTHROPIC_API_KEY")

        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")

        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=model,
            max_tokens=6000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        letter_content = message.content[0].text
        duration = time.monotonic() - start_time

        metadata = {
            "model": model,
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
            "processing_time_seconds": round(duration, 2),
        }

        logger.info(
            "Letter generated",
            letter_id=letter_id,
            review_id=review_id,
            model=model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            processing_time_seconds=round(duration, 2),
        )

        # Upload letter output files (both local and S3)
        letter_url = _upload_letter_output(
            letter_id=letter_id,
            application_ref=review_result.get("application_ref", "unknown"),
            letter_content=letter_content,
            metadata=metadata,
            storage=storage,
        )

        # Update letter record with content and output URL
        if redis_client:
            await redis_client.update_letter_status(
                letter_id,
                status="completed",
                content=letter_content,
                metadata=metadata,
                completed_at=datetime.now(UTC),
                output_url=letter_url,
            )
            # Set reverse lookup for review → letter URL
            if letter_url:
                await redis_client.set_review_letter_url(review_id, letter_url)

        # Implements [global-webhooks:FR-005] - letter.completed webhook
        fire_webhook("letter.completed", review_id, {
            "letter_id": letter_id,
            "review_id": review_id,
            "application_ref": review_result.get("application_ref", "unknown"),
            "stance": stance,
            "tone": tone,
            "content": letter_content,
            "metadata": metadata,
        })

        return {
            "letter_id": letter_id,
            "status": "completed",
            "metadata": metadata,
        }

    except anthropic.APIError as e:
        duration = time.monotonic() - start_time
        logger.error(
            "Claude API error during letter generation",
            letter_id=letter_id,
            error=str(e),
            processing_time_seconds=round(duration, 2),
        )

        if redis_client:
            await redis_client.update_letter_status(
                letter_id,
                status="failed",
                error={"code": "letter_generation_failed", "message": str(e)},
                completed_at=datetime.now(UTC),
            )

        return {"letter_id": letter_id, "status": "failed", "error": "letter_generation_failed"}

    except Exception as e:
        duration = time.monotonic() - start_time
        logger.exception(
            "Unexpected error during letter generation",
            letter_id=letter_id,
            processing_time_seconds=round(duration, 2),
        )

        if redis_client:
            await redis_client.update_letter_status(
                letter_id,
                status="failed",
                error={"code": "letter_generation_failed", "message": str(e)},
                completed_at=datetime.now(UTC),
            )

        return {"letter_id": letter_id, "status": "failed", "error": "letter_generation_failed"}


def _upload_letter_output(
    letter_id: str,
    application_ref: str,
    letter_content: str,
    metadata: dict[str, Any],
    storage: StorageBackend,
) -> str | None:
    """Upload letter JSON and markdown. Non-fatal on failure.

    Returns the public URL for the letter markdown, or None on failure.
    """
    safe_ref = application_ref.replace("/", "_")
    prefix = f"{safe_ref}/output"
    letter_url: str | None = None

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Letter JSON
            letter_data = {
                "letter_id": letter_id,
                "application_ref": application_ref,
                "content": letter_content,
                "metadata": metadata,
            }
            json_path = Path(tmpdir) / f"{letter_id}_letter.json"
            json_path.write_text(json.dumps(letter_data, indent=2, default=str))
            storage.upload(json_path, f"{prefix}/{letter_id}_letter.json")

            # Letter markdown
            md_key = f"{prefix}/{letter_id}_letter.md"
            md_path = Path(tmpdir) / f"{letter_id}_letter.md"
            md_path.write_text(letter_content)
            storage.upload(md_path, md_key)
            letter_url = storage.public_url(md_key)

        logger.info(
            "Letter output uploaded",
            letter_id=letter_id,
            application_ref=application_ref,
        )
    except Exception as e:
        logger.warning(
            "Failed to upload letter output",
            letter_id=letter_id,
            error=str(e),
        )

    return letter_url
