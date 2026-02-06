"""
ChromaDB client for document storage and retrieval.

Implements [document-processing:FR-005] - Store chunks with metadata
Implements [document-processing:FR-007] - Semantic search with filtering
Implements [document-processing:NFR-004] - Storage efficiency
Implements [document-processing:NFR-005] - Search latency <500ms
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import chromadb
import structlog
from chromadb.config import Settings

logger = structlog.get_logger(__name__)


@dataclass
class ChunkRecord:
    """A chunk stored in ChromaDB."""

    chunk_id: str
    text: str
    embedding: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    """A search result from ChromaDB."""

    chunk_id: str
    text: str
    relevance_score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentRecord:
    """Document-level tracking record."""

    document_id: str
    file_path: str
    file_hash: str
    application_ref: str
    document_type: str
    chunk_count: int
    ingested_at: str
    extraction_method: str = "text_layer"
    contains_drawings: bool = False


class ChromaClient:
    """
    Client for ChromaDB operations on the application_docs collection.

    Handles connection management, idempotent upserts, semantic search,
    and document metadata queries.

    Implements:
    - [document-processing:ChromaClient/TS-01] Store chunk with metadata
    - [document-processing:ChromaClient/TS-02] Idempotent upsert
    - [document-processing:ChromaClient/TS-03] Semantic search
    - [document-processing:ChromaClient/TS-04] Search with filter
    - [document-processing:ChromaClient/TS-05] Search empty collection
    - [document-processing:ChromaClient/TS-06] Delete chunks by document
    - [document-processing:ChromaClient/TS-07] Get chunks by document
    - [document-processing:ChromaClient/TS-09] Collection initialization
    """

    COLLECTION_NAME = "application_docs"
    DOCUMENT_REGISTRY_COLLECTION = "document_registry"

    def __init__(
        self,
        persist_directory: str | Path | None = None,
        client: chromadb.ClientAPI | None = None,
    ) -> None:
        """
        Initialize the ChromaDB client.

        Args:
            persist_directory: Directory for persistent storage. If None, uses in-memory.
            client: Optional pre-configured ChromaDB client (for testing).
        """
        self._persist_directory = Path(persist_directory) if persist_directory else None
        self._client = client
        self._collection: chromadb.Collection | None = None
        self._registry_collection: chromadb.Collection | None = None

    def _get_client(self) -> chromadb.ClientAPI:
        """Get or create the ChromaDB client."""
        if self._client is None:
            if self._persist_directory:
                self._persist_directory.mkdir(parents=True, exist_ok=True)
                self._client = chromadb.PersistentClient(
                    path=str(self._persist_directory),
                    settings=Settings(anonymized_telemetry=False),
                )
                logger.info(
                    "ChromaDB persistent client created",
                    path=str(self._persist_directory),
                )
            else:
                self._client = chromadb.Client(
                    settings=Settings(anonymized_telemetry=False),
                )
                logger.info("ChromaDB in-memory client created")
        return self._client

    def _get_collection(self) -> chromadb.Collection:
        """Get or create the application_docs collection."""
        if self._collection is None:
            client = self._get_client()
            self._collection = client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                metadata={"description": "Planning application document chunks"},
            )
            logger.debug("Collection initialized", name=self.COLLECTION_NAME)
        return self._collection

    def _get_registry_collection(self) -> chromadb.Collection:
        """Get or create the document registry collection."""
        if self._registry_collection is None:
            client = self._get_client()
            self._registry_collection = client.get_or_create_collection(
                name=self.DOCUMENT_REGISTRY_COLLECTION,
                metadata={"description": "Document-level metadata registry"},
            )
        return self._registry_collection

    @staticmethod
    def generate_chunk_id(
        application_ref: str,
        file_hash: str,
        page_number: int,
        chunk_index: int,
    ) -> str:
        """
        Generate a deterministic chunk ID.

        Format: {sanitized_ref}_{file_hash_short}_{page}_{chunk_idx}
        Example: 25_01178_REM_a1b2c3_014_042
        """
        # Sanitize application ref
        safe_ref = application_ref.replace("/", "_")
        # Use first 6 chars of file hash
        hash_short = file_hash[:6] if len(file_hash) >= 6 else file_hash
        return f"{safe_ref}_{hash_short}_{page_number:03d}_{chunk_index:03d}"

    @staticmethod
    def generate_document_id(application_ref: str, file_hash: str) -> str:
        """Generate a document ID from application ref and file hash."""
        safe_ref = application_ref.replace("/", "_")
        hash_short = file_hash[:6] if len(file_hash) >= 6 else file_hash
        return f"{safe_ref}_{hash_short}"

    @staticmethod
    def compute_file_hash(file_path: str | Path) -> str:
        """Compute SHA256 hash of a file."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def upsert_chunk(self, chunk: ChunkRecord) -> None:
        """
        Store or update a chunk in the collection.

        Implements [document-processing:ChromaClient/TS-01] - Store chunk
        Implements [document-processing:ChromaClient/TS-02] - Idempotent upsert

        Args:
            chunk: The chunk to store.
        """
        collection = self._get_collection()

        collection.upsert(
            ids=[chunk.chunk_id],
            embeddings=[chunk.embedding],
            documents=[chunk.text],
            metadatas=[chunk.metadata],
        )

        logger.debug("Chunk upserted", chunk_id=chunk.chunk_id)

    def upsert_chunks(self, chunks: list[ChunkRecord]) -> None:
        """
        Store or update multiple chunks efficiently.

        Args:
            chunks: List of chunks to store.
        """
        if not chunks:
            return

        collection = self._get_collection()

        collection.upsert(
            ids=[c.chunk_id for c in chunks],
            embeddings=[c.embedding for c in chunks],
            documents=[c.text for c in chunks],
            metadatas=[c.metadata for c in chunks],
        )

        logger.debug("Chunks upserted", count=len(chunks))

    def search(
        self,
        query_embedding: list[float],
        n_results: int = 10,
        application_ref: str | None = None,
        document_types: list[str] | None = None,
    ) -> list[SearchResult]:
        """
        Perform semantic search on the collection.

        Implements [document-processing:ChromaClient/TS-03] - Semantic search
        Implements [document-processing:ChromaClient/TS-04] - Search with filter

        Args:
            query_embedding: The query embedding vector.
            n_results: Maximum number of results to return.
            application_ref: Optional filter by application reference.
            document_types: Optional filter by document types.

        Returns:
            List of search results ordered by relevance.
        """
        collection = self._get_collection()

        # Build where clause for filters
        where: dict[str, Any] | None = None
        where_conditions = []

        if application_ref:
            where_conditions.append({"application_ref": application_ref})
        if document_types:
            where_conditions.append({"document_type": {"$in": document_types}})

        if len(where_conditions) == 1:
            where = where_conditions[0]
        elif len(where_conditions) > 1:
            where = {"$and": where_conditions}

        try:
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            logger.error("Search failed", error=str(e))
            return []

        # Convert to SearchResult objects
        search_results = []
        if results["ids"] and results["ids"][0]:
            ids = results["ids"][0]
            documents = results["documents"][0] if results["documents"] else []
            metadatas = results["metadatas"][0] if results["metadatas"] else []
            distances = results["distances"][0] if results["distances"] else []

            for i, chunk_id in enumerate(ids):
                # Convert distance to relevance score (1 - normalized_distance)
                # ChromaDB returns L2 distance by default
                distance = distances[i] if i < len(distances) else 0
                relevance_score = max(0, 1 - (distance / 2))  # Normalize

                search_results.append(
                    SearchResult(
                        chunk_id=chunk_id,
                        text=documents[i] if i < len(documents) else "",
                        relevance_score=relevance_score,
                        metadata=metadatas[i] if i < len(metadatas) else {},
                    )
                )

        return search_results

    def get_document_chunks(self, document_id: str) -> list[ChunkRecord]:
        """
        Get all chunks for a document.

        Implements [document-processing:ChromaClient/TS-07]

        Args:
            document_id: The document ID (prefix for chunk IDs).

        Returns:
            List of chunks ordered by chunk index.
        """
        collection = self._get_collection()

        # Query chunks that start with the document ID
        # ChromaDB doesn't support prefix matching, so we filter by metadata
        try:
            results = collection.get(
                where={"document_id": document_id},
                include=["documents", "metadatas", "embeddings"],
            )
        except Exception:
            # If document_id metadata doesn't exist, return empty
            return []

        chunks = []
        if results["ids"]:
            # Use explicit None checks - numpy arrays can't be used in boolean context
            embeddings_list = results.get("embeddings")
            if embeddings_list is None:
                embeddings_list = []
            documents_list = results.get("documents")
            if documents_list is None:
                documents_list = []
            metadatas_list = results.get("metadatas")
            if metadatas_list is None:
                metadatas_list = []

            for i, chunk_id in enumerate(results["ids"]):
                # Convert embedding to list if it's a numpy array
                embedding: list[float] = []
                if i < len(embeddings_list) and embeddings_list[i] is not None:
                    emb = embeddings_list[i]
                    embedding = emb.tolist() if hasattr(emb, "tolist") else list(emb)

                chunks.append(
                    ChunkRecord(
                        chunk_id=chunk_id,
                        text=documents_list[i] if i < len(documents_list) else "",
                        embedding=embedding,
                        metadata=metadatas_list[i] if i < len(metadatas_list) else {},
                    )
                )

        # Sort by chunk index
        chunks.sort(key=lambda c: c.metadata.get("chunk_index", 0))
        return chunks

    def delete_document(self, document_id: str) -> int:
        """
        Delete all chunks for a document.

        Implements [document-processing:ChromaClient/TS-06]

        Args:
            document_id: The document ID.

        Returns:
            Number of chunks deleted.
        """
        collection = self._get_collection()

        # Get chunks to delete
        try:
            results = collection.get(
                where={"document_id": document_id},
            )
        except Exception:
            return 0

        if not results["ids"]:
            return 0

        chunk_ids = results["ids"]
        collection.delete(ids=chunk_ids)

        # Also remove from registry
        try:
            registry = self._get_registry_collection()
            registry.delete(ids=[document_id])
        except Exception:
            pass

        logger.info("Document deleted", document_id=document_id, chunks_deleted=len(chunk_ids))
        return len(chunk_ids)

    def register_document(self, record: DocumentRecord) -> None:
        """
        Register a document in the document registry.

        Args:
            record: Document metadata record.
        """
        registry = self._get_registry_collection()

        # Store as a simple record with metadata
        registry.upsert(
            ids=[record.document_id],
            documents=[record.file_path],
            metadatas=[
                {
                    "file_path": record.file_path,
                    "file_hash": record.file_hash,
                    "application_ref": record.application_ref,
                    "document_type": record.document_type,
                    "chunk_count": record.chunk_count,
                    "ingested_at": record.ingested_at,
                    "extraction_method": record.extraction_method,
                    "contains_drawings": record.contains_drawings,
                }
            ],
            embeddings=[[0.0] * 384],  # Placeholder embedding for registry
        )

    def get_document_record(self, document_id: str) -> DocumentRecord | None:
        """
        Get a document record from the registry.

        Args:
            document_id: The document ID.

        Returns:
            Document record if found, None otherwise.
        """
        registry = self._get_registry_collection()

        try:
            results = registry.get(ids=[document_id], include=["metadatas"])
        except Exception:
            return None

        if not results["ids"] or not results["metadatas"]:
            return None

        meta = results["metadatas"][0]
        return DocumentRecord(
            document_id=document_id,
            file_path=meta.get("file_path", ""),
            file_hash=meta.get("file_hash", ""),
            application_ref=meta.get("application_ref", ""),
            document_type=meta.get("document_type", ""),
            chunk_count=meta.get("chunk_count", 0),
            ingested_at=meta.get("ingested_at", ""),
            extraction_method=meta.get("extraction_method", "text_layer"),
            contains_drawings=meta.get("contains_drawings", False),
        )

    def is_document_ingested(self, file_hash: str, application_ref: str) -> bool:
        """
        Check if a document with this hash has been ingested.

        Args:
            file_hash: SHA256 hash of the file.
            application_ref: Application reference.

        Returns:
            True if document is already ingested with matching hash.
        """
        document_id = self.generate_document_id(application_ref, file_hash)
        record = self.get_document_record(document_id)
        return record is not None and record.file_hash == file_hash

    def list_documents_by_application(self, application_ref: str) -> list[DocumentRecord]:
        """
        List all documents for an application.

        Args:
            application_ref: Application reference.

        Returns:
            List of document records.
        """
        registry = self._get_registry_collection()

        try:
            results = registry.get(
                where={"application_ref": application_ref},
                include=["metadatas"],
            )
        except Exception:
            return []

        records = []
        if results["ids"]:
            for i, doc_id in enumerate(results["ids"]):
                meta = results["metadatas"][i] if results["metadatas"] else {}
                records.append(
                    DocumentRecord(
                        document_id=doc_id,
                        file_path=meta.get("file_path", ""),
                        file_hash=meta.get("file_hash", ""),
                        application_ref=meta.get("application_ref", ""),
                        document_type=meta.get("document_type", ""),
                        chunk_count=meta.get("chunk_count", 0),
                        ingested_at=meta.get("ingested_at", ""),
                        extraction_method=meta.get("extraction_method", "text_layer"),
                        contains_drawings=meta.get("contains_drawings", False),
                    )
                )

        return records

    def get_collection_stats(self) -> dict[str, Any]:
        """Get statistics about the collection."""
        collection = self._get_collection()
        registry = self._get_registry_collection()

        return {
            "total_chunks": collection.count(),
            "total_documents": registry.count(),
            "collection_name": self.COLLECTION_NAME,
        }
