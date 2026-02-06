"""
Tests for WebhookURLValidator.

Verifies [api-hardening:FR-010] - HTTPS webhook enforcement in production
"""

from unittest.mock import patch

import pytest

from src.api.validators.webhook import WebhookValidationError, validate_webhook_url


class TestHTTPSAccepted:
    """
    Tests for HTTPS URL acceptance.

    Verifies [api-hardening:WebhookURLValidator/TS-01] - HTTPS URL accepted
    """

    def test_https_accepted_in_production(self):
        """
        Verifies [api-hardening:WebhookURLValidator/TS-01] - HTTPS URL accepted

        Given: Production mode
        When: Validate "https://example.com/hook"
        Then: Passes validation
        """
        with patch.dict("os.environ", {"ENVIRONMENT": "production"}):
            result = validate_webhook_url("https://example.com/hook")
            assert result == "https://example.com/hook"

    def test_https_accepted_in_development(self):
        """HTTPS is accepted in development mode too."""
        with patch.dict("os.environ", {"ENVIRONMENT": "development"}):
            result = validate_webhook_url("https://example.com/hook")
            assert result == "https://example.com/hook"


class TestHTTPRejectedInProduction:
    """
    Tests for HTTP rejection in production.

    Verifies [api-hardening:WebhookURLValidator/TS-02] - HTTP URL rejected in prod
    """

    def test_http_rejected_in_production(self):
        """
        Verifies [api-hardening:WebhookURLValidator/TS-02] - HTTP URL rejected in prod

        Given: Production mode
        When: Validate "http://example.com/hook"
        Then: Returns 422 with error code "invalid_webhook_url"
        """
        with patch.dict("os.environ", {"ENVIRONMENT": "production"}):
            with pytest.raises(WebhookValidationError) as exc_info:
                validate_webhook_url("http://example.com/hook")

            assert exc_info.value.code == "invalid_webhook_url"
            assert "HTTPS" in str(exc_info.value)

    def test_http_rejected_with_prod_env(self):
        """HTTP rejected when ENVIRONMENT=prod."""
        with patch.dict("os.environ", {"ENVIRONMENT": "prod"}), pytest.raises(
            WebhookValidationError
        ):
            validate_webhook_url("http://example.com/hook")


class TestHTTPAllowedInDevelopment:
    """
    Tests for HTTP allowed in development.

    Verifies [api-hardening:WebhookURLValidator/TS-03] - HTTP URL allowed in dev
    """

    def test_http_allowed_in_development(self):
        """
        Verifies [api-hardening:WebhookURLValidator/TS-03] - HTTP URL allowed in dev

        Given: Development mode
        When: Validate "http://localhost:8000/hook"
        Then: Passes validation
        """
        with patch.dict("os.environ", {"ENVIRONMENT": "development"}):
            result = validate_webhook_url("http://localhost:8000/hook")
            assert result == "http://localhost:8000/hook"

    def test_http_allowed_with_no_environment_set(self):
        """HTTP allowed when ENVIRONMENT not set (defaults to development)."""
        with patch.dict("os.environ", {}, clear=True):
            import os

            os.environ.pop("ENVIRONMENT", None)
            result = validate_webhook_url("http://localhost:8000/hook")
            assert result == "http://localhost:8000/hook"


class TestMalformedURL:
    """
    Tests for malformed URL rejection.

    Verifies [api-hardening:WebhookURLValidator/TS-04] - Malformed URL rejected
    """

    def test_malformed_url_rejected(self):
        """
        Verifies [api-hardening:WebhookURLValidator/TS-04] - Malformed URL rejected

        Given: Any mode
        When: Validate "not-a-url"
        Then: Returns 422 with error code "invalid_webhook_url"
        """
        with pytest.raises(WebhookValidationError) as exc_info:
            validate_webhook_url("not-a-url")

        assert exc_info.value.code == "invalid_webhook_url"

    def test_missing_scheme_rejected(self):
        """URL without scheme is rejected."""
        with pytest.raises(WebhookValidationError):
            validate_webhook_url("example.com/hook")

    def test_missing_host_rejected(self):
        """URL without host is rejected."""
        with pytest.raises(WebhookValidationError):
            validate_webhook_url("https:///path")

    def test_invalid_scheme_rejected(self):
        """URL with invalid scheme is rejected."""
        with pytest.raises(WebhookValidationError) as exc_info:
            validate_webhook_url("ftp://example.com/hook")
        assert "http or https" in str(exc_info.value)


class TestEmptyURL:
    """
    Tests for empty URL handling.

    Verifies [api-hardening:WebhookURLValidator/TS-05] - Empty URL allowed
    """

    def test_none_allowed(self):
        """
        Verifies [api-hardening:WebhookURLValidator/TS-05] - Empty URL allowed

        Given: Webhook optional
        When: Validate with no webhook
        Then: Passes validation
        """
        result = validate_webhook_url(None)
        assert result is None

    def test_empty_string_allowed(self):
        """Empty string returns None."""
        result = validate_webhook_url("")
        assert result is None

    def test_whitespace_only_allowed(self):
        """Whitespace-only string returns None."""
        result = validate_webhook_url("   ")
        assert result is None


class TestURLNormalization:
    """Tests for URL handling edge cases."""

    def test_whitespace_trimmed(self):
        """Leading/trailing whitespace is trimmed."""
        with patch.dict("os.environ", {"ENVIRONMENT": "development"}):
            result = validate_webhook_url("  https://example.com/hook  ")
            assert result == "https://example.com/hook"

    def test_url_with_port_accepted(self):
        """URL with port is accepted."""
        with patch.dict("os.environ", {"ENVIRONMENT": "development"}):
            result = validate_webhook_url("http://localhost:3000/webhooks")
            assert result == "http://localhost:3000/webhooks"

    def test_url_with_path_and_query_accepted(self):
        """URL with path and query parameters is accepted."""
        with patch.dict("os.environ", {"ENVIRONMENT": "development"}):
            result = validate_webhook_url("https://api.example.com/v1/hooks?key=abc")
            assert result == "https://api.example.com/v1/hooks?key=abc"
