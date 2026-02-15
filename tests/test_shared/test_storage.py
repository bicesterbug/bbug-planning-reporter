"""
Tests for S3-compatible storage backend abstraction.

Verifies [s3-document-storage:StorageBackend/TS-01] - Protocol compliance
Verifies [s3-document-storage:S3StorageBackend/TS-01] through [TS-10]
Verifies [s3-document-storage:LocalStorageBackend/TS-01] through [TS-04]
Verifies [s3-document-storage:Factory/TS-01] through [TS-03]
Verifies [s3-document-storage:InMemoryBackend/TS-01] through [TS-02]
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.shared.storage import (
    InMemoryStorageBackend,
    LocalStorageBackend,
    S3StorageBackend,
    StorageBackend,
    StorageConfigError,
    StorageUploadError,
    create_storage_backend,
)

# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestStorageBackendProtocol:
    """Verifies [s3-document-storage:StorageBackend/TS-01] - Protocol compliance."""

    def test_local_backend_is_storage_backend(self):
        """LocalStorageBackend satisfies StorageBackend protocol."""
        backend = LocalStorageBackend()
        assert isinstance(backend, StorageBackend)

    def test_in_memory_backend_is_storage_backend(self):
        """InMemoryStorageBackend satisfies StorageBackend protocol."""
        backend = InMemoryStorageBackend()
        assert isinstance(backend, StorageBackend)

    def test_s3_backend_is_storage_backend(self):
        """S3StorageBackend satisfies StorageBackend protocol (without connection)."""
        backend = S3StorageBackend(
            endpoint_url="https://nyc3.digitaloceanspaces.com",
            bucket="test-bucket",
            access_key_id="test-key",
            secret_access_key="test-secret",
            validate_on_init=False,
        )
        assert isinstance(backend, StorageBackend)


# ---------------------------------------------------------------------------
# LocalStorageBackend
# ---------------------------------------------------------------------------


class TestLocalStorageBackend:
    """Tests for LocalStorageBackend file persistence and URL generation."""

    def test_upload_writes_file_to_output_directory(self, tmp_path: Path):
        """Upload copies the source file to the output directory under the given key."""
        backend = LocalStorageBackend(output_dir=str(tmp_path))
        src = tmp_path / "src.json"
        src.write_text('{"test": true}')

        backend.upload(src, "25_01178_REM/output/rev_xxx_review.json")

        dest = tmp_path / "25_01178_REM" / "output" / "rev_xxx_review.json"
        assert dest.exists()
        assert dest.read_text() == '{"test": true}'

    def test_upload_creates_parent_directories(self, tmp_path: Path):
        """Upload creates intermediate directories automatically."""
        backend = LocalStorageBackend(output_dir=str(tmp_path))
        src = tmp_path / "src.md"
        src.write_text("# Review")

        backend.upload(src, "deep/nested/path/file.md")

        assert (tmp_path / "deep" / "nested" / "path" / "file.md").exists()

    def test_public_url_returns_api_relative_path(self):
        """public_url returns an API-relative path for the file serving endpoint."""
        backend = LocalStorageBackend()
        url = backend.public_url("25_01178_REM/output/rev_xxx_review.json")
        assert url == "/api/v1/files/25_01178_REM/output/rev_xxx_review.json"

    def test_public_url_encodes_spaces(self):
        """public_url percent-encodes spaces in the key."""
        backend = LocalStorageBackend()
        url = backend.public_url("25_01178_REM/003_Delegated Officer Report.pdf")
        assert url == "/api/v1/files/25_01178_REM/003_Delegated%20Officer%20Report.pdf"

    def test_public_url_preserves_slashes(self):
        """public_url does not encode path separators."""
        backend = LocalStorageBackend()
        url = backend.public_url("path/to/file.json")
        assert "/" in url.replace("/api/v1/files/", "")

    def test_is_remote_false(self):
        """is_remote returns False for local storage."""
        backend = LocalStorageBackend()
        assert backend.is_remote is False

    def test_delete_local_is_noop(self, tmp_path: Path):
        """delete_local does not remove files (they stay on persistent volume)."""
        backend = LocalStorageBackend(output_dir=str(tmp_path))
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"test content")

        backend.delete_local(test_file)
        assert test_file.exists()

    def test_source_file_preserved_after_upload(self, tmp_path: Path):
        """Upload copies (not moves) the source file."""
        backend = LocalStorageBackend(output_dir=str(tmp_path / "output"))
        src = tmp_path / "src.json"
        src.write_text("content")

        backend.upload(src, "key.json")

        assert src.exists(), "Source file should not be removed"


# ---------------------------------------------------------------------------
# S3StorageBackend
# ---------------------------------------------------------------------------


class TestS3StorageBackend:
    """Verifies [s3-document-storage:S3StorageBackend/TS-01] through [TS-10]."""

    def _make_backend(self, **overrides) -> S3StorageBackend:
        """Create an S3StorageBackend with sensible test defaults (no validation)."""
        defaults = {
            "endpoint_url": "https://nyc3.digitaloceanspaces.com",
            "bucket": "mybucket",
            "access_key_id": "AKIAIOSFODNN7EXAMPLE",
            "secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "prefix": "planning",
            "validate_on_init": False,
        }
        defaults.update(overrides)
        return S3StorageBackend(**defaults)

    def test_is_remote_true(self):
        """S3 backend is_remote returns True."""
        backend = self._make_backend()
        assert backend.is_remote is True

    def test_upload_calls_put_object(self, tmp_path: Path):
        """Verifies [s3-document-storage:S3StorageBackend/TS-01] - Upload with public-read."""
        backend = self._make_backend()
        test_file = tmp_path / "doc.pdf"
        test_file.write_bytes(b"PDF content here")

        mock_client = MagicMock()
        backend._client = mock_client

        backend.upload(test_file, "25_00284_F/001_Transport.pdf")

        mock_client.put_object.assert_called_once()
        call_kwargs = mock_client.put_object.call_args[1]
        assert call_kwargs["Bucket"] == "mybucket"
        assert call_kwargs["Key"] == "planning/25_00284_F/001_Transport.pdf"
        assert call_kwargs["ACL"] == "public-read"

    def test_public_url_format(self):
        """Verifies [s3-document-storage:S3StorageBackend/TS-02] - Public URL format."""
        backend = self._make_backend()
        url = backend.public_url("25_00284_F/001_Transport.pdf")
        assert url == "https://mybucket.nyc3.digitaloceanspaces.com/planning/25_00284_F/001_Transport.pdf"

    def test_public_url_encodes_spaces(self):
        """public_url percent-encodes spaces in S3 keys."""
        backend = self._make_backend()
        url = backend.public_url("25_00284_F/003_Delegated Officer Report.pdf")
        assert "Delegated%20Officer%20Report" in url
        assert "Delegated Officer" not in url

    def test_upload_retry_on_failure(self, tmp_path: Path):
        """Verifies [s3-document-storage:S3StorageBackend/TS-03] - Retry on failure."""
        backend = self._make_backend()
        test_file = tmp_path / "doc.pdf"
        test_file.write_bytes(b"content")

        mock_client = MagicMock()
        # Fail twice, succeed on third
        mock_client.put_object.side_effect = [
            Exception("Timeout"),
            Exception("Timeout"),
            None,  # success
        ]
        backend._client = mock_client

        with patch("src.shared.storage.time.sleep"):
            backend.upload(test_file, "key.pdf")

        assert mock_client.put_object.call_count == 3

    def test_upload_permanent_failure(self, tmp_path: Path):
        """Verifies [s3-document-storage:S3StorageBackend/TS-04] - Raises after max retries."""
        backend = self._make_backend()
        test_file = tmp_path / "doc.pdf"
        test_file.write_bytes(b"content")

        mock_client = MagicMock()
        mock_client.put_object.side_effect = Exception("Permanent failure")
        backend._client = mock_client

        with patch("src.shared.storage.time.sleep"), pytest.raises(StorageUploadError) as exc_info:
                backend.upload(test_file, "key.pdf")

        assert exc_info.value.attempts == 3
        assert exc_info.value.key == "planning/key.pdf"

    def test_upload_multipart_for_large_files(self, tmp_path: Path):
        """Verifies [s3-document-storage:S3StorageBackend/TS-05] - Multipart for large files."""
        backend = self._make_backend()
        test_file = tmp_path / "large.pdf"
        # Write a file > 8MB
        test_file.write_bytes(os.urandom(9 * 1024 * 1024))

        mock_client = MagicMock()
        backend._client = mock_client

        mock_transfer_config = MagicMock()
        backend._transfer_config = mock_transfer_config

        backend.upload(test_file, "large.pdf")

        # Should use upload_file (multipart) instead of put_object
        mock_client.upload_file.assert_called_once()
        call_args = mock_client.upload_file.call_args
        assert call_args[0][0] == str(test_file)  # local file path
        assert call_args[0][1] == "mybucket"  # bucket
        assert call_args[0][2] == "planning/large.pdf"  # key
        assert call_args[1]["ExtraArgs"] == {"ACL": "public-read"}

    def test_startup_validation_missing_bucket(self):
        """Verifies [s3-document-storage:S3StorageBackend/TS-06] - Missing bucket error."""
        mock_client = MagicMock()
        mock_client.head_bucket.side_effect = Exception("NoSuchBucket")

        with patch("boto3.client", return_value=mock_client), pytest.raises(StorageConfigError, match="connectivity check failed"):
            S3StorageBackend(
                endpoint_url="https://nyc3.digitaloceanspaces.com",
                bucket="nonexistent",
                access_key_id="key",
                secret_access_key="secret",
                validate_on_init=True,
            )

    def test_startup_validation_unreachable(self):
        """Verifies [s3-document-storage:S3StorageBackend/TS-07] - Unreachable endpoint."""
        mock_client = MagicMock()
        mock_client.head_bucket.side_effect = ConnectionError("Could not connect")

        with patch("boto3.client", return_value=mock_client), pytest.raises(StorageConfigError, match="connectivity check failed"):
            S3StorageBackend(
                endpoint_url="https://bad-endpoint.example.com",
                bucket="bucket",
                access_key_id="key",
                secret_access_key="secret",
                validate_on_init=True,
            )

    def test_no_credentials_in_logs(self, tmp_path: Path):
        """Verifies [s3-document-storage:S3StorageBackend/TS-08] - No creds in error messages."""
        access_key = "AKIAIOSFODNN7EXAMPLE"
        secret_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"

        backend = self._make_backend(
            access_key_id=access_key,
            secret_access_key=secret_key,
        )

        # Simulate an error message containing credentials
        error_msg = f"Auth failed for {access_key} with secret {secret_key}"
        scrubbed = backend._scrub_credentials(error_msg)

        assert access_key not in scrubbed
        assert secret_key not in scrubbed
        assert "***ACCESS_KEY***" in scrubbed
        assert "***SECRET_KEY***" in scrubbed

    def test_custom_prefix(self):
        """Verifies [s3-document-storage:S3StorageBackend/TS-09] - Custom prefix."""
        backend = self._make_backend(prefix="bbug-prod")
        url = backend.public_url("25_00284_F/doc.pdf")
        assert "bbug-prod/25_00284_F/doc.pdf" in url

    def test_default_prefix(self):
        """Verifies [s3-document-storage:S3StorageBackend/TS-10] - Default prefix."""
        backend = self._make_backend(prefix="planning")
        url = backend.public_url("25_00284_F/doc.pdf")
        assert "planning/25_00284_F/doc.pdf" in url

    def test_delete_local_removes_file(self, tmp_path: Path):
        """S3 backend deletes local temp files."""
        backend = self._make_backend()
        test_file = tmp_path / "temp.pdf"
        test_file.write_bytes(b"temp content")

        backend.delete_local(test_file)
        assert not test_file.exists()

    def test_delete_local_missing_file_no_error(self, tmp_path: Path):
        """Deleting a non-existent file does not raise."""
        backend = self._make_backend()
        backend.delete_local(tmp_path / "nonexistent.pdf")

    def test_startup_validation_success(self):
        """Successful startup validation logs and does not raise."""
        mock_client = MagicMock()
        mock_client.head_bucket.return_value = {}

        with patch("boto3.client", return_value=mock_client):
            backend = S3StorageBackend(
                endpoint_url="https://nyc3.digitaloceanspaces.com",
                bucket="mybucket",
                access_key_id="key",
                secret_access_key="secret",
                validate_on_init=True,
            )
            assert backend.is_remote is True

    def test_public_url_no_prefix(self):
        """Public URL with empty prefix omits prefix segment."""
        backend = self._make_backend(prefix="")
        url = backend.public_url("25_00284_F/doc.pdf")
        assert url == "https://mybucket.nyc3.digitaloceanspaces.com/25_00284_F/doc.pdf"


# ---------------------------------------------------------------------------
# InMemoryStorageBackend
# ---------------------------------------------------------------------------


class TestInMemoryStorageBackend:
    """Verifies [s3-document-storage:InMemoryBackend/TS-01] through [TS-02]."""

    def test_upload_stores_content(self, tmp_path: Path):
        """Verifies [s3-document-storage:InMemoryBackend/TS-01] - Upload stores content."""
        backend = InMemoryStorageBackend()
        test_file = tmp_path / "doc.pdf"
        test_file.write_bytes(b"test PDF content")

        backend.upload(test_file, "25_00284_F/001_Transport.pdf")

        assert "25_00284_F/001_Transport.pdf" in backend.uploads
        assert backend.uploads["25_00284_F/001_Transport.pdf"] == b"test PDF content"

    def test_public_url_predictable(self):
        """Verifies [s3-document-storage:InMemoryBackend/TS-02] - Predictable URL."""
        backend = InMemoryStorageBackend()
        url = backend.public_url("25_00284_F/001_Transport.pdf")
        assert url == "https://test-bucket.example.com/25_00284_F/001_Transport.pdf"

    def test_public_url_encodes_spaces(self):
        """public_url percent-encodes spaces."""
        backend = InMemoryStorageBackend()
        url = backend.public_url("path/file name.pdf")
        assert "file%20name.pdf" in url
        assert "file name" not in url

    def test_public_url_custom_base(self):
        """Custom base URL is used in public_url."""
        backend = InMemoryStorageBackend(base_url="https://custom.example.com")
        url = backend.public_url("key.pdf")
        assert url == "https://custom.example.com/key.pdf"

    def test_is_remote_true(self):
        """InMemory backend is_remote returns True (simulates S3)."""
        backend = InMemoryStorageBackend()
        assert backend.is_remote is True

    def test_delete_local_tracks_and_removes(self, tmp_path: Path):
        """delete_local removes file and tracks path."""
        backend = InMemoryStorageBackend()
        test_file = tmp_path / "temp.pdf"
        test_file.write_bytes(b"content")

        backend.delete_local(test_file)

        assert not test_file.exists()
        assert str(test_file) in backend.deleted

    def test_download_to_roundtrip(self, tmp_path: Path):
        """Upload then download_to returns same content."""
        backend = InMemoryStorageBackend()
        src = tmp_path / "src.pdf"
        src.write_bytes(b"roundtrip content")

        backend.upload(src, "key.pdf")

        dest = tmp_path / "dest.pdf"
        backend.download_to("key.pdf", dest)

        assert dest.read_bytes() == b"roundtrip content"

    def test_download_to_missing_key_raises(self, tmp_path: Path):
        """download_to raises FileNotFoundError for missing key."""
        backend = InMemoryStorageBackend()
        with pytest.raises(FileNotFoundError):
            backend.download_to("missing.pdf", tmp_path / "dest.pdf")


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


class TestCreateStorageBackend:
    """Verifies [s3-document-storage:Factory/TS-01] through [TS-03]."""

    def test_no_s3_vars_returns_local(self):
        """Verifies [s3-document-storage:Factory/TS-02] - No S3 vars returns local."""
        env = {}
        with patch.dict(os.environ, env, clear=True):
            # Ensure S3_ENDPOINT_URL is not set
            os.environ.pop("S3_ENDPOINT_URL", None)
            os.environ.pop("S3_BUCKET", None)
            os.environ.pop("S3_ACCESS_KEY_ID", None)
            os.environ.pop("S3_SECRET_ACCESS_KEY", None)

            backend = create_storage_backend()
            assert isinstance(backend, LocalStorageBackend)
            assert backend.is_remote is False

    def test_all_s3_vars_returns_s3(self):
        """Verifies [s3-document-storage:Factory/TS-01] - All S3 vars returns S3."""
        mock_client = MagicMock()
        mock_client.head_bucket.return_value = {}

        env = {
            "S3_ENDPOINT_URL": "https://nyc3.digitaloceanspaces.com",
            "S3_BUCKET": "mybucket",
            "S3_ACCESS_KEY_ID": "test-key",
            "S3_SECRET_ACCESS_KEY": "test-secret",
        }

        with patch.dict(os.environ, env, clear=False), patch("boto3.client", return_value=mock_client):
            backend = create_storage_backend()
            assert isinstance(backend, S3StorageBackend)
            assert backend.is_remote is True

    def test_partial_s3_config_raises(self):
        """Verifies [s3-document-storage:Factory/TS-03] - Partial config raises error."""
        env = {
            "S3_ENDPOINT_URL": "https://nyc3.digitaloceanspaces.com",
            # S3_BUCKET missing
        }

        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("S3_BUCKET", None)
            os.environ.pop("S3_ACCESS_KEY_ID", None)
            os.environ.pop("S3_SECRET_ACCESS_KEY", None)

            with pytest.raises(StorageConfigError, match="missing S3_BUCKET"):
                create_storage_backend()

    def test_partial_config_names_all_missing(self):
        """Error message lists all missing variables."""
        env = {
            "S3_ENDPOINT_URL": "https://nyc3.digitaloceanspaces.com",
            "S3_BUCKET": "mybucket",
            # S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY missing
        }

        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("S3_ACCESS_KEY_ID", None)
            os.environ.pop("S3_SECRET_ACCESS_KEY", None)

            with pytest.raises(StorageConfigError, match="S3_ACCESS_KEY_ID") as exc_info:
                create_storage_backend()
            assert "S3_SECRET_ACCESS_KEY" in str(exc_info.value)

    def test_custom_prefix_from_env(self):
        """Factory reads S3_KEY_PREFIX from environment."""
        mock_client = MagicMock()
        mock_client.head_bucket.return_value = {}

        env = {
            "S3_ENDPOINT_URL": "https://nyc3.digitaloceanspaces.com",
            "S3_BUCKET": "mybucket",
            "S3_ACCESS_KEY_ID": "test-key",
            "S3_SECRET_ACCESS_KEY": "test-secret",
            "S3_KEY_PREFIX": "bbug-prod",
        }

        with patch.dict(os.environ, env, clear=False), patch("boto3.client", return_value=mock_client):
            backend = create_storage_backend()
            assert isinstance(backend, S3StorageBackend)
            url = backend.public_url("test.pdf")
            assert "bbug-prod/test.pdf" in url

    def test_default_prefix(self):
        """Factory uses 'planning' as default prefix."""
        mock_client = MagicMock()
        mock_client.head_bucket.return_value = {}

        env = {
            "S3_ENDPOINT_URL": "https://nyc3.digitaloceanspaces.com",
            "S3_BUCKET": "mybucket",
            "S3_ACCESS_KEY_ID": "test-key",
            "S3_SECRET_ACCESS_KEY": "test-secret",
        }

        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("S3_KEY_PREFIX", None)
            with patch("boto3.client", return_value=mock_client):
                backend = create_storage_backend()
                url = backend.public_url("test.pdf")
                assert "planning/test.pdf" in url


# ---------------------------------------------------------------------------
# Exception classes
# ---------------------------------------------------------------------------


class TestExceptions:
    """Test exception classes."""

    def test_storage_config_error_message(self):
        """StorageConfigError carries descriptive message."""
        err = StorageConfigError("Missing S3_BUCKET")
        assert "S3_BUCKET" in str(err)

    def test_storage_upload_error_attributes(self):
        """StorageUploadError carries key, attempts, and last_error."""
        cause = ConnectionError("timeout")
        err = StorageUploadError(key="planning/doc.pdf", attempts=3, last_error=cause)
        assert err.key == "planning/doc.pdf"
        assert err.attempts == 3
        assert err.last_error is cause
        assert "planning/doc.pdf" in str(err)
        assert "3 attempts" in str(err)
