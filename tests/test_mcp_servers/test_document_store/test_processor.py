"""
Tests for DocumentProcessor.

Implements test scenarios from [document-processing:DocumentProcessor/TS-01] through [TS-08]
"""

from pathlib import Path

import fitz  # PyMuPDF
import pytest

from src.mcp_servers.document_store.processor import (
    DocumentExtraction,
    DocumentProcessor,
    ExtractionError,
    PageExtraction,
)


@pytest.fixture
def processor() -> DocumentProcessor:
    """Create a DocumentProcessor instance."""
    return DocumentProcessor(enable_ocr=False)


@pytest.fixture
def processor_with_ocr() -> DocumentProcessor:
    """Create a DocumentProcessor with OCR enabled."""
    return DocumentProcessor(enable_ocr=True)


@pytest.fixture
def sample_pdf_with_text(tmp_path: Path) -> Path:
    """Create a sample PDF with text content."""
    pdf_path = tmp_path / "sample_text.pdf"
    doc = fitz.open()

    # Add pages with text
    for _, content in enumerate(
        [
            "Page 1: Introduction to the Transport Assessment.\n\nThis document provides an overview of traffic impacts.",
            "Page 2: Cycle Parking Provision.\n\n48 Sheffield stands are proposed near the main entrance.",
            "Page 3: Conclusions and Recommendations.\n\nThe development meets local plan requirements.",
        ]
    ):
        page = doc.new_page()
        page.insert_text((72, 72), content, fontsize=12)

    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.fixture
def empty_pdf(tmp_path: Path) -> Path:
    """Create an empty PDF with no text."""
    pdf_path = tmp_path / "empty.pdf"
    doc = fitz.open()
    doc.new_page()  # Add empty page
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.fixture
def corrupt_pdf(tmp_path: Path) -> Path:
    """Create a corrupt PDF file."""
    pdf_path = tmp_path / "corrupt.pdf"
    pdf_path.write_text("This is not a valid PDF content")
    return pdf_path


class TestExtractTextFromPDF:
    """Tests for PDF text extraction."""

    def test_extract_text_from_pdf_with_text_layer(
        self, processor: DocumentProcessor, sample_pdf_with_text: Path
    ) -> None:
        """
        Verifies [document-processing:DocumentProcessor/TS-01]

        Given: PDF with embedded text
        When: Call extract_text()
        Then: Returns extracted text with page boundaries
        """
        result = processor.extract_text(sample_pdf_with_text)

        assert isinstance(result, DocumentExtraction)
        assert result.total_pages == 3
        assert result.extraction_method == "text_layer"
        assert result.total_char_count > 0
        assert result.total_word_count > 0

        # Check individual pages
        assert len(result.pages) == 3
        assert "Transport Assessment" in result.pages[0].text
        assert "Cycle Parking" in result.pages[1].text
        assert "Conclusions" in result.pages[2].text

        # Check page extraction details
        for page in result.pages:
            assert page.extraction_method == "text_layer"
            assert page.char_count > 0

    def test_handle_empty_pdf(
        self, processor: DocumentProcessor, empty_pdf: Path
    ) -> None:
        """
        Verifies [document-processing:DocumentProcessor/TS-05]

        Given: Valid PDF with no extractable content
        When: Call extract_text()
        Then: Returns empty text with appropriate metadata
        """
        result = processor.extract_text(empty_pdf)

        assert isinstance(result, DocumentExtraction)
        assert result.total_pages == 1
        assert result.total_char_count == 0
        assert result.total_word_count == 0

        # Page should exist but have no text
        assert len(result.pages) == 1
        assert result.pages[0].text.strip() == ""

    def test_handle_corrupt_pdf(
        self, processor: DocumentProcessor, corrupt_pdf: Path
    ) -> None:
        """
        Verifies [document-processing:DocumentProcessor/TS-06]

        Given: Malformed PDF file
        When: Call extract_text()
        Then: Raises ExtractionError with descriptive message
        """
        with pytest.raises(ExtractionError) as exc_info:
            processor.extract_text(corrupt_pdf)

        assert "Failed to open PDF" in str(exc_info.value)

    def test_file_not_found(self, processor: DocumentProcessor) -> None:
        """
        Given: Non-existent file path
        When: Call extract_text()
        Then: Raises FileNotFoundError
        """
        with pytest.raises(FileNotFoundError):
            processor.extract_text("/nonexistent/path/file.pdf")

    def test_unsupported_file_type(
        self, processor: DocumentProcessor, tmp_path: Path
    ) -> None:
        """
        Given: Unsupported file type
        When: Call extract_text()
        Then: Raises ValueError
        """
        docx_file = tmp_path / "document.docx"
        docx_file.write_text("content")

        with pytest.raises(ValueError) as exc_info:
            processor.extract_text(docx_file)

        assert "Unsupported file type" in str(exc_info.value)


class TestFullTextProperty:
    """Tests for the full_text property."""

    def test_full_text_concatenates_pages(
        self, processor: DocumentProcessor, sample_pdf_with_text: Path
    ) -> None:
        """
        Given: Multi-page PDF
        When: Access full_text property
        Then: Returns concatenated text with page markers
        """
        result = processor.extract_text(sample_pdf_with_text)
        full_text = result.full_text

        assert "[Page 1]" in full_text
        assert "[Page 2]" in full_text
        assert "[Page 3]" in full_text
        assert "Transport Assessment" in full_text
        assert "Cycle Parking" in full_text


class TestImageDetection:
    """Tests for image-heavy page detection."""

    def test_text_page_not_marked_as_drawings(
        self, processor: DocumentProcessor, sample_pdf_with_text: Path
    ) -> None:
        """
        Given: PDF with text content
        When: Extract text
        Then: Pages not marked as containing drawings
        """
        result = processor.extract_text(sample_pdf_with_text)

        assert not result.contains_drawings
        for page in result.pages:
            assert not page.contains_drawings


class TestPageExtraction:
    """Tests for PageExtraction dataclass."""

    def test_page_extraction_fields(self) -> None:
        """Test PageExtraction dataclass has expected fields."""
        page = PageExtraction(
            page_number=1,
            text="Sample text",
            extraction_method="text_layer",
            char_count=11,
            word_count=2,
            contains_drawings=False,
            ocr_confidence=None,
            image_ratio=0.1,
        )

        assert page.page_number == 1
        assert page.text == "Sample text"
        assert page.extraction_method == "text_layer"
        assert page.char_count == 11
        assert page.word_count == 2
        assert not page.contains_drawings
        assert page.ocr_confidence is None
        assert page.image_ratio == 0.1


class TestDocumentExtraction:
    """Tests for DocumentExtraction dataclass."""

    def test_document_extraction_fields(self) -> None:
        """Test DocumentExtraction dataclass has expected fields."""
        pages = [
            PageExtraction(
                page_number=1,
                text="Page 1 text",
                extraction_method="text_layer",
                char_count=11,
                word_count=3,
            )
        ]

        doc = DocumentExtraction(
            file_path="/path/to/file.pdf",
            total_pages=1,
            pages=pages,
            extraction_method="text_layer",
            contains_drawings=False,
            total_char_count=11,
            total_word_count=3,
        )

        assert doc.file_path == "/path/to/file.pdf"
        assert doc.total_pages == 1
        assert len(doc.pages) == 1
        assert doc.extraction_method == "text_layer"
        assert not doc.contains_drawings
