"""
End-to-end smoke tests for document store.

Implements [document-processing:E2E-01] - Full workflow test
Implements [document-processing:E2E-03] - Multi-document application test

These tests require real documents and are marked for optional execution.
"""

import os
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


class E2ETestMCP(DocumentStoreMCP):
    """MCP server for E2E tests with mock embeddings."""

    def __init__(self, chroma_persist_dir=None):
        super().__init__(chroma_persist_dir=chroma_persist_dir, enable_ocr=False)
        self._mock_model = MockEmbeddingModel()

    def _get_embedding_service(self) -> EmbeddingService:
        if self._embedding_service is None:
            self._embedding_service = EmbeddingService(model=self._mock_model)
        return self._embedding_service


def create_realistic_transport_assessment(path: Path) -> None:
    """Create a realistic Transport Assessment PDF with multiple pages."""
    doc = fitz.open()

    pages_content = [
        """TRANSPORT ASSESSMENT

        Site: Land North of Example Road, Bicester
        Application Reference: 25/01234/REM

        Prepared by: Transport Consultants Ltd
        Date: January 2025

        1. INTRODUCTION

        This Transport Assessment has been prepared in support of the Reserved Matters
        application for the residential development of Land North of Example Road, Bicester.
        The development comprises 150 dwellings including affordable housing provision.
        """,
        """2. TRIP GENERATION

        The proposed development is expected to generate the following vehicle trips:

        - AM Peak (08:00-09:00): 45 arrivals, 82 departures
        - PM Peak (17:00-18:00): 78 arrivals, 52 departures
        - Daily total: 520 two-way vehicle movements

        Trip rates have been derived from TRICS database analysis of comparable
        residential developments in similar locations.

        Multi-modal trip generation:
        - Walking: 15% of all trips
        - Cycling: 8% of all trips
        - Public transport: 12% of all trips
        - Private car: 65% of all trips
        """,
        """3. CYCLE PARKING PROVISION

        In accordance with the Cherwell Local Plan policy and Oxfordshire County Council
        guidance, the following cycle parking will be provided:

        - 48 Sheffield cycle stands near the main entrance
        - Covered cycle storage for 96 bicycles
        - Secure residential cycle parking: 2 spaces per dwelling (300 total)

        The cycle parking exceeds the minimum policy requirements and is designed
        to encourage sustainable travel modes. Sheffield stands are positioned
        near key desire lines and building entrances.

        Cycle routes will connect to the existing National Cycle Network Route 51
        via a new 3-metre wide shared-use path along the site's southern boundary.
        """,
        """4. PEDESTRIAN ACCESS AND SAFETY

        The development provides comprehensive pedestrian infrastructure:

        - 2-metre wide footways along all internal roads
        - Dropped kerbs with tactile paving at all crossing points
        - Direct pedestrian routes to bus stops on Example Road
        - New pedestrian crossing on Example Road (signalised)

        Pedestrian desire lines have been considered in the masterplan design,
        ensuring direct and safe routes to key destinations including the local
        primary school, bus stops, and town centre.

        5. CONCLUSIONS

        The transport impacts of the development are acceptable. Adequate parking,
        cycle provision, and pedestrian facilities are proposed. The development
        promotes sustainable travel in accordance with local and national policy.
        """,
    ]

    for content in pages_content:
        page = doc.new_page()
        page.insert_text((50, 50), content, fontsize=10)

    doc.save(str(path))
    doc.close()


def create_design_access_statement(path: Path) -> None:
    """Create a Design and Access Statement PDF."""
    doc = fitz.open()

    content = """DESIGN AND ACCESS STATEMENT

    Site: Land North of Example Road, Bicester
    Application Reference: 25/01234/REM

    1. DESIGN PRINCIPLES

    The development has been designed to complement the established
    character of the surrounding area while providing high-quality
    homes for future residents.

    Key design principles:
    - Perimeter block layout with active frontages
    - Varied building heights (2-3 storeys)
    - Traditional materials palette
    - Generous amenity spaces

    2. ACCESS

    Vehicle access is taken from Example Road via a new priority junction.
    The internal road layout follows Manual for Streets guidance.

    3. SUSTAINABILITY

    All dwellings will achieve EPC rating A. Air source heat pumps
    and solar PV panels are provided throughout.
    """

    page = doc.new_page()
    page.insert_text((50, 50), content, fontsize=10)
    doc.save(str(path))
    doc.close()


@pytest.fixture
def e2e_server(tmp_path: Path) -> E2ETestMCP:
    """Create an E2E test server with fresh ChromaDB."""
    chroma_dir = tmp_path / f"chroma_{uuid.uuid4().hex[:8]}"
    return E2ETestMCP(chroma_persist_dir=str(chroma_dir))


@pytest.fixture
def realistic_documents(tmp_path: Path) -> dict[str, Path]:
    """Create realistic test documents."""
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()

    transport_assessment = doc_dir / "Transport_Assessment_Jan2025.pdf"
    create_realistic_transport_assessment(transport_assessment)

    design_statement = doc_dir / "Design_Access_Statement.pdf"
    create_design_access_statement(design_statement)

    return {
        "transport_assessment": transport_assessment,
        "design_statement": design_statement,
    }


@pytest.mark.e2e
class TestFullWorkflow:
    """
    End-to-end tests for the complete document workflow.

    Verifies [document-processing:E2E-01] - Full workflow test
    """

    @pytest.mark.asyncio
    async def test_ingest_search_retrieve_workflow(
        self, e2e_server: E2ETestMCP, realistic_documents: dict[str, Path]
    ) -> None:
        """
        Verifies [document-processing:E2E-01]

        Given: Realistic planning application documents
        When: Ingest, search, and retrieve documents
        Then: Full workflow completes successfully
        """
        application_ref = "25/01234/REM"
        transport_assessment = realistic_documents["transport_assessment"]

        # Step 1: Ingest document
        ingest_result = await e2e_server._ingest_document(
            IngestDocumentInput(
                file_path=str(transport_assessment),
                application_ref=application_ref,
            )
        )

        assert ingest_result["status"] == "success"
        document_id = ingest_result["document_id"]
        assert ingest_result["chunks_created"] > 0

        # Step 2: Search for cycle parking information
        search_result = await e2e_server._search_documents(
            SearchInput(
                query="cycle parking Sheffield stands",
                application_ref=application_ref,
                max_results=5,
            )
        )

        assert search_result["status"] == "success"
        assert search_result["results_count"] > 0

        # Verify relevant content found
        search_texts = [r["text"] for r in search_result["results"]]
        combined_text = " ".join(search_texts).lower()
        assert "sheffield" in combined_text or "cycle" in combined_text

        # Step 3: Retrieve full document text
        text_result = await e2e_server._get_document_text(
            GetDocumentTextInput(document_id=document_id)
        )

        assert text_result["status"] == "success"
        assert "Transport Assessment" in text_result["text"]
        assert "cycle parking" in text_result["text"].lower()

        # Step 4: List documents
        list_result = await e2e_server._list_documents(
            ListDocumentsInput(application_ref=application_ref)
        )

        assert list_result["status"] == "success"
        assert list_result["document_count"] == 1
        assert list_result["documents"][0]["document_type"] == "transport_assessment"


@pytest.mark.e2e
class TestMultiDocumentApplication:
    """
    End-to-end tests for applications with multiple documents.

    Verifies [document-processing:E2E-03] - Multi-document application test
    """

    @pytest.mark.asyncio
    async def test_multi_document_ingestion_and_search(
        self, e2e_server: E2ETestMCP, realistic_documents: dict[str, Path]
    ) -> None:
        """
        Verifies [document-processing:E2E-03]

        Given: Multiple documents for same application
        When: Ingest all and search across them
        Then: Search returns results from appropriate documents
        """
        application_ref = "25/01234/REM"

        # Ingest both documents
        for doc_type, doc_path in realistic_documents.items():
            result = await e2e_server._ingest_document(
                IngestDocumentInput(
                    file_path=str(doc_path),
                    application_ref=application_ref,
                )
            )
            assert result["status"] == "success", f"Failed to ingest {doc_type}"

        # List all documents
        list_result = await e2e_server._list_documents(
            ListDocumentsInput(application_ref=application_ref)
        )

        assert list_result["document_count"] == 2

        # Search for transport-specific content
        transport_search = await e2e_server._search_documents(
            SearchInput(
                query="trip generation vehicle movements AM peak",
                application_ref=application_ref,
                max_results=5,
            )
        )

        assert transport_search["status"] == "success"
        assert transport_search["results_count"] > 0

        # Search for design-specific content
        design_search = await e2e_server._search_documents(
            SearchInput(
                query="perimeter block layout building heights",
                application_ref=application_ref,
                max_results=5,
            )
        )

        assert design_search["status"] == "success"

    @pytest.mark.asyncio
    async def test_document_classification_accuracy(
        self, e2e_server: E2ETestMCP, realistic_documents: dict[str, Path]
    ) -> None:
        """Test that documents are classified correctly based on content."""
        application_ref = "25/01234/REM"

        # Ingest transport assessment
        ta_result = await e2e_server._ingest_document(
            IngestDocumentInput(
                file_path=str(realistic_documents["transport_assessment"]),
                application_ref=application_ref,
            )
        )
        assert ta_result["status"] == "success"

        # Ingest design statement
        das_result = await e2e_server._ingest_document(
            IngestDocumentInput(
                file_path=str(realistic_documents["design_statement"]),
                application_ref=application_ref,
            )
        )
        assert das_result["status"] == "success"

        # Verify classification
        list_result = await e2e_server._list_documents(
            ListDocumentsInput(application_ref=application_ref)
        )

        doc_types = {d["document_type"] for d in list_result["documents"]}
        assert "transport_assessment" in doc_types
        assert "design_access_statement" in doc_types


@pytest.mark.e2e
class TestRealDocumentsOptional:
    """
    Optional tests that can run against real Cherwell documents.

    These tests are skipped unless REAL_DOCS_DIR environment variable is set
    to a directory containing actual planning documents.
    """

    @pytest.fixture
    def real_docs_dir(self) -> Path | None:
        """Get real documents directory from environment."""
        real_dir = os.getenv("REAL_DOCS_DIR")
        if real_dir and Path(real_dir).exists():
            return Path(real_dir)
        return None

    @pytest.mark.asyncio
    async def test_ingest_real_documents(
        self, e2e_server: E2ETestMCP, real_docs_dir: Path | None
    ) -> None:
        """Test ingestion of real planning documents if available."""
        if real_docs_dir is None:
            pytest.skip("REAL_DOCS_DIR not set - skipping real document test")

        pdf_files = list(real_docs_dir.glob("*.pdf"))[:5]  # Limit to 5 files
        if not pdf_files:
            pytest.skip("No PDF files found in REAL_DOCS_DIR")

        application_ref = "test/real/docs"

        for pdf_path in pdf_files:
            result = await e2e_server._ingest_document(
                IngestDocumentInput(
                    file_path=str(pdf_path),
                    application_ref=application_ref,
                )
            )

            # Real documents should either ingest or have a known error
            assert result["status"] in ["success", "already_ingested", "error"]
            if result["status"] == "error":
                # Acceptable errors for real documents
                assert result["error_type"] in [
                    "no_content",
                    "extraction_failed",
                ]

        # List what was ingested
        list_result = await e2e_server._list_documents(
            ListDocumentsInput(application_ref=application_ref)
        )
        print(f"\nIngested {list_result['document_count']} real documents")
        for doc in list_result["documents"]:
            print(f"  - {doc['document_type']}: {doc['file_path']}")
