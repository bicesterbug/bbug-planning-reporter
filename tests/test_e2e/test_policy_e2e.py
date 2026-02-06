"""
End-to-end tests for policy knowledge base.

Implements [policy-knowledge-base:E2E-01] - Policy management workflow
Implements [policy-knowledge-base:E2E-02] - Agent uses temporally correct policy
Implements [policy-knowledge-base:E2E-03] - Policy update workflow
Implements [policy-knowledge-base:E2E-04] - First deployment seeding

These tests verify the complete policy lifecycle from registration through agent queries.
"""

import json
from datetime import date
from pathlib import Path

import fitz
import pytest

from src.api.schemas.policy import PolicyCategory, RevisionStatus
from src.mcp_servers.document_store.embeddings import EmbeddingService, MockEmbeddingModel
from src.mcp_servers.policy_kb.server import PolicyKBMCP, SearchPolicyInput
from src.scripts.seed_policies import PolicySeeder
from src.shared.policy_chroma_client import PolicyChromaClient
from src.shared.policy_registry import PolicyRegistry
from src.worker.policy_jobs import PolicyIngestionService


def create_policy_pdf(path: Path, title: str, content: str) -> None:
    """Create a test policy PDF with given content."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), f"{title}\n\n{content}", fontsize=10)
    doc.save(str(path))
    doc.close()


def create_nppf_2023_pdf(path: Path) -> None:
    """Create a mock NPPF September 2023 PDF."""
    content = """National Planning Policy Framework

September 2023 Edition

Chapter 9: Promoting Sustainable Transport

Para 108. Planning policies should support an appropriate mix of uses across an area,
and within larger scale sites, to minimise the number and length of journeys needed
for employment, shopping, leisure, education and other activities.

Para 109. Significant development should be focused on locations which are or can
be made sustainable, through limiting the need to travel and offering a genuine
choice of transport modes. This can help to reduce congestion and emissions, and
improve air quality and public health.

Para 110. Planning policies and decisions should:
a) give priority first to pedestrian and cycle movements
b) address the needs of people with disabilities
c) facilitate access to high quality public transport"""
    create_policy_pdf(path, "NPPF September 2023", content)


def create_nppf_2024_pdf(path: Path) -> None:
    """Create a mock NPPF December 2024 PDF."""
    content = """National Planning Policy Framework

December 2024 Edition

Chapter 9: Promoting Sustainable Transport

Para 108. Planning policies should support a mix of uses across an area to minimise
journey lengths for employment, shopping, leisure and education.

Para 109. Development should be focused on sustainable locations, limiting travel
and offering genuine transport mode choices to reduce congestion and emissions.

Para 110. Planning policies and decisions should:
a) prioritise pedestrian and cycle movements first
b) consider needs of people with disabilities and reduced mobility
c) provide access to high quality public transport
d) enable charging of plug-in and other ultra-low emission vehicles"""
    create_policy_pdf(path, "NPPF December 2024", content)


def create_ltn_1_20_pdf(path: Path) -> None:
    """Create a mock LTN 1/20 PDF."""
    content = """Local Transport Note 1/20: Cycle Infrastructure Design

July 2020

1. Introduction

This Local Transport Note provides guidance on designing high-quality cycle
infrastructure. It is intended to assist local authorities in delivering cycling
schemes that meet current best practice.

4.2 Width requirements

Table 4-1: Minimum widths for cycle routes

Desirable minimum width for one-way cycle track: 2.0m
Absolute minimum width for one-way cycle track: 1.5m
Desirable minimum width for two-way cycle track: 3.0m

5.3 Junction design

5.3.1 Protected junctions should be considered at all signal-controlled junctions
where cycle routes cross or join main roads.

5.3.2 Corner radii should be minimised to slow turning traffic."""
    create_policy_pdf(path, "LTN 1/20", content)


@pytest.fixture
def mock_embedder():
    """Create a mock embedding service."""
    return EmbeddingService(model=MockEmbeddingModel())


@pytest.fixture
def policy_chroma_client():
    """Create a PolicyChromaClient with fresh ChromaDB."""
    import contextlib

    import chromadb
    from chromadb.config import Settings

    client = chromadb.Client(settings=Settings(anonymized_telemetry=False))
    # Delete the collection if it exists from a previous test
    with contextlib.suppress(Exception):
        client.delete_collection(PolicyChromaClient.COLLECTION_NAME)
    return PolicyChromaClient(client=client)


@pytest.fixture
async def policy_registry(fake_redis) -> PolicyRegistry:
    """Create a PolicyRegistry with fakeredis."""
    return PolicyRegistry(fake_redis)


@pytest.fixture
async def policy_ingestion_service(
    policy_registry: PolicyRegistry,
    policy_chroma_client: PolicyChromaClient,
    mock_embedder: EmbeddingService,
) -> PolicyIngestionService:
    """Create a PolicyIngestionService for testing."""
    return PolicyIngestionService(
        registry=policy_registry,
        chroma_client=policy_chroma_client,
        embedder=mock_embedder,
    )


@pytest.fixture
async def policy_kb_mcp(
    policy_registry: PolicyRegistry,
    policy_chroma_client: PolicyChromaClient,
    mock_embedder: EmbeddingService,
) -> PolicyKBMCP:
    """Create a PolicyKBMCP server for testing."""
    return PolicyKBMCP(
        registry=policy_registry,
        chroma_client=policy_chroma_client,
        embedder=mock_embedder,
    )


@pytest.fixture
def policy_pdfs(tmp_path: Path) -> dict[str, Path]:
    """Create test policy PDFs."""
    pdf_dir = tmp_path / "policies"
    pdf_dir.mkdir()

    nppf_2023 = pdf_dir / "nppf_2023_09.pdf"
    create_nppf_2023_pdf(nppf_2023)

    nppf_2024 = pdf_dir / "nppf_2024_12.pdf"
    create_nppf_2024_pdf(nppf_2024)

    ltn_1_20 = pdf_dir / "ltn_1_20.pdf"
    create_ltn_1_20_pdf(ltn_1_20)

    return {
        "nppf_2023": nppf_2023,
        "nppf_2024": nppf_2024,
        "ltn_1_20": ltn_1_20,
    }


@pytest.mark.e2e
class TestPolicyManagementWorkflow:
    """
    Verifies [policy-knowledge-base:E2E-01] - Policy management workflow

    Given: Admin with PDF
    When: Register policy, upload revision, check status, verify searchable
    Then: Policy available for agent queries
    """

    @pytest.mark.asyncio
    async def test_full_policy_management_workflow(
        self,
        policy_registry: PolicyRegistry,
        policy_ingestion_service: PolicyIngestionService,
        policy_kb_mcp: PolicyKBMCP,
        policy_pdfs: dict[str, Path],
    ) -> None:
        """
        Verifies [policy-knowledge-base:E2E-01]

        Given: Admin with policy PDF
        When: Register policy, ingest revision, verify searchable
        Then: Policy available for agent queries
        """
        # Step 1: Register a new policy
        await policy_registry.create_policy(
            source="LTN_1_20",
            title="Cycle Infrastructure Design (LTN 1/20)",
            description="National guidance on cycle infrastructure",
            category=PolicyCategory.NATIONAL_GUIDANCE,
        )

        # Verify policy created
        policy = await policy_registry.get_policy("LTN_1_20")
        assert policy is not None
        assert policy.title == "Cycle Infrastructure Design (LTN 1/20)"

        # Step 2: Create a revision
        revision = await policy_registry.create_revision(
            source="LTN_1_20",
            revision_id="rev_LTN_1_20_2020_07",
            version_label="July 2020",
            effective_from=date(2020, 7, 27),
            effective_to=None,
        )
        revision_id = revision.revision_id

        # Step 3: Ingest the revision
        result = await policy_ingestion_service.ingest_revision(
            source="LTN_1_20",
            revision_id=revision_id,
            file_path=policy_pdfs["ltn_1_20"],
        )
        assert result.chunk_count > 0

        # Step 4: Update revision status to indexed
        await policy_registry.update_revision(
            source="LTN_1_20",
            revision_id=revision_id,
            chunk_count=result.chunk_count,
            status=RevisionStatus.ACTIVE,
        )

        # Step 5: Verify searchable via MCP
        search_result = await policy_kb_mcp._search_policy(
            SearchPolicyInput(query="cycle track width minimum", n_results=5)
        )

        assert search_result["results_count"] > 0
        # Should find LTN 1/20 content about widths
        found_content = " ".join(r["text"] for r in search_result["results"])
        assert "width" in found_content.lower() or "cycle" in found_content.lower()

        # Verify policy appears in list
        list_result = await policy_kb_mcp._list_policy_documents()
        sources = [p["source"] for p in list_result["policies"]]
        assert "LTN_1_20" in sources


@pytest.mark.e2e
class TestAgentUsesTemporallyCorrectPolicy:
    """
    Verifies [policy-knowledge-base:E2E-02] - Agent uses temporally correct policy

    Given: Review for app validated 2024-03-15, NPPF has Dec 2024 revision
    When: Review runs with temporal filtering
    Then: Review cites Sep 2023 NPPF (correct for date)
    """

    @pytest.mark.asyncio
    async def test_temporal_query_returns_correct_revision(
        self,
        policy_registry: PolicyRegistry,
        policy_ingestion_service: PolicyIngestionService,
        policy_kb_mcp: PolicyKBMCP,
        policy_pdfs: dict[str, Path],
    ) -> None:
        """
        Verifies [policy-knowledge-base:E2E-02]

        Given: NPPF has two revisions - Sep 2023 and Dec 2024
        When: Query with effective_date 2024-03-15
        Then: Results come from Sep 2023 revision (before Dec 2024)
        """
        # Register NPPF policy
        await policy_registry.create_policy(
            source="NPPF",
            title="National Planning Policy Framework",
            description="Government planning policies for England",
            category=PolicyCategory.NATIONAL_POLICY,
        )

        # Create and ingest September 2023 revision
        rev_2023 = await policy_registry.create_revision(
            source="NPPF",
            revision_id="rev_NPPF_2023_09",
            version_label="September 2023",
            effective_from=date(2023, 9, 5),
            effective_to=date(2024, 12, 11),
        )
        rev_2023_id = rev_2023.revision_id

        result_2023 = await policy_ingestion_service.ingest_revision(
            source="NPPF",
            revision_id=rev_2023_id,
            file_path=policy_pdfs["nppf_2023"],
        )
        assert result_2023.chunk_count > 0

        await policy_registry.update_revision(
            source="NPPF",
            revision_id=rev_2023_id,
            chunk_count=result_2023.chunk_count,
            status=RevisionStatus.ACTIVE,
        )

        # Create and ingest December 2024 revision
        rev_2024 = await policy_registry.create_revision(
            source="NPPF",
            revision_id="rev_NPPF_2024_12",
            version_label="December 2024",
            effective_from=date(2024, 12, 12),
            effective_to=None,
        )
        rev_2024_id = rev_2024.revision_id

        result_2024 = await policy_ingestion_service.ingest_revision(
            source="NPPF",
            revision_id=rev_2024_id,
            file_path=policy_pdfs["nppf_2024"],
        )
        assert result_2024.chunk_count > 0

        await policy_registry.update_revision(
            source="NPPF",
            revision_id=rev_2024_id,
            chunk_count=result_2024.chunk_count,
            status=RevisionStatus.ACTIVE,
        )

        # Query with date in March 2024 - should get Sep 2023 revision
        search_result = await policy_kb_mcp._search_policy(
            SearchPolicyInput(
                query="sustainable transport pedestrian cycle",
                effective_date="2024-03-15",
                n_results=10,
            )
        )

        assert search_result["results_count"] > 0

        # All results should be from 2023 revision
        for result in search_result["results"]:
            # 2023 revision has effective_to of 2024-12-11
            assert result["revision_id"] == rev_2023_id

        # Query with date in January 2025 - should get Dec 2024 revision
        search_result_2025 = await policy_kb_mcp._search_policy(
            SearchPolicyInput(
                query="sustainable transport pedestrian cycle",
                effective_date="2025-01-15",
                n_results=10,
            )
        )

        assert search_result_2025["results_count"] > 0

        # All results should be from 2024 revision
        for result in search_result_2025["results"]:
            assert result["revision_id"] == rev_2024_id


@pytest.mark.e2e
class TestPolicyUpdateWorkflow:
    """
    Verifies [policy-knowledge-base:E2E-03] - Policy update workflow

    Given: LTN 1/20 update released
    When: Upload new revision
    Then: Old revision superseded, new revision searchable, correct by date
    """

    @pytest.mark.asyncio
    async def test_revision_supersession_workflow(
        self,
        policy_registry: PolicyRegistry,
        policy_ingestion_service: PolicyIngestionService,
        policy_kb_mcp: PolicyKBMCP,
        tmp_path: Path,
    ) -> None:
        """
        Verifies [policy-knowledge-base:E2E-03]

        Given: Policy with active revision
        When: Upload new revision with later effective_from
        Then: Old revision superseded, both searchable by appropriate date
        """
        # Create test PDFs
        ltn_2020 = tmp_path / "ltn_2020.pdf"
        create_policy_pdf(
            ltn_2020,
            "LTN 1/20 July 2020",
            "Original 2020 guidance. Minimum cycle track width: 1.5m absolute minimum.",
        )

        ltn_2025 = tmp_path / "ltn_2025.pdf"
        create_policy_pdf(
            ltn_2025,
            "LTN 1/20 January 2025 Update",
            "Updated 2025 guidance. Minimum cycle track width: 2.0m absolute minimum.",
        )

        # Register policy
        await policy_registry.create_policy(
            source="LTN_1_20",
            title="Cycle Infrastructure Design (LTN 1/20)",
            description="National guidance",
            category=PolicyCategory.NATIONAL_GUIDANCE,
        )

        # Create and ingest 2020 revision (no effective_to initially)
        rev_2020_rec = await policy_registry.create_revision(
            source="LTN_1_20",
            revision_id="rev_LTN_1_20_2020_07",
            version_label="July 2020",
            effective_from=date(2020, 7, 27),
            effective_to=None,  # Currently active
        )
        rev_2020_id = rev_2020_rec.revision_id

        result_2020 = await policy_ingestion_service.ingest_revision(
            source="LTN_1_20",
            revision_id=rev_2020_id,
            file_path=ltn_2020,
        )

        await policy_registry.update_revision(
            source="LTN_1_20",
            revision_id=rev_2020_id,
            chunk_count=result_2020.chunk_count,
            status=RevisionStatus.ACTIVE,
        )

        # Verify 2020 revision is active
        rev_2020 = await policy_registry.get_revision("LTN_1_20", rev_2020_id)
        assert rev_2020.effective_to is None

        # Create 2025 revision - this should trigger supersession
        rev_2025_rec = await policy_registry.create_revision(
            source="LTN_1_20",
            revision_id="rev_LTN_1_20_2025_01",
            version_label="January 2025",
            effective_from=date(2025, 1, 15),
            effective_to=None,
        )
        rev_2025_id = rev_2025_rec.revision_id

        # Update 2020 revision to be superseded (set effective_to)
        await policy_registry.update_revision(
            source="LTN_1_20",
            revision_id=rev_2020_id,
            effective_to=date(2025, 1, 14),
        )

        # Reindex 2020 revision to update ChromaDB metadata with new effective_to
        await policy_ingestion_service.ingest_revision(
            source="LTN_1_20",
            revision_id=rev_2020_id,
            file_path=ltn_2020,
            reindex=True,
        )

        # Ingest 2025 revision
        result_2025 = await policy_ingestion_service.ingest_revision(
            source="LTN_1_20",
            revision_id=rev_2025_id,
            file_path=ltn_2025,
        )

        await policy_registry.update_revision(
            source="LTN_1_20",
            revision_id=rev_2025_id,
            chunk_count=result_2025.chunk_count,
            status=RevisionStatus.ACTIVE,
        )

        # Verify 2020 revision is now superseded
        rev_2020_updated = await policy_registry.get_revision("LTN_1_20", rev_2020_id)
        assert rev_2020_updated.effective_to == date(2025, 1, 14)

        # Query with 2024 date - should get 2020 revision (1.5m)
        search_2024 = await policy_kb_mcp._search_policy(
            SearchPolicyInput(
                query="minimum cycle track width",
                effective_date="2024-06-15",
                sources=["LTN_1_20"],
                n_results=5,
            )
        )

        assert search_2024["results_count"] > 0
        for result in search_2024["results"]:
            assert result["revision_id"] == rev_2020_id

        # Query with 2025 date - should get 2025 revision (2.0m)
        search_2025 = await policy_kb_mcp._search_policy(
            SearchPolicyInput(
                query="minimum cycle track width",
                effective_date="2025-02-01",
                sources=["LTN_1_20"],
                n_results=5,
            )
        )

        assert search_2025["results_count"] > 0
        for result in search_2025["results"]:
            assert result["revision_id"] == rev_2025_id


@pytest.mark.e2e
class TestFirstDeploymentSeeding:
    """
    Verifies [policy-knowledge-base:E2E-04] - First deployment seeding

    Given: Fresh system
    When: Start stack
    Then: Policy seeder runs, all policies available
    """

    @pytest.mark.asyncio
    async def test_seeder_populates_fresh_system(
        self,
        policy_registry: PolicyRegistry,
        policy_ingestion_service: PolicyIngestionService,
        policy_kb_mcp: PolicyKBMCP,
        tmp_path: Path,
    ) -> None:
        """
        Verifies [policy-knowledge-base:E2E-04]

        Given: Fresh system with no policies
        When: Run policy seeder with seed config
        Then: All configured policies available and searchable
        """
        # Create seed directory
        seed_dir = tmp_path / "seed"
        seed_dir.mkdir()

        # Create seed PDFs
        ltn_pdf = seed_dir / "ltn_1_20.pdf"
        create_ltn_1_20_pdf(ltn_pdf)

        nppf_pdf = seed_dir / "nppf_2024_12.pdf"
        create_nppf_2024_pdf(nppf_pdf)

        # Create seed config
        config_path = tmp_path / "seed_config.json"
        seed_config = {
            "policies": [
                {
                    "source": "LTN_1_20",
                    "title": "Cycle Infrastructure Design (LTN 1/20)",
                    "description": "National guidance on cycle infrastructure",
                    "category": "national_guidance",
                    "revisions": [
                        {
                            "version_label": "July 2020",
                            "effective_from": "2020-07-27",
                            "effective_to": None,
                            "file": "ltn_1_20.pdf",
                        }
                    ],
                },
                {
                    "source": "NPPF",
                    "title": "National Planning Policy Framework",
                    "description": "Government planning policies",
                    "category": "national_policy",
                    "revisions": [
                        {
                            "version_label": "December 2024",
                            "effective_from": "2024-12-12",
                            "effective_to": None,
                            "file": "nppf_2024_12.pdf",
                        }
                    ],
                },
            ]
        }
        config_path.write_text(json.dumps(seed_config))

        # Verify system is empty
        policies_before = await policy_registry.list_policies()
        assert len(policies_before) == 0

        # Run seeder
        seeder = PolicySeeder(
            registry=policy_registry,
            ingestion_service=policy_ingestion_service,
            config_path=config_path,
            seed_dir=seed_dir,
        )
        await seeder.seed()

        # Verify policies created
        policies_after = await policy_registry.list_policies()
        assert len(policies_after) == 2

        sources = {p.source for p in policies_after}
        assert sources == {"LTN_1_20", "NPPF"}

        # Verify searchable via MCP
        ltn_search = await policy_kb_mcp._search_policy(
            SearchPolicyInput(
                query="cycle track width",
                sources=["LTN_1_20"],
                n_results=5,
            )
        )
        assert ltn_search["results_count"] > 0

        nppf_search = await policy_kb_mcp._search_policy(
            SearchPolicyInput(
                query="sustainable transport",
                sources=["NPPF"],
                n_results=5,
            )
        )
        assert nppf_search["results_count"] > 0

        # Verify list via MCP
        list_result = await policy_kb_mcp._list_policy_documents()
        assert len(list_result["policies"]) == 2


@pytest.mark.e2e
class TestSectionRefExtraction:
    """Test that section references are extracted correctly for agent citations."""

    def test_extract_section_ref_from_ltn_content(self) -> None:
        """
        Verify section ref extraction works on LTN content.

        Implements [policy-knowledge-base:FR-006] - section_ref metadata
        """
        # Table references
        content_table = "Table 4-1: Minimum widths for cycle routes"
        assert PolicyIngestionService._extract_section_ref(content_table) == "Table 4-1"

        # Paragraph references with "Para" prefix (matches Para X.Y pattern)
        content_para = "Para 116 states that development should prioritize cycling"
        assert PolicyIngestionService._extract_section_ref(content_para) == "Para 116"

        # Chapter references
        content_chapter = "Chapter 9: Promoting Sustainable Transport"
        assert PolicyIngestionService._extract_section_ref(content_chapter) == "Chapter 9"

        # Section references
        content_section = "Section 3.2 describes the requirements for cycle parking."
        assert PolicyIngestionService._extract_section_ref(content_section) == "Section 3.2"

        # No reference found returns empty string
        content_none = "General discussion about cycling"
        assert PolicyIngestionService._extract_section_ref(content_none) == ""
