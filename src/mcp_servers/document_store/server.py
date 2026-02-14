"""
Document Store MCP Server.

Implements [document-processing:FR-006] - ingest_document tool
Implements [document-processing:FR-007] - search_application_docs tool
Implements [document-processing:FR-008] - get_document_text tool
Implements [document-processing:FR-009] - list_ingested_documents tool
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import structlog
from mcp.server import Server
from mcp.types import TextContent, Tool
from pydantic import BaseModel, Field
from starlette.applications import Starlette

from src.mcp_servers.document_store.chroma_client import (
    ChromaClient,
    ChunkRecord,
    DocumentRecord,
)
from src.mcp_servers.document_store.chunker import TextChunker
from src.mcp_servers.document_store.classifier import DocumentClassifier
from src.mcp_servers.document_store.embeddings import EmbeddingService
from src.mcp_servers.document_store.processor import (
    DocumentProcessor,
    ExtractionError,
)

logger = structlog.get_logger(__name__)


# Tool input schemas
class IngestDocumentInput(BaseModel):
    """Input schema for ingest_document tool."""

    file_path: str = Field(description="Path to the document file to ingest")
    application_ref: str = Field(description="Planning application reference (e.g., '25/01178/REM')")
    document_type: str | None = Field(
        default=None, description="Document type (e.g., 'transport_assessment'). Auto-classified if not provided."
    )


class SearchInput(BaseModel):
    """Input schema for search_application_docs tool."""

    query: str = Field(description="Natural language search query")
    application_ref: str | None = Field(
        default=None, description="Filter results to specific application"
    )
    document_types: list[str] | None = Field(
        default=None, description="Filter by document types"
    )
    max_results: int = Field(default=10, description="Maximum number of results to return")


class GetDocumentTextInput(BaseModel):
    """Input schema for get_document_text tool."""

    document_id: str = Field(description="Document ID to retrieve")


class ListDocumentsInput(BaseModel):
    """Input schema for list_ingested_documents tool."""

    application_ref: str = Field(description="Application reference to list documents for")


class DocumentStoreMCP:
    """
    MCP server for document storage and retrieval.

    Implements [document-processing:DocumentStoreMCP/TS-12] - Server initialization
    """

    def __init__(
        self,
        chroma_persist_dir: str | Path | None = None,
        enable_ocr: bool = True,
    ) -> None:
        """
        Initialize the Document Store MCP server.

        Args:
            chroma_persist_dir: Directory for ChromaDB persistence.
            enable_ocr: Whether to enable OCR fallback for scanned documents.
        """
        self._chroma_persist_dir = chroma_persist_dir
        self._enable_ocr = enable_ocr

        # Lazy initialization
        self._chroma_client: ChromaClient | None = None
        self._processor: DocumentProcessor | None = None
        self._chunker: TextChunker | None = None
        self._embedding_service: EmbeddingService | None = None
        self._classifier: DocumentClassifier | None = None

        # MCP server
        self._server = Server("document-store-mcp")
        self._setup_handlers()

    def _get_chroma_client(self) -> ChromaClient:
        """Get or create ChromaClient."""
        if self._chroma_client is None:
            self._chroma_client = ChromaClient(persist_directory=self._chroma_persist_dir)
            logger.info("ChromaClient initialized", persist_dir=str(self._chroma_persist_dir))
        return self._chroma_client

    def _get_processor(self) -> DocumentProcessor:
        """Get or create DocumentProcessor."""
        if self._processor is None:
            self._processor = DocumentProcessor(enable_ocr=self._enable_ocr)
            logger.info("DocumentProcessor initialized", ocr_enabled=self._enable_ocr)
        return self._processor

    def _get_chunker(self) -> TextChunker:
        """Get or create TextChunker."""
        if self._chunker is None:
            self._chunker = TextChunker()
            logger.info("TextChunker initialized")
        return self._chunker

    def _get_embedding_service(self) -> EmbeddingService:
        """Get or create EmbeddingService."""
        if self._embedding_service is None:
            self._embedding_service = EmbeddingService()
            logger.info("EmbeddingService initialized")
        return self._embedding_service

    def _get_classifier(self) -> DocumentClassifier:
        """Get or create DocumentClassifier."""
        if self._classifier is None:
            self._classifier = DocumentClassifier()
            logger.info("DocumentClassifier initialized")
        return self._classifier

    def _setup_handlers(self) -> None:
        """Set up MCP server handlers."""

        @self._server.list_tools()
        async def list_tools() -> list[Tool]:
            """List available tools."""
            return [
                Tool(
                    name="ingest_document",
                    description="Ingest a document into the vector store. Extracts text, chunks it, generates embeddings, and stores in ChromaDB.",
                    inputSchema=IngestDocumentInput.model_json_schema(),
                ),
                Tool(
                    name="search_application_docs",
                    description="Search documents using natural language. Returns semantically similar chunks with relevance scores.",
                    inputSchema=SearchInput.model_json_schema(),
                ),
                Tool(
                    name="get_document_text",
                    description="Get the full text of an ingested document by reassembling its chunks.",
                    inputSchema=GetDocumentTextInput.model_json_schema(),
                ),
                Tool(
                    name="list_ingested_documents",
                    description="List all ingested documents for a planning application.",
                    inputSchema=ListDocumentsInput.model_json_schema(),
                ),
            ]

        @self._server.call_tool()
        async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
            """Handle tool calls."""
            try:
                if name == "ingest_document":
                    result = await self._ingest_document(IngestDocumentInput(**arguments))
                elif name == "search_application_docs":
                    result = await self._search_documents(SearchInput(**arguments))
                elif name == "get_document_text":
                    result = await self._get_document_text(GetDocumentTextInput(**arguments))
                elif name == "list_ingested_documents":
                    result = await self._list_documents(ListDocumentsInput(**arguments))
                else:
                    result = {"error": f"Unknown tool: {name}"}

                return [TextContent(type="text", text=json.dumps(result))]

            except Exception as e:
                logger.exception("Tool call failed", tool=name, error=str(e))
                return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    async def _ingest_document(self, input: IngestDocumentInput) -> dict[str, Any]:
        """
        Ingest a document into the vector store.

        Implements [document-processing:DocumentStoreMCP/TS-01] - Ingest valid PDF
        Implements [document-processing:DocumentStoreMCP/TS-02] - Ingest already processed
        Implements [document-processing:DocumentStoreMCP/TS-03] - Ingest invalid file type
        Implements [document-processing:DocumentStoreMCP/TS-04] - Ingest non-existent file
        """
        file_path = Path(input.file_path)

        # Check file exists
        if not file_path.exists():
            logger.warning("File not found", file_path=str(file_path))
            return {
                "status": "error",
                "error_type": "file_not_found",
                "message": f"File not found: {file_path}",
            }

        # Check file type
        if file_path.suffix.lower() not in [".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif"]:
            logger.warning("Unsupported file type", file_path=str(file_path), suffix=file_path.suffix)
            return {
                "status": "error",
                "error_type": "unsupported_file_type",
                "message": f"Unsupported file type: {file_path.suffix}",
            }

        # Compute file hash for idempotency
        chroma = self._get_chroma_client()
        file_hash = ChromaClient.compute_file_hash(file_path)

        # Check if already ingested
        if chroma.is_document_ingested(file_hash, input.application_ref):
            logger.info(
                "Document already ingested",
                file_path=str(file_path),
                application_ref=input.application_ref,
            )
            return {
                "status": "already_ingested",
                "document_id": ChromaClient.generate_document_id(input.application_ref, file_hash),
                "message": "Document has already been ingested with the same content",
            }

        # Implements [document-type-detection:FR-001] - Classify before extraction
        # Implements [document-type-detection:FR-002] - Skip ingestion for image-based docs
        processor = self._get_processor()
        classification = processor.classify_document(file_path)
        if classification.is_image_based:
            logger.info(
                "Document skipped (image-based)",
                file_path=str(file_path),
                application_ref=input.application_ref,
                image_ratio=round(classification.average_image_ratio, 3),
                page_count=classification.page_count,
            )
            return {
                "status": "skipped",
                "reason": "image_based",
                "image_ratio": round(classification.average_image_ratio, 3),
                "total_pages": classification.page_count,
            }

        # Extract text
        try:
            extraction = processor.extract_text(file_path)
        except ExtractionError as e:
            logger.error("Extraction failed", file_path=str(file_path), error=str(e))
            return {
                "status": "error",
                "error_type": "extraction_failed",
                "message": str(e),
            }

        # Skip if no text extracted
        if extraction.total_char_count == 0:
            logger.warning("No text extracted", file_path=str(file_path))
            return {
                "status": "error",
                "error_type": "no_content",
                "message": "No text could be extracted from the document",
            }

        # Chunk text
        chunker = self._get_chunker()
        pages = [(p.page_number, p.text) for p in extraction.pages]
        chunks = chunker.chunk_pages(pages)

        if not chunks:
            logger.warning("No chunks produced", file_path=str(file_path))
            return {
                "status": "error",
                "error_type": "no_chunks",
                "message": "Document produced no valid text chunks",
            }

        # Generate embeddings
        embedding_service = self._get_embedding_service()
        texts = [c.text for c in chunks]
        embeddings = embedding_service.embed_batch(texts)

        # Generate document ID
        document_id = ChromaClient.generate_document_id(input.application_ref, file_hash)

        # Classify document type if not provided
        if input.document_type:
            document_type = input.document_type
            classification_method = "provided"
        else:
            classifier = self._get_classifier()
            # Use full text for content-based classification
            full_text = extraction.full_text
            classification = classifier.classify(file_path.name, content=full_text)
            document_type = classification.document_type
            classification_method = classification.method
            logger.info(
                "Document auto-classified",
                filename=file_path.name,
                document_type=document_type,
                confidence=classification.confidence,
                method=classification_method,
            )

        # Create chunk records
        chunk_records = []
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True)):
            chunk_id = ChromaClient.generate_chunk_id(
                application_ref=input.application_ref,
                file_hash=file_hash,
                page_number=chunk.page_numbers[0] if chunk.page_numbers else 0,
                chunk_index=i,
            )
            # ChromaDB metadata must be str, int, float, bool, or None (no lists)
            page_numbers_str = ",".join(str(p) for p in chunk.page_numbers) if chunk.page_numbers else ""
            chunk_records.append(
                ChunkRecord(
                    chunk_id=chunk_id,
                    text=chunk.text,
                    embedding=embedding,
                    metadata={
                        "application_ref": input.application_ref,
                        "document_id": document_id,
                        "source_file": file_path.name,
                        "document_type": document_type,
                        "page_numbers": page_numbers_str,
                        "chunk_index": i,
                        "total_chunks": len(chunks),
                        "extraction_method": extraction.extraction_method,
                        "char_count": chunk.char_count,
                        "word_count": chunk.word_count,
                    },
                )
            )

        # Store chunks
        chroma.upsert_chunks(chunk_records)

        # Register document
        import datetime

        chroma.register_document(
            DocumentRecord(
                document_id=document_id,
                file_path=str(file_path),
                file_hash=file_hash,
                application_ref=input.application_ref,
                document_type=document_type,
                chunk_count=len(chunk_records),
                ingested_at=datetime.datetime.now(datetime.UTC).isoformat(),
                extraction_method=extraction.extraction_method,
                contains_drawings=extraction.contains_drawings,
            )
        )

        logger.info(
            "Document ingested",
            document_id=document_id,
            chunks=len(chunk_records),
            extraction_method=extraction.extraction_method,
        )

        return {
            "status": "success",
            "document_id": document_id,
            "chunks_created": len(chunk_records),
            "extraction_method": extraction.extraction_method,
            "contains_drawings": extraction.contains_drawings,
            "total_chars": extraction.total_char_count,
            "total_words": extraction.total_word_count,
        }

    async def _search_documents(self, input: SearchInput) -> dict[str, Any]:
        """
        Search documents using semantic search.

        Implements [document-processing:DocumentStoreMCP/TS-05] - Search with results
        Implements [document-processing:DocumentStoreMCP/TS-06] - Search with no results
        Implements [document-processing:DocumentStoreMCP/TS-07] - Search with filter
        """
        # Generate query embedding
        embedding_service = self._get_embedding_service()
        query_embedding = embedding_service.embed(input.query)

        # Search
        chroma = self._get_chroma_client()
        results = chroma.search(
            query_embedding=query_embedding,
            n_results=input.max_results,
            application_ref=input.application_ref,
            document_types=input.document_types,
        )

        logger.info(
            "Search completed",
            query=input.query[:50],
            results_count=len(results),
            application_ref=input.application_ref,
        )

        return {
            "status": "success",
            "query": input.query,
            "results_count": len(results),
            "results": [
                {
                    "chunk_id": r.chunk_id,
                    "text": r.text,
                    "relevance_score": r.relevance_score,
                    "metadata": r.metadata,
                }
                for r in results
            ],
        }

    async def _get_document_text(self, input: GetDocumentTextInput) -> dict[str, Any]:
        """
        Get full document text.

        Implements [document-processing:DocumentStoreMCP/TS-08] - Get document text
        Implements [document-processing:DocumentStoreMCP/TS-09] - Get non-existent document
        """
        chroma = self._get_chroma_client()

        # Get document record
        record = chroma.get_document_record(input.document_id)
        if record is None:
            logger.warning("Document not found", document_id=input.document_id)
            return {
                "status": "error",
                "error_type": "document_not_found",
                "message": f"Document not found: {input.document_id}",
            }

        # Get all chunks
        chunks = chroma.get_document_chunks(input.document_id)
        if not chunks:
            return {
                "status": "error",
                "error_type": "no_chunks",
                "message": f"No chunks found for document: {input.document_id}",
            }

        # Concatenate text
        full_text = "\n\n".join(c.text for c in chunks)

        logger.info(
            "Document text retrieved",
            document_id=input.document_id,
            chunks=len(chunks),
            chars=len(full_text),
        )

        return {
            "status": "success",
            "document_id": input.document_id,
            "file_path": record.file_path,
            "document_type": record.document_type,
            "chunk_count": len(chunks),
            "text": full_text,
        }

    async def _list_documents(self, input: ListDocumentsInput) -> dict[str, Any]:
        """
        List ingested documents for an application.

        Implements [document-processing:DocumentStoreMCP/TS-10] - List ingested documents
        Implements [document-processing:DocumentStoreMCP/TS-11] - List for empty application
        """
        chroma = self._get_chroma_client()
        documents = chroma.list_documents_by_application(input.application_ref)

        logger.info(
            "Documents listed",
            application_ref=input.application_ref,
            count=len(documents),
        )

        return {
            "status": "success",
            "application_ref": input.application_ref,
            "document_count": len(documents),
            "documents": [
                {
                    "document_id": d.document_id,
                    "file_path": d.file_path,
                    "document_type": d.document_type,
                    "chunk_count": d.chunk_count,
                    "ingested_at": d.ingested_at,
                    "extraction_method": d.extraction_method,
                    "contains_drawings": d.contains_drawings,
                }
                for d in documents
            ],
        }

    @property
    def server(self) -> Server:
        """Get the MCP server instance."""
        return self._server


def create_app(
    chroma_persist_dir: str | Path | None = None,
    enable_ocr: bool = True,
) -> Starlette:
    """
    Create the Starlette application with SSE + Streamable HTTP transport.

    Args:
        chroma_persist_dir: Directory for ChromaDB persistence.
        enable_ocr: Whether to enable OCR fallback.

    Returns:
        Configured Starlette application.
    """
    from src.mcp_servers.shared.transport import create_mcp_app

    mcp_server = DocumentStoreMCP(
        chroma_persist_dir=chroma_persist_dir,
        enable_ocr=enable_ocr,
    )

    return create_mcp_app(mcp_server.server)


async def main() -> None:
    """Run the MCP server."""
    import uvicorn

    # Configuration from environment
    chroma_dir = os.getenv("CHROMA_PERSIST_DIR", "/data/chroma")
    enable_ocr = os.getenv("ENABLE_OCR", "true").lower() == "true"
    port = int(os.getenv("DOCUMENT_STORE_PORT", "3002"))

    logger.info(
        "Document Store MCP Server starting",
        component="document-store-mcp",
        chroma_dir=chroma_dir,
        enable_ocr=enable_ocr,
        port=port,
    )

    app = create_app(
        chroma_persist_dir=chroma_dir,
        enable_ocr=enable_ocr,
    )

    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
