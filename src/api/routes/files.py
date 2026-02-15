"""
File serving router for local storage deployments.

Serves review output artefacts from the local /data/output/ directory.
When S3 is configured, all requests return 404 since files are served
directly from S3 public URLs.
"""

import os
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

logger = structlog.get_logger(__name__)

router = APIRouter()

OUTPUT_BASE_DIR = Path("/data/output")

CONTENT_TYPES = {
    ".json": "application/json",
    ".md": "text/markdown",
}


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
    "/files/{file_path:path}",
    responses={
        200: {
            "description": "File content",
            "content": {
                "application/json": {},
                "text/markdown": {},
                "application/octet-stream": {},
            },
        },
        400: {"description": "Invalid path"},
        404: {"description": "File not found"},
    },
)
async def serve_file(file_path: str) -> FileResponse:
    """
    Serve an output file from local storage.

    Only active when using LocalStorageBackend (no S3 configured).
    Validates paths to prevent directory traversal attacks.
    """
    # When S3 is configured, local file serving is disabled
    if os.getenv("S3_ENDPOINT_URL"):
        raise HTTPException(
            status_code=404,
            detail=make_error_response(
                code="local_files_not_available",
                message="File serving is not available when S3 storage is configured",
            ),
        )

    # Resolve and validate path against traversal
    resolved = (OUTPUT_BASE_DIR / file_path).resolve()
    if not resolved.is_relative_to(OUTPUT_BASE_DIR.resolve()):
        raise HTTPException(
            status_code=400,
            detail=make_error_response(
                code="invalid_path",
                message="Invalid file path",
                details={"path": file_path},
            ),
        )

    if not resolved.is_file():
        raise HTTPException(
            status_code=404,
            detail=make_error_response(
                code="file_not_found",
                message=f"File not found: {file_path}",
                details={"path": file_path},
            ),
        )

    content_type = CONTENT_TYPES.get(resolved.suffix, "application/octet-stream")
    return FileResponse(resolved, media_type=content_type)
