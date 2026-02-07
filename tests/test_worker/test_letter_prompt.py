"""
Tests for letter prompt builder.

Implements test scenarios from [response-letter:LetterPrompt/TS-01] through [TS-09]
"""

from datetime import date

import pytest

from src.worker.letter_prompt import build_letter_prompt


@pytest.fixture
def sample_review_result() -> dict:
    """Create a sample completed review result."""
    return {
        "review_id": "rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
        "application_ref": "25/01178/REM",
        "application": {
            "reference": "25/01178/REM",
            "address": "Land North of Railway, Bicester",
            "proposal": "Reserved matters for 150 dwellings",
            "applicant": "Test Developments Ltd",
        },
        "review": {
            "overall_rating": "amber",
            "full_markdown": (
                "# Cycle Advocacy Review: 25/01178/REM\n\n"
                "## Assessment Summary\n"
                "**Overall Rating:** AMBER\n\n"
                "## Detailed Assessment\n\n"
                "### Cycle Parking\n"
                "The application provides 1 cycle space per dwelling, falling short of "
                "the 2 spaces required by LTN 1/20, Section 11.1.\n\n"
                "### Cycle Routes\n"
                "The proposed shared-use path is 2.5m wide, below the 3m minimum "
                "required by LTN 1/20, Table 5-2 for a route with this traffic volume.\n\n"
                "## Recommendations\n"
                "1. Increase cycle parking to 2 spaces per dwelling\n"
                "2. Widen shared-use path to 3m minimum\n\n"
                "## Suggested Conditions\n"
                "1. Prior to occupation, cycle parking shall be provided at 2 spaces per dwelling\n"
            ),
        },
        "metadata": {
            "model": "claude-sonnet-4-5-20250929",
            "total_tokens_used": 5000,
        },
    }


@pytest.fixture
def default_group_args() -> dict:
    """Default group identity arguments."""
    return {
        "group_name": "Bicester Bike Users' Group",
        "group_stylised": "Bicester BUG",
        "group_short": "BBUG",
    }


@pytest.fixture
def sample_policy_revisions() -> list[dict]:
    """Sample policy revisions for bibliography."""
    return [
        {
            "title": "Local Transport Note 1/20: Cycle Infrastructure Design",
            "effective_from": "2020-07-27",
            "publisher": "Department for Transport",
        },
        {
            "title": "National Planning Policy Framework",
            "effective_from": "2024-12-01",
            "publisher": "Ministry of Housing, Communities and Local Government",
        },
    ]


class TestObjectStancePrompt:
    """Tests for object stance prompt."""

    def test_object_stance_system_prompt(
        self, sample_review_result: dict, default_group_args: dict
    ) -> None:
        """
        Verifies [response-letter:LetterPrompt/TS-01]

        Given: stance=object, group="Bicester BUG"
        When: System prompt is built
        Then: Prompt instructs LLM to frame letter as an objection
        """
        system, _ = build_letter_prompt(
            review_result=sample_review_result,
            stance="object",
            tone="formal",
            **default_group_args,
        )

        assert "OBJECTS" in system
        assert "objection" in system.lower()
        assert "refused" in system.lower()


class TestSupportStancePrompt:
    """Tests for support stance prompt."""

    def test_support_stance_system_prompt(
        self, sample_review_result: dict, default_group_args: dict
    ) -> None:
        """
        Verifies [response-letter:LetterPrompt/TS-02]

        Given: stance=support
        When: System prompt is built
        Then: Prompt instructs LLM to frame letter as support
        """
        system, _ = build_letter_prompt(
            review_result=sample_review_result,
            stance="support",
            tone="formal",
            **default_group_args,
        )

        assert "SUPPORTS" in system
        assert "support" in system.lower()
        assert "approved" in system.lower()


class TestConditionalStancePrompt:
    """Tests for conditional stance prompt."""

    def test_conditional_stance_system_prompt(
        self, sample_review_result: dict, default_group_args: dict
    ) -> None:
        """
        Verifies [response-letter:LetterPrompt/TS-03]

        Given: stance=conditional
        When: System prompt is built
        Then: Prompt instructs LLM to frame as support-with-conditions
            and include suggested conditions section
        """
        system, _ = build_letter_prompt(
            review_result=sample_review_result,
            stance="conditional",
            tone="formal",
            **default_group_args,
        )

        assert "SUBJECT TO CONDITIONS" in system
        assert "conditional" in system.lower()
        assert "Suggested Conditions" in system


class TestNeutralStancePrompt:
    """Tests for neutral stance prompt."""

    def test_neutral_stance_system_prompt(
        self, sample_review_result: dict, default_group_args: dict
    ) -> None:
        """
        Verifies [response-letter:LetterPrompt/TS-04]

        Given: stance=neutral
        When: System prompt is built
        Then: Prompt instructs LLM to provide factual comments without explicit position
        """
        system, _ = build_letter_prompt(
            review_result=sample_review_result,
            stance="neutral",
            tone="formal",
            **default_group_args,
        )

        assert "NEUTRAL COMMENTS" in system
        assert "factual" in system.lower()


class TestFormalTonePrompt:
    """Tests for formal tone."""

    def test_formal_tone(
        self, sample_review_result: dict, default_group_args: dict
    ) -> None:
        """
        Verifies [response-letter:LetterPrompt/TS-05]

        Given: tone=formal
        When: System prompt is built
        Then: Prompt includes formal technical language instructions
        """
        system, _ = build_letter_prompt(
            review_result=sample_review_result,
            stance="object",
            tone="formal",
            **default_group_args,
        )

        assert "professional planning language" in system.lower()
        assert "technical terminology" in system.lower()


class TestAccessibleTonePrompt:
    """Tests for accessible tone."""

    def test_accessible_tone(
        self, sample_review_result: dict, default_group_args: dict
    ) -> None:
        """
        Verifies [response-letter:LetterPrompt/TS-06]

        Given: tone=accessible
        When: System prompt is built
        Then: Prompt includes accessible, jargon-light language instructions
        """
        system, _ = build_letter_prompt(
            review_result=sample_review_result,
            stance="object",
            tone="accessible",
            **default_group_args,
        )

        assert "jargon-light" in system.lower()
        assert "accessible" in system.lower()


class TestBibliographyInstruction:
    """Tests for bibliography/references instructions."""

    def test_bibliography_instruction_with_policies(
        self,
        sample_review_result: dict,
        default_group_args: dict,
        sample_policy_revisions: list[dict],
    ) -> None:
        """
        Verifies [response-letter:LetterPrompt/TS-07]

        Given: Review has policy references
        When: User prompt is built
        Then: Prompt includes policy documents for bibliography
        """
        _, user = build_letter_prompt(
            review_result=sample_review_result,
            stance="object",
            tone="formal",
            policy_revisions=sample_policy_revisions,
            **default_group_args,
        )

        assert "Policy Documents Available" in user
        assert "Local Transport Note 1/20" in user
        assert "National Planning Policy Framework" in user
        assert "Department for Transport" in user

    def test_no_policies_no_bibliography_section(
        self, sample_review_result: dict, default_group_args: dict
    ) -> None:
        """When no policy revisions are provided, no policy section appears in user prompt."""
        _, user = build_letter_prompt(
            review_result=sample_review_result,
            stance="object",
            tone="formal",
            policy_revisions=None,
            **default_group_args,
        )

        assert "Policy Documents Available" not in user

    def test_references_section_in_system_prompt(
        self, sample_review_result: dict, default_group_args: dict
    ) -> None:
        """System prompt always includes references section requirement."""
        system, _ = build_letter_prompt(
            review_result=sample_review_result,
            stance="object",
            tone="formal",
            **default_group_args,
        )

        assert "References" in system
        assert "bibliography" in system.lower()


class TestLetterSections:
    """Tests for required letter sections."""

    def test_all_sections_specified(
        self, sample_review_result: dict, default_group_args: dict
    ) -> None:
        """
        Verifies [response-letter:LetterPrompt/TS-08]

        Given: Any stance/tone
        When: System prompt is built
        Then: Prompt lists all 10 required letter sections
        """
        system, _ = build_letter_prompt(
            review_result=sample_review_result,
            stance="object",
            tone="formal",
            **default_group_args,
        )

        # All 10 sections from FR-009
        assert "Sender header" in system
        assert "Recipient addressing" in system
        assert "Subject line" in system
        assert "Opening paragraph" in system
        assert "Body paragraphs" in system
        assert "Recommendations" in system
        assert "Suggested conditions" in system
        assert "Closing paragraph" in system
        assert "Sign-off" in system
        assert "References" in system


class TestGroupIdentity:
    """Tests for group identity injection."""

    def test_group_identity_in_system_prompt(
        self, sample_review_result: dict
    ) -> None:
        """
        Verifies [response-letter:LetterPrompt/TS-09]

        Given: Custom group env vars
        When: System prompt is built
        Then: Group name, stylised name, and abbreviation appear in the prompt
        """
        system, _ = build_letter_prompt(
            review_result=sample_review_result,
            stance="object",
            tone="formal",
            group_name="Oxford Cycling Network",
            group_stylised="OxCycleNet",
            group_short="OCN",
        )

        assert "Oxford Cycling Network" in system
        assert "OxCycleNet" in system
        assert "OCN" in system

    def test_group_identity_in_user_prompt(
        self, sample_review_result: dict, default_group_args: dict
    ) -> None:
        """Group stylised name and short appear in user prompt."""
        _, user = build_letter_prompt(
            review_result=sample_review_result,
            stance="object",
            tone="formal",
            **default_group_args,
        )

        assert "Bicester BUG" in user
        assert "BBUG" in user


class TestCaseOfficerAddressing:
    """Tests for case officer handling in user prompt."""

    def test_named_case_officer(
        self, sample_review_result: dict, default_group_args: dict
    ) -> None:
        """Named case officer produces 'Dear [name]' and 'Yours sincerely'."""
        _, user = build_letter_prompt(
            review_result=sample_review_result,
            stance="object",
            tone="formal",
            case_officer="Ms J. Smith",
            **default_group_args,
        )

        assert "Dear Ms J. Smith" in user
        assert "Yours sincerely" in user

    def test_no_case_officer_fallback(
        self, sample_review_result: dict, default_group_args: dict
    ) -> None:
        """No case officer produces 'Dear Sir/Madam' and 'Yours faithfully'."""
        _, user = build_letter_prompt(
            review_result=sample_review_result,
            stance="object",
            tone="formal",
            case_officer=None,
            **default_group_args,
        )

        assert "Dear Sir/Madam" in user
        assert "Yours faithfully" in user


class TestLetterDate:
    """Tests for letter date handling."""

    def test_custom_date(
        self, sample_review_result: dict, default_group_args: dict
    ) -> None:
        """Custom letter_date is formatted in user prompt."""
        _, user = build_letter_prompt(
            review_result=sample_review_result,
            stance="object",
            tone="formal",
            letter_date=date(2026, 3, 15),
            **default_group_args,
        )

        assert "15 March 2026" in user

    def test_default_date_is_today(
        self, sample_review_result: dict, default_group_args: dict
    ) -> None:
        """When no letter_date, today's date is used."""
        _, user = build_letter_prompt(
            review_result=sample_review_result,
            stance="object",
            tone="formal",
            letter_date=None,
            **default_group_args,
        )

        today = date.today()
        expected = today.strftime("%-d %B %Y")
        assert expected in user


class TestApplicationMetadata:
    """Tests for application metadata in user prompt."""

    def test_application_details_in_user_prompt(
        self, sample_review_result: dict, default_group_args: dict
    ) -> None:
        """Application ref, address, and proposal appear in user prompt."""
        _, user = build_letter_prompt(
            review_result=sample_review_result,
            stance="object",
            tone="formal",
            **default_group_args,
        )

        assert "25/01178/REM" in user
        assert "Land North of Railway, Bicester" in user
        assert "Reserved matters for 150 dwellings" in user

    def test_review_markdown_in_user_prompt(
        self, sample_review_result: dict, default_group_args: dict
    ) -> None:
        """Review markdown content is included in user prompt."""
        _, user = build_letter_prompt(
            review_result=sample_review_result,
            stance="object",
            tone="formal",
            **default_group_args,
        )

        assert "Cycle Advocacy Review" in user
        assert "LTN 1/20" in user

    def test_missing_application_handled(
        self, default_group_args: dict
    ) -> None:
        """Missing application data uses fallbacks."""
        result = {
            "review_id": "rev_test",
            "application_ref": "25/99999/FUL",
            "application": None,
            "review": {"full_markdown": "Some review content"},
        }

        _, user = build_letter_prompt(
            review_result=result,
            stance="object",
            tone="formal",
            **default_group_args,
        )

        # Should still produce a valid prompt with fallback values
        assert "Unknown" in user or "25/99999/FUL" in user
