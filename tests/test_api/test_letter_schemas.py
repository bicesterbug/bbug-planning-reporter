"""
Tests for letter API schemas.

Implements test scenarios from [response-letter:LetterSchemas/TS-01] through [TS-05]
"""

from datetime import date

import pytest
from pydantic import ValidationError

from src.api.schemas.letter import (
    LetterRequest,
    LetterStance,
    LetterStatus,
    LetterTone,
)


class TestLetterRequest:
    """Tests for LetterRequest validation."""

    def test_valid_request_all_fields(self) -> None:
        """
        Verifies [response-letter:LetterSchemas/TS-01]

        Given: A request with stance=object, tone=formal, case_officer, and letter_date
        When: Request is validated
        Then: All fields are parsed correctly
        """
        request = LetterRequest(
            stance="object",
            tone="formal",
            case_officer="Ms J. Smith",
            letter_date="2026-02-10",
        )
        assert request.stance == LetterStance.OBJECT
        assert request.tone == LetterTone.FORMAL
        assert request.case_officer == "Ms J. Smith"
        assert request.letter_date == date(2026, 2, 10)

    def test_minimal_request_stance_only(self) -> None:
        """
        Verifies [response-letter:LetterSchemas/TS-02]

        Given: A request with only stance=neutral
        When: Request is validated
        Then: Defaults applied: tone=formal, case_officer=None, letter_date=None
        """
        request = LetterRequest(stance="neutral")
        assert request.stance == LetterStance.NEUTRAL
        assert request.tone == LetterTone.FORMAL
        assert request.case_officer is None
        assert request.letter_date is None

    def test_invalid_stance_rejected(self) -> None:
        """
        Verifies [response-letter:LetterSchemas/TS-03]

        Given: A request with stance=invalid
        When: Request is validated
        Then: Pydantic raises validation error
        """
        with pytest.raises(ValidationError) as exc_info:
            LetterRequest(stance="invalid")
        errors = exc_info.value.errors()
        assert any("stance" in str(e.get("loc", "")) for e in errors)

    def test_invalid_date_rejected(self) -> None:
        """
        Verifies [response-letter:LetterSchemas/TS-04]

        Given: A request with letter_date=not-a-date
        When: Request is validated
        Then: Pydantic raises validation error
        """
        with pytest.raises(ValidationError) as exc_info:
            LetterRequest(stance="object", letter_date="not-a-date")
        errors = exc_info.value.errors()
        assert any("letter_date" in str(e.get("loc", "")) for e in errors)

    def test_invalid_tone_rejected(self) -> None:
        """
        Verifies [response-letter:LetterSchemas/TS-05]

        Given: A request with tone=casual
        When: Request is validated
        Then: Pydantic raises validation error
        """
        with pytest.raises(ValidationError) as exc_info:
            LetterRequest(stance="object", tone="casual")
        errors = exc_info.value.errors()
        assert any("tone" in str(e.get("loc", "")) for e in errors)

    @pytest.mark.parametrize("stance", ["object", "support", "conditional", "neutral"])
    def test_all_stances_valid(self, stance: str) -> None:
        """All four stance values are accepted."""
        request = LetterRequest(stance=stance)
        assert request.stance == stance

    @pytest.mark.parametrize("tone", ["formal", "accessible"])
    def test_all_tones_valid(self, tone: str) -> None:
        """Both tone values are accepted."""
        request = LetterRequest(stance="object", tone=tone)
        assert request.tone == tone

    def test_stance_is_required(self) -> None:
        """Stance field is mandatory."""
        with pytest.raises(ValidationError) as exc_info:
            LetterRequest()
        errors = exc_info.value.errors()
        assert any("stance" in str(e.get("loc", "")) for e in errors)


class TestLetterEnums:
    """Tests for letter enum values."""

    def test_letter_stance_values(self) -> None:
        """All expected stance values exist."""
        assert set(LetterStance) == {"object", "support", "conditional", "neutral"}

    def test_letter_tone_values(self) -> None:
        """All expected tone values exist."""
        assert set(LetterTone) == {"formal", "accessible"}

    def test_letter_status_values(self) -> None:
        """All expected status values exist."""
        assert set(LetterStatus) == {"generating", "completed", "failed"}
