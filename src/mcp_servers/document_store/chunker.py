"""
Text chunking for document processing.

Implements [document-processing:FR-003] - Chunk text with configurable size and overlap
"""

from dataclasses import dataclass, field
from typing import Any

import structlog
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = structlog.get_logger(__name__)


@dataclass
class TextChunk:
    """A chunk of text from a document."""

    text: str
    chunk_index: int
    char_count: int
    word_count: int
    page_numbers: list[int] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class TextChunker:
    """
    Splits text into chunks suitable for embedding.

    Uses recursive character splitting with configurable size and overlap,
    attempting to respect paragraph and sentence boundaries.

    Implements:
    - [document-processing:TextChunker/TS-01] Chunk long document
    - [document-processing:TextChunker/TS-02] Preserve paragraph boundaries
    - [document-processing:TextChunker/TS-03] Handle short document
    - [document-processing:TextChunker/TS-04] Maintain context with overlap
    - [document-processing:TextChunker/TS-05] Handle no natural boundaries
    - [document-processing:TextChunker/TS-06] Include page context
    """

    # Default parameters matching design spec
    DEFAULT_CHUNK_SIZE = 1000  # tokens (approximated as chars/4)
    DEFAULT_CHUNK_OVERLAP = 200  # tokens
    CHARS_PER_TOKEN = 4  # Rough approximation

    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> None:
        """
        Initialize the text chunker.

        Args:
            chunk_size: Target chunk size in tokens (default 1000).
            chunk_overlap: Overlap between chunks in tokens (default 200).
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

        # Convert tokens to characters for the splitter
        char_chunk_size = chunk_size * self.CHARS_PER_TOKEN
        char_overlap = chunk_overlap * self.CHARS_PER_TOKEN

        # Configure the splitter with separators that respect document structure
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=char_chunk_size,
            chunk_overlap=char_overlap,
            length_function=len,
            separators=[
                "\n\n",  # Paragraph breaks first
                "\n",  # Line breaks
                ". ",  # Sentence endings
                "! ",
                "? ",
                "; ",  # Clause boundaries
                ", ",  # Phrase boundaries
                " ",  # Word boundaries
                "",  # Character level (last resort)
            ],
            keep_separator=True,
        )

    def chunk_text(
        self,
        text: str,
        page_numbers: list[int] | None = None,
    ) -> list[TextChunk]:
        """
        Split text into chunks.

        Args:
            text: The text to chunk.
            page_numbers: Optional list of page numbers that contributed to this text.
                          If provided, chunks will include page context.

        Returns:
            List of TextChunk objects.
        """
        if not text.strip():
            return []

        # Split the text
        chunks = self._splitter.split_text(text)

        logger.debug(
            "Text chunked",
            input_chars=len(text),
            output_chunks=len(chunks),
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
        )

        result: list[TextChunk] = []
        for i, chunk_text in enumerate(chunks):
            chunk = TextChunk(
                text=chunk_text,
                chunk_index=i,
                char_count=len(chunk_text),
                word_count=len(chunk_text.split()),
                page_numbers=page_numbers or [],
            )
            result.append(chunk)

        return result

    def chunk_pages(
        self,
        pages: list[tuple[int, str]],
    ) -> list[TextChunk]:
        """
        Chunk text from multiple pages, tracking which pages contributed to each chunk.

        Args:
            pages: List of (page_number, text) tuples.

        Returns:
            List of TextChunk objects with page context.
        """
        if not pages:
            return []

        # Build a mapping of character positions to page numbers
        full_text = ""
        char_to_page: dict[int, int] = {}

        for page_num, page_text in pages:
            start_pos = len(full_text)
            if page_text.strip():
                # Add page marker and content
                if full_text:
                    full_text += "\n\n"
                full_text += page_text

                # Map characters to page number
                for pos in range(start_pos, len(full_text)):
                    char_to_page[pos] = page_num

        if not full_text.strip():
            return []

        # Split the combined text
        chunks = self._splitter.split_text(full_text)

        result: list[TextChunk] = []
        current_pos = 0

        for i, chunk_text in enumerate(chunks):
            # Find where this chunk appears in the full text
            chunk_start = full_text.find(chunk_text, current_pos)
            if chunk_start == -1:
                chunk_start = current_pos

            chunk_end = chunk_start + len(chunk_text)

            # Determine which pages this chunk spans
            pages_in_chunk: set[int] = set()
            for pos in range(chunk_start, min(chunk_end, len(full_text))):
                if pos in char_to_page:
                    pages_in_chunk.add(char_to_page[pos])

            chunk = TextChunk(
                text=chunk_text,
                chunk_index=i,
                char_count=len(chunk_text),
                word_count=len(chunk_text.split()),
                page_numbers=sorted(pages_in_chunk),
            )
            result.append(chunk)

            # Move position forward (accounting for overlap)
            current_pos = max(current_pos, chunk_start + 1)

        logger.debug(
            "Pages chunked",
            total_pages=len(pages),
            output_chunks=len(result),
        )

        return result

    def estimate_tokens(self, text: str) -> int:
        """
        Estimate the number of tokens in a text.

        Uses a simple character-based approximation.

        Args:
            text: The text to estimate.

        Returns:
            Estimated token count.
        """
        return len(text) // self.CHARS_PER_TOKEN
