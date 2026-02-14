"""
Policy Knowledge Base MCP Server.

Implements [policy-knowledge-base:FR-005] - search_policy with effective_date filtering
Implements [policy-knowledge-base:FR-006] - get_policy_section tool
Implements [policy-knowledge-base:FR-007] - list_policy_documents tool
Implements [policy-knowledge-base:FR-008] - list_policy_revisions tool
Implements [policy-knowledge-base:NFR-002] - 100% correct revision selection
Implements [policy-knowledge-base:NFR-003] - Top 5 results contain relevant content

Implements:
- [policy-knowledge-base:PolicyKBMCP/TS-01] Search without date filter
- [policy-knowledge-base:PolicyKBMCP/TS-02] Search with effective date
- [policy-knowledge-base:PolicyKBMCP/TS-03] Search filtered by sources
- [policy-knowledge-base:PolicyKBMCP/TS-04] Date before any revision
- [policy-knowledge-base:PolicyKBMCP/TS-05] Get policy section exists
- [policy-knowledge-base:PolicyKBMCP/TS-06] Get policy section not found
- [policy-knowledge-base:PolicyKBMCP/TS-07] Get section with specific revision
- [policy-knowledge-base:PolicyKBMCP/TS-08] List policy documents
- [policy-knowledge-base:PolicyKBMCP/TS-09] List policy revisions
- [policy-knowledge-base:PolicyKBMCP/TS-10] Ingest policy revision
- [policy-knowledge-base:PolicyKBMCP/TS-11] Remove policy revision
- [policy-knowledge-base:PolicyKBMCP/TS-12] Search relevance
"""

import asyncio
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import structlog
from mcp.server import Server
from mcp.types import TextContent, Tool
from pydantic import BaseModel, Field
from starlette.applications import Starlette

from src.mcp_servers.document_store.chunker import TextChunker
from src.mcp_servers.document_store.embeddings import EmbeddingService
from src.mcp_servers.document_store.processor import DocumentProcessor
from src.shared.policy_chroma_client import PolicyChromaClient
from src.shared.policy_registry import PolicyRegistry

logger = structlog.get_logger(__name__)


# Tool input schemas
class SearchPolicyInput(BaseModel):
    """Input schema for search_policy tool."""

    query: str = Field(description="Natural language search query")
    sources: list[str] | None = Field(
        default=None, description="Filter results to specific policy sources (e.g., ['LTN_1_20', 'NPPF'])"
    )
    effective_date: str | None = Field(
        default=None, description="ISO date (YYYY-MM-DD) for temporal filtering"
    )
    n_results: int = Field(default=10, description="Maximum number of results to return")


class GetPolicySectionInput(BaseModel):
    """Input schema for get_policy_section tool."""

    source: str = Field(description="Policy source slug (e.g., 'LTN_1_20')")
    section_ref: str = Field(description="Section reference (e.g., 'Chapter 5', 'Table 5-2', 'Para 116')")
    revision_id: str | None = Field(
        default=None, description="Specific revision ID (default: latest effective)"
    )


class ListPolicyRevisionsInput(BaseModel):
    """Input schema for list_policy_revisions tool."""

    source: str = Field(description="Policy source slug")


class IngestPolicyRevisionInput(BaseModel):
    """Input schema for ingest_policy_revision tool."""

    source: str = Field(description="Policy source slug")
    revision_id: str = Field(description="Revision ID")
    file_path: str = Field(description="Path to PDF file")
    reindex: bool = Field(default=False, description="If True, delete existing chunks first")


class RemovePolicyRevisionInput(BaseModel):
    """Input schema for remove_policy_revision tool."""

    source: str = Field(description="Policy source slug")
    revision_id: str = Field(description="Revision ID")


class PolicyKBMCP:
    """
    MCP server for Policy Knowledge Base.

    Implements [policy-knowledge-base:PolicyKBMCP/TS-01] through [TS-12]
    """

    def __init__(
        self,
        registry: PolicyRegistry | None = None,
        chroma_client: PolicyChromaClient | None = None,
        embedder: EmbeddingService | None = None,
        processor: DocumentProcessor | None = None,
        chunker: TextChunker | None = None,
    ) -> None:
        """
        Initialize the Policy KB MCP server.

        Args:
            registry: PolicyRegistry for metadata operations.
            chroma_client: PolicyChromaClient for vector storage.
            embedder: EmbeddingService for generating embeddings.
            processor: DocumentProcessor for PDF extraction.
            chunker: TextChunker for chunking text.
        """
        self._registry = registry
        self._chroma_client = chroma_client
        self._embedder = embedder
        self._processor = processor
        self._chunker = chunker

        # MCP server
        self._server = Server("policy-kb-mcp")
        self._setup_handlers()

    def _get_chroma_client(self) -> PolicyChromaClient:
        """Get or create PolicyChromaClient."""
        if self._chroma_client is None:
            chroma_dir = os.getenv("CHROMA_PERSIST_DIR", "/data/chroma")
            self._chroma_client = PolicyChromaClient(persist_directory=chroma_dir)
            logger.info("PolicyChromaClient initialized", persist_dir=chroma_dir)
        return self._chroma_client

    def _get_embedder(self) -> EmbeddingService:
        """Get or create EmbeddingService."""
        if self._embedder is None:
            self._embedder = EmbeddingService()
            logger.info("EmbeddingService initialized")
        return self._embedder

    def _get_processor(self) -> DocumentProcessor:
        """Get or create DocumentProcessor."""
        if self._processor is None:
            self._processor = DocumentProcessor(enable_ocr=True)
            logger.info("DocumentProcessor initialized")
        return self._processor

    def _get_chunker(self) -> TextChunker:
        """Get or create TextChunker."""
        if self._chunker is None:
            self._chunker = TextChunker()
            logger.info("TextChunker initialized")
        return self._chunker

    def _setup_handlers(self) -> None:
        """Set up MCP server handlers."""

        @self._server.list_tools()
        async def list_tools() -> list[Tool]:
            """List available tools."""
            return [
                Tool(
                    name="search_policy",
                    description="Search policy documents with optional temporal filtering. Returns semantically similar chunks with relevance scores.",
                    inputSchema=SearchPolicyInput.model_json_schema(),
                ),
                Tool(
                    name="get_policy_section",
                    description="Retrieve a specific policy section by reference (e.g., 'Chapter 5', 'Table 5-2').",
                    inputSchema=GetPolicySectionInput.model_json_schema(),
                ),
                Tool(
                    name="list_policy_documents",
                    description="List all registered policy documents with current revision info.",
                    inputSchema={},
                ),
                Tool(
                    name="list_policy_revisions",
                    description="List all revisions for a specific policy.",
                    inputSchema=ListPolicyRevisionsInput.model_json_schema(),
                ),
                Tool(
                    name="ingest_policy_revision",
                    description="Ingest a policy revision PDF into the vector store.",
                    inputSchema=IngestPolicyRevisionInput.model_json_schema(),
                ),
                Tool(
                    name="remove_policy_revision",
                    description="Remove all chunks for a policy revision from the vector store.",
                    inputSchema=RemovePolicyRevisionInput.model_json_schema(),
                ),
            ]

        @self._server.call_tool()
        async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
            """Handle tool calls."""
            try:
                if name == "search_policy":
                    result = await self._search_policy(SearchPolicyInput(**arguments))
                elif name == "get_policy_section":
                    result = await self._get_policy_section(GetPolicySectionInput(**arguments))
                elif name == "list_policy_documents":
                    result = await self._list_policy_documents()
                elif name == "list_policy_revisions":
                    result = await self._list_policy_revisions(ListPolicyRevisionsInput(**arguments))
                elif name == "ingest_policy_revision":
                    result = await self._ingest_policy_revision(IngestPolicyRevisionInput(**arguments))
                elif name == "remove_policy_revision":
                    result = await self._remove_policy_revision(RemovePolicyRevisionInput(**arguments))
                else:
                    result = {"error": f"Unknown tool: {name}"}

                return [TextContent(type="text", text=json.dumps(result))]

            except Exception as e:
                logger.exception("Tool call failed", tool=name, error=str(e))
                return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    async def _search_policy(self, input: SearchPolicyInput) -> dict[str, Any]:
        """
        Search policy documents with optional temporal filtering.

        Implements [policy-knowledge-base:PolicyKBMCP/TS-01] - Search without date filter
        Implements [policy-knowledge-base:PolicyKBMCP/TS-02] - Search with effective date
        Implements [policy-knowledge-base:PolicyKBMCP/TS-03] - Search filtered by sources
        Implements [policy-knowledge-base:PolicyKBMCP/TS-04] - Date before any revision
        Implements [policy-knowledge-base:PolicyKBMCP/TS-12] - Search relevance
        """
        # Parse effective date if provided
        effective_date: date | None = None
        if input.effective_date:
            try:
                effective_date = date.fromisoformat(input.effective_date)
            except ValueError:
                return {
                    "status": "error",
                    "error_type": "invalid_date",
                    "message": f"Invalid date format: {input.effective_date}. Use YYYY-MM-DD.",
                }

        # Generate query embedding
        embedder = self._get_embedder()
        query_embedding = embedder.embed(input.query)

        # Search ChromaDB
        chroma = self._get_chroma_client()
        results = chroma.search(
            query_embedding=query_embedding,
            n_results=input.n_results,
            effective_date=effective_date,
            sources=input.sources,
        )

        logger.info(
            "Policy search completed",
            query=input.query[:50],
            results_count=len(results),
            effective_date=str(effective_date) if effective_date else None,
            sources=input.sources,
        )

        return {
            "status": "success",
            "query": input.query,
            "effective_date": input.effective_date,
            "results_count": len(results),
            "results": [
                {
                    "chunk_id": r.chunk_id,
                    "text": r.text,
                    "relevance_score": r.relevance_score,
                    "source": r.source,
                    "revision_id": r.revision_id,
                    "version_label": r.version_label,
                    "section_ref": r.section_ref,
                    "page_number": r.page_number,
                }
                for r in results
            ],
        }

    async def _get_policy_section(self, input: GetPolicySectionInput) -> dict[str, Any]:
        """
        Retrieve a specific policy section by reference.

        Implements [policy-knowledge-base:PolicyKBMCP/TS-05] - Get policy section exists
        Implements [policy-knowledge-base:PolicyKBMCP/TS-06] - Get policy section not found
        Implements [policy-knowledge-base:PolicyKBMCP/TS-07] - Get section with specific revision
        """
        chroma = self._get_chroma_client()

        # Query for section by metadata filter
        # We search for chunks where section_ref matches
        # Use a dummy embedding and filter by metadata
        embedder = self._get_embedder()
        query_embedding = embedder.embed(input.section_ref)

        results = chroma.search(
            query_embedding=query_embedding,
            n_results=20,  # Get more results to find exact match
            sources=[input.source],
            revision_id=input.revision_id,
        )

        # Filter to exact section_ref matches
        matching_chunks = [
            r for r in results
            if r.section_ref == input.section_ref
        ]

        if not matching_chunks:
            logger.warning(
                "Section not found",
                source=input.source,
                section_ref=input.section_ref,
            )
            return {
                "status": "error",
                "error_type": "section_not_found",
                "message": f"Section '{input.section_ref}' not found in policy '{input.source}'",
            }

        # Combine text from matching chunks (they may be split across chunks)
        combined_text = "\n\n".join(c.text for c in matching_chunks)
        first_chunk = matching_chunks[0]

        logger.info(
            "Section retrieved",
            source=input.source,
            section_ref=input.section_ref,
            chunks_found=len(matching_chunks),
        )

        return {
            "status": "success",
            "source": input.source,
            "section_ref": input.section_ref,
            "revision_id": first_chunk.revision_id,
            "version_label": first_chunk.version_label,
            "text": combined_text,
            "page_numbers": sorted({c.page_number for c in matching_chunks}),
        }

    async def _list_policy_documents(self) -> dict[str, Any]:
        """
        List all registered policy documents.

        Implements [policy-knowledge-base:PolicyKBMCP/TS-08] - List policy documents
        """
        if self._registry is None:
            return {
                "status": "error",
                "error_type": "registry_unavailable",
                "message": "PolicyRegistry not configured",
            }

        policies = await self._registry.list_policies()

        logger.info("Listed policy documents", count=len(policies))

        return {
            "status": "success",
            "policy_count": len(policies),
            "policies": [
                {
                    "source": p.source,
                    "title": p.title,
                    "category": p.category.value if p.category else None,
                }
                for p in policies
            ],
        }

    async def _list_policy_revisions(self, input: ListPolicyRevisionsInput) -> dict[str, Any]:
        """
        List all revisions for a policy.

        Implements [policy-knowledge-base:PolicyKBMCP/TS-09] - List policy revisions
        """
        if self._registry is None:
            return {
                "status": "error",
                "error_type": "registry_unavailable",
                "message": "PolicyRegistry not configured",
            }

        revisions = await self._registry.list_revisions(input.source)

        logger.info(
            "Listed policy revisions",
            source=input.source,
            count=len(revisions),
        )

        return {
            "status": "success",
            "source": input.source,
            "revision_count": len(revisions),
            "revisions": [
                {
                    "revision_id": r.revision_id,
                    "version_label": r.version_label,
                    "effective_from": r.effective_from.isoformat() if r.effective_from else None,
                    "effective_to": r.effective_to.isoformat() if r.effective_to else None,
                    "status": r.status.value if r.status else None,
                    "chunk_count": r.chunk_count,
                }
                for r in revisions
            ],
        }

    async def _ingest_policy_revision(self, input: IngestPolicyRevisionInput) -> dict[str, Any]:
        """
        Ingest a policy revision PDF.

        Implements [policy-knowledge-base:PolicyKBMCP/TS-10] - Ingest policy revision
        """
        file_path = Path(input.file_path)

        if not file_path.exists():
            return {
                "status": "error",
                "error_type": "file_not_found",
                "message": f"File not found: {file_path}",
            }

        # Get revision metadata from registry
        if self._registry is None:
            return {
                "status": "error",
                "error_type": "registry_unavailable",
                "message": "PolicyRegistry not configured",
            }

        revision = await self._registry.get_revision(input.source, input.revision_id)
        if revision is None:
            return {
                "status": "error",
                "error_type": "revision_not_found",
                "message": f"Revision not found: {input.source}/{input.revision_id}",
            }

        chroma = self._get_chroma_client()

        # If reindex, clear existing chunks first
        if input.reindex:
            deleted_count = chroma.delete_revision_chunks(input.source, input.revision_id)
            logger.info(
                "Cleared existing chunks for reindex",
                source=input.source,
                revision_id=input.revision_id,
                deleted_count=deleted_count,
            )

        # Extract text from PDF
        processor = self._get_processor()
        extraction = processor.extract_text(str(file_path))

        # Chunk the text
        chunker = self._get_chunker()
        pages_with_text = [
            (page.page_number, page.text)
            for page in extraction.pages
            if page.text.strip()
        ]
        chunks = chunker.chunk_pages(pages_with_text)

        if not chunks:
            return {
                "status": "error",
                "error_type": "no_content",
                "message": "No text could be extracted from PDF",
            }

        # Generate embeddings
        embedder = self._get_embedder()
        texts = [c.text for c in chunks]
        embeddings = embedder.embed_batch(texts)

        # Prepare temporal metadata
        effective_from_int = PolicyChromaClient.format_date_for_metadata(revision.effective_from)
        effective_to_int = PolicyChromaClient.format_date_for_metadata(revision.effective_to)

        # Create chunk records
        from src.shared.policy_chroma_client import PolicyChunkRecord
        from src.worker.policy_jobs import PolicyIngestionService

        chunk_records = []
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True)):
            section_ref = PolicyIngestionService._extract_section_ref(chunk.text)

            chunk_id = PolicyChromaClient.generate_chunk_id(
                source=input.source,
                revision_id=input.revision_id,
                section_ref=section_ref,
                chunk_index=i,
            )

            chunk_records.append(
                PolicyChunkRecord(
                    chunk_id=chunk_id,
                    text=chunk.text,
                    embedding=embedding,
                    metadata={
                        "source": input.source,
                        "source_title": revision.source,
                        "revision_id": input.revision_id,
                        "version_label": revision.version_label,
                        "effective_from": effective_from_int,
                        "effective_to": effective_to_int,
                        "section_ref": section_ref,
                        "page_number": chunk.page_numbers[0] if chunk.page_numbers else 0,
                        "chunk_index": i,
                    },
                )
            )

        # Store chunks
        chroma.upsert_chunks(chunk_records)

        logger.info(
            "Policy revision ingested",
            source=input.source,
            revision_id=input.revision_id,
            chunks_created=len(chunk_records),
        )

        return {
            "status": "success",
            "source": input.source,
            "revision_id": input.revision_id,
            "chunks_created": len(chunk_records),
            "page_count": extraction.total_pages,
            "extraction_method": extraction.extraction_method,
        }

    async def _remove_policy_revision(self, input: RemovePolicyRevisionInput) -> dict[str, Any]:
        """
        Remove all chunks for a policy revision.

        Implements [policy-knowledge-base:PolicyKBMCP/TS-11] - Remove policy revision
        """
        chroma = self._get_chroma_client()

        chunks_removed = chroma.delete_revision_chunks(input.source, input.revision_id)

        logger.info(
            "Policy revision removed",
            source=input.source,
            revision_id=input.revision_id,
            chunks_removed=chunks_removed,
        )

        return {
            "status": "success",
            "source": input.source,
            "revision_id": input.revision_id,
            "chunks_removed": chunks_removed,
        }

    @property
    def server(self) -> Server:
        """Get the MCP server instance."""
        return self._server


def create_app(
    chroma_persist_dir: str | Path | None = None,
    redis_url: str | None = None,
) -> Starlette:
    """
    Create the Starlette application with SSE + Streamable HTTP transport.

    Args:
        chroma_persist_dir: Directory for ChromaDB persistence.
        redis_url: Redis URL for PolicyRegistry.

    Returns:
        Configured Starlette application.
    """
    import redis.asyncio as aioredis

    from src.mcp_servers.shared.transport import create_mcp_app

    # Create dependencies
    chroma_client = PolicyChromaClient(persist_directory=chroma_persist_dir)

    registry = None
    if redis_url:
        redis_client = aioredis.from_url(redis_url, decode_responses=True)
        registry = PolicyRegistry(redis_client)

    mcp_server = PolicyKBMCP(
        registry=registry,
        chroma_client=chroma_client,
    )

    return create_mcp_app(mcp_server.server)


async def main() -> None:
    """Run the MCP server."""
    import uvicorn

    # Configuration from environment
    chroma_dir = os.getenv("CHROMA_PERSIST_DIR", "/data/chroma")
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    port = int(os.getenv("POLICY_KB_PORT", "3003"))

    logger.info(
        "Policy KB MCP Server starting",
        component="policy-kb-mcp",
        chroma_dir=chroma_dir,
        port=port,
    )

    app = create_app(
        chroma_persist_dir=chroma_dir,
        redis_url=redis_url,
    )

    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
