"""
Tests for AgentOrchestrator.

Implements test scenarios from [agent-integration:AgentOrchestrator/TS-01] through [TS-08]
"""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.mcp_client import MCPClientManager, MCPConnectionError, MCPServerType, MCPToolError
from src.agent.orchestrator import (
    AgentOrchestrator,
    ApplicationMetadata,
    DocumentIngestionResult,
    OrchestratorError,
    ReviewResult,
)
from src.agent.progress import ReviewPhase


@pytest.fixture(autouse=True)
def _sequential_ingestion(monkeypatch):
    """Force sequential ingestion in tests so side_effect lists are consumed in order."""
    monkeypatch.setenv("INGEST_CONCURRENCY", "1")


@pytest.fixture
def mock_mcp_client():
    """Create a mock MCPClientManager."""
    client = AsyncMock(spec=MCPClientManager)
    client.initialize = AsyncMock()
    client.close = AsyncMock()
    client.call_tool = AsyncMock()
    client.is_connected = MagicMock(return_value=True)
    return client


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    redis = AsyncMock()
    redis.set = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.delete = AsyncMock()
    redis.publish = AsyncMock()
    redis.exists = AsyncMock(return_value=False)
    return redis


@pytest.fixture
def sample_application_response():
    """Sample response from get_application_details tool."""
    return {
        "status": "success",
        "application": {
            "reference": "25/01178/REM",
            "address": "Land at Test Site, Bicester",
            "proposal": "Reserved matters application for residential development",
            "applicant": "Test Developments Ltd",
            "status": "Under consideration",
            "date_validated": "2025-01-20",
            "consultation_end": "2025-02-15",
            "documents": [
                {"id": "doc1", "name": "Transport Assessment.pdf", "url": "https://..."},
                {"id": "doc2", "name": "Site Plan.pdf", "url": "https://..."},
                {"id": "doc3", "name": "Design Statement.pdf", "url": "https://..."},
            ],
        },
    }


@pytest.fixture
def sample_download_response():
    """Sample response from download_all_documents tool."""
    return {
        "status": "success",
        "downloaded": [
            {"id": "doc1", "path": "/data/raw/25_01178_REM/transport_assessment.pdf"},
            {"id": "doc2", "path": "/data/raw/25_01178_REM/site_plan.pdf"},
            {"id": "doc3", "path": "/data/raw/25_01178_REM/design_statement.pdf"},
        ],
        "failed": [],
    }


@pytest.fixture
def sample_ingest_response():
    """Sample response from ingest_document tool."""
    return {
        "status": "success",
        "document_id": "doc_test123",
        "chunks_created": 15,
    }


class TestSuccessfulMCPConnections:
    """
    Tests for successful MCP server connections.

    Implements [agent-integration:AgentOrchestrator/TS-01] - Successful MCP connections
    """

    @pytest.mark.asyncio
    async def test_successful_mcp_connections(self, mock_mcp_client, mock_redis):
        """
        Verifies [agent-integration:AgentOrchestrator/TS-01] - Successful MCP connections

        Given: All 3 MCP servers running
        When: Orchestrator initialises
        Then: Connections established to cherwell-scraper, document-store, policy-kb
        """
        orchestrator = AgentOrchestrator(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )

        await orchestrator.initialize()

        mock_mcp_client.initialize.assert_called_once()
        assert orchestrator._initialized is True

        await orchestrator.close()


class TestCompleteWorkflowExecution:
    """
    Tests for complete workflow execution.

    Implements [agent-integration:AgentOrchestrator/TS-03] - Complete workflow execution
    """

    @pytest.mark.asyncio
    async def test_complete_workflow_execution(
        self,
        mock_mcp_client,
        mock_redis,
        sample_application_response,
        sample_download_response,
        sample_ingest_response,
    ):
        """
        Verifies [agent-integration:AgentOrchestrator/TS-03] - Complete workflow execution

        Given: Valid review job
        When: Orchestrator executes workflow
        Then: All 5 phases complete in order; review result produced
        """
        # Set up mock responses for each tool call
        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,  # get_application_details
            sample_download_response,  # download_all_documents
            sample_ingest_response,  # ingest_document (doc 1)
            sample_ingest_response,  # ingest_document (doc 2)
            sample_ingest_response,  # ingest_document (doc 3)
        ]

        orchestrator = AgentOrchestrator(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )

        result = await orchestrator.run()

        assert result.success is True
        assert result.error is None
        assert result.application is not None
        assert result.application.reference == "25/01178/REM"

        # Verify all phases were executed
        completed_phases = orchestrator.progress.state.completed_phases
        assert "fetching_metadata" in completed_phases
        assert "downloading_documents" in completed_phases
        assert "ingesting_documents" in completed_phases
        assert "analysing_application" in completed_phases
        assert "generating_review" in completed_phases

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_workflow_with_no_documents(self, mock_mcp_client, mock_redis):
        """Test workflow when application has no documents."""
        mock_mcp_client.call_tool.side_effect = [
            {
                "status": "success",
                "application": {
                    "reference": "25/00001/FUL",
                    "address": "Test Address",
                    "documents": [],
                },
            },
        ]

        orchestrator = AgentOrchestrator(
            review_id="rev_test123",
            application_ref="25/00001/FUL",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )

        result = await orchestrator.run()

        # Should still succeed, just with no documents
        assert result.success is True
        assert result.application is not None

        await orchestrator.close()


class TestScraperFailureHandling:
    """
    Tests for scraper failure handling.

    Implements [agent-integration:AgentOrchestrator/TS-04] - Scraper failure handling
    """

    @pytest.mark.asyncio
    async def test_scraper_failure_handling(self, mock_mcp_client, mock_redis):
        """
        Verifies [agent-integration:AgentOrchestrator/TS-04] - Scraper failure handling

        Given: Cherwell scraper returns error
        When: Fetching metadata phase
        Then: Workflow fails gracefully; error details captured; `review.failed` published
        """
        mock_mcp_client.call_tool.side_effect = MCPToolError(
            "get_application_details",
            "Application not found",
        )

        orchestrator = AgentOrchestrator(
            review_id="rev_test123",
            application_ref="INVALID/REF",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )

        result = await orchestrator.run()

        assert result.success is False
        assert "Scraper error" in result.error

        # Verify review.failed was published
        failed_calls = [
            c for c in mock_redis.publish.call_args_list
            if "review.failed" in str(c)
        ]
        assert len(failed_calls) >= 1

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_scraper_returns_error_status(self, mock_mcp_client, mock_redis):
        """Test handling when scraper returns error in response."""
        mock_mcp_client.call_tool.return_value = {
            "status": "error",
            "message": "Failed to access Cherwell portal",
        }

        orchestrator = AgentOrchestrator(
            review_id="rev_test123",
            application_ref="25/00001/FUL",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )

        result = await orchestrator.run()

        assert result.success is False
        assert "Failed to access" in result.error

        await orchestrator.close()


class TestPartialDocumentIngestion:
    """
    Tests for partial document ingestion handling.

    Implements [agent-integration:AgentOrchestrator/TS-05] - Partial document ingestion
    """

    @pytest.mark.asyncio
    async def test_partial_document_ingestion(
        self,
        mock_mcp_client,
        mock_redis,
        sample_application_response,
        sample_download_response,
        sample_ingest_response,
    ):
        """
        Verifies [agent-integration:AgentOrchestrator/TS-05] - Partial document ingestion

        Given: 2 of 3 documents fail ingestion
        When: Ingesting documents phase
        Then: Workflow continues; failed documents logged; review produced with available docs
        """
        # First doc succeeds, next two fail
        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,
            sample_download_response,
            sample_ingest_response,  # doc 1 succeeds
            MCPToolError("ingest_document", "OCR failed - corrupt file"),  # doc 2 fails
            MCPToolError("ingest_document", "Unsupported format"),  # doc 3 fails
        ]

        orchestrator = AgentOrchestrator(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )

        result = await orchestrator.run()

        # Should still succeed with partial documents
        assert result.success is True

        # Errors should be recorded
        errors = orchestrator.progress.state.errors_encountered
        assert len(errors) == 2
        assert any("OCR failed" in e.get("error", "") for e in errors)

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_all_documents_fail_ingestion(
        self,
        mock_mcp_client,
        mock_redis,
        sample_application_response,
        sample_download_response,
    ):
        """Test that workflow fails if all documents fail to ingest."""
        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,
            sample_download_response,
            MCPToolError("ingest_document", "Failed"),
            MCPToolError("ingest_document", "Failed"),
            MCPToolError("ingest_document", "Failed"),
        ]

        orchestrator = AgentOrchestrator(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )

        result = await orchestrator.run()

        assert result.success is False
        assert "No documents could be ingested" in result.error

        await orchestrator.close()


class TestCancellationHandling:
    """
    Tests for cancellation handling.

    Implements [agent-integration:AgentOrchestrator/TS-06] - Cancellation handling
    """

    @pytest.mark.asyncio
    async def test_cancellation_handling(
        self,
        mock_mcp_client,
        mock_redis,
        sample_application_response,
    ):
        """
        Verifies [agent-integration:AgentOrchestrator/TS-06] - Cancellation handling

        Given: Cancellation flag set
        When: Between phases
        Then: Workflow stops; status set to "cancelled"
        """
        mock_mcp_client.call_tool.return_value = sample_application_response

        # Cancel after first phase
        call_count = 0

        async def mock_exists(key):
            nonlocal call_count
            call_count += 1
            # Cancel after first phase completes
            return call_count > 1

        mock_redis.exists.side_effect = mock_exists

        orchestrator = AgentOrchestrator(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )

        result = await orchestrator.run()

        assert result.error == "Workflow cancelled"
        assert orchestrator.progress.is_cancelled is True

        await orchestrator.close()


class TestStatePersistence:
    """
    Tests for state persistence and recovery.

    Implements [agent-integration:AgentOrchestrator/TS-07] - State persistence
    """

    @pytest.mark.asyncio
    async def test_state_persistence(
        self,
        mock_mcp_client,
        mock_redis,
        sample_application_response,
        sample_download_response,
        sample_ingest_response,
    ):
        """
        Verifies [agent-integration:AgentOrchestrator/TS-07] - State persistence

        Given: Workflow in phase 3
        When: Worker restarts
        Then: Orchestrator resumes from phase 3 (idempotent)
        """
        # Simulate saved state from previous run
        saved_state = {
            "review_id": "rev_test123",
            "application_ref": "25/01178/REM",
            "current_phase": "ingesting_documents",
            "completed_phases": ["fetching_metadata", "downloading_documents"],
            "phase_info": {},
            "documents_processed": 0,
            "documents_total": 3,
            "errors_encountered": [],
            "started_at": "2025-02-05T14:30:00+00:00",
            "cancelled": False,
        }
        mock_redis.get.return_value = json.dumps(saved_state)

        mock_mcp_client.call_tool.side_effect = [
            sample_ingest_response,  # Start from ingesting
            sample_ingest_response,
            sample_ingest_response,
        ]

        orchestrator = AgentOrchestrator(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )

        # Initialize but pretend we have application data
        orchestrator._application = ApplicationMetadata(
            reference="25/01178/REM",
            documents=[],
        )
        orchestrator._ingestion_result = DocumentIngestionResult(
            documents_fetched=3,
            document_paths=[
                "/data/raw/doc1.pdf",
                "/data/raw/doc2.pdf",
                "/data/raw/doc3.pdf",
            ],
        )

        result = await orchestrator.run()

        # Should not have called get_application_details or download
        # (those phases were completed in previous run)
        tool_calls = [c[0][0] for c in mock_mcp_client.call_tool.call_args_list]
        assert "get_application_details" not in tool_calls
        assert "download_all_documents" not in tool_calls

        await orchestrator.close()


class TestAllServersUnavailable:
    """
    Tests for all servers unavailable scenario.

    Implements [agent-integration:AgentOrchestrator/TS-08] - All servers unavailable
    """

    @pytest.mark.asyncio
    async def test_all_servers_unavailable(self, mock_redis):
        """
        Verifies [agent-integration:AgentOrchestrator/TS-08] - All servers unavailable

        Given: All MCP servers down
        When: Orchestrator initialises
        Then: Fails with clear error after retry exhaustion
        """
        mock_mcp_client = AsyncMock(spec=MCPClientManager)
        mock_mcp_client.initialize.side_effect = MCPConnectionError(
            MCPServerType.CHERWELL_SCRAPER,
            "All MCP servers are unavailable",
        )

        orchestrator = AgentOrchestrator(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )

        with pytest.raises(OrchestratorError) as exc_info:
            await orchestrator.initialize()

        assert "Failed to connect" in str(exc_info.value)

        await orchestrator.close()


class TestReconnectionOnTransientFailure:
    """
    Tests for reconnection on transient failures.

    Implements [agent-integration:AgentOrchestrator/TS-02] - Reconnection on transient failure
    """

    @pytest.mark.asyncio
    async def test_reconnection_error_recorded(
        self,
        mock_mcp_client,
        mock_redis,
        sample_application_response,
        sample_download_response,
        sample_ingest_response,
    ):
        """
        Verifies [agent-integration:AgentOrchestrator/TS-02] - Reconnection on transient failure

        Given: MCP connection error occurs mid-workflow (recoverable)
        When: Connection error detected
        Then: Error is logged as recoverable; workflow continues with degraded functionality
        """
        # First call fails with connection error (recoverable), subsequent calls succeed
        # The orchestrator treats MCPConnectionError as recoverable
        mock_mcp_client.call_tool.side_effect = [
            MCPConnectionError(MCPServerType.CHERWELL_SCRAPER, "Connection lost"),  # Phase 1 fails
            sample_download_response,  # Phase 2 succeeds (but no app data)
            sample_ingest_response,  # Phase 3 succeeds
        ]

        orchestrator = AgentOrchestrator(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )

        # Manually set application data since phase 1 fails
        orchestrator._application = ApplicationMetadata(
            reference="25/01178/REM",
            documents=[{"id": "doc1", "name": "test.pdf"}],
        )

        result = await orchestrator.run()

        # The error should be recorded
        errors = orchestrator.progress.state.errors_encountered
        assert any("Connection lost" in e.get("error", "") for e in errors)

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_non_recoverable_connection_error(
        self,
        mock_mcp_client,
        mock_redis,
    ):
        """Test that non-recoverable errors fail the workflow."""
        # Scraper error is not recoverable
        mock_mcp_client.call_tool.side_effect = MCPToolError(
            "get_application_details",
            "Portal unavailable",
        )

        orchestrator = AgentOrchestrator(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )

        result = await orchestrator.run()

        assert result.success is False
        assert "Scraper error" in result.error

        await orchestrator.close()


class TestContextManager:
    """Tests for context manager usage."""

    @pytest.mark.asyncio
    async def test_context_manager(self, mock_mcp_client, mock_redis, sample_application_response):
        """Test orchestrator as context manager."""
        mock_mcp_client.call_tool.return_value = sample_application_response

        async with AgentOrchestrator(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        ) as orchestrator:
            assert orchestrator._initialized is True

        # Close should have been called
        mock_mcp_client.close.assert_not_called()  # We provided the client, so we don't own it


class TestOrchestratorError:
    """Tests for OrchestratorError."""

    def test_orchestrator_error_with_phase(self):
        """Test OrchestratorError includes phase information."""
        error = OrchestratorError(
            "Test error",
            phase=ReviewPhase.INGESTING_DOCUMENTS,
            recoverable=True,
        )

        assert error.message == "Test error"
        assert error.phase == ReviewPhase.INGESTING_DOCUMENTS
        assert error.recoverable is True
        assert str(error) == "Test error"


class TestGracefulDegradation:
    """
    Additional tests for graceful degradation.

    Extends [agent-integration:AgentOrchestrator/TS-05] - Partial document ingestion
    """

    @pytest.mark.asyncio
    async def test_download_partial_failure(
        self,
        mock_mcp_client,
        mock_redis,
        sample_application_response,
        sample_ingest_response,
    ):
        """Test workflow continues with partial download failures."""
        # Download returns some failures
        download_response = {
            "status": "success",
            "downloaded": [
                {"id": "doc1", "path": "/data/raw/doc1.pdf"},
            ],
            "failed": [
                {"id": "doc2", "error": "Timeout"},
                {"id": "doc3", "error": "404 Not Found"},
            ],
        }

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,
            download_response,
            sample_ingest_response,
        ]

        orchestrator = AgentOrchestrator(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )

        result = await orchestrator.run()

        # Should succeed with partial downloads
        assert result.success is True
        # Only 1 document was downloaded and ingested
        assert orchestrator._ingestion_result.documents_fetched == 1

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_ingest_returns_error_status(
        self,
        mock_mcp_client,
        mock_redis,
        sample_application_response,
        sample_download_response,
    ):
        """Test handling of ingest_document returning error status."""
        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,
            sample_download_response,
            {"status": "error", "message": "Corrupt PDF"},  # doc 1 fails
            {"status": "success", "chunks_created": 10},  # doc 2 succeeds
            {"status": "success", "chunks_created": 5},  # doc 3 succeeds
        ]

        orchestrator = AgentOrchestrator(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )

        result = await orchestrator.run()

        # Should succeed with 2 of 3 documents
        assert result.success is True
        assert orchestrator._ingestion_result.documents_ingested == 2

        # Error should be recorded
        errors = orchestrator.progress.state.errors_encountered
        assert len(errors) == 1
        assert "Corrupt PDF" in errors[0]["error"]

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_already_ingested_counts_as_success(
        self,
        mock_mcp_client,
        mock_redis,
        sample_application_response,
        sample_download_response,
    ):
        """Test that already_ingested status is counted as success."""
        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,
            sample_download_response,
            {"status": "already_ingested", "document_id": "doc_123"},
            {"status": "already_ingested", "document_id": "doc_456"},
            {"status": "already_ingested", "document_id": "doc_789"},
        ]

        orchestrator = AgentOrchestrator(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )

        result = await orchestrator.run()

        assert result.success is True
        assert orchestrator._ingestion_result.documents_ingested == 3

        await orchestrator.close()
