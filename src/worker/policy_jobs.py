"""
Worker job functions for policy document ingestion.

Implements [policy-knowledge-base:FR-003] - Async processing through extraction, chunking, embedding
Implements [policy-knowledge-base:FR-004] - Chunks include effective_from/effective_to metadata
Implements [policy-knowledge-base:FR-012] - Re-run ingestion pipeline for reindex
Implements [policy-knowledge-base:NFR-001] - Complete 100-page PDF within 2 minutes

Implements:
- [policy-knowledge-base:PolicyIngestionJob/TS-01] Successful ingestion
- [policy-knowledge-base:PolicyIngestionJob/TS-02] Ingestion failure
- [policy-knowledge-base:PolicyIngestionJob/TS-03] Chunks have temporal metadata
- [policy-knowledge-base:PolicyIngestionJob/TS-04] Reindex clears old chunks
- [policy-knowledge-base:PolicyIngestionJob/TS-05] Progress updates
"""

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from src.api.schemas.policy import RevisionStatus
from src.mcp_servers.document_store.chunker import TextChunker
from src.mcp_servers.document_store.embeddings import EmbeddingService
from src.mcp_servers.document_store.processor import DocumentProcessor, ExtractionError
from src.shared.policy_chroma_client import PolicyChromaClient, PolicyChunkRecord
from src.shared.policy_registry import PolicyRegistry

logger = structlog.get_logger(__name__)


@dataclass
class IngestionProgress:
    """Progress information for policy ingestion."""

    phase: str  # "extracting", "chunking", "embedding", "storing", "complete", "failed"
    percent_complete: int
    pages_processed: int | None = None
    total_pages: int | None = None
    chunks_processed: int | None = None
    total_chunks: int | None = None
    error: str | None = None


@dataclass
class IngestionResult:
    """Result of policy ingestion job."""

    success: bool
    source: str
    revision_id: str
    chunk_count: int
    page_count: int
    extraction_method: str
    error: str | None = None
    duration_seconds: float = 0.0


class PolicyIngestionService:
    """
    Service for ingesting policy PDFs into ChromaDB.

    Coordinates the full pipeline: extraction -> chunking -> embedding -> storage.
    Updates revision status in PolicyRegistry throughout the process.
    """

    def __init__(
        self,
        registry: PolicyRegistry,
        chroma_client: PolicyChromaClient,
        processor: DocumentProcessor | None = None,
        chunker: TextChunker | None = None,
        embedder: EmbeddingService | None = None,
    ) -> None:
        """
        Initialize the ingestion service.

        Args:
            registry: PolicyRegistry for updating revision status.
            chroma_client: PolicyChromaClient for storing chunks.
            processor: Optional DocumentProcessor (defaults to new instance).
            chunker: Optional TextChunker (defaults to new instance).
            embedder: Optional EmbeddingService (defaults to new instance).
        """
        self._registry = registry
        self._chroma = chroma_client
        self._processor = processor or DocumentProcessor(enable_ocr=True)
        self._chunker = chunker or TextChunker()
        self._embedder = embedder or EmbeddingService()

    async def ingest_revision(
        self,
        source: str,
        revision_id: str,
        file_path: str | Path,
        reindex: bool = False,
    ) -> IngestionResult:
        """
        Ingest a policy revision PDF.

        Implements [policy-knowledge-base:PolicyIngestionJob/TS-01] - Successful ingestion
        Implements [policy-knowledge-base:PolicyIngestionJob/TS-02] - Ingestion failure
        Implements [policy-knowledge-base:PolicyIngestionJob/TS-03] - Chunks have temporal metadata
        Implements [policy-knowledge-base:PolicyIngestionJob/TS-04] - Reindex clears old chunks

        Args:
            source: Policy source slug.
            revision_id: Revision ID.
            file_path: Path to PDF file.
            reindex: If True, delete existing chunks first.

        Returns:
            IngestionResult with success status and details.
        """
        start_time = datetime.now(UTC)
        file_path = Path(file_path)

        logger.info(
            "Starting policy ingestion",
            source=source,
            revision_id=revision_id,
            file_path=str(file_path),
            reindex=reindex,
        )

        try:
            # Get revision metadata for temporal fields
            revision = await self._registry.get_revision(source, revision_id)
            if revision is None:
                raise ValueError(f"Revision not found: {source}/{revision_id}")

            # If reindex, clear existing chunks first
            if reindex:
                deleted_count = self._chroma.delete_revision_chunks(source, revision_id)
                logger.info(
                    "Cleared existing chunks for reindex",
                    source=source,
                    revision_id=revision_id,
                    deleted_count=deleted_count,
                )

            # Phase 1: Extract text from PDF
            logger.info("Extracting text from PDF", phase="extracting")
            extraction = self._processor.extract(str(file_path))

            page_count = extraction.total_pages
            logger.info(
                "Text extraction complete",
                pages=page_count,
                method=extraction.extraction_method,
                char_count=extraction.total_char_count,
            )

            # Phase 2: Chunk the text
            logger.info("Chunking text", phase="chunking")
            pages_with_text = [
                (page.page_number, page.text)
                for page in extraction.pages
                if page.text.strip()
            ]
            chunks = self._chunker.chunk_pages(pages_with_text)

            logger.info("Chunking complete", chunk_count=len(chunks))

            if not chunks:
                # No text extracted - mark as failed
                await self._update_revision_failed(
                    source, revision_id, "No text could be extracted from PDF"
                )
                return IngestionResult(
                    success=False,
                    source=source,
                    revision_id=revision_id,
                    chunk_count=0,
                    page_count=page_count,
                    extraction_method=extraction.extraction_method,
                    error="No text could be extracted from PDF",
                    duration_seconds=(datetime.now(UTC) - start_time).total_seconds(),
                )

            # Phase 3: Generate embeddings
            logger.info("Generating embeddings", phase="embedding", chunk_count=len(chunks))
            texts = [c.text for c in chunks]
            embeddings = self._embedder.embed_batch(texts)

            logger.info("Embeddings generated")

            # Phase 4: Store chunks in ChromaDB
            logger.info("Storing chunks", phase="storing", chunk_count=len(chunks))

            # Prepare temporal metadata
            effective_from_int = PolicyChromaClient.format_date_for_metadata(revision.effective_from)
            effective_to_int = PolicyChromaClient.format_date_for_metadata(revision.effective_to)

            chunk_records = []
            for i, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True)):
                # Determine section reference from chunk
                section_ref = self._extract_section_ref(chunk.text)

                chunk_id = PolicyChromaClient.generate_chunk_id(
                    source=source,
                    revision_id=revision_id,
                    section_ref=section_ref,
                    chunk_index=i,
                )

                chunk_records.append(
                    PolicyChunkRecord(
                        chunk_id=chunk_id,
                        text=chunk.text,
                        embedding=embedding,
                        metadata={
                            "source": source,
                            "source_title": revision.source,  # Will be policy title
                            "revision_id": revision_id,
                            "version_label": revision.version_label,
                            "effective_from": effective_from_int,
                            "effective_to": effective_to_int,
                            "section_ref": section_ref,
                            "page_number": chunk.page_numbers[0] if chunk.page_numbers else 0,
                            "chunk_index": i,
                        },
                    )
                )

            self._chroma.upsert_chunks(chunk_records)

            logger.info("Chunks stored", count=len(chunk_records))

            # Update revision status to active
            await self._registry.update_revision(
                source=source,
                revision_id=revision_id,
                status=RevisionStatus.ACTIVE,
                chunk_count=len(chunk_records),
                ingested_at=datetime.now(UTC),
            )

            duration = (datetime.now(UTC) - start_time).total_seconds()
            logger.info(
                "Policy ingestion complete",
                source=source,
                revision_id=revision_id,
                chunk_count=len(chunk_records),
                page_count=page_count,
                duration_seconds=duration,
            )

            return IngestionResult(
                success=True,
                source=source,
                revision_id=revision_id,
                chunk_count=len(chunk_records),
                page_count=page_count,
                extraction_method=extraction.extraction_method,
                duration_seconds=duration,
            )

        except ExtractionError as e:
            error_msg = f"PDF extraction failed: {e}"
            logger.error("Ingestion failed", source=source, revision_id=revision_id, error=error_msg)
            await self._update_revision_failed(source, revision_id, error_msg)
            return IngestionResult(
                success=False,
                source=source,
                revision_id=revision_id,
                chunk_count=0,
                page_count=0,
                extraction_method="failed",
                error=error_msg,
                duration_seconds=(datetime.now(UTC) - start_time).total_seconds(),
            )

        except Exception as e:
            error_msg = f"Ingestion failed: {e}"
            logger.error(
                "Ingestion failed",
                source=source,
                revision_id=revision_id,
                error=str(e),
                exc_info=True,
            )
            await self._update_revision_failed(source, revision_id, error_msg)
            return IngestionResult(
                success=False,
                source=source,
                revision_id=revision_id,
                chunk_count=0,
                page_count=0,
                extraction_method="failed",
                error=error_msg,
                duration_seconds=(datetime.now(UTC) - start_time).total_seconds(),
            )

    async def _update_revision_failed(
        self, source: str, revision_id: str, error: str
    ) -> None:
        """Update revision status to failed with error message."""
        try:
            await self._registry.update_revision(
                source=source,
                revision_id=revision_id,
                status=RevisionStatus.FAILED,
                error=error,
            )
        except Exception as e:
            logger.error(
                "Failed to update revision status",
                source=source,
                revision_id=revision_id,
                error=str(e),
            )

    @staticmethod
    def _extract_section_ref(text: str) -> str:
        """
        Extract section reference from chunk text if present.

        Looks for patterns like "Chapter 5", "Section 3.2", "Para 116", "Table 5-2".
        """
        import re

        # Common section patterns
        patterns = [
            r"(Chapter\s+\d+(?:\.\d+)?)",
            r"(Section\s+\d+(?:\.\d+)?)",
            r"(Para(?:graph)?\s+\d+(?:\.\d+)?)",
            r"(Table\s+\d+(?:[-.]\d+)?)",
            r"(Figure\s+\d+(?:[-.]\d+)?)",
            r"(Annex\s+[A-Z])",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)

        return ""


# arq job function
async def ingest_policy_revision(
    ctx: dict[str, Any],
    source: str,
    revision_id: str,
    file_path: str,
    reindex: bool = False,
) -> dict[str, Any]:
    """
    arq job function for policy ingestion.

    Args:
        ctx: arq context with Redis pool.
        source: Policy source slug.
        revision_id: Revision ID.
        file_path: Path to PDF file.
        reindex: If True, clear existing chunks first.

    Returns:
        Dict with ingestion result.
    """
    # Get dependencies from context or create defaults
    redis_client = ctx.get("redis")

    # Create registry with Redis client
    if redis_client:
        registry = PolicyRegistry(redis_client)
    else:
        # For testing without Redis context
        import redis.asyncio as aioredis
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        redis_client = aioredis.from_url(redis_url, decode_responses=True)
        registry = PolicyRegistry(redis_client)

    # Create ChromaDB client
    chroma_persist_dir = os.getenv("CHROMA_PERSIST_DIR", "/data/chroma")
    chroma_client = PolicyChromaClient(persist_directory=chroma_persist_dir)

    # Create service and run ingestion
    service = PolicyIngestionService(
        registry=registry,
        chroma_client=chroma_client,
    )

    result = await service.ingest_revision(
        source=source,
        revision_id=revision_id,
        file_path=file_path,
        reindex=reindex,
    )

    return {
        "success": result.success,
        "source": result.source,
        "revision_id": result.revision_id,
        "chunk_count": result.chunk_count,
        "page_count": result.page_count,
        "extraction_method": result.extraction_method,
        "error": result.error,
        "duration_seconds": result.duration_seconds,
    }
