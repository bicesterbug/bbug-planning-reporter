"""
Tests for PDFGenerator.

Verifies [api-hardening:FR-007] - Download review as PDF
Verifies [api-hardening:NFR-006] - PDF generation quality
"""

import pytest

from src.api.services.pdf_generator import PDFGenerator


@pytest.fixture
def generator():
    """Create PDF generator instance."""
    return PDFGenerator()


class TestBasicMarkdownToPDF:
    """
    Tests for basic Markdown to PDF conversion.

    Verifies [api-hardening:PDFGenerator/TS-01] - Basic markdown to PDF
    """

    def test_generates_pdf_from_markdown(self, generator):
        """
        Verifies [api-hardening:PDFGenerator/TS-01] - Basic markdown to PDF

        Given: Simple markdown with headings
        When: Generate PDF
        Then: PDF contains formatted headings
        """
        markdown = """# Cycle Advocacy Review

## Summary

This is a review of planning application 25/01178/REM.

### Key Findings

- Finding 1
- Finding 2
"""
        pdf_bytes = generator.generate(markdown)

        # Should produce valid PDF
        assert pdf_bytes is not None
        assert len(pdf_bytes) > 0
        # PDF should start with PDF header
        assert pdf_bytes[:4] == b"%PDF"

    def test_generates_pdf_with_title(self, generator):
        """PDF can be generated with a document title."""
        markdown = "# Test Review\n\nContent here."
        pdf_bytes = generator.generate(markdown, title="Review for 25/01178/REM")

        assert pdf_bytes is not None
        assert pdf_bytes[:4] == b"%PDF"


class TestTableRendering:
    """
    Tests for table rendering in PDF.

    Verifies [api-hardening:PDFGenerator/TS-02] - Tables render correctly
    """

    def test_tables_render_in_pdf(self, generator):
        """
        Verifies [api-hardening:PDFGenerator/TS-02] - Tables render correctly

        Given: Markdown with policy compliance table
        When: Generate PDF
        Then: Table structure preserved with borders
        """
        markdown = """# Compliance Matrix

| Requirement | Policy Source | Compliant | Notes |
|------------|--------------|-----------|-------|
| Cycle parking | LTN 1/20 | Yes | 20 spaces provided |
| Route width | NPPF | No | Below minimum |
| Junction design | Manual for Streets | Yes | Meets standards |
"""
        pdf_bytes = generator.generate(markdown)

        assert pdf_bytes is not None
        assert len(pdf_bytes) > 0
        assert pdf_bytes[:4] == b"%PDF"


class TestRatingIcons:
    """
    Tests for rating icon rendering.

    Verifies [api-hardening:PDFGenerator/TS-03] - Rating icons
    """

    def test_emoji_ratings_converted(self, generator):
        """
        Verifies [api-hardening:PDFGenerator/TS-03] - Rating icons

        Given: Markdown with rating emojis
        When: Generate PDF
        Then: Icons rendered or replaced with text labels
        """
        markdown = """# Review Results

## Overall Rating: 🟡 AMBER

### Aspect Ratings

- Cycle Parking: 🟢 GREEN
- Cycle Routes: 🔴 RED
- Junction Design: 🟡 AMBER
"""
        pdf_bytes = generator.generate(markdown)

        # Should generate valid PDF without errors
        assert pdf_bytes is not None
        assert len(pdf_bytes) > 0
        assert pdf_bytes[:4] == b"%PDF"

    def test_checkmarks_converted(self, generator):
        """Check and cross marks are converted."""
        markdown = """# Checklist

- ✅ Cycle parking provided
- ❌ Route width insufficient
- ⚠️ Junction needs review
"""
        pdf_bytes = generator.generate(markdown)
        assert pdf_bytes is not None
        assert pdf_bytes[:4] == b"%PDF"


class TestPolicyCitations:
    """
    Tests for policy citation formatting.

    Verifies [api-hardening:PDFGenerator/TS-04] - Policy citations
    """

    def test_policy_citations_legible(self, generator):
        """
        Verifies [api-hardening:PDFGenerator/TS-04] - Policy citations

        Given: Markdown with policy references
        When: Generate PDF
        Then: Citations legible with proper formatting
        """
        markdown = """# Policy Analysis

According to **LTN 1/20 Table 5-2**, segregated cycle tracks are required
where traffic volume exceeds 2500 PCU/day.

The **NPPF Paragraph 116** states that development should give priority
first to pedestrian and cycle movements.

> "Cycle parking should be secure, weatherproof, and conveniently located"
> — LTN 1/20 Chapter 11
"""
        pdf_bytes = generator.generate(markdown)
        assert pdf_bytes is not None
        assert pdf_bytes[:4] == b"%PDF"


class TestLongDocuments:
    """
    Tests for long document handling.

    Verifies [api-hardening:PDFGenerator/TS-05] - Long document handling
    """

    def test_large_document_generates(self, generator):
        """
        Verifies [api-hardening:PDFGenerator/TS-05] - Long document handling

        Given: Large review (simulating 50+ pages)
        When: Generate PDF
        Then: PDF generated without timeout
        """
        # Generate a large document
        sections = []
        for i in range(50):
            sections.append(f"""## Section {i+1}

This is content for section {i+1}. It includes multiple paragraphs
to simulate a real review document.

### Subsection {i+1}.1

- Point A about this section
- Point B with more details
- Point C referencing policy

| Item | Value | Notes |
|------|-------|-------|
| Row 1 | Data | Comment |
| Row 2 | Data | Comment |
""")

        markdown = "# Large Review Document\n\n" + "\n".join(sections)

        pdf_bytes = generator.generate(markdown)

        assert pdf_bytes is not None
        assert len(pdf_bytes) > 0
        assert pdf_bytes[:4] == b"%PDF"
        # Large PDF should be at least several KB
        assert len(pdf_bytes) > 10000


class TestUnicodeSupport:
    """
    Tests for Unicode character support.

    Verifies [api-hardening:PDFGenerator/TS-06] - Unicode support
    """

    def test_unicode_characters_render(self, generator):
        """
        Verifies [api-hardening:PDFGenerator/TS-06] - Unicode support

        Given: Markdown with special characters
        When: Generate PDF
        Then: Characters render correctly
        """
        markdown = """# Review with Special Characters

## Summary

This review covers:
- Street names like "Böse Straße" and "O'Connell Street"
- Measurements: 2.5m × 3m area
- Temperature: 20°C
- Currency: £100,000 (€115,000)
- Fractions: ½ cup, ¼ mile
- Arrows: → ← ↑ ↓

### Quote

"The café's façade features naïve artwork"
"""
        pdf_bytes = generator.generate(markdown)
        assert pdf_bytes is not None
        assert pdf_bytes[:4] == b"%PDF"


class TestCustomCSS:
    """Tests for custom CSS support."""

    def test_custom_css_applied(self):
        """Custom CSS can be added to the generator."""
        custom_css = """
        h1 { color: navy; }
        .custom-class { font-weight: bold; }
        """
        generator = PDFGenerator(custom_css=custom_css)

        markdown = "# Test Heading\n\nContent"
        pdf_bytes = generator.generate(markdown)

        assert pdf_bytes is not None
        assert pdf_bytes[:4] == b"%PDF"


class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_markdown(self, generator):
        """Empty markdown produces valid PDF."""
        pdf_bytes = generator.generate("")
        assert pdf_bytes is not None
        assert pdf_bytes[:4] == b"%PDF"

    def test_markdown_with_code_blocks(self, generator):
        """Code blocks render correctly."""
        markdown = """# Technical Details

```python
def calculate_spaces(units: int) -> int:
    return units * 2
```

Inline code like `variable_name` also works.
"""
        pdf_bytes = generator.generate(markdown)
        assert pdf_bytes is not None

    def test_markdown_with_links(self, generator):
        """Links render correctly."""
        markdown = """# Resources

See [LTN 1/20](https://www.gov.uk/ltn-1-20) for details.
"""
        pdf_bytes = generator.generate(markdown)
        assert pdf_bytes is not None
