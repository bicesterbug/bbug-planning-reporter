"""
Worker job functions for document processing.

Implements [document-processing:FR-006] - Document ingestion
Implements [document-processing:NFR-006] - Graceful error handling
"""

from pathlib import Path
from typing import Any

import structlog

from src.mcp_servers.document_store.progress import (
    ProgressReporter,
)
from src.mcp_servers.document_store.server import DocumentStoreMCP, IngestDocumentInput

# Allow injection of a custom MCP server for testing
_mcp_server_factory: type[DocumentStoreMCP] | None = None


def set_mcp_server_factory(factory: type[DocumentStoreMCP] | None) -> None:
    """Set a custom MCP server factory for testing."""
    global _mcp_server_factory
    _mcp_server_factory = factory


def _create_mcp_server(chroma_persist_dir: str | None, enable_ocr: bool) -> DocumentStoreMCP:
    """Create MCP server instance, using factory if set."""
    if _mcp_server_factory is not None:
        return _mcp_server_factory(chroma_persist_dir=chroma_persist_dir, enable_ocr=enable_ocr)
    return DocumentStoreMCP(chroma_persist_dir=chroma_persist_dir, enable_ocr=enable_ocr)

logger = structlog.get_logger(__name__)


async def ingest_application_documents(
    ctx: dict[str, Any],
    application_ref: str,
    document_paths: list[str],
    chroma_persist_dir: str | None = None,
) -> dict[str, Any]:
    """
    Ingest all documents for a planning application.

    Implements [document-processing:ITS-01] - Full ingestion pipeline
    Implements [document-processing:ITS-07] - Graceful degradation on failures

    Args:
        ctx: arq context (contains Redis pool).
        application_ref: Planning application reference (e.g., "25/01178/REM").
        document_paths: List of file paths to ingest.
        chroma_persist_dir: Optional ChromaDB persistence directory.

    Returns:
        Dict with ingestion results including success/failure counts.
    """
    logger.info(
        "Starting document ingestion job",
        application_ref=application_ref,
        document_count=len(document_paths),
    )

    # Create MCP server instance (using directly, not via MCP protocol)
    mcp_server = _create_mcp_server(
        chroma_persist_dir=chroma_persist_dir,
        enable_ocr=True,
    )

    # Set up progress reporter with Redis callback if available
    callbacks = []
    if "redis" in ctx:
        from src.mcp_servers.document_store.progress import RedisProgressCallback

        redis_callback = RedisProgressCallback(ctx["redis"], application_ref)
        callbacks.append(redis_callback)

    progress_reporter = ProgressReporter(
        total_documents=len(document_paths),
        application_ref=application_ref,
        callbacks=callbacks,
    )

    # Process each document
    results = []
    for doc_path in document_paths:
        await progress_reporter.start_document(doc_path)

        try:
            result = await mcp_server._ingest_document(
                IngestDocumentInput(
                    file_path=doc_path,
                    application_ref=application_ref,
                    document_type=None,  # Auto-classify
                )
            )

            success = result.get("status") == "success" or result.get("status") == "already_ingested"
            error_msg = result.get("message") if not success else None

            await progress_reporter.complete_document(doc_path, success=success, error=error_msg)

            results.append({
                "file_path": doc_path,
                "status": result.get("status"),
                "document_id": result.get("document_id"),
                "chunks_created": result.get("chunks_created", 0),
                "error": error_msg,
            })

        except Exception as e:
            logger.exception("Document ingestion failed", document=doc_path, error=str(e))
            await progress_reporter.complete_document(doc_path, success=False, error=str(e))
            results.append({
                "file_path": doc_path,
                "status": "error",
                "error": str(e),
            })

    # Compile final summary
    progress = progress_reporter.progress
    summary = {
        "application_ref": application_ref,
        "total_documents": progress.total_documents,
        "ingested_count": progress.ingested_count,
        "failed_count": progress.failed_count,
        "results": results,
        "errors": progress.errors,
    }

    logger.info(
        "Document ingestion job complete",
        application_ref=application_ref,
        ingested=progress.ingested_count,
        failed=progress.failed_count,
    )

    return summary


async def ingest_directory(
    ctx: dict[str, Any],
    application_ref: str,
    directory: str,
    file_patterns: list[str] | None = None,
    chroma_persist_dir: str | None = None,
) -> dict[str, Any]:
    """
    Ingest all documents from a directory for a planning application.

    Args:
        ctx: arq context.
        application_ref: Planning application reference.
        directory: Directory path containing documents.
        file_patterns: Optional list of glob patterns (default: ["*.pdf"]).
        chroma_persist_dir: Optional ChromaDB persistence directory.

    Returns:
        Dict with ingestion results.
    """
    dir_path = Path(directory)
    if not dir_path.exists():
        logger.error("Directory not found", directory=directory)
        return {
            "application_ref": application_ref,
            "error": f"Directory not found: {directory}",
            "total_documents": 0,
            "ingested_count": 0,
            "failed_count": 0,
        }

    # Find all matching files
    patterns = file_patterns or ["*.pdf"]
    document_paths = []
    for pattern in patterns:
        document_paths.extend(str(p) for p in dir_path.glob(pattern))

    if not document_paths:
        logger.warning("No documents found in directory", directory=directory, patterns=patterns)
        return {
            "application_ref": application_ref,
            "directory": directory,
            "total_documents": 0,
            "ingested_count": 0,
            "failed_count": 0,
            "results": [],
        }

    logger.info(
        "Found documents to ingest",
        directory=directory,
        document_count=len(document_paths),
    )

    return await ingest_application_documents(
        ctx=ctx,
        application_ref=application_ref,
        document_paths=document_paths,
        chroma_persist_dir=chroma_persist_dir,
    )


async def search_documents(
    ctx: dict[str, Any],  # noqa: ARG001 - required by arq interface
    query: str,
    application_ref: str | None = None,
    max_results: int = 10,
    chroma_persist_dir: str | None = None,
) -> dict[str, Any]:
    """
    Search ingested documents.

    Args:
        ctx: arq context.
        query: Search query text.
        application_ref: Optional filter by application.
        max_results: Maximum results to return.
        chroma_persist_dir: Optional ChromaDB persistence directory.

    Returns:
        Search results with relevant chunks.
    """
    from src.mcp_servers.document_store.server import SearchInput

    mcp_server = _create_mcp_server(
        chroma_persist_dir=chroma_persist_dir,
        enable_ocr=False,  # Not needed for search
    )

    result = await mcp_server._search_documents(
        SearchInput(
            query=query,
            application_ref=application_ref,
            max_results=max_results,
        )
    )

    return result
