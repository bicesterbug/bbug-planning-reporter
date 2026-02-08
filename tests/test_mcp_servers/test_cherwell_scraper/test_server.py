"""
Tests for CherwellScraperMCP server.

Implements:
- [foundation-api:CherwellScraperMCP/TS-01] Fetch application details
- [foundation-api:CherwellScraperMCP/TS-02] Non-existent application
- [foundation-api:CherwellScraperMCP/TS-03] List documents
- [foundation-api:CherwellScraperMCP/TS-04] Download single document
- [foundation-api:CherwellScraperMCP/TS-05] Download all documents
"""

import tempfile
from pathlib import Path

import httpx
import pytest
import respx

from src.mcp_servers.cherwell_scraper.server import CherwellScraperMCP


@pytest.fixture
def base_url() -> str:
    """Base URL for mock server."""
    return "https://planning.test.gov.uk"


@pytest.fixture
def mcp_server(base_url: str) -> CherwellScraperMCP:
    """Create MCP server instance."""
    return CherwellScraperMCP(portal_url=base_url, rate_limit=0.1)


class TestGetApplicationDetails:
    """Tests for get_application_details tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_fetch_application_details_success(
        self, mcp_server: CherwellScraperMCP, base_url: str
    ):
        """
        Verifies [foundation-api:CherwellScraperMCP/TS-01] - Fetch application details

        Given: Valid reference "25/01178/REM"
        When: Call get_application_details
        Then: Returns structured metadata (address, proposal, dates, status)
        """
        html = """
        <html><body>
            <dl>
                <dt>Address:</dt>
                <dd>123 Test Street, Cherwell</dd>
                <dt>Proposal:</dt>
                <dd>Construction of dwelling</dd>
                <dt>Status:</dt>
                <dd>Under Consideration</dd>
                <dt>Date Received:</dt>
                <dd>15/01/2025</dd>
            </dl>
        </body></html>
        """
        respx.get(f"{base_url}/Planning/Display/25/01178/REM").mock(
            return_value=httpx.Response(200, text=html)
        )

        # Call the tool handler directly
        from src.mcp_servers.cherwell_scraper.server import GetApplicationDetailsInput

        result = await mcp_server._get_application_details(
            GetApplicationDetailsInput(application_ref="25/01178/REM")
        )

        assert result["status"] == "success"
        assert "application" in result
        app = result["application"]
        assert app["reference"] == "25/01178/REM"
        assert app["address"] == "123 Test Street, Cherwell"
        assert app["proposal"] == "Construction of dwelling"
        assert app["status"] == "Under Consideration"
        assert app["date_received"] == "2025-01-15"

    @pytest.mark.asyncio
    @respx.mock
    async def test_fetch_nonexistent_application(
        self, mcp_server: CherwellScraperMCP, base_url: str
    ):
        """
        Verifies [foundation-api:CherwellScraperMCP/TS-02] - Non-existent application

        Given: Invalid reference
        When: Call get_application_details
        Then: Returns error with code "application_not_found"
        """
        respx.get(f"{base_url}/Planning/Display/99/99999/XXX").mock(
            return_value=httpx.Response(404, text="Not Found")
        )

        from src.mcp_servers.cherwell_scraper.client import ApplicationNotFoundError
        from src.mcp_servers.cherwell_scraper.server import GetApplicationDetailsInput

        with pytest.raises(ApplicationNotFoundError) as exc_info:
            await mcp_server._get_application_details(
                GetApplicationDetailsInput(application_ref="99/99999/XXX")
            )

        assert exc_info.value.error_code == "application_not_found"


class TestListApplicationDocuments:
    """Tests for list_application_documents tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_documents_success(
        self, mcp_server: CherwellScraperMCP, base_url: str
    ):
        """
        Verifies [foundation-api:CherwellScraperMCP/TS-03] - List documents

        Given: Application with documents
        When: Call list_application_documents
        Then: Returns array with document info (type, date, URL)
        """
        html = """
        <html><body>
            <table>
                <thead>
                    <tr><th>Document Type</th><th>Description</th><th>Date</th></tr>
                </thead>
                <tbody>
                    <tr>
                        <td>Application</td>
                        <td><a href="/docs/app.pdf">Application Form</a></td>
                        <td>15/01/2025</td>
                    </tr>
                    <tr>
                        <td>Plans</td>
                        <td><a href="/docs/plans.pdf">Site Plans</a></td>
                        <td>15/01/2025</td>
                    </tr>
                </tbody>
            </table>
        </body></html>
        """
        respx.get(f"{base_url}/Planning/Display/25/01178/REM").mock(
            return_value=httpx.Response(200, text=html)
        )

        from src.mcp_servers.cherwell_scraper.server import ListApplicationDocumentsInput

        result = await mcp_server._list_application_documents(
            ListApplicationDocumentsInput(application_ref="25/01178/REM")
        )

        assert result["status"] == "success"
        assert result["document_count"] == 2
        assert len(result["documents"]) == 2

        doc = result["documents"][0]
        assert "document_id" in doc
        assert "description" in doc
        assert "url" in doc
        assert doc["url"].startswith("https://")

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_documents_empty(
        self, mcp_server: CherwellScraperMCP, base_url: str
    ):
        """
        Verifies [foundation-api:CherwellScraperMCP/TS-03] - List documents

        Given: Application with no documents
        When: Call list_application_documents
        Then: Returns empty array
        """
        html = """
        <html><body>
            <p>No documents have been uploaded.</p>
        </body></html>
        """
        respx.get(f"{base_url}/Planning/Display/25/01178/REM").mock(
            return_value=httpx.Response(200, text=html)
        )

        from src.mcp_servers.cherwell_scraper.server import ListApplicationDocumentsInput

        result = await mcp_server._list_application_documents(
            ListApplicationDocumentsInput(application_ref="25/01178/REM")
        )

        assert result["status"] == "success"
        assert result["document_count"] == 0
        assert result["documents"] == []


class TestDownloadDocument:
    """Tests for download_document tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_download_single_document(
        self, mcp_server: CherwellScraperMCP, base_url: str
    ):
        """
        Verifies [foundation-api:CherwellScraperMCP/TS-04] - Download single document

        Given: Valid document URL
        When: Call download_document
        Then: File saved to output_dir, path returned
        """
        pdf_content = b"%PDF-1.4 test content"
        respx.get(f"{base_url}/docs/test.pdf").mock(
            return_value=httpx.Response(200, content=pdf_content)
        )

        from src.mcp_servers.cherwell_scraper.server import DownloadDocumentInput

        with tempfile.TemporaryDirectory() as tmpdir:
            result = await mcp_server._download_document(
                DownloadDocumentInput(
                    document_url=f"{base_url}/docs/test.pdf",
                    output_dir=tmpdir,
                    filename="test_doc.pdf",
                )
            )

            assert result["status"] == "success"
            assert "file_path" in result
            assert result["file_size"] == len(pdf_content)

            # Verify file was created
            output_path = Path(result["file_path"])
            assert output_path.exists()
            assert output_path.read_bytes() == pdf_content


class TestDownloadAllDocuments:
    """Tests for download_all_documents tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_download_all_documents(
        self, mcp_server: CherwellScraperMCP, base_url: str
    ):
        """
        Verifies [foundation-api:CherwellScraperMCP/TS-05] - Download all documents

        Given: Application with 3 documents
        When: Call download_all_documents
        Then: All 3 files saved, paths returned
        """
        # Mock document list page
        list_html = """
        <html><body>
            <table>
                <thead><tr><th>Document</th></tr></thead>
                <tbody>
                    <tr><td><a href="/docs/doc1.pdf">Document 1</a></td></tr>
                    <tr><td><a href="/docs/doc2.pdf">Document 2</a></td></tr>
                    <tr><td><a href="/docs/doc3.pdf">Document 3</a></td></tr>
                </tbody>
            </table>
        </body></html>
        """
        respx.get(f"{base_url}/Planning/Display/25/01178/REM").mock(
            return_value=httpx.Response(200, text=list_html)
        )

        # Mock document downloads
        pdf_content = b"%PDF-1.4 test"
        respx.get(f"{base_url}/docs/doc1.pdf").mock(
            return_value=httpx.Response(200, content=pdf_content)
        )
        respx.get(f"{base_url}/docs/doc2.pdf").mock(
            return_value=httpx.Response(200, content=pdf_content)
        )
        respx.get(f"{base_url}/docs/doc3.pdf").mock(
            return_value=httpx.Response(200, content=pdf_content)
        )

        from src.mcp_servers.cherwell_scraper.server import DownloadAllDocumentsInput

        with tempfile.TemporaryDirectory() as tmpdir:
            result = await mcp_server._download_all_documents(
                DownloadAllDocumentsInput(
                    application_ref="25/01178/REM",
                    output_dir=tmpdir,
                )
            )

            assert result["status"] == "success"
            assert result["total_documents"] == 3
            assert result["successful_downloads"] == 3
            assert result["failed_downloads"] == 0

            # Verify directory structure
            output_dir = Path(result["output_dir"])
            assert output_dir.exists()
            assert output_dir.name == "25_01178_REM"

            # Verify files exist
            files = list(output_dir.glob("*.pdf"))
            assert len(files) == 3

    @pytest.mark.asyncio
    @respx.mock
    async def test_download_all_with_failures(
        self, mcp_server: CherwellScraperMCP, base_url: str
    ):
        """
        Verifies [foundation-api:CherwellScraperMCP/TS-05] - Download all documents

        Given: Application with 3 documents, 1 fails to download
        When: Call download_all_documents
        Then: 2 succeed, 1 fails, all results reported
        """
        list_html = """
        <html><body>
            <table>
                <thead><tr><th>Document</th></tr></thead>
                <tbody>
                    <tr><td><a href="/docs/doc1.pdf">Document 1</a></td></tr>
                    <tr><td><a href="/docs/doc2.pdf">Document 2</a></td></tr>
                    <tr><td><a href="/docs/doc3.pdf">Document 3</a></td></tr>
                </tbody>
            </table>
        </body></html>
        """
        respx.get(f"{base_url}/Planning/Display/25/01178/REM").mock(
            return_value=httpx.Response(200, text=list_html)
        )

        pdf_content = b"%PDF-1.4 test"
        respx.get(f"{base_url}/docs/doc1.pdf").mock(
            return_value=httpx.Response(200, content=pdf_content)
        )
        respx.get(f"{base_url}/docs/doc2.pdf").mock(
            return_value=httpx.Response(500, text="Server Error")
        )
        respx.get(f"{base_url}/docs/doc3.pdf").mock(
            return_value=httpx.Response(200, content=pdf_content)
        )

        from src.mcp_servers.cherwell_scraper.server import DownloadAllDocumentsInput

        with tempfile.TemporaryDirectory() as tmpdir:
            result = await mcp_server._download_all_documents(
                DownloadAllDocumentsInput(
                    application_ref="25/01178/REM",
                    output_dir=tmpdir,
                )
            )

            assert result["status"] == "success"
            assert result["total_documents"] == 3
            assert result["successful_downloads"] == 2
            assert result["failed_downloads"] == 1

            # Check individual download results
            downloads = result["downloads"]
            successful = [d for d in downloads if d["success"]]
            failed = [d for d in downloads if not d["success"]]

            assert len(successful) == 2
            assert len(failed) == 1
            assert "error" in failed[0]
