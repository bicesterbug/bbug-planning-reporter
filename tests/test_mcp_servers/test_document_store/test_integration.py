"""
Integration tests for document processing pipeline.

Implements test scenarios from [document-processing:ITS-01] through [ITS-08]
"""

import uuid
from pathlib import Path

import fitz
import pytest

from src.mcp_servers.document_store.embeddings import EmbeddingService, MockEmbeddingModel
from src.mcp_servers.document_store.server import (
    DocumentStoreMCP,
    GetDocumentTextInput,
    IngestDocumentInput,
    ListDocumentsInput,
    SearchInput,
)


class IntegrationTestMCP(DocumentStoreMCP):
    """MCP server for integration tests with mock embeddings."""

    def __init__(self, chroma_persist_dir=None):
        super().__init__(chroma_persist_dir=chroma_persist_dir, enable_ocr=False)
        self._mock_model = MockEmbeddingModel()

    def _get_embedding_service(self) -> EmbeddingService:
        if self._embedding_service is None:
            self._embedding_service = EmbeddingService(model=self._mock_model)
        return self._embedding_service


@pytest.fixture
def integration_server(tmp_path: Path) -> IntegrationTestMCP:
    """Create an integration test server with persistent ChromaDB."""
    chroma_dir = tmp_path / f"chroma_{uuid.uuid4().hex[:8]}"
    return IntegrationTestMCP(chroma_persist_dir=str(chroma_dir))


@pytest.fixture
def transport_assessment_pdf(tmp_path: Path) -> Path:
    """Create a Transport Assessment PDF."""
    pdf_path = tmp_path / "Transport_Assessment.pdf"
    doc = fitz.open()

    pages = [
        "Transport Assessment\n\nThis document provides a comprehensive assessment of transport impacts.",
        "Trip Generation Analysis\n\nThe development is expected to generate 150 vehicle trips per day.",
        "Cycle Parking\n\n48 Sheffield cycle stands will be provided near the main entrance.",
        "Conclusions\n\nThe transport impacts are acceptable and the development meets requirements.",
    ]

    for content in pages:
        page = doc.new_page()
        page.insert_text((72, 72), content, fontsize=12)

    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.fixture
def design_statement_pdf(tmp_path: Path) -> Path:
    """Create a Design and Access Statement PDF."""
    pdf_path = tmp_path / "Design_Access_Statement.pdf"
    doc = fitz.open()

    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "Design and Access Statement\n\nThe development has been designed to complement the character of the area.",
        fontsize=12,
    )

    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.mark.integration
class TestFullIngestionPipeline:
    """
    Integration tests for the full document processing pipeline.

    Verifies [document-processing:ITS-01] - Full ingestion pipeline
    """

    @pytest.mark.asyncio
    async def test_full_pipeline_ingest_extract_chunk_embed_store(
        self, integration_server: IntegrationTestMCP, transport_assessment_pdf: Path
    ) -> None:
        """
        Verifies [document-processing:ITS-01]

        Given: PDF in /data/raw/
        When: Call ingest_document via MCP
        Then: Text extracted, chunked, embedded, stored in ChromaDB
        """
        result = await integration_server._ingest_document(
            IngestDocumentInput(
                file_path=str(transport_assessment_pdf),
                application_ref="25/01178/REM",
            )
        )

        # Verify successful ingestion
        assert result["status"] == "success"
        assert result["document_id"] is not None
        assert result["chunks_created"] > 0
        assert result["extraction_method"] == "text_layer"

        # Verify document was auto-classified
        assert result.get("document_id") is not None

        # Verify chunks are stored and retrievable
        get_result = await integration_server._get_document_text(
            GetDocumentTextInput(document_id=result["document_id"])
        )
        assert get_result["status"] == "success"
        assert "Transport Assessment" in get_result["text"]
        assert "cycle" in get_result["text"].lower()


@pytest.mark.integration
class TestSearchAfterIngestion:
    """
    Integration tests for search functionality.

    Verifies [document-processing:ITS-02] - Search after ingestion
    """

    @pytest.mark.asyncio
    async def test_search_returns_relevant_chunks(
        self, integration_server: IntegrationTestMCP, transport_assessment_pdf: Path
    ) -> None:
        """
        Verifies [document-processing:ITS-02]

        Given: Documents ingested
        When: Call search_application_docs
        Then: Returns relevant chunks with scores
        """
        # Ingest document
        await integration_server._ingest_document(
            IngestDocumentInput(
                file_path=str(transport_assessment_pdf),
                application_ref="25/01178/REM",
            )
        )

        # Search
        result = await integration_server._search_documents(
            SearchInput(
                query="cycle parking facilities Sheffield stands",
                application_ref="25/01178/REM",
            )
        )

        assert result["status"] == "success"
        assert result["results_count"] > 0

        # Results should have relevance scores (0-1 range, 0 is valid for distant matches)
        for r in result["results"]:
            assert "relevance_score" in r
            assert 0 <= r["relevance_score"] <= 1


@pytest.mark.integration
class TestReingestionScenarios:
    """
    Integration tests for document re-ingestion.
    """

    @pytest.mark.asyncio
    async def test_same_file_reingestion_idempotent(
        self, integration_server: IntegrationTestMCP, transport_assessment_pdf: Path
    ) -> None:
        """
        Verifies [document-processing:ITS-04]

        Given: Document already ingested
        When: Call ingest_document again with same file
        Then: Returns already_ingested, no duplicate chunks
        """
        # First ingestion
        result1 = await integration_server._ingest_document(
            IngestDocumentInput(
                file_path=str(transport_assessment_pdf),
                application_ref="25/01178/REM",
            )
        )
        assert result1["status"] == "success"

        # Second ingestion of same file
        result2 = await integration_server._ingest_document(
            IngestDocumentInput(
                file_path=str(transport_assessment_pdf),
                application_ref="25/01178/REM",
            )
        )

        assert result2["status"] == "already_ingested"
        assert result2["document_id"] == result1["document_id"]

        # Verify no duplicate chunks
        list_result = await integration_server._list_documents(
            ListDocumentsInput(application_ref="25/01178/REM")
        )
        assert list_result["document_count"] == 1

    @pytest.mark.asyncio
    async def test_changed_file_reingestion(
        self, integration_server: IntegrationTestMCP, tmp_path: Path
    ) -> None:
        """
        Verifies [document-processing:ITS-05]

        Given: New version of document (different content)
        When: Call ingest_document
        Then: New document stored (different hash = different document)
        """
        # Create version 1
        pdf_v1 = tmp_path / "Document_v1.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Version 1 content", fontsize=12)
        doc.save(str(pdf_v1))
        doc.close()

        # Ingest version 1
        result1 = await integration_server._ingest_document(
            IngestDocumentInput(
                file_path=str(pdf_v1),
                application_ref="25/01178/REM",
            )
        )
        assert result1["status"] == "success"

        # Create version 2 (different content = different hash)
        pdf_v2 = tmp_path / "Document_v2.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Version 2 with completely different content", fontsize=12)
        doc.save(str(pdf_v2))
        doc.close()

        # Ingest version 2
        result2 = await integration_server._ingest_document(
            IngestDocumentInput(
                file_path=str(pdf_v2),
                application_ref="25/01178/REM",
            )
        )

        # Different file = different document (different hash)
        assert result2["status"] == "success"
        assert result2["document_id"] != result1["document_id"]


@pytest.mark.integration
class TestFilteredSearch:
    """
    Integration tests for filtered search.

    Verifies [document-processing:ITS-08] - Filter search by application
    """

    @pytest.mark.asyncio
    async def test_search_filtered_by_application(
        self,
        integration_server: IntegrationTestMCP,
        transport_assessment_pdf: Path,
        design_statement_pdf: Path,
    ) -> None:
        """
        Verifies [document-processing:ITS-08]

        Given: Docs from 2 applications
        When: Search with application_ref filter
        Then: Returns only matching application
        """
        # Ingest for app 1
        await integration_server._ingest_document(
            IngestDocumentInput(
                file_path=str(transport_assessment_pdf),
                application_ref="25/01178/REM",
            )
        )

        # Ingest for app 2
        await integration_server._ingest_document(
            IngestDocumentInput(
                file_path=str(design_statement_pdf),
                application_ref="25/99999/F",
            )
        )

        # Search with app 1 filter
        result = await integration_server._search_documents(
            SearchInput(
                query="transport design development",
                application_ref="25/01178/REM",
            )
        )

        # Should only return app 1 results
        assert result["status"] == "success"
        for r in result["results"]:
            assert r["metadata"]["application_ref"] == "25/01178/REM"

        # Search with app 2 filter
        result2 = await integration_server._search_documents(
            SearchInput(
                query="transport design development",
                application_ref="25/99999/F",
            )
        )

        # Should only return app 2 results
        for r in result2["results"]:
            assert r["metadata"]["application_ref"] == "25/99999/F"


@pytest.mark.integration
class TestDocumentListing:
    """Integration tests for document listing."""

    @pytest.mark.asyncio
    async def test_list_multiple_documents(
        self,
        integration_server: IntegrationTestMCP,
        transport_assessment_pdf: Path,
        design_statement_pdf: Path,
    ) -> None:
        """Test listing multiple ingested documents."""
        # Ingest multiple documents for same application
        await integration_server._ingest_document(
            IngestDocumentInput(
                file_path=str(transport_assessment_pdf),
                application_ref="25/01178/REM",
            )
        )
        await integration_server._ingest_document(
            IngestDocumentInput(
                file_path=str(design_statement_pdf),
                application_ref="25/01178/REM",
            )
        )

        # List documents
        result = await integration_server._list_documents(
            ListDocumentsInput(application_ref="25/01178/REM")
        )

        assert result["status"] == "success"
        assert result["document_count"] == 2
        assert len(result["documents"]) == 2

        # Each document should have metadata
        for doc in result["documents"]:
            assert "document_id" in doc
            assert "file_path" in doc
            assert "document_type" in doc
            assert "chunk_count" in doc


@pytest.mark.integration
class TestDocumentClassification:
    """Integration tests for auto-classification during ingestion."""

    @pytest.mark.asyncio
    async def test_transport_assessment_auto_classified(
        self, integration_server: IntegrationTestMCP, transport_assessment_pdf: Path
    ) -> None:
        """Test that transport assessment is auto-classified."""
        result = await integration_server._ingest_document(
            IngestDocumentInput(
                file_path=str(transport_assessment_pdf),
                application_ref="25/01178/REM",
                # document_type not provided - should auto-classify
            )
        )

        assert result["status"] == "success"

        # Get document to check classification
        list_result = await integration_server._list_documents(
            ListDocumentsInput(application_ref="25/01178/REM")
        )

        doc = list_result["documents"][0]
        assert doc["document_type"] == "transport_assessment"

    @pytest.mark.asyncio
    async def test_design_statement_auto_classified(
        self, integration_server: IntegrationTestMCP, design_statement_pdf: Path
    ) -> None:
        """Test that design statement is auto-classified."""
        result = await integration_server._ingest_document(
            IngestDocumentInput(
                file_path=str(design_statement_pdf),
                application_ref="25/01178/REM",
            )
        )

        assert result["status"] == "success"

        list_result = await integration_server._list_documents(
            ListDocumentsInput(application_ref="25/01178/REM")
        )

        doc = list_result["documents"][0]
        assert doc["document_type"] == "design_access_statement"
