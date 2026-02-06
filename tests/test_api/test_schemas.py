"""
Tests for API schemas.

Implements test scenarios from [foundation-api:ReviewRequestModels/TS-01] through [TS-04]
"""

import pytest
from pydantic import ValidationError

from src.api.schemas import ReviewRequest, WebhookConfigRequest


class TestApplicationReferenceValidation:
    """Tests for application reference validation."""

    @pytest.mark.parametrize(
        "ref",
        [
            "25/01178/REM",
            "08/00707/F",
            "23/01421/TCA",
            "24/12345/OUT",
            "99/0001/F",
        ],
    )
    def test_valid_reference_patterns(self, ref: str) -> None:
        """
        Verifies [foundation-api:ReviewRequestModels/TS-01]

        Given: Various valid refs
        When: Validate against pattern
        Then: All pass
        """
        request = ReviewRequest(application_ref=ref)
        assert request.application_ref == ref

    @pytest.mark.parametrize(
        "ref",
        [
            "INVALID",
            "25-01178-REM",
            "25/178/REM",  # Only 3 digits (minimum is 4)
            "2025/01178/REM",  # 4-digit year
            "25/01178/TOOLONG",  # Type code too long
            "25/01178/rem",  # Lowercase
            "",
            "25//REM",
            "/01178/REM",
        ],
    )
    def test_invalid_reference_patterns(self, ref: str) -> None:
        """
        Verifies [foundation-api:ReviewRequestModels/TS-02]

        Given: Various invalid refs
        When: Validate against pattern
        Then: All fail
        """
        with pytest.raises(ValidationError) as exc_info:
            ReviewRequest(application_ref=ref)

        # Check that the error is about the application_ref
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("application_ref",) for e in errors)


class TestWebhookConfigValidation:
    """Tests for webhook configuration validation."""

    def test_valid_https_url(self) -> None:
        """
        Verifies [foundation-api:ReviewRequestModels/TS-03]

        Given: HTTPS URL
        When: Create webhook config
        Then: Passes validation
        """
        config = WebhookConfigRequest(
            url="https://example.com/hooks/cherwell",
            secret="test_secret",
        )
        assert config.url == "https://example.com/hooks/cherwell"

    def test_valid_http_url(self) -> None:
        """
        Given: HTTP URL (allowed for local dev)
        When: Create webhook config
        Then: Passes validation
        """
        config = WebhookConfigRequest(
            url="http://localhost:8080/hooks",
            secret="test_secret",
        )
        assert config.url == "http://localhost:8080/hooks"

    def test_invalid_url_scheme(self) -> None:
        """
        Given: Invalid URL scheme
        When: Create webhook config
        Then: Fails validation
        """
        with pytest.raises(ValidationError) as exc_info:
            WebhookConfigRequest(
                url="ftp://example.com/hooks",
                secret="test_secret",
            )

        errors = exc_info.value.errors()
        assert any("url" in str(e["loc"]) for e in errors)

    def test_valid_events(self) -> None:
        """
        Given: Valid event names
        When: Create webhook config
        Then: Passes validation
        """
        config = WebhookConfigRequest(
            url="https://example.com/hooks",
            secret="test_secret",
            events=["review.started", "review.completed"],
        )
        assert len(config.events) == 2

    def test_invalid_event_name(self) -> None:
        """
        Given: Invalid event name
        When: Create webhook config
        Then: Fails validation
        """
        with pytest.raises(ValidationError):
            WebhookConfigRequest(
                url="https://example.com/hooks",
                secret="test_secret",
                events=["review.invalid_event"],
            )


class TestReviewRequestOptionalFields:
    """Tests for optional fields handling."""

    def test_minimal_request(self) -> None:
        """
        Verifies [foundation-api:ReviewRequestModels/TS-04]

        Given: Minimal request with only required fields
        When: Parse request body
        Then: Defaults applied, optional fields None
        """
        request = ReviewRequest(application_ref="25/01178/REM")

        assert request.application_ref == "25/01178/REM"
        assert request.options is None
        assert request.webhook is None

    def test_full_request(self) -> None:
        """
        Given: Request with all fields
        When: Parse request body
        Then: All fields populated
        """
        request = ReviewRequest(
            application_ref="25/01178/REM",
            options={
                "focus_areas": ["cycle_parking", "cycle_routes"],
                "output_format": "markdown",
            },
            webhook={
                "url": "https://example.com/hooks",
                "secret": "test_secret",
                "events": ["review.completed"],
            },
        )

        assert request.application_ref == "25/01178/REM"
        assert request.options is not None
        assert request.options.focus_areas == ["cycle_parking", "cycle_routes"]
        assert request.webhook is not None
        assert request.webhook.url == "https://example.com/hooks"
