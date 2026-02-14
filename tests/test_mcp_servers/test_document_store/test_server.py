"""
Tests for DocumentStoreMCP server.

Implements test scenarios from [document-processing:DocumentStoreMCP/TS-01] through [TS-12]
"""

import json
import uuid
from pathlib import Path

import chromadb
import fitz
import pytest
from chromadb.config import Settings

from src.mcp_servers.document_store.chroma_client import ChromaClient
from src.mcp_servers.document_store.embeddings import MockEmbeddingModel
from src.mcp_servers.document_store.server import DocumentStoreMCP


class IsolatedDocumentStoreMCP(DocumentStoreMCP):
    """DocumentStoreMCP with test isolation."""

    def __init__(self, test_id: str) -> None:
        super().__init__(chroma_persist_dir=None, enable_ocr=False)
        self._test_id = test_id
        self._mock_model = MockEmbeddingModel()

    def _get_chroma_client(self) -> ChromaClient:
        """Get ChromaClient with isolated collections."""
        if self._chroma_client is None:
            # Create isolated ChromaClient
            client = chromadb.EphemeralClient(settings=Settings(anonymized_telemetry=False))

            class IsolatedChromaClient(ChromaClient):
                def __init__(inner_self, chroma_client, test_id):
                    super().__init__(client=chroma_client)
                    inner_self._isolated_test_id = test_id

                @property
                def COLLECTION_NAME(inner_self) -> str:
                    return f"test_docs_{inner_self._isolated_test_id}"

                @property
                def DOCUMENT_REGISTRY_COLLECTION(inner_self) -> str:
                    return f"test_registry_{inner_self._isolated_test_id}"

            self._chroma_client = IsolatedChromaClient(client, self._test_id)
        return self._chroma_client

    def _get_embedding_service(self):
        """Get mocked EmbeddingService."""
        if self._embedding_service is None:
            from src.mcp_servers.document_store.embeddings import EmbeddingService

            self._embedding_service = EmbeddingService(model=self._mock_model)
        return self._embedding_service


@pytest.fixture
def mcp_server() -> DocumentStoreMCP:
    """Create an isolated DocumentStoreMCP for testing."""
    test_id = uuid.uuid4().hex[:8]
    return IsolatedDocumentStoreMCP(test_id)


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """Create a sample PDF with text content."""
    pdf_path = tmp_path / "sample_document.pdf"
    doc = fitz.open()

    for _, content in enumerate(
        [
            "Page 1: Introduction to the Transport Assessment.\n\nThis document provides an overview of traffic impacts.",
            "Page 2: Cycle Parking Provision.\n\n48 Sheffield stands are proposed near the main entrance.",
            "Page 3: Conclusions and Recommendations.\n\nThe development meets local plan requirements.",
        ]
    ):
        page = doc.new_page()
        page.insert_text((72, 72), content, fontsize=12)

    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.fixture
def empty_pdf(tmp_path: Path) -> Path:
    """Create an empty PDF."""
    pdf_path = tmp_path / "empty.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


class TestIngestDocument:
    """Tests for ingest_document tool."""

    @pytest.mark.asyncio
    async def test_ingest_valid_pdf(
        self, mcp_server: DocumentStoreMCP, sample_pdf: Path
    ) -> None:
        """
        Verifies [document-processing:DocumentStoreMCP/TS-01]

        Given: PDF file path
        When: Call ingest_document
        Then: Returns success with chunk count
        """
        from src.mcp_servers.document_store.server import IngestDocumentInput

        input_data = IngestDocumentInput(
            file_path=str(sample_pdf),
            application_ref="25/01178/REM",
            document_type="transport_assessment",
        )

        result = await mcp_server._ingest_document(input_data)

        assert result["status"] == "success"
        assert result["document_id"] is not None
        assert result["chunks_created"] > 0
        assert result["extraction_method"] == "text_layer"

    @pytest.mark.asyncio
    async def test_ingest_already_processed(
        self, mcp_server: DocumentStoreMCP, sample_pdf: Path
    ) -> None:
        """
        Verifies [document-processing:DocumentStoreMCP/TS-02]

        Given: Same file ingested before
        When: Call ingest_document
        Then: Returns already_ingested status
        """
        from src.mcp_servers.document_store.server import IngestDocumentInput

        input_data = IngestDocumentInput(
            file_path=str(sample_pdf),
            application_ref="25/01178/REM",
            document_type="transport_assessment",
        )

        # First ingestion
        result1 = await mcp_server._ingest_document(input_data)
        assert result1["status"] == "success"

        # Second ingestion of same file
        result2 = await mcp_server._ingest_document(input_data)
        assert result2["status"] == "already_ingested"
        assert result2["document_id"] == result1["document_id"]

    @pytest.mark.asyncio
    async def test_ingest_invalid_file_type(
        self, mcp_server: DocumentStoreMCP, tmp_path: Path
    ) -> None:
        """
        Verifies [document-processing:DocumentStoreMCP/TS-03]

        Given: .docx file path
        When: Call ingest_document
        Then: Returns unsupported_file_type error
        """
        from src.mcp_servers.document_store.server import IngestDocumentInput

        docx_file = tmp_path / "document.docx"
        docx_file.write_text("content")

        input_data = IngestDocumentInput(
            file_path=str(docx_file),
            application_ref="25/01178/REM",
        )

        result = await mcp_server._ingest_document(input_data)

        assert result["status"] == "error"
        assert result["error_type"] == "unsupported_file_type"

    @pytest.mark.asyncio
    async def test_ingest_nonexistent_file(
        self, mcp_server: DocumentStoreMCP
    ) -> None:
        """
        Verifies [document-processing:DocumentStoreMCP/TS-04]

        Given: Invalid path
        When: Call ingest_document
        Then: Returns file_not_found error
        """
        from src.mcp_servers.document_store.server import IngestDocumentInput

        input_data = IngestDocumentInput(
            file_path="/nonexistent/path/file.pdf",
            application_ref="25/01178/REM",
        )

        result = await mcp_server._ingest_document(input_data)

        assert result["status"] == "error"
        assert result["error_type"] == "file_not_found"

    @pytest.mark.asyncio
    async def test_ingest_empty_pdf(
        self, mcp_server: DocumentStoreMCP, empty_pdf: Path
    ) -> None:
        """
        Given: Empty PDF with no text
        When: Call ingest_document
        Then: Returns no_content error
        """
        from src.mcp_servers.document_store.server import IngestDocumentInput

        input_data = IngestDocumentInput(
            file_path=str(empty_pdf),
            application_ref="25/01178/REM",
        )

        result = await mcp_server._ingest_document(input_data)

        assert result["status"] == "error"
        assert result["error_type"] == "no_content"


class TestImageBasedDocumentSkip:
    """
    Tests for image-based document skip during ingestion.

    Verifies [document-type-detection:DocumentStoreMCP/TS-01] through [TS-03]
    """

    @pytest.fixture
    def image_heavy_pdf(self, tmp_path: Path) -> Path:
        """Create a PDF where all pages are image-heavy."""
        from PIL import Image

        pdf_path = tmp_path / "site_plan.pdf"
        img_path = tmp_path / "plan.png"

        img = Image.new("RGB", (2000, 2000), color="blue")
        img.save(str(img_path))

        doc = fitz.open()
        for _ in range(2):
            page = doc.new_page()
            page.insert_image(page.rect, filename=str(img_path))
        doc.save(str(pdf_path))
        doc.close()
        return pdf_path

    @pytest.mark.asyncio
    async def test_image_based_pdf_skipped(
        self, mcp_server: DocumentStoreMCP, image_heavy_pdf: Path
    ) -> None:
        """
        Verifies [document-type-detection:DocumentStoreMCP/TS-01]

        Given: A PDF classified as image-based (ratio > threshold)
        When: ingest_document tool is called
        Then: Returns {"status": "skipped", "reason": "image_based", "image_ratio": ...}
        """
        from src.mcp_servers.document_store.server import IngestDocumentInput

        input_data = IngestDocumentInput(
            file_path=str(image_heavy_pdf),
            application_ref="21/03266/F",
        )

        result = await mcp_server._ingest_document(input_data)

        assert result["status"] == "skipped"
        assert result["reason"] == "image_based"
        assert result["image_ratio"] > 0.7
        assert result["total_pages"] == 2

    @pytest.mark.asyncio
    async def test_text_pdf_ingested_normally(
        self, mcp_server: DocumentStoreMCP, sample_pdf: Path
    ) -> None:
        """
        Verifies [document-type-detection:DocumentStoreMCP/TS-02]

        Given: A PDF classified as text-based (ratio < threshold)
        When: ingest_document tool is called
        Then: Returns {"status": "success", "chunks_created": ...} as before
        """
        from src.mcp_servers.document_store.server import IngestDocumentInput

        input_data = IngestDocumentInput(
            file_path=str(sample_pdf),
            application_ref="21/03266/F",
            document_type="transport_assessment",
        )

        result = await mcp_server._ingest_document(input_data)

        assert result["status"] == "success"
        assert result["chunks_created"] > 0

    @pytest.mark.asyncio
    async def test_duplicate_check_before_classification(
        self, mcp_server: DocumentStoreMCP, sample_pdf: Path
    ) -> None:
        """
        Verifies [document-type-detection:DocumentStoreMCP/TS-03]

        Given: A PDF that was already ingested
        When: ingest_document tool is called
        Then: Returns "already_ingested" (duplicate check before classification)
        """
        from src.mcp_servers.document_store.server import IngestDocumentInput

        input_data = IngestDocumentInput(
            file_path=str(sample_pdf),
            application_ref="21/03266/F",
            document_type="transport_assessment",
        )

        # First ingestion
        result1 = await mcp_server._ingest_document(input_data)
        assert result1["status"] == "success"

        # Second ingestion — should return already_ingested, not re-classify
        result2 = await mcp_server._ingest_document(input_data)
        assert result2["status"] == "already_ingested"


class TestSearchDocuments:
    """Tests for search_application_docs tool."""

    @pytest.mark.asyncio
    async def test_search_with_results(
        self, mcp_server: DocumentStoreMCP, sample_pdf: Path
    ) -> None:
        """
        Verifies [document-processing:DocumentStoreMCP/TS-05]

        Given: Query matching content
        When: Call search_application_docs
        Then: Returns ranked results
        """
        from src.mcp_servers.document_store.server import IngestDocumentInput, SearchInput

        # First ingest a document
        ingest_input = IngestDocumentInput(
            file_path=str(sample_pdf),
            application_ref="25/01178/REM",
        )
        await mcp_server._ingest_document(ingest_input)

        # Search for content
        search_input = SearchInput(
            query="cycle parking Sheffield stands",
            application_ref="25/01178/REM",
            max_results=5,
        )
        result = await mcp_server._search_documents(search_input)

        assert result["status"] == "success"
        assert result["results_count"] > 0
        assert len(result["results"]) > 0

        # Check result structure
        first_result = result["results"][0]
        assert "chunk_id" in first_result
        assert "text" in first_result
        assert "relevance_score" in first_result

    @pytest.mark.asyncio
    async def test_search_no_results(
        self, mcp_server: DocumentStoreMCP
    ) -> None:
        """
        Verifies [document-processing:DocumentStoreMCP/TS-06]

        Given: Query not matching
        When: Call search_application_docs
        Then: Returns empty results array
        """
        from src.mcp_servers.document_store.server import SearchInput

        search_input = SearchInput(
            query="completely unrelated query about quantum physics",
            max_results=5,
        )
        result = await mcp_server._search_documents(search_input)

        assert result["status"] == "success"
        assert result["results_count"] == 0
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_search_with_filter(
        self, mcp_server: DocumentStoreMCP, sample_pdf: Path
    ) -> None:
        """
        Verifies [document-processing:DocumentStoreMCP/TS-07]

        Given: Query + application_ref
        When: Call search_application_docs
        Then: Returns filtered results
        """
        from src.mcp_servers.document_store.server import IngestDocumentInput, SearchInput

        # Ingest document for one application
        ingest_input = IngestDocumentInput(
            file_path=str(sample_pdf),
            application_ref="25/01178/REM",
        )
        await mcp_server._ingest_document(ingest_input)

        # Search with different application filter
        search_input = SearchInput(
            query="transport assessment",
            application_ref="25/99999/F",  # Different application
            max_results=5,
        )
        result = await mcp_server._search_documents(search_input)

        assert result["status"] == "success"
        assert result["results_count"] == 0  # No results for this application


class TestGetDocumentText:
    """Tests for get_document_text tool."""

    @pytest.mark.asyncio
    async def test_get_document_text(
        self, mcp_server: DocumentStoreMCP, sample_pdf: Path
    ) -> None:
        """
        Verifies [document-processing:DocumentStoreMCP/TS-08]

        Given: Valid document_id
        When: Call get_document_text
        Then: Returns concatenated text
        """
        from src.mcp_servers.document_store.server import (
            GetDocumentTextInput,
            IngestDocumentInput,
        )

        # First ingest a document
        ingest_input = IngestDocumentInput(
            file_path=str(sample_pdf),
            application_ref="25/01178/REM",
        )
        ingest_result = await mcp_server._ingest_document(ingest_input)
        document_id = ingest_result["document_id"]

        # Get document text
        get_input = GetDocumentTextInput(document_id=document_id)
        result = await mcp_server._get_document_text(get_input)

        assert result["status"] == "success"
        assert result["document_id"] == document_id
        assert "text" in result
        assert "Transport Assessment" in result["text"]

    @pytest.mark.asyncio
    async def test_get_nonexistent_document(
        self, mcp_server: DocumentStoreMCP
    ) -> None:
        """
        Verifies [document-processing:DocumentStoreMCP/TS-09]

        Given: Invalid document_id
        When: Call get_document_text
        Then: Returns document_not_found error
        """
        from src.mcp_servers.document_store.server import GetDocumentTextInput

        get_input = GetDocumentTextInput(document_id="nonexistent_doc_id")
        result = await mcp_server._get_document_text(get_input)

        assert result["status"] == "error"
        assert result["error_type"] == "document_not_found"


class TestListDocuments:
    """Tests for list_ingested_documents tool."""

    @pytest.mark.asyncio
    async def test_list_documents(
        self, mcp_server: DocumentStoreMCP, sample_pdf: Path, tmp_path: Path
    ) -> None:
        """
        Verifies [document-processing:DocumentStoreMCP/TS-10]

        Given: Application with 2 docs
        When: Call list_ingested_documents
        Then: Returns list of 2 documents
        """
        from src.mcp_servers.document_store.server import (
            IngestDocumentInput,
            ListDocumentsInput,
        )

        # Create a second PDF
        pdf2_path = tmp_path / "second_doc.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Second document content", fontsize=12)
        doc.save(str(pdf2_path))
        doc.close()

        # Ingest both documents
        for pdf_path in [sample_pdf, pdf2_path]:
            ingest_input = IngestDocumentInput(
                file_path=str(pdf_path),
                application_ref="25/01178/REM",
            )
            await mcp_server._ingest_document(ingest_input)

        # List documents
        list_input = ListDocumentsInput(application_ref="25/01178/REM")
        result = await mcp_server._list_documents(list_input)

        assert result["status"] == "success"
        assert result["document_count"] == 2
        assert len(result["documents"]) == 2

        # Check document structure
        for doc_info in result["documents"]:
            assert "document_id" in doc_info
            assert "file_path" in doc_info
            assert "chunk_count" in doc_info

    @pytest.mark.asyncio
    async def test_list_documents_empty(
        self, mcp_server: DocumentStoreMCP
    ) -> None:
        """
        Verifies [document-processing:DocumentStoreMCP/TS-11]

        Given: Application with no docs
        When: Call list_ingested_documents
        Then: Returns empty list
        """
        from src.mcp_servers.document_store.server import ListDocumentsInput

        list_input = ListDocumentsInput(application_ref="25/99999/NONE")
        result = await mcp_server._list_documents(list_input)

        assert result["status"] == "success"
        assert result["document_count"] == 0
        assert result["documents"] == []


class TestServerInitialization:
    """Tests for server initialization."""

    def test_server_creates_tools(self) -> None:
        """
        Verifies [document-processing:DocumentStoreMCP/TS-12]

        Given: Server startup
        When: Start server
        Then: Registers all 4 tools
        """
        test_id = uuid.uuid4().hex[:8]
        server = IsolatedDocumentStoreMCP(test_id)

        # Server should be initialized
        assert server.server is not None
        assert server.server.name == "document-store-mcp"

    def test_lazy_initialization(self) -> None:
        """
        Given: Fresh server
        When: Access server
        Then: Components are lazily initialized
        """
        test_id = uuid.uuid4().hex[:8]
        server = IsolatedDocumentStoreMCP(test_id)

        # Before any operations, clients should not be initialized
        assert server._chroma_client is None
        assert server._processor is None
        assert server._chunker is None
        assert server._embedding_service is None

        # After getting clients, they should be initialized
        server._get_chroma_client()
        assert server._chroma_client is not None


class TestCallToolJsonSerialization:
    """Tests that the call_tool handler returns valid JSON strings."""

    @pytest.mark.asyncio
    async def test_ingest_success_returns_valid_json(
        self, mcp_server: DocumentStoreMCP, sample_pdf: Path
    ) -> None:
        """
        Given: A valid PDF is ingested via the call_tool handler
        When: call_tool returns the result
        Then: The TextContent text is valid JSON (not Python repr)
        """
        from mcp.types import CallToolRequest, CallToolRequestParams

        handler = mcp_server.server.request_handlers.get(CallToolRequest)
        assert handler is not None

        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="ingest_document",
                arguments={
                    "file_path": str(sample_pdf),
                    "application_ref": "25/99999/JSON",
                    "document_type": "transport_assessment",
                },
            ),
        )

        server_result = await handler(request)
        text = server_result.root.content[0].text

        # Must be valid JSON
        parsed = json.loads(text)
        assert parsed["status"] == "success"
        assert isinstance(parsed["chunks_created"], int)

    @pytest.mark.asyncio
    async def test_error_returns_valid_json(
        self, mcp_server: DocumentStoreMCP
    ) -> None:
        """
        Given: An invalid file path is provided
        When: call_tool returns an error result
        Then: The TextContent text is valid JSON
        """
        from mcp.types import CallToolRequest, CallToolRequestParams

        handler = mcp_server.server.request_handlers.get(CallToolRequest)
        assert handler is not None

        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="ingest_document",
                arguments={
                    "file_path": "/nonexistent/file.pdf",
                    "application_ref": "25/99999/JSON",
                },
            ),
        )

        server_result = await handler(request)
        text = server_result.root.content[0].text

        parsed = json.loads(text)
        assert parsed["status"] == "error"
