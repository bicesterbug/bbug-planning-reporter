"""
Tests for PolicyChromaClient.

Implements test scenarios from [policy-knowledge-base:ChromaDBSchema/TS-01] through [TS-03]
"""

from datetime import date

import chromadb
import pytest
from chromadb.config import Settings

from src.shared.policy_chroma_client import (
    PolicyChromaClient,
    PolicyChunkRecord,
)


@pytest.fixture
def chroma_client():
    """Create an in-memory ChromaDB client for testing with fresh collection."""
    client = chromadb.Client(settings=Settings(anonymized_telemetry=False))
    # Delete collection if exists to ensure clean state
    try:
        client.delete_collection(PolicyChromaClient.COLLECTION_NAME)
    except Exception:
        pass
    return PolicyChromaClient(client=client)


@pytest.fixture
def sample_chunks():
    """Create sample chunks with different revisions."""
    # Mock embeddings (384-dim normalized vectors)
    def mock_embedding(seed: int) -> list[float]:
        import random
        random.seed(seed)
        vec = [random.gauss(0, 1) for _ in range(384)]
        norm = sum(x*x for x in vec) ** 0.5
        return [x / norm for x in vec]

    # Use integer dates: YYYYMMDD format
    # 99991231 = currently in force (far future)
    chunks = [
        # LTN 1/20 revision from July 2020 (currently in force)
        PolicyChunkRecord(
            chunk_id="LTN_1_20__rev_LTN_1_20_2020_07__Chapter_5__001",
            text="Cycle lanes should be 2.0m wide on busy roads.",
            embedding=mock_embedding(1),
            metadata={
                "source": "LTN_1_20",
                "source_title": "Cycle Infrastructure Design (LTN 1/20)",
                "revision_id": "rev_LTN_1_20_2020_07",
                "version_label": "July 2020",
                "effective_from": 20200727,  # 2020-07-27
                "effective_to": 99991231,  # Currently in force
                "section_ref": "Chapter 5",
                "page_number": 42,
                "chunk_index": 1,
            },
        ),
        PolicyChunkRecord(
            chunk_id="LTN_1_20__rev_LTN_1_20_2020_07__Chapter_5__002",
            text="Protected cycle lanes provide safety benefits.",
            embedding=mock_embedding(2),
            metadata={
                "source": "LTN_1_20",
                "source_title": "Cycle Infrastructure Design (LTN 1/20)",
                "revision_id": "rev_LTN_1_20_2020_07",
                "version_label": "July 2020",
                "effective_from": 20200727,
                "effective_to": 99991231,
                "section_ref": "Chapter 5",
                "page_number": 43,
                "chunk_index": 2,
            },
        ),
        # NPPF revision from September 2023 (superseded)
        PolicyChunkRecord(
            chunk_id="NPPF__rev_NPPF_2023_09__Para_116__001",
            text="Development should give priority to pedestrian and cycle movements.",
            embedding=mock_embedding(3),
            metadata={
                "source": "NPPF",
                "source_title": "National Planning Policy Framework",
                "revision_id": "rev_NPPF_2023_09",
                "version_label": "September 2023",
                "effective_from": 20230905,  # 2023-09-05
                "effective_to": 20241211,  # 2024-12-11 (superseded)
                "section_ref": "Para 116",
                "page_number": 34,
                "chunk_index": 1,
            },
        ),
        # NPPF revision from December 2024 (currently in force)
        PolicyChunkRecord(
            chunk_id="NPPF__rev_NPPF_2024_12__Para_116__001",
            text="Development must prioritise walking and cycling over motor vehicles.",
            embedding=mock_embedding(4),
            metadata={
                "source": "NPPF",
                "source_title": "National Planning Policy Framework",
                "revision_id": "rev_NPPF_2024_12",
                "version_label": "December 2024",
                "effective_from": 20241212,  # 2024-12-12
                "effective_to": 99991231,  # Currently in force
                "section_ref": "Para 116",
                "page_number": 35,
                "chunk_index": 1,
            },
        ),
    ]
    return chunks


class TestUpsertChunks:
    """Tests for upserting chunks."""

    def test_upsert_single_chunk(self, chroma_client):
        """Test upserting a single chunk."""
        chunk = PolicyChunkRecord(
            chunk_id="test__rev_test_2024_01__doc__001",
            text="Test content",
            embedding=[0.1] * 384,
            metadata={
                "source": "TEST",
                "revision_id": "rev_test_2024_01",
                "effective_from": 20240101,
                "effective_to": 99991231,
            },
        )

        chroma_client.upsert_chunk(chunk)

        stats = chroma_client.get_collection_stats()
        assert stats["total_chunks"] == 1

    def test_upsert_multiple_chunks(self, chroma_client, sample_chunks):
        """Test upserting multiple chunks."""
        count = chroma_client.upsert_chunks(sample_chunks)

        assert count == 4
        stats = chroma_client.get_collection_stats()
        assert stats["total_chunks"] == 4

    def test_upsert_idempotent(self, chroma_client, sample_chunks):
        """Test that upserting same chunks twice is idempotent."""
        chroma_client.upsert_chunks(sample_chunks)
        chroma_client.upsert_chunks(sample_chunks)

        stats = chroma_client.get_collection_stats()
        assert stats["total_chunks"] == 4


class TestTemporalFiltering:
    """
    Tests for temporal filtering.

    Implements [policy-knowledge-base:ChromaDBSchema/TS-01] - Temporal filter returns correct revision
    Implements [policy-knowledge-base:ChromaDBSchema/TS-03] - Empty effective_to means current
    """

    def test_temporal_filter_returns_correct_revision(self, chroma_client, sample_chunks):
        """
        Verifies [policy-knowledge-base:ChromaDBSchema/TS-01] - Temporal filter returns correct revision

        Given: Chunks from 3 revisions (LTN 2020, NPPF 2023, NPPF 2024)
        When: Query with effective_date 2024-03-15
        Then: Only chunks from in-force revisions returned (LTN 2020, NPPF 2023)
        """
        chroma_client.upsert_chunks(sample_chunks)

        # Query for a date in March 2024
        # Should get LTN 1/20 (2020-07-27 to current) and NPPF 2023 (2023-09-05 to 2024-12-11)
        # Should NOT get NPPF 2024 (effective from 2024-12-12)
        query_embedding = [0.1] * 384
        results = chroma_client.search(
            query_embedding=query_embedding,
            effective_date=date(2024, 3, 15),
            n_results=10,
        )

        # Check that we get results from the correct revisions
        revision_ids = {r.revision_id for r in results}
        assert "rev_LTN_1_20_2020_07" in revision_ids
        assert "rev_NPPF_2023_09" in revision_ids
        assert "rev_NPPF_2024_12" not in revision_ids

    def test_empty_effective_to_means_current(self, chroma_client, sample_chunks):
        """
        Verifies [policy-knowledge-base:ChromaDBSchema/TS-03] - Empty effective_to means current

        Given: Chunk with effective_to=""
        When: Query with current date
        Then: Chunk included in results
        """
        chroma_client.upsert_chunks(sample_chunks)

        # Query for a date in January 2025 (after NPPF 2024 became effective)
        query_embedding = [0.1] * 384
        results = chroma_client.search(
            query_embedding=query_embedding,
            effective_date=date(2025, 1, 15),
            n_results=10,
        )

        # Should get LTN 1/20 (still current) and NPPF 2024 (now current)
        # Should NOT get NPPF 2023 (superseded on 2024-12-11)
        revision_ids = {r.revision_id for r in results}
        assert "rev_LTN_1_20_2020_07" in revision_ids
        assert "rev_NPPF_2024_12" in revision_ids
        assert "rev_NPPF_2023_09" not in revision_ids

    def test_date_before_any_revision(self, chroma_client, sample_chunks):
        """Test querying with date before any revisions exist."""
        chroma_client.upsert_chunks(sample_chunks)

        # Query for a date before LTN 1/20 (first revision)
        query_embedding = [0.1] * 384
        results = chroma_client.search(
            query_embedding=query_embedding,
            effective_date=date(2019, 1, 1),
            n_results=10,
        )

        # No revisions should match
        assert len(results) == 0


class TestSourceFiltering:
    """
    Tests for source filtering.

    Implements [policy-knowledge-base:ChromaDBSchema/TS-02] - Source filter works
    """

    def test_source_filter_single(self, chroma_client, sample_chunks):
        """
        Verifies [policy-knowledge-base:ChromaDBSchema/TS-02] - Source filter works

        Given: Chunks from multiple policies
        When: Query with source filter
        Then: Only specified policy chunks returned
        """
        chroma_client.upsert_chunks(sample_chunks)

        query_embedding = [0.1] * 384
        results = chroma_client.search(
            query_embedding=query_embedding,
            sources=["LTN_1_20"],
            n_results=10,
        )

        # All results should be from LTN_1_20
        assert all(r.source == "LTN_1_20" for r in results)
        assert len(results) == 2

    def test_source_filter_multiple(self, chroma_client, sample_chunks):
        """Test filtering by multiple sources."""
        chroma_client.upsert_chunks(sample_chunks)

        query_embedding = [0.1] * 384
        results = chroma_client.search(
            query_embedding=query_embedding,
            sources=["LTN_1_20", "NPPF"],
            n_results=10,
        )

        # Should get results from both sources
        sources = {r.source for r in results}
        assert "LTN_1_20" in sources
        assert "NPPF" in sources


class TestRevisionOperations:
    """Tests for revision-level operations."""

    def test_get_revision_chunks(self, chroma_client, sample_chunks):
        """Test getting all chunks for a revision."""
        chroma_client.upsert_chunks(sample_chunks)

        chunks = chroma_client.get_revision_chunks("LTN_1_20", "rev_LTN_1_20_2020_07")

        assert len(chunks) == 2
        # Should be sorted by chunk_index
        assert chunks[0].metadata["chunk_index"] == 1
        assert chunks[1].metadata["chunk_index"] == 2

    def test_delete_revision_chunks(self, chroma_client, sample_chunks):
        """Test deleting all chunks for a revision."""
        chroma_client.upsert_chunks(sample_chunks)

        # Delete LTN 1/20 chunks
        deleted_count = chroma_client.delete_revision_chunks("LTN_1_20", "rev_LTN_1_20_2020_07")

        assert deleted_count == 2

        # Verify they're gone
        remaining = chroma_client.get_revision_chunks("LTN_1_20", "rev_LTN_1_20_2020_07")
        assert len(remaining) == 0

        # NPPF chunks should still exist
        stats = chroma_client.get_collection_stats()
        assert stats["total_chunks"] == 2

    def test_delete_nonexistent_revision(self, chroma_client):
        """Test deleting chunks for a revision that doesn't exist."""
        deleted_count = chroma_client.delete_revision_chunks("NONEXISTENT", "rev_none")
        assert deleted_count == 0


class TestChunkIdGeneration:
    """Tests for chunk ID generation."""

    def test_generate_chunk_id(self):
        """Test chunk ID generation format."""
        chunk_id = PolicyChromaClient.generate_chunk_id(
            source="LTN_1_20",
            revision_id="rev_LTN_1_20_2020_07",
            section_ref="Chapter 5",
            chunk_index=42,
        )

        assert chunk_id == "LTN_1_20__rev_LTN_1_20_2020_07__Chapter_5__042"

    def test_generate_chunk_id_special_chars(self):
        """Test chunk ID generation with special characters."""
        chunk_id = PolicyChromaClient.generate_chunk_id(
            source="NPPF",
            revision_id="rev_NPPF_2024_12",
            section_ref="Table 5-2/Annex A",
            chunk_index=1,
        )

        # Slashes and spaces should be replaced
        assert "/" not in chunk_id
        assert " " not in chunk_id


class TestDateFormatting:
    """Tests for date formatting."""

    def test_format_date(self):
        """Test date formatting for metadata as integer YYYYMMDD."""
        result = PolicyChromaClient.format_date_for_metadata(date(2024, 3, 15))
        assert result == 20240315

    def test_format_none_date(self):
        """Test formatting None date (means currently in force - far future)."""
        result = PolicyChromaClient.format_date_for_metadata(None)
        assert result == 99991231

    def test_parse_date(self):
        """Test parsing date from integer format."""
        result = PolicyChromaClient.parse_date_from_metadata(20240315)
        assert result == date(2024, 3, 15)

    def test_parse_none_date(self):
        """Test parsing far future date as None (currently in force)."""
        result = PolicyChromaClient.parse_date_from_metadata(99991231)
        assert result is None


class TestGetChunkCount:
    """Tests for chunk counting."""

    def test_get_total_chunk_count(self, chroma_client, sample_chunks):
        """Test getting total chunk count."""
        chroma_client.upsert_chunks(sample_chunks)

        count = chroma_client.get_chunk_count()
        assert count == 4

    def test_get_chunk_count_by_source(self, chroma_client, sample_chunks):
        """Test getting chunk count filtered by source."""
        chroma_client.upsert_chunks(sample_chunks)

        count = chroma_client.get_chunk_count(source="LTN_1_20")
        assert count == 2

    def test_get_chunk_count_by_revision(self, chroma_client, sample_chunks):
        """Test getting chunk count filtered by revision."""
        chroma_client.upsert_chunks(sample_chunks)

        count = chroma_client.get_chunk_count(revision_id="rev_NPPF_2023_09")
        assert count == 1
