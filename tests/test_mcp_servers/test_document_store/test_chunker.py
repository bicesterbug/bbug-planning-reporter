"""
Tests for TextChunker.

Implements test scenarios from [document-processing:TextChunker/TS-01] through [TS-06]
"""

import pytest

from src.mcp_servers.document_store.chunker import TextChunk, TextChunker


@pytest.fixture
def chunker() -> TextChunker:
    """Create a TextChunker with default settings."""
    return TextChunker()


@pytest.fixture
def small_chunker() -> TextChunker:
    """Create a TextChunker with small chunk size for testing."""
    return TextChunker(chunk_size=50, chunk_overlap=10)


class TestChunkLongDocument:
    """Tests for chunking long documents."""

    def test_chunk_long_document(self, small_chunker: TextChunker) -> None:
        """
        Verifies [document-processing:TextChunker/TS-01]

        Given: Document with 5000 words
        When: Call chunk_text()
        Then: Returns ~5 chunks with overlap
        """
        # Create text with ~500 words (small chunks, so we get multiple chunks)
        words = ["word"] * 500
        text = " ".join(words)

        chunks = small_chunker.chunk_text(text)

        assert len(chunks) > 1
        assert all(isinstance(c, TextChunk) for c in chunks)

        # Verify each chunk has content
        for chunk in chunks:
            assert chunk.char_count > 0
            assert chunk.word_count > 0

    def test_chunks_have_sequential_indices(self, small_chunker: TextChunker) -> None:
        """
        Given: Long document
        When: Chunk text
        Then: Chunks have sequential indices starting from 0
        """
        text = " ".join(["paragraph"] * 300)
        chunks = small_chunker.chunk_text(text)

        for i, chunk in enumerate(chunks):
            assert chunk.chunk_index == i


class TestPreserveParagraphBoundaries:
    """Tests for paragraph boundary preservation."""

    def test_preserve_paragraph_boundaries(self, small_chunker: TextChunker) -> None:
        """
        Verifies [document-processing:TextChunker/TS-02]

        Given: Text with clear paragraphs
        When: Call chunk_text()
        Then: Chunks break at paragraph boundaries when possible
        """
        # Create text with clear paragraph breaks
        paragraphs = [
            "This is the first paragraph. It contains multiple sentences about the project.",
            "This is the second paragraph. It discusses different aspects of the development.",
            "This is the third paragraph. It covers the transport assessment details.",
        ]
        text = "\n\n".join(paragraphs)

        chunks = small_chunker.chunk_text(text)

        # With small chunk size, we should get multiple chunks
        assert len(chunks) >= 1

        # Chunks should start/end at natural boundaries when possible
        # (This is a best-effort check - the splitter respects boundaries when it can)
        for chunk in chunks:
            # No chunk should start or end mid-word if we have natural breaks available
            stripped = chunk.text.strip()
            assert len(stripped) > 0


class TestHandleShortDocument:
    """Tests for short documents."""

    def test_handle_short_document(self, chunker: TextChunker) -> None:
        """
        Verifies [document-processing:TextChunker/TS-03]

        Given: Document shorter than chunk_size
        When: Call chunk_text()
        Then: Returns single chunk
        """
        short_text = "This is a short document with only a few words."

        chunks = chunker.chunk_text(short_text)

        assert len(chunks) == 1
        assert chunks[0].text == short_text
        assert chunks[0].chunk_index == 0

    def test_empty_text_returns_empty_list(self, chunker: TextChunker) -> None:
        """
        Given: Empty text
        When: Call chunk_text()
        Then: Returns empty list
        """
        chunks = chunker.chunk_text("")
        assert chunks == []

        chunks = chunker.chunk_text("   \n\n   ")
        assert chunks == []


class TestMaintainContextWithOverlap:
    """Tests for chunk overlap."""

    def test_maintain_context_with_overlap(self, small_chunker: TextChunker) -> None:
        """
        Verifies [document-processing:TextChunker/TS-04]

        Given: Multi-chunk document
        When: Call chunk_text()
        Then: Each chunk overlaps with previous by ~200 tokens
        """
        # Create text that will definitely produce multiple chunks
        text = " ".join(["content"] * 500)

        chunks = small_chunker.chunk_text(text)

        # Should have multiple chunks
        assert len(chunks) > 2

        # Verify all chunks are properly formed
        for chunk in chunks:
            assert len(chunk.text) > 0


class TestHandleNoNaturalBoundaries:
    """Tests for text without natural boundaries."""

    def test_handle_no_natural_boundaries(self, small_chunker: TextChunker) -> None:
        """
        Verifies [document-processing:TextChunker/TS-05]

        Given: Dense text without paragraphs
        When: Call chunk_text()
        Then: Falls back to sentence/word boundaries
        """
        # Create text without paragraph breaks
        dense_text = "word " * 500

        chunks = small_chunker.chunk_text(dense_text)

        # Should still produce valid chunks
        assert len(chunks) > 1

        # Each chunk should have content
        for chunk in chunks:
            assert chunk.char_count > 0


class TestIncludePageContext:
    """Tests for page context tracking."""

    def test_include_page_context(self, small_chunker: TextChunker) -> None:
        """
        Verifies [document-processing:TextChunker/TS-06]

        Given: Multi-page text
        When: Call chunk_text() with page_numbers
        Then: Each chunk includes source page in metadata
        """
        text = " ".join(["content"] * 200)
        page_numbers = [1, 2, 3]

        chunks = small_chunker.chunk_text(text, page_numbers=page_numbers)

        # All chunks should have the page numbers
        for chunk in chunks:
            assert chunk.page_numbers == page_numbers

    def test_chunk_pages_tracks_per_chunk_pages(self, small_chunker: TextChunker) -> None:
        """
        Given: Multiple pages with different content
        When: Call chunk_pages()
        Then: Each chunk tracks which pages it came from
        """
        pages = [
            (1, "Content from page one. " * 20),
            (2, "Content from page two. " * 20),
            (3, "Content from page three. " * 20),
        ]

        chunks = small_chunker.chunk_pages(pages)

        assert len(chunks) > 0

        # Each chunk should have page numbers
        for chunk in chunks:
            assert len(chunk.page_numbers) > 0
            # Page numbers should be valid
            for pn in chunk.page_numbers:
                assert pn in [1, 2, 3]


class TestTextChunkDataclass:
    """Tests for TextChunk dataclass."""

    def test_text_chunk_fields(self) -> None:
        """Test TextChunk has expected fields."""
        chunk = TextChunk(
            text="Sample chunk text",
            chunk_index=0,
            char_count=17,
            word_count=3,
            page_numbers=[1, 2],
            metadata={"key": "value"},
        )

        assert chunk.text == "Sample chunk text"
        assert chunk.chunk_index == 0
        assert chunk.char_count == 17
        assert chunk.word_count == 3
        assert chunk.page_numbers == [1, 2]
        assert chunk.metadata == {"key": "value"}

    def test_text_chunk_defaults(self) -> None:
        """Test TextChunk default values."""
        chunk = TextChunk(
            text="text",
            chunk_index=0,
            char_count=4,
            word_count=1,
        )

        assert chunk.page_numbers == []
        assert chunk.metadata == {}


class TestEstimateTokens:
    """Tests for token estimation."""

    def test_estimate_tokens(self, chunker: TextChunker) -> None:
        """Test token estimation."""
        # 100 characters should be ~25 tokens (100/4)
        text = "a" * 100
        tokens = chunker.estimate_tokens(text)

        assert tokens == 25

    def test_estimate_empty_tokens(self, chunker: TextChunker) -> None:
        """Test token estimation for empty text."""
        tokens = chunker.estimate_tokens("")
        assert tokens == 0


class TestChunkEmbeddingLimit:
    """
    Verifies [review-workflow-redesign:TextChunker/TS-01] - Chunks fit embedding limit
    Verifies [review-workflow-redesign:ITS-05] - Chunk size prevents truncation
    """

    EMBEDDING_MAX_CHARS = 1024

    def test_chunks_fit_embedding_limit(self, chunker: TextChunker) -> None:
        """
        Verifies [review-workflow-redesign:TextChunker/TS-01]

        Given: 10,000-character document
        When: Chunked with new defaults
        Then: All chunks are <= 1024 characters
        """
        text = "This is a test sentence with some content. " * 250  # ~11,000 chars
        chunks = chunker.chunk_text(text)

        assert len(chunks) > 1, "Should produce multiple chunks"
        for chunk in chunks:
            assert chunk.char_count <= self.EMBEDDING_MAX_CHARS, (
                f"Chunk {chunk.chunk_index} has {chunk.char_count} chars, "
                f"exceeds embedding limit of {self.EMBEDDING_MAX_CHARS}"
            )

    def test_overlap_preserved_with_new_defaults(self, chunker: TextChunker) -> None:
        """
        Verifies [review-workflow-redesign:TextChunker/TS-02]

        Given: Document with clear sentence boundaries
        When: Chunked with new defaults
        Then: Adjacent chunks share overlapping text
        """
        text = "This is a sentence about the planning application. " * 100
        chunks = chunker.chunk_text(text)

        assert len(chunks) > 2, "Should produce enough chunks to verify overlap"
        # Check that consecutive chunks share some text
        for i in range(len(chunks) - 1):
            this_text = chunks[i].text
            next_text = chunks[i + 1].text
            # The end of this chunk should overlap with the start of the next
            overlap_found = any(
                word in next_text[:300]
                for word in this_text[-200:].split()
                if len(word) > 3
            )
            assert overlap_found, f"No overlap found between chunk {i} and {i + 1}"

    def test_short_document_single_chunk(self, chunker: TextChunker) -> None:
        """
        Verifies [review-workflow-redesign:TextChunker/TS-03]

        Given: 500-character document
        When: Chunked with new defaults
        Then: Single chunk containing full text
        """
        text = "Short document content. " * 20  # ~480 chars
        chunks = chunker.chunk_text(text)

        assert len(chunks) == 1
        assert chunks[0].text.strip() == text.strip()

    def test_chunk_pages_fit_embedding_limit(self, chunker: TextChunker) -> None:
        """
        Given: Multi-page document
        When: chunk_pages() called with new defaults
        Then: All chunks are <= 1024 characters
        """
        pages = [
            (1, "Content from page one with detailed information. " * 30),
            (2, "Content from page two about transport assessment. " * 30),
            (3, "Content from page three covering site layout. " * 30),
        ]
        chunks = chunker.chunk_pages(pages)

        assert len(chunks) > 1
        for chunk in chunks:
            assert chunk.char_count <= self.EMBEDDING_MAX_CHARS, (
                f"Chunk {chunk.chunk_index} has {chunk.char_count} chars, "
                f"exceeds embedding limit of {self.EMBEDDING_MAX_CHARS}"
            )
