"""
Policy Knowledge Base API router.

Implements [policy-knowledge-base:FR-001] - Create policy document
Implements [policy-knowledge-base:FR-002] - Upload revision with PDF
Implements [policy-knowledge-base:FR-007] - List policies
Implements [policy-knowledge-base:FR-008] - Get policy detail
Implements [policy-knowledge-base:FR-009] - Get effective snapshot
Implements [policy-knowledge-base:FR-010] - Update revision metadata
Implements [policy-knowledge-base:FR-011] - Delete revision
Implements [policy-knowledge-base:FR-012] - Re-index revision
"""

import contextlib
import os
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from src.api.dependencies import EffectiveDateResolverDep, PolicyRegistryDep
from src.api.schemas import (
    CreatePolicyRequest,
    EffectivePolicySnapshot,
    EffectiveSnapshotResponse,
    ErrorResponse,
    PolicyCategory,
    PolicyDocumentDetail,
    PolicyListResponse,
    UpdatePolicyRequest,
)
from src.api.schemas.policy import (
    PolicyRevisionDetail,
    RevisionCreateResponse,
    RevisionDeleteResponse,
    RevisionStatus,
    RevisionStatusResponse,
    UpdateRevisionRequest,
)
from src.shared.policy_registry import (
    CannotDeleteSoleRevisionError,
    PolicyAlreadyExistsError,
    PolicyNotFoundError,
    RevisionNotFoundError,
    RevisionOverlapError,
)

logger = structlog.get_logger(__name__)

router = APIRouter()


def make_error_response(code: str, message: str, details: dict[str, Any] | None = None) -> dict:
    """Create a standard error response dict."""
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
        }
    }


# =============================================================================
# Policy Document Endpoints
# =============================================================================


@router.post(
    "/policies",
    response_model=PolicyDocumentDetail,
    status_code=201,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request"},
        409: {"model": ErrorResponse, "description": "Policy already exists"},
    },
)
async def create_policy(
    request: CreatePolicyRequest,
    registry: PolicyRegistryDep,
) -> PolicyDocumentDetail:
    """
    Register a new policy document.

    Implements [policy-knowledge-base:FR-001] - Create policy document
    Implements [policy-knowledge-base:PolicyRouter/TS-01] - Register new policy
    Implements [policy-knowledge-base:PolicyRouter/TS-02] - Duplicate source rejected
    Implements [policy-knowledge-base:PolicyRouter/TS-03] - Invalid source format
    """
    logger.info(
        "Policy creation requested",
        source=request.source,
        category=request.category.value,
    )

    try:
        record = await registry.create_policy(
            source=request.source,
            title=request.title,
            category=request.category,
            description=request.description,
        )
    except PolicyAlreadyExistsError:
        logger.warning("Duplicate policy rejected", source=request.source)
        raise HTTPException(
            status_code=409,
            detail=make_error_response(
                code="policy_already_exists",
                message=f"A policy with source '{request.source}' already exists",
                details={"source": request.source},
            ),
        )

    logger.info("Policy created", source=request.source)

    # Return full detail (no revisions yet)
    return PolicyDocumentDetail(
        source=record.source,
        title=record.title,
        description=record.description,
        category=record.category,
        revisions=[],
        current_revision=None,
        revision_count=0,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.get(
    "/policies",
    response_model=PolicyListResponse,
)
async def list_policies(
    registry: PolicyRegistryDep,
    category: PolicyCategory | None = Query(None, description="Filter by category"),
    source: str | None = Query(None, description="Filter by source slug"),
) -> PolicyListResponse:
    """
    List all registered policy documents.

    Implements [policy-knowledge-base:FR-007] - List policies
    Implements [policy-knowledge-base:PolicyRouter/TS-07] - List policies
    """
    policies = await registry.list_policies(
        category=category,
        source_filter=source,
    )

    return PolicyListResponse(
        policies=policies,
        total=len(policies),
    )


@router.get(
    "/policies/effective",
    response_model=EffectiveSnapshotResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid date format"},
    },
)
async def get_effective_snapshot(
    resolver: EffectiveDateResolverDep,
    effective_date: date = Query(..., alias="date", description="Date to get effective snapshot for"),
) -> EffectiveSnapshotResponse:
    """
    Get policy snapshot showing which revisions were in force on a given date.

    Implements [policy-knowledge-base:FR-009] - Get effective snapshot
    Implements [policy-knowledge-base:PolicyRouter/TS-10] - Get effective snapshot
    Implements [policy-knowledge-base:PolicyRouter/TS-11] - Invalid date format
    """
    snapshot = await resolver.resolve_snapshot(effective_date)

    policies = [
        EffectivePolicySnapshot(
            source=p.source,
            title=p.title,
            category=p.category,
            effective_revision=p.effective_revision,
        )
        for p in snapshot.policies
    ]

    return EffectiveSnapshotResponse(
        effective_date=snapshot.effective_date,
        policies=policies,
        policies_not_yet_effective=snapshot.policies_not_yet_effective,
    )


@router.get(
    "/policies/{source}",
    response_model=PolicyDocumentDetail,
    responses={
        404: {"model": ErrorResponse, "description": "Policy not found"},
    },
)
async def get_policy(
    source: str,
    registry: PolicyRegistryDep,
) -> PolicyDocumentDetail:
    """
    Get a policy document with all its revisions.

    Implements [policy-knowledge-base:FR-008] - Get policy detail
    Implements [policy-knowledge-base:PolicyRouter/TS-08] - Get policy detail
    Implements [policy-knowledge-base:PolicyRouter/TS-09] - Policy not found
    """
    policy = await registry.get_policy_with_revisions(source)

    if policy is None:
        raise HTTPException(
            status_code=404,
            detail=make_error_response(
                code="policy_not_found",
                message=f"No policy found with source '{source}'",
                details={"source": source},
            ),
        )

    return policy


@router.patch(
    "/policies/{source}",
    response_model=PolicyDocumentDetail,
    responses={
        404: {"model": ErrorResponse, "description": "Policy not found"},
    },
)
async def update_policy(
    source: str,
    request: UpdatePolicyRequest,
    registry: PolicyRegistryDep,
) -> PolicyDocumentDetail:
    """
    Update policy document metadata.

    Implements [policy-knowledge-base:PolicyRouter/TS-08] - Update policy metadata
    """
    try:
        await registry.update_policy(
            source=source,
            title=request.title,
            description=request.description,
            category=request.category,
        )
    except PolicyNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=make_error_response(
                code="policy_not_found",
                message=f"No policy found with source '{source}'",
                details={"source": source},
            ),
        )

    # Return updated policy with revisions
    policy = await registry.get_policy_with_revisions(source)
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found after update")

    logger.info("Policy updated", source=source)
    return policy


# =============================================================================
# Policy Revision Endpoints
# =============================================================================


def _generate_revision_id(source: str, effective_from: date) -> str:
    """Generate revision ID in format rev_{SOURCE}_{YYYY}_{MM}."""
    return f"rev_{source}_{effective_from.year}_{effective_from.month:02d}"


def _get_policy_data_dir() -> Path:
    """Get the policy data directory from environment or default."""
    return Path(os.getenv("POLICY_DATA_DIR", "/data/policy"))


@router.post(
    "/policies/{source}/revisions",
    response_model=RevisionCreateResponse,
    status_code=202,
    responses={
        404: {"model": ErrorResponse, "description": "Policy not found"},
        409: {"model": ErrorResponse, "description": "Revision overlap"},
        422: {"model": ErrorResponse, "description": "Unsupported file type"},
    },
)
async def upload_revision(
    source: str,
    registry: PolicyRegistryDep,
    file: UploadFile = File(..., description="PDF file to upload"),
    version_label: str = Form(..., description="Human-readable version (e.g., 'December 2024')"),
    effective_from: date = Form(..., description="Date from which revision is in force"),
    effective_to: date | None = Form(None, description="Date until which revision is in force"),
    notes: str | None = Form(None, description="Notes about this revision"),
) -> RevisionCreateResponse:
    """
    Upload a new policy revision with PDF file.

    Implements [policy-knowledge-base:FR-002] - Upload revision with PDF
    Implements [policy-knowledge-base:PolicyRouter/TS-04] - Upload revision
    Implements [policy-knowledge-base:PolicyRouter/TS-05] - Upload non-PDF rejected
    Implements [policy-knowledge-base:PolicyRouter/TS-06] - Overlapping dates rejected
    """
    logger.info(
        "Revision upload requested",
        source=source,
        version_label=version_label,
        effective_from=str(effective_from),
        filename=file.filename,
    )

    # Validate file type
    if file.content_type != "application/pdf" and not (
        file.filename and file.filename.lower().endswith(".pdf")
    ):
        raise HTTPException(
            status_code=422,
            detail=make_error_response(
                code="unsupported_file_type",
                message="Only PDF files are supported",
                details={"content_type": file.content_type, "filename": file.filename},
            ),
        )

    # Check policy exists
    policy = await registry.get_policy(source)
    if policy is None:
        raise HTTPException(
            status_code=404,
            detail=make_error_response(
                code="policy_not_found",
                message=f"No policy found with source '{source}'",
                details={"source": source},
            ),
        )

    # Generate revision ID
    revision_id = _generate_revision_id(source, effective_from)

    # Save file to disk
    data_dir = _get_policy_data_dir()
    revision_dir = data_dir / source / revision_id
    revision_dir.mkdir(parents=True, exist_ok=True)
    file_path = revision_dir / (file.filename or f"{revision_id}.pdf")

    content = await file.read()
    file_path.write_bytes(content)
    file_size = len(content)

    logger.info(
        "Revision file saved",
        source=source,
        revision_id=revision_id,
        file_path=str(file_path),
        file_size=file_size,
    )

    # Create revision in registry
    try:
        await registry.create_revision(
            source=source,
            revision_id=revision_id,
            version_label=version_label,
            effective_from=effective_from,
            effective_to=effective_to,
            notes=notes,
            file_path=str(file_path),
            file_size_bytes=file_size,
        )
    except RevisionOverlapError as e:
        # Clean up saved file
        file_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=409,
            detail=make_error_response(
                code="revision_overlap",
                message=str(e),
                details={"source": source, "effective_from": str(effective_from)},
            ),
        )
    except PolicyNotFoundError:
        # Clean up saved file
        file_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=404,
            detail=make_error_response(
                code="policy_not_found",
                message=f"No policy found with source '{source}'",
                details={"source": source},
            ),
        )

    # Generate a job ID for tracking (ingestion will be implemented in Phase 3)
    ingestion_job_id = f"job_{uuid.uuid4().hex[:12]}"

    logger.info(
        "Revision created, ingestion queued",
        source=source,
        revision_id=revision_id,
        ingestion_job_id=ingestion_job_id,
    )

    # Check for supersession side effects
    side_effects = None
    revisions = await registry.list_revisions(source)
    superseded = [r for r in revisions if r.status == RevisionStatus.SUPERSEDED and r.revision_id != revision_id]
    if superseded:
        # Find the one that was just superseded (has effective_to = effective_from - 1 day)
        from datetime import timedelta
        expected_effective_to = effective_from - timedelta(days=1)
        for r in superseded:
            if r.effective_to == expected_effective_to:
                side_effects = {
                    "superseded_revision": r.revision_id,
                    "superseded_effective_to": str(r.effective_to),
                }
                break

    return RevisionCreateResponse(
        source=source,
        revision_id=revision_id,
        version_label=version_label,
        effective_from=effective_from,
        effective_to=effective_to,
        status=RevisionStatus.PROCESSING,
        ingestion_job_id=ingestion_job_id,
        links={
            "self": f"/api/v1/policies/{source}/revisions/{revision_id}",
            "status": f"/api/v1/policies/{source}/revisions/{revision_id}/status",
            "policy": f"/api/v1/policies/{source}",
        },
        side_effects=side_effects,
    )


@router.get(
    "/policies/{source}/revisions/{revision_id}",
    response_model=PolicyRevisionDetail,
    responses={
        404: {"model": ErrorResponse, "description": "Policy or revision not found"},
    },
)
async def get_revision(
    source: str,
    revision_id: str,
    registry: PolicyRegistryDep,
) -> PolicyRevisionDetail:
    """
    Get revision details.

    Implements [policy-knowledge-base:PolicyRouter/TS-12] - Get revision detail
    """
    revision = await registry.get_revision(source, revision_id)

    if revision is None:
        # Check if policy exists to provide correct error
        policy = await registry.get_policy(source)
        if policy is None:
            raise HTTPException(
                status_code=404,
                detail=make_error_response(
                    code="policy_not_found",
                    message=f"No policy found with source '{source}'",
                    details={"source": source},
                ),
            )
        raise HTTPException(
            status_code=404,
            detail=make_error_response(
                code="revision_not_found",
                message=f"No revision found with ID '{revision_id}'",
                details={"source": source, "revision_id": revision_id},
            ),
        )

    return PolicyRevisionDetail(
        revision_id=revision.revision_id,
        source=revision.source,
        version_label=revision.version_label,
        effective_from=revision.effective_from,
        effective_to=revision.effective_to,
        status=revision.status,
        file_path=revision.file_path,
        file_size_bytes=revision.file_size_bytes,
        page_count=revision.page_count,
        chunk_count=revision.chunk_count,
        notes=revision.notes,
        created_at=revision.created_at,
        ingested_at=revision.ingested_at,
        error=revision.error,
    )


@router.get(
    "/policies/{source}/revisions/{revision_id}/status",
    response_model=RevisionStatusResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Policy or revision not found"},
    },
)
async def get_revision_status(
    source: str,
    revision_id: str,
    registry: PolicyRegistryDep,
) -> RevisionStatusResponse:
    """
    Get revision ingestion status.

    Implements [policy-knowledge-base:PolicyRouter/TS-16] - Get revision status
    """
    revision = await registry.get_revision(source, revision_id)

    if revision is None:
        policy = await registry.get_policy(source)
        if policy is None:
            raise HTTPException(
                status_code=404,
                detail=make_error_response(
                    code="policy_not_found",
                    message=f"No policy found with source '{source}'",
                    details={"source": source},
                ),
            )
        raise HTTPException(
            status_code=404,
            detail=make_error_response(
                code="revision_not_found",
                message=f"No revision found with ID '{revision_id}'",
                details={"source": source, "revision_id": revision_id},
            ),
        )

    # Progress details would come from the ingestion job
    # For now, return basic status info
    progress = None
    if revision.status == RevisionStatus.PROCESSING:
        progress = {
            "phase": "pending",
            "percent_complete": 0,
            "chunks_processed": 0,
        }
    elif revision.status == RevisionStatus.ACTIVE:
        progress = {
            "phase": "complete",
            "percent_complete": 100,
            "chunks_processed": revision.chunk_count or 0,
        }

    return RevisionStatusResponse(
        revision_id=revision_id,
        status=revision.status,
        progress=progress,
    )


@router.patch(
    "/policies/{source}/revisions/{revision_id}",
    response_model=PolicyRevisionDetail,
    responses={
        404: {"model": ErrorResponse, "description": "Policy or revision not found"},
        409: {"model": ErrorResponse, "description": "Revision overlap"},
    },
)
async def update_revision(
    source: str,
    revision_id: str,
    request: UpdateRevisionRequest,
    registry: PolicyRegistryDep,
) -> PolicyRevisionDetail:
    """
    Update revision metadata.

    Implements [policy-knowledge-base:FR-010] - Update revision metadata
    Implements [policy-knowledge-base:PolicyRouter/TS-12] - Update revision metadata
    """
    try:
        await registry.update_revision(
            source=source,
            revision_id=revision_id,
            version_label=request.version_label,
            effective_from=request.effective_from,
            effective_to=request.effective_to,
            notes=request.notes,
        )
    except PolicyNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=make_error_response(
                code="policy_not_found",
                message=f"No policy found with source '{source}'",
                details={"source": source},
            ),
        )
    except RevisionNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=make_error_response(
                code="revision_not_found",
                message=f"No revision found with ID '{revision_id}'",
                details={"source": source, "revision_id": revision_id},
            ),
        )
    except RevisionOverlapError as e:
        raise HTTPException(
            status_code=409,
            detail=make_error_response(
                code="revision_overlap",
                message=str(e),
                details={"source": source, "revision_id": revision_id},
            ),
        )

    # Return updated revision
    revision = await registry.get_revision(source, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found after update")

    logger.info("Revision updated", source=source, revision_id=revision_id)

    return PolicyRevisionDetail(
        revision_id=revision.revision_id,
        source=revision.source,
        version_label=revision.version_label,
        effective_from=revision.effective_from,
        effective_to=revision.effective_to,
        status=revision.status,
        file_path=revision.file_path,
        file_size_bytes=revision.file_size_bytes,
        page_count=revision.page_count,
        chunk_count=revision.chunk_count,
        notes=revision.notes,
        created_at=revision.created_at,
        ingested_at=revision.ingested_at,
        error=revision.error,
    )


@router.delete(
    "/policies/{source}/revisions/{revision_id}",
    response_model=RevisionDeleteResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Policy or revision not found"},
        409: {"model": ErrorResponse, "description": "Cannot delete sole revision"},
    },
)
async def delete_revision(
    source: str,
    revision_id: str,
    registry: PolicyRegistryDep,
) -> RevisionDeleteResponse:
    """
    Delete a revision and its chunks from ChromaDB.

    Implements [policy-knowledge-base:FR-011] - Delete revision
    Implements [policy-knowledge-base:PolicyRouter/TS-13] - Delete revision
    Implements [policy-knowledge-base:PolicyRouter/TS-14] - Cannot delete sole revision
    """
    # Get revision first to check chunk count
    revision = await registry.get_revision(source, revision_id)

    if revision is None:
        policy = await registry.get_policy(source)
        if policy is None:
            raise HTTPException(
                status_code=404,
                detail=make_error_response(
                    code="policy_not_found",
                    message=f"No policy found with source '{source}'",
                    details={"source": source},
                ),
            )
        raise HTTPException(
            status_code=404,
            detail=make_error_response(
                code="revision_not_found",
                message=f"No revision found with ID '{revision_id}'",
                details={"source": source, "revision_id": revision_id},
            ),
        )

    chunks_count = revision.chunk_count or 0

    try:
        await registry.delete_revision(source, revision_id)
    except CannotDeleteSoleRevisionError:
        raise HTTPException(
            status_code=409,
            detail=make_error_response(
                code="cannot_delete_sole_revision",
                message="Cannot delete the sole active revision for a policy",
                details={"source": source, "revision_id": revision_id},
            ),
        )
    except RevisionNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=make_error_response(
                code="revision_not_found",
                message=f"No revision found with ID '{revision_id}'",
                details={"source": source, "revision_id": revision_id},
            ),
        )

    # TODO: Also delete chunks from ChromaDB (Phase 3)

    # Delete file from disk
    if revision.file_path:
        file_path = Path(revision.file_path)
        file_path.unlink(missing_ok=True)
        # Try to remove empty parent directories
        with contextlib.suppress(OSError):
            file_path.parent.rmdir()

    logger.info(
        "Revision deleted",
        source=source,
        revision_id=revision_id,
        chunks_removed=chunks_count,
    )

    return RevisionDeleteResponse(
        source=source,
        revision_id=revision_id,
        status="deleted",
        chunks_removed=chunks_count,
    )


@router.post(
    "/policies/{source}/revisions/{revision_id}/reindex",
    response_model=RevisionStatusResponse,
    status_code=202,
    responses={
        404: {"model": ErrorResponse, "description": "Policy or revision not found"},
        409: {"model": ErrorResponse, "description": "Cannot reindex"},
    },
)
async def reindex_revision(
    source: str,
    revision_id: str,
    registry: PolicyRegistryDep,
) -> RevisionStatusResponse:
    """
    Re-run ingestion pipeline for existing revision.

    Implements [policy-knowledge-base:FR-012] - Re-index revision
    Implements [policy-knowledge-base:PolicyRouter/TS-15] - Reindex revision
    """
    revision = await registry.get_revision(source, revision_id)

    if revision is None:
        policy = await registry.get_policy(source)
        if policy is None:
            raise HTTPException(
                status_code=404,
                detail=make_error_response(
                    code="policy_not_found",
                    message=f"No policy found with source '{source}'",
                    details={"source": source},
                ),
            )
        raise HTTPException(
            status_code=404,
            detail=make_error_response(
                code="revision_not_found",
                message=f"No revision found with ID '{revision_id}'",
                details={"source": source, "revision_id": revision_id},
            ),
        )

    # Can't reindex if already processing
    if revision.status == RevisionStatus.PROCESSING:
        raise HTTPException(
            status_code=409,
            detail=make_error_response(
                code="cannot_reindex",
                message="Revision is already processing",
                details={"source": source, "revision_id": revision_id, "status": revision.status},
            ),
        )

    # Update status to processing
    await registry.update_revision(
        source=source,
        revision_id=revision_id,
        status=RevisionStatus.PROCESSING,
    )

    # TODO: Enqueue reindex job (Phase 3)

    logger.info(
        "Reindex requested",
        source=source,
        revision_id=revision_id,
    )

    return RevisionStatusResponse(
        revision_id=revision_id,
        status=RevisionStatus.PROCESSING,
        progress={
            "phase": "pending",
            "percent_complete": 0,
            "chunks_processed": 0,
        },
    )
