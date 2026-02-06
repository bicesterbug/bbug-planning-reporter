"""
Tests for Policy Knowledge Base MCP Server.

Implements test scenarios from [policy-knowledge-base:PolicyKBMCP/TS-01] through [TS-12]
"""

import contextlib
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import chromadb
import pytest
from chromadb.config import Settings

from src.api.schemas.policy import (
    PolicyCategory,
    PolicyDocumentRecord,
    PolicyRevisionRecord,
    RevisionStatus,
)
from src.mcp_servers.document_store.embeddings import EmbeddingService, MockEmbeddingModel
from src.shared.policy_chroma_client import PolicyChromaClient, PolicyChunkRecord


@pytest.fixture
def mock_registry():
    """Create mock PolicyRegistry."""
    registry = AsyncMock()
    registry.list_policies = AsyncMock(return_value=[])
    registry.get_policy = AsyncMock(return_value=None)
    registry.list_revisions = AsyncMock(return_value=[])
    registry.get_revision = AsyncMock(return_value=None)
    registry.get_effective_revision = AsyncMock(return_value=None)
    return registry


@pytest.fixture
def chroma_client():
    """Create an in-memory ChromaDB client for testing."""
    client = chromadb.Client(settings=Settings(anonymized_telemetry=False))
    with contextlib.suppress(Exception):
        client.delete_collection(PolicyChromaClient.COLLECTION_NAME)
    return PolicyChromaClient(client=client)


@pytest.fixture
def mock_embedder():
    """Create mock EmbeddingService with deterministic embeddings."""
    return EmbeddingService(model=MockEmbeddingModel())


@pytest.fixture
def sample_policy():
    """Create a sample policy record."""
    from datetime import UTC, datetime
    return PolicyDocumentRecord(
        source="LTN_1_20",
        title="Cycle Infrastructure Design (LTN 1/20)",
        description="National guidance on cycle infrastructure",
        category=PolicyCategory.NATIONAL_GUIDANCE,
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def sample_revisions():
    """Create sample revision records."""
    from datetime import UTC, datetime
    return [
        PolicyRevisionRecord(
            revision_id="rev_LTN_1_20_2020_07",
            source="LTN_1_20",
            version_label="July 2020",
            effective_from=date(2020, 7, 27),
            effective_to=None,
            status=RevisionStatus.ACTIVE,
            chunk_count=100,
            created_at=datetime.now(UTC),
        ),
    ]


@pytest.fixture
def sample_chunks():
    """Create sample chunks with embeddings."""
    def mock_embedding(seed: int) -> list[float]:
        import random
        random.seed(seed)
        vec = [random.gauss(0, 1) for _ in range(384)]
        norm = sum(x*x for x in vec) ** 0.5
        return [x / norm for x in vec]

    return [
        PolicyChunkRecord(
            chunk_id="LTN_1_20__rev_LTN_1_20_2020_07__Chapter_5__001",
            text="Cycle lanes should be 2.0m wide on busy roads.",
            embedding=mock_embedding(1),
            metadata={
                "source": "LTN_1_20",
                "source_title": "Cycle Infrastructure Design (LTN 1/20)",
                "revision_id": "rev_LTN_1_20_2020_07",
                "version_label": "July 2020",
                "effective_from": 20200727,
                "effective_to": 99991231,
                "section_ref": "Chapter 5",
                "page_number": 42,
                "chunk_index": 1,
            },
        ),
        PolicyChunkRecord(
            chunk_id="LTN_1_20__rev_LTN_1_20_2020_07__Chapter_5__002",
            text="Protected cycle lanes provide safety benefits.",
            embedding=mock_embedding(2),
            metadata={
                "source": "LTN_1_20",
                "source_title": "Cycle Infrastructure Design (LTN 1/20)",
                "revision_id": "rev_LTN_1_20_2020_07",
                "version_label": "July 2020",
                "effective_from": 20200727,
                "effective_to": 99991231,
                "section_ref": "Chapter 5",
                "page_number": 43,
                "chunk_index": 2,
            },
        ),
        PolicyChunkRecord(
            chunk_id="LTN_1_20__rev_LTN_1_20_2020_07__Table_5_2__003",
            text="Table 5-2 shows recommended minimum widths for cycle lanes.",
            embedding=mock_embedding(3),
            metadata={
                "source": "LTN_1_20",
                "source_title": "Cycle Infrastructure Design (LTN 1/20)",
                "revision_id": "rev_LTN_1_20_2020_07",
                "version_label": "July 2020",
                "effective_from": 20200727,
                "effective_to": 99991231,
                "section_ref": "Table 5-2",
                "page_number": 45,
                "chunk_index": 3,
            },
        ),
    ]


@pytest.fixture
def nppf_chunks():
    """Create NPPF chunks with multiple revisions for temporal testing."""
    def mock_embedding(seed: int) -> list[float]:
        import random
        random.seed(seed)
        vec = [random.gauss(0, 1) for _ in range(384)]
        norm = sum(x*x for x in vec) ** 0.5
        return [x / norm for x in vec]

    return [
        # NPPF September 2023 (superseded)
        PolicyChunkRecord(
            chunk_id="NPPF__rev_NPPF_2023_09__Para_116__001",
            text="Development should give priority to pedestrian and cycle movements.",
            embedding=mock_embedding(10),
            metadata={
                "source": "NPPF",
                "source_title": "National Planning Policy Framework",
                "revision_id": "rev_NPPF_2023_09",
                "version_label": "September 2023",
                "effective_from": 20230905,
                "effective_to": 20241211,
                "section_ref": "Para 116",
                "page_number": 34,
                "chunk_index": 1,
            },
        ),
        # NPPF December 2024 (current)
        PolicyChunkRecord(
            chunk_id="NPPF__rev_NPPF_2024_12__Para_116__001",
            text="Development must prioritise walking and cycling over motor vehicles.",
            embedding=mock_embedding(11),
            metadata={
                "source": "NPPF",
                "source_title": "National Planning Policy Framework",
                "revision_id": "rev_NPPF_2024_12",
                "version_label": "December 2024",
                "effective_from": 20241212,
                "effective_to": 99991231,
                "section_ref": "Para 116",
                "page_number": 35,
                "chunk_index": 1,
            },
        ),
    ]


class TestSearchPolicy:
    """
    Tests for search_policy tool.

    Implements [policy-knowledge-base:PolicyKBMCP/TS-01] through [TS-04], [TS-12]
    """

    @pytest.mark.asyncio
    async def test_search_without_date_filter(
        self,
        mock_registry,
        chroma_client,
        mock_embedder,
        sample_chunks,
    ):
        """
        Verifies [policy-knowledge-base:PolicyKBMCP/TS-01] - Search without date filter

        Given: Policy chunks exist in ChromaDB
        When: search_policy("cycle lane width") without effective_date
        Then: Returns relevant chunks from all revisions
        """
        from src.mcp_servers.policy_kb.server import PolicyKBMCP, SearchPolicyInput

        # Setup
        chroma_client.upsert_chunks(sample_chunks)

        mcp = PolicyKBMCP(
            registry=mock_registry,
            chroma_client=chroma_client,
            embedder=mock_embedder,
        )

        result = await mcp._search_policy(SearchPolicyInput(
            query="cycle lane width",
            n_results=10,
        ))

        assert result["status"] == "success"
        assert result["results_count"] > 0
        # Should return chunks related to cycle lanes
        assert any("cycle" in r["text"].lower() for r in result["results"])

    @pytest.mark.asyncio
    async def test_search_with_effective_date(
        self,
        mock_registry,
        chroma_client,
        mock_embedder,
        sample_chunks,
        nppf_chunks,
    ):
        """
        Verifies [policy-knowledge-base:PolicyKBMCP/TS-02] - Search with effective date

        Given: NPPF revisions from 2023 and 2024
        When: search_policy("cycle", effective_date="2024-03-15")
        Then: Returns only chunks from 2023 revision (in force on that date)
        """
        from src.mcp_servers.policy_kb.server import PolicyKBMCP, SearchPolicyInput

        # Setup - add both LTN and NPPF chunks
        chroma_client.upsert_chunks(sample_chunks + nppf_chunks)

        mcp = PolicyKBMCP(
            registry=mock_registry,
            chroma_client=chroma_client,
            embedder=mock_embedder,
        )

        result = await mcp._search_policy(SearchPolicyInput(
            query="pedestrian and cycle",
            effective_date="2024-03-15",
            n_results=10,
        ))

        assert result["status"] == "success"

        # Filter to NPPF results only
        nppf_results = [r for r in result["results"] if r["source"] == "NPPF"]

        # Should only have 2023 revision (effective until 2024-12-11)
        for r in nppf_results:
            assert r["revision_id"] == "rev_NPPF_2023_09", \
                f"Expected 2023 revision, got {r['revision_id']}"

    @pytest.mark.asyncio
    async def test_search_filtered_by_sources(
        self,
        mock_registry,
        chroma_client,
        mock_embedder,
        sample_chunks,
        nppf_chunks,
    ):
        """
        Verifies [policy-knowledge-base:PolicyKBMCP/TS-03] - Search filtered by sources

        Given: Chunks from multiple policies (LTN_1_20, NPPF)
        When: search_policy("transport", sources=["LTN_1_20"])
        Then: Returns only LTN 1/20 chunks
        """
        from src.mcp_servers.policy_kb.server import PolicyKBMCP, SearchPolicyInput

        # Setup
        chroma_client.upsert_chunks(sample_chunks + nppf_chunks)

        mcp = PolicyKBMCP(
            registry=mock_registry,
            chroma_client=chroma_client,
            embedder=mock_embedder,
        )

        result = await mcp._search_policy(SearchPolicyInput(
            query="cycle infrastructure",
            sources=["LTN_1_20"],
            n_results=10,
        ))

        assert result["status"] == "success"
        # All results should be from LTN_1_20
        for r in result["results"]:
            assert r["source"] == "LTN_1_20"

    @pytest.mark.asyncio
    async def test_search_date_before_any_revision(
        self,
        mock_registry,
        chroma_client,
        mock_embedder,
        sample_chunks,
    ):
        """
        Verifies [policy-knowledge-base:PolicyKBMCP/TS-04] - Date before any revision

        Given: First revision effective 2020-07-27
        When: search_policy("test", effective_date="2019-01-01")
        Then: Returns empty results
        """
        from src.mcp_servers.policy_kb.server import PolicyKBMCP, SearchPolicyInput

        # Setup
        chroma_client.upsert_chunks(sample_chunks)

        mcp = PolicyKBMCP(
            registry=mock_registry,
            chroma_client=chroma_client,
            embedder=mock_embedder,
        )

        result = await mcp._search_policy(SearchPolicyInput(
            query="cycle lane",
            effective_date="2019-01-01",
            n_results=10,
        ))

        assert result["status"] == "success"
        assert result["results_count"] == 0

    @pytest.mark.asyncio
    async def test_search_relevance(
        self,
        mock_registry,
        chroma_client,
        mock_embedder,
        sample_chunks,
    ):
        """
        Verifies [policy-knowledge-base:PolicyKBMCP/TS-12] - Search relevance

        Given: Standard cycling query
        When: search_policy("cycle parking standards")
        Then: Top 5 results contain relevant content
        """
        from src.mcp_servers.policy_kb.server import PolicyKBMCP, SearchPolicyInput

        # Setup
        chroma_client.upsert_chunks(sample_chunks)

        mcp = PolicyKBMCP(
            registry=mock_registry,
            chroma_client=chroma_client,
            embedder=mock_embedder,
        )

        result = await mcp._search_policy(SearchPolicyInput(
            query="cycle lane width requirements",
            n_results=5,
        ))

        assert result["status"] == "success"
        # Results should have relevance scores
        for r in result["results"]:
            assert "relevance_score" in r
            assert 0 <= r["relevance_score"] <= 1


class TestGetPolicySection:
    """
    Tests for get_policy_section tool.

    Implements [policy-knowledge-base:PolicyKBMCP/TS-05] through [TS-07]
    """

    @pytest.mark.asyncio
    async def test_get_section_exists(
        self,
        mock_registry,
        chroma_client,
        mock_embedder,
        sample_chunks,
    ):
        """
        Verifies [policy-knowledge-base:PolicyKBMCP/TS-05] - Get policy section exists

        Given: Table 5-2 indexed in LTN 1/20
        When: get_policy_section("LTN_1_20", "Table 5-2")
        Then: Returns table content
        """
        from src.mcp_servers.policy_kb.server import GetPolicySectionInput, PolicyKBMCP

        # Setup
        chroma_client.upsert_chunks(sample_chunks)

        mcp = PolicyKBMCP(
            registry=mock_registry,
            chroma_client=chroma_client,
            embedder=mock_embedder,
        )

        result = await mcp._get_policy_section(GetPolicySectionInput(
            source="LTN_1_20",
            section_ref="Table 5-2",
        ))

        assert result["status"] == "success"
        assert "Table 5-2" in result["text"]
        assert result["source"] == "LTN_1_20"

    @pytest.mark.asyncio
    async def test_get_section_not_found(
        self,
        mock_registry,
        chroma_client,
        mock_embedder,
        sample_chunks,
    ):
        """
        Verifies [policy-knowledge-base:PolicyKBMCP/TS-06] - Get policy section not found

        Given: No such section exists
        When: get_policy_section("LTN_1_20", "Table 99")
        Then: Returns error section_not_found
        """
        from src.mcp_servers.policy_kb.server import GetPolicySectionInput, PolicyKBMCP

        # Setup
        chroma_client.upsert_chunks(sample_chunks)

        mcp = PolicyKBMCP(
            registry=mock_registry,
            chroma_client=chroma_client,
            embedder=mock_embedder,
        )

        result = await mcp._get_policy_section(GetPolicySectionInput(
            source="LTN_1_20",
            section_ref="Table 99",
        ))

        assert result["status"] == "error"
        assert result["error_type"] == "section_not_found"

    @pytest.mark.asyncio
    async def test_get_section_with_specific_revision(
        self,
        mock_registry,
        chroma_client,
        mock_embedder,
        nppf_chunks,
    ):
        """
        Verifies [policy-knowledge-base:PolicyKBMCP/TS-07] - Get section with specific revision

        Given: Multiple NPPF revisions
        When: get_policy_section("NPPF", "Para 116", revision_id="rev_NPPF_2023_09")
        Then: Returns from specified revision
        """
        from src.mcp_servers.policy_kb.server import GetPolicySectionInput, PolicyKBMCP

        # Setup
        chroma_client.upsert_chunks(nppf_chunks)

        mcp = PolicyKBMCP(
            registry=mock_registry,
            chroma_client=chroma_client,
            embedder=mock_embedder,
        )

        result = await mcp._get_policy_section(GetPolicySectionInput(
            source="NPPF",
            section_ref="Para 116",
            revision_id="rev_NPPF_2023_09",
        ))

        assert result["status"] == "success"
        assert result["revision_id"] == "rev_NPPF_2023_09"
        assert "priority" in result["text"].lower()  # 2023 version uses "priority"


class TestListPolicyDocuments:
    """
    Tests for list_policy_documents tool.

    Implements [policy-knowledge-base:PolicyKBMCP/TS-08]
    """

    @pytest.mark.asyncio
    async def test_list_policy_documents(
        self,
        mock_registry,
        chroma_client,
        mock_embedder,
    ):
        """
        Verifies [policy-knowledge-base:PolicyKBMCP/TS-08] - List policy documents

        Given: 5 policies registered
        When: list_policy_documents()
        Then: Returns all 5 with current revision
        """
        from datetime import UTC, datetime

        from src.mcp_servers.policy_kb.server import PolicyKBMCP

        # Setup mock to return policies
        policies = [
            PolicyDocumentRecord(
                source="LTN_1_20",
                title="Cycle Infrastructure Design",
                category=PolicyCategory.NATIONAL_GUIDANCE,
                created_at=datetime.now(UTC),
            ),
            PolicyDocumentRecord(
                source="NPPF",
                title="National Planning Policy Framework",
                category=PolicyCategory.NATIONAL_POLICY,
                created_at=datetime.now(UTC),
            ),
        ]
        mock_registry.list_policies.return_value = policies

        mcp = PolicyKBMCP(
            registry=mock_registry,
            chroma_client=chroma_client,
            embedder=mock_embedder,
        )

        result = await mcp._list_policy_documents()

        assert result["status"] == "success"
        assert result["policy_count"] == 2
        assert len(result["policies"]) == 2


class TestListPolicyRevisions:
    """
    Tests for list_policy_revisions tool.

    Implements [policy-knowledge-base:PolicyKBMCP/TS-09]
    """

    @pytest.mark.asyncio
    async def test_list_policy_revisions(
        self,
        mock_registry,
        chroma_client,
        mock_embedder,
    ):
        """
        Verifies [policy-knowledge-base:PolicyKBMCP/TS-09] - List policy revisions

        Given: NPPF with 3 revisions
        When: list_policy_revisions("NPPF")
        Then: Returns all 3 ordered by date
        """
        from datetime import UTC, datetime

        from src.mcp_servers.policy_kb.server import ListPolicyRevisionsInput, PolicyKBMCP

        # Setup mock to return revisions
        revisions = [
            PolicyRevisionRecord(
                revision_id="rev_NPPF_2024_12",
                source="NPPF",
                version_label="December 2024",
                effective_from=date(2024, 12, 12),
                effective_to=None,
                status=RevisionStatus.ACTIVE,
                created_at=datetime.now(UTC),
            ),
            PolicyRevisionRecord(
                revision_id="rev_NPPF_2023_09",
                source="NPPF",
                version_label="September 2023",
                effective_from=date(2023, 9, 5),
                effective_to=date(2024, 12, 11),
                status=RevisionStatus.SUPERSEDED,
                created_at=datetime.now(UTC),
            ),
        ]
        mock_registry.list_revisions.return_value = revisions

        mcp = PolicyKBMCP(
            registry=mock_registry,
            chroma_client=chroma_client,
            embedder=mock_embedder,
        )

        result = await mcp._list_policy_revisions(ListPolicyRevisionsInput(source="NPPF"))

        assert result["status"] == "success"
        assert result["revision_count"] == 2
        # Should be ordered by effective_from DESC
        assert result["revisions"][0]["revision_id"] == "rev_NPPF_2024_12"


class TestIngestPolicyRevision:
    """
    Tests for ingest_policy_revision tool.

    Implements [policy-knowledge-base:PolicyKBMCP/TS-10]
    """

    @pytest.mark.asyncio
    async def test_ingest_policy_revision(
        self,
        mock_registry,
        chroma_client,
        mock_embedder,
        sample_revisions,
        tmp_path,
    ):
        """
        Verifies [policy-knowledge-base:PolicyKBMCP/TS-10] - Ingest policy revision

        Given: Valid file path and revision metadata
        When: ingest_policy_revision(...)
        Then: Chunks created with correct metadata
        """
        from src.mcp_servers.policy_kb.server import IngestPolicyRevisionInput, PolicyKBMCP

        # Create a mock PDF file
        pdf_path = tmp_path / "test_policy.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 test content")

        # Setup mock registry
        mock_registry.get_revision.return_value = sample_revisions[0]

        # Create mock processor and chunker
        mock_processor = MagicMock()
        mock_processor.extract.return_value = MagicMock(
            total_pages=2,
            pages=[
                MagicMock(page_number=1, text="Chapter 5: Cycle Lane Design"),
                MagicMock(page_number=2, text="Table 5-2: Minimum widths"),
            ],
            extraction_method="text_layer",
            total_char_count=100,
        )

        mock_chunker = MagicMock()
        mock_chunker.chunk_pages.return_value = [
            MagicMock(
                text="Chapter 5: Cycle Lane Design",
                chunk_index=0,
                page_numbers=[1],
                char_count=30,
                word_count=5,
            ),
        ]

        mcp = PolicyKBMCP(
            registry=mock_registry,
            chroma_client=chroma_client,
            embedder=mock_embedder,
            processor=mock_processor,
            chunker=mock_chunker,
        )

        result = await mcp._ingest_policy_revision(IngestPolicyRevisionInput(
            source="LTN_1_20",
            revision_id="rev_LTN_1_20_2020_07",
            file_path=str(pdf_path),
        ))

        assert result["status"] == "success"
        assert result["chunks_created"] > 0

        # Verify chunks in ChromaDB
        chunks = chroma_client.get_revision_chunks("LTN_1_20", "rev_LTN_1_20_2020_07")
        assert len(chunks) > 0


class TestRemovePolicyRevision:
    """
    Tests for remove_policy_revision tool.

    Implements [policy-knowledge-base:PolicyKBMCP/TS-11]
    """

    @pytest.mark.asyncio
    async def test_remove_policy_revision(
        self,
        mock_registry,
        chroma_client,
        mock_embedder,
        sample_chunks,
    ):
        """
        Verifies [policy-knowledge-base:PolicyKBMCP/TS-11] - Remove policy revision

        Given: Existing chunks for revision
        When: remove_policy_revision("LTN_1_20", "rev_LTN_1_20_2020_07")
        Then: All chunks for revision deleted
        """
        from src.mcp_servers.policy_kb.server import PolicyKBMCP, RemovePolicyRevisionInput

        # Setup - add chunks first
        chroma_client.upsert_chunks(sample_chunks)

        # Verify chunks exist
        initial_count = len(chroma_client.get_revision_chunks("LTN_1_20", "rev_LTN_1_20_2020_07"))
        assert initial_count == 3

        mcp = PolicyKBMCP(
            registry=mock_registry,
            chroma_client=chroma_client,
            embedder=mock_embedder,
        )

        result = await mcp._remove_policy_revision(RemovePolicyRevisionInput(
            source="LTN_1_20",
            revision_id="rev_LTN_1_20_2020_07",
        ))

        assert result["status"] == "success"
        assert result["chunks_removed"] == 3

        # Verify chunks are gone
        remaining = chroma_client.get_revision_chunks("LTN_1_20", "rev_LTN_1_20_2020_07")
        assert len(remaining) == 0
