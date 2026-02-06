"""
Tests for PolicySeeder script.

Implements test scenarios from [policy-knowledge-base:PolicySeeder/TS-01] through [TS-05]
"""

import json
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest

from src.api.schemas.policy import PolicyCategory, RevisionStatus


@pytest.fixture
def mock_registry():
    """Create mock PolicyRegistry."""
    registry = AsyncMock()
    registry.get_policy = AsyncMock(return_value=None)
    registry.create_policy = AsyncMock()
    registry.create_revision = AsyncMock()
    registry.get_revision = AsyncMock(return_value=None)
    return registry


@pytest.fixture
def mock_ingestion_service():
    """Create mock PolicyIngestionService."""
    from src.worker.policy_jobs import IngestionResult

    service = AsyncMock()
    service.ingest_revision = AsyncMock(return_value=IngestionResult(
        success=True,
        source="TEST",
        revision_id="rev_TEST_2024_01",
        chunk_count=10,
        page_count=5,
        extraction_method="text_layer",
    ))
    return service


@pytest.fixture
def sample_seed_config(tmp_path):
    """Create a sample seed configuration file."""
    config = {
        "policies": [
            {
                "source": "LTN_1_20",
                "title": "Cycle Infrastructure Design (LTN 1/20)",
                "description": "National guidance on cycle infrastructure design",
                "category": "national_guidance",
                "revisions": [
                    {
                        "version_label": "July 2020",
                        "effective_from": "2020-07-27",
                        "effective_to": None,
                        "file": "ltn_1_20.pdf"
                    }
                ]
            },
            {
                "source": "NPPF",
                "title": "National Planning Policy Framework",
                "description": "National planning policy",
                "category": "national_policy",
                "revisions": [
                    {
                        "version_label": "December 2024",
                        "effective_from": "2024-12-12",
                        "effective_to": None,
                        "file": "nppf_2024.pdf"
                    },
                    {
                        "version_label": "September 2023",
                        "effective_from": "2023-09-05",
                        "effective_to": "2024-12-11",
                        "file": "nppf_2023.pdf"
                    }
                ]
            }
        ]
    }

    config_path = tmp_path / "seed_config.json"
    config_path.write_text(json.dumps(config, indent=2))

    # Create seed PDF directory and dummy PDFs
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    (seed_dir / "ltn_1_20.pdf").write_bytes(b"%PDF-1.4 LTN 1/20 content")
    (seed_dir / "nppf_2024.pdf").write_bytes(b"%PDF-1.4 NPPF 2024 content")
    (seed_dir / "nppf_2023.pdf").write_bytes(b"%PDF-1.4 NPPF 2023 content")

    return config_path, seed_dir


class TestFirstRunSeeding:
    """
    Tests for first run seeding.

    Implements [policy-knowledge-base:PolicySeeder/TS-01] - First run seeds all policies
    """

    @pytest.mark.asyncio
    async def test_first_run_seeds_all_policies(
        self,
        mock_registry,
        mock_ingestion_service,
        sample_seed_config,
    ):
        """
        Verifies [policy-knowledge-base:PolicySeeder/TS-01] - First run seeds all policies

        Given: Empty registry
        When: Run seeder
        Then: All configured policies created with revisions
        """
        from src.scripts.seed_policies import PolicySeeder

        config_path, seed_dir = sample_seed_config

        seeder = PolicySeeder(
            registry=mock_registry,
            ingestion_service=mock_ingestion_service,
            config_path=config_path,
            seed_dir=seed_dir,
        )

        result = await seeder.seed()

        # Verify all policies were created
        assert result.policies_created == 2
        assert result.revisions_created == 3

        # Verify create_policy was called for each policy
        assert mock_registry.create_policy.call_count == 2

        # Verify create_revision was called for each revision
        assert mock_registry.create_revision.call_count == 3


class TestIdempotentRerun:
    """
    Tests for idempotent re-run.

    Implements [policy-knowledge-base:PolicySeeder/TS-02] - Idempotent re-run
    """

    @pytest.mark.asyncio
    async def test_idempotent_rerun(
        self,
        mock_registry,
        mock_ingestion_service,
        sample_seed_config,
    ):
        """
        Verifies [policy-knowledge-base:PolicySeeder/TS-02] - Idempotent re-run

        Given: Policies already seeded
        When: Run seeder again
        Then: No duplicates, no errors
        """
        from src.api.schemas.policy import PolicyDocumentRecord, PolicyRevisionRecord
        from src.scripts.seed_policies import PolicySeeder

        config_path, seed_dir = sample_seed_config

        # Mock that policies already exist
        mock_registry.get_policy.return_value = PolicyDocumentRecord(
            source="LTN_1_20",
            title="Cycle Infrastructure Design (LTN 1/20)",
            category=PolicyCategory.NATIONAL_GUIDANCE,
            created_at=datetime.now(UTC),
        )
        mock_registry.get_revision.return_value = PolicyRevisionRecord(
            revision_id="rev_LTN_1_20_2020_07",
            source="LTN_1_20",
            version_label="July 2020",
            effective_from=date(2020, 7, 27),
            status=RevisionStatus.ACTIVE,
            created_at=datetime.now(UTC),
        )

        seeder = PolicySeeder(
            registry=mock_registry,
            ingestion_service=mock_ingestion_service,
            config_path=config_path,
            seed_dir=seed_dir,
        )

        result = await seeder.seed()

        # No policies or revisions should be created
        assert result.policies_created == 0
        assert result.revisions_created == 0
        assert result.policies_skipped == 2
        assert result.revisions_skipped == 3

        # create_policy should not be called
        mock_registry.create_policy.assert_not_called()


class TestSeedFilesPresent:
    """
    Tests for seed files present.

    Implements [policy-knowledge-base:PolicySeeder/TS-03] - Seed files present
    """

    @pytest.mark.asyncio
    async def test_seed_files_processed(
        self,
        mock_registry,
        mock_ingestion_service,
        sample_seed_config,
    ):
        """
        Verifies [policy-knowledge-base:PolicySeeder/TS-03] - Seed files present

        Given: Seed PDF files in /data/policy/seed
        When: Run seeder
        Then: All files processed
        """
        from src.scripts.seed_policies import PolicySeeder

        config_path, seed_dir = sample_seed_config

        seeder = PolicySeeder(
            registry=mock_registry,
            ingestion_service=mock_ingestion_service,
            config_path=config_path,
            seed_dir=seed_dir,
        )

        result = await seeder.seed()

        # All files should be processed
        assert result.files_processed == 3
        assert result.errors == []


class TestCorrectEffectiveDates:
    """
    Tests for correct effective dates.

    Implements [policy-knowledge-base:PolicySeeder/TS-04] - Correct effective dates
    """

    @pytest.mark.asyncio
    async def test_correct_effective_dates(
        self,
        mock_registry,
        mock_ingestion_service,
        sample_seed_config,
    ):
        """
        Verifies [policy-knowledge-base:PolicySeeder/TS-04] - Correct effective dates

        Given: Seed config with dates
        When: Run seeder
        Then: Revisions have correct effective_from dates
        """
        from src.scripts.seed_policies import PolicySeeder

        config_path, seed_dir = sample_seed_config

        seeder = PolicySeeder(
            registry=mock_registry,
            ingestion_service=mock_ingestion_service,
            config_path=config_path,
            seed_dir=seed_dir,
        )

        await seeder.seed()

        # Check that create_revision was called with correct dates
        calls = mock_registry.create_revision.call_args_list

        # Find the LTN 1/20 revision call
        ltn_call = None
        for call in calls:
            if call[1].get("source") == "LTN_1_20":
                ltn_call = call
                break

        assert ltn_call is not None
        assert ltn_call[1]["effective_from"] == date(2020, 7, 27)
        assert ltn_call[1]["effective_to"] is None


class TestMissingSeedFile:
    """
    Tests for missing seed file.

    Implements [policy-knowledge-base:PolicySeeder/TS-05] - Missing seed file
    """

    @pytest.mark.asyncio
    async def test_missing_seed_file_continues(
        self,
        mock_registry,
        mock_ingestion_service,
        sample_seed_config,
    ):
        """
        Verifies [policy-knowledge-base:PolicySeeder/TS-05] - Missing seed file

        Given: One PDF missing
        When: Run seeder
        Then: Logs warning, continues with others
        """
        from src.scripts.seed_policies import PolicySeeder

        config_path, seed_dir = sample_seed_config

        # Delete one of the PDFs
        (seed_dir / "nppf_2023.pdf").unlink()

        seeder = PolicySeeder(
            registry=mock_registry,
            ingestion_service=mock_ingestion_service,
            config_path=config_path,
            seed_dir=seed_dir,
        )

        result = await seeder.seed()

        # Should still process the other files
        assert result.files_processed == 2
        assert result.files_missing == 1
        assert len(result.errors) == 1
        assert "nppf_2023.pdf" in result.errors[0]

        # Policies and other revisions should still be created
        assert result.policies_created == 2
        assert result.revisions_created == 2  # Only 2 of 3 revisions


class TestConfigParsing:
    """Tests for config file parsing."""

    @pytest.mark.asyncio
    async def test_invalid_config_file(
        self,
        mock_registry,
        mock_ingestion_service,
        tmp_path,
    ):
        """Test handling of invalid config file."""
        from src.scripts.seed_policies import PolicySeeder, SeedError

        config_path = tmp_path / "invalid.json"
        config_path.write_text("not valid json")
        seed_dir = tmp_path / "seed"
        seed_dir.mkdir()

        seeder = PolicySeeder(
            registry=mock_registry,
            ingestion_service=mock_ingestion_service,
            config_path=config_path,
            seed_dir=seed_dir,
        )

        with pytest.raises(SeedError, match="Failed to parse"):
            await seeder.seed()

    @pytest.mark.asyncio
    async def test_missing_config_file(
        self,
        mock_registry,
        mock_ingestion_service,
        tmp_path,
    ):
        """Test handling of missing config file."""
        from src.scripts.seed_policies import PolicySeeder, SeedError

        config_path = tmp_path / "nonexistent.json"
        seed_dir = tmp_path / "seed"
        seed_dir.mkdir()

        seeder = PolicySeeder(
            registry=mock_registry,
            ingestion_service=mock_ingestion_service,
            config_path=config_path,
            seed_dir=seed_dir,
        )

        with pytest.raises(SeedError, match="Config file not found"):
            await seeder.seed()
