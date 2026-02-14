"""
Tests for AgentOrchestrator.

Implements test scenarios from [agent-integration:AgentOrchestrator/TS-01] through [TS-08]
Implements test scenarios from [key-documents:DocumentIngestionResult/TS-01] through [TS-02]
Implements test scenarios from [key-documents:AgentOrchestrator._phase_download_documents/TS-01] through [TS-02]
Implements test scenarios from [key-documents:AgentOrchestrator._phase_generate_review/TS-01] through [TS-04]
Implements test scenarios from [key-documents:ITS-01] through [ITS-02]
Implements test scenarios from [s3-document-storage:AgentOrchestrator/TS-01] through [TS-05]
Implements test scenarios from [s3-document-storage:DownloadPhase/TS-01] through [TS-02]
Implements test scenarios from [structured-review-output:AgentOrchestrator/TS-01] through [TS-05]
Implements test scenarios from [structured-review-output:ITS-01] through [ITS-03]
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from src.agent.mcp_client import MCPClientManager, MCPConnectionError, MCPServerType, MCPToolError
from src.agent.orchestrator import (
    AgentOrchestrator,
    ApplicationMetadata,
    DocumentIngestionResult,
    OrchestratorError,
)
from src.agent.progress import ReviewPhase
from src.shared.storage import StorageUploadError

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


@pytest.fixture
def sample_list_documents_response():
    """Sample response from list_application_documents tool."""
    return {
        "status": "success",
        "documents": [
            {
                "document_id": "doc1",
                "description": "Transport Assessment",
                "document_type": "Transport Assessment",
                "url": "https://example.com/doc1",
                "date_published": "2024-01-01",
            },
            {
                "document_id": "doc2",
                "description": "Site Plan",
                "document_type": "Plans - Site Plan",
                "url": "https://example.com/doc2",
                "date_published": "2024-01-01",
            },
            {
                "document_id": "doc3",
                "description": "Design and Access Statement",
                "document_type": "Design and Access Statement",
                "url": "https://example.com/doc3",
                "date_published": "2024-01-01",
            },
        ],
    }


@pytest.fixture
def sample_per_doc_download_responses():
    """Sample responses for per-document download_document calls (3 docs)."""
    return [
        {
            "status": "success",
            "file_path": "/data/raw/25_01178_REM/001_Transport Assessment.pdf",
            "file_size": 150000,
        },
        {
            "status": "success",
            "file_path": "/data/raw/25_01178_REM/002_Site Plan.pdf",
            "file_size": 80000,
        },
        {
            "status": "success",
            "file_path": "/data/raw/25_01178_REM/003_Design Statement.pdf",
            "file_size": 120000,
        },
    ]


def _make_claude_response(
    text: str = "# Review\n## Application Summary\n...\n## Key Documents\n...",
    input_tokens: int = 1000,
    output_tokens: int = 2000,
):
    """Build a mock Anthropic Messages response."""
    content_block = SimpleNamespace(text=text)
    usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    return SimpleNamespace(content=[content_block], usage=usage)


# Default structure call JSON used across tests
SAMPLE_STRUCTURE_JSON = json.dumps({
    "overall_rating": "amber",
    "summary": "The application provides basic cycle parking but lacks safe cycle routes and adequate junction design. Partial policy compliance with room for improvement.",
    "aspects": [
        {"name": "Cycle Parking", "rating": "amber", "key_issue": "Design unverified",
         "analysis": "Minimum spaces provided."},
        {"name": "Cycle Routes", "rating": "red", "key_issue": "No connections",
         "analysis": "No off-site routes."},
        {"name": "Junctions", "rating": "amber", "key_issue": "Limited detail",
         "analysis": "Junction designs not provided."},
        {"name": "Permeability", "rating": "amber", "key_issue": "Could improve",
         "analysis": "Some permeability but gaps."},
        {"name": "Policy Compliance", "rating": "amber", "key_issue": "Partial compliance",
         "analysis": "Meets some but not all."},
    ],
    "policy_compliance": [
        {"requirement": "Sustainable transport", "policy_source": "NPPF 115",
         "compliant": False, "notes": "Car-based design"},
    ],
    "recommendations": ["Provide cycle track"],
    "suggested_conditions": [],
    "key_documents": [
        {"title": "Transport Assessment", "category": "Transport & Access",
         "summary": "Traffic analysis.", "url": "https://example.com/ta.pdf"},
    ],
})


def _make_two_phase_side_effect(
    structure_json: str = SAMPLE_STRUCTURE_JSON,
    markdown: str = "# Review\n## Application Summary\n...\n**Overall Rating:** AMBER",
    structure_tokens: tuple[int, int] = (500, 1500),
    report_tokens: tuple[int, int] = (1000, 3000),
):
    """Build side_effect for messages.create that handles queries + structure + report + verify calls."""
    query_resp = _make_query_response()
    structure_resp = _make_claude_response(
        text=structure_json,
        input_tokens=structure_tokens[0],
        output_tokens=structure_tokens[1],
    )
    report_resp = _make_claude_response(
        text=markdown,
        input_tokens=report_tokens[0],
        output_tokens=report_tokens[1],
    )
    return [query_resp, structure_resp, report_resp, _make_verification_response()]


def _make_review_side_effect(
    structure_json: str = SAMPLE_STRUCTURE_JSON,
    markdown: str = "# Review\n## Application Summary\n...\n**Overall Rating:** AMBER",
    structure_tokens: tuple[int, int] = (500, 1500),
    report_tokens: tuple[int, int] = (1000, 3000),
):
    """Build side_effect for messages.create for direct _phase_generate_review calls (structure + report only)."""
    structure_resp = _make_claude_response(
        text=structure_json,
        input_tokens=structure_tokens[0],
        output_tokens=structure_tokens[1],
    )
    report_resp = _make_claude_response(
        text=markdown,
        input_tokens=report_tokens[0],
        output_tokens=report_tokens[1],
    )
    return [structure_resp, report_resp]


# Default filter response: selects all 3 docs (doc1, doc2, doc3)
SAMPLE_FILTER_DOC_IDS = json.dumps(["doc1", "doc2", "doc3"])

# Default query generation response JSON
SAMPLE_QUERY_RESPONSE_JSON = json.dumps({
    "application_queries": [
        "cycle parking provision quantity type location",
        "cycle route design connectivity network",
        "junction design safety for cyclists",
        "pedestrian cycle permeability through site",
    ],
    "policy_queries": [
        {"query": "cycle infrastructure design segregation", "sources": ["LTN_1_20"]},
        {"query": "sustainable transport cycling policy", "sources": ["NPPF", "CHERWELL_LP_2015"]},
        {"query": "cycling walking infrastructure plan", "sources": ["OCC_LTCP", "BICESTER_LCWIP"]},
    ],
})

# Default verification response JSON
SAMPLE_VERIFICATION_RESPONSE_JSON = json.dumps({
    "claims": [
        {"claim": "The development includes cycle parking", "verified": True, "source": "Transport Assessment"},
        {"claim": "NPPF paragraph 115 requires sustainable transport", "verified": True, "source": "NPPF evidence chunk"},
        {"claim": "No off-site cycle connections provided", "verified": True, "source": "Transport Assessment"},
    ],
})


def _make_filter_response(
    doc_ids_json: str = SAMPLE_FILTER_DOC_IDS,
    input_tokens: int = 200,
    output_tokens: int = 50,
):
    """Build a mock Claude response for the document filter call."""
    return _make_claude_response(
        text=doc_ids_json,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _make_query_response(
    query_json: str = SAMPLE_QUERY_RESPONSE_JSON,
    input_tokens: int = 300,
    output_tokens: int = 150,
):
    """Build a mock Claude response for the query generation call."""
    return _make_claude_response(
        text=query_json,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _make_verification_response(
    verification_json: str = SAMPLE_VERIFICATION_RESPONSE_JSON,
    input_tokens: int = 400,
    output_tokens: int = 200,
):
    """Build a mock Claude response for the verification call."""
    return _make_claude_response(
        text=verification_json,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _make_three_phase_side_effect(
    filter_doc_ids: str = SAMPLE_FILTER_DOC_IDS,
    structure_json: str = SAMPLE_STRUCTURE_JSON,
    markdown: str = "# Review\n## Application Summary\n...\n**Overall Rating:** AMBER",
    structure_tokens: tuple[int, int] = (500, 1500),
    report_tokens: tuple[int, int] = (1000, 3000),
):
    """Build side_effect for messages.create: filter + queries + structure + report + verify calls."""
    filter_resp = _make_filter_response(doc_ids_json=filter_doc_ids)
    query_resp = _make_query_response()
    structure_resp = _make_claude_response(
        text=structure_json,
        input_tokens=structure_tokens[0],
        output_tokens=structure_tokens[1],
    )
    report_resp = _make_claude_response(
        text=markdown,
        input_tokens=report_tokens[0],
        output_tokens=report_tokens[1],
    )
    return [filter_resp, query_resp, structure_resp, report_resp, _make_verification_response()]


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
        sample_list_documents_response,
        sample_per_doc_download_responses,
        sample_ingest_response,
        sample_search_response,
    ):
        """
        Verifies [agent-integration:AgentOrchestrator/TS-03] - Complete workflow execution

        Given: Valid review job
        When: Orchestrator executes workflow
        Then: All 7 phases complete in order; review result produced
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,       # Phase 1: get_application_details
            sample_list_documents_response,    # Phase 2: list_application_documents
            *sample_per_doc_download_responses, # Phase 3: download_document x3
            sample_ingest_response,            # Phase 4: ingest doc 1
            sample_ingest_response,            # Phase 4: ingest doc 2
            sample_ingest_response,            # Phase 4: ingest doc 3
            *_search_side_effects(7, sample_search_response),  # Phase 5
        ]

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.side_effect = _make_three_phase_side_effect()
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
        assert "filtering_documents" in completed_phases
        assert "downloading_documents" in completed_phases
        assert "ingesting_documents" in completed_phases
        assert "analysing_application" in completed_phases
        assert "generating_review" in completed_phases
        assert "verifying_review" in completed_phases

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_workflow_with_no_documents(
        self, mock_mcp_client, mock_redis, monkeypatch, sample_search_response,
    ):
        """Test workflow when application has no documents."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        mock_mcp_client.call_tool.side_effect = [
            {
                "status": "success",
                "application": {
                    "reference": "25/00001/FUL",
                    "address": "Test Address",
                    "documents": [],
                },
            },
            {"status": "success", "documents": []},  # Phase 2: list_application_documents (empty)
            # Phase 3: no downloads (no selected docs)
            # Phase 4: no ingestion (no docs)
            *_search_side_effects(7, sample_search_response),  # Phase 5
        ]

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            # No filter call needed (0 documents), just structure + report
            mock_client_inst.messages.create.side_effect = _make_two_phase_side_effect()
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
        sample_list_documents_response,
        sample_per_doc_download_responses,
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

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,        # Phase 1
            sample_list_documents_response,     # Phase 2: list_application_documents
            *sample_per_doc_download_responses,  # Phase 3: download_document x3
            sample_ingest_response,  # Phase 4: doc 1 succeeds
            MCPToolError("ingest_document", "OCR failed - corrupt file"),  # doc 2 fails
            MCPToolError("ingest_document", "Unsupported format"),  # doc 3 fails
            *_search_side_effects(7, sample_search_response),
        ]

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.side_effect = _make_three_phase_side_effect()
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
        monkeypatch,
        sample_application_response,
        sample_list_documents_response,
        sample_per_doc_download_responses,
    ):
        """Test that workflow fails if all documents fail to ingest."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,        # Phase 1
            sample_list_documents_response,     # Phase 2: list_application_documents
            *sample_per_doc_download_responses,  # Phase 3: download_document x3
            MCPToolError("ingest_document", "Failed"),  # Phase 4: all fail
            MCPToolError("ingest_document", "Failed"),
            MCPToolError("ingest_document", "Failed"),
        ]

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.side_effect = [_make_filter_response()]
            MockAnthropic.return_value = mock_client_inst

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

        # Simulate saved state from previous run
        saved_state = {
            "review_id": "rev_test123",
            "application_ref": "25/01178/REM",
            "current_phase": "ingesting_documents",
            "completed_phases": ["fetching_metadata", "filtering_documents", "downloading_documents"],
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
            mock_client_inst.messages.create.side_effect = _make_two_phase_side_effect()
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

            await orchestrator.run()

        # Should not have called get_application_details, list_application_documents,
        # or download_document (those phases were completed in previous run)
        tool_calls = [c[0][0] for c in mock_mcp_client.call_tool.call_args_list]
        assert "get_application_details" not in tool_calls
        assert "list_application_documents" not in tool_calls
        assert "download_document" not in tool_calls

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
        sample_list_documents_response,
        sample_per_doc_download_responses,
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

        mock_mcp_client.call_tool.side_effect = [
            MCPConnectionError(MCPServerType.CHERWELL_SCRAPER, "Connection lost"),  # Phase 1 fails
            sample_list_documents_response,     # Phase 2: list_application_documents
            *sample_per_doc_download_responses,  # Phase 3: download_document x3
            sample_ingest_response,             # Phase 4: ingest doc 1
            sample_ingest_response,             # Phase 4: ingest doc 2
            sample_ingest_response,             # Phase 4: ingest doc 3
            *_search_side_effects(7, sample_search_response),
        ]

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.side_effect = _make_three_phase_side_effect()
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

            await orchestrator.run()

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
        sample_list_documents_response,
        sample_ingest_response,
        sample_search_response,
    ):
        """Test workflow continues with partial download failures."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,        # Phase 1
            sample_list_documents_response,     # Phase 2: list_application_documents
            # Phase 3: per-doc downloads (doc1 succeeds, doc2 and doc3 fail)
            {"status": "success", "file_path": "/data/raw/doc1.pdf", "file_size": 100000},
            MCPToolError("download_document", "Timeout"),
            MCPToolError("download_document", "404 Not Found"),
            sample_ingest_response,  # Phase 4: Only 1 doc to ingest
            *_search_side_effects(7, sample_search_response),
        ]

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.side_effect = _make_three_phase_side_effect()
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
        sample_list_documents_response,
        sample_per_doc_download_responses,
        sample_search_response,
    ):
        """Test handling of ingest_document returning error status."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,        # Phase 1
            sample_list_documents_response,     # Phase 2: list_application_documents
            *sample_per_doc_download_responses,  # Phase 3: download_document x3
            {"status": "error", "message": "Corrupt PDF"},  # Phase 4: doc 1 fails
            {"status": "success", "chunks_created": 10},    # doc 2 succeeds
            {"status": "success", "chunks_created": 5},     # doc 3 succeeds
            *_search_side_effects(7, sample_search_response),
        ]

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.side_effect = _make_three_phase_side_effect()
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
        sample_list_documents_response,
        sample_per_doc_download_responses,
        sample_search_response,
    ):
        """Test that already_ingested status is counted as success."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,        # Phase 1
            sample_list_documents_response,     # Phase 2: list_application_documents
            *sample_per_doc_download_responses,  # Phase 3: download_document x3
            {"status": "already_ingested", "document_id": "doc_123"},  # Phase 4
            {"status": "already_ingested", "document_id": "doc_456"},
            {"status": "already_ingested", "document_id": "doc_789"},
            *_search_side_effects(7, sample_search_response),
        ]

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.side_effect = _make_three_phase_side_effect()
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
        sample_per_doc_download_responses,
    ):
        """
        Verifies [key-documents:AgentOrchestrator._phase_download_documents/TS-01]

        Given: Download results contain document_id, description, document_type, url
        When: Phase 3 completes
        Then: DocumentIngestionResult.document_metadata contains a dict mapping file_path to metadata
        """
        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,
            *sample_per_doc_download_responses,  # download_document x3
        ]

        orchestrator = AgentOrchestrator(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )
        await orchestrator.initialize()

        # Run Phase 1 to populate application metadata
        await orchestrator._phase_fetch_metadata()

        # Set selected documents (normally done by filter phase)
        orchestrator._selected_documents = [
            {
                "document_id": "doc1",
                "description": "Transport Assessment",
                "document_type": "Transport Assessment",
                "url": "https://planningregister.cherwell.gov.uk/Document/Download?id=doc1",
                "date_published": "2024-01-01",
            },
            {
                "document_id": "doc2",
                "description": "Site Plan",
                "document_type": "Plans - Site Plan",
                "url": "https://planningregister.cherwell.gov.uk/Document/Download?id=doc2",
                "date_published": "2024-01-01",
            },
            {
                "document_id": "doc3",
                "description": "Design and Access Statement",
                "document_type": "Design and Access Statement",
                "url": "https://planningregister.cherwell.gov.uk/Document/Download?id=doc3",
                "date_published": "2024-01-01",
            },
        ]

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
        When: Phase 3 completes
        Then: The failed document has no entry in document_metadata
        """
        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,
            # Per-doc download: doc1 succeeds, doc2 fails
            {"status": "success", "file_path": "/data/raw/doc1.pdf", "file_size": 100000},
            MCPToolError("download_document", "Download timeout"),
        ]

        orchestrator = AgentOrchestrator(
            review_id="rev_test123",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )
        await orchestrator.initialize()

        await orchestrator._phase_fetch_metadata()

        # Set selected documents (normally done by filter phase)
        orchestrator._selected_documents = [
            {
                "document_id": "doc1",
                "description": "Transport Assessment",
                "document_type": "Transport Assessment",
                "url": "https://example.com/doc1.pdf",
                "date_published": "2024-01-01",
            },
            {
                "document_id": "doc2",
                "description": "Site Plan",
                "document_type": "Plans - Site Plan",
                "url": "https://example.com/doc2.pdf",
                "date_published": "2024-01-01",
            },
        ]

        await orchestrator._phase_download_documents()

        meta = orchestrator._ingestion_result.document_metadata
        # Only the successful download should appear
        assert len(meta) == 1
        assert "/data/raw/doc1.pdf" in meta

        await orchestrator.close()


class TestGenerateReviewKeyDocuments:
    """
    Tests for key_documents generation in _phase_generate_review (two-phase approach).

    Key documents now come from the structure call JSON, not from
    inline markdown parsing.

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

        Given: Structure call returns JSON with key_documents array
        When: Phase 5 completes
        Then: ReviewResult.review contains "key_documents" list from structure JSON
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        structure_json = json.dumps({
            "overall_rating": "amber",
            "summary": "Basic cycle parking provided but lacks safe routes and detailed junction design.",
            "aspects": [
                {"name": "Cycle Parking", "rating": "amber", "key_issue": "Design unverified",
                 "analysis": "Minimum spaces provided."},
                {"name": "Cycle Routes", "rating": "red", "key_issue": "No connections",
                 "analysis": "No off-site routes."},
                {"name": "Junctions", "rating": "amber", "key_issue": "Limited detail",
                 "analysis": "Junction designs not provided."},
                {"name": "Permeability", "rating": "amber", "key_issue": "Could improve",
                 "analysis": "Some permeability but gaps."},
                {"name": "Policy Compliance", "rating": "amber", "key_issue": "Partial compliance",
                 "analysis": "Meets some but not all."},
            ],
            "policy_compliance": [
                {"requirement": "Sustainable transport", "policy_source": "NPPF 115",
                 "compliant": False, "notes": "Car-based design"},
            ],
            "recommendations": ["Provide cycle track"],
            "suggested_conditions": [],
            "key_documents": [
                {"title": "Transport Assessment", "category": "Transport & Access",
                 "summary": "Analyses traffic impacts.", "url": "https://example.com/ta.pdf"},
                {"title": "Design and Access Statement", "category": "Design & Layout",
                 "summary": "Describes site layout.", "url": "https://example.com/das.pdf"},
            ],
        })

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
            mock_client_inst.messages.create.side_effect = _make_review_side_effect(
                structure_json=structure_json,
            )
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

        Given: Report call returns markdown with Key Documents section
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
            mock_client_inst.messages.create.side_effect = _make_review_side_effect(
                markdown=markdown,
            )
            MockAnthropic.return_value = mock_client_inst

            await orchestrator._phase_generate_review()

        full_md = orchestrator._review_result.review["full_markdown"]
        assert "## Key Documents" in full_md

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_graceful_fallback_on_structure_failure(
        self,
        mock_mcp_client,
        mock_redis,
        monkeypatch,
    ):
        """
        Verifies [key-documents:AgentOrchestrator._phase_generate_review/TS-03]

        Given: Structure call returns invalid JSON (no key_documents available)
        When: Phase 5 falls back to single markdown call
        Then: ReviewResult.review["key_documents"] is None, review still succeeds
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

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
            # First call (structure) returns invalid JSON, second call (fallback) returns markdown
            mock_client_inst.messages.create.side_effect = [
                _make_claude_response(text="This is not valid JSON"),
                _make_claude_response(text="# Review\n**Overall Rating:** RED\nNo key documents found."),
            ]
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
            mock_client_inst.messages.create.side_effect = _make_review_side_effect()
            MockAnthropic.return_value = mock_client_inst

            await orchestrator._phase_generate_review()

            # Check that the first (structure) call's user prompt included the URL
            first_call_args = mock_client_inst.messages.create.call_args_list[0]
            user_msg = first_call_args.kwargs["messages"][0]["content"]
            assert "planningregister.cherwell.gov.uk" in user_msg
            assert "Transport Assessment" in user_msg

        await orchestrator.close()


class TestKeyDocumentsIntegration:
    """
    Integration tests for key documents flowing through the pipeline.

    Key documents now come from the structure call JSON in the two-phase approach.

    Implements [key-documents:ITS-01] and [key-documents:ITS-02]
    """

    @pytest.mark.asyncio
    async def test_key_documents_flow_through_pipeline(
        self,
        mock_mcp_client,
        mock_redis,
        monkeypatch,
        sample_application_response,
        sample_list_documents_response,
        sample_per_doc_download_responses,
        sample_ingest_response,
        sample_search_response,
    ):
        """
        Verifies [key-documents:ITS-01] - Key documents flow through pipeline

        Given: A review is submitted for an application with transport and design documents
        When: Review completes (two-phase: structure call returns key_documents in JSON)
        Then: ReviewResult.review contains key_documents with correct titles, categories, summaries, urls
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        structure_json = json.dumps({
            "overall_rating": "amber",
            "summary": "Mixed provision with some cycle parking but lacking safe routes and junction detail.",
            "aspects": [
                {"name": "Cycle Parking", "rating": "amber", "key_issue": "Design unverified",
                 "analysis": "Minimum spaces."},
                {"name": "Cycle Routes", "rating": "red", "key_issue": "No connections",
                 "analysis": "No routes."},
                {"name": "Junctions", "rating": "amber", "key_issue": "Limited detail",
                 "analysis": "Not provided."},
                {"name": "Permeability", "rating": "amber", "key_issue": "Could improve",
                 "analysis": "Gaps."},
                {"name": "Policy Compliance", "rating": "amber", "key_issue": "Partial",
                 "analysis": "Partial."},
            ],
            "policy_compliance": [
                {"requirement": "Sustainable transport", "policy_source": "NPPF 115",
                 "compliant": False, "notes": "Car-based design"},
            ],
            "recommendations": ["Provide cycle track"],
            "suggested_conditions": [],
            "key_documents": [
                {"title": "Transport Assessment", "category": "Transport & Access",
                 "summary": "Analyses traffic impacts of the proposed development.",
                 "url": "https://planningregister.cherwell.gov.uk/Document/Download?id=doc1"},
                {"title": "Design and Access Statement", "category": "Design & Layout",
                 "summary": "Describes site layout including cycle parking locations.",
                 "url": "https://planningregister.cherwell.gov.uk/Document/Download?id=doc3"},
                {"title": "Site Plan", "category": "Design & Layout",
                 "summary": "Shows proposed layout with access roads.",
                 "url": "https://planningregister.cherwell.gov.uk/Document/Download?id=doc2"},
            ],
        })

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,        # Phase 1
            sample_list_documents_response,     # Phase 2: list_application_documents
            *sample_per_doc_download_responses,  # Phase 3: download_document x3
            sample_ingest_response,             # Phase 4: ingest x3
            sample_ingest_response,
            sample_ingest_response,
            *_search_side_effects(7, sample_search_response),
        ]

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.side_effect = _make_three_phase_side_effect(
                structure_json=structure_json,
            )
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
        sample_list_documents_response,
        sample_per_doc_download_responses,
        sample_ingest_response,
        sample_search_response,
    ):
        """
        Verifies [key-documents:ITS-02] - Key documents in stored result

        Given: A review completes with key_documents from structure call
        When: Result is stored
        Then: Result JSON includes review.key_documents array with expected structure
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,        # Phase 1
            sample_list_documents_response,     # Phase 2: list_application_documents
            *sample_per_doc_download_responses,  # Phase 3: download_document x3
            sample_ingest_response,             # Phase 4: ingest x3
            sample_ingest_response,
            sample_ingest_response,
            *_search_side_effects(7, sample_search_response),
        ]

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            # Default SAMPLE_STRUCTURE_JSON includes 1 key_document
            mock_client_inst.messages.create.side_effect = _make_three_phase_side_effect()
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


# ---------------------------------------------------------------------------
# review-scope-control tests
# ---------------------------------------------------------------------------


class TestOrchestratorPassesToggleFlags:
    """
    Tests that AgentOrchestrator handles options parameter.

    With the new LLM filter phase, document selection is handled by the filter,
    not by toggle flags on the download call. These tests verify the workflow
    still completes when options are provided and that the new filter/download
    flow uses list_application_documents + per-doc download_document.

    Updated from [review-scope-control:AgentOrchestrator/TS-01] and [TS-02]
    """

    @pytest.mark.asyncio
    async def test_workflow_with_options_set(
        self, mock_mcp_client, mock_redis, sample_application_response,
        sample_list_documents_response, sample_per_doc_download_responses,
        sample_ingest_response, sample_search_response,
        monkeypatch,
    ):
        """
        Verifies workflow completes when options are provided.

        Given: Orchestrator created with options
        When: Workflow executes
        Then: Filter phase selects documents via LLM; per-doc downloads succeed
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        # Create options-like object
        from types import SimpleNamespace as _NS
        options = _NS(
            include_consultation_responses=True,
            include_public_comments=False,
        )

        mock_mcp_client.call_tool = AsyncMock(side_effect=[
            sample_application_response,        # Phase 1: get_application_details
            sample_list_documents_response,     # Phase 2: list_application_documents
            *sample_per_doc_download_responses,  # Phase 3: download_document x3
            sample_ingest_response,             # Phase 4: ingest doc1
            sample_ingest_response,             # Phase 4: ingest doc2
            sample_ingest_response,             # Phase 4: ingest doc3
        ] + _search_side_effects(7, sample_search_response))

        with patch("src.agent.orchestrator.anthropic.Anthropic") as mock_anthropic_cls:
            mock_client_instance = MagicMock()
            mock_client_instance.messages.create.side_effect = _make_three_phase_side_effect()
            mock_anthropic_cls.return_value = mock_client_instance

            orchestrator = AgentOrchestrator(
                review_id="rev_toggle_test",
                application_ref="25/01178/REM",
                mcp_client=mock_mcp_client,
                redis_client=mock_redis,
                options=options,
            )

            result = await orchestrator.run()

        assert result.success is True

        # Verify list_application_documents was called (second call_tool invocation)
        list_call = mock_mcp_client.call_tool.call_args_list[1]
        assert list_call[0][0] == "list_application_documents"

        # Verify per-doc download_document calls (3rd, 4th, 5th invocations)
        for i in range(2, 5):
            dl_call = mock_mcp_client.call_tool.call_args_list[i]
            assert dl_call[0][0] == "download_document"

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_default_options_uses_filter_phase(
        self, mock_mcp_client, mock_redis, sample_application_response,
        sample_list_documents_response, sample_per_doc_download_responses,
        sample_ingest_response, sample_search_response,
        monkeypatch,
    ):
        """
        Verifies workflow uses filter phase with default options.

        Given: Orchestrator created with no options
        When: Workflow executes
        Then: Filter phase lists and selects documents; per-doc downloads succeed
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        mock_mcp_client.call_tool = AsyncMock(side_effect=[
            sample_application_response,        # Phase 1
            sample_list_documents_response,     # Phase 2: list_application_documents
            *sample_per_doc_download_responses,  # Phase 3: download_document x3
            sample_ingest_response,             # Phase 4: ingest x3
            sample_ingest_response,
            sample_ingest_response,
        ] + _search_side_effects(7, sample_search_response))

        with patch("src.agent.orchestrator.anthropic.Anthropic") as mock_anthropic_cls:
            mock_client_instance = MagicMock()
            mock_client_instance.messages.create.side_effect = _make_three_phase_side_effect()
            mock_anthropic_cls.return_value = mock_client_instance

            orchestrator = AgentOrchestrator(
                review_id="rev_default_test",
                application_ref="25/01178/REM",
                mcp_client=mock_mcp_client,
                redis_client=mock_redis,
                # No options passed
            )

            result = await orchestrator.run()

        assert result.success is True

        # Verify list_application_documents was called
        list_call = mock_mcp_client.call_tool.call_args_list[1]
        assert list_call[0][0] == "list_application_documents"

        # Verify no download_all_documents calls (replaced by per-doc downloads)
        tool_names = [c[0][0] for c in mock_mcp_client.call_tool.call_args_list]
        assert "download_all_documents" not in tool_names

        await orchestrator.close()


# ---------------------------------------------------------------------------
# S3 Document Storage tests
# ---------------------------------------------------------------------------


def _make_s3_backend_mock():
    """Create a mock S3 storage backend for testing."""
    backend = MagicMock()
    type(backend).is_remote = PropertyMock(return_value=True)
    backend.upload.return_value = None
    backend.public_url.side_effect = (
        lambda key: f"https://test-bucket.nyc3.digitaloceanspaces.com/{key}"
    )
    backend.delete_local.return_value = None
    return backend


def _make_local_backend_mock():
    """Create a mock local storage backend for testing."""
    backend = MagicMock()
    type(backend).is_remote = PropertyMock(return_value=False)
    backend.upload.return_value = None
    backend.public_url.return_value = None
    backend.delete_local.return_value = None
    return backend


@pytest.fixture
def sample_s3_per_doc_download_responses():
    """Sample per-document download responses for S3 mode."""
    return [
        {
            "status": "success",
            "file_path": "/data/raw/25_01178_REM/001_Transport Assessment.pdf",
            "file_size": 150000,
        },
        {
            "status": "success",
            "file_path": "/data/raw/25_01178_REM/002_Site Plan.pdf",
            "file_size": 80000,
        },
        {
            "status": "success",
            "file_path": "/data/raw/25_01178_REM/003_Design Statement.pdf",
            "file_size": 120000,
        },
    ]


# Selected documents used by S3 tests (contains URLs that get rewritten)
S3_SELECTED_DOCUMENTS = [
    {
        "document_id": "doc1",
        "description": "Transport Assessment",
        "document_type": "Transport Assessment",
        "url": "https://planningregister.cherwell.gov.uk/Document/Download?id=doc1",
        "date_published": "2024-01-01",
    },
    {
        "document_id": "doc2",
        "description": "Site Plan",
        "document_type": "Plans - Site Plan",
        "url": "https://planningregister.cherwell.gov.uk/Document/Download?id=doc2",
        "date_published": "2024-01-01",
    },
    {
        "document_id": "doc3",
        "description": "Design and Access Statement",
        "document_type": "Design and Access Statement",
        "url": "https://planningregister.cherwell.gov.uk/Document/Download?id=doc3",
        "date_published": "2024-01-01",
    },
]


class TestS3StorageDownloadPhase:
    """
    Tests for S3 storage integration in download phase.

    Implements [s3-document-storage:AgentOrchestrator/TS-01], [TS-04], [TS-05]
    Implements [s3-document-storage:DownloadPhase/TS-01], [TS-02]
    """

    @pytest.mark.asyncio
    async def test_s3_upload_after_download_with_url_rewriting(
        self,
        mock_mcp_client,
        mock_redis,
        sample_application_response,
        sample_s3_per_doc_download_responses,
    ):
        """
        Verifies [s3-document-storage:AgentOrchestrator/TS-01] and [DownloadPhase/TS-01]

        Given: S3 backend configured
        When: Download phase completes
        Then: Each file uploaded to S3, URLs rewritten to S3 public URLs
        """
        backend = _make_s3_backend_mock()

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,
            *sample_s3_per_doc_download_responses,  # download_document x3
        ]

        orchestrator = AgentOrchestrator(
            review_id="rev_test_s3",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
            storage_backend=backend,
        )
        await orchestrator.initialize()

        await orchestrator._phase_fetch_metadata()
        orchestrator._selected_documents = S3_SELECTED_DOCUMENTS[:]
        await orchestrator._phase_download_documents()

        # Verify upload was called for each file
        assert backend.upload.call_count == 3
        upload_calls = backend.upload.call_args_list
        assert upload_calls[0][0] == (
            Path("/data/raw/25_01178_REM/001_Transport Assessment.pdf"),
            "25_01178_REM/001_Transport Assessment.pdf",
        )
        assert upload_calls[1][0] == (
            Path("/data/raw/25_01178_REM/002_Site Plan.pdf"),
            "25_01178_REM/002_Site Plan.pdf",
        )
        assert upload_calls[2][0] == (
            Path("/data/raw/25_01178_REM/003_Design Statement.pdf"),
            "25_01178_REM/003_Design Statement.pdf",
        )

        # Verify URLs were rewritten to S3 URLs
        meta = orchestrator._ingestion_result.document_metadata
        assert len(meta) == 3
        for _file_path, doc_meta in meta.items():
            assert "test-bucket.nyc3.digitaloceanspaces.com" in doc_meta["url"]
            assert "cherwell.gov.uk" not in doc_meta["url"]

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_s3_upload_failure_keeps_cherwell_url(
        self,
        mock_mcp_client,
        mock_redis,
        sample_application_response,
        sample_s3_per_doc_download_responses,
    ):
        """
        Verifies [s3-document-storage:AgentOrchestrator/TS-04] - S3 upload failure

        Given: S3 backend configured, upload fails for one file
        When: Download phase completes
        Then: Failed file keeps Cherwell URL, other files have S3 URLs, no exception raised
        """
        backend = _make_s3_backend_mock()
        # Second upload fails
        backend.upload.side_effect = [
            None,  # doc1 succeeds
            StorageUploadError(key="25_01178_REM/002_Site Plan.pdf", attempts=3),
            None,  # doc3 succeeds
        ]

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,
            *sample_s3_per_doc_download_responses,  # download_document x3
        ]

        orchestrator = AgentOrchestrator(
            review_id="rev_test_s3_fail",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
            storage_backend=backend,
        )
        await orchestrator.initialize()

        await orchestrator._phase_fetch_metadata()
        orchestrator._selected_documents = S3_SELECTED_DOCUMENTS[:]
        await orchestrator._phase_download_documents()

        meta = orchestrator._ingestion_result.document_metadata

        # doc1 and doc3 should have S3 URLs
        doc1_path = "/data/raw/25_01178_REM/001_Transport Assessment.pdf"
        doc3_path = "/data/raw/25_01178_REM/003_Design Statement.pdf"
        assert "test-bucket.nyc3.digitaloceanspaces.com" in meta[doc1_path]["url"]
        assert "test-bucket.nyc3.digitaloceanspaces.com" in meta[doc3_path]["url"]

        # doc2 should keep Cherwell URL (upload failed)
        doc2_path = "/data/raw/25_01178_REM/002_Site Plan.pdf"
        assert "cherwell.gov.uk" in meta[doc2_path]["url"]

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_local_backend_passthrough(
        self,
        mock_mcp_client,
        mock_redis,
        sample_application_response,
        sample_per_doc_download_responses,
    ):
        """
        Verifies [s3-document-storage:AgentOrchestrator/TS-05] and [DownloadPhase/TS-02]

        Given: Local storage backend (no S3)
        When: Download phase runs
        Then: No upload calls, Cherwell URLs preserved
        """
        backend = _make_local_backend_mock()

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,
            *sample_per_doc_download_responses,  # download_document x3
        ]

        orchestrator = AgentOrchestrator(
            review_id="rev_test_local",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
            storage_backend=backend,
        )
        await orchestrator.initialize()

        await orchestrator._phase_fetch_metadata()
        orchestrator._selected_documents = S3_SELECTED_DOCUMENTS[:]
        await orchestrator._phase_download_documents()

        # Verify no upload calls
        backend.upload.assert_not_called()

        # Verify Cherwell URLs preserved
        meta = orchestrator._ingestion_result.document_metadata
        for _file_path, doc_meta in meta.items():
            assert "cherwell.gov.uk" in doc_meta["url"]

        await orchestrator.close()


class TestS3StorageIngestionPhase:
    """
    Tests for S3 storage integration in ingestion phase.

    Implements [s3-document-storage:AgentOrchestrator/TS-02], [TS-03]
    """

    @pytest.mark.asyncio
    async def test_local_cleanup_after_successful_ingestion(
        self,
        mock_mcp_client,
        mock_redis,
        sample_ingest_response,
    ):
        """
        Verifies [s3-document-storage:AgentOrchestrator/TS-02] - Local cleanup after ingestion

        Given: S3 backend configured, files uploaded
        When: Ingestion succeeds for a document
        Then: Local temp file is deleted via backend.delete_local()
        """
        backend = _make_s3_backend_mock()

        mock_mcp_client.call_tool.side_effect = [
            sample_ingest_response,  # doc1 ingested
            sample_ingest_response,  # doc2 ingested
        ]

        orchestrator = AgentOrchestrator(
            review_id="rev_test_cleanup",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
            storage_backend=backend,
        )
        await orchestrator.initialize()

        orchestrator._ingestion_result = DocumentIngestionResult(
            documents_fetched=2,
            document_paths=[
                "/data/raw/25_01178_REM/001_Transport Assessment.pdf",
                "/data/raw/25_01178_REM/002_Site Plan.pdf",
            ],
        )

        await orchestrator._phase_ingest_documents()

        # Verify delete_local was called for each successfully ingested file
        assert backend.delete_local.call_count == 2
        delete_paths = [str(c[0][0]) for c in backend.delete_local.call_args_list]
        assert "/data/raw/25_01178_REM/001_Transport Assessment.pdf" in delete_paths
        assert "/data/raw/25_01178_REM/002_Site Plan.pdf" in delete_paths

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_retain_file_on_ingestion_failure(
        self,
        mock_mcp_client,
        mock_redis,
        sample_ingest_response,
    ):
        """
        Verifies [s3-document-storage:AgentOrchestrator/TS-03] - Retain file on ingestion failure

        Given: S3 backend configured
        When: Ingestion fails for a document
        Then: Local temp file is NOT deleted
        """
        backend = _make_s3_backend_mock()

        mock_mcp_client.call_tool.side_effect = [
            sample_ingest_response,  # doc1 succeeds
            MCPToolError("ingest_document", "OCR failed"),  # doc2 fails
        ]

        orchestrator = AgentOrchestrator(
            review_id="rev_test_retain",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
            storage_backend=backend,
        )
        await orchestrator.initialize()

        orchestrator._ingestion_result = DocumentIngestionResult(
            documents_fetched=2,
            document_paths=[
                "/data/raw/25_01178_REM/001_Transport Assessment.pdf",
                "/data/raw/25_01178_REM/002_Site Plan.pdf",
            ],
        )

        await orchestrator._phase_ingest_documents()

        # Only one delete_local call (for the successful one)
        assert backend.delete_local.call_count == 1
        deleted_path = str(backend.delete_local.call_args_list[0][0][0])
        assert "001_Transport Assessment" in deleted_path

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_no_cleanup_with_local_backend(
        self,
        mock_mcp_client,
        mock_redis,
        sample_ingest_response,
    ):
        """
        Verifies local backend does not trigger file cleanup.

        Given: Local storage backend (no S3)
        When: Ingestion succeeds
        Then: delete_local is NOT called (files remain on persistent volume)
        """
        backend = _make_local_backend_mock()

        mock_mcp_client.call_tool.side_effect = [
            sample_ingest_response,
            sample_ingest_response,
        ]

        orchestrator = AgentOrchestrator(
            review_id="rev_test_local_ingest",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
            storage_backend=backend,
        )
        await orchestrator.initialize()

        orchestrator._ingestion_result = DocumentIngestionResult(
            documents_fetched=2,
            document_paths=[
                "/data/raw/25_01178_REM/001_Transport Assessment.pdf",
                "/data/raw/25_01178_REM/002_Site Plan.pdf",
            ],
        )

        await orchestrator._phase_ingest_documents()

        backend.delete_local.assert_not_called()

        await orchestrator.close()


class TestTwoPhaseReviewGeneration:
    """
    Tests for two-phase review generation.

    Implements [structured-review-output:AgentOrchestrator/TS-01] through [TS-05]
    Implements [structured-review-output:ITS-01] through [ITS-03]
    """

    @pytest.mark.asyncio
    async def test_two_phase_success(
        self, mock_mcp_client, mock_redis, monkeypatch,
    ):
        """
        Verifies [structured-review-output:AgentOrchestrator/TS-01] - Two-phase success.

        Given: Claude returns valid JSON for structure call, valid markdown for report call
        When: _phase_generate_review() completes
        Then: All structured fields populated from JSON; full_markdown from report call
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        structure_json = json.dumps({
            "overall_rating": "red",
            "summary": "The application fails to provide adequate cycling infrastructure and is non-compliant with key transport policies.",
            "aspects": [
                {"name": "Cycle Parking", "rating": "amber", "key_issue": "Design unverified",
                 "analysis": "Minimum spaces."},
                {"name": "Cycle Routes", "rating": "red", "key_issue": "No connections",
                 "analysis": "No routes."},
                {"name": "Junctions", "rating": "red", "key_issue": "No priority",
                 "analysis": "Cars only."},
                {"name": "Permeability", "rating": "red", "key_issue": "Car-only",
                 "analysis": "No permeability."},
                {"name": "Policy Compliance", "rating": "red", "key_issue": "Fails NPPF",
                 "analysis": "Non-compliant."},
            ],
            "policy_compliance": [
                {"requirement": "Sustainable transport", "policy_source": "NPPF 115",
                 "compliant": False, "notes": "Car-based"},
                {"requirement": "Safe access", "policy_source": "NPPF 115(b)",
                 "compliant": False, "notes": None},
            ],
            "recommendations": ["Provide cycle track", "Add Sheffield stands"],
            "suggested_conditions": ["Submit cycle parking details"],
            "key_documents": [
                {"title": "Transport Assessment", "category": "Transport & Access",
                 "summary": "Traffic analysis.", "url": "https://example.com/ta.pdf"},
            ],
        })

        report_md = "# Cycle Advocacy Review: 25/01178/REM\n## Assessment Summary\n**Overall Rating:** RED"

        orchestrator = AgentOrchestrator(
            review_id="rev_2phase_test",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )
        await orchestrator.initialize()
        orchestrator._application = ApplicationMetadata(
            reference="25/01178/REM", address="Test", proposal="Test",
        )
        orchestrator._ingestion_result = DocumentIngestionResult(
            documents_fetched=1, documents_ingested=1,
            document_paths=["/data/ta.pdf"],
            document_metadata={"/data/ta.pdf": {"description": "TA", "document_type": "TA", "url": None}},
        )

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.side_effect = _make_review_side_effect(
                structure_json=structure_json, markdown=report_md,
            )
            MockAnthropic.return_value = mock_client_inst

            await orchestrator._phase_generate_review()

        review = orchestrator._review_result.review

        # Structured fields from JSON
        assert review["overall_rating"] == "red"
        assert review["aspects"] is not None
        assert len(review["aspects"]) == 5
        assert review["aspects"][0]["name"] == "Cycle Parking"
        assert review["aspects"][0]["rating"] == "amber"
        assert review["aspects"][1]["rating"] == "red"
        assert review["policy_compliance"] is not None
        assert len(review["policy_compliance"]) == 2
        assert review["policy_compliance"][0]["compliant"] is False
        assert review["recommendations"] == ["Provide cycle track", "Add Sheffield stands"]
        assert review["suggested_conditions"] == ["Submit cycle parking details"]
        assert review["key_documents"] is not None
        assert len(review["key_documents"]) == 1
        assert review["key_documents"][0]["title"] == "Transport Assessment"

        # Markdown from report call
        assert review["full_markdown"] == report_md
        assert "RED" in review["full_markdown"]

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_structure_call_invalid_json_fallback(
        self, mock_mcp_client, mock_redis, monkeypatch,
    ):
        """
        Verifies [structured-review-output:AgentOrchestrator/TS-02] - Structure call invalid JSON.

        Given: Claude returns non-JSON text for structure call
        When: Structure call parsing fails
        Then: Falls back to single markdown call; structured fields are None
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        fallback_md = "# Review\n**Overall Rating:** AMBER\nSome review content."

        orchestrator = AgentOrchestrator(
            review_id="rev_fallback_test",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )
        await orchestrator.initialize()
        orchestrator._application = ApplicationMetadata(
            reference="25/01178/REM", address="Test", proposal="Test",
        )
        orchestrator._ingestion_result = DocumentIngestionResult()

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            # First call (structure) returns invalid JSON, second call (fallback) returns markdown
            mock_client_inst.messages.create.side_effect = [
                _make_claude_response(text="This is not JSON at all"),
                _make_claude_response(text=fallback_md),
            ]
            MockAnthropic.return_value = mock_client_inst

            await orchestrator._phase_generate_review()

        review = orchestrator._review_result.review

        assert review["full_markdown"] == fallback_md
        assert review["overall_rating"] == "amber"
        # Structured fields are None in fallback
        assert review["aspects"] is None
        assert review["policy_compliance"] is None
        assert review["recommendations"] is None
        assert review["suggested_conditions"] is None
        assert review["key_documents"] is None
        assert orchestrator._review_result.success is True

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_structure_call_api_error_fallback(
        self, mock_mcp_client, mock_redis, monkeypatch,
    ):
        """
        Verifies [structured-review-output:AgentOrchestrator/TS-03] - Structure call API error.

        Given: Claude API raises error on structure call
        When: Structure call fails
        Then: Falls back to single markdown call; review still completes
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        import anthropic as anthropic_mod

        fallback_md = "# Review\n**Overall Rating:** RED\nFallback review."

        orchestrator = AgentOrchestrator(
            review_id="rev_api_err_test",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )
        await orchestrator.initialize()
        orchestrator._application = ApplicationMetadata(
            reference="25/01178/REM", address="Test", proposal="Test",
        )
        orchestrator._ingestion_result = DocumentIngestionResult()

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            # First call raises API error, second call (fallback) works
            mock_client_inst.messages.create.side_effect = [
                anthropic_mod.APIStatusError(
                    message="Rate limited",
                    response=MagicMock(status_code=429),
                    body=None,
                ),
                _make_claude_response(text=fallback_md),
            ]
            MockAnthropic.return_value = mock_client_inst

            await orchestrator._phase_generate_review()

        review = orchestrator._review_result.review
        assert review["full_markdown"] == fallback_md
        assert review["overall_rating"] == "red"
        assert review["aspects"] is None  # Fallback has no structured fields
        assert orchestrator._review_result.success is True

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_token_usage_tracked(
        self, mock_mcp_client, mock_redis, monkeypatch,
    ):
        """
        Verifies [structured-review-output:AgentOrchestrator/TS-04] - Token usage tracked.

        Given: Both calls complete successfully
        When: Review result metadata examined
        Then: input_tokens and output_tokens are the sum of both calls
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        orchestrator = AgentOrchestrator(
            review_id="rev_token_test",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )
        await orchestrator.initialize()
        orchestrator._application = ApplicationMetadata(
            reference="25/01178/REM", address="Test", proposal="Test",
        )
        orchestrator._ingestion_result = DocumentIngestionResult()

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.side_effect = _make_review_side_effect(
                structure_tokens=(500, 1500),
                report_tokens=(1000, 3000),
            )
            MockAnthropic.return_value = mock_client_inst

            await orchestrator._phase_generate_review()

        review = orchestrator._review_result.review
        assert review["input_tokens"] == 500 + 1000  # sum of both
        assert review["output_tokens"] == 1500 + 3000

        metadata = orchestrator._review_result.metadata
        assert metadata["total_tokens_used"] == 500 + 1500 + 1000 + 3000

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_review_dict_shape_preserved(
        self, mock_mcp_client, mock_redis, monkeypatch,
    ):
        """
        Verifies [structured-review-output:AgentOrchestrator/TS-05] - Review dict shape preserved.

        Given: Two-phase completes
        When: Review dict examined
        Then: Contains all expected keys
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        orchestrator = AgentOrchestrator(
            review_id="rev_shape_test",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )
        await orchestrator.initialize()
        orchestrator._application = ApplicationMetadata(
            reference="25/01178/REM", address="Test", proposal="Test",
        )
        orchestrator._ingestion_result = DocumentIngestionResult()

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.side_effect = _make_review_side_effect()
            MockAnthropic.return_value = mock_client_inst

            await orchestrator._phase_generate_review()

        review = orchestrator._review_result.review
        expected_keys = {
            "overall_rating", "key_documents", "aspects", "policy_compliance",
            "recommendations", "suggested_conditions", "full_markdown",
            "summary", "model", "input_tokens", "output_tokens",
        }
        assert set(review.keys()) == expected_keys

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_structure_to_report_consistency(
        self, mock_mcp_client, mock_redis, monkeypatch,
    ):
        """
        Verifies [structured-review-output:ITS-01] - Structure-to-report consistency.

        Given: Structure call returns JSON with known data
        When: Report call generates markdown
        Then: Structured fields and markdown both populated from same source
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        structure_json = json.dumps({
            "overall_rating": "green",
            "summary": "Excellent cycling provision throughout with safe routes and policy compliance.",
            "aspects": [
                {"name": "Cycle Parking", "rating": "green", "key_issue": "Meets standards",
                 "analysis": "Good provision."},
                {"name": "Cycle Routes", "rating": "green", "key_issue": "Connected",
                 "analysis": "Well connected."},
                {"name": "Junctions", "rating": "green", "key_issue": "Safe design",
                 "analysis": "Good junctions."},
                {"name": "Permeability", "rating": "green", "key_issue": "Excellent",
                 "analysis": "Fully permeable."},
                {"name": "Policy Compliance", "rating": "green", "key_issue": "Compliant",
                 "analysis": "Meets all policies."},
            ],
            "policy_compliance": [
                {"requirement": "Cycle parking", "policy_source": "LTN 1/20",
                 "compliant": True, "notes": "Sheffield stands provided"},
            ],
            "recommendations": ["Consider covered cycle parking"],
            "suggested_conditions": [],
            "key_documents": [],
        })

        report_md = "# Cycle Advocacy Review\n**Overall Rating:** GREEN\n## Assessment Summary\n..."

        orchestrator = AgentOrchestrator(
            review_id="rev_consistency_test",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )
        await orchestrator.initialize()
        orchestrator._application = ApplicationMetadata(reference="25/01178/REM")
        orchestrator._ingestion_result = DocumentIngestionResult()

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.side_effect = _make_review_side_effect(
                structure_json=structure_json, markdown=report_md,
            )
            MockAnthropic.return_value = mock_client_inst

            await orchestrator._phase_generate_review()

        review = orchestrator._review_result.review
        assert review["overall_rating"] == "green"
        assert review["aspects"][0]["rating"] == "green"
        assert review["policy_compliance"][0]["compliant"] is True
        assert review["full_markdown"] == report_md

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_fallback_produces_valid_review(
        self, mock_mcp_client, mock_redis, monkeypatch,
    ):
        """
        Verifies [structured-review-output:ITS-02] - Fallback produces valid review.

        Given: Structure call mocked to return invalid JSON
        When: Full _phase_generate_review() executes
        Then: Review completes with full_markdown; structured fields are None
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        orchestrator = AgentOrchestrator(
            review_id="rev_fallback_valid_test",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )
        await orchestrator.initialize()
        orchestrator._application = ApplicationMetadata(reference="25/01178/REM")
        orchestrator._ingestion_result = DocumentIngestionResult()

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.side_effect = [
                _make_claude_response(text="not json"),
                _make_claude_response(text="# Review\n**Overall Rating:** GREEN\nContent."),
            ]
            MockAnthropic.return_value = mock_client_inst

            await orchestrator._phase_generate_review()

        review = orchestrator._review_result.review
        assert review["full_markdown"] is not None
        assert review["overall_rating"] is not None
        assert review["aspects"] is None
        assert orchestrator._review_result.success is True

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_review_dict_matches_api_schema(
        self, mock_mcp_client, mock_redis, monkeypatch,
    ):
        """
        Verifies [structured-review-output:ITS-03] - Review dict matches API schema.

        Given: Two-phase completes
        When: Review dict passed to ReviewContent Pydantic model
        Then: Model validates without error
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        from src.api.schemas import ReviewContent

        orchestrator = AgentOrchestrator(
            review_id="rev_schema_test",
            application_ref="25/01178/REM",
            mcp_client=mock_mcp_client,
            redis_client=mock_redis,
        )
        await orchestrator.initialize()
        orchestrator._application = ApplicationMetadata(reference="25/01178/REM")
        orchestrator._ingestion_result = DocumentIngestionResult()

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.side_effect = _make_review_side_effect()
            MockAnthropic.return_value = mock_client_inst

            await orchestrator._phase_generate_review()

        review = orchestrator._review_result.review
        # Should validate without error
        content = ReviewContent.model_validate(review)
        assert content.overall_rating is not None
        assert content.aspects is not None
        assert content.full_markdown is not None

        await orchestrator.close()


# ---------------------------------------------------------------------------
# Error message extraction tests (document ingestion bug fix)
# ---------------------------------------------------------------------------


class TestIngestErrorExtraction:
    """Tests for improved error message extraction during document ingestion."""

    @pytest.mark.asyncio
    async def test_ingest_error_key_extraction(
        self,
        mock_mcp_client,
        mock_redis,
        monkeypatch,
        sample_application_response,
        sample_list_documents_response,
        sample_per_doc_download_responses,
        sample_search_response,
    ):
        """
        When call_tool returns {"error": "Connection lost"} (no "status"/"message"),
        the orchestrator should record "Connection lost" as the error, not "Unknown error".
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,        # Phase 1
            sample_list_documents_response,     # Phase 2: list_application_documents
            *sample_per_doc_download_responses,  # Phase 3: download_document x3
            {"error": "Connection lost"},                     # Phase 4: doc 1: error key only
            {"status": "success", "chunks_created": 10},      # doc 2 succeeds
            {"status": "success", "chunks_created": 5},       # doc 3 succeeds
            *_search_side_effects(7, sample_search_response),
        ]

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.side_effect = _make_three_phase_side_effect()
            MockAnthropic.return_value = mock_client_inst

            orchestrator = AgentOrchestrator(
                review_id="rev_error_key_test",
                application_ref="25/01178/REM",
                mcp_client=mock_mcp_client,
                redis_client=mock_redis,
            )

            result = await orchestrator.run()

        assert result.success is True
        assert orchestrator._ingestion_result.documents_ingested == 2

        errors = orchestrator.progress.state.errors_encountered
        assert len(errors) == 1
        assert "Connection lost" in errors[0]["error"]
        assert "Unknown error" not in errors[0]["error"]

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_ingest_empty_dict_response(
        self,
        mock_mcp_client,
        mock_redis,
        monkeypatch,
        sample_application_response,
        sample_list_documents_response,
        sample_per_doc_download_responses,
        sample_search_response,
    ):
        """
        When call_tool returns {} (empty dict, e.g. from transport failure),
        the orchestrator should record "Unknown error" and count it as failed
        without crashing.
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,        # Phase 1
            sample_list_documents_response,     # Phase 2: list_application_documents
            *sample_per_doc_download_responses,  # Phase 3: download_document x3
            {},                                               # Phase 4: doc 1: empty dict
            {"status": "success", "chunks_created": 10},      # doc 2 succeeds
            {"status": "success", "chunks_created": 5},       # doc 3 succeeds
            *_search_side_effects(7, sample_search_response),
        ]

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.side_effect = _make_three_phase_side_effect()
            MockAnthropic.return_value = mock_client_inst

            orchestrator = AgentOrchestrator(
                review_id="rev_empty_dict_test",
                application_ref="25/01178/REM",
                mcp_client=mock_mcp_client,
                redis_client=mock_redis,
            )

            result = await orchestrator.run()

        assert result.success is True
        assert orchestrator._ingestion_result.documents_ingested == 2

        errors = orchestrator.progress.state.errors_encountered
        assert len(errors) == 1
        assert "Unknown error" in errors[0]["error"]

        await orchestrator.close()


# ---------------------------------------------------------------------------
# Verification phase tests
# ---------------------------------------------------------------------------


class TestVerificationPhase:
    """
    Tests for post-generation verification.

    Implements [review-workflow-redesign:AgentOrchestrator/TS-05]
    """

    @pytest.mark.asyncio
    async def test_verification_populates_metadata(
        self,
        mock_mcp_client,
        mock_redis,
        monkeypatch,
        sample_application_response,
        sample_list_documents_response,
        sample_per_doc_download_responses,
        sample_ingest_response,
        sample_search_response,
    ):
        """
        Verifies [review-workflow-redesign:AgentOrchestrator/TS-05]

        Given: Review generated successfully with evidence
        When: Phase 7 runs
        Then: ReviewResult.metadata contains verification dict
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,
            sample_list_documents_response,
            *sample_per_doc_download_responses,
            sample_ingest_response,
            sample_ingest_response,
            sample_ingest_response,
            *_search_side_effects(7, sample_search_response),
        ]

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            mock_client_inst.messages.create.side_effect = _make_three_phase_side_effect()
            MockAnthropic.return_value = mock_client_inst

            orchestrator = AgentOrchestrator(
                review_id="rev_test123",
                application_ref="25/01178/REM",
                mcp_client=mock_mcp_client,
                redis_client=mock_redis,
            )

            result = await orchestrator.run()

        assert result.success is True
        assert "verification" in result.metadata
        verification = result.metadata["verification"]
        assert verification["status"] == "verified"
        assert verification["verified_claims"] == 3
        assert verification["unverified_claims"] == 0
        assert verification["total_claims"] == 3
        assert len(verification["details"]) == 3
        assert "duration_seconds" in verification

        await orchestrator.close()

    @pytest.mark.asyncio
    async def test_verification_failure_does_not_fail_review(
        self,
        mock_mcp_client,
        mock_redis,
        monkeypatch,
        sample_application_response,
        sample_list_documents_response,
        sample_per_doc_download_responses,
        sample_ingest_response,
        sample_search_response,
    ):
        """
        Verifies [review-workflow-redesign:AgentOrchestrator/TS-08]

        Given: Verification API call fails
        When: Phase 7 runs
        Then: Review still succeeds, verification metadata absent
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,
            sample_list_documents_response,
            *sample_per_doc_download_responses,
            sample_ingest_response,
            sample_ingest_response,
            sample_ingest_response,
            *_search_side_effects(7, sample_search_response),
        ]

        import anthropic as anthropic_module

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic:
            mock_client_inst = MagicMock()
            # filter + queries + structure + report succeed, verification fails
            mock_client_inst.messages.create.side_effect = [
                _make_filter_response(),
                _make_query_response(),
                _make_claude_response(text=SAMPLE_STRUCTURE_JSON),
                _make_claude_response(text="# Review\n**Overall Rating:** AMBER"),
                anthropic_module.APIError(message="Service unavailable", request=MagicMock(), body=None),
            ]
            MockAnthropic.return_value = mock_client_inst

            orchestrator = AgentOrchestrator(
                review_id="rev_test123",
                application_ref="25/01178/REM",
                mcp_client=mock_mcp_client,
                redis_client=mock_redis,
            )

            result = await orchestrator.run()

        # Review should still succeed even though verification failed
        assert result.success is True
        assert "verification" not in result.metadata

        await orchestrator.close()
