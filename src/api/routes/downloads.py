"""
Review download router for exporting reviews in various formats.

Implements [api-hardening:FR-005] - Download review as Markdown
Implements [api-hardening:FR-006] - Download review as JSON
Implements [api-hardening:FR-007] - Download review as PDF
Implements [api-hardening:NFR-006] - PDF generation quality

Implements test scenarios:
- [api-hardening:ReviewDownloadRouter/TS-01] Download as Markdown
- [api-hardening:ReviewDownloadRouter/TS-02] Download as JSON
- [api-hardening:ReviewDownloadRouter/TS-03] Download as PDF
- [api-hardening:ReviewDownloadRouter/TS-04] Default format
- [api-hardening:ReviewDownloadRouter/TS-05] Invalid format
- [api-hardening:ReviewDownloadRouter/TS-06] Incomplete review
- [api-hardening:ReviewDownloadRouter/TS-07] Non-existent review
- [api-hardening:ReviewDownloadRouter/TS-08] Content-Disposition header
"""

import json
from enum import StrEnum
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from src.api.dependencies import RedisClientDep
from src.api.schemas import ErrorResponse
from src.api.services.pdf_generator import PDFGenerator
from src.shared.models import ReviewStatus

logger = structlog.get_logger(__name__)

router = APIRouter()


class DownloadFormat(StrEnum):
    """Supported download formats."""

    MARKDOWN = "markdown"
    JSON = "json"
    PDF = "pdf"


def make_error_response(code: str, message: str, details: dict[str, Any] | None = None) -> dict:
    """Create a standard error response dict."""
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
        }
    }


@router.get(
    "/reviews/{review_id}/download",
    responses={
        200: {
            "description": "Review download",
            "content": {
                "text/markdown": {},
                "application/json": {},
                "application/pdf": {},
            },
        },
        400: {"model": ErrorResponse, "description": "Invalid request"},
        404: {"model": ErrorResponse, "description": "Review not found"},
    },
)
async def download_review(
    review_id: str,
    redis: RedisClientDep,
    format: DownloadFormat = Query(
        default=DownloadFormat.MARKDOWN,
        description="Output format (markdown, json, pdf)",
    ),
) -> Response:
    """
    Download a completed review in the specified format.

    Implements [api-hardening:FR-005], [api-hardening:FR-006], [api-hardening:FR-007]
    Implements [api-hardening:ReviewDownloadRouter/TS-01] through [TS-08]
    """
    logger.info("Download request", review_id=review_id, format=format.value)

    # Get the job to check status
    job = await redis.get_job(review_id)
    if job is None:
        logger.warning("Review not found for download", review_id=review_id)
        raise HTTPException(
            status_code=404,
            detail=make_error_response(
                code="review_not_found",
                message=f"No review found with ID {review_id}",
                details={"review_id": review_id},
            ),
        )

    # Check if review is complete
    status_value = job.status.value if isinstance(job.status, ReviewStatus) else job.status
    if status_value != ReviewStatus.COMPLETED.value:
        logger.warning(
            "Review not complete for download",
            review_id=review_id,
            status=status_value,
        )
        raise HTTPException(
            status_code=400,
            detail=make_error_response(
                code="review_incomplete",
                message=f"Cannot download review with status '{status_value}'. Wait for completion.",
                details={"review_id": review_id, "status": status_value},
            ),
        )

    # Get the result
    result = await redis.get_result(review_id)
    if result is None:
        logger.error("Review marked complete but no result", review_id=review_id)
        raise HTTPException(
            status_code=500,
            detail=make_error_response(
                code="internal_error",
                message="Review result not found",
            ),
        )

    # Get review content
    review_content = result.get("review", {})
    application_ref = job.application_ref

    # Generate response based on format
    if format == DownloadFormat.MARKDOWN:
        return _markdown_response(review_id, application_ref, review_content)
    elif format == DownloadFormat.JSON:
        return _json_response(review_id, result)
    elif format == DownloadFormat.PDF:
        return _pdf_response(review_id, application_ref, review_content)
    else:
        # This shouldn't happen due to enum validation, but just in case
        raise HTTPException(
            status_code=400,
            detail=make_error_response(
                code="invalid_download_format",
                message=f"Unsupported format: {format}",
                details={"supported_formats": ["markdown", "json", "pdf"]},
            ),
        )


def _markdown_response(review_id: str, application_ref: str, review_content: dict) -> Response:
    """Create Markdown download response."""
    # Get full markdown from review, or generate from content
    markdown = review_content.get("full_markdown", "")
    if not markdown:
        markdown = _generate_markdown_from_content(application_ref, review_content)

    return Response(
        content=markdown.encode("utf-8"),
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="review-{review_id}.md"',
        },
    )


def _json_response(review_id: str, result: dict) -> Response:
    """Create JSON download response."""
    json_content = json.dumps(result, indent=2, ensure_ascii=False)

    return Response(
        content=json_content.encode("utf-8"),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="review-{review_id}.json"',
        },
    )


def _pdf_response(review_id: str, application_ref: str, review_content: dict) -> Response:
    """Create PDF download response."""
    # Get markdown content
    markdown = review_content.get("full_markdown", "")
    if not markdown:
        markdown = _generate_markdown_from_content(application_ref, review_content)

    # Generate PDF
    generator = PDFGenerator()
    pdf_bytes = generator.generate(markdown, title=f"Review for {application_ref}")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="review-{review_id}.pdf"',
        },
    )


def _generate_markdown_from_content(application_ref: str, review_content: dict) -> str:
    """Generate Markdown from review content if full_markdown not available."""
    lines = [
        f"# Cycle Advocacy Review: {application_ref}",
        "",
    ]

    overall_rating = review_content.get("overall_rating", "Unknown")
    lines.append(f"## Overall Rating: {overall_rating.upper()}")
    lines.append("")

    if summary := review_content.get("summary"):
        lines.append("## Summary")
        lines.append("")
        lines.append(summary)
        lines.append("")

    if aspects := review_content.get("aspects"):
        lines.append("## Aspect Assessments")
        lines.append("")
        for aspect in aspects:
            name = aspect.get("name", "Unknown")
            rating = aspect.get("rating", "Unknown")
            lines.append(f"### {name}: {rating.upper()}")
            if key_issue := aspect.get("key_issue"):
                lines.append(f"**Key Issue:** {key_issue}")
            if detail := aspect.get("detail"):
                lines.append("")
                lines.append(detail)
            lines.append("")

    if recommendations := review_content.get("recommendations"):
        lines.append("## Recommendations")
        lines.append("")
        for rec in recommendations:
            lines.append(f"- {rec}")
        lines.append("")

    return "\n".join(lines)
