"""
Document processor for text extraction from PDFs and images.

Implements [document-processing:FR-001] - PDF text extraction via PyMuPDF
Implements [document-processing:FR-002] - OCR via Tesseract for scanned documents
Implements [document-processing:FR-012] - Detection of image-heavy pages
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import fitz  # PyMuPDF
import structlog

# Lazy import pytesseract - only loaded when OCR is needed
# This allows the module to be imported even if tesseract isn't installed
try:
    import pytesseract
    from PIL import Image as PILImage

    PYTESSERACT_AVAILABLE = True
except ImportError:
    pytesseract = None  # type: ignore[assignment]
    PILImage = None  # type: ignore[assignment, misc]
    PYTESSERACT_AVAILABLE = False

if TYPE_CHECKING:
    from PIL import Image as PILImage

logger = structlog.get_logger(__name__)


class ExtractionError(Exception):
    """Raised when text extraction fails."""

    pass


@dataclass
class PageExtraction:
    """Result of extracting text from a single page."""

    page_number: int
    text: str
    extraction_method: str  # "text_layer" | "ocr" | "mixed"
    char_count: int
    word_count: int
    contains_drawings: bool = False
    ocr_confidence: float | None = None
    image_ratio: float = 0.0


@dataclass
class DocumentExtraction:
    """Result of extracting text from an entire document."""

    file_path: str
    total_pages: int
    pages: list[PageExtraction]
    extraction_method: str  # Overall method used
    contains_drawings: bool
    total_char_count: int
    total_word_count: int
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        """Get concatenated text from all pages with page markers."""
        parts = []
        for page in self.pages:
            if page.text.strip():
                parts.append(f"[Page {page.page_number}]\n{page.text}")
        return "\n\n".join(parts)


class DocumentProcessor:
    """
    Core document processing engine for text extraction.

    Handles PDFs with text layers using PyMuPDF. For scanned documents,
    falls back to OCR via Tesseract.

    Implements:
    - [document-processing:DocumentProcessor/TS-01] Extract text from PDF with text layer
    - [document-processing:DocumentProcessor/TS-05] Handle empty PDF
    - [document-processing:DocumentProcessor/TS-06] Handle corrupt PDF
    """

    # Minimum characters per page to consider it has text
    MIN_CHARS_PER_PAGE = 10

    # Image area ratio threshold for detecting image-heavy pages
    IMAGE_HEAVY_THRESHOLD = 0.7

    # Supported file extensions
    SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp"}

    def __init__(self, enable_ocr: bool = True) -> None:
        """
        Initialize the document processor.

        Args:
            enable_ocr: Whether to enable OCR fallback for scanned documents.
        """
        self.enable_ocr = enable_ocr
        self._ocr_available: bool | None = None

    def _check_ocr_available(self) -> bool:
        """Check if Tesseract OCR is available."""
        if self._ocr_available is not None:
            return self._ocr_available

        if not PYTESSERACT_AVAILABLE:
            self._ocr_available = False
            logger.warning("pytesseract not installed, OCR fallback disabled")
            return self._ocr_available

        try:
            pytesseract.get_tesseract_version()
            self._ocr_available = True
        except Exception:
            self._ocr_available = False
            logger.warning("Tesseract OCR not available, OCR fallback disabled")

        return self._ocr_available

    def extract_text(self, file_path: str | Path) -> DocumentExtraction:
        """
        Extract text from a document.

        Implements [document-processing:FR-001] - PDF text extraction

        Args:
            file_path: Path to the document file.

        Returns:
            DocumentExtraction with extracted text and metadata.

        Raises:
            ExtractionError: If the file cannot be processed.
            FileNotFoundError: If the file does not exist.
            ValueError: If the file type is not supported.
        """
        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        extension = path.suffix.lower()
        if extension not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file type: {extension}. "
                f"Supported types: {', '.join(self.SUPPORTED_EXTENSIONS)}"
            )

        logger.info("Starting text extraction", file_path=str(path), extension=extension)

        if extension == ".pdf":
            return self._extract_from_pdf(path)
        else:
            return self._extract_from_image(path)

    def _extract_from_pdf(self, path: Path) -> DocumentExtraction:
        """
        Extract text from a PDF file.

        Implements [document-processing:DocumentProcessor/TS-01] - Text layer extraction
        """
        try:
            doc = fitz.open(str(path))
        except Exception as e:
            raise ExtractionError(f"Failed to open PDF: {e}") from e

        pages: list[PageExtraction] = []
        total_chars = 0
        total_words = 0
        has_drawings = False
        methods_used: set[str] = set()

        try:
            for page_num in range(len(doc)):
                page = doc[page_num]
                page_extraction = self._extract_page(page, page_num + 1)
                pages.append(page_extraction)

                total_chars += page_extraction.char_count
                total_words += page_extraction.word_count
                methods_used.add(page_extraction.extraction_method)

                if page_extraction.contains_drawings:
                    has_drawings = True

        except Exception as e:
            doc.close()
            raise ExtractionError(f"Error extracting page {page_num + 1}: {e}") from e
        finally:
            doc.close()

        # Determine overall extraction method
        if len(methods_used) == 1:
            overall_method = methods_used.pop()
        elif methods_used:
            overall_method = "mixed"
        else:
            overall_method = "text_layer"

        logger.info(
            "PDF extraction complete",
            file_path=str(path),
            total_pages=len(pages),
            total_chars=total_chars,
            extraction_method=overall_method,
            contains_drawings=has_drawings,
        )

        return DocumentExtraction(
            file_path=str(path),
            total_pages=len(pages),
            pages=pages,
            extraction_method=overall_method,
            contains_drawings=has_drawings,
            total_char_count=total_chars,
            total_word_count=total_words,
        )

    def _extract_page(self, page: fitz.Page, page_number: int) -> PageExtraction:
        """
        Extract text from a single PDF page.

        Attempts text-layer extraction first, falls back to OCR if needed.
        """
        # Try text-layer extraction
        text = page.get_text("text")
        char_count = len(text.strip())
        word_count = len(text.split()) if text.strip() else 0

        # Calculate image ratio to detect drawings/scanned content
        image_ratio = self._calculate_image_ratio(page)
        contains_drawings = image_ratio > self.IMAGE_HEAVY_THRESHOLD

        extraction_method = "text_layer"
        ocr_confidence = None

        # If minimal text extracted and OCR is enabled, try OCR
        if char_count < self.MIN_CHARS_PER_PAGE and self.enable_ocr and self._check_ocr_available():
            ocr_text, confidence = self._ocr_page(page)
            if len(ocr_text.strip()) > char_count:
                text = ocr_text
                char_count = len(text.strip())
                word_count = len(text.split()) if text.strip() else 0
                extraction_method = "ocr"
                ocr_confidence = confidence

        return PageExtraction(
            page_number=page_number,
            text=text,
            extraction_method=extraction_method,
            char_count=char_count,
            word_count=word_count,
            contains_drawings=contains_drawings,
            ocr_confidence=ocr_confidence,
            image_ratio=image_ratio,
        )

    def _calculate_image_ratio(self, page: fitz.Page) -> float:
        """
        Calculate the ratio of image area to page area.

        Implements [document-processing:FR-012] - Image-heavy page detection
        """
        try:
            page_area = page.rect.width * page.rect.height
            if page_area == 0:
                return 0.0

            image_list = page.get_images(full=True)
            total_image_area = 0.0

            for img in image_list:
                xref = img[0]
                try:
                    # Get image dimensions from the xref
                    img_info = page.parent.extract_image(xref)
                    if img_info:
                        width = img_info.get("width", 0)
                        height = img_info.get("height", 0)
                        total_image_area += width * height
                except Exception:
                    # Skip images we can't analyze
                    continue

            # Normalize - images may have different resolution than page
            # Use a heuristic ratio
            return min(1.0, total_image_area / (page_area * 4))

        except Exception as e:
            logger.debug("Error calculating image ratio", error=str(e))
            return 0.0

    def _ocr_page(self, page: fitz.Page) -> tuple[str, float]:
        """
        Perform OCR on a PDF page.

        Implements [document-processing:FR-002] - OCR via Tesseract

        Returns:
            Tuple of (extracted_text, average_confidence)
        """
        if not PYTESSERACT_AVAILABLE or pytesseract is None or PILImage is None:
            logger.error("OCR called but pytesseract not available")
            return "", 0.0

        try:
            # Render page to image at 300 DPI for good OCR quality
            pix = page.get_pixmap(dpi=300)
            img = PILImage.frombytes("RGB", (pix.width, pix.height), pix.samples)

            # Get detailed OCR data with confidence scores
            data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)

            # Calculate average confidence (excluding -1 which means no text)
            confidences = [c for c in data["conf"] if c > 0]
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

            # Get full text
            text = pytesseract.image_to_string(img)

            if avg_confidence < 70:
                logger.warning(
                    "Low OCR confidence",
                    page_number=page.number + 1,
                    confidence=avg_confidence,
                )

            return text, avg_confidence / 100.0  # Normalize to 0-1

        except Exception as e:
            logger.error("OCR failed", error=str(e))
            return "", 0.0

    def _extract_from_image(self, path: Path) -> DocumentExtraction:
        """
        Extract text from an image file using OCR.

        Implements [document-processing:DocumentProcessor/TS-07] - Image extraction
        """
        if not self.enable_ocr or not self._check_ocr_available():
            raise ExtractionError("OCR is required for image files but is not available")

        if not PYTESSERACT_AVAILABLE or pytesseract is None or PILImage is None:
            raise ExtractionError("OCR is required for image files but pytesseract is not installed")

        try:
            img = PILImage.open(path)

            # Get detailed OCR data
            data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
            confidences = [c for c in data["conf"] if c > 0]
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

            text = pytesseract.image_to_string(img)
            char_count = len(text.strip())
            word_count = len(text.split()) if text.strip() else 0

            page = PageExtraction(
                page_number=1,
                text=text,
                extraction_method="ocr",
                char_count=char_count,
                word_count=word_count,
                contains_drawings=False,  # Assume images are documents, not drawings
                ocr_confidence=avg_confidence / 100.0,
                image_ratio=1.0,
            )

            return DocumentExtraction(
                file_path=str(path),
                total_pages=1,
                pages=[page],
                extraction_method="ocr",
                contains_drawings=False,
                total_char_count=char_count,
                total_word_count=word_count,
            )

        except Exception as e:
            raise ExtractionError(f"Failed to extract text from image: {e}") from e
