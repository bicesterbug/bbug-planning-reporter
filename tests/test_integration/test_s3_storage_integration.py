"""
Integration tests for S3 document storage across the full review/letter pipeline.

Implements [s3-document-storage:ITS-01] through [ITS-04]

These tests exercise the real AgentOrchestrator, process_review, and letter_job
with InMemoryStorageBackend (not mocked), verifying that storage operations
flow correctly through the entire pipeline.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from src.agent.orchestrator import AgentOrchestrator
from src.shared.storage import InMemoryStorageBackend, LocalStorageBackend, StorageUploadError
from src.worker.letter_jobs import letter_job
from src.worker.review_jobs import process_review

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_claude_response(
    markdown: str = "# Review\n**Overall Rating:** AMBER\n## Key Documents\n...",
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


@pytest.fixture(autouse=True)
def _sequential_ingestion(monkeypatch):
    """Force sequential ingestion so side_effect lists are consumed in order."""
    monkeypatch.setenv("INGEST_CONCURRENCY", "1")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_mcp_client():
    """Create a mock MCP client."""
    from src.agent.mcp_client import MCPClientManager, MCPServerType

    client = AsyncMock(spec=MCPClientManager)
    client.initialize = AsyncMock()
    client.close = AsyncMock()
    client.call_tool = AsyncMock()
    # Return False for CYCLE_ROUTE so ASSESSING_ROUTES phase is skipped
    client.is_connected = MagicMock(
        side_effect=lambda server_type=None: server_type != MCPServerType.CYCLE_ROUTE
    )
    return client


@pytest.fixture
def mock_redis():
    """Create a mock Redis client for orchestrator."""
    r = AsyncMock()
    r.set = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.delete = AsyncMock()
    r.publish = AsyncMock()
    r.exists = AsyncMock(return_value=False)
    return r


@pytest.fixture
def mock_redis_wrapper():
    """Create a mock RedisClient for worker jobs."""
    wrapper = AsyncMock()
    wrapper.update_job_status = AsyncMock()
    wrapper.store_result = AsyncMock()
    wrapper.get_job = AsyncMock(return_value=None)
    wrapper.get_letter = AsyncMock()
    wrapper.get_result = AsyncMock()
    wrapper.update_letter_status = AsyncMock()
    return wrapper


@pytest.fixture
def sample_application_response():
    """Sample response from get_application_details."""
    return {
        "status": "success",
        "application": {
            "reference": "25/01178/REM",
            "address": "Land at Test Site, Bicester",
            "proposal": "Reserved matters for residential development",
            "applicant": "Test Developments Ltd",
            "status": "Under consideration",
            "date_validated": "2025-01-20",
            "consultation_end": "2025-02-15",
            "documents": [
                {"id": "doc1", "name": "Transport Assessment.pdf"},
                {"id": "doc2", "name": "Site Plan.pdf"},
                {"id": "doc3", "name": "Design Statement.pdf"},
            ],
        },
    }


@pytest.fixture
def sample_list_documents_response():
    """Sample response from list_application_documents."""
    return {
        "status": "success",
        "documents": [
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
                "date_published": "2024-01-02",
            },
            {
                "document_id": "doc3",
                "description": "Design and Access Statement",
                "document_type": "Design and Access Statement",
                "url": "https://planningregister.cherwell.gov.uk/Document/Download?id=doc3",
                "date_published": "2024-01-03",
            },
        ],
    }


def _s3_download_responses():
    """Per-document download responses with /tmp/raw paths for S3 mode."""
    return [
        {
            "status": "success",
            "file_path": "/tmp/raw/25_01178_REM/001_Transport Assessment.pdf",
            "file_size": 150000,
        },
        {
            "status": "success",
            "file_path": "/tmp/raw/25_01178_REM/002_Site Plan.pdf",
            "file_size": 80000,
        },
        {
            "status": "success",
            "file_path": "/tmp/raw/25_01178_REM/003_Design Statement.pdf",
            "file_size": 120000,
        },
    ]


def _local_download_responses():
    """Per-document download responses with /data/raw paths for local mode."""
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


def _make_filter_response(doc_ids: list[str] | None = None):
    """Build a mock Anthropic Messages response for the document filter call."""
    ids = doc_ids or ["doc1", "doc2", "doc3"]
    content_block = SimpleNamespace(text=json.dumps(ids))
    usage = SimpleNamespace(input_tokens=500, output_tokens=100)
    return SimpleNamespace(content=[content_block], usage=usage)


def _make_query_response():
    """Build a mock Anthropic Messages response for the query generation call."""
    query_json = json.dumps({
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
    return _make_claude_response(
        markdown=query_json,
        input_tokens=300,
        output_tokens=150,
    )


def _make_verification_response():
    """Build a mock Anthropic Messages response for the verification call."""
    verification_json = json.dumps({
        "claims": [
            {"claim": "The development includes cycle parking", "verified": True, "source": "Transport Assessment"},
            {"claim": "NPPF paragraph 115 requires sustainable transport", "verified": True, "source": "NPPF evidence chunk"},
            {"claim": "No off-site cycle connections provided", "verified": True, "source": "Transport Assessment"},
        ],
    })
    return _make_claude_response(
        markdown=verification_json,
        input_tokens=400,
        output_tokens=200,
    )


@pytest.fixture
def sample_ingest_response():
    """Sample response from ingest_document."""
    return {"status": "success", "document_id": "doc_test123", "chunks_created": 15}


@pytest.fixture
def sample_search_response():
    """Sample response from search tools."""
    return {
        "results": [
            {
                "text": "The proposed development includes cycle parking.",
                "metadata": {"source_file": "ta.pdf"},
            },
        ],
    }


# ---------------------------------------------------------------------------
# ITS-01: Full review with S3 storage
# ---------------------------------------------------------------------------


class TestFullReviewWithS3Storage:
    """
    Integration test for full review flow with S3 storage.

    Implements [s3-document-storage:ITS-01]

    Given: S3 configured (InMemoryStorageBackend), mock MCP client returns downloads
    When: Review job runs to completion
    Then: All documents uploaded to backend, URLs in review are S3 URLs,
          temp files cleaned up, review JSON+MD uploaded to backend
    """

    @pytest.mark.asyncio
    async def test_full_review_with_s3_storage(
        self,
        mock_mcp_client,
        mock_redis,
        mock_redis_wrapper,
        monkeypatch,
        sample_application_response,
        sample_list_documents_response,
        sample_ingest_response,
        sample_search_response,
    ):
        """
        Verifies [s3-document-storage:ITS-01] - Full review with S3 storage

        End-to-end: process_review → AgentOrchestrator → InMemoryStorageBackend
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        # Create fake downloaded files at the paths the MCP mock will return.
        # In production, the scraper creates these; here we simulate their existence.
        raw_dir = Path("/tmp/raw/25_01178_REM")
        raw_dir.mkdir(parents=True, exist_ok=True)
        created_files: list[Path] = []
        s3_downloads = _s3_download_responses()

        try:
            for dl in s3_downloads:
                fp = Path(dl["file_path"])
                fp.write_bytes(b"%PDF-1.4 fake content for " + fp.name.encode())
                created_files.append(fp)

            # Real InMemoryStorageBackend (not mocked)
            backend = InMemoryStorageBackend(
                base_url="https://test-bucket.nyc3.digitaloceanspaces.com"
            )

            key_docs = [
                {
                    "title": "Transport Assessment",
                    "category": "Transport & Access",
                    "summary": "Analyses traffic impacts.",
                    "url": "https://test-bucket.nyc3.digitaloceanspaces.com/25_01178_REM/001_Transport Assessment.pdf",
                },
            ]

            structure_resp = _make_claude_response(
                markdown=(
                    '{"overall_rating": "amber", "aspects": [], '
                    '"policy_compliance": [], "recommendations": [], '
                    '"suggested_conditions": [], "key_documents": []}'
                ),
            )
            # Overwrite content to be raw JSON (structure call returns JSON, not markdown)
            structure_resp.content[0].text = json.dumps({
                "overall_rating": "amber",
                "summary": "The application provides basic infrastructure but requires further assessment.",
                "aspects": [],
                "policy_compliance": [],
                "recommendations": [],
                "suggested_conditions": [],
                "key_documents": [
                    {
                        "title": "Transport Assessment",
                        "category": "Transport & Access",
                        "summary": "Analyses traffic impacts.",
                        "url": "https://test-bucket.nyc3.digitaloceanspaces.com/25_01178_REM/001_Transport Assessment.pdf",
                    },
                ],
            })

            report_resp = _make_claude_response(
                markdown=(
                    "# Cycle Advocacy Review: 25/01178/REM\n\n"
                    "## Application Summary\n...\n"
                    "## Key Documents\n...\n"
                    "## Assessment Summary\n**Overall Rating:** AMBER\n"
                ),
                key_documents_json=key_docs,
            )

            filter_resp = _make_filter_response(["doc1", "doc2", "doc3"])

            mock_mcp_client.call_tool.side_effect = [
                sample_application_response,          # Phase 1: get_application_details
                sample_list_documents_response,       # Phase 2: list_application_documents
                *s3_downloads,                        # Phase 3: download_document x3
                sample_ingest_response,               # Phase 4: ingest_document x3
                sample_ingest_response,
                sample_ingest_response,
                *_search_side_effects(7, sample_search_response),  # Phase 5: searches
            ]

            with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic, \
                 patch("src.worker.review_jobs.AgentOrchestrator") as MockOrchCls, \
                 patch("src.worker.review_jobs.create_storage_backend", return_value=backend):

                # Set up the real orchestrator but inject it via the mock class
                real_orchestrator = AgentOrchestrator(
                    review_id="rev_its01",
                    application_ref="25/01178/REM",
                    mcp_client=mock_mcp_client,
                    redis_client=mock_redis,
                    storage_backend=backend,
                )

                MockOrchCls.return_value = real_orchestrator

                mock_claude_client = MagicMock()
                # Filter call, then query generation, then structure call, then report call, then verify
                mock_claude_client.messages.create.side_effect = [
                    filter_resp,
                    _make_query_response(),
                    structure_resp,
                    report_resp,
                    _make_verification_response(),
                ]
                MockAnthropic.return_value = mock_claude_client

                ctx = {
                    "redis": mock_redis,
                    "redis_client": mock_redis_wrapper,
                }

                result = await process_review(
                    ctx=ctx,
                    review_id="rev_its01",
                    application_ref="25/01178/REM",
                )

            # --- Assertions ---

            # Review completed successfully
            assert result["status"] == "completed"
            assert result["review_id"] == "rev_its01"

            # Documents were uploaded to InMemoryStorageBackend
            # 3 documents + review JSON + review MD = 5 uploads
            upload_keys = list(backend.uploads.keys())
            assert len(backend.uploads) >= 3, (
                f"Expected at least 3 uploads, got {len(backend.uploads)}: {upload_keys}"
            )

            # Document uploads present (keyed by relative path stripped of output_dir)
            doc_keys = [k for k in upload_keys if "001_Transport" in k or "002_Site" in k or "003_Design" in k]
            assert len(doc_keys) == 3, f"Expected 3 document uploads, got keys: {upload_keys}"

            # Review output uploads exist
            review_json_keys = [k for k in upload_keys if "rev_its01_review.json" in k]
            review_md_keys = [k for k in upload_keys if "rev_its01_review.md" in k]
            assert len(review_json_keys) == 1, f"Expected 1 review JSON upload, got: {upload_keys}"
            assert len(review_md_keys) == 1, f"Expected 1 review MD upload, got: {upload_keys}"

            # Verify the review JSON content is valid
            review_json_content = json.loads(backend.uploads[review_json_keys[0]])
            assert review_json_content["review_id"] == "rev_its01"
            assert review_json_content["status"] == "completed"

            # Temp files were marked for cleanup (listed in backend.deleted)
            assert len(backend.deleted) == 3, (
                f"Expected 3 temp files cleaned up, got {len(backend.deleted)}"
            )
            assert any("001_Transport" in d for d in backend.deleted)
            assert any("002_Site" in d for d in backend.deleted)
            assert any("003_Design" in d for d in backend.deleted)

            # Verify URLs in document metadata are S3 URLs (not Cherwell)
            for fp, meta in real_orchestrator._ingestion_result.document_metadata.items():
                assert "test-bucket.nyc3.digitaloceanspaces.com" in meta["url"], (
                    f"Expected S3 URL for {fp}, got {meta['url']}"
                )

            # Result was stored in Redis
            mock_redis_wrapper.store_result.assert_called_once()

            await real_orchestrator.close()

        finally:
            # Clean up any remaining temp files
            import shutil

            for fp in created_files:
                fp.unlink(missing_ok=True)
            if raw_dir.exists():
                shutil.rmtree(raw_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# ITS-02: Full review with local storage
# ---------------------------------------------------------------------------


class TestFullReviewWithLocalStorage:
    """
    Integration test for full review flow with local storage (no S3).

    Implements [s3-document-storage:ITS-02]

    Given: No S3 configured
    When: Review job runs to completion
    Then: No upload calls, Cherwell URLs in review, files remain at /data/raw,
          no temp cleanup
    """

    @pytest.mark.asyncio
    async def test_full_review_with_local_storage(
        self,
        mock_mcp_client,
        mock_redis,
        mock_redis_wrapper,
        monkeypatch,
        sample_application_response,
        sample_list_documents_response,
        sample_ingest_response,
        sample_search_response,
    ):
        """
        Verifies [s3-document-storage:ITS-02] - Full review with local storage

        End-to-end: process_review → AgentOrchestrator → LocalStorageBackend
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        # Real LocalStorageBackend
        backend = LocalStorageBackend()
        local_downloads = _local_download_responses()

        structure_resp = _make_claude_response()
        structure_resp.content[0].text = json.dumps({
            "overall_rating": "green",
            "summary": "Good cycling provision with compliant infrastructure.",
            "aspects": [],
            "policy_compliance": [],
            "recommendations": [],
            "suggested_conditions": [],
            "key_documents": [
                {
                    "title": "Transport Assessment",
                    "category": "Transport & Access",
                    "summary": "Analyses traffic impacts.",
                    "url": "https://planningregister.cherwell.gov.uk/Document/Download?id=doc1",
                },
            ],
        })

        report_resp = _make_claude_response(
            markdown=(
                "# Cycle Advocacy Review: 25/01178/REM\n\n"
                "## Assessment Summary\n**Overall Rating:** GREEN\n"
            ),
            key_documents_json=[
                {
                    "title": "Transport Assessment",
                    "category": "Transport & Access",
                    "summary": "Analyses traffic impacts.",
                    "url": "https://planningregister.cherwell.gov.uk/Document/Download?id=doc1",
                },
            ],
        )

        filter_resp = _make_filter_response(["doc1", "doc2", "doc3"])

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,          # Phase 1: get_application_details
            sample_list_documents_response,       # Phase 2: list_application_documents
            *local_downloads,                     # Phase 3: download_document x3
            sample_ingest_response,               # Phase 4: ingest_document x3
            sample_ingest_response,
            sample_ingest_response,
            *_search_side_effects(7, sample_search_response),  # Phase 5: searches
        ]

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic, \
             patch("src.worker.review_jobs.AgentOrchestrator") as MockOrchCls, \
             patch("src.worker.review_jobs.create_storage_backend", return_value=backend):

            real_orchestrator = AgentOrchestrator(
                review_id="rev_its02",
                application_ref="25/01178/REM",
                mcp_client=mock_mcp_client,
                redis_client=mock_redis,
                storage_backend=backend,
            )

            MockOrchCls.return_value = real_orchestrator

            mock_claude_client = MagicMock()
            # Filter call, then query generation, then structure call, then report call, then verify
            mock_claude_client.messages.create.side_effect = [
                filter_resp,
                _make_query_response(),
                structure_resp,
                report_resp,
                _make_verification_response(),
            ]
            MockAnthropic.return_value = mock_claude_client

            ctx = {
                "redis": mock_redis,
                "redis_client": mock_redis_wrapper,
            }

            result = await process_review(
                ctx=ctx,
                review_id="rev_its02",
                application_ref="25/01178/REM",
            )

        # --- Assertions ---

        # Review completed successfully
        assert result["status"] == "completed"

        # Verify first download_document call used the correct output_dir
        # call_args_list: [0]=get_application_details, [1]=list_documents,
        # [2]=download_doc1, [3]=download_doc2, [4]=download_doc3, ...
        download_call = mock_mcp_client.call_tool.call_args_list[2]
        assert download_call[0][1]["output_dir"] == "/data/raw/25_01178_REM"

        # Verify Cherwell URLs are preserved in the review's document metadata
        # (The orchestrator builds document_metadata with Cherwell URLs when local)
        ingestion = real_orchestrator._ingestion_result
        assert ingestion is not None
        for file_path, meta in ingestion.document_metadata.items():
            assert "cherwell.gov.uk" in meta["url"], (
                f"Expected Cherwell URL for {file_path}, got {meta['url']}"
            )

        # No temp file cleanup (local backend's delete_local is a no-op)
        # Verify by checking files start with /data/raw, not /tmp/raw
        for doc_path in ingestion.document_paths:
            assert doc_path.startswith("/data/raw"), f"Expected /data/raw path, got {doc_path}"

        # Result stored in Redis
        mock_redis_wrapper.store_result.assert_called_once()

        await real_orchestrator.close()


# ---------------------------------------------------------------------------
# ITS-03: Letter output upload with S3
# ---------------------------------------------------------------------------


class TestLetterOutputUploadWithS3:
    """
    Integration test for letter output upload to S3.

    Implements [s3-document-storage:ITS-03]

    Given: S3 configured (InMemoryStorageBackend), completed review in Redis
    When: Letter job runs
    Then: Letter JSON+MD uploaded to backend at correct S3 key
    """

    @pytest.mark.asyncio
    async def test_letter_output_upload_to_s3(
        self,
        mock_redis_wrapper,
    ):
        """
        Verifies [s3-document-storage:ITS-03] - Letter output upload

        End-to-end: letter_job → InMemoryStorageBackend
        """
        backend = InMemoryStorageBackend(
            base_url="https://test-bucket.nyc3.digitaloceanspaces.com"
        )

        letter_record = {
            "letter_id": "ltr_its03",
            "review_id": "rev_its03",
            "application_ref": "25/01178/REM",
            "stance": "object",
            "tone": "formal",
            "case_officer": None,
            "letter_date": None,
            "status": "generating",
            "content": None,
            "metadata": None,
            "error": None,
            "created_at": datetime.now(UTC).isoformat(),
            "completed_at": None,
        }

        review_result = {
            "review_id": "rev_its03",
            "application_ref": "25/01178/REM",
            "status": "completed",
            "application": {
                "reference": "25/01178/REM",
                "address": "Land at Test Site, Bicester",
                "proposal": "Reserved matters for 150 dwellings",
                "applicant": "Test Developments Ltd",
                "case_officer": "Ms J. Smith",
            },
            "review": {
                "overall_rating": "amber",
                "full_markdown": (
                    "# Cycle Advocacy Review: 25/01178/REM\n\n"
                    "**Overall Rating:** AMBER\n\n"
                    "The application provides insufficient cycle parking.\n"
                ),
            },
            "metadata": {"model": "claude-sonnet-4-5-20250929"},
        }

        mock_redis_wrapper.get_letter.return_value = letter_record
        mock_redis_wrapper.get_result.return_value = review_result

        ctx = {"redis_client": mock_redis_wrapper}

        mock_anthropic_response = MagicMock()
        mock_anthropic_response.content = [
            MagicMock(text="# Response Letter\n\nDear Ms J. Smith,\n\nWe write regarding...")
        ]
        mock_anthropic_response.usage = MagicMock(input_tokens=3000, output_tokens=1500)

        with patch("src.worker.letter_jobs.anthropic") as mock_anthropic_mod, \
             patch("src.worker.letter_jobs.create_storage_backend", return_value=backend), \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):

            mock_client = MagicMock()
            mock_anthropic_mod.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = mock_anthropic_response

            result = await letter_job(
                ctx=ctx,
                letter_id="ltr_its03",
                review_id="rev_its03",
            )

        # --- Assertions ---

        # Letter completed
        assert result["status"] == "completed"
        assert result["letter_id"] == "ltr_its03"

        # Letter JSON and MD uploaded to S3
        assert len(backend.uploads) == 2, f"Expected 2 uploads, got {len(backend.uploads)}: {list(backend.uploads.keys())}"

        # Verify S3 keys match expected structure
        expected_json_key = "25_01178_REM/output/ltr_its03_letter.json"
        expected_md_key = "25_01178_REM/output/ltr_its03_letter.md"
        assert expected_json_key in backend.uploads, f"Missing {expected_json_key} in {list(backend.uploads.keys())}"
        assert expected_md_key in backend.uploads, f"Missing {expected_md_key} in {list(backend.uploads.keys())}"

        # Verify JSON content
        letter_json = json.loads(backend.uploads[expected_json_key])
        assert letter_json["letter_id"] == "ltr_its03"
        assert letter_json["application_ref"] == "25/01178/REM"
        assert "Dear Ms J. Smith" in letter_json["content"]

        # Verify MD content
        letter_md = backend.uploads[expected_md_key].decode("utf-8")
        assert "Dear Ms J. Smith" in letter_md

        # Redis was also updated
        mock_redis_wrapper.update_letter_status.assert_called_once()
        call_kwargs = mock_redis_wrapper.update_letter_status.call_args.kwargs
        assert call_kwargs["status"] == "completed"


# ---------------------------------------------------------------------------
# ITS-04: S3 upload failure mid-job
# ---------------------------------------------------------------------------


class TestS3UploadFailureMidJob:
    """
    Integration test for partial S3 upload failure during review.

    Implements [s3-document-storage:ITS-04]

    Given: S3 backend configured to fail on 3rd upload
    When: Review job runs
    Then: First 2 docs have S3 URLs, 3rd falls back to Cherwell URL,
          error logged, job continues to completion
    """

    @pytest.mark.asyncio
    async def test_s3_upload_failure_mid_job(
        self,
        mock_mcp_client,
        mock_redis,
        mock_redis_wrapper,
        monkeypatch,
        sample_application_response,
        sample_list_documents_response,
        sample_ingest_response,
        sample_search_response,
    ):
        """
        Verifies [s3-document-storage:ITS-04] - S3 upload failure mid-job

        End-to-end: process_review → AgentOrchestrator → failing mock backend
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        s3_downloads = _s3_download_responses()

        # Create a mock backend that fails on the 3rd upload
        backend = MagicMock()
        type(backend).is_remote = PropertyMock(return_value=True)

        upload_call_count = 0

        def upload_side_effect(local_path, key):
            nonlocal upload_call_count
            upload_call_count += 1
            if upload_call_count == 3:
                raise StorageUploadError(
                    key=key, attempts=3, last_error=Exception("Connection reset")
                )

        backend.upload.side_effect = upload_side_effect
        backend.public_url.side_effect = lambda key: f"https://test-bucket.nyc3.digitaloceanspaces.com/{key}"
        backend.delete_local.return_value = None

        structure_resp = _make_claude_response()
        structure_resp.content[0].text = json.dumps({
            "overall_rating": "amber",
            "summary": "Basic cycling provision with partial policy compliance.",
            "aspects": [],
            "policy_compliance": [],
            "recommendations": [],
            "suggested_conditions": [],
            "key_documents": [],
        })

        report_resp = _make_claude_response(
            markdown="# Review\n**Overall Rating:** AMBER\n",
        )

        filter_resp = _make_filter_response(["doc1", "doc2", "doc3"])

        mock_mcp_client.call_tool.side_effect = [
            sample_application_response,          # Phase 1: get_application_details
            sample_list_documents_response,       # Phase 2: list_application_documents
            *s3_downloads,                        # Phase 3: download_document x3
            sample_ingest_response,               # Phase 4: ingest_document x3
            sample_ingest_response,
            sample_ingest_response,
            *_search_side_effects(7, sample_search_response),  # Phase 5: searches
        ]

        with patch("src.agent.orchestrator.anthropic.Anthropic") as MockAnthropic, \
             patch("src.worker.review_jobs.AgentOrchestrator") as MockOrchCls, \
             patch("src.worker.review_jobs.create_storage_backend", return_value=backend):

            real_orchestrator = AgentOrchestrator(
                review_id="rev_its04",
                application_ref="25/01178/REM",
                mcp_client=mock_mcp_client,
                redis_client=mock_redis,
                storage_backend=backend,
            )

            MockOrchCls.return_value = real_orchestrator

            mock_claude_client = MagicMock()
            # Filter call, then query generation, then structure call, then report call, then verify
            mock_claude_client.messages.create.side_effect = [
                filter_resp,
                _make_query_response(),
                structure_resp,
                report_resp,
                _make_verification_response(),
            ]
            MockAnthropic.return_value = mock_claude_client

            ctx = {
                "redis": mock_redis,
                "redis_client": mock_redis_wrapper,
            }

            result = await process_review(
                ctx=ctx,
                review_id="rev_its04",
                application_ref="25/01178/REM",
            )

        # --- Assertions ---

        # Job still completed despite partial upload failure
        assert result["status"] == "completed"

        # Verify URLs: first 2 docs have S3 URLs, 3rd has Cherwell URL
        meta = real_orchestrator._ingestion_result.document_metadata

        doc1_path = "/tmp/raw/25_01178_REM/001_Transport Assessment.pdf"
        doc2_path = "/tmp/raw/25_01178_REM/002_Site Plan.pdf"
        doc3_path = "/tmp/raw/25_01178_REM/003_Design Statement.pdf"

        # doc1: first upload succeeds → S3 URL
        assert "test-bucket.nyc3.digitaloceanspaces.com" in meta[doc1_path]["url"], (
            f"doc1 should have S3 URL, got: {meta[doc1_path]['url']}"
        )

        # doc2: second upload succeeds → S3 URL
        assert "test-bucket.nyc3.digitaloceanspaces.com" in meta[doc2_path]["url"], (
            f"doc2 should have S3 URL, got: {meta[doc2_path]['url']}"
        )

        # doc3: third upload fails → Cherwell URL preserved
        assert "cherwell.gov.uk" in meta[doc3_path]["url"], (
            f"doc3 should keep Cherwell URL after upload failure, got: {meta[doc3_path]['url']}"
        )

        # All 3 documents were still ingested (upload failure doesn't block ingestion)
        assert real_orchestrator._ingestion_result.documents_ingested == 3

        # Temp files cleaned up for all 3 (ingestion succeeded for all)
        assert backend.delete_local.call_count == 3

        # Result stored in Redis
        mock_redis_wrapper.store_result.assert_called_once()

        await real_orchestrator.close()
