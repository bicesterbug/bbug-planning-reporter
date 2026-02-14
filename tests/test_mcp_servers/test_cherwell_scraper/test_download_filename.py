"""
Tests for download filename extraction and deduplication.

Verifies [download-filename-fix:FR-001] - Extract filename from query parameter
Verifies [download-filename-fix:FR-002] - Disambiguate duplicate filenames
Verifies [download-filename-fix:NFR-001] - Backwards compatibility
"""

import tempfile
from pathlib import Path

import httpx
import pytest
import respx

from src.mcp_servers.cherwell_scraper.server import (
    CherwellScraperMCP,
    DownloadDocumentInput,
)


@pytest.fixture
def base_url() -> str:
    return "https://planning.test.gov.uk"


@pytest.fixture
def mcp_server(base_url: str) -> CherwellScraperMCP:
    return CherwellScraperMCP(portal_url=base_url, rate_limit=0.0)


PDF_CONTENT = b"%PDF-1.4 test content"


class TestFilenameFromQueryParameter:
    """Verifies [download-filename-fix:_download_document/TS-01], TS-02."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_filename_from_query_param(
        self, mcp_server: CherwellScraperMCP, base_url: str
    ):
        """Verifies [download-filename-fix:_download_document/TS-01].

        Given: URL has fileName=Report.pdf query param
        When: _download_document is called
        Then: File is saved as Report.pdf
        """
        url = f"{base_url}/Document/Download?module=PLA&recordNumber=1&fileName=Report.pdf"
        respx.get(url).mock(return_value=httpx.Response(200, content=PDF_CONTENT))

        with tempfile.TemporaryDirectory() as tmpdir:
            result = await mcp_server._download_document(
                DownloadDocumentInput(document_url=url, output_dir=tmpdir)
            )
            assert result["status"] == "success"
            assert Path(result["file_path"]).name == "Report.pdf"
            assert Path(result["file_path"]).exists()

    @pytest.mark.asyncio
    @respx.mock
    async def test_url_encoded_filename(
        self, mcp_server: CherwellScraperMCP, base_url: str
    ):
        """Verifies [download-filename-fix:_download_document/TS-02].

        Given: URL has fileName=Transport%20Assessment.pdf
        When: _download_document is called
        Then: File is saved as Transport Assessment.pdf
        """
        url = f"{base_url}/Document/Download?module=PLA&fileName=Transport%20Assessment.pdf"
        respx.get(url).mock(return_value=httpx.Response(200, content=PDF_CONTENT))

        with tempfile.TemporaryDirectory() as tmpdir:
            result = await mcp_server._download_document(
                DownloadDocumentInput(document_url=url, output_dir=tmpdir)
            )
            assert Path(result["file_path"]).name == "Transport Assessment.pdf"


class TestPathBasedFallback:
    """Verifies [download-filename-fix:_download_document/TS-03], TS-04, TS-05."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_filename_from_path(
        self, mcp_server: CherwellScraperMCP, base_url: str
    ):
        """Verifies [download-filename-fix:_download_document/TS-03].

        Given: URL has filename in path (no query params)
        When: _download_document is called
        Then: File is saved with path-based filename
        """
        url = f"{base_url}/files/report.pdf"
        respx.get(url).mock(return_value=httpx.Response(200, content=PDF_CONTENT))

        with tempfile.TemporaryDirectory() as tmpdir:
            result = await mcp_server._download_document(
                DownloadDocumentInput(document_url=url, output_dir=tmpdir)
            )
            assert Path(result["file_path"]).name == "report.pdf"

    @pytest.mark.asyncio
    @respx.mock
    async def test_hash_based_default(
        self, mcp_server: CherwellScraperMCP, base_url: str
    ):
        """Verifies [download-filename-fix:_download_document/TS-04].

        Given: URL path has no usable filename and no query params
        When: _download_document is called
        Then: File is saved as document_XXXXXXXX.pdf
        """
        url = f"{base_url}/"
        respx.get(url).mock(return_value=httpx.Response(200, content=PDF_CONTENT))

        with tempfile.TemporaryDirectory() as tmpdir:
            result = await mcp_server._download_document(
                DownloadDocumentInput(document_url=url, output_dir=tmpdir)
            )
            name = Path(result["file_path"]).name
            assert name.startswith("document_")
            assert name.endswith(".pdf")

    @pytest.mark.asyncio
    @respx.mock
    async def test_explicit_filename_parameter(
        self, mcp_server: CherwellScraperMCP, base_url: str
    ):
        """Verifies [download-filename-fix:_download_document/TS-05].

        Given: filename input parameter is provided
        When: _download_document is called
        Then: Explicit filename is used
        """
        url = f"{base_url}/Document/Download?fileName=Report.pdf"
        respx.get(url).mock(return_value=httpx.Response(200, content=PDF_CONTENT))

        with tempfile.TemporaryDirectory() as tmpdir:
            result = await mcp_server._download_document(
                DownloadDocumentInput(
                    document_url=url, output_dir=tmpdir, filename="custom_name.pdf"
                )
            )
            assert Path(result["file_path"]).name == "custom_name.pdf"


class TestDuplicateFilenameDisambiguation:
    """Verifies [download-filename-fix:_download_document/TS-06], TS-07."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_duplicate_filename_gets_suffix(
        self, mcp_server: CherwellScraperMCP, base_url: str
    ):
        """Verifies [download-filename-fix:_download_document/TS-06].

        Given: A file named Report.pdf already exists in output dir
        When: Another document with the same filename is downloaded
        Then: Second file is saved as Report_1.pdf
        """
        url1 = f"{base_url}/Document/Download?fileName=Report.pdf&imageId=1"
        url2 = f"{base_url}/Document/Download?fileName=Report.pdf&imageId=2"
        respx.get(url1).mock(return_value=httpx.Response(200, content=PDF_CONTENT))
        respx.get(url2).mock(return_value=httpx.Response(200, content=b"second pdf"))

        with tempfile.TemporaryDirectory() as tmpdir:
            r1 = await mcp_server._download_document(
                DownloadDocumentInput(document_url=url1, output_dir=tmpdir)
            )
            r2 = await mcp_server._download_document(
                DownloadDocumentInput(document_url=url2, output_dir=tmpdir)
            )
            assert Path(r1["file_path"]).name == "Report.pdf"
            assert Path(r2["file_path"]).name == "Report_1.pdf"
            assert Path(r2["file_path"]).exists()

    @pytest.mark.asyncio
    @respx.mock
    async def test_multiple_duplicates(
        self, mcp_server: CherwellScraperMCP, base_url: str
    ):
        """Verifies [download-filename-fix:_download_document/TS-07].

        Given: Three documents with the same filename
        When: All three are downloaded
        Then: Files are Report.pdf, Report_1.pdf, Report_2.pdf
        """
        urls = [
            f"{base_url}/Document/Download?fileName=Report.pdf&imageId={i}"
            for i in range(3)
        ]
        for url in urls:
            respx.get(url).mock(
                return_value=httpx.Response(200, content=PDF_CONTENT)
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            results = []
            for url in urls:
                r = await mcp_server._download_document(
                    DownloadDocumentInput(document_url=url, output_dir=tmpdir)
                )
                results.append(r)

            names = [Path(r["file_path"]).name for r in results]
            assert names == ["Report.pdf", "Report_1.pdf", "Report_2.pdf"]
