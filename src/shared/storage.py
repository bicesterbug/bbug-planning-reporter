"""
S3-compatible document storage backend abstraction.

Implements [s3-document-storage:FR-001] - Storage backend toggle
Implements [s3-document-storage:FR-002] - Document upload with public-read ACL
Implements [s3-document-storage:FR-004] - Public URL generation
Implements [s3-document-storage:FR-006] - S3 configuration validation
Implements [s3-document-storage:FR-007] - Configurable S3 key prefix
Implements [s3-document-storage:NFR-001] - Multipart upload for large files
Implements [s3-document-storage:NFR-002] - Upload retries for reliability
Implements [s3-document-storage:NFR-003] - No credentials in logs
Implements [s3-document-storage:NFR-004] - Backwards compatible local backend
Implements [s3-document-storage:NFR-005] - Fail-fast startup validation
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class StorageConfigError(Exception):
    """Raised when S3 configuration is incomplete or invalid.

    Implements [s3-document-storage:FR-006]
    """

    pass


class StorageUploadError(Exception):
    """Raised when an S3 upload fails after all retries.

    Implements [s3-document-storage:FR-002]
    """

    def __init__(self, key: str, attempts: int, last_error: Exception | None = None):
        self.key = key
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"Failed to upload '{key}' after {attempts} attempts: {last_error}"
        )


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
# Implements [s3-document-storage:StorageBackend/TS-01]
class StorageBackend(Protocol):
    """Protocol defining the interface for document storage operations.

    Implements [s3-document-storage:FR-001] - Toggle mechanism via is_remote property
    """

    @property
    def is_remote(self) -> bool:
        """Whether this backend stores files remotely (S3) vs locally."""
        ...

    def upload(self, local_path: Path, key: str) -> None:
        """Upload a local file to the storage backend.

        Args:
            local_path: Path to the local file to upload.
            key: Storage key (relative path within the bucket/prefix).

        Raises:
            StorageUploadError: If upload fails after retries.
        """
        ...

    def public_url(self, key: str) -> str | None:
        """Get the public URL for a stored object.

        Args:
            key: Storage key (relative path within the bucket/prefix).

        Returns:
            Public URL string, or None if the backend doesn't generate URLs.
        """
        ...

    def download_to(self, key: str, local_path: Path) -> None:
        """Download a stored object to a local file.

        Args:
            key: Storage key to download.
            local_path: Local path to write the file to.
        """
        ...

    def delete_local(self, local_path: Path) -> None:
        """Delete a local file (used for temp cleanup after ingestion).

        Args:
            local_path: Path to the local file to delete.
        """
        ...


# ---------------------------------------------------------------------------
# Local (no-op) backend
# ---------------------------------------------------------------------------


class LocalStorageBackend:
    """Local filesystem storage backend.

    Persists output files to a local directory and generates API-relative
    URLs for serving them via the files endpoint.

    Implements [s3-document-storage:FR-001] - Default backend when S3 not configured
    """

    def __init__(self, output_dir: str = "/data/output") -> None:
        self._output_dir = Path(output_dir)

    @property
    def is_remote(self) -> bool:
        return False

    def upload(self, local_path: Path, key: str) -> None:
        dest = self._output_dir / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dest)

    def public_url(self, key: str) -> str | None:
        return f"/api/v1/files/{key}"

    def download_to(self, key: str, local_path: Path) -> None:
        src = self._output_dir / key
        local_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, local_path)

    def delete_local(self, local_path: Path) -> None:
        pass


# ---------------------------------------------------------------------------
# S3 backend
# ---------------------------------------------------------------------------

# Retry configuration
_S3_MAX_RETRIES = 3
_S3_RETRY_BASE_DELAY = 1.0  # seconds
_S3_MULTIPART_THRESHOLD = 8 * 1024 * 1024  # 8MB


class S3StorageBackend:
    """S3-compatible storage backend using boto3.

    Uploads files with public-read ACL. Generates public URLs.
    Validates configuration on construction (fail-fast).

    Implements [s3-document-storage:FR-002] - Upload with public-read ACL
    Implements [s3-document-storage:FR-004] - Public URL generation
    Implements [s3-document-storage:FR-006] - Startup validation
    Implements [s3-document-storage:FR-007] - Configurable key prefix
    Implements [s3-document-storage:NFR-001] - Multipart upload for large files
    Implements [s3-document-storage:NFR-002] - Upload retries
    Implements [s3-document-storage:NFR-003] - No credentials in logs
    Implements [s3-document-storage:NFR-005] - Fail-fast startup validation
    """

    def __init__(
        self,
        endpoint_url: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        prefix: str = "planning",
        region: str | None = None,
        validate_on_init: bool = True,
    ):
        self._endpoint_url = endpoint_url.rstrip("/")
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._region = region or self._infer_region(endpoint_url)

        # Build public base URL from endpoint
        # e.g. https://nyc3.digitaloceanspaces.com -> https://bucket.nyc3.digitaloceanspaces.com
        parsed = urlparse(self._endpoint_url)
        self._public_base_url = f"{parsed.scheme}://{self._bucket}.{parsed.hostname}"

        # Lazy-init boto3 client (avoids import at module level)
        self._client: Any = None
        self._transfer_config: Any = None

        if validate_on_init:
            self._validate_and_connect()

    @property
    def is_remote(self) -> bool:
        return True

    def _infer_region(self, endpoint_url: str) -> str:
        """Infer region from endpoint URL (e.g. nyc3 from nyc3.digitaloceanspaces.com)."""
        parsed = urlparse(endpoint_url)
        hostname = parsed.hostname or ""
        parts = hostname.split(".")
        if len(parts) >= 3:
            return parts[0]
        return "us-east-1"

    def _get_client(self) -> Any:
        """Get or create the boto3 S3 client."""
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "s3",
                endpoint_url=self._endpoint_url,
                aws_access_key_id=self._access_key_id,
                aws_secret_access_key=self._secret_access_key,
                region_name=self._region,
                config=Config(
                    retries={"max_attempts": 0},  # We handle retries ourselves
                    signature_version="s3v4",
                ),
            )
        return self._client

    def _get_transfer_config(self) -> Any:
        """Get boto3 transfer config for multipart upload."""
        if self._transfer_config is None:
            from boto3.s3.transfer import TransferConfig

            # Implements [s3-document-storage:NFR-001] - Multipart for files > 8MB
            self._transfer_config = TransferConfig(
                multipart_threshold=_S3_MULTIPART_THRESHOLD,
                multipart_chunksize=_S3_MULTIPART_THRESHOLD,
            )
        return self._transfer_config

    def _validate_and_connect(self) -> None:
        """Validate configuration and test connectivity.

        Implements [s3-document-storage:S3StorageBackend/TS-06]
        Implements [s3-document-storage:S3StorageBackend/TS-07]
        """
        client = self._get_client()
        try:
            client.head_bucket(Bucket=self._bucket)
            logger.info(
                "S3 storage enabled",
                bucket=self._bucket,
                prefix=self._prefix,
                endpoint=self._endpoint_url,
            )
        except Exception as e:
            error_msg = self._scrub_credentials(str(e))
            raise StorageConfigError(
                f"S3 connectivity check failed for bucket '{self._bucket}': {error_msg}"
            ) from None

    def _full_key(self, key: str) -> str:
        """Build the full S3 object key including prefix."""
        if self._prefix:
            return f"{self._prefix}/{key}"
        return key

    def _scrub_credentials(self, message: str) -> str:
        """Remove credentials from error messages before logging.

        Implements [s3-document-storage:NFR-003] - No credentials in logs
        Implements [s3-document-storage:S3StorageBackend/TS-08]
        """
        scrubbed = message
        if self._access_key_id:
            scrubbed = scrubbed.replace(self._access_key_id, "***ACCESS_KEY***")
        if self._secret_access_key:
            scrubbed = scrubbed.replace(self._secret_access_key, "***SECRET_KEY***")
        return scrubbed

    def upload(self, local_path: Path, key: str) -> None:
        """Upload a local file to S3 with public-read ACL.

        Implements [s3-document-storage:S3StorageBackend/TS-01] - Upload with public-read
        Implements [s3-document-storage:S3StorageBackend/TS-03] - Retry on failure
        Implements [s3-document-storage:S3StorageBackend/TS-04] - Permanent failure after retries
        Implements [s3-document-storage:S3StorageBackend/TS-05] - Multipart for large files
        """
        full_key = self._full_key(key)
        client = self._get_client()
        last_error: Exception | None = None

        file_size = local_path.stat().st_size

        for attempt in range(1, _S3_MAX_RETRIES + 1):
            try:
                if file_size > _S3_MULTIPART_THRESHOLD:
                    # Implements [s3-document-storage:NFR-001]
                    client.upload_file(
                        str(local_path),
                        self._bucket,
                        full_key,
                        ExtraArgs={"ACL": "public-read"},
                        Config=self._get_transfer_config(),
                    )
                else:
                    with open(local_path, "rb") as f:
                        client.put_object(
                            Bucket=self._bucket,
                            Key=full_key,
                            Body=f,
                            ACL="public-read",
                        )

                logger.debug(
                    "S3 upload complete",
                    key=full_key,
                    size_bytes=file_size,
                    attempt=attempt,
                )
                return

            except Exception as e:
                last_error = e
                scrubbed_msg = self._scrub_credentials(str(e))
                if attempt < _S3_MAX_RETRIES:
                    delay = _S3_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "S3 upload failed, retrying",
                        key=full_key,
                        attempt=attempt,
                        max_retries=_S3_MAX_RETRIES,
                        retry_delay=delay,
                        error=scrubbed_msg,
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        "S3 upload failed permanently",
                        key=full_key,
                        attempts=_S3_MAX_RETRIES,
                        error=scrubbed_msg,
                    )

        raise StorageUploadError(
            key=full_key,
            attempts=_S3_MAX_RETRIES,
            last_error=last_error,
        )

    def public_url(self, key: str) -> str | None:
        """Get the public URL for a stored object.

        Implements [s3-document-storage:S3StorageBackend/TS-02] - URL format
        Implements [s3-document-storage:S3StorageBackend/TS-09] - Custom prefix
        Implements [s3-document-storage:S3StorageBackend/TS-10] - Default prefix
        """
        full_key = self._full_key(key)
        return f"{self._public_base_url}/{full_key}"

    def download_to(self, key: str, local_path: Path) -> None:
        """Download an object from S3 to a local file."""
        full_key = self._full_key(key)
        client = self._get_client()
        local_path.parent.mkdir(parents=True, exist_ok=True)
        client.download_file(self._bucket, full_key, str(local_path))

    def delete_local(self, local_path: Path) -> None:
        """Delete a local temporary file."""
        try:
            if local_path.exists():
                local_path.unlink()
                logger.debug("Deleted local temp file", path=str(local_path))
        except OSError as e:
            logger.warning(
                "Failed to delete local temp file",
                path=str(local_path),
                error=str(e),
            )


# ---------------------------------------------------------------------------
# In-memory backend (testing)
# ---------------------------------------------------------------------------


class InMemoryStorageBackend:
    """In-memory storage backend for testing.

    Stores uploads in a dict and generates predictable URLs.
    Useful for verifying upload calls without mocking boto3.
    """

    def __init__(self, base_url: str = "https://test-bucket.example.com") -> None:
        self._base_url = base_url.rstrip("/")
        self.uploads: dict[str, bytes] = {}
        self.deleted: list[str] = []

    @property
    def is_remote(self) -> bool:
        return True

    def upload(self, local_path: Path, key: str) -> None:
        # Implements [s3-document-storage:InMemoryBackend/TS-01]
        self.uploads[key] = local_path.read_bytes()

    def public_url(self, key: str) -> str | None:
        # Implements [s3-document-storage:InMemoryBackend/TS-02]
        return f"{self._base_url}/{key}"

    def download_to(self, key: str, local_path: Path) -> None:
        if key not in self.uploads:
            raise FileNotFoundError(f"Key not found in in-memory store: {key}")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(self.uploads[key])

    def delete_local(self, local_path: Path) -> None:
        self.deleted.append(str(local_path))
        if local_path.exists():
            local_path.unlink()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_storage_backend() -> StorageBackend:
    """Create the appropriate storage backend from environment variables.

    Reads S3_ENDPOINT_URL to determine whether to use S3 or local storage.
    When S3 is enabled, validates all required variables are present and
    tests connectivity (fail-fast).

    Implements [s3-document-storage:FR-001] - Toggle based on S3_ENDPOINT_URL
    Implements [s3-document-storage:FR-006] - Validation at startup
    Implements [s3-document-storage:NFR-005] - Fail-fast on bad config

    Implements [s3-document-storage:Factory/TS-01]
    Implements [s3-document-storage:Factory/TS-02]
    Implements [s3-document-storage:Factory/TS-03]

    Returns:
        StorageBackend: S3StorageBackend if S3 configured, LocalStorageBackend otherwise.

    Raises:
        StorageConfigError: If S3 is partially configured (missing required variables)
            or connectivity check fails.
    """
    endpoint_url = os.getenv("S3_ENDPOINT_URL")

    if not endpoint_url:
        logger.debug("S3 not configured, using local storage backend")
        return LocalStorageBackend()

    # S3 is requested — validate all required variables
    required_vars = {
        "S3_BUCKET": os.getenv("S3_BUCKET"),
        "S3_ACCESS_KEY_ID": os.getenv("S3_ACCESS_KEY_ID"),
        "S3_SECRET_ACCESS_KEY": os.getenv("S3_SECRET_ACCESS_KEY"),
    }

    missing = [name for name, value in required_vars.items() if not value]
    if missing:
        raise StorageConfigError(
            f"S3 configuration incomplete: missing {', '.join(missing)}"
        )

    prefix = os.getenv("S3_KEY_PREFIX", "planning")
    region = os.getenv("S3_REGION")

    return S3StorageBackend(
        endpoint_url=endpoint_url,
        bucket=required_vars["S3_BUCKET"],  # type: ignore[arg-type]
        access_key_id=required_vars["S3_ACCESS_KEY_ID"],  # type: ignore[arg-type]
        secret_access_key=required_vars["S3_SECRET_ACCESS_KEY"],  # type: ignore[arg-type]
        prefix=prefix,
        region=region,
        validate_on_init=True,
    )
