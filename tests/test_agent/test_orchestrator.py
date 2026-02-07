"""
Tests for AgentOrchestrator.

Implements test scenarios from [agent-integration:AgentOrchestrator/TS-01] through [TS-08]
Implements test scenarios from [key-documents:DocumentIngestionResult/TS-01] through [TS-02]
Implements test scenarios from [key-documents:AgentOrchestrator._phase_download_documents/TS-01] through [TS-02]
Implements test scenarios from [key-documents:AgentOrchestrator._phase_generate_review/TS-01] through [TS-04]
Implements test scenarios from [key-documents:ITS-01] through [ITS-02]
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
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


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

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
    """Sample response from download_all_documents tool (matches actual scraper output)."""
    return {
        "status": "success",
        "downloads": [
            {
                "document_id": "doc1",
                "file_path": "/data/raw/25_01178_REM/001_Transport Assessment.pdf",
                "file_size": 150000,
                "success": True,
                "description": "Transport Assessment",
                "document_type": "Transport Assessment",
                "url": "https://planningregister.cherwell.gov.uk/Document/Download?id=doc1",
            },
            {
                "document_id": "doc2",
                "file_path": "/data/raw/25_01178_REM/002_Site Plan.pdf",
                "file_size": 80000,
                "success": True,
                "description": "Site Plan",
                "document_type": "Plans - Site Plan",
                "url": "https://planningregister.cherwell.gov.uk/Document/Download?id=doc2",
            },
            {
                "document_id": "doc3",
                "file_path": "/data/raw/25_01178_REM/003_Design Statement.pdf",
                "file_size": 120000,
                "success": True,
                "description": "Design and Access Statement",
                "document_type": "Design and Access Statement",
                "url": "https://planningregister.cherwell.gov.uk/Document/Download?id=doc3",
            },
        ],
    }


@pytest.fixture
def sample_ingest_response():
    """Sample response from ingest_document tool."""
    return {
        "status": "success",
        "document_id": "doc_test123",
        "chunks_created": 15,
    }


@pytest.fixture
def sample_search_response():
    """Sample response from search_application_docs or search_policy tools."""
    return {
        "results": [
            {"text": "The proposed development includes cycle parking.", "metadata": {"source_file": "ta.pdf"}},
        ],
    }


def _make_claude_response(
    markdown: str = "# Review\n## Application Summary\n...\n## Key Documents\n...",
    key_documents_json: list | None = None,
    input_tokens: int = 1000,
    output_tokens: int = 2000,
):
    """Build a mock Anthropic Messages response."""
    text = markdown
    if key_documents_json is not None:
        text += "\n\n```key_documents_json\n"
        text += json.dumps(key_documents_json, indent=2)
        text += "\n```"

    content_block = SimpleNamespace(text=text)
    usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    return SimpleNamespace(content=[content_block], usage=usage)


def _search_side_effects(n: int = 7, response: dict | None = None):
    """Generate n search response entries for Phase 4 (4 doc + 3 policy searches)."""
    resp = response or {"results": []}
    return [resp] * n


# ---------------------------------------------------------------------------
# Original agent-integration tests
# ---------------------------------------------------------------------------

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
        monkeypatch,
        sample_application_response,
        sample_download_response,
        sample_ingest_response,
        sample_search_response,
    ):
        """
        Verifies [agent-integration:AgentOrchestrator/TS-03] - Complete workflow execution

        Given: Valid review job
        When: Orchestrator executes workflow
        Then: All 5 phases complete in order; review result produced
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        claude_resp = _make_claude_response(
            markdown="# Review\n## Application Summary\n...\n**Overall Rating:** AMBER\n## Key Documents\n...",
            key_documents_json=[
                {"title": "Transport Assessment", "category": "Transport & Access",
                 "summary": "Analyses traffic impacts.", "url": "https://example.com/ta.pdf"},
            ],
        )

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,   # Phase 1
            sample_download_response,      # Phase 2
            sample_ingest_response,        # Phase 3 doc 1
            sample_ingest_response,        # Phase 3 doc 2
            sample_ingest_response,        # Phase 3 doc 3
            *_search_side_effects(7, sample_search_response),  # Phase 4
        ]

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.return_value = claude_resp
            MockAnthropic.return_value = mock_client_inst

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
    async def test_workflow_with_no_documents(
        self, mock_mcp_client, mock_redis, monkeypatch, sample_search_response,
    ):
        """Test workflow when application has no documents."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        claude_resp = _make_claude_response(
            markdown="# Review\n**Overall Rating:** AMBER\nNo transport documents available.",
        )

        mock_mcp_client.call_tool.side_effect = [
            {
                "status": "success",
                "application": {
                    "reference": "25/00001/FUL",
                    "address": "Test Address",
                    "documents": [],
                },
            },
            {"status": "success", "downloads": []},  # Phase 2: no downloads
            *_search_side_effects(7, sample_search_response),  # Phase 4
        ]

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.return_value = claude_resp
            MockAnthropic.return_value = mock_client_inst

            orchestrator = AgentOrchestrator(
                review_id="rev_test123",
                application_ref="25/00001/FUL",
                mcp_client=mock_mcp_client,
                redis_client=mock_redis,
            )

            result = await orchestrator.run()

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
        monkeypatch,
        sample_application_response,
        sample_download_response,
        sample_ingest_response,
        sample_search_response,
    ):
        """
        Verifies [agent-integration:AgentOrchestrator/TS-05] - Partial document ingestion

        Given: 2 of 3 documents fail ingestion
        When: Ingesting documents phase
        Then: Workflow continues; failed documents logged; review produced with available docs
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        claude_resp = _make_claude_response(
            markdown="# Review\n**Overall Rating:** AMBER",
        )

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,
            sample_download_response,
            sample_ingest_response,  # doc 1 succeeds
            MCPToolError("ingest_document", "OCR failed - corrupt file"),  # doc 2 fails
            MCPToolError("ingest_document", "Unsupported format"),  # doc 3 fails
            *_search_side_effects(7, sample_search_response),
        ]

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.return_value = claude_resp
            MockAnthropic.return_value = mock_client_inst

            orchestrator = AgentOrchestrator(
                review_id="rev_test123",
                application_ref="25/01178/REM",
                mcp_client=mock_mcp_client,
                redis_client=mock_redis,
            )

            result = await orchestrator.run()

        assert result.success is True

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
        monkeypatch,
        sample_application_response,
        sample_download_response,
        sample_ingest_response,
        sample_search_response,
    ):
        """
        Verifies [agent-integration:AgentOrchestrator/TS-07] - State persistence

        Given: Workflow in phase 3
        When: Worker restarts
        Then: Orchestrator resumes from phase 3 (idempotent)
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        claude_resp = _make_claude_response(
            markdown="# Review\n**Overall Rating:** GREEN",
        )

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
            *_search_side_effects(7, sample_search_response),
        ]

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.return_value = claude_resp
            MockAnthropic.return_value = mock_client_inst

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
        monkeypatch,
        sample_application_response,
        sample_download_response,
        sample_ingest_response,
        sample_search_response,
    ):
        """
        Verifies [agent-integration:AgentOrchestrator/TS-02] - Reconnection on transient failure

        Given: MCP connection error occurs mid-workflow (recoverable)
        When: Connection error detected
        Then: Error is logged as recoverable; workflow continues with degraded functionality
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        claude_resp = _make_claude_response(
            markdown="# Review\n**Overall Rating:** AMBER",
        )

        mock_mcp_client.call_tool.side_effect = [
            MCPConnectionError(MCPServerType.CHERWELL_SCRAPER, "Connection lost"),  # Phase 1 fails
            sample_download_response,  # Phase 2
            sample_ingest_response,    # Phase 3 (1 downloaded doc)
            sample_ingest_response,
            sample_ingest_response,
            *_search_side_effects(7, sample_search_response),
        ]

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.return_value = claude_resp
            MockAnthropic.return_value = mock_client_inst

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
        monkeypatch,
        sample_application_response,
        sample_ingest_response,
        sample_search_response,
    ):
        """Test workflow continues with partial download failures."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        claude_resp = _make_claude_response(
            markdown="# Review\n**Overall Rating:** AMBER",
        )

        download_response = {
            "status": "success",
            "downloads": [
                {
                    "document_id": "doc1",
                    "file_path": "/data/raw/doc1.pdf",
                    "file_size": 100000,
                    "success": True,
                    "description": "Transport Assessment",
                    "document_type": "Transport Assessment",
                    "url": "https://example.com/doc1.pdf",
                },
                {
                    "document_id": "doc2",
                    "file_path": "",
                    "file_size": 0,
                    "success": False,
                    "error": "Timeout",
                    "description": "Site Plan",
                    "document_type": "Plans - Site Plan",
                    "url": "https://example.com/doc2.pdf",
                },
                {
                    "document_id": "doc3",
                    "file_path": "",
                    "file_size": 0,
                    "success": False,
                    "error": "404 Not Found",
                    "description": "Design Statement",
                    "document_type": "Design and Access Statement",
                    "url": "https://example.com/doc3.pdf",
                },
            ],
        }

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,
            download_response,
            sample_ingest_response,  # Only 1 doc to ingest
            *_search_side_effects(7, sample_search_response),
        ]

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.return_value = claude_resp
            MockAnthropic.return_value = mock_client_inst

            orchestrator = AgentOrchestrator(
                review_id="rev_test123",
                application_ref="25/01178/REM",
                mcp_client=mock_mcp_client,
                redis_client=mock_redis,
            )

            result = await orchestrator.run()

        assert result.success is True
        assert orchestrator._ingestion_result.documents_fetched == 1

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_ingest_returns_error_status(
        self,
        mock_mcp_client,
        mock_redis,
        monkeypatch,
        sample_application_response,
        sample_download_response,
        sample_search_response,
    ):
        """Test handling of ingest_document returning error status."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        claude_resp = _make_claude_response(
            markdown="# Review\n**Overall Rating:** AMBER",
        )

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,
            sample_download_response,
            {"status": "error", "message": "Corrupt PDF"},  # doc 1 fails
            {"status": "success", "chunks_created": 10},    # doc 2 succeeds
            {"status": "success", "chunks_created": 5},     # doc 3 succeeds
            *_search_side_effects(7, sample_search_response),
        ]

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.return_value = claude_resp
            MockAnthropic.return_value = mock_client_inst

            orchestrator = AgentOrchestrator(
                review_id="rev_test123",
                application_ref="25/01178/REM",
                mcp_client=mock_mcp_client,
                redis_client=mock_redis,
            )

            result = await orchestrator.run()

        assert result.success is True
        assert orchestrator._ingestion_result.documents_ingested == 2

        errors = orchestrator.progress.state.errors_encountered
        assert len(errors) == 1
        assert "Corrupt PDF" in errors[0]["error"]

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_already_ingested_counts_as_success(
        self,
        mock_mcp_client,
        mock_redis,
        monkeypatch,
        sample_application_response,
        sample_download_response,
        sample_search_response,
    ):
        """Test that already_ingested status is counted as success."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        claude_resp = _make_claude_response(
            markdown="# Review\n**Overall Rating:** GREEN",
        )

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,
            sample_download_response,
            {"status": "already_ingested", "document_id": "doc_123"},
            {"status": "already_ingested", "document_id": "doc_456"},
            {"status": "already_ingested", "document_id": "doc_789"},
            *_search_side_effects(7, sample_search_response),
        ]

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.return_value = claude_resp
            MockAnthropic.return_value = mock_client_inst

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


# ---------------------------------------------------------------------------
# Key Documents tests
# ---------------------------------------------------------------------------


class TestDocumentIngestionResultMetadata:
    """
    Tests for DocumentIngestionResult.document_metadata field.

    Implements [key-documents:DocumentIngestionResult/TS-01] and [TS-02]
    """

    def test_metadata_dict_populated(self):
        """
        Verifies [key-documents:DocumentIngestionResult/TS-01] - Metadata dict populated

        Given: 3 documents downloaded with metadata
        When: DocumentIngestionResult created
        Then: document_metadata has 3 entries keyed by file_path
        """
        result = DocumentIngestionResult(
            documents_fetched=3,
            document_paths=["/data/doc1.pdf", "/data/doc2.pdf", "/data/doc3.pdf"],
            document_metadata={
                "/data/doc1.pdf": {
                    "description": "Transport Assessment",
                    "document_type": "Transport Assessment",
                    "url": "https://example.com/doc1.pdf",
                },
                "/data/doc2.pdf": {
                    "description": "Site Plan",
                    "document_type": "Plans - Site Plan",
                    "url": "https://example.com/doc2.pdf",
                },
                "/data/doc3.pdf": {
                    "description": "Design Statement",
                    "document_type": "Design and Access Statement",
                    "url": "https://example.com/doc3.pdf",
                },
            },
        )

        assert len(result.document_metadata) == 3
        assert "/data/doc1.pdf" in result.document_metadata
        assert result.document_metadata["/data/doc1.pdf"]["document_type"] == "Transport Assessment"
        assert result.document_metadata["/data/doc2.pdf"]["url"] == "https://example.com/doc2.pdf"

    def test_metadata_dict_empty_when_no_downloads(self):
        """
        Verifies [key-documents:DocumentIngestionResult/TS-02] - Metadata dict empty when no downloads

        Given: No documents downloaded
        When: DocumentIngestionResult created
        Then: document_metadata is empty dict
        """
        result = DocumentIngestionResult()

        assert result.document_metadata == {}
        assert isinstance(result.document_metadata, dict)


class TestDownloadPhaseMetadata:
    """
    Tests for document metadata capture in _phase_download_documents.

    Implements [key-documents:AgentOrchestrator._phase_download_documents/TS-01] and [TS-02]
    """

    @pytest.mark.asyncio
    async def test_download_metadata_preserved(
        self,
        mock_mcp_client,
        mock_redis,
        sample_application_response,
        sample_download_response,
    ):
        """
        Verifies [key-documents:AgentOrchestrator._phase_download_documents/TS-01]

        Given: Download results contain document_id, description, document_type, url
        When: Phase 2 completes
        Then: DocumentIngestionResult.document_metadata contains a dict mapping file_path to metadata
        """
        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,
            sample_download_response,
        ]

        orchestrator = AgentOrchestrator(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )
        await orchestrator.initialize()

        # Run just Phase 1 and Phase 2
        await orchestrator._phase_fetch_metadata()
        await orchestrator._phase_download_documents()

        meta = orchestrator._ingestion_result.document_metadata
        assert len(meta) == 3

        # Check first document metadata
        ta_path = "/data/raw/25_01178_REM/001_Transport Assessment.pdf"
        assert ta_path in meta
        assert meta[ta_path]["description"] == "Transport Assessment"
        assert meta[ta_path]["document_type"] == "Transport Assessment"
        assert "cherwell.gov.uk" in meta[ta_path]["url"]

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_failed_downloads_excluded_from_metadata(
        self,
        mock_mcp_client,
        mock_redis,
        sample_application_response,
    ):
        """
        Verifies [key-documents:AgentOrchestrator._phase_download_documents/TS-02]

        Given: A document fails to download
        When: Phase 2 completes
        Then: The failed document has no entry in document_metadata
        """
        download_response = {
            "status": "success",
            "downloads": [
                {
                    "document_id": "doc1",
                    "file_path": "/data/raw/doc1.pdf",
                    "file_size": 100000,
                    "success": True,
                    "description": "Transport Assessment",
                    "document_type": "Transport Assessment",
                    "url": "https://example.com/doc1.pdf",
                },
                {
                    "document_id": "doc2",
                    "file_path": "",
                    "file_size": 0,
                    "success": False,
                    "error": "Download timeout",
                    "description": "Site Plan",
                    "document_type": "Plans - Site Plan",
                    "url": "https://example.com/doc2.pdf",
                },
            ],
        }

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,
            download_response,
        ]

        orchestrator = AgentOrchestrator(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )
        await orchestrator.initialize()

        await orchestrator._phase_fetch_metadata()
        await orchestrator._phase_download_documents()

        meta = orchestrator._ingestion_result.document_metadata
        # Only the successful download should appear (failed has empty file_path)
        assert len(meta) == 1
        assert "/data/raw/doc1.pdf" in meta

        await orchestrator.close()


class TestGenerateReviewKeyDocuments:
    """
    Tests for key_documents generation in _phase_generate_review.

    Implements [key-documents:AgentOrchestrator._phase_generate_review/TS-01] through [TS-04]
    """

    @pytest.mark.asyncio
    async def test_key_documents_in_review_dict(
        self,
        mock_mcp_client,
        mock_redis,
        monkeypatch,
        sample_search_response,
    ):
        """
        Verifies [key-documents:AgentOrchestrator._phase_generate_review/TS-01]

        Given: Claude returns valid key_documents JSON
        When: Phase 5 completes
        Then: ReviewResult.review contains "key_documents" list
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        key_docs = [
            {"title": "Transport Assessment", "category": "Transport & Access",
             "summary": "Analyses traffic impacts.", "url": "https://example.com/ta.pdf"},
            {"title": "Design and Access Statement", "category": "Design & Layout",
             "summary": "Describes site layout.", "url": "https://example.com/das.pdf"},
        ]

        claude_resp = _make_claude_response(
            markdown="# Review\n**Overall Rating:** AMBER\n## Key Documents\n...",
            key_documents_json=key_docs,
        )

        # Pre-populate orchestrator state for Phase 5
        orchestrator = AgentOrchestrator(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )
        await orchestrator.initialize()

        orchestrator._application = ApplicationMetadata(
            reference="25/01178/REM",
            address="Test Site",
            proposal="Test proposal",
        )
        orchestrator._ingestion_result = DocumentIngestionResult(
            documents_fetched=2,
            documents_ingested=2,
            document_paths=["/data/ta.pdf", "/data/das.pdf"],
            document_metadata={
                "/data/ta.pdf": {"description": "Transport Assessment", "document_type": "Transport Assessment", "url": "https://example.com/ta.pdf"},
                "/data/das.pdf": {"description": "Design and Access Statement", "document_type": "Design and Access Statement", "url": "https://example.com/das.pdf"},
            },
        )

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.return_value = claude_resp
            MockAnthropic.return_value = mock_client_inst

            await orchestrator._phase_generate_review()

        review = orchestrator._review_result.review
        assert review["key_documents"] is not None
        assert len(review["key_documents"]) == 2
        assert review["key_documents"][0]["title"] == "Transport Assessment"
        assert review["key_documents"][1]["category"] == "Design & Layout"

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_key_documents_markdown_section(
        self,
        mock_mcp_client,
        mock_redis,
        monkeypatch,
    ):
        """
        Verifies [key-documents:AgentOrchestrator._phase_generate_review/TS-02]

        Given: Claude returns markdown with Key Documents section
        When: Phase 5 completes
        Then: full_markdown contains "## Key Documents" section
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        markdown = (
            "# Cycle Advocacy Review: 25/01178/REM\n\n"
            "## Application Summary\n- **Reference:** 25/01178/REM\n\n"
            "## Key Documents\n\n### Transport & Access\n"
            "- [Transport Assessment](https://example.com/ta.pdf)\n"
            "  Analyses traffic impacts.\n\n"
            "## Assessment Summary\n**Overall Rating:** AMBER"
        )

        claude_resp = _make_claude_response(
            markdown=markdown,
            key_documents_json=[
                {"title": "Transport Assessment", "category": "Transport & Access",
                 "summary": "Analyses traffic impacts.", "url": "https://example.com/ta.pdf"},
            ],
        )

        orchestrator = AgentOrchestrator(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )
        await orchestrator.initialize()

        orchestrator._application = ApplicationMetadata(reference="25/01178/REM")
        orchestrator._ingestion_result = DocumentIngestionResult()

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.return_value = claude_resp
            MockAnthropic.return_value = mock_client_inst

            await orchestrator._phase_generate_review()

        full_md = orchestrator._review_result.review["full_markdown"]
        assert "## Key Documents" in full_md
        # The key_documents_json block should be stripped from the markdown
        assert "```key_documents_json" not in full_md

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_graceful_fallback_on_parse_failure(
        self,
        mock_mcp_client,
        mock_redis,
        monkeypatch,
    ):
        """
        Verifies [key-documents:AgentOrchestrator._phase_generate_review/TS-03]

        Given: Claude response does not contain valid key_documents JSON
        When: Phase 5 completes
        Then: ReviewResult.review["key_documents"] is None, review still succeeds
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        # No key_documents_json block in the response
        claude_resp = _make_claude_response(
            markdown="# Review\n**Overall Rating:** RED\nNo key documents found.",
        )

        orchestrator = AgentOrchestrator(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )
        await orchestrator.initialize()

        orchestrator._application = ApplicationMetadata(reference="25/01178/REM")
        orchestrator._ingestion_result = DocumentIngestionResult()

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.return_value = claude_resp
            MockAnthropic.return_value = mock_client_inst

            await orchestrator._phase_generate_review()

        review = orchestrator._review_result.review
        assert review["key_documents"] is None
        assert review["overall_rating"] == "red"
        assert orchestrator._review_result.success is True

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_document_urls_passed_through(
        self,
        mock_mcp_client,
        mock_redis,
        monkeypatch,
    ):
        """
        Verifies [key-documents:AgentOrchestrator._phase_generate_review/TS-04]

        Given: Ingested document metadata includes urls
        When: Phase 5 builds prompt
        Then: Prompt includes document urls for Claude to reproduce in output
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        claude_resp = _make_claude_response(
            markdown="# Review\n**Overall Rating:** AMBER",
        )

        orchestrator = AgentOrchestrator(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )
        await orchestrator.initialize()

        orchestrator._application = ApplicationMetadata(reference="25/01178/REM")
        orchestrator._ingestion_result = DocumentIngestionResult(
            documents_fetched=1,
            documents_ingested=1,
            document_paths=["/data/ta.pdf"],
            document_metadata={
                "/data/ta.pdf": {
                    "description": "Transport Assessment",
                    "document_type": "Transport Assessment",
                    "url": "https://planningregister.cherwell.gov.uk/Document/Download?id=ta123",
                },
            },
        )

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.return_value = claude_resp
            MockAnthropic.return_value = mock_client_inst

            await orchestrator._phase_generate_review()

            # Check that the user prompt included the URL
            call_args = mock_client_inst.messages.create.call_args
            user_msg = call_args.kwargs["messages"][0]["content"]
            assert "planningregister.cherwell.gov.uk" in user_msg
            assert "Transport Assessment" in user_msg

        await orchestrator.close()


class TestKeyDocumentsIntegration:
    """
    Integration tests for key documents flowing through the pipeline.

    Implements [key-documents:ITS-01] and [key-documents:ITS-02]
    """

    @pytest.mark.asyncio
    async def test_key_documents_flow_through_pipeline(
        self,
        mock_mcp_client,
        mock_redis,
        monkeypatch,
        sample_application_response,
        sample_download_response,
        sample_ingest_response,
        sample_search_response,
    ):
        """
        Verifies [key-documents:ITS-01] - Key documents flow through pipeline

        Given: A review is submitted for an application with transport and design documents
        When: Review completes
        Then: ReviewResult.review contains key_documents with correct titles, categories, summaries, urls
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        key_docs = [
            {"title": "Transport Assessment", "category": "Transport & Access",
             "summary": "Analyses traffic impacts of the proposed development.",
             "url": "https://planningregister.cherwell.gov.uk/Document/Download?id=doc1"},
            {"title": "Design and Access Statement", "category": "Design & Layout",
             "summary": "Describes site layout including cycle parking locations.",
             "url": "https://planningregister.cherwell.gov.uk/Document/Download?id=doc3"},
            {"title": "Site Plan", "category": "Design & Layout",
             "summary": "Shows proposed layout with access roads.",
             "url": "https://planningregister.cherwell.gov.uk/Document/Download?id=doc2"},
        ]

        claude_resp = _make_claude_response(
            markdown="# Cycle Advocacy Review: 25/01178/REM\n\n## Application Summary\n...\n## Key Documents\n### Transport & Access\n- [Transport Assessment](https://...)\n  Analyses traffic impacts.\n\n## Assessment Summary\n**Overall Rating:** AMBER",
            key_documents_json=key_docs,
        )

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,
            sample_download_response,
            sample_ingest_response,
            sample_ingest_response,
            sample_ingest_response,
            *_search_side_effects(7, sample_search_response),
        ]

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.return_value = claude_resp
            MockAnthropic.return_value = mock_client_inst

            orchestrator = AgentOrchestrator(
                review_id="rev_test123",
                application_ref="25/01178/REM",
                mcp_client=mock_mcp_client,
                redis_client=mock_redis,
            )

            result = await orchestrator.run()

        assert result.success is True
        assert result.review is not None
        assert result.review["key_documents"] is not None
        assert len(result.review["key_documents"]) == 3

        # Verify document structure
        ta_doc = result.review["key_documents"][0]
        assert ta_doc["title"] == "Transport Assessment"
        assert ta_doc["category"] == "Transport & Access"
        assert ta_doc["summary"].startswith("Analyses")
        assert ta_doc["url"] is not None

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_key_documents_in_stored_result(
        self,
        mock_mcp_client,
        mock_redis,
        monkeypatch,
        sample_application_response,
        sample_download_response,
        sample_ingest_response,
        sample_search_response,
    ):
        """
        Verifies [key-documents:ITS-02] - Key documents in stored result

        Given: A review completes with key_documents
        When: Result is stored
        Then: Result JSON includes review.key_documents array with expected structure
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        key_docs = [
            {"title": "Transport Assessment", "category": "Transport & Access",
             "summary": "Traffic analysis.", "url": "https://example.com/ta.pdf"},
        ]

        claude_resp = _make_claude_response(
            markdown="# Review\n**Overall Rating:** AMBER\n## Key Documents\n...",
            key_documents_json=key_docs,
        )

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,
            sample_download_response,
            sample_ingest_response,
            sample_ingest_response,
            sample_ingest_response,
            *_search_side_effects(7, sample_search_response),
        ]

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.return_value = claude_resp
            MockAnthropic.return_value = mock_client_inst

            orchestrator = AgentOrchestrator(
                review_id="rev_test123",
                application_ref="25/01178/REM",
                mcp_client=mock_mcp_client,
                redis_client=mock_redis,
            )

            result = await orchestrator.run()

        # Simulate what the API layer does: serialize the review dict to JSON
        review_json = json.dumps(result.review)
        parsed = json.loads(review_json)

        assert "key_documents" in parsed
        assert isinstance(parsed["key_documents"], list)
        assert len(parsed["key_documents"]) == 1
        assert parsed["key_documents"][0]["title"] == "Transport Assessment"
        assert parsed["key_documents"][0]["category"] == "Transport & Access"

        await orchestrator.close()
