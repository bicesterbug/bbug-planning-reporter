"""
Tests for ChromaClient.

Implements test scenarios from [document-processing:ChromaClient/TS-01] through [TS-09]
"""

import uuid
from pathlib import Path

import chromadb
import pytest
from chromadb.config import Settings

from src.mcp_servers.document_store.chroma_client import (
    ChromaClient,
    ChunkRecord,
    DocumentRecord,
    SearchResult,
)


class IsolatedChromaClient(ChromaClient):
    """ChromaClient with unique collection names for test isolation."""

    def __init__(self, client: chromadb.ClientAPI, test_id: str) -> None:
        super().__init__(client=client)
        self._test_id = test_id

    @property
    def COLLECTION_NAME(self) -> str:  # type: ignore
        return f"test_docs_{self._test_id}"

    @property
    def DOCUMENT_REGISTRY_COLLECTION(self) -> str:  # type: ignore
        return f"test_registry_{self._test_id}"


@pytest.fixture
def chroma_client() -> ChromaClient:
    """Create an in-memory ChromaClient for testing with isolation."""
    # Create a fresh ephemeral client and use unique collection names
    client = chromadb.EphemeralClient(settings=Settings(anonymized_telemetry=False))
    test_id = uuid.uuid4().hex[:8]
    return IsolatedChromaClient(client=client, test_id=test_id)


@pytest.fixture
def sample_embedding() -> list[float]:
    """Create a sample 384-dim embedding."""
    import numpy as np

    rng = np.random.RandomState(42)
    embedding = rng.randn(384).astype(np.float32)
    embedding = embedding / np.linalg.norm(embedding)
    return embedding.tolist()


@pytest.fixture
def sample_chunk(sample_embedding: list[float]) -> ChunkRecord:
    """Create a sample chunk record."""
    return ChunkRecord(
        chunk_id="25_01178_REM_abc123_001_000",
        text="The proposed development includes 48 Sheffield cycle stands.",
        embedding=sample_embedding,
        metadata={
            "application_ref": "25/01178/REM",
            "document_id": "25_01178_REM_abc123",
            "source_file": "Transport_Assessment.pdf",
            "document_type": "transport_assessment",
            "page_number": 1,
            "chunk_index": 0,
        },
    )


class TestStoreChunk:
    """Tests for storing chunks."""

    def test_store_chunk_with_metadata(
        self, chroma_client: ChromaClient, sample_chunk: ChunkRecord
    ) -> None:
        """
        Verifies [document-processing:ChromaClient/TS-01]

        Given: Chunk text, embedding, metadata
        When: Call upsert_chunk()
        Then: Chunk stored, retrievable by ID
        """
        chroma_client.upsert_chunk(sample_chunk)

        # Retrieve and verify
        collection = chroma_client._get_collection()
        results = collection.get(ids=[sample_chunk.chunk_id], include=["documents", "metadatas"])

        assert len(results["ids"]) == 1
        assert results["ids"][0] == sample_chunk.chunk_id
        assert results["documents"][0] == sample_chunk.text
        assert results["metadatas"][0]["application_ref"] == "25/01178/REM"

    def test_idempotent_upsert(
        self, chroma_client: ChromaClient, sample_chunk: ChunkRecord
    ) -> None:
        """
        Verifies [document-processing:ChromaClient/TS-02]

        Given: Same chunk ID twice
        When: Call upsert_chunk() twice
        Then: No duplicate, same data stored
        """
        chroma_client.upsert_chunk(sample_chunk)
        chroma_client.upsert_chunk(sample_chunk)

        collection = chroma_client._get_collection()
        assert collection.count() == 1

    def test_upsert_multiple_chunks(
        self, chroma_client: ChromaClient, sample_embedding: list[float]
    ) -> None:
        """
        Given: Multiple chunks
        When: Call upsert_chunks()
        Then: All chunks stored efficiently
        """
        chunks = [
            ChunkRecord(
                chunk_id=f"25_01178_REM_abc123_001_{i:03d}",
                text=f"Chunk {i} content",
                embedding=sample_embedding,
                metadata={
                    "application_ref": "25/01178/REM",
                    "document_id": "25_01178_REM_abc123",
                    "chunk_index": i,
                },
            )
            for i in range(10)
        ]

        chroma_client.upsert_chunks(chunks)

        collection = chroma_client._get_collection()
        assert collection.count() == 10


class TestSemanticSearch:
    """Tests for semantic search."""

    def test_semantic_search(
        self, chroma_client: ChromaClient, sample_embedding: list[float]
    ) -> None:
        """
        Verifies [document-processing:ChromaClient/TS-03]

        Given: Query and collection with docs
        When: Call search()
        Then: Returns ranked results with scores
        """
        # Add some chunks
        chunks = [
            ChunkRecord(
                chunk_id="chunk_1",
                text="Cycle parking provision includes Sheffield stands",
                embedding=sample_embedding,
                metadata={"application_ref": "25/01178/REM", "document_id": "doc_1"},
            ),
            ChunkRecord(
                chunk_id="chunk_2",
                text="Transport assessment traffic analysis",
                embedding=[e + 0.1 for e in sample_embedding],  # Slightly different
                metadata={"application_ref": "25/01178/REM", "document_id": "doc_1"},
            ),
        ]
        chroma_client.upsert_chunks(chunks)

        # Search
        results = chroma_client.search(query_embedding=sample_embedding, n_results=5)

        assert len(results) == 2
        assert all(isinstance(r, SearchResult) for r in results)
        # First result should be more relevant (closer embedding)
        assert results[0].relevance_score >= results[1].relevance_score

    def test_search_with_filter(
        self, chroma_client: ChromaClient, sample_embedding: list[float]
    ) -> None:
        """
        Verifies [document-processing:ChromaClient/TS-04]

        Given: Query with application_ref filter
        When: Call search()
        Then: Returns only matching application docs
        """
        # Add chunks from different applications
        chunks = [
            ChunkRecord(
                chunk_id="chunk_app1",
                text="Content from app 1",
                embedding=sample_embedding,
                metadata={"application_ref": "25/01178/REM", "document_id": "doc_1"},
            ),
            ChunkRecord(
                chunk_id="chunk_app2",
                text="Content from app 2",
                embedding=sample_embedding,
                metadata={"application_ref": "25/99999/F", "document_id": "doc_2"},
            ),
        ]
        chroma_client.upsert_chunks(chunks)

        # Search with filter
        results = chroma_client.search(
            query_embedding=sample_embedding,
            application_ref="25/01178/REM",
        )

        assert len(results) == 1
        assert results[0].metadata["application_ref"] == "25/01178/REM"

    def test_search_empty_collection(
        self, chroma_client: ChromaClient, sample_embedding: list[float]
    ) -> None:
        """
        Verifies [document-processing:ChromaClient/TS-05]

        Given: Query on empty collection
        When: Call search()
        Then: Returns empty results, no error
        """
        results = chroma_client.search(query_embedding=sample_embedding)

        assert results == []


class TestDeleteDocument:
    """Tests for document deletion."""

    def test_delete_chunks_by_document(
        self, chroma_client: ChromaClient, sample_embedding: list[float]
    ) -> None:
        """
        Verifies [document-processing:ChromaClient/TS-06]

        Given: Document ID
        When: Call delete_document()
        Then: All chunks for document removed
        """
        # Add chunks for two documents
        doc1_chunks = [
            ChunkRecord(
                chunk_id=f"doc1_chunk_{i}",
                text=f"Doc 1 chunk {i}",
                embedding=sample_embedding,
                metadata={"document_id": "doc_1", "application_ref": "25/01178/REM"},
            )
            for i in range(5)
        ]
        doc2_chunks = [
            ChunkRecord(
                chunk_id=f"doc2_chunk_{i}",
                text=f"Doc 2 chunk {i}",
                embedding=sample_embedding,
                metadata={"document_id": "doc_2", "application_ref": "25/01178/REM"},
            )
            for i in range(3)
        ]
        chroma_client.upsert_chunks(doc1_chunks + doc2_chunks)

        # Delete doc_1
        deleted = chroma_client.delete_document("doc_1")

        assert deleted == 5
        # Only doc_2 chunks should remain
        collection = chroma_client._get_collection()
        assert collection.count() == 3


class TestGetDocumentChunks:
    """Tests for retrieving document chunks."""

    def test_get_chunks_by_document(
        self, chroma_client: ChromaClient, sample_embedding: list[float]
    ) -> None:
        """
        Verifies [document-processing:ChromaClient/TS-07]

        Given: Document ID with 10 chunks
        When: Call get_document_chunks()
        Then: Returns all 10 chunks in order
        """
        chunks = [
            ChunkRecord(
                chunk_id=f"doc_chunk_{i}",
                text=f"Chunk {i} content",
                embedding=sample_embedding,
                metadata={
                    "document_id": "test_doc",
                    "chunk_index": i,
                    "application_ref": "25/01178/REM",
                },
            )
            for i in range(10)
        ]
        # Insert in random order
        import random

        shuffled = chunks.copy()
        random.shuffle(shuffled)
        chroma_client.upsert_chunks(shuffled)

        # Retrieve
        retrieved = chroma_client.get_document_chunks("test_doc")

        assert len(retrieved) == 10
        # Should be sorted by chunk_index
        for i, chunk in enumerate(retrieved):
            assert chunk.metadata["chunk_index"] == i


class TestCollectionInitialization:
    """Tests for collection initialization."""

    def test_collection_initialization(self, chroma_client: ChromaClient) -> None:
        """
        Verifies [document-processing:ChromaClient/TS-09]

        Given: Fresh ChromaDB
        When: Access collection
        Then: Creates collection if not exists
        """
        collection = chroma_client._get_collection()

        assert collection is not None
        # IsolatedChromaClient uses dynamic collection names for test isolation
        assert collection.name == chroma_client.COLLECTION_NAME


class TestDocumentRegistry:
    """Tests for document registry operations."""

    def test_register_document(self, chroma_client: ChromaClient) -> None:
        """Test document registration."""
        record = DocumentRecord(
            document_id="25_01178_REM_abc123",
            file_path="/data/raw/25_01178_REM/Transport_Assessment.pdf",
            file_hash="abc123def456",
            application_ref="25/01178/REM",
            document_type="transport_assessment",
            chunk_count=47,
            ingested_at="2025-02-05T10:30:00Z",
            extraction_method="text_layer",
            contains_drawings=False,
        )

        chroma_client.register_document(record)

        # Retrieve
        retrieved = chroma_client.get_document_record("25_01178_REM_abc123")

        assert retrieved is not None
        assert retrieved.file_hash == "abc123def456"
        assert retrieved.chunk_count == 47

    def test_is_document_ingested(self, chroma_client: ChromaClient) -> None:
        """Test checking if document is ingested."""
        # Not ingested
        assert not chroma_client.is_document_ingested("abc123", "25/01178/REM")

        # Register
        record = DocumentRecord(
            document_id="25_01178_REM_abc123",
            file_path="/path/to/file.pdf",
            file_hash="abc123def456",
            application_ref="25/01178/REM",
            document_type="transport_assessment",
            chunk_count=10,
            ingested_at="2025-02-05T10:30:00Z",
        )
        chroma_client.register_document(record)

        # Now should be ingested
        assert chroma_client.is_document_ingested("abc123def456", "25/01178/REM")

    def test_list_documents_by_application(self, chroma_client: ChromaClient) -> None:
        """Test listing documents by application."""
        # Register multiple documents
        for i in range(3):
            record = DocumentRecord(
                document_id=f"25_01178_REM_doc{i}",
                file_path=f"/path/to/doc{i}.pdf",
                file_hash=f"hash{i}",
                application_ref="25/01178/REM",
                document_type="transport_assessment",
                chunk_count=10,
                ingested_at="2025-02-05T10:30:00Z",
            )
            chroma_client.register_document(record)

        # List
        docs = chroma_client.list_documents_by_application("25/01178/REM")

        assert len(docs) == 3


class TestHelperMethods:
    """Tests for helper methods."""

    def test_generate_chunk_id(self) -> None:
        """Test chunk ID generation."""
        chunk_id = ChromaClient.generate_chunk_id(
            application_ref="25/01178/REM",
            file_hash="abc123def456",
            page_number=14,
            chunk_index=42,
        )

        assert chunk_id == "25_01178_REM_abc123_014_042"

    def test_generate_document_id(self) -> None:
        """Test document ID generation."""
        doc_id = ChromaClient.generate_document_id(
            application_ref="25/01178/REM",
            file_hash="abc123def456",
        )

        assert doc_id == "25_01178_REM_abc123"

    def test_compute_file_hash(self, tmp_path: Path) -> None:
        """Test file hash computation."""
        # Create a test file
        test_file = tmp_path / "test.pdf"
        test_file.write_text("Test content for hashing")

        hash1 = ChromaClient.compute_file_hash(test_file)
        hash2 = ChromaClient.compute_file_hash(test_file)

        # Should be deterministic
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex


class TestCollectionStats:
    """Tests for collection statistics."""

    def test_get_collection_stats(
        self, chroma_client: ChromaClient, sample_embedding: list[float]
    ) -> None:
        """Test getting collection statistics."""
        # Add some data
        chunks = [
            ChunkRecord(
                chunk_id=f"chunk_{i}",
                text=f"Content {i}",
                embedding=sample_embedding,
                metadata={"document_id": "doc_1"},
            )
            for i in range(5)
        ]
        chroma_client.upsert_chunks(chunks)

        stats = chroma_client.get_collection_stats()

        assert stats["total_chunks"] == 5
        # IsolatedChromaClient uses dynamic collection names for test isolation
        assert stats["collection_name"] == chroma_client.COLLECTION_NAME
