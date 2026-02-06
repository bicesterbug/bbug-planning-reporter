"""
Webhook URL validator.

Implements [api-hardening:FR-010] - HTTPS webhook enforcement in production

Implements test scenarios:
- [api-hardening:WebhookURLValidator/TS-01] HTTPS URL accepted
- [api-hardening:WebhookURLValidator/TS-02] HTTP URL rejected in prod
- [api-hardening:WebhookURLValidator/TS-03] HTTP URL allowed in dev
- [api-hardening:WebhookURLValidator/TS-04] Malformed URL rejected
- [api-hardening:WebhookURLValidator/TS-05] Empty URL allowed
"""

import os
from urllib.parse import urlparse

import structlog

logger = structlog.get_logger(__name__)


class WebhookValidationError(ValueError):
    """Raised when webhook URL validation fails."""

    def __init__(self, message: str, code: str = "invalid_webhook_url"):
        super().__init__(message)
        self.code = code
        self.message = message


def is_production_mode() -> bool:
    """Check if running in production mode."""
    env = os.getenv("ENVIRONMENT", "development").lower()
    return env in ("production", "prod")


def validate_webhook_url(url: str | None) -> str | None:
    """
    Validate a webhook URL.

    In production mode, only HTTPS URLs are allowed.
    In development mode, HTTP is allowed for local testing.

    Args:
        url: The webhook URL to validate, or None.

    Returns:
        The validated URL, or None if empty.

    Raises:
        WebhookValidationError: If the URL is invalid or HTTP in production.
    """
    # Empty/None is allowed (webhook is optional)
    if not url or not url.strip():
        return None

    url = url.strip()

    # Parse and validate URL structure
    try:
        parsed = urlparse(url)
    except Exception as e:
        logger.warning("Malformed webhook URL", url=url, error=str(e))
        raise WebhookValidationError(
            f"Malformed webhook URL: {url}",
            code="invalid_webhook_url",
        )

    # Check for valid scheme
    if not parsed.scheme:
        raise WebhookValidationError(
            "Webhook URL must include a scheme (http:// or https://)",
            code="invalid_webhook_url",
        )

    if parsed.scheme not in ("http", "https"):
        raise WebhookValidationError(
            f"Webhook URL must use http or https, not '{parsed.scheme}'",
            code="invalid_webhook_url",
        )

    # Check for valid host
    if not parsed.netloc:
        raise WebhookValidationError(
            "Webhook URL must include a host",
            code="invalid_webhook_url",
        )

    # In production, require HTTPS
    if is_production_mode() and parsed.scheme != "https":
        logger.warning(
            "HTTP webhook URL rejected in production",
            url=url,
        )
        raise WebhookValidationError(
            "Webhook URL must use HTTPS in production mode",
            code="invalid_webhook_url",
        )

    return url
