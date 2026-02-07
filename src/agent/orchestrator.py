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

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import anthropic
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
        self._evidence_chunks: list[dict[str, Any]] = []
        self._review_result: ReviewResult | None = None
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

            # Build final result from Phase 5 output
            if hasattr(self, "_review_result") and self._review_result:
                result.application = self._review_result.application
                result.review = self._review_result.review
                result.metadata = self._review_result.metadata
            else:
                result.application = self._application
            result.success = True

            # Merge workflow metadata
            completion_metadata = await self._progress.complete_workflow(success=True)
            result.metadata.update(completion_metadata)

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
                {"application_ref": self._application_ref},
                timeout=60.0,
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

        await self._progress.update_sub_progress("Downloading application documents")

        try:
            # download_all_documents handles listing + downloading internally
            # Timeout increased to 1800s (30 min) to handle large applications
            # At 1 req/sec, can download up to 1800 documents before timeout
            result = await self._mcp_client.call_tool(
                "download_all_documents",
                {
                    "application_ref": self._application_ref,
                    "output_dir": "/data/raw",
                },
                timeout=1800.0,
            )

            results_list = result.get("downloads", [])
            downloaded = [r for r in results_list if r.get("success", True)]
            failed = [r for r in results_list if not r.get("success", True)]

            self._ingestion_result = DocumentIngestionResult(
                documents_fetched=len(downloaded),
                document_paths=[d.get("file_path") for d in downloaded if d.get("file_path")],
                failed_documents=failed,
            )

            total_docs = len(results_list)
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
                recoverable=True,  # Can continue with policy-only review
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
        """Phase 4: Analyse application by searching docs and policy."""
        assert self._mcp_client is not None

        await self._progress.update_sub_progress("Searching application documents")

        # Gather evidence from application documents
        search_queries = [
            "transport assessment cycling pedestrian active travel",
            "highway access junction road layout parking",
            "cycle parking bicycle storage provision",
            "site layout permeability connectivity walking",
        ]

        self._evidence_chunks: list[dict[str, Any]] = []

        for i, query in enumerate(search_queries):
            await self._progress.update_sub_progress(
                f"Searching documents ({i + 1}/{len(search_queries)})",
                current=i + 1,
                total=len(search_queries) + 3,  # +3 for policy searches
            )

            try:
                result = await self._mcp_client.call_tool(
                    "search_application_docs",
                    {
                        "query": query,
                        "application_ref": self._application_ref,
                        "max_results": 5,
                    },
                    timeout=60.0,
                )
                results_list = result.get("results", [])
                for r in results_list:
                    self._evidence_chunks.append({
                        "source": "application",
                        "query": query,
                        "text": r.get("text", r.get("document", "")),
                        "metadata": r.get("metadata", {}),
                    })
            except (MCPToolError, MCPConnectionError) as e:
                logger.warning("Doc search failed", query=query, error=str(e))

        # Search policy documents
        await self._progress.update_sub_progress("Searching policy documents")

        policy_queries = [
            ("cycle infrastructure design segregation width", ["LTN_1_20"]),
            ("sustainable transport planning cycling", ["NPPF", "CHERWELL_LP_2015"]),
            ("cycling walking infrastructure plan", ["OCC_LTCP", "BICESTER_LCWIP"]),
        ]

        for i, (query, sources) in enumerate(policy_queries):
            await self._progress.update_sub_progress(
                f"Searching policies ({i + 1}/{len(policy_queries)})",
                current=len(search_queries) + i + 1,
                total=len(search_queries) + len(policy_queries),
            )

            try:
                result = await self._mcp_client.call_tool(
                    "search_policy",
                    {
                        "query": query,
                        "sources": sources,
                        "n_results": 5,
                    },
                    timeout=60.0,
                )
                results_list = result.get("results", [])
                for r in results_list:
                    self._evidence_chunks.append({
                        "source": "policy",
                        "query": query,
                        "text": r.get("text", r.get("document", "")),
                        "metadata": r.get("metadata", {}),
                    })
            except (MCPToolError, MCPConnectionError) as e:
                logger.warning("Policy search failed", query=query, error=str(e))

        logger.info(
            "Analysis phase complete",
            review_id=self._review_id,
            evidence_chunks=len(self._evidence_chunks),
        )

    async def _phase_generate_review(self) -> None:
        """Phase 5: Generate the review using Claude."""
        await self._progress.update_sub_progress("Generating review with Claude")

        # Build context from gathered evidence
        app_summary = "No application metadata available."
        if self._application:
            app_summary = (
                f"Reference: {self._application.reference}\n"
                f"Address: {self._application.address or 'Unknown'}\n"
                f"Proposal: {self._application.proposal or 'Unknown'}\n"
                f"Applicant: {self._application.applicant or 'Unknown'}\n"
                f"Status: {self._application.status or 'Unknown'}\n"
                f"Date Validated: {self._application.date_validated or 'Unknown'}\n"
                f"Documents Fetched: {len(self._application.documents)}"
            )

        # Build evidence text
        app_evidence = []
        policy_evidence = []
        for chunk in getattr(self, "_evidence_chunks", []):
            text = chunk.get("text", "")
            meta = chunk.get("metadata", {})
            if chunk.get("source") == "policy":
                source_name = meta.get("source", meta.get("source_title", "Unknown policy"))
                policy_evidence.append(f"[{source_name}] {text}")
            else:
                source_file = meta.get("source_file", "Unknown document")
                app_evidence.append(f"[{source_file}] {text}")

        app_evidence_text = "\n\n---\n\n".join(app_evidence[:20]) if app_evidence else "No application document content retrieved."
        policy_evidence_text = "\n\n---\n\n".join(policy_evidence[:15]) if policy_evidence else "No policy content retrieved."

        system_prompt = """You are a planning application reviewer acting on behalf of a local cycling advocacy group in the Cherwell District. Your role is to assess planning applications from the perspective of people who walk and cycle.

For each application you review, you should:

1. UNDERSTAND THE PROPOSAL — Summarise what is being proposed, its location, and scale.

2. ASSESS TRANSPORT & MOVEMENT — Evaluate:
   - Cycle parking provision (quantity, type, location, security, accessibility)
   - Cycle route provision (on-site and connections to existing network)
   - Whether cycle infrastructure meets LTN 1/20 standards
   - Pedestrian permeability and filtered permeability for cycles
   - Impact on existing cycle routes and rights of way
   - Trip generation and modal split assumptions in any Transport Assessment

3. COMPARE AGAINST POLICY — Reference specific policies:
   - LTN 1/20 design standards (especially Table 5-2 on segregation triggers, Chapter 5 on geometric design, Chapter 6 on junctions)
   - Cherwell Local Plan policies on sustainable transport
   - NPPF paragraphs on sustainable transport
   - Oxfordshire LCWIP route proposals
   - Manual for Streets design principles

4. IDENTIFY ISSUES — Flag:
   - Non-compliance with LTN 1/20
   - Missing or inadequate cycle parking
   - Missed opportunities for active travel connections
   - Hostile junction designs for cyclists
   - Failure to provide filtered permeability
   - Inadequate width or shared-use paths where segregation is warranted

5. MAKE RECOMMENDATIONS — Suggest specific, constructive improvements with policy justification.

Always cite specific policy references. Be constructive and evidence-based.
Your review should be suitable for submission as a formal consultation response.

Output your review in the following markdown format:

# Cycle Advocacy Review: [Application Reference]

## Application Summary
- **Reference:** [ref]
- **Site:** [address]
- **Proposal:** [description]
- **Applicant:** [name]
- **Status:** [current status]

## Assessment Summary
**Overall Rating:** RED / AMBER / GREEN

| Aspect | Rating | Key Issue |
|--------|--------|-----------|
| Cycle Parking | ... | ... |
| Cycle Routes | ... | ... |
| Junctions | ... | ... |
| Permeability | ... | ... |
| Policy Compliance | ... | ... |

## Detailed Assessment

### 1. Cycle Parking
[Analysis]

### 2. Cycle Route Provision
[Analysis]

### 3. Junction Design
[Analysis]

### 4. Pedestrian & Cycle Permeability
[Analysis]

### 5. Transport Assessment Review
[Analysis]

## Policy Compliance Matrix

| Requirement | Policy Source | Compliant? | Notes |
|---|---|---|---|
| ... | ... | ... | ... |

## Recommendations
1. [Specific recommendations with policy justification]

## Suggested Conditions
If the council is minded to approve, the following conditions are recommended:
1. ...
"""

        user_prompt = f"""Please review the following planning application from a cycling advocacy perspective.

## Application Details
{app_summary}

## Evidence from Application Documents
{app_evidence_text}

## Relevant Policy Extracts
{policy_evidence_text}

Please provide a comprehensive cycle advocacy review based on the above information. If the application documents don't contain enough transport/cycling information, note this as a concern and base your review on what is available. Use the policy extracts to ground your assessment in specific policy requirements."""

        # Call Claude API
        model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")
        api_key = os.getenv("ANTHROPIC_API_KEY")

        if not api_key:
            raise OrchestratorError(
                "ANTHROPIC_API_KEY not set",
                phase=ReviewPhase.GENERATING_REVIEW,
                recoverable=False,
            )

        await self._progress.update_sub_progress("Calling Claude API")

        try:
            client = anthropic.Anthropic(api_key=api_key)
            message = client.messages.create(
                model=model,
                max_tokens=8000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )

            review_markdown = message.content[0].text

            # Parse overall rating from the markdown
            overall_rating = "amber"  # default
            rating_lower = review_markdown.lower()
            if "overall rating:** red" in rating_lower or "overall rating:** 🔴" in rating_lower:
                overall_rating = "red"
            elif "overall rating:** green" in rating_lower or "overall rating:** 🟢" in rating_lower:
                overall_rating = "green"

            # Store review result on the ReviewResult object
            result = ReviewResult(
                review_id=self._review_id,
                application_ref=self._application_ref,
                application=self._application,
                review={
                    "overall_rating": overall_rating,
                    "full_markdown": review_markdown,
                    "summary": review_markdown[:500],
                    "model": model,
                    "input_tokens": message.usage.input_tokens,
                    "output_tokens": message.usage.output_tokens,
                },
                metadata={
                    "model": model,
                    "total_tokens_used": message.usage.input_tokens + message.usage.output_tokens,
                    "evidence_chunks_used": len(getattr(self, "_evidence_chunks", [])),
                    "documents_analysed": (
                        self._ingestion_result.documents_ingested
                        if self._ingestion_result
                        else 0
                    ),
                },
                success=True,
            )

            # Store on self so the run() method can access it
            self._review_result = result

            logger.info(
                "Review generated",
                review_id=self._review_id,
                overall_rating=overall_rating,
                tokens_used=message.usage.input_tokens + message.usage.output_tokens,
            )

        except anthropic.APIError as e:
            raise OrchestratorError(
                f"Claude API error: {e}",
                phase=ReviewPhase.GENERATING_REVIEW,
                recoverable=False,
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
