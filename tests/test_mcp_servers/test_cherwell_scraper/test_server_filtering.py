"""
Tests for document filtering in MCP server.

Verifies [document-filtering:CherwellScraperMCP/TS-01] through TS-04
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_servers.cherwell_scraper.models import DocumentInfo
from src.mcp_servers.cherwell_scraper.server import (
    CherwellScraperMCP,
    DownloadAllDocumentsInput,
)


class TestCherwellScraperMCPFiltering:
    """Tests for document filtering in CherwellScraperMCP."""

    @pytest.fixture
    def scraper(self):
        """Create a CherwellScraperMCP instance for testing."""
        return CherwellScraperMCP(
            portal_url="https://test.portal.example.com",
            rate_limit=0.1,
        )

    @pytest.fixture
    def mock_parser_with_mixed_docs(self):
        """
        Mock parser that returns mixed document types.

        Returns:
        - 2 core documents (should be downloaded)
        - 1 technical assessment (should be downloaded)
        - 2 public comments (should be filtered)
        """
        mock_parser = MagicMock()
        mock_parser.parse_document_list.return_value = [
            DocumentInfo(
                document_id="core1",
                description="Planning Statement",
                document_type="Planning Statement",
                url="https://test.portal.example.com/doc/core1.pdf",
            ),
            DocumentInfo(
                document_id="core2",
                description="Proposed Plans",
                document_type="Proposed Plans",
                url="https://test.portal.example.com/doc/core2.pdf",
            ),
            DocumentInfo(
                document_id="assess1",
                description="Transport Assessment",
                document_type="Transport Assessment",
                url="https://test.portal.example.com/doc/assess1.pdf",
            ),
            DocumentInfo(
                document_id="comment1",
                description="Objection from resident",
                document_type="Public Comment",
                url="https://test.portal.example.com/doc/comment1.pdf",
            ),
            DocumentInfo(
                document_id="comment2",
                description="Letter of objection",
                document_type="Letter of Objection",
                url="https://test.portal.example.com/doc/comment2.pdf",
            ),
        ]
        mock_parser.get_next_page_url.return_value = None  # No pagination
        return mock_parser

    @pytest.fixture
    def mock_client(self):
        """Mock Cherwell client."""
        mock_client = AsyncMock()
        mock_client.get_documents_page.return_value = "<html>mock</html>"
        mock_client.download_document.return_value = 1024  # 1KB per doc
        mock_client.base_url = "https://test.portal.example.com"
        return mock_client

    @pytest.mark.asyncio
    async def test_filter_public_comments(
        self, scraper, mock_parser_with_mixed_docs, mock_client, tmp_path
    ):
        """
        Verifies [document-filtering:CherwellScraperMCP/TS-01] - Filter public comments

        Given: Application has 5 core docs + 3 public comments
        When: download_all_documents called with default settings
        Then: Only 5 core docs downloaded, 3 filtered with reasons in response
        """
        scraper._parser = mock_parser_with_mixed_docs

        input_data = DownloadAllDocumentsInput(
            application_ref="25/01178/REM",
            output_dir=str(tmp_path),
        )

        with patch.object(scraper, "_get_client") as mock_get_client:
            mock_get_client.return_value.__aenter__.return_value = mock_client

            result = await scraper._download_all_documents(input_data)

        # Verify response structure
        assert result["status"] == "success"
        assert result["application_ref"] == "25/01178/REM"

        # Verify counts
        assert result["total_documents"] == 5
        assert result["downloaded_count"] == 3  # core1, core2, assess1
        assert result["filtered_count"] == 2  # comment1, comment2

        # Verify downloads (only allowed documents)
        assert len(result["downloads"]) == 3
        downloaded_ids = {d["document_id"] for d in result["downloads"]}
        assert downloaded_ids == {"core1", "core2", "assess1"}

        # Verify filtered documents
        assert len(result["filtered_documents"]) == 2
        filtered_ids = {f["document_id"] for f in result["filtered_documents"]}
        assert filtered_ids == {"comment1", "comment2"}

        # Check filter reasons are present
        for filtered_doc in result["filtered_documents"]:
            assert "filter_reason" in filtered_doc
            assert "Public comment" in filtered_doc["filter_reason"]

    @pytest.mark.asyncio
    async def test_override_filter(
        self, scraper, mock_parser_with_mixed_docs, mock_client, tmp_path
    ):
        """
        Verifies [document-filtering:CherwellScraperMCP/TS-02] - Override filter

        Given: Application has 10 docs including public comments
        When: download_all_documents called with skip_filter=true
        Then: All 10 documents downloaded, filtered_documents array is empty
        """
        scraper._parser = mock_parser_with_mixed_docs

        input_data = DownloadAllDocumentsInput(
            application_ref="25/01178/REM",
            output_dir=str(tmp_path),
            skip_filter=True,  # Override filter
        )

        with patch.object(scraper, "_get_client") as mock_get_client:
            mock_get_client.return_value.__aenter__.return_value = mock_client

            result = await scraper._download_all_documents(input_data)

        # Verify all documents downloaded
        assert result["total_documents"] == 5
        assert result["downloaded_count"] == 5  # All documents
        assert result["filtered_count"] == 0  # Nothing filtered

        # Verify downloads include ALL documents
        assert len(result["downloads"]) == 5
        downloaded_ids = {d["document_id"] for d in result["downloads"]}
        assert downloaded_ids == {"core1", "core2", "assess1", "comment1", "comment2"}

        # Verify filtered_documents is empty
        assert len(result["filtered_documents"]) == 0

    @pytest.mark.asyncio
    async def test_unknown_type_defaults_to_download(
        self, scraper, mock_client, tmp_path
    ):
        """
        Verifies [document-filtering:CherwellScraperMCP/TS-03] - Unknown type defaults to download

        Given: Application has doc with type "Unknown Category"
        When: download_all_documents called
        Then: Unknown doc is downloaded (fail-safe behavior)
        """
        # Mock parser that returns unknown document type
        mock_parser = MagicMock()
        mock_parser.parse_document_list.return_value = [
            DocumentInfo(
                document_id="unknown1",
                description="Some unknown document",
                document_type="Unknown Category",
                url="https://test.portal.example.com/doc/unknown1.pdf",
            )
        ]
        mock_parser.get_next_page_url.return_value = None
        scraper._parser = mock_parser

        input_data = DownloadAllDocumentsInput(
            application_ref="25/01178/REM",
            output_dir=str(tmp_path),
        )

        with patch.object(scraper, "_get_client") as mock_get_client:
            mock_get_client.return_value.__aenter__.return_value = mock_client

            result = await scraper._download_all_documents(input_data)

        # Verify unknown document is downloaded (fail-safe)
        assert result["total_documents"] == 1
        assert result["downloaded_count"] == 1
        assert result["filtered_count"] == 0

        assert len(result["downloads"]) == 1
        assert result["downloads"][0]["document_id"] == "unknown1"

    @pytest.mark.asyncio
    async def test_response_format_includes_counts(
        self, scraper, mock_parser_with_mixed_docs, mock_client, tmp_path
    ):
        """
        Verifies [document-filtering:CherwellScraperMCP/TS-04] - Response format includes counts

        Given: Application has 20 docs, 5 filtered
        When: download_all_documents completes
        Then: Response has total_documents=20, downloaded_count=15, filtered_count=5
        """
        scraper._parser = mock_parser_with_mixed_docs

        input_data = DownloadAllDocumentsInput(
            application_ref="25/01178/REM",
            output_dir=str(tmp_path),
        )

        with patch.object(scraper, "_get_client") as mock_get_client:
            mock_get_client.return_value.__aenter__.return_value = mock_client

            result = await scraper._download_all_documents(input_data)

        # Verify all required fields are present
        assert "total_documents" in result
        assert "downloaded_count" in result
        assert "filtered_count" in result
        assert "successful_downloads" in result
        assert "failed_downloads" in result
        assert "downloads" in result
        assert "filtered_documents" in result

        # Verify counts are consistent
        assert (
            result["total_documents"]
            == result["downloaded_count"] + result["filtered_count"]
        )
        assert result["downloaded_count"] == len(result["downloads"])
        assert result["filtered_count"] == len(result["filtered_documents"])

    @pytest.mark.asyncio
    async def test_no_documents_found(self, scraper, mock_client, tmp_path):
        """
        Test handling when no documents exist.

        Given: Application has no documents
        When: download_all_documents is called
        Then: Returns success with zero counts
        """
        # Mock parser that returns empty list
        mock_parser = MagicMock()
        mock_parser.parse_document_list.return_value = []
        mock_parser.get_next_page_url.return_value = None
        scraper._parser = mock_parser

        input_data = DownloadAllDocumentsInput(
            application_ref="25/01178/REM",
            output_dir=str(tmp_path),
        )

        with patch.object(scraper, "_get_client") as mock_get_client:
            mock_get_client.return_value.__aenter__.return_value = mock_client

            result = await scraper._download_all_documents(input_data)

        assert result["status"] == "success"
        assert result["total_documents"] == 0
        assert result["downloaded_count"] == 0
        assert result["filtered_count"] == 0
        assert len(result["downloads"]) == 0
        assert len(result["filtered_documents"]) == 0

    @pytest.mark.asyncio
    async def test_all_documents_filtered(self, scraper, mock_client, tmp_path):
        """
        Test handling when all documents are filtered.

        Given: Application has only public comments
        When: download_all_documents is called
        Then: All documents are in filtered_documents, downloads is empty
        """
        # Mock parser that returns only public comments
        mock_parser = MagicMock()
        mock_parser.parse_document_list.return_value = [
            DocumentInfo(
                document_id="comment1",
                description="Public comment 1",
                document_type="Public Comment",
                url="https://test.portal.example.com/doc/comment1.pdf",
            ),
            DocumentInfo(
                document_id="comment2",
                description="Public comment 2",
                document_type="Objection Letter",
                url="https://test.portal.example.com/doc/comment2.pdf",
            ),
        ]
        mock_parser.get_next_page_url.return_value = None
        scraper._parser = mock_parser

        input_data = DownloadAllDocumentsInput(
            application_ref="25/01178/REM",
            output_dir=str(tmp_path),
        )

        with patch.object(scraper, "_get_client") as mock_get_client:
            mock_get_client.return_value.__aenter__.return_value = mock_client

            result = await scraper._download_all_documents(input_data)

        assert result["total_documents"] == 2
        assert result["downloaded_count"] == 0
        assert result["filtered_count"] == 2
        assert len(result["downloads"]) == 0
        assert len(result["filtered_documents"]) == 2


class TestDownloadAllDocumentsInputToggles:
    """
    Tests for review-scope-control toggle fields on DownloadAllDocumentsInput.

    Verifies [review-scope-control:DownloadAllDocumentsInput/TS-01]
    Verifies [review-scope-control:DownloadAllDocumentsInput/TS-02]
    """

    def test_defaults_both_toggles_to_false(self):
        """
        Verifies [review-scope-control:DownloadAllDocumentsInput/TS-01] - Defaults

        Given: No toggle values provided in input
        When: Model is instantiated with only required fields
        Then: include_consultation_responses and include_public_comments are both False
        """
        input_data = DownloadAllDocumentsInput(
            application_ref="25/01178/REM",
            output_dir="/data/raw",
        )

        assert input_data.include_consultation_responses is False
        assert input_data.include_public_comments is False

    def test_accepts_both_toggles_as_true(self):
        """
        Verifies [review-scope-control:DownloadAllDocumentsInput/TS-02] - Accepts true

        Given: Input includes both toggles set to true
        When: Model is instantiated
        Then: Both fields are True
        """
        input_data = DownloadAllDocumentsInput(
            application_ref="25/01178/REM",
            output_dir="/data/raw",
            include_consultation_responses=True,
            include_public_comments=True,
        )

        assert input_data.include_consultation_responses is True
        assert input_data.include_public_comments is True


class TestDownloadAllDocumentsTogglePassthrough:
    """
    Tests that toggle parameters are passed through to the filter.

    Verifies [review-scope-control:_download_all_documents/TS-01]
    """

    @pytest.fixture
    def scraper(self):
        """Create a CherwellScraperMCP instance for testing."""
        return CherwellScraperMCP(
            portal_url="https://test.portal.example.com",
            rate_limit=0.1,
        )

    @pytest.fixture
    def mock_client(self):
        """Mock Cherwell client."""
        mock_client = AsyncMock()
        mock_client.get_documents_page.return_value = "<html>mock</html>"
        mock_client.download_document.return_value = 1024
        mock_client.base_url = "https://test.portal.example.com"
        return mock_client

    @pytest.mark.asyncio
    async def test_passes_consultation_toggle_to_filter(
        self, scraper, mock_client, tmp_path
    ):
        """
        Verifies [review-scope-control:_download_all_documents/TS-01] - Toggle passthrough

        Given: Tool called with include_consultation_responses=True
        When: _download_all_documents executes
        Then: Consultation response documents are downloaded (not filtered)
        """
        mock_parser = MagicMock()
        mock_parser.parse_document_list.return_value = [
            DocumentInfo(
                document_id="cr1",
                description="Consultation Response - OCC Highways",
                document_type="Consultation Response",
                url="https://test.portal.example.com/doc/cr1.pdf",
            ),
            DocumentInfo(
                document_id="core1",
                description="Planning Statement",
                document_type="Planning Statement",
                url="https://test.portal.example.com/doc/core1.pdf",
            ),
        ]
        mock_parser.get_next_page_url.return_value = None
        scraper._parser = mock_parser

        input_data = DownloadAllDocumentsInput(
            application_ref="25/01178/REM",
            output_dir=str(tmp_path),
            include_consultation_responses=True,
        )

        with patch.object(scraper, "_get_client") as mock_get_client:
            mock_get_client.return_value.__aenter__.return_value = mock_client

            result = await scraper._download_all_documents(input_data)

        # Both documents should be downloaded
        assert result["downloaded_count"] == 2
        assert result["filtered_count"] == 0
        downloaded_ids = {d["document_id"] for d in result["downloads"]}
        assert "cr1" in downloaded_ids
