"""
Agent Orchestrator for coordinating the review workflow.

Implements [agent-integration:FR-001] - Establishes and maintains MCP server connections
Implements [agent-integration:FR-002] - Orchestrates complete review workflow
Implements [agent-integration:NFR-001] - Workflow completes within time limits
Implements [agent-integration:NFR-005] - Handles partial failures gracefully

Implements:
- [agent-integration:AgentOrchestrator/TS-01] Successful MCP connections
- [agent-integration:AgentOrchestrator/TS-02] Reconnection on transient failure
- [agent-integration:AgentOrchestrator/TS-03] Complete workflow execution
- [agent-integration:AgentOrchestrator/TS-04] Scraper failure handling
- [agent-integration:AgentOrchestrator/TS-05] Partial document ingestion
- [agent-integration:AgentOrchestrator/TS-06] Cancellation handling
- [agent-integration:AgentOrchestrator/TS-07] State persistence
- [agent-integration:AgentOrchestrator/TS-08] All servers unavailable
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as redis
import structlog

from src.agent.mcp_client import MCPClientManager, MCPConnectionError, MCPToolError
from src.agent.progress import ProgressTracker, ReviewPhase

logger = structlog.get_logger(__name__)


@dataclass
class ApplicationMetadata:
    """Metadata about a planning application."""

    reference: str
    address: str | None = None
    proposal: str | None = None
    applicant: str | None = None
    status: str | None = None
    date_validated: str | None = None
    consultation_end: str | None = None
    documents: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DocumentIngestionResult:
    """Result of document ingestion."""

    documents_fetched: int = 0
    documents_ingested: int = 0
    failed_documents: list[dict[str, Any]] = field(default_factory=list)
    document_paths: list[str] = field(default_factory=list)


@dataclass
class ReviewResult:
    """Complete review result."""

    review_id: str
    application_ref: str
    application: ApplicationMetadata | None = None
    review: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    success: bool = False
    error: str | None = None


class OrchestratorError(Exception):
    """Error during orchestration."""

    def __init__(self, message: str, phase: ReviewPhase | None = None, recoverable: bool = False):
        self.message = message
        self.phase = phase
        self.recoverable = recoverable
        super().__init__(message)


class AgentOrchestrator:
    """
    Central coordinator for the review workflow.

    Implements [agent-integration:AgentOrchestrator/TS-01] through [TS-08]

    Manages:
    - MCP client connections to all three servers
    - Execution of the 5-phase review workflow
    - Error handling and partial failure recovery
    - State persistence for restart recovery
    - Cancellation support
    """

    def __init__(
        self,
        review_id: str,
        application_ref: str,
        mcp_client: MCPClientManager | None = None,
        redis_client: redis.Redis | None = None,
    ) -> None:
        """
        Initialize the orchestrator.

        Args:
            review_id: The review job ID.
            application_ref: The planning application reference.
            mcp_client: Optional MCPClientManager (created if not provided).
            redis_client: Optional Redis client for state persistence.
        """
        self._review_id = review_id
        self._application_ref = application_ref
        self._mcp_client = mcp_client
        self._redis = redis_client
        self._owns_mcp_client = mcp_client is None

        # Progress tracker
        self._progress = ProgressTracker(
            review_id=review_id,
            application_ref=application_ref,
            redis_client=redis_client,
        )

        # State
        self._application: ApplicationMetadata | None = None
        self._ingestion_result: DocumentIngestionResult | None = None
        self._initialized = False

    @property
    def progress(self) -> ProgressTracker:
        """Get the progress tracker."""
        return self._progress

    async def initialize(self) -> None:
        """
        Initialize the orchestrator and MCP connections.

        Implements [agent-integration:AgentOrchestrator/TS-01] - Successful MCP connections
        Implements [agent-integration:AgentOrchestrator/TS-08] - All servers unavailable

        Raises:
            OrchestratorError: If unable to initialize.
        """
        if self._initialized:
            return

        # Try to load existing state for recovery
        recovered = await self._progress.load_state()
        if recovered:
            logger.info(
                "Recovered workflow state",
                review_id=self._review_id,
                current_phase=self._progress.current_phase.value if self._progress.current_phase else None,
            )

        # Initialize MCP client if not provided
        if self._mcp_client is None:
            self._mcp_client = MCPClientManager()

        try:
            await self._mcp_client.initialize()
            self._initialized = True
            logger.info(
                "Orchestrator initialized",
                review_id=self._review_id,
                application_ref=self._application_ref,
            )
        except MCPConnectionError as e:
            raise OrchestratorError(
                f"Failed to connect to MCP servers: {e}",
                recoverable=False,
            )

    async def run(self) -> ReviewResult:
        """
        Execute the complete review workflow.

        Implements [agent-integration:AgentOrchestrator/TS-03] - Complete workflow execution
        Implements [agent-integration:AgentOrchestrator/TS-06] - Cancellation handling
        Implements [agent-integration:AgentOrchestrator/TS-07] - State persistence

        Returns:
            ReviewResult with the complete review or error information.
        """
        if not self._initialized:
            await self.initialize()

        result = ReviewResult(
            review_id=self._review_id,
            application_ref=self._application_ref,
        )

        try:
            await self._progress.start_workflow()

            # Determine starting phase (for recovery)
            start_phase = self._get_resume_phase()

            # Execute workflow phases
            phases = [
                (ReviewPhase.FETCHING_METADATA, self._phase_fetch_metadata),
                (ReviewPhase.DOWNLOADING_DOCUMENTS, self._phase_download_documents),
                (ReviewPhase.INGESTING_DOCUMENTS, self._phase_ingest_documents),
                (ReviewPhase.ANALYSING_APPLICATION, self._phase_analyse_application),
                (ReviewPhase.GENERATING_REVIEW, self._phase_generate_review),
            ]

            started = False
            for phase, handler in phases:
                # Skip phases we've already completed (recovery)
                if not started:
                    if phase == start_phase:
                        started = True
                    elif phase.value in self._progress.state.completed_phases:
                        continue
                    else:
                        started = True

                # Check for cancellation between phases
                if await self._progress.check_cancellation():
                    logger.info(
                        "Workflow cancelled",
                        review_id=self._review_id,
                        phase=phase.value,
                    )
                    result.error = "Workflow cancelled"
                    return result

                # Execute phase
                await self._progress.start_phase(phase)

                try:
                    await handler()
                except OrchestratorError as e:
                    await self._progress.record_error(phase, str(e))
                    if not e.recoverable:
                        raise
                    # Recoverable error - continue with degraded functionality
                    logger.warning(
                        "Recoverable error in phase",
                        review_id=self._review_id,
                        phase=phase.value,
                        error=str(e),
                    )

            # Build final result
            result.application = self._application
            result.success = True

            # Get workflow metadata
            completion_metadata = await self._progress.complete_workflow(success=True)
            result.metadata = completion_metadata

            logger.info(
                "Review workflow completed successfully",
                review_id=self._review_id,
                application_ref=self._application_ref,
            )

            return result

        except OrchestratorError as e:
            logger.error(
                "Workflow failed",
                review_id=self._review_id,
                error=str(e),
                phase=e.phase.value if e.phase else None,
            )
            result.error = str(e)
            result.metadata = await self._progress.complete_workflow(success=False)
            return result

        except Exception as e:
            logger.exception(
                "Unexpected error in workflow",
                review_id=self._review_id,
            )
            result.error = str(e)
            result.metadata = await self._progress.complete_workflow(success=False)
            return result

    def _get_resume_phase(self) -> ReviewPhase:
        """Determine which phase to resume from."""
        # If we have a current phase, resume from there
        if self._progress.current_phase:
            return self._progress.current_phase

        # Otherwise start from the beginning
        return ReviewPhase.FETCHING_METADATA

    async def _phase_fetch_metadata(self) -> None:
        """
        Phase 1: Fetch application metadata from Cherwell.

        Implements [agent-integration:AgentOrchestrator/TS-04] - Scraper failure handling
        """
        assert self._mcp_client is not None

        try:
            result = await self._mcp_client.call_tool(
                "get_application_details",
                {"reference": self._application_ref},
            )

            if result.get("status") == "error":
                raise OrchestratorError(
                    f"Failed to fetch application: {result.get('message', 'Unknown error')}",
                    phase=ReviewPhase.FETCHING_METADATA,
                    recoverable=False,
                )

            # Parse application metadata
            app_data = result.get("application", result)
            self._application = ApplicationMetadata(
                reference=self._application_ref,
                address=app_data.get("address"),
                proposal=app_data.get("proposal"),
                applicant=app_data.get("applicant"),
                status=app_data.get("status"),
                date_validated=app_data.get("date_validated"),
                consultation_end=app_data.get("consultation_end"),
                documents=app_data.get("documents", []),
            )

            logger.info(
                "Application metadata fetched",
                review_id=self._review_id,
                application_ref=self._application_ref,
                document_count=len(self._application.documents),
            )

        except MCPToolError as e:
            raise OrchestratorError(
                f"Scraper error: {e.message}",
                phase=ReviewPhase.FETCHING_METADATA,
                recoverable=False,
            )
        except MCPConnectionError as e:
            raise OrchestratorError(
                f"MCP connection error: {e}",
                phase=ReviewPhase.FETCHING_METADATA,
                recoverable=True,  # Can retry connection
            )

    async def _phase_download_documents(self) -> None:
        """Phase 2: Download application documents."""
        assert self._mcp_client is not None
        assert self._application is not None

        if not self._application.documents:
            logger.warning(
                "No documents to download",
                review_id=self._review_id,
                application_ref=self._application_ref,
            )
            self._ingestion_result = DocumentIngestionResult()
            return

        total_docs = len(self._application.documents)
        await self._progress.update_sub_progress(
            f"Downloading {total_docs} documents",
            current=0,
            total=total_docs,
        )

        try:
            result = await self._mcp_client.call_tool(
                "download_all_documents",
                {
                    "reference": self._application_ref,
                    "document_list": self._application.documents,
                },
            )

            downloaded = result.get("downloaded", [])
            failed = result.get("failed", [])

            self._ingestion_result = DocumentIngestionResult(
                documents_fetched=len(downloaded),
                document_paths=[d.get("path") for d in downloaded if d.get("path")],
                failed_documents=failed,
            )

            await self._progress.update_sub_progress(
                f"Downloaded {len(downloaded)} of {total_docs} documents",
                current=len(downloaded),
                total=total_docs,
            )

            logger.info(
                "Documents downloaded",
                review_id=self._review_id,
                downloaded=len(downloaded),
                failed=len(failed),
            )

        except MCPToolError as e:
            raise OrchestratorError(
                f"Download error: {e.message}",
                phase=ReviewPhase.DOWNLOADING_DOCUMENTS,
                recoverable=False,
            )

    async def _phase_ingest_documents(self) -> None:
        """
        Phase 3: Ingest documents into vector store.

        Implements [agent-integration:AgentOrchestrator/TS-05] - Partial document ingestion
        """
        assert self._mcp_client is not None

        if self._ingestion_result is None:
            self._ingestion_result = DocumentIngestionResult()

        if not self._ingestion_result.document_paths:
            logger.warning(
                "No documents to ingest",
                review_id=self._review_id,
            )
            return

        total_docs = len(self._ingestion_result.document_paths)
        ingested_count = 0
        failed_count = 0

        for i, doc_path in enumerate(self._ingestion_result.document_paths):
            await self._progress.update_sub_progress(
                f"Ingesting document {i + 1} of {total_docs}",
                current=i + 1,
                total=total_docs,
            )

            try:
                result = await self._mcp_client.call_tool(
                    "ingest_document",
                    {
                        "file_path": doc_path,
                        "application_ref": self._application_ref,
                    },
                )

                if result.get("status") in ("success", "already_ingested"):
                    ingested_count += 1
                else:
                    failed_count += 1
                    error_msg = result.get("message", "Unknown error")
                    await self._progress.record_error(
                        ReviewPhase.INGESTING_DOCUMENTS,
                        error_msg,
                        document=doc_path,
                    )

            except MCPToolError as e:
                # Log and continue - partial ingestion is acceptable
                failed_count += 1
                await self._progress.record_error(
                    ReviewPhase.INGESTING_DOCUMENTS,
                    str(e),
                    document=doc_path,
                )
                logger.warning(
                    "Document ingestion failed",
                    review_id=self._review_id,
                    document=doc_path,
                    error=str(e),
                )

        self._ingestion_result.documents_ingested = ingested_count

        logger.info(
            "Document ingestion complete",
            review_id=self._review_id,
            ingested=ingested_count,
            failed=failed_count,
        )

        # Fail if no documents were successfully ingested
        if ingested_count == 0 and total_docs > 0:
            raise OrchestratorError(
                "No documents could be ingested",
                phase=ReviewPhase.INGESTING_DOCUMENTS,
                recoverable=False,
            )

    async def _phase_analyse_application(self) -> None:
        """Phase 4: Analyse application using Claude."""
        # This will be implemented in Phase 3 (Task 7-12)
        # For now, placeholder that advances the workflow
        await self._progress.update_sub_progress("Analysing documents")

        logger.info(
            "Analysis phase (stub)",
            review_id=self._review_id,
        )

    async def _phase_generate_review(self) -> None:
        """Phase 5: Generate the review output."""
        # This will be implemented in Phase 4 (Task 14-16)
        # For now, placeholder that advances the workflow
        await self._progress.update_sub_progress("Generating review")

        logger.info(
            "Review generation phase (stub)",
            review_id=self._review_id,
        )

    async def close(self) -> None:
        """Clean up resources."""
        if self._owns_mcp_client and self._mcp_client is not None:
            await self._mcp_client.close()

        logger.info(
            "Orchestrator closed",
            review_id=self._review_id,
        )

    async def __aenter__(self) -> "AgentOrchestrator":
        """Async context manager entry."""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.close()
