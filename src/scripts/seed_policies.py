"""
Policy Seeder - Initial policy document ingestion at first deployment.

Implements [policy-knowledge-base:FR-013] - Seed initial policies at first deployment

Implements:
- [policy-knowledge-base:PolicySeeder/TS-01] First run seeds all policies
- [policy-knowledge-base:PolicySeeder/TS-02] Idempotent re-run
- [policy-knowledge-base:PolicySeeder/TS-03] Seed files present
- [policy-knowledge-base:PolicySeeder/TS-04] Correct effective dates
- [policy-knowledge-base:PolicySeeder/TS-05] Missing seed file
"""

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import structlog

from src.api.schemas.policy import PolicyCategory
from src.shared.policy_registry import PolicyRegistry
from src.worker.policy_jobs import PolicyIngestionService

logger = structlog.get_logger(__name__)


class SeedError(Exception):
    """Error during policy seeding."""

    pass


@dataclass
class SeedResult:
    """Result of policy seeding operation."""

    policies_created: int = 0
    policies_skipped: int = 0
    revisions_created: int = 0
    revisions_skipped: int = 0
    files_processed: int = 0
    files_missing: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class PolicyConfig:
    """Configuration for a policy to seed."""

    source: str
    title: str
    description: str | None
    category: PolicyCategory
    revisions: list["RevisionConfig"]


@dataclass
class RevisionConfig:
    """Configuration for a revision to seed."""

    version_label: str
    effective_from: date
    effective_to: date | None
    file: str


class PolicySeeder:
    """
    Seeds initial policy documents at first deployment.

    Implements [policy-knowledge-base:PolicySeeder/TS-01] through [TS-05]
    """

    def __init__(
        self,
        registry: PolicyRegistry,
        ingestion_service: PolicyIngestionService,
        config_path: Path | str,
        seed_dir: Path | str,
    ) -> None:
        """
        Initialize the PolicySeeder.

        Args:
            registry: PolicyRegistry for storing metadata.
            ingestion_service: Service for ingesting PDFs.
            config_path: Path to seed configuration JSON file.
            seed_dir: Directory containing seed PDF files.
        """
        self._registry = registry
        self._ingestion_service = ingestion_service
        self._config_path = Path(config_path)
        self._seed_dir = Path(seed_dir)

    def _load_config(self) -> list[PolicyConfig]:
        """
        Load and parse the seed configuration file.

        Raises:
            SeedError: If config file not found or invalid.
        """
        if not self._config_path.exists():
            raise SeedError(f"Config file not found: {self._config_path}")

        try:
            with open(self._config_path) as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise SeedError(f"Failed to parse config file: {e}") from e

        policies = []
        for policy_data in data.get("policies", []):
            revisions = []
            for rev_data in policy_data.get("revisions", []):
                effective_to = None
                if rev_data.get("effective_to"):
                    effective_to = date.fromisoformat(rev_data["effective_to"])

                revisions.append(RevisionConfig(
                    version_label=rev_data["version_label"],
                    effective_from=date.fromisoformat(rev_data["effective_from"]),
                    effective_to=effective_to,
                    file=rev_data["file"],
                ))

            policies.append(PolicyConfig(
                source=policy_data["source"],
                title=policy_data["title"],
                description=policy_data.get("description"),
                category=PolicyCategory(policy_data["category"]),
                revisions=revisions,
            ))

        return policies

    def _generate_revision_id(self, source: str, effective_from: date) -> str:
        """Generate a revision ID from source and effective date."""
        return f"rev_{source}_{effective_from.year}_{effective_from.month:02d}"

    async def seed(self) -> SeedResult:
        """
        Seed all configured policies.

        Implements [policy-knowledge-base:PolicySeeder/TS-01] - First run seeds all policies
        Implements [policy-knowledge-base:PolicySeeder/TS-02] - Idempotent re-run

        Returns:
            SeedResult with counts and any errors.
        """
        result = SeedResult()

        logger.info(
            "Starting policy seeding",
            config_path=str(self._config_path),
            seed_dir=str(self._seed_dir),
        )

        # Load configuration
        policies = self._load_config()

        for policy_config in policies:
            await self._seed_policy(policy_config, result)

        logger.info(
            "Policy seeding complete",
            policies_created=result.policies_created,
            policies_skipped=result.policies_skipped,
            revisions_created=result.revisions_created,
            revisions_skipped=result.revisions_skipped,
            files_processed=result.files_processed,
            files_missing=result.files_missing,
            errors=len(result.errors),
        )

        return result

    async def _seed_policy(self, config: PolicyConfig, result: SeedResult) -> None:
        """
        Seed a single policy and its revisions.

        Implements [policy-knowledge-base:PolicySeeder/TS-02] - Idempotent re-run
        """
        # Check if policy already exists
        existing = await self._registry.get_policy(config.source)

        if existing:
            logger.info(
                "Policy already exists, skipping creation",
                source=config.source,
            )
            result.policies_skipped += 1
        else:
            # Create the policy
            await self._registry.create_policy(
                source=config.source,
                title=config.title,
                description=config.description,
                category=config.category,
            )
            logger.info(
                "Policy created",
                source=config.source,
                title=config.title,
            )
            result.policies_created += 1

        # Seed each revision
        for revision_config in config.revisions:
            await self._seed_revision(config.source, revision_config, result)

    async def _seed_revision(
        self,
        source: str,
        config: RevisionConfig,
        result: SeedResult,
    ) -> None:
        """
        Seed a single revision.

        Implements [policy-knowledge-base:PolicySeeder/TS-03] - Seed files present
        Implements [policy-knowledge-base:PolicySeeder/TS-04] - Correct effective dates
        Implements [policy-knowledge-base:PolicySeeder/TS-05] - Missing seed file
        """
        revision_id = self._generate_revision_id(source, config.effective_from)

        # Check if revision already exists
        existing = await self._registry.get_revision(source, revision_id)

        if existing:
            logger.info(
                "Revision already exists, skipping",
                source=source,
                revision_id=revision_id,
            )
            result.revisions_skipped += 1
            return

        # Check if file exists
        file_path = self._seed_dir / config.file
        if not file_path.exists():
            error_msg = f"Seed file not found: {config.file}"
            logger.warning(
                error_msg,
                source=source,
                revision_id=revision_id,
                file=config.file,
            )
            result.files_missing += 1
            result.errors.append(error_msg)
            return

        # Create the revision record
        await self._registry.create_revision(
            source=source,
            revision_id=revision_id,
            version_label=config.version_label,
            effective_from=config.effective_from,
            effective_to=config.effective_to,
            file_path=str(file_path),
        )
        logger.info(
            "Revision created",
            source=source,
            revision_id=revision_id,
            version_label=config.version_label,
            effective_from=str(config.effective_from),
        )
        result.revisions_created += 1

        # Ingest the PDF
        try:
            ingestion_result = await self._ingestion_service.ingest_revision(
                source=source,
                revision_id=revision_id,
                file_path=file_path,
            )

            if ingestion_result.success:
                logger.info(
                    "Revision ingested",
                    source=source,
                    revision_id=revision_id,
                    chunks=ingestion_result.chunk_count,
                )
                result.files_processed += 1
            else:
                error_msg = f"Ingestion failed for {config.file}: {ingestion_result.error}"
                logger.error(
                    error_msg,
                    source=source,
                    revision_id=revision_id,
                )
                result.errors.append(error_msg)

        except Exception as e:
            error_msg = f"Ingestion error for {config.file}: {e}"
            logger.exception(
                error_msg,
                source=source,
                revision_id=revision_id,
            )
            result.errors.append(error_msg)


async def main() -> None:
    """Run the policy seeder from command line."""
    import redis.asyncio as aioredis

    from src.shared.policy_chroma_client import PolicyChromaClient

    # Configuration from environment
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    chroma_dir = os.getenv("CHROMA_PERSIST_DIR", "/data/chroma")
    config_path = os.getenv("SEED_CONFIG_PATH", "/data/policy/seed_config.json")
    seed_dir = os.getenv("SEED_DIR", "/data/policy/seed")

    logger.info(
        "Policy Seeder starting",
        redis_url=redis_url,
        chroma_dir=chroma_dir,
        config_path=config_path,
        seed_dir=seed_dir,
    )

    # Create dependencies
    redis_client = aioredis.from_url(redis_url, decode_responses=True)
    registry = PolicyRegistry(redis_client)
    chroma_client = PolicyChromaClient(persist_directory=chroma_dir)
    ingestion_service = PolicyIngestionService(
        registry=registry,
        chroma_client=chroma_client,
    )

    # Create and run seeder
    seeder = PolicySeeder(
        registry=registry,
        ingestion_service=ingestion_service,
        config_path=config_path,
        seed_dir=seed_dir,
    )

    try:
        result = await seeder.seed()

        if result.errors:
            logger.warning(
                "Seeding completed with errors",
                errors=result.errors,
            )
            exit(1)
        else:
            logger.info("Seeding completed successfully")
            exit(0)

    finally:
        await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
