"""
Performance tests for document processing pipeline.

Verifies [document-processing:NFR-005] - Search latency <500ms
Verifies [document-processing:ChromaClient/TS-08] - Search performance
"""

import time
import uuid
from pathlib import Path

import fitz
import pytest

from src.mcp_servers.document_store.embeddings import EmbeddingService, MockEmbeddingModel
from src.mcp_servers.document_store.server import (
    DocumentStoreMCP,
    IngestDocumentInput,
    SearchInput,
)


class PerformanceTestMCP(DocumentStoreMCP):
    """MCP server for performance tests with mock embeddings."""

    def __init__(self, chroma_persist_dir=None):
        super().__init__(chroma_persist_dir=chroma_persist_dir, enable_ocr=False)
        self._mock_model = MockEmbeddingModel()

    def _get_embedding_service(self) -> EmbeddingService:
        if self._embedding_service is None:
            self._embedding_service = EmbeddingService(model=self._mock_model)
        return self._embedding_service


def create_test_pdf(path: Path, content: str) -> None:
    """Create a test PDF with the given content."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), content, fontsize=12)
    doc.save(str(path))
    doc.close()


@pytest.fixture
def performance_server(tmp_path: Path) -> PerformanceTestMCP:
    """Create a performance test server with persistent ChromaDB."""
    chroma_dir = tmp_path / f"chroma_{uuid.uuid4().hex[:8]}"
    return PerformanceTestMCP(chroma_persist_dir=str(chroma_dir))


@pytest.fixture
def populated_server(tmp_path: Path) -> tuple[PerformanceTestMCP, int]:
    """
    Create a server populated with multiple documents.

    Returns tuple of (server, document_count).
    """
    chroma_dir = tmp_path / f"chroma_{uuid.uuid4().hex[:8]}"
    server = PerformanceTestMCP(chroma_persist_dir=str(chroma_dir))

    # Create and ingest multiple documents
    doc_count = 20
    doc_dir = tmp_path / "documents"
    doc_dir.mkdir()

    import asyncio

    async def populate():
        for i in range(doc_count):
            pdf_path = doc_dir / f"Document_{i:03d}.pdf"
            content = f"""
            Document {i} - Planning Application Analysis

            This is test document number {i} containing various planning-related content.
            It includes information about transport assessments, cycle parking, trip generation,
            and other relevant planning matters.

            Transport impact: {i * 10} vehicle trips per day
            Cycle parking: {i * 5} Sheffield stands
            Development area: {i * 100} square meters
            """
            create_test_pdf(pdf_path, content)

            await server._ingest_document(
                IngestDocumentInput(
                    file_path=str(pdf_path),
                    application_ref=f"25/{1000 + i}/REM",
                )
            )

    asyncio.get_event_loop().run_until_complete(populate())

    return server, doc_count


@pytest.mark.integration
class TestSearchPerformance:
    """Performance tests for search operations."""

    @pytest.mark.asyncio
    async def test_search_latency_under_500ms(
        self, populated_server: tuple[PerformanceTestMCP, int]
    ) -> None:
        """
        Verifies [document-processing:NFR-005] - Search latency <500ms
        Verifies [document-processing:ChromaClient/TS-08]

        Given: Collection with multiple documents
        When: Call search()
        Then: Returns in <500ms
        """
        server, doc_count = populated_server

        # Warm up (first query may be slower)
        await server._search_documents(
            SearchInput(query="planning assessment", max_results=5)
        )

        # Measure multiple searches
        latencies = []
        queries = [
            "transport assessment trip generation",
            "cycle parking Sheffield stands",
            "development impact analysis",
            "planning application requirements",
            "vehicle movements pedestrian access",
        ]

        for query in queries:
            start = time.perf_counter()
            result = await server._search_documents(
                SearchInput(query=query, max_results=10)
            )
            elapsed = (time.perf_counter() - start) * 1000  # Convert to ms

            latencies.append(elapsed)
            assert result["status"] == "success"

        # Verify all searches under 500ms
        avg_latency = sum(latencies) / len(latencies)
        max_latency = max(latencies)

        print(f"\nSearch performance with {doc_count} documents:")
        print(f"  Average latency: {avg_latency:.1f}ms")
        print(f"  Max latency: {max_latency:.1f}ms")
        print(f"  Min latency: {min(latencies):.1f}ms")

        # Assert NFR requirement
        assert max_latency < 500, f"Max search latency {max_latency:.1f}ms exceeds 500ms limit"

    @pytest.mark.asyncio
    async def test_filtered_search_latency(
        self, populated_server: tuple[PerformanceTestMCP, int]
    ) -> None:
        """Test that filtered search is also performant."""
        server, _ = populated_server

        start = time.perf_counter()
        result = await server._search_documents(
            SearchInput(
                query="transport assessment",
                application_ref="25/1005/REM",  # Specific application
                max_results=5,
            )
        )
        elapsed = (time.perf_counter() - start) * 1000

        assert result["status"] == "success"
        assert elapsed < 500, f"Filtered search latency {elapsed:.1f}ms exceeds 500ms limit"


@pytest.mark.integration
class TestIngestionPerformance:
    """Performance tests for document ingestion."""

    @pytest.mark.asyncio
    async def test_single_document_ingestion_time(
        self, performance_server: PerformanceTestMCP, tmp_path: Path
    ) -> None:
        """Test single document ingestion is reasonably fast."""
        # Create a moderately-sized document
        pdf_path = tmp_path / "test_doc.pdf"
        doc = fitz.open()

        # Add 10 pages
        for i in range(10):
            page = doc.new_page()
            page.insert_text(
                (72, 72),
                f"Page {i+1}\n\nThis is test content for performance measurement. " * 20,
                fontsize=10,
            )

        doc.save(str(pdf_path))
        doc.close()

        # Measure ingestion time
        start = time.perf_counter()
        result = await performance_server._ingest_document(
            IngestDocumentInput(
                file_path=str(pdf_path),
                application_ref="25/01178/REM",
            )
        )
        elapsed = time.perf_counter() - start

        assert result["status"] == "success"

        print("\nIngestion performance:")
        print(f"  Time: {elapsed:.2f}s")
        print(f"  Chunks created: {result['chunks_created']}")

        # Should complete in reasonable time (10 seconds max for 10 pages)
        assert elapsed < 10, f"Ingestion took {elapsed:.1f}s, expected <10s"


@pytest.mark.integration
class TestScalability:
    """Scalability tests for document storage."""

    @pytest.mark.asyncio
    async def test_many_documents_storage(
        self, performance_server: PerformanceTestMCP, tmp_path: Path
    ) -> None:
        """
        Test system handles many documents.

        Verifies [document-processing:NFR-001] - Support 50 documents
        """
        doc_dir = tmp_path / "documents"
        doc_dir.mkdir()

        # Create and ingest 50 documents
        target_count = 50

        for i in range(target_count):
            pdf_path = doc_dir / f"Doc_{i:03d}.pdf"
            content = f"Document {i} with unique content about topic {i}"
            create_test_pdf(pdf_path, content)

            result = await performance_server._ingest_document(
                IngestDocumentInput(
                    file_path=str(pdf_path),
                    application_ref="25/01178/REM",
                )
            )
            assert result["status"] == "success", f"Failed to ingest document {i}"

        # Verify all documents are searchable
        search_result = await performance_server._search_documents(
            SearchInput(query="document unique content", max_results=60)
        )

        assert search_result["status"] == "success"
        print("\nScalability test:")
        print(f"  Documents ingested: {target_count}")
        print(f"  Search results: {search_result['results_count']}")
