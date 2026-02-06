"""
Tests for worker job functions.

Implements test scenarios for document ingestion jobs.
"""

import uuid
from pathlib import Path

import fitz
import pytest

from src.mcp_servers.document_store.embeddings import EmbeddingService, MockEmbeddingModel
from src.mcp_servers.document_store.server import DocumentStoreMCP
from src.worker.jobs import (
    ingest_application_documents,
    ingest_directory,
    search_documents,
    set_mcp_server_factory,
)


class MockedDocumentStoreMCP(DocumentStoreMCP):
    """DocumentStoreMCP with mock embedding service for testing."""

    def __init__(self, chroma_persist_dir=None, enable_ocr=False):
        super().__init__(chroma_persist_dir=chroma_persist_dir, enable_ocr=enable_ocr)
        self._mock_model = MockEmbeddingModel()

    def _get_embedding_service(self) -> EmbeddingService:
        """Get mocked EmbeddingService."""
        if self._embedding_service is None:
            self._embedding_service = EmbeddingService(model=self._mock_model)
        return self._embedding_service


@pytest.fixture(autouse=True)
def use_test_mcp_server():
    """Use test MCP server with mock embeddings for all tests."""
    set_mcp_server_factory(MockedDocumentStoreMCP)
    yield
    set_mcp_server_factory(None)


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """Create a sample PDF with text content."""
    pdf_path = tmp_path / "Transport_Assessment.pdf"
    doc = fitz.open()

    for content in [
        "Page 1: Transport Assessment Introduction.\n\nThis document assesses traffic impacts.",
        "Page 2: Cycle Parking.\n\n48 Sheffield stands are proposed.",
        "Page 3: Conclusions.\n\nThe development meets requirements.",
    ]:
        page = doc.new_page()
        page.insert_text((72, 72), content, fontsize=12)

    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.fixture
def sample_pdf_directory(tmp_path: Path) -> Path:
    """Create a directory with multiple sample PDFs."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()

    for name, content in [
        ("Transport_Assessment.pdf", "Transport assessment with trip generation analysis."),
        ("Design_Access_Statement.pdf", "Design and access statement for the development."),
        ("Site_Plan.pdf", "Site layout and location plan."),
    ]:
        pdf_path = doc_dir / name
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), content, fontsize=12)
        doc.save(str(pdf_path))
        doc.close()

    return doc_dir


@pytest.fixture
def mock_ctx() -> dict:
    """Create a mock arq context."""
    return {}


class TestIngestApplicationDocuments:
    """Tests for ingest_application_documents job."""

    @pytest.mark.asyncio
    async def test_ingest_single_document(
        self, mock_ctx: dict, sample_pdf: Path, tmp_path: Path
    ) -> None:
        """
        Verifies [document-processing:ITS-01] - Full ingestion pipeline

        Given: PDF in document path list
        When: Call ingest_application_documents
        Then: Document ingested successfully
        """
        chroma_dir = tmp_path / f"chroma_{uuid.uuid4().hex[:8]}"

        result = await ingest_application_documents(
            ctx=mock_ctx,
            application_ref="25/01178/REM",
            document_paths=[str(sample_pdf)],
            chroma_persist_dir=str(chroma_dir),
        )

        assert result["application_ref"] == "25/01178/REM"
        assert result["total_documents"] == 1
        assert result["ingested_count"] == 1
        assert result["failed_count"] == 0
        assert len(result["results"]) == 1
        assert result["results"][0]["status"] == "success"

    @pytest.mark.asyncio
    async def test_ingest_multiple_documents(
        self, mock_ctx: dict, sample_pdf_directory: Path, tmp_path: Path
    ) -> None:
        """Test ingesting multiple documents."""
        chroma_dir = tmp_path / f"chroma_{uuid.uuid4().hex[:8]}"
        document_paths = [str(p) for p in sample_pdf_directory.glob("*.pdf")]

        result = await ingest_application_documents(
            ctx=mock_ctx,
            application_ref="25/01178/REM",
            document_paths=document_paths,
            chroma_persist_dir=str(chroma_dir),
        )

        assert result["total_documents"] == 3
        assert result["ingested_count"] == 3
        assert result["failed_count"] == 0

    @pytest.mark.asyncio
    async def test_graceful_handling_of_missing_file(
        self, mock_ctx: dict, sample_pdf: Path, tmp_path: Path
    ) -> None:
        """
        Verifies [document-processing:ITS-07] - Graceful degradation

        Given: Mix of valid and missing files
        When: Call ingest_application_documents
        Then: Valid files succeed, missing files fail, job continues
        """
        chroma_dir = tmp_path / f"chroma_{uuid.uuid4().hex[:8]}"

        result = await ingest_application_documents(
            ctx=mock_ctx,
            application_ref="25/01178/REM",
            document_paths=[
                str(sample_pdf),
                "/nonexistent/file.pdf",
            ],
            chroma_persist_dir=str(chroma_dir),
        )

        assert result["total_documents"] == 2
        assert result["ingested_count"] == 1
        assert result["failed_count"] == 1

        # Check individual results
        success_result = next(r for r in result["results"] if r["status"] == "success")
        assert success_result["file_path"] == str(sample_pdf)

        failed_result = next(r for r in result["results"] if r["status"] == "error")
        assert "/nonexistent/file.pdf" in failed_result["file_path"]

    @pytest.mark.asyncio
    async def test_idempotent_reingestion(
        self, mock_ctx: dict, sample_pdf: Path, tmp_path: Path
    ) -> None:
        """
        Verifies [document-processing:ITS-04] - Re-ingestion with same file

        Given: Document already ingested
        When: Call ingest_application_documents again
        Then: Returns already_ingested status
        """
        chroma_dir = tmp_path / f"chroma_{uuid.uuid4().hex[:8]}"

        # First ingestion
        result1 = await ingest_application_documents(
            ctx=mock_ctx,
            application_ref="25/01178/REM",
            document_paths=[str(sample_pdf)],
            chroma_persist_dir=str(chroma_dir),
        )
        assert result1["ingested_count"] == 1

        # Second ingestion of same file
        result2 = await ingest_application_documents(
            ctx=mock_ctx,
            application_ref="25/01178/REM",
            document_paths=[str(sample_pdf)],
            chroma_persist_dir=str(chroma_dir),
        )

        # Should count as success (already_ingested)
        assert result2["ingested_count"] == 1
        assert result2["results"][0]["status"] == "already_ingested"


class TestIngestDirectory:
    """Tests for ingest_directory job."""

    @pytest.mark.asyncio
    async def test_ingest_directory(
        self, mock_ctx: dict, sample_pdf_directory: Path, tmp_path: Path
    ) -> None:
        """Test ingesting all PDFs from a directory."""
        chroma_dir = tmp_path / f"chroma_{uuid.uuid4().hex[:8]}"

        result = await ingest_directory(
            ctx=mock_ctx,
            application_ref="25/01178/REM",
            directory=str(sample_pdf_directory),
            chroma_persist_dir=str(chroma_dir),
        )

        assert result["total_documents"] == 3
        assert result["ingested_count"] == 3

    @pytest.mark.asyncio
    async def test_ingest_nonexistent_directory(self, mock_ctx: dict) -> None:
        """Test handling of nonexistent directory."""
        result = await ingest_directory(
            ctx=mock_ctx,
            application_ref="25/01178/REM",
            directory="/nonexistent/directory",
        )

        assert "error" in result
        assert result["total_documents"] == 0

    @pytest.mark.asyncio
    async def test_ingest_empty_directory(
        self, mock_ctx: dict, tmp_path: Path
    ) -> None:
        """Test handling of empty directory."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        result = await ingest_directory(
            ctx=mock_ctx,
            application_ref="25/01178/REM",
            directory=str(empty_dir),
        )

        assert result["total_documents"] == 0
        assert result["ingested_count"] == 0

    @pytest.mark.asyncio
    async def test_ingest_with_pattern(
        self, mock_ctx: dict, sample_pdf_directory: Path, tmp_path: Path
    ) -> None:
        """Test ingesting with specific file pattern."""
        chroma_dir = tmp_path / f"chroma_{uuid.uuid4().hex[:8]}"

        # Add a non-PDF file
        (sample_pdf_directory / "notes.txt").write_text("Some notes")

        result = await ingest_directory(
            ctx=mock_ctx,
            application_ref="25/01178/REM",
            directory=str(sample_pdf_directory),
            file_patterns=["*.pdf"],
            chroma_persist_dir=str(chroma_dir),
        )

        # Should only ingest PDFs
        assert result["total_documents"] == 3


class TestSearchDocuments:
    """Tests for search_documents job."""

    @pytest.mark.asyncio
    async def test_search_after_ingestion(
        self, mock_ctx: dict, sample_pdf: Path, tmp_path: Path
    ) -> None:
        """
        Verifies [document-processing:ITS-02] - Search after ingestion

        Given: Documents ingested
        When: Call search_documents
        Then: Returns relevant chunks with scores
        """
        chroma_dir = tmp_path / f"chroma_{uuid.uuid4().hex[:8]}"

        # First ingest
        await ingest_application_documents(
            ctx=mock_ctx,
            application_ref="25/01178/REM",
            document_paths=[str(sample_pdf)],
            chroma_persist_dir=str(chroma_dir),
        )

        # Then search
        result = await search_documents(
            ctx=mock_ctx,
            query="cycle parking Sheffield stands",
            application_ref="25/01178/REM",
            chroma_persist_dir=str(chroma_dir),
        )

        assert result["status"] == "success"
        assert result["results_count"] > 0

    @pytest.mark.asyncio
    async def test_search_empty_collection(
        self, mock_ctx: dict, tmp_path: Path
    ) -> None:
        """Test search on empty collection."""
        chroma_dir = tmp_path / f"chroma_{uuid.uuid4().hex[:8]}"

        result = await search_documents(
            ctx=mock_ctx,
            query="anything",
            chroma_persist_dir=str(chroma_dir),
        )

        assert result["status"] == "success"
        assert result["results_count"] == 0

    @pytest.mark.asyncio
    async def test_search_with_application_filter(
        self, mock_ctx: dict, sample_pdf_directory: Path, tmp_path: Path
    ) -> None:
        """
        Verifies [document-processing:ITS-08] - Filter search by application

        Given: Documents from multiple applications
        When: Search with application_ref filter
        Then: Returns only matching application documents
        """
        chroma_dir = tmp_path / f"chroma_{uuid.uuid4().hex[:8]}"
        pdfs = list(sample_pdf_directory.glob("*.pdf"))

        # Ingest some docs for app 1
        await ingest_application_documents(
            ctx=mock_ctx,
            application_ref="25/01178/REM",
            document_paths=[str(pdfs[0])],
            chroma_persist_dir=str(chroma_dir),
        )

        # Ingest some docs for app 2
        await ingest_application_documents(
            ctx=mock_ctx,
            application_ref="25/99999/F",
            document_paths=[str(pdfs[1])],
            chroma_persist_dir=str(chroma_dir),
        )

        # Search with filter for app 1
        result = await search_documents(
            ctx=mock_ctx,
            query="transport design site",
            application_ref="25/01178/REM",
            chroma_persist_dir=str(chroma_dir),
        )

        # Should only return app 1 results
        assert result["status"] == "success"
        for r in result["results"]:
            assert r["metadata"]["application_ref"] == "25/01178/REM"
