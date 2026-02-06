"""
PDF Generator service for converting Markdown reviews to PDF.

Implements [api-hardening:FR-007] - Download review as PDF
Implements [api-hardening:NFR-006] - PDF generation quality

Implements test scenarios:
- [api-hardening:PDFGenerator/TS-01] Basic markdown to PDF
- [api-hardening:PDFGenerator/TS-02] Tables render correctly
- [api-hardening:PDFGenerator/TS-03] Rating icons
- [api-hardening:PDFGenerator/TS-04] Policy citations
- [api-hardening:PDFGenerator/TS-05] Long document handling
- [api-hardening:PDFGenerator/TS-06] Unicode support
"""

import io

import structlog
from markdown_it import MarkdownIt
from weasyprint import CSS, HTML

logger = structlog.get_logger(__name__)

# CSS styles for the PDF
PDF_STYLES = """
@page {
    size: A4;
    margin: 2cm;
    @bottom-center {
        content: "Page " counter(page) " of " counter(pages);
        font-size: 10px;
        color: #666;
    }
}

body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.6;
    color: #333;
}

h1 {
    color: #1a1a1a;
    border-bottom: 2px solid #333;
    padding-bottom: 0.5em;
    font-size: 24pt;
    page-break-after: avoid;
}

h2 {
    color: #2a2a2a;
    border-bottom: 1px solid #ccc;
    padding-bottom: 0.3em;
    font-size: 18pt;
    margin-top: 1.5em;
    page-break-after: avoid;
}

h3 {
    color: #3a3a3a;
    font-size: 14pt;
    margin-top: 1em;
    page-break-after: avoid;
}

/* Rating badges */
.rating-green, .rating-🟢 {
    background-color: #22c55e;
    color: white;
    padding: 2px 8px;
    border-radius: 4px;
    font-weight: bold;
}

.rating-amber, .rating-🟡 {
    background-color: #f59e0b;
    color: white;
    padding: 2px 8px;
    border-radius: 4px;
    font-weight: bold;
}

.rating-red, .rating-🔴 {
    background-color: #ef4444;
    color: white;
    padding: 2px 8px;
    border-radius: 4px;
    font-weight: bold;
}

/* Tables */
table {
    width: 100%;
    border-collapse: collapse;
    margin: 1em 0;
    font-size: 10pt;
    page-break-inside: avoid;
}

th {
    background-color: #f3f4f6;
    border: 1px solid #d1d5db;
    padding: 8px 12px;
    text-align: left;
    font-weight: 600;
}

td {
    border: 1px solid #d1d5db;
    padding: 8px 12px;
}

tr:nth-child(even) {
    background-color: #f9fafb;
}

/* Code and quotes */
code {
    background-color: #f3f4f6;
    padding: 2px 4px;
    border-radius: 3px;
    font-family: "Courier New", monospace;
    font-size: 10pt;
}

pre {
    background-color: #f3f4f6;
    padding: 1em;
    border-radius: 4px;
    overflow-x: auto;
    page-break-inside: avoid;
}

blockquote {
    border-left: 4px solid #d1d5db;
    margin: 1em 0;
    padding-left: 1em;
    color: #666;
    font-style: italic;
}

/* Lists */
ul, ol {
    margin: 1em 0;
    padding-left: 2em;
}

li {
    margin-bottom: 0.5em;
}

/* Links */
a {
    color: #2563eb;
    text-decoration: none;
}

/* Policy citations */
.policy-ref {
    font-style: italic;
    color: #4b5563;
}

/* Emoji replacements for PDF */
.emoji-green::before { content: "[GREEN]"; color: #22c55e; font-weight: bold; }
.emoji-amber::before { content: "[AMBER]"; color: #f59e0b; font-weight: bold; }
.emoji-red::before { content: "[RED]"; color: #ef4444; font-weight: bold; }
"""


class PDFGenerator:
    """
    Converts Markdown review content to styled PDF format.

    Uses WeasyPrint for HTML-to-PDF rendering with custom CSS.
    Handles tables, policy citations, and rating indicators.
    """

    def __init__(self, custom_css: str | None = None) -> None:
        """
        Initialize PDF generator.

        Args:
            custom_css: Optional additional CSS to apply.
        """
        # Use commonmark preset with table extension (avoids linkify dependency)
        self.md = MarkdownIt("commonmark").enable("table")
        self.css = PDF_STYLES
        if custom_css:
            self.css += "\n" + custom_css

    def generate(self, markdown_content: str, title: str | None = None) -> bytes:
        """
        Convert Markdown to PDF.

        Args:
            markdown_content: The Markdown content to convert.
            title: Optional document title for the PDF metadata.

        Returns:
            PDF content as bytes.
        """
        # Convert Markdown to HTML
        html_content = self._markdown_to_html(markdown_content, title)

        # Generate PDF
        pdf_bytes = self._html_to_pdf(html_content)

        logger.info(
            "PDF generated",
            markdown_length=len(markdown_content),
            pdf_size=len(pdf_bytes),
        )

        return pdf_bytes

    def _markdown_to_html(self, markdown: str, title: str | None) -> str:
        """Convert Markdown to HTML with styling hooks."""
        # Pre-process: convert emoji ratings to styled spans
        markdown = self._process_rating_emojis(markdown)

        # Convert to HTML
        body_html = self.md.render(markdown)

        # Wrap in full HTML document
        title_tag = f"<title>{title}</title>" if title else ""
        return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    {title_tag}
</head>
<body>
{body_html}
</body>
</html>
"""

    def _process_rating_emojis(self, markdown: str) -> str:
        """Replace rating emojis with styled spans."""
        # Replace common rating patterns
        replacements = [
            ("🟢", '<span class="rating-green">GREEN</span>'),
            ("🟡", '<span class="rating-amber">AMBER</span>'),
            ("🔴", '<span class="rating-red">RED</span>'),
            ("✅", "✓"),
            ("❌", "✗"),
            ("⚠️", "⚠"),
        ]
        for emoji, replacement in replacements:
            markdown = markdown.replace(emoji, replacement)
        return markdown

    def _html_to_pdf(self, html_content: str) -> bytes:
        """Convert HTML to PDF using WeasyPrint."""
        # Create PDF in memory
        pdf_buffer = io.BytesIO()

        html = HTML(string=html_content)
        css = CSS(string=self.css)

        html.write_pdf(pdf_buffer, stylesheets=[css])

        return pdf_buffer.getvalue()
