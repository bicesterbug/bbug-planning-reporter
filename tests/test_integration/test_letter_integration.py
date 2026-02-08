"""
Integration and E2E tests for response letter generation.

Implements test scenarios:
- [response-letter:ITS-01] Full letter generation flow
- [response-letter:ITS-02] Letter with custom parameters
- [response-letter:ITS-03] Letter generation failure handling
- [response-letter:E2E-01] Generate and retrieve objection letter
- [response-letter:E2E-02] Generate conditional support letter with overrides
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import fakeredis.aioredis
import pytest

from src.shared.redis_client import RedisClient
from src.worker.letter_jobs import letter_job
from src.worker.letter_prompt import build_letter_prompt

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def redis_client(fake_redis: fakeredis.aioredis.FakeRedis) -> RedisClient:
    """Create a RedisClient with fake Redis backend."""
    client = RedisClient()
    client._client = fake_redis
    return client


@pytest.fixture
def sample_review_result() -> dict:
    """A realistic completed review result stored in Redis."""
    return {
        "review_id": "rev_test_integration",
        "application_ref": "25/01178/REM",
        "status": "completed",
        "application": {
            "reference": "25/01178/REM",
            "address": "Land North of Railway, Bicester",
            "proposal": "Reserved matters for 150 dwellings with associated infrastructure",
            "applicant": "Test Developments Ltd",
            "case_officer": "Ms J. Smith",
            "status": "Under consideration",
            "date_validated": "2025-01-20",
            "documents_fetched": 5,
        },
        "review": {
            "overall_rating": "amber",
            "full_markdown": (
                "# Cycle Advocacy Review: 25/01178/REM\n\n"
                "## Application Summary\n"
                "- **Reference:** 25/01178/REM\n"
                "- **Site:** Land North of Railway, Bicester\n"
                "- **Proposal:** Reserved matters for 150 dwellings\n\n"
                "## Assessment Summary\n"
                "**Overall Rating:** AMBER\n\n"
                "| Aspect | Rating | Key Issue |\n"
                "|--------|--------|-----------|\n"
                "| Cycle Parking | AMBER | Below LTN 1/20 standard |\n"
                "| Cycle Routes | RED | Shared-use path too narrow |\n\n"
                "## Detailed Assessment\n\n"
                "### 1. Cycle Parking\n"
                "The application proposes 1 cycle space per dwelling (150 spaces total). "
                "LTN 1/20, Section 11.1 requires a minimum of 2 long-stay spaces per dwelling "
                "for residential developments. This represents a 50% shortfall.\n\n"
                "### 2. Cycle Route Provision\n"
                "The proposed shared-use path along the northern boundary is 2.5m wide. "
                "LTN 1/20, Table 5-2 specifies that segregated provision is required for "
                "roads with traffic flows above 2,500 vehicles/day. The Transport Assessment "
                "projects 3,200 vehicles/day. A minimum 3.0m segregated cycle track is required "
                "under paragraph 112 of the NPPF (December 2024).\n\n"
                "## Policy Compliance Matrix\n\n"
                "| Requirement | Policy Source | Compliant? |\n"
                "|---|---|---|\n"
                "| Cycle parking quantity | LTN 1/20 s.11.1 | No |\n"
                "| Segregated cycle track | LTN 1/20 Table 5-2 | No |\n"
                "| Sustainable transport | NPPF para 112 | Partial |\n\n"
                "## Recommendations\n"
                "1. Increase cycle parking to 2 spaces per dwelling (300 total)\n"
                "2. Provide 3.0m segregated cycle track along northern boundary\n"
                "3. Add filtered permeability links to adjacent residential areas\n\n"
                "## Suggested Conditions\n"
                "1. Prior to occupation, cycle parking shall be provided at 2 spaces per dwelling\n"
                "2. The cycle track shall be constructed to a minimum width of 3.0m\n"
            ),
        },
        "metadata": {
            "model": "claude-sonnet-4-5-20250929",
            "total_tokens_used": 5000,
            "evidence_chunks_used": 15,
        },
    }


SAMPLE_LETTER_MARKDOWN = """# Bicester BUG

7 February 2026

The Case Officer
Cherwell District Council
Planning Department

**Dear Ms J. Smith**

**Re: Planning Application 25/01178/REM — Land North of Railway, Bicester**

## Our Position

Bicester BUG wishes to **object** to this planning application on the grounds that the proposed development fails to provide adequate cycling infrastructure as required by national and local planning policy.

## Cycle Parking

The application proposes just 1 cycle space per dwelling (150 spaces total), representing a significant shortfall against the minimum standard set by LTN 1/20, Section 11.1, which requires 2 long-stay spaces per dwelling for residential developments. This 50% deficit is unacceptable and contrary to the Government's objective of promoting sustainable transport (NPPF, paragraph 112).

## Cycle Route Provision

The proposed shared-use path along the northern boundary, at only 2.5m wide, falls below the design standards in LTN 1/20, Table 5-2. Given that the Transport Assessment itself projects traffic flows of 3,200 vehicles per day, segregated provision is required. A minimum 3.0m segregated cycle track must be provided in accordance with both LTN 1/20 and paragraph 112 of the NPPF (December 2024).

## Recommendations

Bicester BUG requests the following improvements:

1. Cycle parking must be increased to a minimum of 2 spaces per dwelling (300 total)
2. A segregated cycle track of at least 3.0m width must replace the proposed shared-use path
3. Filtered permeability links should be provided to adjacent residential areas

## Closing

For the reasons set out above, Bicester BUG requests that this application be refused unless the above matters are addressed. We would welcome the opportunity to discuss these concerns further.

Yours sincerely,

On behalf of Bicester Bike Users' Group (BBUG)

## References

- Department for Transport, *Local Transport Note 1/20: Cycle Infrastructure Design*, July 2020
- Ministry of Housing, Communities and Local Government, *National Planning Policy Framework*, December 2024
"""


@pytest.fixture
def mock_anthropic_response():
    """Create a mock Anthropic API response with realistic letter content."""
    message = MagicMock()
    message.content = [MagicMock(text=SAMPLE_LETTER_MARKDOWN)]
    message.usage = MagicMock(input_tokens=4200, output_tokens=1800)
    return message


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------


class TestFullLetterGenerationFlow:
    """Integration tests for the complete letter generation flow."""

    @pytest.mark.asyncio
    async def test_full_flow_object_stance(
        self,
        redis_client: RedisClient,
        sample_review_result: dict,
        mock_anthropic_response,
    ) -> None:
        """
        Verifies [response-letter:ITS-01]

        Given: A completed review with result in Redis
        When: POST /reviews/{id}/letter with stance=object, then letter_job runs,
              then GET /letters/{id}
        Then: Letter content is Markdown containing group name, case officer,
              policy refs, and sign-off
        """
        # Store the review result in Redis
        review_id = sample_review_result["review_id"]
        await redis_client.store_result(review_id, sample_review_result, ttl_days=1)

        # Create initial letter record (simulates what the router does)
        letter_id = "ltr_integration_test_01"
        now = datetime.now(UTC)
        letter_record = {
            "letter_id": letter_id,
            "review_id": review_id,
            "application_ref": "25/01178/REM",
            "stance": "object",
            "tone": "formal",
            "case_officer": None,
            "letter_date": None,
            "status": "generating",
            "content": None,
            "metadata": None,
            "error": None,
            "created_at": now.isoformat(),
            "completed_at": None,
        }
        await redis_client.store_letter(letter_id, letter_record)

        # Run the letter job with mocked Claude
        ctx = {"redis_client": redis_client}

        with patch("src.worker.letter_jobs.anthropic") as mock_anthropic_mod:
            mock_client = MagicMock()
            mock_anthropic_mod.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = mock_anthropic_response

            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                result = await letter_job(ctx, letter_id, review_id)

        # Verify job completed
        assert result["status"] == "completed"
        assert result["metadata"]["input_tokens"] == 4200
        assert result["metadata"]["output_tokens"] == 1800

        # Retrieve the letter from Redis (simulates GET /letters/{id})
        stored_letter = await redis_client.get_letter(letter_id)
        assert stored_letter is not None
        assert stored_letter["status"] == "completed"
        assert stored_letter["content"] is not None

        # Validate letter content contains required elements
        content = stored_letter["content"]
        assert "Bicester BUG" in content
        assert "Ms J. Smith" in content
        assert "25/01178/REM" in content
        assert "object" in content.lower()
        assert "LTN 1/20" in content
        assert "NPPF" in content
        assert "Bicester Bike Users' Group" in content
        assert "References" in content

    @pytest.mark.asyncio
    async def test_custom_parameters(
        self,
        redis_client: RedisClient,
        sample_review_result: dict,
        mock_anthropic_response,
    ) -> None:
        """
        Verifies [response-letter:ITS-02]

        Given: A completed review exists
        When: POST with stance=conditional, tone=accessible, case_officer="Ms Smith",
              letter_date=2026-03-01, then job runs
        Then: Letter prompt uses the custom parameters
        """
        review_id = sample_review_result["review_id"]
        await redis_client.store_result(review_id, sample_review_result, ttl_days=1)

        letter_id = "ltr_integration_test_02"
        now = datetime.now(UTC)
        letter_record = {
            "letter_id": letter_id,
            "review_id": review_id,
            "application_ref": "25/01178/REM",
            "stance": "conditional",
            "tone": "accessible",
            "case_officer": "Mr A. Jones",
            "letter_date": "2026-03-01",
            "status": "generating",
            "content": None,
            "metadata": None,
            "error": None,
            "created_at": now.isoformat(),
            "completed_at": None,
        }
        await redis_client.store_letter(letter_id, letter_record)

        ctx = {"redis_client": redis_client}

        with patch("src.worker.letter_jobs.anthropic") as mock_anthropic_mod:
            mock_client = MagicMock()
            mock_anthropic_mod.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = mock_anthropic_response

            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                result = await letter_job(ctx, letter_id, review_id)

        assert result["status"] == "completed"

        # Verify the prompt sent to Claude used custom parameters
        call_kwargs = mock_client.messages.create.call_args.kwargs
        system_prompt = call_kwargs["system"]
        user_prompt = call_kwargs["messages"][0]["content"]

        # Conditional stance framing
        assert "SUBJECT TO CONDITIONS" in system_prompt
        # Accessible tone
        assert "jargon-light" in system_prompt.lower()
        # Custom case officer
        assert "Dear Mr A. Jones" in user_prompt
        # Custom letter date
        assert "1 March 2026" in user_prompt
        # Yours sincerely (named officer)
        assert "Yours sincerely" in user_prompt

    @pytest.mark.asyncio
    async def test_failure_handling(
        self,
        redis_client: RedisClient,
        sample_review_result: dict,
    ) -> None:
        """
        Verifies [response-letter:ITS-03]

        Given: A completed review exists but Claude API is mocked to fail
        When: POST, then job runs
        Then: GET returns status=failed with letter_generation_failed error
        """
        review_id = sample_review_result["review_id"]
        await redis_client.store_result(review_id, sample_review_result, ttl_days=1)

        letter_id = "ltr_integration_test_03"
        now = datetime.now(UTC)
        letter_record = {
            "letter_id": letter_id,
            "review_id": review_id,
            "application_ref": "25/01178/REM",
            "stance": "object",
            "tone": "formal",
            "case_officer": None,
            "letter_date": None,
            "status": "generating",
            "content": None,
            "metadata": None,
            "error": None,
            "created_at": now.isoformat(),
            "completed_at": None,
        }
        await redis_client.store_letter(letter_id, letter_record)

        ctx = {"redis_client": redis_client}

        with patch("src.worker.letter_jobs.anthropic") as mock_anthropic_mod:
            mock_client = MagicMock()
            mock_anthropic_mod.Anthropic.return_value = mock_client
            mock_anthropic_mod.APIError = Exception
            mock_client.messages.create.side_effect = Exception("Service overloaded")

            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                result = await letter_job(ctx, letter_id, review_id)

        assert result["status"] == "failed"
        assert result["error"] == "letter_generation_failed"

        # Verify Redis was updated with failure
        stored_letter = await redis_client.get_letter(letter_id)
        assert stored_letter["status"] == "failed"
        assert stored_letter["error"]["code"] == "letter_generation_failed"
        assert stored_letter["completed_at"] is not None


# ---------------------------------------------------------------------------
# E2E Tests
# ---------------------------------------------------------------------------


class TestE2EObjectionLetter:
    """E2E test for generating and retrieving an objection letter."""

    @pytest.mark.asyncio
    async def test_generate_and_retrieve_objection_letter(
        self,
        redis_client: RedisClient,
        sample_review_result: dict,
        mock_anthropic_response,
    ) -> None:
        """
        Verifies [response-letter:E2E-01]

        Given: A completed review for application 25/01178/REM
        When: 1. POST /reviews/{id}/letter {stance: "object"}
              2. letter_job runs
              3. GET /letters/{id}
        Then: Markdown letter contains: "Bicester BUG" header,
              "Dear" salutation, application reference in subject,
              objection framing, policy citations, recommendations,
              sign-off, references section
        """
        review_id = sample_review_result["review_id"]
        await redis_client.store_result(review_id, sample_review_result, ttl_days=1)

        # Step 1: Simulate POST (create letter record and enqueue)
        letter_id = "ltr_e2e_test_01"
        now = datetime.now(UTC)
        letter_record = {
            "letter_id": letter_id,
            "review_id": review_id,
            "application_ref": "25/01178/REM",
            "stance": "object",
            "tone": "formal",
            "case_officer": None,
            "letter_date": None,
            "status": "generating",
            "content": None,
            "metadata": None,
            "error": None,
            "created_at": now.isoformat(),
            "completed_at": None,
        }
        await redis_client.store_letter(letter_id, letter_record)

        # Step 2: Run the letter job
        ctx = {"redis_client": redis_client}

        with patch("src.worker.letter_jobs.anthropic") as mock_anthropic_mod:
            mock_client = MagicMock()
            mock_anthropic_mod.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = mock_anthropic_response

            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                result = await letter_job(ctx, letter_id, review_id)

        assert result["status"] == "completed"

        # Step 3: Retrieve the letter (simulates GET)
        letter = await redis_client.get_letter(letter_id)
        assert letter is not None
        assert letter["status"] == "completed"

        content = letter["content"]

        # Validate required sections from E2E-01
        assert "Bicester BUG" in content  # Group header
        assert "Dear" in content  # Salutation
        assert "25/01178/REM" in content  # Application reference
        assert "object" in content.lower()  # Objection framing
        assert "LTN 1/20" in content  # Policy citations
        assert "NPPF" in content  # Policy citations
        assert "Bicester Bike Users' Group" in content  # Sign-off
        assert "BBUG" in content  # Abbreviation
        assert "References" in content  # References section

        # Verify metadata
        assert letter["metadata"] is not None
        assert letter["metadata"]["input_tokens"] == 4200
        assert letter["metadata"]["output_tokens"] == 1800
        assert letter["metadata"]["processing_time_seconds"] >= 0

        # Verify the system prompt sent to Claude had objection framing
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert "OBJECTS" in call_kwargs["system"]
        assert "professional planning language" in call_kwargs["system"].lower()


class TestE2EConditionalLetterWithOverrides:
    """E2E test for conditional support letter with all overrides."""

    @pytest.mark.asyncio
    async def test_conditional_letter_with_overrides(
        self,
        redis_client: RedisClient,
        sample_review_result: dict,
        mock_anthropic_response,
    ) -> None:
        """
        Verifies [response-letter:E2E-02]

        Given: A completed review
        When: 1. POST /reviews/{id}/letter {stance: "conditional", tone: "accessible",
                 case_officer: "Mr A. Jones", letter_date: "2026-03-15"}
              2. letter_job runs
              3. GET /letters/{id}
        Then: Letter dated "15 March 2026", addressed "Dear Mr A. Jones",
              accessible language, conditional support framing
        """
        review_id = sample_review_result["review_id"]
        await redis_client.store_result(review_id, sample_review_result, ttl_days=1)

        # Step 1: Simulate POST with all overrides
        letter_id = "ltr_e2e_test_02"
        now = datetime.now(UTC)
        letter_record = {
            "letter_id": letter_id,
            "review_id": review_id,
            "application_ref": "25/01178/REM",
            "stance": "conditional",
            "tone": "accessible",
            "case_officer": "Mr A. Jones",
            "letter_date": "2026-03-15",
            "status": "generating",
            "content": None,
            "metadata": None,
            "error": None,
            "created_at": now.isoformat(),
            "completed_at": None,
        }
        await redis_client.store_letter(letter_id, letter_record)

        # Step 2: Run the letter job
        ctx = {"redis_client": redis_client}

        with patch("src.worker.letter_jobs.anthropic") as mock_anthropic_mod:
            mock_client = MagicMock()
            mock_anthropic_mod.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = mock_anthropic_response

            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                result = await letter_job(ctx, letter_id, review_id)

        assert result["status"] == "completed"

        # Step 3: Verify the prompts sent to Claude
        call_kwargs = mock_client.messages.create.call_args.kwargs
        system_prompt = call_kwargs["system"]
        user_prompt = call_kwargs["messages"][0]["content"]

        # Conditional stance framing
        assert "SUBJECT TO CONDITIONS" in system_prompt
        assert "Suggested Conditions" in system_prompt

        # Accessible tone
        assert "jargon-light" in system_prompt.lower()
        assert "accessible" in system_prompt.lower()

        # Custom case officer
        assert "Dear Mr A. Jones" in user_prompt
        assert "Yours sincerely" in user_prompt

        # Custom date
        assert "15 March 2026" in user_prompt

        # Step 3b: Verify letter stored correctly
        letter = await redis_client.get_letter(letter_id)
        assert letter["status"] == "completed"
        assert letter["content"] is not None


class TestPromptBuilderIntegration:
    """Integration tests for the prompt builder with realistic review data."""

    def test_all_stances_produce_valid_prompts(
        self, sample_review_result: dict
    ) -> None:
        """All four stances produce valid system+user prompt pairs."""
        for stance in ["object", "support", "conditional", "neutral"]:
            system, user = build_letter_prompt(
                review_result=sample_review_result,
                stance=stance,
                tone="formal",
                group_name="Bicester Bike Users' Group",
                group_stylised="Bicester BUG",
                group_short="BBUG",
            )
            assert len(system) > 500, f"System prompt too short for stance={stance}"
            assert len(user) > 200, f"User prompt too short for stance={stance}"
            assert "Bicester BUG" in system
            assert "25/01178/REM" in user

    def test_both_tones_produce_different_instructions(
        self, sample_review_result: dict
    ) -> None:
        """Formal and accessible tones produce different style instructions."""
        formal_system, _ = build_letter_prompt(
            review_result=sample_review_result,
            stance="object",
            tone="formal",
            group_name="Bicester Bike Users' Group",
            group_stylised="Bicester BUG",
            group_short="BBUG",
        )
        accessible_system, _ = build_letter_prompt(
            review_result=sample_review_result,
            stance="object",
            tone="accessible",
            group_name="Bicester Bike Users' Group",
            group_stylised="Bicester BUG",
            group_short="BBUG",
        )

        assert "professional planning language" in formal_system.lower()
        assert "jargon-light" in accessible_system.lower()
        assert formal_system != accessible_system
