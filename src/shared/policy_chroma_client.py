"""
ChromaDB client for policy document storage and retrieval.

Implements [policy-knowledge-base:FR-004] - Store chunks with temporal metadata
Implements [policy-knowledge-base:FR-014] - ChromaDB stores embeddings
Implements [policy-knowledge-base:NFR-004] - Atomic deletion

Implements:
- [policy-knowledge-base:ChromaDBSchema/TS-01] Temporal filter returns correct revision
- [policy-knowledge-base:ChromaDBSchema/TS-02] Source filter works
- [policy-knowledge-base:ChromaDBSchema/TS-03] Empty effective_to means current
"""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import chromadb
import structlog
from chromadb.config import Settings

logger = structlog.get_logger(__name__)


@dataclass
class PolicyChunkRecord:
    """A chunk stored in the policy_docs ChromaDB collection."""

    chunk_id: str
    text: str
    embedding: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicySearchResult:
    """A search result from the policy_docs collection."""

    chunk_id: str
    text: str
    relevance_score: float
    source: str
    revision_id: str
    version_label: str
    effective_from: date | None  # Parsed from metadata
    effective_to: date | None  # Parsed from metadata (None = currently in force)
    section_ref: str
    page_number: int
    metadata: dict[str, Any] = field(default_factory=dict)


class PolicyChromaClient:
    """
    Client for ChromaDB operations on the policy_docs collection.

    Handles policy document chunks with temporal metadata for effective date
    filtering. Supports source-based filtering and revision-based deletion.

    Key features:
    - Chunks include effective_from/effective_to for temporal queries
    - Source and revision_id metadata for filtering
    - Atomic deletion by revision_id
    """

    COLLECTION_NAME = "policy_docs"

    def __init__(
        self,
        persist_directory: str | Path | None = None,
        client: chromadb.ClientAPI | None = None,
    ) -> None:
        """
        Initialize the PolicyChromaClient.

        Args:
            persist_directory: Directory for persistent storage. If None, uses in-memory.
            client: Optional pre-configured ChromaDB client (for testing).
        """
        self._persist_directory = Path(persist_directory) if persist_directory else None
        self._client = client
        self._collection: chromadb.Collection | None = None

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
                    "PolicyChromaClient persistent client created",
                    path=str(self._persist_directory),
                )
            else:
                self._client = chromadb.Client(
                    settings=Settings(anonymized_telemetry=False),
                )
                logger.info("PolicyChromaClient in-memory client created")
        return self._client

    def _get_collection(self) -> chromadb.Collection:
        """Get or create the policy_docs collection."""
        if self._collection is None:
            client = self._get_client()
            self._collection = client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                metadata={"description": "Policy document chunks with temporal metadata"},
            )
            logger.debug("Collection initialized", name=self.COLLECTION_NAME)
        return self._collection

    @staticmethod
    def generate_chunk_id(
        source: str,
        revision_id: str,
        section_ref: str,
        chunk_index: int,
    ) -> str:
        """
        Generate a deterministic chunk ID.

        Format: {source}__{revision_id}__{section}__{chunk_index}
        Example: LTN_1_20__rev_LTN_1_20_2020_07__Chapter_5__042
        """
        # Sanitize section_ref for use in ID
        safe_section = section_ref.replace(" ", "_").replace("/", "_") if section_ref else "doc"
        return f"{source}__{revision_id}__{safe_section}__{chunk_index:03d}"

    @staticmethod
    def format_date_for_metadata(d: date | None) -> int:
        """
        Format a date for ChromaDB metadata as integer YYYYMMDD.

        Returns 99991231 for None (meaning currently in force - far future date).
        ChromaDB requires numeric values for comparison operators.
        """
        if d is None:
            return 99991231  # Far future date for "currently in force"
        return d.year * 10000 + d.month * 100 + d.day

    @staticmethod
    def parse_date_from_metadata(value: int) -> date | None:
        """
        Parse a date from ChromaDB metadata integer YYYYMMDD format.

        Returns None if value is 99991231 (currently in force).
        """
        if value == 99991231:
            return None
        year = value // 10000
        month = (value % 10000) // 100
        day = value % 100
        return date(year, month, day)

    def upsert_chunk(self, chunk: PolicyChunkRecord) -> None:
        """
        Store or update a chunk in the collection.

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

        logger.debug("Policy chunk upserted", chunk_id=chunk.chunk_id)

    def upsert_chunks(self, chunks: list[PolicyChunkRecord]) -> int:
        """
        Store or update multiple chunks efficiently.

        Args:
            chunks: List of chunks to store.

        Returns:
            Number of chunks upserted.
        """
        if not chunks:
            return 0

        collection = self._get_collection()

        collection.upsert(
            ids=[c.chunk_id for c in chunks],
            embeddings=[c.embedding for c in chunks],
            documents=[c.text for c in chunks],
            metadatas=[c.metadata for c in chunks],
        )

        logger.debug("Policy chunks upserted", count=len(chunks))
        return len(chunks)

    def search(
        self,
        query_embedding: list[float],
        n_results: int = 10,
        effective_date: date | None = None,
        sources: list[str] | None = None,
        revision_id: str | None = None,
    ) -> list[PolicySearchResult]:
        """
        Search policy documents with optional temporal and source filtering.

        Implements [policy-knowledge-base:ChromaDBSchema/TS-01] - Temporal filter
        Implements [policy-knowledge-base:ChromaDBSchema/TS-02] - Source filter
        Implements [policy-knowledge-base:ChromaDBSchema/TS-03] - Empty effective_to

        Args:
            query_embedding: The query embedding vector.
            n_results: Maximum number of results to return.
            effective_date: Optional date for temporal filtering.
            sources: Optional list of source slugs to filter.
            revision_id: Optional specific revision ID to search.

        Returns:
            List of search results ordered by relevance.
        """
        collection = self._get_collection()

        # Build where clause for filters
        where_conditions: list[dict[str, Any]] = []

        # Temporal filtering
        if effective_date is not None:
            date_int = self.format_date_for_metadata(effective_date)
            # effective_from <= effective_date
            where_conditions.append({"effective_from": {"$lte": date_int}})
            # effective_to >= effective_date (99991231 means currently in force, which is always >= any date)
            where_conditions.append({"effective_to": {"$gte": date_int}})

        # Source filtering
        if sources and len(sources) == 1:
            where_conditions.append({"source": sources[0]})
        elif sources and len(sources) > 1:
            where_conditions.append({"source": {"$in": sources}})

        # Specific revision filtering
        if revision_id:
            where_conditions.append({"revision_id": revision_id})

        # Combine conditions
        where: dict[str, Any] | None = None
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
            logger.error("Policy search failed", error=str(e))
            return []

        # Convert to PolicySearchResult objects
        search_results: list[PolicySearchResult] = []
        if results["ids"] and results["ids"][0]:
            ids = results["ids"][0]
            documents = results["documents"][0] if results["documents"] else []
            metadatas = results["metadatas"][0] if results["metadatas"] else []
            distances = results["distances"][0] if results["distances"] else []

            for i, chunk_id in enumerate(ids):
                # Convert distance to relevance score
                distance = distances[i] if i < len(distances) else 0
                relevance_score = max(0, 1 - (distance / 2))

                meta = metadatas[i] if i < len(metadatas) else {}

                # Parse dates from integer format
                effective_from_int = meta.get("effective_from", 0)
                effective_to_int = meta.get("effective_to", 99991231)

                search_results.append(
                    PolicySearchResult(
                        chunk_id=chunk_id,
                        text=documents[i] if i < len(documents) else "",
                        relevance_score=relevance_score,
                        source=meta.get("source", ""),
                        revision_id=meta.get("revision_id", ""),
                        version_label=meta.get("version_label", ""),
                        effective_from=self.parse_date_from_metadata(effective_from_int),
                        effective_to=self.parse_date_from_metadata(effective_to_int),
                        section_ref=meta.get("section_ref", ""),
                        page_number=meta.get("page_number", 0),
                        metadata=meta,
                    )
                )

        return search_results

    def delete_revision_chunks(self, source: str, revision_id: str) -> int:
        """
        Delete all chunks for a specific revision.

        Implements [policy-knowledge-base:NFR-004] - Atomic deletion

        Args:
            source: Policy source slug.
            revision_id: Revision ID.

        Returns:
            Number of chunks deleted.
        """
        collection = self._get_collection()

        # Find all chunks for this revision
        try:
            results = collection.get(
                where={
                    "$and": [
                        {"source": source},
                        {"revision_id": revision_id},
                    ]
                },
            )
        except Exception as e:
            logger.error(
                "Failed to find revision chunks for deletion",
                source=source,
                revision_id=revision_id,
                error=str(e),
            )
            return 0

        if not results["ids"]:
            logger.info(
                "No chunks found for revision",
                source=source,
                revision_id=revision_id,
            )
            return 0

        chunk_ids = results["ids"]
        collection.delete(ids=chunk_ids)

        logger.info(
            "Revision chunks deleted",
            source=source,
            revision_id=revision_id,
            chunks_deleted=len(chunk_ids),
        )
        return len(chunk_ids)

    def get_revision_chunks(self, source: str, revision_id: str) -> list[PolicyChunkRecord]:
        """
        Get all chunks for a specific revision.

        Args:
            source: Policy source slug.
            revision_id: Revision ID.

        Returns:
            List of chunks ordered by chunk index.
        """
        collection = self._get_collection()

        try:
            results = collection.get(
                where={
                    "$and": [
                        {"source": source},
                        {"revision_id": revision_id},
                    ]
                },
                include=["documents", "metadatas", "embeddings"],
            )
        except Exception:
            return []

        chunks: list[PolicyChunkRecord] = []
        if results["ids"]:
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
                embedding: list[float] = []
                if i < len(embeddings_list) and embeddings_list[i] is not None:
                    emb = embeddings_list[i]
                    embedding = emb.tolist() if hasattr(emb, "tolist") else list(emb)

                chunks.append(
                    PolicyChunkRecord(
                        chunk_id=chunk_id,
                        text=documents_list[i] if i < len(documents_list) else "",
                        embedding=embedding,
                        metadata=metadatas_list[i] if i < len(metadatas_list) else {},
                    )
                )

        # Sort by chunk index from metadata
        chunks.sort(key=lambda c: c.metadata.get("chunk_index", 0))
        return chunks

    def get_chunk_count(self, source: str | None = None, revision_id: str | None = None) -> int:
        """
        Get the count of chunks, optionally filtered.

        Args:
            source: Optional source filter.
            revision_id: Optional revision ID filter.

        Returns:
            Number of matching chunks.
        """
        collection = self._get_collection()

        if source is None and revision_id is None:
            return collection.count()

        where_conditions: list[dict[str, Any]] = []
        if source:
            where_conditions.append({"source": source})
        if revision_id:
            where_conditions.append({"revision_id": revision_id})

        where: dict[str, Any] | None = None
        if len(where_conditions) == 1:
            where = where_conditions[0]
        elif len(where_conditions) > 1:
            where = {"$and": where_conditions}

        try:
            results = collection.get(where=where)
            return len(results["ids"]) if results["ids"] else 0
        except Exception:
            return 0

    def get_collection_stats(self) -> dict[str, Any]:
        """Get statistics about the collection."""
        collection = self._get_collection()

        return {
            "total_chunks": collection.count(),
            "collection_name": self.COLLECTION_NAME,
        }
