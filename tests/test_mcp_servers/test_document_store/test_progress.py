"""
Tests for progress reporting during document ingestion.

Implements test scenarios for [document-processing:FR-011]
"""

import pytest

from src.mcp_servers.document_store.progress import (
    IngestionProgress,
    ProgressReporter,
)


class TestIngestionProgress:
    """Tests for IngestionProgress dataclass."""

    def test_progress_initialization(self) -> None:
        """Test progress initializes with correct defaults."""
        progress = IngestionProgress(total_documents=10)

        assert progress.total_documents == 10
        assert progress.ingested_count == 0
        assert progress.failed_count == 0
        assert progress.current_document is None
        assert not progress.is_complete
        assert progress.errors == []

    def test_progress_percent_calculation(self) -> None:
        """Test progress percentage calculation."""
        progress = IngestionProgress(total_documents=10)

        assert progress.progress_percent == 0.0

        progress.ingested_count = 5
        assert progress.progress_percent == 50.0

        progress.ingested_count = 8
        progress.failed_count = 2
        assert progress.progress_percent == 100.0

    def test_progress_percent_empty(self) -> None:
        """Test progress percentage with zero documents."""
        progress = IngestionProgress(total_documents=0)
        assert progress.progress_percent == 100.0

    def test_is_complete(self) -> None:
        """Test completion detection."""
        progress = IngestionProgress(total_documents=5)

        assert not progress.is_complete

        progress.ingested_count = 3
        progress.failed_count = 2
        assert progress.is_complete

    def test_format_message_in_progress(self) -> None:
        """Test progress message formatting during ingestion."""
        progress = IngestionProgress(total_documents=10)
        progress.ingested_count = 4

        message = progress.format_message()

        assert "5 of 10" in message  # Next document is 5th

    def test_format_message_complete(self) -> None:
        """Test progress message formatting when complete."""
        progress = IngestionProgress(total_documents=10)
        progress.ingested_count = 8
        progress.failed_count = 2

        message = progress.format_message()

        assert "Ingested 8 of 10" in message
        assert "2 failed" in message

    def test_to_dict(self) -> None:
        """Test dictionary serialization."""
        progress = IngestionProgress(total_documents=10)
        progress.ingested_count = 5
        progress.failed_count = 1
        progress.current_document = "/path/to/doc.pdf"

        data = progress.to_dict()

        assert data["total_documents"] == 10
        assert data["ingested_count"] == 5
        assert data["failed_count"] == 1
        assert data["current_document"] == "/path/to/doc.pdf"
        assert data["progress_percent"] == 60.0
        assert data["is_complete"] is False
        assert "started_at" in data


class TestProgressReporter:
    """Tests for ProgressReporter class."""

    @pytest.mark.asyncio
    async def test_start_document(self) -> None:
        """Test starting document processing."""
        reporter = ProgressReporter(total_documents=5, application_ref="25/01178/REM")

        await reporter.start_document("/path/to/doc1.pdf")

        assert reporter.progress.current_document == "/path/to/doc1.pdf"

    @pytest.mark.asyncio
    async def test_complete_document_success(self) -> None:
        """Test successful document completion."""
        reporter = ProgressReporter(total_documents=5, application_ref="25/01178/REM")

        await reporter.start_document("/path/to/doc1.pdf")
        await reporter.complete_document("/path/to/doc1.pdf", success=True)

        assert reporter.progress.ingested_count == 1
        assert reporter.progress.failed_count == 0
        assert reporter.progress.current_document is None

    @pytest.mark.asyncio
    async def test_complete_document_failure(self) -> None:
        """Test failed document completion."""
        reporter = ProgressReporter(total_documents=5, application_ref="25/01178/REM")

        await reporter.start_document("/path/to/doc1.pdf")
        await reporter.complete_document(
            "/path/to/doc1.pdf", success=False, error="Extraction failed"
        )

        assert reporter.progress.ingested_count == 0
        assert reporter.progress.failed_count == 1
        assert len(reporter.progress.errors) == 1
        assert reporter.progress.errors[0]["error"] == "Extraction failed"

    @pytest.mark.asyncio
    async def test_callback_invoked(self) -> None:
        """Test that callbacks are invoked on progress updates."""
        callback_invocations = []

        async def mock_callback(progress: IngestionProgress) -> None:
            callback_invocations.append(progress.to_dict())

        reporter = ProgressReporter(
            total_documents=3,
            application_ref="25/01178/REM",
            callbacks=[mock_callback],
        )

        await reporter.start_document("/doc1.pdf")
        await reporter.complete_document("/doc1.pdf", success=True)
        await reporter.start_document("/doc2.pdf")

        # Should have 3 invocations: start, complete, start
        assert len(callback_invocations) == 3

    @pytest.mark.asyncio
    async def test_full_batch_progress(self) -> None:
        """
        Test full batch ingestion progress tracking.

        Verifies progress reporting format: "Ingested X of Y documents"
        """
        messages = []

        async def capture_message(progress: IngestionProgress) -> None:
            messages.append(progress.format_message())

        reporter = ProgressReporter(
            total_documents=3,
            application_ref="25/01178/REM",
            callbacks=[capture_message],
        )

        # Simulate batch ingestion
        for doc in ["/doc1.pdf", "/doc2.pdf", "/doc3.pdf"]:
            await reporter.start_document(doc)
            await reporter.complete_document(doc, success=True)

        # Final message should show all complete
        assert reporter.progress.is_complete
        assert "Ingested 3 of 3" in messages[-1]

    @pytest.mark.asyncio
    async def test_mixed_success_failure(self) -> None:
        """Test progress with mixed success and failure."""
        reporter = ProgressReporter(total_documents=4, application_ref="25/01178/REM")

        await reporter.start_document("/doc1.pdf")
        await reporter.complete_document("/doc1.pdf", success=True)

        await reporter.start_document("/doc2.pdf")
        await reporter.complete_document("/doc2.pdf", success=False, error="Corrupt PDF")

        await reporter.start_document("/doc3.pdf")
        await reporter.complete_document("/doc3.pdf", success=True)

        await reporter.start_document("/doc4.pdf")
        await reporter.complete_document("/doc4.pdf", success=True)

        assert reporter.progress.ingested_count == 3
        assert reporter.progress.failed_count == 1
        assert reporter.progress.is_complete
        assert "3 of 4" in reporter.progress.format_message()
        assert "1 failed" in reporter.progress.format_message()

    @pytest.mark.asyncio
    async def test_callback_error_doesnt_stop_progress(self) -> None:
        """Test that callback errors don't interrupt progress."""

        async def failing_callback(progress: IngestionProgress) -> None:  # noqa: ARG001
            raise RuntimeError("Callback error")

        reporter = ProgressReporter(
            total_documents=2,
            application_ref="25/01178/REM",
            callbacks=[failing_callback],
        )

        # Should not raise
        await reporter.start_document("/doc1.pdf")
        await reporter.complete_document("/doc1.pdf", success=True)

        assert reporter.progress.ingested_count == 1


class TestIngestionProgressEdgeCases:
    """Edge case tests for progress tracking."""

    def test_progress_with_one_document(self) -> None:
        """Test progress with single document."""
        progress = IngestionProgress(total_documents=1)
        progress.ingested_count = 1

        assert progress.is_complete
        assert progress.progress_percent == 100.0

    def test_all_documents_fail(self) -> None:
        """Test progress when all documents fail."""
        progress = IngestionProgress(total_documents=3)
        progress.failed_count = 3

        assert progress.is_complete
        assert progress.ingested_count == 0
        assert "0 of 3" in progress.format_message()
        assert "3 failed" in progress.format_message()
