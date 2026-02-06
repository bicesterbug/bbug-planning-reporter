"""
Progress reporting for document ingestion.

Implements [document-processing:FR-011] - Progress reporting during ingestion
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class IngestionProgress:
    """Progress state for document ingestion."""

    total_documents: int
    ingested_count: int = 0
    failed_count: int = 0
    current_document: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        """Check if ingestion is complete."""
        return (self.ingested_count + self.failed_count) >= self.total_documents

    @property
    def progress_percent(self) -> float:
        """Get progress as percentage."""
        if self.total_documents == 0:
            return 100.0
        return ((self.ingested_count + self.failed_count) / self.total_documents) * 100

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "total_documents": self.total_documents,
            "ingested_count": self.ingested_count,
            "failed_count": self.failed_count,
            "current_document": self.current_document,
            "progress_percent": round(self.progress_percent, 1),
            "is_complete": self.is_complete,
            "started_at": self.started_at.isoformat(),
            "errors": self.errors,
        }

    def format_message(self) -> str:
        """Format progress as human-readable message."""
        if self.is_complete:
            return f"Ingested {self.ingested_count} of {self.total_documents} documents ({self.failed_count} failed)"
        return f"Ingesting {self.ingested_count + 1} of {self.total_documents} documents"


class ProgressCallback(Protocol):
    """Protocol for progress callbacks."""

    async def __call__(self, progress: IngestionProgress) -> None:
        """Called when progress is updated."""
        ...


class ProgressReporter:
    """
    Reports ingestion progress to callbacks and optionally Redis.

    Implements [document-processing:ITS-03] - Progress reporting
    """

    def __init__(
        self,
        total_documents: int,
        application_ref: str,
        callbacks: list[ProgressCallback] | None = None,
    ) -> None:
        """
        Initialize progress reporter.

        Args:
            total_documents: Total number of documents to ingest.
            application_ref: Application reference being processed.
            callbacks: Optional list of async callbacks to notify.
        """
        self._progress = IngestionProgress(total_documents=total_documents)
        self._application_ref = application_ref
        self._callbacks = callbacks or []

    @property
    def progress(self) -> IngestionProgress:
        """Get current progress state."""
        return self._progress

    async def start_document(self, document_path: str) -> None:
        """
        Mark start of document processing.

        Args:
            document_path: Path to document being processed.
        """
        self._progress.current_document = document_path
        logger.info(
            "Starting document ingestion",
            document=document_path,
            progress=self._progress.format_message(),
            application_ref=self._application_ref,
        )
        await self._notify()

    async def complete_document(self, document_path: str, success: bool, error: str | None = None) -> None:
        """
        Mark completion of document processing.

        Args:
            document_path: Path to document that was processed.
            success: Whether ingestion succeeded.
            error: Optional error message if failed.
        """
        if success:
            self._progress.ingested_count += 1
            logger.info(
                "Document ingested successfully",
                document=document_path,
                progress=self._progress.format_message(),
                application_ref=self._application_ref,
            )
        else:
            self._progress.failed_count += 1
            self._progress.errors.append({
                "document": document_path,
                "error": error or "Unknown error",
            })
            logger.warning(
                "Document ingestion failed",
                document=document_path,
                error=error,
                progress=self._progress.format_message(),
                application_ref=self._application_ref,
            )

        self._progress.current_document = None
        await self._notify()

    async def _notify(self) -> None:
        """Notify all callbacks of progress update."""
        for callback in self._callbacks:
            try:
                await callback(self._progress)
            except Exception as e:
                logger.error("Progress callback failed", error=str(e))


class RedisProgressCallback:
    """
    Publishes progress to Redis pub/sub channel.

    Channel format: document_ingestion:{application_ref}
    """

    def __init__(self, redis_client: Any, application_ref: str) -> None:
        """
        Initialize Redis progress callback.

        Args:
            redis_client: Async Redis client.
            application_ref: Application reference for channel name.
        """
        self._redis = redis_client
        self._channel = f"document_ingestion:{application_ref.replace('/', '_')}"

    async def __call__(self, progress: IngestionProgress) -> None:
        """Publish progress to Redis channel."""
        import json

        message = json.dumps({
            "type": "ingestion_progress",
            "progress": progress.to_dict(),
            "message": progress.format_message(),
        })

        try:
            await self._redis.publish(self._channel, message)
            logger.debug(
                "Progress published to Redis",
                channel=self._channel,
                progress_percent=progress.progress_percent,
            )
        except Exception as e:
            logger.error("Failed to publish progress to Redis", error=str(e))
