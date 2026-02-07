"""
Tests for letter worker jobs.

Implements test scenarios from [response-letter:LetterJob/TS-01] through [TS-07]
Implements test scenarios from [s3-document-storage:LetterJobs/TS-01] through [TS-02]
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from src.shared.storage import StorageUploadError
from src.worker.letter_jobs import (
    _get_group_config,
    _resolve_case_officer,
    _upload_letter_output,
    letter_job,
)


@pytest.fixture
def mock_redis_client():
    """Create mock RedisClient."""
    client = AsyncMock()
    client.get_letter = AsyncMock()
    client.get_result = AsyncMock()
    client.update_letter_status = AsyncMock()
    return client


@pytest.fixture
def sample_letter_record() -> dict:
    """A letter record as stored in Redis."""
    return {
        "letter_id": "ltr_01HQXK7V3WNPB8MTJF2R5ADGX9",
        "review_id": "rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
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


@pytest.fixture
def sample_review_result() -> dict:
    """A completed review result as stored in Redis."""
    return {
        "review_id": "rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
        "application_ref": "25/01178/REM",
        "status": "completed",
        "application": {
            "reference": "25/01178/REM",
            "address": "Land North of Railway, Bicester",
            "proposal": "Reserved matters for 150 dwellings",
            "applicant": "Test Developments Ltd",
            "case_officer": "Ms J. Smith",
        },
        "review": {
            "overall_rating": "amber",
            "full_markdown": (
                "# Cycle Advocacy Review: 25/01178/REM\n\n"
                "## Assessment Summary\n"
                "**Overall Rating:** AMBER\n\n"
                "The application provides insufficient cycle parking.\n"
            ),
        },
        "metadata": {"model": "claude-sonnet-4-5-20250929"},
    }


@pytest.fixture
def mock_anthropic_response():
    """Create a mock Anthropic API response."""
    message = MagicMock()
    message.content = [MagicMock(text="# Response Letter\n\nDear Ms J. Smith,\n\n...")]
    message.usage = MagicMock(input_tokens=3000, output_tokens=1500)
    return message


class TestSuccessfulGeneration:
    """Tests for successful letter generation."""

    @pytest.mark.asyncio
    async def test_successful_generation(
        self,
        mock_redis_client,
        sample_letter_record,
        sample_review_result,
        mock_anthropic_response,
    ) -> None:
        """
        Verifies [response-letter:LetterJob/TS-01]

        Given: A completed review result in Redis and a letter record with status=generating
        When: letter_job runs
        Then: Letter record updated to status=completed with Markdown content and metadata
        """
        mock_redis_client.get_letter.return_value = sample_letter_record
        mock_redis_client.get_result.return_value = sample_review_result

        ctx = {"redis_client": mock_redis_client}

        with patch("src.worker.letter_jobs.anthropic") as mock_anthropic_mod:
            mock_client = MagicMock()
            mock_anthropic_mod.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = mock_anthropic_response

            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                result = await letter_job(
                    ctx=ctx,
                    letter_id="ltr_01HQXK7V3WNPB8MTJF2R5ADGX9",
                    review_id="rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
                )

        assert result["status"] == "completed"
        assert result["letter_id"] == "ltr_01HQXK7V3WNPB8MTJF2R5ADGX9"
        assert result["metadata"]["input_tokens"] == 3000
        assert result["metadata"]["output_tokens"] == 1500
        assert result["metadata"]["model"] is not None
        assert result["metadata"]["processing_time_seconds"] >= 0

        # Verify Redis was updated
        mock_redis_client.update_letter_status.assert_called_once()
        call_kwargs = mock_redis_client.update_letter_status.call_args.kwargs
        assert call_kwargs["status"] == "completed"
        assert "Response Letter" in call_kwargs["content"]
        assert call_kwargs["metadata"]["input_tokens"] == 3000


class TestReviewResultMissing:
    """Tests for missing review result."""

    @pytest.mark.asyncio
    async def test_review_result_not_found(
        self,
        mock_redis_client,
        sample_letter_record,
    ) -> None:
        """
        Verifies [response-letter:LetterJob/TS-02]

        Given: Letter record exists but review result expired from Redis
        When: letter_job runs
        Then: Letter record updated to status=failed with error code review_result_not_found
        """
        mock_redis_client.get_letter.return_value = sample_letter_record
        mock_redis_client.get_result.return_value = None

        ctx = {"redis_client": mock_redis_client}

        result = await letter_job(
            ctx=ctx,
            letter_id="ltr_01HQXK7V3WNPB8MTJF2R5ADGX9",
            review_id="rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
        )

        assert result["status"] == "failed"
        assert result["error"] == "review_result_not_found"

        # Verify Redis was updated with failure
        mock_redis_client.update_letter_status.assert_called_once()
        call_kwargs = mock_redis_client.update_letter_status.call_args.kwargs
        assert call_kwargs["status"] == "failed"
        assert call_kwargs["error"]["code"] == "review_result_not_found"


class TestLLMCallFailure:
    """Tests for LLM call failure."""

    @pytest.mark.asyncio
    async def test_claude_api_error(
        self,
        mock_redis_client,
        sample_letter_record,
        sample_review_result,
    ) -> None:
        """
        Verifies [response-letter:LetterJob/TS-03]

        Given: Review result exists but Claude API returns an error
        When: letter_job runs
        Then: Letter record updated to status=failed with error code letter_generation_failed
        """
        mock_redis_client.get_letter.return_value = sample_letter_record
        mock_redis_client.get_result.return_value = sample_review_result

        ctx = {"redis_client": mock_redis_client}

        with patch("src.worker.letter_jobs.anthropic") as mock_anthropic_mod:
            mock_client = MagicMock()
            mock_anthropic_mod.Anthropic.return_value = mock_client
            mock_anthropic_mod.APIError = Exception
            mock_client.messages.create.side_effect = Exception("API rate limit exceeded")

            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                result = await letter_job(
                    ctx=ctx,
                    letter_id="ltr_01HQXK7V3WNPB8MTJF2R5ADGX9",
                    review_id="rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
                )

        assert result["status"] == "failed"
        assert result["error"] == "letter_generation_failed"

        # Verify Redis was updated with failure
        mock_redis_client.update_letter_status.assert_called_once()
        call_kwargs = mock_redis_client.update_letter_status.call_args.kwargs
        assert call_kwargs["status"] == "failed"
        assert call_kwargs["error"]["code"] == "letter_generation_failed"


class TestAdvocacyGroupFromEnvironment:
    """Tests for advocacy group configuration."""

    @pytest.mark.asyncio
    async def test_custom_group_from_env(
        self,
        mock_redis_client,
        sample_letter_record,
        sample_review_result,
        mock_anthropic_response,
    ) -> None:
        """
        Verifies [response-letter:LetterJob/TS-04]

        Given: ADVOCACY_GROUP_STYLISED=TestGroup set in env
        When: letter_job runs
        Then: Prompt includes "TestGroup" as the group name
        """
        mock_redis_client.get_letter.return_value = sample_letter_record
        mock_redis_client.get_result.return_value = sample_review_result

        ctx = {"redis_client": mock_redis_client}

        with patch("src.worker.letter_jobs.anthropic") as mock_anthropic_mod:
            mock_client = MagicMock()
            mock_anthropic_mod.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = mock_anthropic_response

            with patch.dict("os.environ", {
                "ANTHROPIC_API_KEY": "test-key",
                "ADVOCACY_GROUP_NAME": "Test Cycling Group",
                "ADVOCACY_GROUP_STYLISED": "TestGroup",
                "ADVOCACY_GROUP_SHORT": "TCG",
            }):
                await letter_job(
                    ctx=ctx,
                    letter_id="ltr_01HQXK7V3WNPB8MTJF2R5ADGX9",
                    review_id="rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
                )

        # Verify the system prompt passed to Claude includes the custom group
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert "TestGroup" in call_kwargs["system"]
        assert "TCG" in call_kwargs["system"]
        assert "Test Cycling Group" in call_kwargs["system"]

    @pytest.mark.asyncio
    async def test_default_group_when_no_env(
        self,
        mock_redis_client,
        sample_letter_record,
        sample_review_result,
        mock_anthropic_response,
    ) -> None:
        """
        Verifies [response-letter:LetterJob/TS-05]

        Given: No ADVOCACY_GROUP_* env vars set
        When: letter_job runs
        Then: Prompt includes "Bicester BUG" as the group name
        """
        mock_redis_client.get_letter.return_value = sample_letter_record
        mock_redis_client.get_result.return_value = sample_review_result

        ctx = {"redis_client": mock_redis_client}

        with patch("src.worker.letter_jobs.anthropic") as mock_anthropic_mod:
            mock_client = MagicMock()
            mock_anthropic_mod.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = mock_anthropic_response

            env = {"ANTHROPIC_API_KEY": "test-key"}
            # Remove any group env vars that might exist
            for key in ["ADVOCACY_GROUP_NAME", "ADVOCACY_GROUP_STYLISED", "ADVOCACY_GROUP_SHORT"]:
                env[key] = ""

            with patch.dict("os.environ", env, clear=False):
                # Need to also clear them to ensure defaults are used
                with patch("src.worker.letter_jobs._get_group_config") as mock_config:
                    mock_config.return_value = (
                        "Bicester Bike Users' Group",
                        "Bicester BUG",
                        "BBUG",
                    )
                    await letter_job(
                        ctx=ctx,
                        letter_id="ltr_01HQXK7V3WNPB8MTJF2R5ADGX9",
                        review_id="rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
                    )

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert "Bicester BUG" in call_kwargs["system"]
        assert "BBUG" in call_kwargs["system"]


class TestCaseOfficerResolution:
    """Tests for case officer resolution."""

    @pytest.mark.asyncio
    async def test_case_officer_from_review_data(
        self,
        mock_redis_client,
        sample_letter_record,
        sample_review_result,
        mock_anthropic_response,
    ) -> None:
        """
        Verifies [response-letter:LetterJob/TS-06]

        Given: Review application data includes case_officer field, no override in request
        When: letter_job runs
        Then: Prompt addresses the case officer by name
        """
        # No case_officer override in letter record
        sample_letter_record["case_officer"] = None
        # case_officer is in review_result application data
        mock_redis_client.get_letter.return_value = sample_letter_record
        mock_redis_client.get_result.return_value = sample_review_result

        ctx = {"redis_client": mock_redis_client}

        with patch("src.worker.letter_jobs.anthropic") as mock_anthropic_mod:
            mock_client = MagicMock()
            mock_anthropic_mod.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = mock_anthropic_response

            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                await letter_job(
                    ctx=ctx,
                    letter_id="ltr_01HQXK7V3WNPB8MTJF2R5ADGX9",
                    review_id="rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
                )

        # User prompt should contain the case officer from review data
        call_kwargs = mock_client.messages.create.call_args.kwargs
        user_msg = call_kwargs["messages"][0]["content"]
        assert "Dear Ms J. Smith" in user_msg

    @pytest.mark.asyncio
    async def test_case_officer_fallback_generic(
        self,
        mock_redis_client,
        sample_letter_record,
        sample_review_result,
        mock_anthropic_response,
    ) -> None:
        """
        Verifies [response-letter:LetterJob/TS-07]

        Given: No case officer in review data or request
        When: letter_job runs
        Then: Prompt uses "Dear Sir/Madam"
        """
        sample_letter_record["case_officer"] = None
        sample_review_result["application"]["case_officer"] = None
        mock_redis_client.get_letter.return_value = sample_letter_record
        mock_redis_client.get_result.return_value = sample_review_result

        ctx = {"redis_client": mock_redis_client}

        with patch("src.worker.letter_jobs.anthropic") as mock_anthropic_mod:
            mock_client = MagicMock()
            mock_anthropic_mod.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = mock_anthropic_response

            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                await letter_job(
                    ctx=ctx,
                    letter_id="ltr_01HQXK7V3WNPB8MTJF2R5ADGX9",
                    review_id="rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
                )

        call_kwargs = mock_client.messages.create.call_args.kwargs
        user_msg = call_kwargs["messages"][0]["content"]
        assert "Dear Sir/Madam" in user_msg


class TestResolveCaseOfficer:
    """Unit tests for _resolve_case_officer helper."""

    def test_request_override_takes_priority(self) -> None:
        """Request case_officer overrides review data."""
        result = _resolve_case_officer(
            "Mr A. Jones",
            {"application": {"case_officer": "Ms J. Smith"}},
        )
        assert result == "Mr A. Jones"

    def test_falls_back_to_review_data(self) -> None:
        """When no request override, uses review application data."""
        result = _resolve_case_officer(
            None,
            {"application": {"case_officer": "Ms J. Smith"}},
        )
        assert result == "Ms J. Smith"

    def test_returns_none_when_no_data(self) -> None:
        """Returns None when neither source has case officer."""
        result = _resolve_case_officer(None, {"application": {}})
        assert result is None


class TestGetGroupConfig:
    """Unit tests for _get_group_config helper."""

    def test_defaults(self) -> None:
        """Returns defaults when no env vars set."""
        with patch.dict("os.environ", {}, clear=True):
            name, stylised, short = _get_group_config()
            assert name == "Bicester Bike Users' Group"
            assert stylised == "Bicester BUG"
            assert short == "BBUG"

    def test_custom_env_vars(self) -> None:
        """Reads custom values from environment."""
        with patch.dict("os.environ", {
            "ADVOCACY_GROUP_NAME": "Oxford Cycling Network",
            "ADVOCACY_GROUP_STYLISED": "OxCycleNet",
            "ADVOCACY_GROUP_SHORT": "OCN",
        }):
            name, stylised, short = _get_group_config()
            assert name == "Oxford Cycling Network"
            assert stylised == "OxCycleNet"
            assert short == "OCN"


# ---------------------------------------------------------------------------
# S3 Document Storage tests
# ---------------------------------------------------------------------------


def _make_s3_backend_mock():
    """Create a mock S3 storage backend for testing."""
    backend = MagicMock()
    type(backend).is_remote = PropertyMock(return_value=True)
    backend.upload.return_value = None
    backend.delete_local.return_value = None
    return backend


class TestS3LetterOutputUpload:
    """
    Tests for S3 letter output upload.

    Implements [s3-document-storage:LetterJobs/TS-01], [TS-02]
    """

    @pytest.mark.asyncio
    async def test_s3_letter_output_upload(
        self,
        mock_redis_client,
        sample_letter_record,
        sample_review_result,
        mock_anthropic_response,
    ):
        """
        Verifies [s3-document-storage:LetterJobs/TS-01] - S3 letter output upload

        Given: S3 configured, letter generation succeeds
        When: Letter stored in Redis
        Then: letter.json and letter.md uploaded to S3
        """
        mock_redis_client.get_letter.return_value = sample_letter_record
        mock_redis_client.get_result.return_value = sample_review_result

        ctx = {"redis_client": mock_redis_client}

        mock_backend = _make_s3_backend_mock()

        with patch("src.worker.letter_jobs.anthropic") as mock_anthropic_mod, \
             patch("src.worker.letter_jobs.create_storage_backend", return_value=mock_backend):
            mock_client = MagicMock()
            mock_anthropic_mod.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = mock_anthropic_response

            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                result = await letter_job(
                    ctx=ctx,
                    letter_id="ltr_test_s3",
                    review_id="rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
                )

        assert result["status"] == "completed"

        # Verify upload was called (JSON + MD = 2 calls)
        assert mock_backend.upload.call_count == 2

        upload_keys = [call[0][1] for call in mock_backend.upload.call_args_list]
        assert any("ltr_test_s3_letter.json" in k for k in upload_keys)
        assert any("ltr_test_s3_letter.md" in k for k in upload_keys)

        # Verify S3 key path structure
        assert upload_keys[0] == "25_01178_REM/output/ltr_test_s3_letter.json"
        assert upload_keys[1] == "25_01178_REM/output/ltr_test_s3_letter.md"

    @pytest.mark.asyncio
    async def test_letter_upload_failure_non_fatal(
        self,
        mock_redis_client,
        sample_letter_record,
        sample_review_result,
        mock_anthropic_response,
    ):
        """
        Verifies [s3-document-storage:LetterJobs/TS-02] - Letter upload failure non-fatal

        Given: S3 configured, letter succeeds, S3 upload fails
        When: Letter stored in Redis
        Then: Warning logged, letter still available in Redis
        """
        mock_redis_client.get_letter.return_value = sample_letter_record
        mock_redis_client.get_result.return_value = sample_review_result

        ctx = {"redis_client": mock_redis_client}

        mock_backend = _make_s3_backend_mock()
        mock_backend.upload.side_effect = StorageUploadError(
            key="test", attempts=3, last_error=Exception("Network error")
        )

        with patch("src.worker.letter_jobs.anthropic") as mock_anthropic_mod, \
             patch("src.worker.letter_jobs.create_storage_backend", return_value=mock_backend):
            mock_client = MagicMock()
            mock_anthropic_mod.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = mock_anthropic_response

            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                result = await letter_job(
                    ctx=ctx,
                    letter_id="ltr_test_s3_fail",
                    review_id="rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
                )

        # Letter should still succeed despite upload failure
        assert result["status"] == "completed"

        # Redis should still have been updated
        mock_redis_client.update_letter_status.assert_called_once()
        call_kwargs = mock_redis_client.update_letter_status.call_args.kwargs
        assert call_kwargs["status"] == "completed"

    def test_upload_letter_output_writes_correct_content(self):
        """Verify _upload_letter_output writes JSON and MD with correct content."""
        backend = _make_s3_backend_mock()

        _upload_letter_output(
            letter_id="ltr_content_test",
            application_ref="25/00284/F",
            letter_content="# Letter\n\nDear Sir/Madam,\n\nTest letter.",
            metadata={"model": "test", "input_tokens": 100, "output_tokens": 200},
            storage=backend,
        )

        assert backend.upload.call_count == 2

        # Verify S3 keys
        json_key = backend.upload.call_args_list[0][0][1]
        md_key = backend.upload.call_args_list[1][0][1]
        assert json_key == "25_00284_F/output/ltr_content_test_letter.json"
        assert md_key == "25_00284_F/output/ltr_content_test_letter.md"

        # Verify paths are Path objects
        assert isinstance(backend.upload.call_args_list[0][0][0], Path)
        assert isinstance(backend.upload.call_args_list[1][0][0], Path)
