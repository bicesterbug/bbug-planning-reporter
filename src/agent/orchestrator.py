"""
Agent Orchestrator for coordinating the review workflow.

Implements [agent-integration:FR-001] - Establishes and maintains MCP server connections
Implements [agent-integration:FR-002] - Orchestrates complete review workflow
Implements [agent-integration:NFR-001] - Workflow completes within time limits
Implements [agent-integration:NFR-005] - Handles partial failures gracefully
Implements [structured-review-output:FR-001] - Two-phase review generation
Implements [structured-review-output:FR-004] - Structured fields from structure call JSON
Implements [structured-review-output:FR-005] - ReviewMarkdownParser removed
Implements [structured-review-output:FR-007] - Fallback on structure call failure
Implements [structured-review-output:NFR-001] - Token budget split
Implements [structured-review-output:NFR-002] - Duration logging per call

Implements:
- [agent-integration:AgentOrchestrator/TS-01] Successful MCP connections
- [agent-integration:AgentOrchestrator/TS-02] Reconnection on transient failure
- [agent-integration:AgentOrchestrator/TS-03] Complete workflow execution
- [agent-integration:AgentOrchestrator/TS-04] Scraper failure handling
- [agent-integration:AgentOrchestrator/TS-05] Partial document ingestion
- [agent-integration:AgentOrchestrator/TS-06] Cancellation handling
- [agent-integration:AgentOrchestrator/TS-07] State persistence
- [agent-integration:AgentOrchestrator/TS-08] All servers unavailable
- [structured-review-output:AgentOrchestrator/TS-01] Two-phase success
- [structured-review-output:AgentOrchestrator/TS-02] Structure call invalid JSON fallback
- [structured-review-output:AgentOrchestrator/TS-03] Structure call API error fallback
- [structured-review-output:AgentOrchestrator/TS-04] Token usage tracked
- [structured-review-output:AgentOrchestrator/TS-05] Review dict shape preserved
"""

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import anthropic
import redis.asyncio as redis
import structlog
from pydantic import ValidationError

from src.agent.mcp_client import MCPClientManager, MCPConnectionError, MCPServerType, MCPToolError
from src.mcp_servers.cherwell_scraper.filters import DocumentFilter
from src.agent.progress import ProgressTracker, ReviewPhase
from src.agent.prompts.document_filter_prompt import build_document_filter_prompt
from src.agent.prompts.report_prompt import build_report_prompt
from src.agent.prompts.search_query_prompt import build_search_query_prompt
from src.agent.prompts.structure_prompt import build_structure_prompt
from src.agent.prompts.verification_prompt import build_verification_prompt
from src.agent.review_schema import ReviewStructure
from src.shared.storage import LocalStorageBackend, StorageBackend, StorageUploadError

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
    # Implements [key-documents:FR-005] - Maps file_path to {description, document_type, url}
    document_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Implements [document-type-detection:FR-003] - Track image-based docs separately
    skipped_documents: list[dict[str, Any]] = field(default_factory=list)


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
        options: Any | None = None,
        storage_backend: StorageBackend | None = None,
        previous_review_id: str | None = None,
    ) -> None:
        """
        Initialize the orchestrator.

        Implements [review-scope-control:FR-004] - Accepts review options

        Args:
            review_id: The review job ID.
            application_ref: The planning application reference.
            mcp_client: Optional MCPClientManager (created if not provided).
            redis_client: Optional Redis client for state persistence.
            options: Optional ReviewOptions with toggle flags for document filtering.
            storage_backend: Optional StorageBackend for document storage (defaults to local).
            previous_review_id: Optional ID of previous completed review for document reuse.
        """
        self._review_id = review_id
        self._application_ref = application_ref
        self._mcp_client = mcp_client
        self._redis = redis_client
        self._options = options
        self._storage: StorageBackend = storage_backend or LocalStorageBackend()
        self._owns_mcp_client = mcp_client is None
        self._previous_review_id = previous_review_id
        self._resubmission_stats: dict[str, Any] = {
            "previous_review_id": previous_review_id,
            "documents_reused": 0,
            "documents_new": 0,
            "documents_removed": 0,
        }

        # Progress tracker
        self._progress = ProgressTracker(
            review_id=review_id,
            application_ref=application_ref,
            redis_client=redis_client,
        )

        # State
        self._application: ApplicationMetadata | None = None
        self._selected_documents: list[dict[str, Any]] = []  # [review-workflow-redesign:FR-001]
        self._ingestion_result: DocumentIngestionResult | None = None
        self._evidence_chunks: list[dict[str, Any]] = []
        self._review_result: ReviewResult | None = None
        self._initialized = False
        # Implements [cycle-route-assessment:FR-008] - Route assessment data
        self._route_assessments: list[dict[str, Any]] = []
        self._site_boundary: dict[str, Any] | None = None

        # Implements [review-workflow-redesign:NFR-001] - Configurable filter model
        self._filter_model = os.getenv("DOCUMENT_FILTER_MODEL", "claude-haiku-4-5-20251001")

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
            # Implements [cycle-route-assessment:AgentOrchestrator/TS-01] - Eight phases
            phases = [
                (ReviewPhase.FETCHING_METADATA, self._phase_fetch_metadata),
                (ReviewPhase.FILTERING_DOCUMENTS, self._phase_filter_documents),
                (ReviewPhase.DOWNLOADING_DOCUMENTS, self._phase_download_documents),
                (ReviewPhase.INGESTING_DOCUMENTS, self._phase_ingest_documents),
                (ReviewPhase.ANALYSING_APPLICATION, self._phase_analyse_application),
                (ReviewPhase.ASSESSING_ROUTES, self._phase_assess_routes),
                (ReviewPhase.GENERATING_REVIEW, self._phase_generate_review),
                (ReviewPhase.VERIFYING_REVIEW, self._phase_verify_review),
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
                    f"Failed to fetch application: {result.get('message') or result.get('error') or 'Unknown error'}",
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

    def _post_filter_consultation_documents(
        self, documents: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Remove consultation responses and public comments from LLM-selected documents.

        Implements [consultation-filter-enforcement:FR-001] - Post-filter after LLM selection
        Implements [consultation-filter-enforcement:FR-002] - Uses DocumentFilter patterns
        Implements [consultation-filter-enforcement:FR-003] - Logs removals

        The LLM filter cannot be relied upon to exclude consultation responses when
        they appear transport-relevant. This programmatic post-filter enforces the
        exclusion unless the corresponding API toggle is enabled.
        """
        include_consultation = (
            self._options
            and hasattr(self._options, "include_consultation_responses")
            and self._options.include_consultation_responses
        )
        include_public = (
            self._options
            and hasattr(self._options, "include_public_comments")
            and self._options.include_public_comments
        )

        kept: list[dict[str, Any]] = []
        for doc in documents:
            doc_type = (doc.get("document_type") or "").lower().strip()
            description = (doc.get("description") or "").lower().strip()

            # Tier 1: Check portal category denylist
            if not include_consultation and doc_type in DocumentFilter.CATEGORY_DENYLIST_CONSULTATION:
                logger.warning(
                    "Document removed by post-filter",
                    review_id=self._review_id,
                    document=doc.get("description", ""),
                    document_type=doc.get("document_type", ""),
                    reason="category_denylist_consultation",
                )
                continue

            if not include_public and doc_type in DocumentFilter.CATEGORY_DENYLIST_PUBLIC:
                logger.warning(
                    "Document removed by post-filter",
                    review_id=self._review_id,
                    document=doc.get("description", ""),
                    document_type=doc.get("document_type", ""),
                    reason="category_denylist_public",
                )
                continue

            # Tier 2: Check title-based pattern denylist (fallback when category unknown)
            if not include_consultation and any(
                p in description for p in DocumentFilter.DENYLIST_CONSULTATION_RESPONSE_PATTERNS
            ):
                logger.warning(
                    "Document removed by post-filter",
                    review_id=self._review_id,
                    document=doc.get("description", ""),
                    document_type=doc.get("document_type", ""),
                    reason="title_pattern_consultation",
                )
                continue

            if not include_public and any(
                p in description for p in DocumentFilter.DENYLIST_PUBLIC_COMMENT_PATTERNS
            ):
                logger.warning(
                    "Document removed by post-filter",
                    review_id=self._review_id,
                    document=doc.get("description", ""),
                    document_type=doc.get("document_type", ""),
                    reason="title_pattern_public",
                )
                continue

            kept.append(doc)

        return kept

    async def _phase_filter_documents(self) -> None:
        """
        Phase 2: Filter application documents using LLM.

        Implements [review-workflow-redesign:FR-001] - LLM-based document filtering
        Implements [review-workflow-redesign:FR-002] - Filter failure aborts review
        Implements [review-workflow-redesign:FR-008] - Application-aware filtering
        Implements [review-workflow-redesign:NFR-001] - Filter latency logging
        Implements [review-workflow-redesign:NFR-002] - Document count comparison
        """
        assert self._mcp_client is not None

        await self._progress.update_sub_progress("Listing application documents")

        try:
            # Fetch the full document list from the scraper
            list_result = await self._mcp_client.call_tool(
                "list_application_documents",
                {"application_ref": self._application_ref},
                timeout=120.0,
            )

            all_documents = list_result.get("documents", [])
            total_listed = len(all_documents)

            if total_listed == 0:
                logger.warning(
                    "No documents found for application",
                    review_id=self._review_id,
                    application_ref=self._application_ref,
                )
                self._selected_documents = []
                return

            await self._progress.update_sub_progress(
                f"Filtering {total_listed} documents with LLM"
            )

            # Build application metadata for the filter prompt
            app_meta = {
                "reference": self._application_ref,
                "address": self._application.address if self._application else "Unknown",
                "proposal": self._application.proposal if self._application else "Unknown",
                "type": getattr(self._application, "application_type", None) or "Unknown",
            }

            # Build document list for the filter prompt
            doc_list = [
                {
                    "id": doc.get("document_id", ""),
                    "description": doc.get("description", "Untitled"),
                    "document_type": doc.get("document_type", "Unknown"),
                    "date_published": doc.get("date_published", ""),
                }
                for doc in all_documents
            ]

            system_prompt, user_prompt = build_document_filter_prompt(app_meta, doc_list)

            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise OrchestratorError(
                    "ANTHROPIC_API_KEY not set",
                    phase=ReviewPhase.FILTERING_DOCUMENTS,
                    recoverable=False,
                )

            filter_start = time.monotonic()
            try:
                client = anthropic.Anthropic(api_key=api_key)
                filter_msg = client.messages.create(
                    model=self._filter_model,
                    max_tokens=4096,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                )

                raw_text = filter_msg.content[0].text.strip()
                # Strip markdown code fence if present
                if raw_text.startswith("```"):
                    lines = raw_text.split("\n")
                    lines = lines[1:]
                    if lines and lines[-1].strip() == "```":
                        lines = lines[:-1]
                    raw_text = "\n".join(lines)

                selected_ids = json.loads(raw_text)
                if not isinstance(selected_ids, list):
                    raise ValueError(f"Expected JSON array, got {type(selected_ids).__name__}")

            except (json.JSONDecodeError, ValueError, KeyError) as e:
                raise OrchestratorError(
                    f"Document filter returned invalid response: {e}",
                    phase=ReviewPhase.FILTERING_DOCUMENTS,
                    recoverable=False,
                )
            except anthropic.APIError as e:
                raise OrchestratorError(
                    f"Document filter API error: {e}",
                    phase=ReviewPhase.FILTERING_DOCUMENTS,
                    recoverable=False,
                )

            filter_duration = time.monotonic() - filter_start

            # Match selected IDs to document metadata
            selected_id_set = {str(sid) for sid in selected_ids}
            llm_selected = [
                doc for doc in all_documents
                if str(doc.get("document_id", "")) in selected_id_set
            ]

            # Implements [consultation-filter-enforcement:FR-001]
            # Programmatic post-filter to remove consultation responses and
            # public comments that the LLM selected despite prompt instructions.
            pre_post_filter_count = len(llm_selected)
            self._selected_documents = self._post_filter_consultation_documents(llm_selected)
            post_filter_removed = pre_post_filter_count - len(self._selected_documents)
            if post_filter_removed > 0:
                logger.info(
                    "Post-filter removed consultation/public comment documents",
                    review_id=self._review_id,
                    removed=post_filter_removed,
                    remaining=len(self._selected_documents),
                )

            selected_count = len(self._selected_documents)
            reduction_pct = round((1 - selected_count / total_listed) * 100, 1) if total_listed > 0 else 0

            # Implements [review-workflow-redesign:NFR-001] - Filter latency logging
            logger.info(
                "Document filter completed",
                review_id=self._review_id,
                duration_seconds=round(filter_duration, 2),
                total_documents=total_listed,
                selected_documents=selected_count,
            )

            # Implements [review-workflow-redesign:NFR-002] - Document count comparison
            logger.info(
                "Document selection summary",
                review_id=self._review_id,
                total_listed=total_listed,
                selected=selected_count,
                reduction_pct=reduction_pct,
            )

        except MCPToolError as e:
            raise OrchestratorError(
                f"Failed to list documents: {e.message}",
                phase=ReviewPhase.FILTERING_DOCUMENTS,
                recoverable=False,
            )
        except MCPConnectionError as e:
            raise OrchestratorError(
                f"MCP connection error during filtering: {e}",
                phase=ReviewPhase.FILTERING_DOCUMENTS,
                recoverable=False,
            )

    def _load_manifest(self, safe_ref: str) -> dict[str, dict[str, Any]]:
        """Load previous manifest from S3 and return a lookup by document_id.

        Returns empty dict if no manifest found, or manifest is malformed.
        """
        if not self._previous_review_id or not self._storage.is_remote:
            return {}

        manifest_key = f"{safe_ref}/manifest.json"
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            self._storage.download_to(manifest_key, tmp_path)
            manifest_data = json.loads(tmp_path.read_text())
            tmp_path.unlink(missing_ok=True)

            manifest_by_id: dict[str, dict[str, Any]] = {}
            for entry in manifest_data.get("documents", []):
                doc_id = entry.get("document_id")
                if doc_id:
                    manifest_by_id[doc_id] = entry

            logger.info(
                "Previous manifest loaded",
                review_id=self._review_id,
                previous_review_id=self._previous_review_id,
                manifest_documents=len(manifest_by_id),
            )
            return manifest_by_id

        except FileNotFoundError:
            logger.info(
                "No previous manifest found",
                review_id=self._review_id,
                previous_review_id=self._previous_review_id,
            )
            return {}
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(
                "Previous manifest malformed, treating as fresh review",
                review_id=self._review_id,
                error=str(e),
            )
            return {}
        except Exception as e:
            logger.warning(
                "Failed to load previous manifest",
                review_id=self._review_id,
                error=str(e),
            )
            return {}

    def _save_manifest(
        self,
        safe_ref: str,
        downloaded: list[dict[str, Any]],
        output_dir: str,
    ) -> None:
        """Build and upload manifest JSON to S3."""
        if not self._storage.is_remote:
            return

        manifest_entries = []
        for dl in downloaded:
            file_path = dl.get("file_path")
            if not file_path:
                continue
            s3_key = file_path.removeprefix(output_dir + "/")

            # Compute file hash
            file_hash = ""
            try:
                sha = hashlib.sha256()
                with open(file_path, "rb") as f:
                    for chunk in iter(lambda: f.read(8192), b""):
                        sha.update(chunk)
                file_hash = f"sha256:{sha.hexdigest()}"
            except OSError:
                pass

            manifest_entries.append({
                "document_id": dl.get("document_id", ""),
                "description": dl.get("description", ""),
                "document_type": dl.get("document_type"),
                "date_published": dl.get("date_published"),
                "s3_key": s3_key,
                "file_hash": file_hash,
                "cherwell_url": dl.get("url", ""),
            })

        from datetime import UTC, datetime
        manifest = {
            "review_id": self._review_id,
            "application_ref": self._application_ref,
            "created_at": datetime.now(UTC).isoformat(),
            "documents": manifest_entries,
        }

        manifest_key = f"{safe_ref}/manifest.json"
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as tmp:
                json.dump(manifest, tmp, indent=2)
                tmp_path = Path(tmp.name)
            self._storage.upload(tmp_path, manifest_key)
            tmp_path.unlink(missing_ok=True)
            logger.info(
                "Manifest saved to S3",
                review_id=self._review_id,
                manifest_key=manifest_key,
                documents=len(manifest_entries),
            )
        except Exception as e:
            logger.warning(
                "Failed to save manifest",
                review_id=self._review_id,
                error=str(e),
            )

    async def _phase_download_documents(self) -> None:
        """
        Phase 3: Download selected documents individually.

        Implements [review-workflow-redesign:FR-001] - Downloads only LLM-selected documents

        When a previous_review_id is set, loads the previous manifest from S3
        and reuses documents that still appear in the selected list.
        """
        assert self._mcp_client is not None

        if not self._selected_documents:
            logger.warning(
                "No documents selected for download",
                review_id=self._review_id,
            )
            self._ingestion_result = DocumentIngestionResult()
            return

        await self._progress.update_sub_progress("Downloading selected documents")

        output_dir = "/data/raw"
        safe_ref = self._application_ref.replace("/", "_")
        app_output_dir = f"{output_dir}/{safe_ref}"

        # Load previous manifest for document reuse
        manifest_by_id = self._load_manifest(safe_ref)

        downloaded: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        document_metadata: dict[str, dict[str, Any]] = {}
        total_docs = len(self._selected_documents)
        reused_count = 0
        new_count = 0

        # Track which manifest entries are still in use
        selected_doc_ids = {doc.get("document_id", "") for doc in self._selected_documents}

        for i, doc in enumerate(self._selected_documents):
            doc_url = doc.get("url")
            doc_id = doc.get("document_id", "")
            desc = doc.get("description", "Unknown")

            if not doc_url:
                failed.append({"document_id": doc_id, "error": "No URL", "success": False})
                continue

            await self._progress.update_sub_progress(
                f"Downloading {i + 1}/{total_docs}: {desc[:50]}",
                current=i + 1,
                total=total_docs,
            )

            # Generate filename from description to avoid URL-derived collisions
            safe_name = "".join(
                c if c.isalnum() or c in "._- " else "_" for c in desc
            )[:100]
            filename = f"{i + 1:03d}_{safe_name}.pdf"
            local_path = f"{app_output_dir}/{filename}"

            # Try reusing from S3 if document exists in previous manifest
            manifest_entry = manifest_by_id.get(doc_id) if manifest_by_id else None
            if manifest_entry and self._storage.is_remote:
                s3_key = manifest_entry.get("s3_key", "")
                try:
                    Path(local_path).parent.mkdir(parents=True, exist_ok=True)
                    self._storage.download_to(s3_key, Path(local_path))

                    dl_record = {
                        "document_id": doc_id,
                        "file_path": local_path,
                        "file_size": Path(local_path).stat().st_size,
                        "success": True,
                        "description": desc,
                        "document_type": doc.get("document_type"),
                        "url": doc_url,
                        "reused": True,
                    }
                    downloaded.append(dl_record)

                    public_url = self._storage.public_url(s3_key)
                    document_metadata[local_path] = {
                        "description": desc,
                        "document_type": doc.get("document_type"),
                        "url": public_url or doc_url,
                    }

                    reused_count += 1
                    logger.info(
                        "Reusing document from S3",
                        review_id=self._review_id,
                        document_id=doc_id,
                        s3_key=s3_key,
                    )
                    continue
                except Exception as e:
                    logger.warning(
                        "S3 reuse failed, falling back to Cherwell download",
                        review_id=self._review_id,
                        document_id=doc_id,
                        error=str(e),
                    )

            # Download from Cherwell (new document or S3 reuse failed)
            try:
                result = await self._mcp_client.call_tool(
                    "download_document",
                    {
                        "document_url": doc_url,
                        "output_dir": app_output_dir,
                        "filename": filename,
                    },
                    timeout=120.0,
                )

                if result.get("status") == "success":
                    file_path = result.get("file_path", "")
                    dl_record = {
                        "document_id": doc_id,
                        "file_path": file_path,
                        "file_size": result.get("file_size"),
                        "success": True,
                        "description": desc,
                        "document_type": doc.get("document_type"),
                        "url": doc_url,
                        "reused": False,
                    }
                    downloaded.append(dl_record)

                    if file_path:
                        document_metadata[file_path] = {
                            "description": desc,
                            "document_type": doc.get("document_type"),
                            "url": doc_url,
                        }
                    new_count += 1
                else:
                    failed.append({
                        "document_id": doc_id,
                        "error": result.get("error", "Unknown error"),
                        "success": False,
                    })

            except (MCPToolError, MCPConnectionError) as e:
                logger.warning(
                    "Document download failed",
                    review_id=self._review_id,
                    document_id=doc_id,
                    error=str(e),
                )
                failed.append({
                    "document_id": doc_id,
                    "error": str(e),
                    "success": False,
                })

        # Implements [s3-document-storage:FR-002] - Upload to S3 after download
        if self._storage.is_remote:
            upload_start = time.monotonic()
            for dl in downloaded:
                file_path = dl.get("file_path")
                if not file_path or dl.get("reused"):
                    continue
                s3_key = file_path.removeprefix(output_dir + "/")
                try:
                    self._storage.upload(Path(file_path), s3_key)
                    public_url = self._storage.public_url(s3_key)
                    if public_url and file_path in document_metadata:
                        document_metadata[file_path]["url"] = public_url
                except StorageUploadError as e:
                    logger.warning(
                        "S3 upload failed, keeping original URL",
                        review_id=self._review_id,
                        file_path=file_path,
                        error=str(e),
                    )
            upload_elapsed = time.monotonic() - upload_start
            logger.info(
                "S3 uploads complete",
                review_id=self._review_id,
                s3_upload_total_seconds=round(upload_elapsed, 2),
                files_uploaded=len([dl for dl in downloaded if dl.get("file_path") and not dl.get("reused")]),
            )

        # Save manifest to S3
        self._save_manifest(safe_ref, downloaded, output_dir)

        # Update resubmission stats
        removed_count = len(set(manifest_by_id.keys()) - selected_doc_ids) if manifest_by_id else 0
        self._resubmission_stats.update({
            "documents_reused": reused_count,
            "documents_new": new_count,
            "documents_removed": removed_count,
        })

        self._ingestion_result = DocumentIngestionResult(
            documents_fetched=len(downloaded),
            document_paths=[d.get("file_path") for d in downloaded if d.get("file_path")],
            failed_documents=failed,
            document_metadata=document_metadata,
        )

        logger.info(
            "Documents downloaded",
            review_id=self._review_id,
            downloaded=len(downloaded),
            failed=len(failed),
            reused=reused_count,
            new=new_count,
            removed=removed_count,
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
        skipped_count = 0
        counter_lock = asyncio.Lock()
        concurrency = int(os.getenv("INGEST_CONCURRENCY", "4"))
        semaphore = asyncio.Semaphore(concurrency)

        async def ingest_one(doc_path: str) -> bool:
            """Ingest a single document, respecting the semaphore."""
            nonlocal ingested_count, failed_count, skipped_count
            async with semaphore:
                try:
                    result = await self._mcp_client.call_tool(
                        "ingest_document",
                        {
                            "file_path": doc_path,
                            "application_ref": self._application_ref,
                        },
                        timeout=float(os.getenv("INGEST_TIMEOUT", "600")),
                    )

                    if result.get("status") in ("success", "already_ingested"):
                        # Implements [s3-document-storage:FR-003] - Clean up temp file
                        if self._storage.is_remote:
                            self._storage.delete_local(Path(doc_path))
                        async with counter_lock:
                            ingested_count += 1
                            await self._progress.update_sub_progress(
                                f"Ingested {ingested_count} of {total_docs} documents",
                                current=ingested_count,
                                total=total_docs,
                            )
                        return True
                    elif result.get("status") == "skipped":
                        # Implements [document-type-detection:FR-002] - Track skipped docs
                        # Implements [document-type-detection:FR-003] - Retain separately
                        async with counter_lock:
                            skipped_count += 1
                            doc_meta = self._ingestion_result.document_metadata.get(doc_path, {})
                            self._ingestion_result.skipped_documents.append({
                                "file_path": doc_path,
                                "description": doc_meta.get("description", os.path.basename(doc_path)),
                                "document_type": doc_meta.get("document_type", "Unknown"),
                                "url": doc_meta.get("url", ""),
                                "reason": result.get("reason", "image_based"),
                                "image_ratio": result.get("image_ratio", 0.0),
                            })
                        logger.info(
                            "Document skipped (image-based)",
                            review_id=self._review_id,
                            document=doc_path,
                            image_ratio=result.get("image_ratio"),
                        )
                        return False
                    else:
                        async with counter_lock:
                            failed_count += 1
                        error_msg = result.get("message") or result.get("error") or "Unknown error"
                        await self._progress.record_error(
                            ReviewPhase.INGESTING_DOCUMENTS,
                            error_msg,
                            document=doc_path,
                        )
                        return False

                except MCPToolError as e:
                    # Log and continue - partial ingestion is acceptable
                    async with counter_lock:
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
                    return False

        await asyncio.gather(*(
            ingest_one(doc_path)
            for doc_path in self._ingestion_result.document_paths
        ))

        self._ingestion_result.documents_ingested = ingested_count

        logger.info(
            "Document ingestion complete",
            review_id=self._review_id,
            ingested=ingested_count,
            skipped=skipped_count,
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
        """
        Phase 5: Analyse application using LLM-generated search queries.

        Implements [review-workflow-redesign:FR-004] - Dynamic search query generation
        """
        assert self._mcp_client is not None

        await self._progress.update_sub_progress("Generating search queries with LLM")

        self._evidence_chunks: list[dict[str, Any]] = []

        # Build metadata for search query generation
        app_meta = {
            "reference": self._application_ref,
            "address": self._application.address if self._application else "Unknown",
            "proposal": self._application.proposal if self._application else "Unknown",
            "type": getattr(self._application, "application_type", None) or "Unknown",
        }

        # Build ingested document list
        ingested_docs: list[dict[str, Any]] = []
        if self._ingestion_result and self._ingestion_result.document_metadata:
            for file_path, meta in self._ingestion_result.document_metadata.items():
                ingested_docs.append({
                    "description": meta.get("description") or os.path.basename(file_path),
                    "document_type": meta.get("document_type", "Unknown"),
                })

        # Generate queries using LLM
        api_key = os.getenv("ANTHROPIC_API_KEY")
        application_queries: list[str] = []
        policy_queries: list[dict[str, Any]] = []

        try:
            system_prompt, user_prompt = build_search_query_prompt(app_meta, ingested_docs)
            client = anthropic.Anthropic(api_key=api_key)
            query_msg = client.messages.create(
                model=self._filter_model,
                max_tokens=4096,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )

            raw_text = query_msg.content[0].text.strip()
            if raw_text.startswith("```"):
                lines = raw_text.split("\n")
                lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                raw_text = "\n".join(lines)

            query_data = json.loads(raw_text)
            application_queries = query_data.get("application_queries", [])
            policy_queries = query_data.get("policy_queries", [])

            logger.info(
                "Search queries generated",
                review_id=self._review_id,
                application_queries=len(application_queries),
                policy_queries=len(policy_queries),
            )

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(
                "Query generation returned invalid response, using defaults",
                review_id=self._review_id,
                error=str(e),
            )
            application_queries = [
                "transport assessment cycling pedestrian active travel",
                "highway access junction road layout parking",
                "cycle parking bicycle storage provision",
                "site layout permeability connectivity walking",
            ]
            policy_queries = [
                {"query": "cycle infrastructure design segregation width", "sources": ["LTN_1_20"]},
                {"query": "sustainable transport planning cycling", "sources": ["NPPF", "CHERWELL_LP_2015"]},
                {"query": "cycling walking infrastructure plan", "sources": ["OCC_LTCP", "BICESTER_LCWIP"]},
            ]
        except anthropic.APIError as e:
            logger.warning(
                "Query generation API error, using defaults",
                review_id=self._review_id,
                error=str(e),
            )
            application_queries = [
                "transport assessment cycling pedestrian active travel",
                "highway access junction road layout parking",
                "cycle parking bicycle storage provision",
                "site layout permeability connectivity walking",
            ]
            policy_queries = [
                {"query": "cycle infrastructure design segregation width", "sources": ["LTN_1_20"]},
                {"query": "sustainable transport planning cycling", "sources": ["NPPF", "CHERWELL_LP_2015"]},
                {"query": "cycling walking infrastructure plan", "sources": ["OCC_LTCP", "BICESTER_LCWIP"]},
            ]

        # Execute application document queries
        total_queries = len(application_queries) + len(policy_queries)
        for i, query in enumerate(application_queries):
            await self._progress.update_sub_progress(
                f"Searching documents ({i + 1}/{len(application_queries)})",
                current=i + 1,
                total=total_queries,
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

        # Execute policy queries
        await self._progress.update_sub_progress("Searching policy documents")

        for i, pq in enumerate(policy_queries):
            query = pq.get("query", "") if isinstance(pq, dict) else pq
            sources = pq.get("sources", []) if isinstance(pq, dict) else []

            await self._progress.update_sub_progress(
                f"Searching policies ({i + 1}/{len(policy_queries)})",
                current=len(application_queries) + i + 1,
                total=total_queries,
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

    async def _phase_assess_routes(self) -> None:
        """
        Phase 6: Assess cycling routes to configured destinations.

        Implements [cycle-route-assessment:FR-008] - Route assessment in pipeline
        Implements [cycle-route-assessment:NFR-002] - Graceful failure handling
        Implements [cycle-route-assessment:NFR-005] - Review completes even if assessment fails

        Looks up site boundary via ArcGIS, then assesses routes to each
        configured destination using the cycle-route MCP server. Results
        are stored as evidence context for the LLM review generation.
        """
        # Check if cycle-route MCP is available
        if not self._mcp_client or not self._mcp_client.is_connected(
            MCPServerType.CYCLE_ROUTE
        ):
            logger.info(
                "Cycle route MCP not available, skipping route assessment",
                review_id=self._review_id,
            )
            raise OrchestratorError(
                "Cycle route MCP server not available",
                phase=ReviewPhase.ASSESSING_ROUTES,
                recoverable=True,
            )

        # Check if destination_ids explicitly set to empty list (skip assessment)
        if (
            self._options
            and hasattr(self._options, "destination_ids")
            and self._options.destination_ids is not None
            and len(self._options.destination_ids) == 0
        ):
            logger.info(
                "Route assessment skipped (empty destination_ids)",
                review_id=self._review_id,
            )
            return

        await self._progress.update_sub_progress("Looking up site boundary")

        # Step 1: Get site boundary
        try:
            boundary_result = await self._mcp_client.call_tool(
                "get_site_boundary",
                {"application_ref": self._application_ref},
                timeout=30.0,
            )

            if boundary_result.get("status") == "error":
                logger.warning(
                    "Site boundary not found",
                    review_id=self._review_id,
                    error=boundary_result.get("message"),
                )
                raise OrchestratorError(
                    f"Site boundary lookup failed: {boundary_result.get('message')}",
                    phase=ReviewPhase.ASSESSING_ROUTES,
                    recoverable=True,
                )

            self._site_boundary = boundary_result.get("geojson")

        except (MCPToolError, MCPConnectionError) as e:
            logger.warning(
                "Site boundary lookup failed",
                review_id=self._review_id,
                error=str(e),
            )
            raise OrchestratorError(
                f"Site boundary lookup failed: {e}",
                phase=ReviewPhase.ASSESSING_ROUTES,
                recoverable=True,
            )

        # Extract centroid from boundary
        centroid = None
        if self._site_boundary and "features" in self._site_boundary:
            for feature in self._site_boundary["features"]:
                if feature.get("geometry", {}).get("type") == "Point":
                    coords = feature["geometry"]["coordinates"]
                    centroid = {"lon": coords[0], "lat": coords[1]}
                    break

        if not centroid:
            logger.warning(
                "No centroid in site boundary",
                review_id=self._review_id,
            )
            raise OrchestratorError(
                "Could not determine site centroid from boundary",
                phase=ReviewPhase.ASSESSING_ROUTES,
                recoverable=True,
            )

        # Step 2: Fetch destinations
        # Import here to avoid circular imports at module level
        from src.shared.destinations import list_destinations
        from src.shared.redis_client import RedisClient

        destinations = []
        if self._redis:
            redis_client = RedisClient()
            redis_client._client = self._redis
            all_destinations = await list_destinations(redis_client)

            # Filter by destination_ids if specified
            if (
                self._options
                and hasattr(self._options, "destination_ids")
                and self._options.destination_ids is not None
            ):
                dest_ids = set(self._options.destination_ids)
                destinations = [d for d in all_destinations if d["id"] in dest_ids]
            else:
                destinations = all_destinations
        else:
            logger.warning(
                "No Redis client for destinations, using empty list",
                review_id=self._review_id,
            )

        if not destinations:
            logger.info(
                "No destinations to assess",
                review_id=self._review_id,
            )
            return

        # Step 3: Assess route to each destination
        await self._progress.update_sub_progress(
            f"Assessing routes to {len(destinations)} destinations"
        )

        for i, dest in enumerate(destinations):
            dest_name = dest.get("name", "Destination")
            await self._progress.update_sub_progress(
                f"Route {i + 1}/{len(destinations)}: {dest_name}",
                current=i + 1,
                total=len(destinations),
            )

            try:
                route_result = await self._mcp_client.call_tool(
                    "assess_cycle_route",
                    {
                        "origin_lon": centroid["lon"],
                        "origin_lat": centroid["lat"],
                        "destination_lon": dest["lon"],
                        "destination_lat": dest["lat"],
                        "destination_name": dest_name,
                    },
                    timeout=30.0,
                )

                if route_result.get("status") == "success":
                    route_result["destination_id"] = dest["id"]
                    self._route_assessments.append(route_result)
                else:
                    logger.warning(
                        "Route assessment returned error",
                        review_id=self._review_id,
                        destination=dest_name,
                        error=route_result.get("message"),
                    )

            except (MCPToolError, MCPConnectionError) as e:
                logger.warning(
                    "Route assessment failed for destination",
                    review_id=self._review_id,
                    destination=dest_name,
                    error=str(e),
                )

        logger.info(
            "Route assessment phase complete",
            review_id=self._review_id,
            routes_assessed=len(self._route_assessments),
            destinations_total=len(destinations),
        )

    def _build_evidence_context(self) -> tuple[str, str, str, str, str, str]:
        """
        Build the evidence context strings used by both the structure and report calls.

        Returns:
            Tuple of (app_summary, ingested_docs_text, app_evidence_text,
            policy_evidence_text, plans_submitted_text, route_evidence_text).
        """
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

        # Implements [key-documents:FR-005] - Build ingested document list for LLM
        ingested_docs_text = "No document metadata available."
        if self._ingestion_result and self._ingestion_result.document_metadata:
            doc_lines = []
            for file_path, meta in self._ingestion_result.document_metadata.items():
                desc = meta.get("description") or os.path.basename(file_path)
                doc_type = meta.get("document_type", "Unknown")
                raw_url = meta.get("url")
                url = quote(raw_url, safe=":/?#[]@!$&'()*+,;=-._~%") if raw_url else "no URL"
                doc_lines.append(f"- {desc} (type: {doc_type}, url: {url})")
            if doc_lines:
                ingested_docs_text = "\n".join(doc_lines)

        # Implements [document-type-detection:FR-004] - Plans submitted metadata
        # Implements [document-type-detection:FR-005] - Agent context for skipped plans
        plans_submitted_text = "No plans or drawings were detected."
        if self._ingestion_result and self._ingestion_result.skipped_documents:
            plan_lines = []
            for doc in self._ingestion_result.skipped_documents:
                desc = doc.get("description", "Unknown")
                doc_type = doc.get("document_type", "Unknown")
                ratio = doc.get("image_ratio", 0.0)
                plan_lines.append(f"- {desc} (type: {doc_type}, image ratio: {ratio:.0%})")
            if plan_lines:
                plans_submitted_text = "\n".join(plan_lines)

        # Implements [cycle-route-assessment:FR-008] - Route assessment evidence for LLM
        route_evidence_text = "No cycling route assessments were performed."
        if self._route_assessments:
            route_lines = []
            for ra in self._route_assessments:
                dest = ra.get("destination", "Unknown")
                distance = ra.get("distance_m", 0)
                score = ra.get("score", {})
                rating = score.get("rating", "unknown")
                score_val = score.get("score", 0)
                issues = ra.get("issues", [])
                s106 = ra.get("s106_suggestions", [])

                route_lines.append(f"### Route to {dest}")
                route_lines.append(f"- Distance: {distance}m, LTN 1/20 score: {score_val}/100 ({rating})")

                # Provision breakdown
                provision = ra.get("provision_breakdown", {})
                if provision:
                    prov_parts = [f"{k}: {v}m" for k, v in provision.items() if v > 0]
                    if prov_parts:
                        route_lines.append(f"- Provision: {', '.join(prov_parts)}")

                # Issues
                if issues:
                    route_lines.append("- Issues:")
                    for issue in issues:
                        route_lines.append(f"  - [{issue.get('severity', 'unknown')}] {issue.get('problem', '')}")
                        if issue.get("suggested_improvement"):
                            route_lines.append(f"    Suggestion: {issue['suggested_improvement']}")

                # S106 suggestions
                if s106:
                    route_lines.append("- S106 funding suggestions:")
                    for sug in s106:
                        route_lines.append(f"  - {sug.get('suggestion', '')}")

            route_evidence_text = "\n".join(route_lines)

        return app_summary, ingested_docs_text, app_evidence_text, policy_evidence_text, plans_submitted_text, route_evidence_text

    def _build_route_evidence_summary(self) -> str:
        """
        Build a condensed route evidence summary for the structure call.

        Implements [reliable-structure-extraction:FR-004] - Route evidence summarization
        Implements [reliable-structure-extraction:NFR-002] - Token reduction

        Per destination includes: distance, LTN 1/20 score and rating,
        provision breakdown as percentages, issue counts by severity,
        and top 5 highest-severity issues with descriptions.

        Excludes: full segment lists, geometry coordinates, S106 details,
        and low-severity issues beyond the top 5.
        """
        if not self._route_assessments:
            return "No cycling route assessments were performed."

        summary_lines = []
        for ra in self._route_assessments:
            dest = ra.get("destination", "Unknown")
            distance = ra.get("distance_m", 0)
            score = ra.get("score", {})
            rating = score.get("rating", "unknown")
            score_val = score.get("score", 0)
            issues = ra.get("issues", [])

            summary_lines.append(f"### Route to {dest}")
            summary_lines.append(f"- Distance: {distance}m, LTN 1/20 score: {score_val}/100 ({rating})")

            # Provision breakdown as percentages
            provision = ra.get("provision_breakdown", {})
            if provision:
                total = sum(provision.values())
                if total > 0:
                    prov_parts = [
                        f"{k}: {v / total * 100:.0f}%"
                        for k, v in provision.items() if v > 0
                    ]
                    if prov_parts:
                        summary_lines.append(f"- Provision: {', '.join(prov_parts)}")

            # Issue counts by severity
            severity_order = {"high": 0, "medium": 1, "low": 2}
            high_count = sum(1 for i in issues if i.get("severity") == "high")
            med_count = sum(1 for i in issues if i.get("severity") == "medium")
            low_count = sum(1 for i in issues if i.get("severity") == "low")
            summary_lines.append(f"- Issues: {high_count} high, {med_count} medium, {low_count} low")

            # Top 5 highest-severity issues
            if issues:
                sorted_issues = sorted(
                    issues,
                    key=lambda i: severity_order.get(i.get("severity", "low"), 3),
                )
                top_issues = sorted_issues[:5]
                for issue in top_issues:
                    summary_lines.append(
                        f"  - [{issue.get('severity', 'unknown')}] {issue.get('problem', '')}"
                    )

        return "\n".join(summary_lines)

    async def _phase_generate_review(self) -> None:
        """
        Phase 5: Generate the review using two sequential Claude API calls.

        Implements [structured-review-output:FR-001] - Two-phase generation
        Implements [structured-review-output:FR-004] - Structured fields from JSON
        Implements [structured-review-output:FR-005] - No ReviewMarkdownParser
        Implements [structured-review-output:FR-007] - Fallback on structure call failure
        Implements [structured-review-output:NFR-001] - Token budget split
        Implements [structured-review-output:NFR-002] - Duration logging
        """
        await self._progress.update_sub_progress("Generating review with Claude")

        model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")
        api_key = os.getenv("ANTHROPIC_API_KEY")

        if not api_key:
            raise OrchestratorError(
                "ANTHROPIC_API_KEY not set",
                phase=ReviewPhase.GENERATING_REVIEW,
                recoverable=False,
            )

        app_summary, ingested_docs_text, app_evidence_text, policy_evidence_text, plans_submitted_text, route_evidence_text = (
            self._build_evidence_context()
        )

        # Implements [reliable-structure-extraction:FR-004] - Condensed route summary for structure call
        route_summary_text = self._build_route_evidence_summary()

        try:
            client = anthropic.Anthropic(api_key=api_key)

            # --- Phase 5a: Structure call via tool_use ---
            # Implements [reliable-structure-extraction:FR-001] - Tool use instead of raw JSON
            structure: ReviewStructure | None = None
            structure_json_str: str | None = None
            structure_input_tokens = 0
            structure_output_tokens = 0

            # Implements [reliable-structure-extraction:FR-002] - Schema from Pydantic model
            tool_schema = ReviewStructure.model_json_schema()
            tool_name = "submit_review_structure"
            tools = [
                {
                    "name": tool_name,
                    "description": "Submit the structured review assessment for this planning application.",
                    "input_schema": tool_schema,
                }
            ]

            await self._progress.update_sub_progress("Structure call: getting JSON assessment")

            structure_start = time.monotonic()
            try:
                system_prompt, user_prompt = build_structure_prompt(
                    app_summary, ingested_docs_text, app_evidence_text, policy_evidence_text,
                    plans_submitted_text, route_summary_text,
                )
                structure_msg = client.messages.create(
                    model=model,
                    max_tokens=8000,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                    tools=tools,
                    tool_choice={"type": "tool", "name": tool_name},
                )
                structure_input_tokens = structure_msg.usage.input_tokens
                structure_output_tokens = structure_msg.usage.output_tokens

                # Extract tool_use content block
                tool_use_block = None
                for block in structure_msg.content:
                    if block.type == "tool_use":
                        tool_use_block = block
                        break

                if tool_use_block is None:
                    raise ValueError("Structure call returned no tool_use content block")

                # Validate the tool input dict with Pydantic
                structure = ReviewStructure.model_validate(tool_use_block.input)
                structure_json_str = json.dumps(tool_use_block.input)

                logger.info(
                    "Structure call succeeded",
                    review_id=self._review_id,
                    structure_call_seconds=round(time.monotonic() - structure_start, 2),
                    structure_call_tokens=structure_input_tokens + structure_output_tokens,
                    aspects_count=len(structure.aspects),
                    compliance_count=len(structure.policy_compliance),
                    recommendations_count=len(structure.recommendations),
                    stop_reason=structure_msg.stop_reason,
                )

            # Implements [reliable-structure-extraction:FR-006] - Fallback preserved
            except (ValidationError, ValueError) as e:
                logger.warning(
                    "Structure call failed, falling back to single-call",
                    review_id=self._review_id,
                    error=str(e),
                    structure_call_seconds=round(time.monotonic() - structure_start, 2),
                )
                structure = None

            except anthropic.APIError as e:
                logger.warning(
                    "Structure call API error, falling back to single-call",
                    review_id=self._review_id,
                    error=str(e),
                    structure_call_seconds=round(time.monotonic() - structure_start, 2),
                )
                structure = None

            # --- Phase 5b: Report call (or fallback) ---
            report_input_tokens = 0
            report_output_tokens = 0
            group_stylised = os.getenv("ADVOCACY_GROUP_STYLISED", "Bicester BUG")

            if structure is not None:
                # Two-phase: report call with JSON as outline
                await self._progress.update_sub_progress("Report call: writing markdown report")

                report_start = time.monotonic()
                report_system, report_user = build_report_prompt(
                    structure_json_str, app_summary, ingested_docs_text,
                    app_evidence_text, policy_evidence_text, plans_submitted_text,
                    route_evidence_text, group_stylised=group_stylised,
                )
                report_msg = client.messages.create(
                    model=model,
                    max_tokens=12000,
                    system=report_system,
                    messages=[{"role": "user", "content": report_user}],
                )
                report_input_tokens = report_msg.usage.input_tokens
                report_output_tokens = report_msg.usage.output_tokens

                review_markdown = report_msg.content[0].text

                logger.info(
                    "Report call succeeded",
                    review_id=self._review_id,
                    report_call_seconds=round(time.monotonic() - report_start, 2),
                    report_call_tokens=report_input_tokens + report_output_tokens,
                )

                # Build structured fields from the validated structure call
                overall_rating = structure.overall_rating
                aspects = [
                    {"name": a.name, "rating": a.rating, "key_issue": a.key_issue}
                    for a in structure.aspects
                ]
                policy_compliance = [
                    {
                        "requirement": c.requirement,
                        "policy_source": c.policy_source,
                        "compliant": c.compliant,
                        "notes": c.notes,
                    }
                    for c in structure.policy_compliance
                ]
                recommendations = list(structure.recommendations)
                suggested_conditions = list(structure.suggested_conditions)
                summary = structure.summary
                key_documents = [
                    {
                        "title": d.title,
                        "category": d.category,
                        "summary": d.summary,
                        "url": d.url,
                    }
                    for d in structure.key_documents
                ]

            else:
                # Fallback: single markdown call (no structured field extraction)
                # Implements [structured-review-output:FR-007]
                await self._progress.update_sub_progress(
                    "Fallback: generating markdown review"
                )

                fallback_system, fallback_user = build_report_prompt(
                    "{}",  # empty JSON — report call will just produce a review
                    app_summary, ingested_docs_text,
                    app_evidence_text, policy_evidence_text, plans_submitted_text,
                    route_evidence_text, group_stylised=group_stylised,
                )
                # Override the system prompt for fallback — don't reference JSON
                fallback_system = f"""You are a planning application reviewer acting on behalf of a local cycling advocacy group in the Cherwell District. Your role is to assess planning applications from the perspective of people who walk and cycle.

Write a concise cycle advocacy review in markdown format with these sections:
1. # {group_stylised} Review: [Reference]
2. ## Application Summary
3. ## Key Documents
4. ## Assessment Summary (with Overall Rating and aspect table)
5. ## Detailed Assessment (subsections per aspect, 1-3 paragraphs each)
6. ## Policy Compliance Matrix (table)
7. ## Recommendations (numbered list, one sentence each)
8. ## Suggested Conditions (numbered list in standard LPA format, if any)

Be concise and evidence-based. Cite specific policy references."""

                fallback_msg = client.messages.create(
                    model=model,
                    max_tokens=12000,
                    system=fallback_system,
                    messages=[{"role": "user", "content": fallback_user}],
                )
                report_input_tokens = fallback_msg.usage.input_tokens
                report_output_tokens = fallback_msg.usage.output_tokens

                review_markdown = fallback_msg.content[0].text

                # Parse overall rating from the markdown (best effort)
                overall_rating = "amber"
                rating_lower = review_markdown.lower()
                if "overall rating:** red" in rating_lower or "overall rating:** 🔴" in rating_lower:
                    overall_rating = "red"
                elif "overall rating:** green" in rating_lower or "overall rating:** 🟢" in rating_lower:
                    overall_rating = "green"

                # No structured fields available in fallback
                aspects = None
                policy_compliance = None
                recommendations = None
                suggested_conditions = None
                key_documents = None
                summary = None

            # Combined token tracking
            total_input = structure_input_tokens + report_input_tokens
            total_output = structure_output_tokens + report_output_tokens

            # Store review result
            result = ReviewResult(
                review_id=self._review_id,
                application_ref=self._application_ref,
                application=self._application,
                review={
                    "overall_rating": overall_rating,
                    "key_documents": key_documents,
                    "aspects": aspects,
                    "policy_compliance": policy_compliance,
                    "recommendations": recommendations,
                    "suggested_conditions": suggested_conditions,
                    "full_markdown": review_markdown,
                    # Implements [review-workflow-redesign:FR-006] - LLM-generated summary
                    "summary": summary,
                    "model": model,
                    "input_tokens": total_input,
                    "output_tokens": total_output,
                    # Implements [cycle-route-assessment:FR-008] - Route assessments in output
                    "route_assessments": self._route_assessments or None,
                },
                metadata={
                    "model": model,
                    "total_tokens_used": total_input + total_output,
                    "evidence_chunks_used": len(getattr(self, "_evidence_chunks", [])),
                    "documents_analysed": (
                        self._ingestion_result.documents_ingested
                        if self._ingestion_result
                        else 0
                    ),
                    # Implements [document-type-detection:FR-004] - Plans submitted in metadata
                    "plans_submitted": (
                        self._ingestion_result.skipped_documents
                        if self._ingestion_result
                        else []
                    ),
                    # Implements [cycle-route-assessment:FR-008] - Site boundary in output
                    "site_boundary": self._site_boundary,
                    "previous_review_id": self._previous_review_id,
                    "documents_reused": self._resubmission_stats["documents_reused"],
                    "documents_new": self._resubmission_stats["documents_new"],
                    "documents_removed": self._resubmission_stats["documents_removed"],
                },
                success=True,
            )

            self._review_result = result

            logger.info(
                "Review generated",
                review_id=self._review_id,
                overall_rating=overall_rating,
                two_phase=structure is not None,
                structure_call_tokens=structure_input_tokens + structure_output_tokens,
                report_call_tokens=report_input_tokens + report_output_tokens,
                total_tokens=total_input + total_output,
            )

        except anthropic.APIError as e:
            raise OrchestratorError(
                f"Claude API error: {e}",
                phase=ReviewPhase.GENERATING_REVIEW,
                recoverable=False,
            )

    async def _phase_verify_review(self) -> None:
        """
        Phase 7: Verify review claims against evidence.

        Implements [review-workflow-redesign:FR-005] - Post-generation verification
        Implements [review-workflow-redesign:NFR-003] - Verification duration logging
        Implements [review-workflow-redesign:NFR-005] - Verification metadata in output

        Verification failure is best-effort: errors are logged but do not fail the review.
        """
        if not self._review_result or not self._review_result.review:
            logger.warning(
                "No review to verify",
                review_id=self._review_id,
            )
            return

        await self._progress.update_sub_progress("Verifying review claims")

        review = self._review_result.review
        review_markdown = review.get("full_markdown", "")

        # Build structure dict for the verification prompt
        review_structure = {
            "overall_rating": review.get("overall_rating"),
            "summary": review.get("summary"),
            "aspects": review.get("aspects", []),
            "policy_compliance": review.get("policy_compliance", []),
            "recommendations": review.get("recommendations", []),
            "suggested_conditions": review.get("suggested_conditions", []),
            "key_documents": review.get("key_documents", []),
        }

        # Build ingested documents list
        ingested_docs: list[dict[str, Any]] = []
        if self._ingestion_result and self._ingestion_result.document_metadata:
            for file_path, meta in self._ingestion_result.document_metadata.items():
                ingested_docs.append({
                    "description": meta.get("description") or os.path.basename(file_path),
                    "document_type": meta.get("document_type", "Unknown"),
                    "url": meta.get("url", ""),
                })

        evidence_chunks = getattr(self, "_evidence_chunks", [])

        verify_start = time.monotonic()
        try:
            system_prompt, user_prompt = build_verification_prompt(
                review_markdown, review_structure, ingested_docs, evidence_chunks
            )

            api_key = os.getenv("ANTHROPIC_API_KEY")
            client = anthropic.Anthropic(api_key=api_key)
            verify_msg = client.messages.create(
                model=self._filter_model,
                max_tokens=4096,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )

            raw_text = verify_msg.content[0].text.strip()
            if raw_text.startswith("```"):
                lines = raw_text.split("\n")
                lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                raw_text = "\n".join(lines)

            verification_data = json.loads(raw_text)
            claims = verification_data.get("claims", [])

            verified_count = sum(1 for c in claims if c.get("verified"))
            unverified_count = len(claims) - verified_count

            if unverified_count == 0:
                status = "verified"
            elif verified_count > 0:
                status = "partial"
            else:
                status = "failed"

            verification = {
                "status": status,
                "verified_claims": verified_count,
                "unverified_claims": unverified_count,
                "total_claims": len(claims),
                "details": claims,
                "duration_seconds": round(time.monotonic() - verify_start, 2),
            }

            # Merge verification into review result metadata
            self._review_result.metadata["verification"] = verification

            # Implements [review-workflow-redesign:NFR-003] - Verification duration logging
            logger.info(
                "Verification completed",
                review_id=self._review_id,
                duration_seconds=verification["duration_seconds"],
                status=status,
                verified_claims=verified_count,
                unverified_claims=unverified_count,
            )

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(
                "Verification returned invalid response",
                review_id=self._review_id,
                error=str(e),
                duration_seconds=round(time.monotonic() - verify_start, 2),
            )
        except anthropic.APIError as e:
            logger.warning(
                "Verification API error",
                review_id=self._review_id,
                error=str(e),
                duration_seconds=round(time.monotonic() - verify_start, 2),
            )
        except Exception as e:
            logger.warning(
                "Unexpected verification error",
                review_id=self._review_id,
                error=str(e),
                duration_seconds=round(time.monotonic() - verify_start, 2),
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
