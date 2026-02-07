"""
HTML parsers for Cherwell planning portal pages.

Implements [foundation-api:FR-009] - Parse application metadata from HTML
Implements [foundation-api:FR-010] - Parse document table from HTML

Implements:
- [foundation-api:CherwellParser/TS-01] Parse application page
- [foundation-api:CherwellParser/TS-02] Parse document table
- [foundation-api:CherwellParser/TS-03] Handle missing fields
- [foundation-api:CherwellParser/TS-04] Handle malformed HTML
"""

import hashlib
import re
from datetime import date, datetime

import structlog
from bs4 import BeautifulSoup, Tag

from src.mcp_servers.cherwell_scraper.models import ApplicationMetadata, DocumentInfo

logger = structlog.get_logger(__name__)


class CherwellParser:
    """
    Parser for Cherwell planning portal HTML pages.

    Extracts structured data from application detail and document list pages.
    Handles malformed HTML gracefully by extracting available fields.
    """

    # Date formats commonly used by Cherwell portals
    DATE_FORMATS = [
        "%d/%m/%Y",
        "%d %b %Y",
        "%d %B %Y",
        "%Y-%m-%d",
        "%d-%m-%Y",
    ]

    def parse_application_details(self, html: str, reference: str) -> ApplicationMetadata:
        """
        Parse application details from the application summary page.

        Implements [foundation-api:CherwellParser/TS-01] - Parse application page
        Implements [foundation-api:CherwellParser/TS-03] - Handle missing fields
        Implements [foundation-api:CherwellParser/TS-04] - Handle malformed HTML

        Args:
            html: Raw HTML content of the application page.
            reference: Application reference for logging context.

        Returns:
            ApplicationMetadata with extracted fields (None for missing fields).
        """
        soup = BeautifulSoup(html, "html.parser")

        # Extract fields from various possible selectors
        metadata = ApplicationMetadata(reference=reference)

        try:
            # Try new Cherwell planning register format (td with label text + span value)
            metadata = self._parse_cherwell_register(soup, metadata)

            # Fallback: Definition list (dl/dt/dd)
            if metadata.address is None:
                metadata = self._parse_definition_list(soup, metadata)

            # Fallback: Table with th/td pairs
            if metadata.address is None:
                metadata = self._parse_table_format(soup, metadata)

            # Fallback: Labelled spans/divs
            if metadata.address is None:
                metadata = self._parse_labelled_elements(soup, metadata)

            logger.debug(
                "Parsed application details",
                reference=reference,
                has_address=metadata.address is not None,
                has_proposal=metadata.proposal is not None,
                has_status=metadata.status is not None,
            )

        except Exception as e:
            logger.warning(
                "Error parsing application details, returning partial data",
                reference=reference,
                error=str(e),
            )

        return metadata

    def _parse_definition_list(
        self, soup: BeautifulSoup, metadata: ApplicationMetadata
    ) -> ApplicationMetadata:
        """Parse fields from definition list format (dl/dt/dd)."""
        for dl in soup.find_all("dl"):
            terms = dl.find_all("dt")
            for dt in terms:
                dd = dt.find_next_sibling("dd")
                if dd:
                    label = self._normalize_label(dt.get_text())
                    value = self._clean_text(dd.get_text())
                    metadata = self._set_field_by_label(metadata, label, value)
        return metadata

    def _parse_table_format(
        self, soup: BeautifulSoup, metadata: ApplicationMetadata
    ) -> ApplicationMetadata:
        """Parse fields from table format (th/td or label td)."""
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all(["th", "td"])
                if len(cells) >= 2:
                    label = self._normalize_label(cells[0].get_text())
                    value = self._clean_text(cells[1].get_text())
                    metadata = self._set_field_by_label(metadata, label, value)
        return metadata

    def _parse_labelled_elements(
        self, soup: BeautifulSoup, metadata: ApplicationMetadata
    ) -> ApplicationMetadata:
        """Parse fields from labelled span/div elements."""
        # Look for common class patterns
        for element in soup.find_all(class_=re.compile(r"(field|label|value|detail)", re.I)):
            # Check for label class followed by value
            if "label" in (element.get("class") or []):
                next_elem = element.find_next_sibling()
                if next_elem:
                    label = self._normalize_label(element.get_text())
                    value = self._clean_text(next_elem.get_text())
                    metadata = self._set_field_by_label(metadata, label, value)
        return metadata

    def _parse_cherwell_register(
        self, soup: BeautifulSoup, metadata: ApplicationMetadata
    ) -> ApplicationMetadata:
        """Parse fields from new Cherwell planning register format.

        The new portal uses <td> cells where the label is plain text
        before a <br> and the value is in a <span> inside a <div>.
        Example: <td>Location <br /> <div><span>The Address</span></div></td>
        """
        for table in soup.find_all("table", class_="summaryTbl"):
            for td in table.find_all("td"):
                # Get the raw text content of the td
                td_text = td.decode_contents()
                # Split on <br> to get label part
                parts = re.split(r"<br\s*/?>", td_text, maxsplit=1)
                if len(parts) < 2:
                    continue

                label_text = BeautifulSoup(parts[0], "html.parser").get_text().strip()
                # Get value from span inside the td
                span = td.find("span")
                if span:
                    value = self._clean_text(span.get_text())
                    if label_text and value:
                        label = self._normalize_label(label_text)
                        metadata = self._set_field_by_label(metadata, label, value)

        return metadata

    def _normalize_label(self, label: str) -> str:
        """Normalize a field label for matching."""
        # Remove colons, extra whitespace, lowercase
        return re.sub(r"[:\s]+", " ", label.lower()).strip()

    def _clean_text(self, text: str) -> str:
        """Clean extracted text."""
        # Normalize whitespace
        text = re.sub(r"\s+", " ", text).strip()
        return text if text else ""

    def _set_field_by_label(
        self, metadata: ApplicationMetadata, label: str, value: str
    ) -> ApplicationMetadata:
        """Set metadata field based on label matching."""
        if not value:
            return metadata

        # Address patterns
        if any(
            p in label
            for p in ["address", "site address", "location", "site location", "property"]
        ):
            metadata.address = value

        # Proposal patterns
        elif any(p in label for p in ["proposal", "description", "development"]):
            metadata.proposal = value

        # Applicant patterns
        elif any(p in label for p in ["applicant name", "applicant"]):
            if "agent" not in label:
                metadata.applicant = value

        # Agent patterns
        elif any(p in label for p in ["agent name", "agent"]):
            metadata.agent = value

        # Status patterns - check "date" first to avoid matching "decision date" as status
        elif any(p in label for p in ["status", "current status"]):
            metadata.status = value

        # Decision (without date) patterns
        elif "decision" in label and "date" not in label:
            metadata.decision = value

        # Application type patterns
        elif any(p in label for p in ["application type", "type", "category"]):
            metadata.application_type = value

        # Ward patterns
        elif any(p in label for p in ["ward", "electoral ward"]):
            metadata.ward = value

        # Parish patterns
        elif any(p in label for p in ["parish", "parish council"]):
            metadata.parish = value

        # Case officer patterns
        elif any(p in label for p in ["case officer", "planning officer", "officer"]):
            metadata.case_officer = value

        # Date patterns
        elif "date" in label:
            parsed_date = self._parse_date(value)
            if parsed_date:
                if "received" in label:
                    metadata.date_received = parsed_date
                elif "validated" in label or "valid" in label:
                    metadata.date_validated = parsed_date
                elif "target" in label:
                    metadata.target_date = parsed_date
                elif "decision" in label:
                    metadata.decision_date = parsed_date

        return metadata

    def _parse_date(self, value: str) -> date | None:
        """Parse date from various formats."""
        for fmt in self.DATE_FORMATS:
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
        return None

    def parse_document_list(self, html: str, reference: str, base_url: str) -> list[DocumentInfo]:
        """
        Parse document list from the documents tab/page.

        Implements [foundation-api:CherwellParser/TS-02] - Parse document table
        Implements [foundation-api:CherwellParser/TS-03] - Handle missing fields
        Implements [foundation-api:CherwellParser/TS-04] - Handle malformed HTML

        Args:
            html: Raw HTML content of the documents page.
            reference: Application reference for logging context.
            base_url: Base URL for resolving relative document links.

        Returns:
            List of DocumentInfo objects.
        """
        soup = BeautifulSoup(html, "html.parser")
        documents: list[DocumentInfo] = []

        try:
            # Try new Cherwell planning register format first
            documents = self._parse_cherwell_register_documents(soup, base_url)

            # Fallback: table-based document list
            if not documents:
                documents = self._parse_document_table(soup, base_url)

            # Fallback: list-based format
            if not documents:
                documents = self._parse_document_list_format(soup, base_url)

            # Fallback: link-based extraction
            if not documents:
                documents = self._parse_document_links(soup, base_url)

            logger.debug(
                "Parsed document list",
                reference=reference,
                document_count=len(documents),
            )

        except Exception as e:
            logger.warning(
                "Error parsing document list, returning partial data",
                reference=reference,
                error=str(e),
            )

        return documents

    def _parse_cherwell_register_documents(
        self, soup: BeautifulSoup, base_url: str
    ) -> list[DocumentInfo]:
        """Parse documents from new Cherwell planning register format.

        Implements [review-output-fixes:CherwellParser/TS-01] - Section headers propagated
        Implements [review-output-fixes:CherwellParser/TS-02] - Consultation category extracted
        Implements [review-output-fixes:CherwellParser/TS-03] - Flat table fallback
        Implements [review-output-fixes:CherwellParser/TS-04] - Description preserved

        Documents have links with class 'singledownloadlink' and URLs like:
        /Document/Download?module=PLA&recordNumber=...&planId=...&imageId=...&isPlan=...&fileName=...

        The portal groups documents under section header rows within the table.
        Each header row has no download link and typically uses colspan or bold text.
        Document rows inherit the category of their preceding section header.
        """
        documents: list[DocumentInfo] = []

        # Find the table containing singledownloadlink elements
        first_link = soup.find("a", class_="singledownloadlink")
        if not first_link:
            return documents

        table = first_link.find_parent("table")
        if not table:
            # No enclosing table — fall back to flat link iteration
            return self._parse_cherwell_register_documents_flat(soup, base_url)

        # Walk all rows in the table in document order
        current_section: str | None = None

        for row in table.find_all("tr"):
            link = row.find("a", class_="singledownloadlink")

            if link and "/Document/Download" in link.get("href", ""):
                # This is a document row
                href = link.get("href", "")
                url = self._resolve_url(href, base_url)
                if not url:
                    continue

                cells = row.find_all("td")
                # Typical structure: checkbox | link (filename) | date | description | size | plans
                link_text = self._clean_text(link.get_text())
                description = link_text
                date_published = None
                if len(cells) >= 4:
                    description = self._clean_text(cells[3].get_text()) or link_text
                if len(cells) >= 3:
                    date_str = self._clean_text(cells[2].get_text())
                    date_published = self._parse_date(date_str)

                doc_id = self._generate_document_id(url)

                documents.append(
                    DocumentInfo(
                        document_id=doc_id,
                        description=description or "Unknown Document",
                        document_type=current_section,
                        date_published=date_published,
                        url=url,
                    )
                )
            else:
                # Potential section header row — no download link
                header_text = self._extract_section_header(row)
                if header_text:
                    current_section = header_text

        return documents

    def _extract_section_header(self, row: Tag) -> str | None:
        """Extract section header text from a non-document table row.

        Implements [review-output-fixes:FR-001] - Extract document category from portal

        Section headers are identified by:
        - Rows with no singledownloadlink
        - Rows with colspan cells (spanning the full table width)
        - Rows with bold/strong text content
        - Non-empty text that doesn't look like a table header (th)
        """
        # Skip rows that are the table header (th elements)
        if row.find("th"):
            return None

        cells = row.find_all("td")
        if not cells:
            return None

        # Check for colspan cell (common for section headers)
        for cell in cells:
            colspan = cell.get("colspan")
            if colspan and int(colspan) > 1:
                text = self._clean_text(cell.get_text())
                if text:
                    return text

        # Check for a row with a single meaningful cell or bold text
        strong = row.find(["strong", "b"])
        if strong:
            text = self._clean_text(strong.get_text())
            if text:
                return text

        return None

    def _parse_cherwell_register_documents_flat(
        self, soup: BeautifulSoup, base_url: str
    ) -> list[DocumentInfo]:
        """Fallback: parse singledownloadlink elements without section headers.

        Implements [review-output-fixes:CherwellParser/TS-03] - Flat table fallback

        Used when documents are not in a table or when no section headers are found.
        Falls back to the original behaviour of using link text as category.
        """
        documents: list[DocumentInfo] = []

        for link in soup.find_all("a", class_="singledownloadlink"):
            href = link.get("href", "")
            if "/Document/Download" not in href:
                continue

            url = self._resolve_url(href, base_url)
            if not url:
                continue

            category = self._clean_text(link.get_text())

            row = link.find_parent("tr")
            if not row:
                continue

            cells = row.find_all("td")
            description = category
            date_published = None
            if len(cells) >= 4:
                description = self._clean_text(cells[3].get_text()) or category
            if len(cells) >= 3:
                date_str = self._clean_text(cells[2].get_text())
                date_published = self._parse_date(date_str)

            doc_id = self._generate_document_id(url)

            documents.append(
                DocumentInfo(
                    document_id=doc_id,
                    description=description or "Unknown Document",
                    document_type=category if category != description else None,
                    date_published=date_published,
                    url=url,
                )
            )

        return documents

    def _parse_document_table(self, soup: BeautifulSoup, base_url: str) -> list[DocumentInfo]:
        """Parse documents from table format."""
        documents: list[DocumentInfo] = []

        # Look for document tables
        for table in soup.find_all("table"):
            # Check if this looks like a document table
            headers = table.find_all("th")
            header_text = " ".join(th.get_text().lower() for th in headers)

            if not any(
                keyword in header_text
                for keyword in ["document", "description", "date", "type", "file"]
            ):
                continue

            # Parse header indices
            header_map = {}
            for i, th in enumerate(headers):
                text = th.get_text().lower().strip()
                if "description" in text or "document" in text or "title" in text:
                    header_map["description"] = i
                elif "type" in text or "category" in text:
                    header_map["type"] = i
                elif "date" in text:
                    header_map["date"] = i

            # Parse rows
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if not cells:
                    continue

                # Find document link
                link = row.find("a", href=True)
                if not link:
                    continue

                url = self._resolve_url(link.get("href", ""), base_url)
                if not url:
                    continue

                # Extract fields
                description = self._clean_text(link.get_text())
                if not description and "description" in header_map:
                    description = self._clean_text(cells[header_map["description"]].get_text())

                doc_type = None
                if "type" in header_map and header_map["type"] < len(cells):
                    doc_type = self._clean_text(cells[header_map["type"]].get_text())

                date_published = None
                if "date" in header_map and header_map["date"] < len(cells):
                    date_str = self._clean_text(cells[header_map["date"]].get_text())
                    date_published = self._parse_date(date_str)

                # Generate document ID from URL
                doc_id = self._generate_document_id(url)

                documents.append(
                    DocumentInfo(
                        document_id=doc_id,
                        description=description or "Unknown Document",
                        document_type=doc_type,
                        date_published=date_published,
                        url=url,
                    )
                )

        return documents

    def _parse_document_list_format(
        self, soup: BeautifulSoup, base_url: str
    ) -> list[DocumentInfo]:
        """Parse documents from list format (ul/li)."""
        documents: list[DocumentInfo] = []

        for ul in soup.find_all("ul", class_=re.compile(r"document", re.I)):
            for li in ul.find_all("li"):
                link = li.find("a", href=True)
                if not link:
                    continue

                url = self._resolve_url(link.get("href", ""), base_url)
                if not url:
                    continue

                description = self._clean_text(link.get_text())
                doc_id = self._generate_document_id(url)

                documents.append(
                    DocumentInfo(
                        document_id=doc_id,
                        description=description or "Unknown Document",
                        url=url,
                    )
                )

        return documents

    def _parse_document_links(self, soup: BeautifulSoup, base_url: str) -> list[DocumentInfo]:
        """Parse documents by finding PDF/document links."""
        documents: list[DocumentInfo] = []

        # Look for links to documents (PDFs, etc.)
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")

            # Check if this looks like a document link
            if not any(
                pattern in href.lower()
                for pattern in [".pdf", "/document/", "/viewdoc", "docid=", "fileid="]
            ):
                continue

            url = self._resolve_url(href, base_url)
            if not url:
                continue

            description = self._clean_text(link.get_text())
            if not description:
                # Try to get description from nearby text
                parent = link.parent
                if parent:
                    description = self._clean_text(parent.get_text())

            doc_id = self._generate_document_id(url)

            documents.append(
                DocumentInfo(
                    document_id=doc_id,
                    description=description or "Unknown Document",
                    url=url,
                )
            )

        return documents

    def _resolve_url(self, href: str, base_url: str) -> str | None:
        """Resolve relative URL to absolute."""
        if not href:
            return None

        # Already absolute
        if href.startswith(("http://", "https://")):
            return href

        # Protocol-relative
        if href.startswith("//"):
            return "https:" + href

        # Absolute path
        if href.startswith("/"):
            # Extract base domain from base_url
            from urllib.parse import urlparse

            parsed = urlparse(base_url)
            return f"{parsed.scheme}://{parsed.netloc}{href}"

        # Relative path
        if not base_url.endswith("/"):
            base_url = base_url.rsplit("/", 1)[0] + "/"
        return base_url + href

    def _generate_document_id(self, url: str) -> str:
        """Generate a stable document ID from URL."""
        # Create hash of URL for stable ID
        return hashlib.md5(url.encode()).hexdigest()[:12]

    def get_next_page_url(self, html: str, base_url: str) -> str | None:
        """
        Extract next page URL for paginated document lists.

        Implements [foundation-api:CherwellScraperMCP/TS-08] - Paginated document list

        Args:
            html: HTML content of current page.
            base_url: Base URL for resolving relative links.

        Returns:
            URL of next page, or None if no more pages.
        """
        soup = BeautifulSoup(html, "html.parser")

        # Look for common pagination patterns
        # Pattern 1: "Next" link
        for link in soup.find_all("a", href=True):
            text = link.get_text().lower().strip()
            if text in ["next", "next >", "next page", ">>", ">"]:
                return self._resolve_url(link.get("href", ""), base_url)

        # Pattern 2: Page numbers with "current" class
        current = soup.find(class_=re.compile(r"current|active", re.I))
        if current and isinstance(current, Tag):
            next_sibling = current.find_next_sibling("a", href=True)
            if next_sibling:
                return self._resolve_url(next_sibling.get("href", ""), base_url)

        return None
