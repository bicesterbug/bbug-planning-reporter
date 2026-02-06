"""
Tests for policy ingestion jobs.

Implements test scenarios from [policy-knowledge-base:PolicyIngestionJob/TS-01] through [TS-05]
"""

import contextlib
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

import chromadb
import pytest
from chromadb.config import Settings

from src.api.schemas.policy import PolicyRevisionRecord, RevisionStatus
from src.mcp_servers.document_store.chunker import TextChunk
from src.mcp_servers.document_store.embeddings import MockEmbeddingModel
from src.mcp_servers.document_store.processor import DocumentExtraction, PageExtraction
from src.shared.policy_chroma_client import PolicyChromaClient
from src.worker.policy_jobs import PolicyIngestionService


@pytest.fixture
def mock_registry():
    """Create mock PolicyRegistry."""
    registry = AsyncMock()
    registry.get_revision = AsyncMock()
    registry.update_revision = AsyncMock()
    return registry


@pytest.fixture
def chroma_client():
    """Create an in-memory ChromaDB client for testing."""
    client = chromadb.Client(settings=Settings(anonymized_telemetry=False))
    with contextlib.suppress(Exception):
        client.delete_collection(PolicyChromaClient.COLLECTION_NAME)
    return PolicyChromaClient(client=client)


@pytest.fixture
def mock_processor():
    """Create mock DocumentProcessor."""
    processor = MagicMock()
    return processor


@pytest.fixture
def mock_chunker():
    """Create mock TextChunker."""
    chunker = MagicMock()
    return chunker


@pytest.fixture
def mock_embedder():
    """Create mock EmbeddingService with deterministic embeddings."""
    from src.mcp_servers.document_store.embeddings import EmbeddingService
    return EmbeddingService(model=MockEmbeddingModel())


@pytest.fixture
def sample_revision():
    """Create a sample revision record."""
    return PolicyRevisionRecord(
        revision_id="rev_LTN_1_20_2020_07",
        source="LTN_1_20",
        version_label="July 2020",
        effective_from=date(2020, 7, 27),
        effective_to=None,
        status=RevisionStatus.PROCESSING,
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def sample_extraction():
    """Create a sample document extraction result."""
    return DocumentExtraction(
        file_path="/data/policy/LTN_1_20/ltn_1_20.pdf",
        total_pages=3,
        pages=[
            PageExtraction(
                page_number=1,
                text="Chapter 5: Cycle Lane Design\n\nCycle lanes should be at least 2.0m wide.",
                extraction_method="text_layer",
                char_count=60,
                word_count=10,
            ),
            PageExtraction(
                page_number=2,
                text="Protected cycle lanes provide significant safety benefits.",
                extraction_method="text_layer",
                char_count=55,
                word_count=7,
            ),
            PageExtraction(
                page_number=3,
                text="Table 5-2 shows recommended minimum widths for different contexts.",
                extraction_method="text_layer",
                char_count=65,
                word_count=9,
            ),
        ],
        extraction_method="text_layer",
        contains_drawings=False,
        total_char_count=180,
        total_word_count=26,
    )


@pytest.fixture
def sample_chunks():
    """Create sample text chunks."""
    return [
        TextChunk(
            text="Chapter 5: Cycle Lane Design\n\nCycle lanes should be at least 2.0m wide.",
            chunk_index=0,
            char_count=60,
            word_count=10,
            page_numbers=[1],
        ),
        TextChunk(
            text="Protected cycle lanes provide significant safety benefits.",
            chunk_index=1,
            char_count=55,
            word_count=7,
            page_numbers=[2],
        ),
        TextChunk(
            text="Table 5-2 shows recommended minimum widths for different contexts.",
            chunk_index=2,
            char_count=65,
            word_count=9,
            page_numbers=[3],
        ),
    ]


class TestSuccessfulIngestion:
    """
    Tests for successful policy ingestion.

    Implements [policy-knowledge-base:PolicyIngestionJob/TS-01] - Successful ingestion
    """

    @pytest.mark.asyncio
    async def test_successful_ingestion(
        self,
        mock_registry,
        chroma_client,
        mock_processor,
        mock_chunker,
        mock_embedder,
        sample_revision,
        sample_extraction,
        sample_chunks,
    ):
        """
        Verifies [policy-knowledge-base:PolicyIngestionJob/TS-01] - Successful ingestion

        Given: Valid PDF file path
        When: Job processes
        Then: Status transitions processing -> active, chunks created in ChromaDB
        """
        # Setup mocks
        mock_registry.get_revision.return_value = sample_revision
        mock_processor.extract.return_value = sample_extraction
        mock_chunker.chunk_pages.return_value = sample_chunks

        service = PolicyIngestionService(
            registry=mock_registry,
            chroma_client=chroma_client,
            processor=mock_processor,
            chunker=mock_chunker,
            embedder=mock_embedder,
        )

        result = await service.ingest_revision(
            source="LTN_1_20",
            revision_id="rev_LTN_1_20_2020_07",
            file_path="/data/policy/LTN_1_20/ltn_1_20.pdf",
        )

        # Verify success
        assert result.success is True
        assert result.chunk_count == 3
        assert result.page_count == 3
        assert result.error is None

        # Verify revision was updated to active
        mock_registry.update_revision.assert_called()
        update_calls = mock_registry.update_revision.call_args_list
        # Last call should be status=active
        last_call_kwargs = update_calls[-1][1]
        assert last_call_kwargs["status"] == RevisionStatus.ACTIVE
        assert last_call_kwargs["chunk_count"] == 3

        # Verify chunks in ChromaDB
        chunks = chroma_client.get_revision_chunks("LTN_1_20", "rev_LTN_1_20_2020_07")
        assert len(chunks) == 3


class TestIngestionFailure:
    """
    Tests for ingestion failure handling.

    Implements [policy-knowledge-base:PolicyIngestionJob/TS-02] - Ingestion failure
    """

    @pytest.mark.asyncio
    async def test_corrupted_pdf_failure(
        self,
        mock_registry,
        chroma_client,
        mock_processor,
        mock_chunker,
        mock_embedder,
        sample_revision,
    ):
        """
        Verifies [policy-knowledge-base:PolicyIngestionJob/TS-02] - Ingestion failure

        Given: Corrupted PDF
        When: Job processes
        Then: Status transitions to "failed" with error details
        """
        from src.mcp_servers.document_store.processor import ExtractionError

        # Setup mocks
        mock_registry.get_revision.return_value = sample_revision
        mock_processor.extract.side_effect = ExtractionError("Failed to open PDF: corrupted file")

        service = PolicyIngestionService(
            registry=mock_registry,
            chroma_client=chroma_client,
            processor=mock_processor,
            chunker=mock_chunker,
            embedder=mock_embedder,
        )

        result = await service.ingest_revision(
            source="LTN_1_20",
            revision_id="rev_LTN_1_20_2020_07",
            file_path="/data/policy/LTN_1_20/corrupted.pdf",
        )

        # Verify failure
        assert result.success is False
        assert "PDF extraction failed" in result.error
        assert result.chunk_count == 0

        # Verify revision was updated to failed
        mock_registry.update_revision.assert_called()
        update_call_kwargs = mock_registry.update_revision.call_args[1]
        assert update_call_kwargs["status"] == RevisionStatus.FAILED
        assert update_call_kwargs["error"] is not None

    @pytest.mark.asyncio
    async def test_empty_pdf_failure(
        self,
        mock_registry,
        chroma_client,
        mock_processor,
        mock_chunker,
        mock_embedder,
        sample_revision,
    ):
        """Test handling of PDF with no extractable text."""
        # Setup mocks - extraction succeeds but no text
        mock_registry.get_revision.return_value = sample_revision
        mock_processor.extract.return_value = DocumentExtraction(
            file_path="/data/policy/empty.pdf",
            total_pages=1,
            pages=[
                PageExtraction(
                    page_number=1,
                    text="",  # No text
                    extraction_method="text_layer",
                    char_count=0,
                    word_count=0,
                )
            ],
            extraction_method="text_layer",
            contains_drawings=False,
            total_char_count=0,
            total_word_count=0,
        )
        mock_chunker.chunk_pages.return_value = []  # No chunks

        service = PolicyIngestionService(
            registry=mock_registry,
            chroma_client=chroma_client,
            processor=mock_processor,
            chunker=mock_chunker,
            embedder=mock_embedder,
        )

        result = await service.ingest_revision(
            source="LTN_1_20",
            revision_id="rev_LTN_1_20_2020_07",
            file_path="/data/policy/empty.pdf",
        )

        assert result.success is False
        assert "No text could be extracted" in result.error


class TestTemporalMetadata:
    """
    Tests for temporal metadata in chunks.

    Implements [policy-knowledge-base:PolicyIngestionJob/TS-03] - Chunks have temporal metadata
    """

    @pytest.mark.asyncio
    async def test_chunks_have_temporal_metadata(
        self,
        mock_registry,
        chroma_client,
        mock_processor,
        mock_chunker,
        mock_embedder,
        sample_revision,
        sample_extraction,
        sample_chunks,
    ):
        """
        Verifies [policy-knowledge-base:PolicyIngestionJob/TS-03] - Chunks have temporal metadata

        Given: Valid PDF
        When: Job completes
        Then: All chunks have effective_from, effective_to in metadata
        """
        mock_registry.get_revision.return_value = sample_revision
        mock_processor.extract.return_value = sample_extraction
        mock_chunker.chunk_pages.return_value = sample_chunks

        service = PolicyIngestionService(
            registry=mock_registry,
            chroma_client=chroma_client,
            processor=mock_processor,
            chunker=mock_chunker,
            embedder=mock_embedder,
        )

        await service.ingest_revision(
            source="LTN_1_20",
            revision_id="rev_LTN_1_20_2020_07",
            file_path="/data/policy/LTN_1_20/ltn_1_20.pdf",
        )

        # Get chunks and verify metadata
        chunks = chroma_client.get_revision_chunks("LTN_1_20", "rev_LTN_1_20_2020_07")

        for chunk in chunks:
            assert "effective_from" in chunk.metadata
            assert "effective_to" in chunk.metadata
            # effective_from should be 2020-07-27 as integer
            assert chunk.metadata["effective_from"] == 20200727
            # effective_to should be far future (None -> 99991231)
            assert chunk.metadata["effective_to"] == 99991231
            # Other required metadata
            assert chunk.metadata["source"] == "LTN_1_20"
            assert chunk.metadata["revision_id"] == "rev_LTN_1_20_2020_07"
            assert chunk.metadata["version_label"] == "July 2020"


class TestReindex:
    """
    Tests for reindexing behavior.

    Implements [policy-knowledge-base:PolicyIngestionJob/TS-04] - Reindex clears old chunks
    """

    @pytest.mark.asyncio
    async def test_reindex_clears_old_chunks(
        self,
        mock_registry,
        chroma_client,
        mock_processor,
        mock_chunker,
        mock_embedder,
        sample_revision,
        sample_extraction,
        sample_chunks,
    ):
        """
        Verifies [policy-knowledge-base:PolicyIngestionJob/TS-04] - Reindex clears old chunks

        Given: Existing chunks for revision
        When: Reindex job runs
        Then: Old chunks deleted before new ones created
        """
        mock_registry.get_revision.return_value = sample_revision
        mock_processor.extract.return_value = sample_extraction
        mock_chunker.chunk_pages.return_value = sample_chunks

        service = PolicyIngestionService(
            registry=mock_registry,
            chroma_client=chroma_client,
            processor=mock_processor,
            chunker=mock_chunker,
            embedder=mock_embedder,
        )

        # First ingestion
        await service.ingest_revision(
            source="LTN_1_20",
            revision_id="rev_LTN_1_20_2020_07",
            file_path="/data/policy/LTN_1_20/ltn_1_20.pdf",
        )

        # Verify initial chunks
        initial_chunks = chroma_client.get_revision_chunks("LTN_1_20", "rev_LTN_1_20_2020_07")
        assert len(initial_chunks) == 3

        # Reindex with different content (fewer chunks)
        mock_chunker.chunk_pages.return_value = sample_chunks[:2]  # Only 2 chunks

        await service.ingest_revision(
            source="LTN_1_20",
            revision_id="rev_LTN_1_20_2020_07",
            file_path="/data/policy/LTN_1_20/ltn_1_20.pdf",
            reindex=True,
        )

        # Verify old chunks were replaced
        final_chunks = chroma_client.get_revision_chunks("LTN_1_20", "rev_LTN_1_20_2020_07")
        assert len(final_chunks) == 2


class TestSectionRefExtraction:
    """Tests for section reference extraction from text."""

    def test_extract_chapter_ref(self):
        """Test extracting Chapter reference."""
        text = "Chapter 5: Cycle Lane Design\n\nCycle lanes should be wide."
        ref = PolicyIngestionService._extract_section_ref(text)
        assert ref == "Chapter 5"

    def test_extract_section_ref(self):
        """Test extracting Section reference."""
        text = "Section 3.2 describes the requirements for cycle parking."
        ref = PolicyIngestionService._extract_section_ref(text)
        assert ref == "Section 3.2"

    def test_extract_para_ref(self):
        """Test extracting Paragraph reference."""
        text = "Para 116 states that development should give priority to cycling."
        ref = PolicyIngestionService._extract_section_ref(text)
        assert ref == "Para 116"

    def test_extract_table_ref(self):
        """Test extracting Table reference."""
        text = "Table 5-2 shows the minimum widths for cycle infrastructure."
        ref = PolicyIngestionService._extract_section_ref(text)
        assert ref == "Table 5-2"

    def test_no_ref_found(self):
        """Test when no section reference is found."""
        text = "This text has no section reference."
        ref = PolicyIngestionService._extract_section_ref(text)
        assert ref == ""


class TestRevisionNotFound:
    """Tests for handling missing revision."""

    @pytest.mark.asyncio
    async def test_revision_not_found(
        self,
        mock_registry,
        chroma_client,
        mock_processor,
        mock_chunker,
        mock_embedder,
    ):
        """Test handling when revision doesn't exist."""
        mock_registry.get_revision.return_value = None

        service = PolicyIngestionService(
            registry=mock_registry,
            chroma_client=chroma_client,
            processor=mock_processor,
            chunker=mock_chunker,
            embedder=mock_embedder,
        )

        result = await service.ingest_revision(
            source="INVALID",
            revision_id="rev_invalid",
            file_path="/data/policy/invalid.pdf",
        )

        assert result.success is False
        assert "Revision not found" in result.error
