"""
Tests for CherwellParser HTML parsing.

Implements:
- [foundation-api:CherwellParser/TS-01] Parse application page
- [foundation-api:CherwellParser/TS-02] Parse document table
- [foundation-api:CherwellParser/TS-03] Handle missing fields
- [foundation-api:CherwellParser/TS-04] Handle malformed HTML
"""

from pathlib import Path

import pytest

from src.mcp_servers.cherwell_scraper.parsers import CherwellParser


@pytest.fixture
def parser() -> CherwellParser:
    """Create a parser instance."""
    return CherwellParser()


class TestParseApplicationDetails:
    """Tests for application details parsing."""

    def test_parse_definition_list_format(self, parser: CherwellParser):
        """
        Verifies [foundation-api:CherwellParser/TS-01] - Parse application page

        Given: HTML with definition list format (dl/dt/dd)
        When: Parse application details
        Then: Extracts all available fields correctly
        """
        html = """
        <html>
        <body>
            <dl>
                <dt>Address:</dt>
                <dd>123 Test Street, Cherwell OX1 1AA</dd>
                <dt>Proposal:</dt>
                <dd>Construction of new dwelling house</dd>
                <dt>Applicant Name:</dt>
                <dd>John Smith</dd>
                <dt>Status:</dt>
                <dd>Under Consideration</dd>
                <dt>Application Type:</dt>
                <dd>Full Planning Permission</dd>
                <dt>Ward:</dt>
                <dd>Central Ward</dd>
                <dt>Date Received:</dt>
                <dd>15/01/2025</dd>
                <dt>Target Date:</dt>
                <dd>15/03/2025</dd>
                <dt>Case Officer:</dt>
                <dd>Jane Doe</dd>
            </dl>
        </body>
        </html>
        """

        metadata = parser.parse_application_details(html, "25/00001/FUL")

        assert metadata.reference == "25/00001/FUL"
        assert metadata.address == "123 Test Street, Cherwell OX1 1AA"
        assert metadata.proposal == "Construction of new dwelling house"
        assert metadata.applicant == "John Smith"
        assert metadata.status == "Under Consideration"
        assert metadata.application_type == "Full Planning Permission"
        assert metadata.ward == "Central Ward"
        assert metadata.date_received is not None
        assert metadata.date_received.day == 15
        assert metadata.date_received.month == 1
        assert metadata.date_received.year == 2025
        assert metadata.case_officer == "Jane Doe"

    def test_parse_table_format(self, parser: CherwellParser):
        """
        Verifies [foundation-api:CherwellParser/TS-01] - Parse application page

        Given: HTML with table format (th/td)
        When: Parse application details
        Then: Extracts fields from table rows
        """
        html = """
        <html>
        <body>
            <table>
                <tr><th>Site Address</th><td>456 Main Road, Banbury</td></tr>
                <tr><th>Proposal</th><td>Erection of garage</td></tr>
                <tr><th>Applicant</th><td>Alice Brown</td></tr>
                <tr><th>Current Status</th><td>Approved</td></tr>
                <tr><th>Date Validated</th><td>20 Feb 2025</td></tr>
                <tr><th>Decision Date</th><td>10 Apr 2025</td></tr>
            </table>
        </body>
        </html>
        """

        metadata = parser.parse_application_details(html, "25/00002/HOU")

        assert metadata.reference == "25/00002/HOU"
        assert metadata.address == "456 Main Road, Banbury"
        assert metadata.proposal == "Erection of garage"
        assert metadata.applicant == "Alice Brown"
        assert metadata.status == "Approved"
        assert metadata.date_validated is not None
        assert metadata.date_validated.month == 2
        assert metadata.decision_date is not None
        assert metadata.decision_date.month == 4

    def test_handle_missing_fields(self, parser: CherwellParser):
        """
        Verifies [foundation-api:CherwellParser/TS-03] - Handle missing fields

        Given: HTML with only some fields present
        When: Parse application details
        Then: Returns available fields, None for missing
        """
        html = """
        <html>
        <body>
            <dl>
                <dt>Address:</dt>
                <dd>Partial Data Street</dd>
                <dt>Status:</dt>
                <dd>Pending</dd>
            </dl>
        </body>
        </html>
        """

        metadata = parser.parse_application_details(html, "25/00003/OUT")

        assert metadata.reference == "25/00003/OUT"
        assert metadata.address == "Partial Data Street"
        assert metadata.status == "Pending"
        assert metadata.proposal is None
        assert metadata.applicant is None
        assert metadata.case_officer is None
        assert metadata.date_received is None

    def test_handle_malformed_html(self, parser: CherwellParser):
        """
        Verifies [foundation-api:CherwellParser/TS-04] - Handle malformed HTML

        Given: Slightly broken/malformed HTML
        When: Parse application details
        Then: Gracefully handles and extracts what's possible
        """
        html = """
        <html>
        <body>
            <dl>
                <dt>Address:
                <dd>Unclosed Tag Lane</dd>
                <dt>Proposal</dt>  <!-- Missing colon -->
                <dd>Some development
                <!-- Missing closing dd tag -->
            </dl>
            <!-- Missing closing tags -->
        """

        # Should not raise an exception
        metadata = parser.parse_application_details(html, "25/00004/FUL")

        assert metadata.reference == "25/00004/FUL"
        # Should extract at least partial data
        assert metadata.address is not None or metadata.proposal is not None

    def test_empty_html(self, parser: CherwellParser):
        """
        Verifies [foundation-api:CherwellParser/TS-03] - Handle missing fields

        Given: Empty or minimal HTML
        When: Parse application details
        Then: Returns metadata with reference only
        """
        html = "<html><body></body></html>"

        metadata = parser.parse_application_details(html, "25/00005/OUT")

        assert metadata.reference == "25/00005/OUT"
        assert metadata.address is None
        assert metadata.proposal is None


class TestParseDocumentList:
    """Tests for document list parsing."""

    def test_parse_document_table(self, parser: CherwellParser):
        """
        Verifies [foundation-api:CherwellParser/TS-02] - Parse document table

        Given: HTML with document table
        When: Parse document list
        Then: Extracts list with type, date, description, URL
        """
        html = """
        <html>
        <body>
            <table>
                <thead>
                    <tr>
                        <th>Document Type</th>
                        <th>Description</th>
                        <th>Date</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>Application Form</td>
                        <td><a href="/documents/doc1.pdf">Application Form</a></td>
                        <td>15/01/2025</td>
                    </tr>
                    <tr>
                        <td>Plans</td>
                        <td><a href="/documents/doc2.pdf">Site Plan</a></td>
                        <td>15/01/2025</td>
                    </tr>
                    <tr>
                        <td>Supporting Statement</td>
                        <td><a href="/documents/doc3.pdf">Design and Access Statement</a></td>
                        <td>16/01/2025</td>
                    </tr>
                </tbody>
            </table>
        </body>
        </html>
        """

        documents = parser.parse_document_list(
            html, "25/00001/FUL", "https://planning.cherwell.gov.uk"
        )

        assert len(documents) == 3

        # Check first document
        assert documents[0].description == "Application Form"
        assert documents[0].url == "https://planning.cherwell.gov.uk/documents/doc1.pdf"
        assert documents[0].date_published is not None
        assert documents[0].date_published.day == 15

        # Check document types extracted
        assert documents[1].description == "Site Plan"
        assert documents[2].description == "Design and Access Statement"

    def test_parse_document_links(self, parser: CherwellParser):
        """
        Verifies [foundation-api:CherwellParser/TS-02] - Parse document table

        Given: HTML with document links (no table)
        When: Parse document list
        Then: Extracts documents from links
        """
        html = """
        <html>
        <body>
            <div class="documents">
                <a href="https://example.com/documents/report.pdf">Planning Report</a>
                <a href="/viewdoc?id=123">Transport Assessment</a>
                <a href="/document/456/view">Noise Impact Study</a>
            </div>
        </body>
        </html>
        """

        documents = parser.parse_document_list(
            html, "25/00002/FUL", "https://planning.cherwell.gov.uk"
        )

        assert len(documents) >= 3

        urls = [d.url for d in documents]
        assert "https://example.com/documents/report.pdf" in urls
        assert "https://planning.cherwell.gov.uk/viewdoc?id=123" in urls
        assert "https://planning.cherwell.gov.uk/document/456/view" in urls

    def test_handle_empty_document_list(self, parser: CherwellParser):
        """
        Verifies [foundation-api:CherwellParser/TS-03] - Handle missing fields

        Given: HTML with no documents
        When: Parse document list
        Then: Returns empty list
        """
        html = """
        <html>
        <body>
            <p>No documents have been uploaded for this application.</p>
        </body>
        </html>
        """

        documents = parser.parse_document_list(
            html, "25/00003/FUL", "https://planning.cherwell.gov.uk"
        )

        assert documents == []

    def test_handle_malformed_document_table(self, parser: CherwellParser):
        """
        Verifies [foundation-api:CherwellParser/TS-04] - Handle malformed HTML

        Given: Malformed document table HTML
        When: Parse document list
        Then: Gracefully handles and extracts what's possible
        """
        html = """
        <html>
        <body>
            <table>
                <tr>
                    <th>Document</th>
                    <th>Date
                    <!-- Missing closing th -->
                </tr>
                <tr>
                    <td><a href="/doc1.pdf">First Document
                    <!-- Missing closing tags -->
                </tr>
            </table>
        """

        # Should not raise an exception
        documents = parser.parse_document_list(
            html, "25/00004/FUL", "https://planning.cherwell.gov.uk"
        )

        # Should extract at least some documents
        assert isinstance(documents, list)


class TestSectionHeaderExtraction:
    """Tests for section header extraction from Cherwell register format.

    Implements [review-output-fixes:CherwellParser/TS-01] through TS-04.
    """

    @pytest.fixture
    def parser(self) -> CherwellParser:
        return CherwellParser()

    @pytest.fixture
    def html_with_categories(self) -> str:
        fixture_path = (
            Path(__file__).parent.parent.parent
            / "fixtures"
            / "cherwell"
            / "document_table_with_categories.html"
        )
        return fixture_path.read_text()

    def test_section_headers_propagated(self, parser: CherwellParser, html_with_categories: str):
        """
        Verifies [review-output-fixes:CherwellParser/TS-01] - Section headers propagated

        Given: HTML table with "Application Forms" header followed by 2 docs,
               then "Supporting Documents" header followed by 3 docs
        When: parse_document_list() is called
        Then: First 2 docs have document_type="Application Forms",
              next 3 have document_type="Supporting Documents"
        """
        documents = parser.parse_document_list(
            html_with_categories,
            "25/00284/F",
            "https://planningregister.cherwell.gov.uk",
        )

        # 2 Application Forms + 3 Supporting Documents + 3 Consultation Responses + 1 Site Plans = 9
        assert len(documents) == 9

        # First 2 docs under "Application Forms"
        assert documents[0].document_type == "Application Forms"
        assert documents[1].document_type == "Application Forms"

        # Next 3 docs under "Supporting Documents"
        assert documents[2].document_type == "Supporting Documents"
        assert documents[3].document_type == "Supporting Documents"
        assert documents[4].document_type == "Supporting Documents"

    def test_consultation_category_extracted(
        self, parser: CherwellParser, html_with_categories: str
    ):
        """
        Verifies [review-output-fixes:CherwellParser/TS-02] - Consultation category extracted

        Given: HTML table with a "Consultation Responses" section header
        When: parse_document_list() is called
        Then: Documents under that header get document_type="Consultation Responses"
        """
        documents = parser.parse_document_list(
            html_with_categories,
            "25/00284/F",
            "https://planningregister.cherwell.gov.uk",
        )

        consultation_docs = [d for d in documents if d.document_type == "Consultation Responses"]
        assert len(consultation_docs) == 3

        descriptions = [d.description for d in consultation_docs]
        assert "Transport Response to Consultees" in descriptions
        assert "Applicant's response to ATE comments" in descriptions
        assert "Consultation Response" in descriptions

    def test_flat_table_fallback(self, parser: CherwellParser):
        """
        Verifies [review-output-fixes:CherwellParser/TS-03] - Flat table fallback

        Given: HTML with singledownloadlink elements but no section header rows
        When: parse_document_list() is called
        Then: Falls back to current behaviour (link text as document_type if
              different from description, else None)
        """
        html = """
        <html><body>
        <table>
          <tr>
            <td><input type="checkbox" /></td>
            <td><a class="singledownloadlink"
                   href="/Document/Download?module=PLA&amp;recordNumber=1&amp;planId=1&amp;imageId=1&amp;isPlan=False&amp;fileName=Report.pdf">Report.pdf</a></td>
            <td>01/01/2025</td>
            <td>Planning Statement</td>
            <td>1 MB</td>
          </tr>
          <tr>
            <td><input type="checkbox" /></td>
            <td><a class="singledownloadlink"
                   href="/Document/Download?module=PLA&amp;recordNumber=1&amp;planId=2&amp;imageId=2&amp;isPlan=False&amp;fileName=Plan.pdf">Site Plan</a></td>
            <td>01/01/2025</td>
            <td>Site Plan</td>
            <td>2 MB</td>
          </tr>
        </table>
        </body></html>
        """
        documents = parser.parse_document_list(
            html, "25/00001/F", "https://planningregister.cherwell.gov.uk"
        )

        assert len(documents) == 2
        # No section headers found — document_type should be None since there
        # are no header rows to propagate
        assert documents[0].document_type is None
        assert documents[1].document_type is None
        # Descriptions still come from the 4th cell
        assert documents[0].description == "Planning Statement"
        assert documents[1].description == "Site Plan"

    def test_description_preserved(self, parser: CherwellParser, html_with_categories: str):
        """
        Verifies [review-output-fixes:CherwellParser/TS-04] - Description preserved

        Given: HTML table with section headers where each row has description in 4th cell
        When: parse_document_list() is called
        Then: description is the 4th cell text (not the section header);
              document_type is the section header
        """
        documents = parser.parse_document_list(
            html_with_categories,
            "25/00284/F",
            "https://planningregister.cherwell.gov.uk",
        )

        # Check a Supporting Documents entry
        ta_doc = next(
            d for d in documents
            if "Transport Assessment" in d.description and d.document_type == "Supporting Documents"
        )
        assert ta_doc.description == "ES Appendix 5.1 Transport Assessment (Part 1 of 7)"
        assert ta_doc.document_type == "Supporting Documents"

        # Check a Consultation Responses entry
        consultee_doc = next(
            d for d in documents
            if d.description == "Transport Response to Consultees"
        )
        assert consultee_doc.document_type == "Consultation Responses"

        # Check that Site Plans doc works too
        masterplan = next(d for d in documents if d.description == "Masterplan")
        assert masterplan.document_type == "Site Plans"


class TestThSectionHeaderExtraction:
    """Tests for <th>-based section header extraction from real Cherwell portal format.

    Verifies [reliable-category-filtering:CherwellParser/TS-01] through TS-05.
    """

    @pytest.fixture
    def parser(self) -> CherwellParser:
        return CherwellParser()

    @pytest.fixture
    def html_with_th_headers(self) -> str:
        fixture_path = (
            Path(__file__).parent.parent.parent
            / "fixtures"
            / "cherwell"
            / "document_table_th_headers.html"
        )
        return fixture_path.read_text()

    def test_single_th_section_header_extracted(self, parser: CherwellParser):
        """
        Verifies [reliable-category-filtering:CherwellParser/TS-01]

        Given: HTML row <tr class="header active"><th>Supporting Documents</th></tr>
        When: _extract_section_header(row) called
        Then: Returns "Supporting Documents"
        """
        from bs4 import BeautifulSoup

        html = '<table><tr class="header active"><th>Supporting Documents</th></tr></table>'
        soup = BeautifulSoup(html, "html.parser")
        row = soup.find("tr")

        result = parser._extract_section_header(row)
        assert result == "Supporting Documents"

    def test_multi_th_column_header_skipped(self, parser: CherwellParser):
        """
        Verifies [reliable-category-filtering:CherwellParser/TS-02]

        Given: HTML row with 6 <th> cells (column headers)
        When: _extract_section_header(row) called
        Then: Returns None
        """
        from bs4 import BeautifulSoup

        html = """<table><tr>
            <th>All</th>
            <th>Document Type</th>
            <th>Date</th>
            <th>Description</th>
            <th>File Size</th>
            <th>Drawing/Rev Number</th>
        </tr></table>"""
        soup = BeautifulSoup(html, "html.parser")
        row = soup.find("tr")

        result = parser._extract_section_header(row)
        assert result is None

    def test_all_portal_categories_extracted(
        self, parser: CherwellParser, html_with_th_headers: str
    ):
        """
        Verifies [reliable-category-filtering:CherwellParser/TS-03]

        Given: HTML table with <th> section headers for 5 categories
        When: parse_document_list() called
        Then: Each document has the correct document_type matching its section header
        """
        documents = parser.parse_document_list(
            html_with_th_headers,
            "21/03267/OUT",
            "https://planningregister.cherwell.gov.uk",
        )

        assert len(documents) == 9

        # Application Forms (2 docs)
        assert documents[0].document_type == "Application Forms"
        assert documents[0].description == "App Form"
        assert documents[1].document_type == "Application Forms"
        assert documents[1].description == "Cover Letter"

        # Supporting Documents (2 docs)
        assert documents[2].document_type == "Supporting Documents"
        assert documents[2].description == "Transport Assessment"
        assert documents[3].document_type == "Supporting Documents"
        assert documents[3].description == "Planning Statement"

        # Site Plans (1 doc)
        assert documents[4].document_type == "Site Plans"
        assert documents[4].description == "Masterplan"

        # Consultee Responses (2 docs)
        assert documents[5].document_type == "Consultee Responses"
        assert documents[5].description == "Oxfordshire County Council"
        assert documents[6].document_type == "Consultee Responses"
        assert documents[6].description == "Thames Water Comments"

        # Public Comments (2 docs)
        assert documents[7].document_type == "Public Comments"
        assert documents[7].description == "Swift House, Street From Baynards Green"
        assert documents[8].document_type == "Public Comments"
        assert documents[8].description == "Garden Cottage, Swifts House Farm"

    def test_existing_colspan_headers_still_work(
        self, parser: CherwellParser
    ):
        """
        Verifies [reliable-category-filtering:CherwellParser/TS-04]

        Given: HTML table from existing fixture with <td colspan>/<strong> headers
        When: parse_document_list() called
        Then: Section headers extracted correctly (backward compat)
        """
        fixture_path = (
            Path(__file__).parent.parent.parent
            / "fixtures"
            / "cherwell"
            / "document_table_with_categories.html"
        )
        html = fixture_path.read_text()

        documents = parser.parse_document_list(
            html, "25/00284/F", "https://planningregister.cherwell.gov.uk"
        )

        assert len(documents) == 9
        assert documents[0].document_type == "Application Forms"
        assert documents[2].document_type == "Supporting Documents"
        consultation_docs = [d for d in documents if d.document_type == "Consultation Responses"]
        assert len(consultation_docs) == 3

    def test_no_none_document_types(
        self, parser: CherwellParser, html_with_th_headers: str
    ):
        """
        Verifies [reliable-category-filtering:CherwellParser/TS-05]

        Given: HTML table with <th> section headers and document rows
        When: parse_document_list() called
        Then: Every document's document_type is non-None
        """
        documents = parser.parse_document_list(
            html_with_th_headers,
            "21/03267/OUT",
            "https://planningregister.cherwell.gov.uk",
        )

        for doc in documents:
            assert doc.document_type is not None, (
                f"Document '{doc.description}' has document_type=None"
            )


class TestPagination:
    """Tests for pagination handling."""

    def test_get_next_page_url_with_next_link(self, parser: CherwellParser):
        """
        Verifies [foundation-api:CherwellScraperMCP/TS-08] - Paginated document list

        Given: HTML with "Next" pagination link
        When: Get next page URL
        Then: Returns correct URL
        """
        html = """
        <html>
        <body>
            <div class="pagination">
                <a href="/page/1">1</a>
                <span class="current">2</span>
                <a href="/page/3">3</a>
                <a href="/page/3">Next</a>
            </div>
        </body>
        </html>
        """

        next_url = parser.get_next_page_url(html, "https://planning.cherwell.gov.uk")

        assert next_url == "https://planning.cherwell.gov.uk/page/3"

    def test_get_next_page_url_no_next(self, parser: CherwellParser):
        """
        Verifies [foundation-api:CherwellScraperMCP/TS-08] - Paginated document list

        Given: HTML on last page (no Next link)
        When: Get next page URL
        Then: Returns None
        """
        html = """
        <html>
        <body>
            <div class="pagination">
                <a href="/page/1">Previous</a>
                <span class="current">3</span>
            </div>
        </body>
        </html>
        """

        next_url = parser.get_next_page_url(html, "https://planning.cherwell.gov.uk")

        assert next_url is None


class TestDateParsing:
    """Tests for date parsing from various formats."""

    def test_parse_date_formats(self, parser: CherwellParser):
        """Test parsing of various date formats."""
        # DD/MM/YYYY
        assert parser._parse_date("15/01/2025").day == 15
        assert parser._parse_date("15/01/2025").month == 1

        # DD Mon YYYY
        assert parser._parse_date("15 Jan 2025").month == 1

        # DD Month YYYY
        assert parser._parse_date("15 January 2025").month == 1

        # YYYY-MM-DD
        assert parser._parse_date("2025-01-15").day == 15

        # Invalid date
        assert parser._parse_date("not a date") is None
