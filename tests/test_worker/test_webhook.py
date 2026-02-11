"""Tests for webhook delivery module."""

import asyncio
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from src.shared.models import WebhookConfig
from src.worker.webhook import (
    _build_payload,
    _deliver_webhook,
    _sign_payload,
    fire_webhook,
)


class TestSignPayload:
    """Tests for HMAC-SHA256 payload signing."""

    def test_produces_correct_hmac(self):
        payload = b'{"event": "test"}'
        secret = "my-secret"
        result = _sign_payload(payload, secret)

        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        assert result == f"sha256={expected}"

    def test_different_secrets_produce_different_sigs(self):
        payload = b'{"event": "test"}'
        sig1 = _sign_payload(payload, "secret-a")
        sig2 = _sign_payload(payload, "secret-b")
        assert sig1 != sig2

    def test_different_payloads_produce_different_sigs(self):
        secret = "same-secret"
        sig1 = _sign_payload(b"payload-1", secret)
        sig2 = _sign_payload(b"payload-2", secret)
        assert sig1 != sig2


class TestBuildPayload:
    """Tests for webhook payload construction."""

    def test_payload_structure(self):
        payload = _build_payload("review.completed", "rev_123", {"key": "value"})

        assert payload["event"] == "review.completed"
        assert payload["review_id"] == "rev_123"
        assert payload["data"] == {"key": "value"}
        assert "delivery_id" in payload
        assert "timestamp" in payload

    def test_unique_delivery_ids(self):
        p1 = _build_payload("review.started", "rev_1", {})
        p2 = _build_payload("review.started", "rev_1", {})
        assert p1["delivery_id"] != p2["delivery_id"]


class TestDeliverWebhook:
    """Tests for HTTP delivery with retries."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_successful_delivery(self):
        route = respx.post("https://example.com/hook").mock(
            return_value=httpx.Response(200)
        )
        payload = json.dumps({"event": "test"}).encode()

        await _deliver_webhook(
            "https://example.com/hook", "secret", "review.started", "del-1", payload
        )

        assert route.called
        assert route.call_count == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_correct_headers(self):
        route = respx.post("https://example.com/hook").mock(
            return_value=httpx.Response(200)
        )
        payload = json.dumps({"event": "test"}).encode()

        await _deliver_webhook(
            "https://example.com/hook", "secret", "review.started", "del-1", payload
        )

        request = route.calls[0].request
        assert request.headers["X-Webhook-Event"] == "review.started"
        assert request.headers["X-Webhook-Delivery-Id"] == "del-1"
        assert "X-Webhook-Signature-256" in request.headers
        assert "X-Webhook-Timestamp" in request.headers
        assert request.headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    @respx.mock
    async def test_signature_matches_body(self):
        route = respx.post("https://example.com/hook").mock(
            return_value=httpx.Response(200)
        )
        payload = json.dumps({"event": "test"}).encode()
        secret = "test-secret"

        await _deliver_webhook(
            "https://example.com/hook", secret, "review.started", "del-1", payload
        )

        request = route.calls[0].request
        sig_header = request.headers["X-Webhook-Signature-256"]
        expected = _sign_payload(request.content, secret)
        assert sig_header == expected

    @pytest.mark.asyncio
    @respx.mock
    async def test_retries_on_500(self):
        route = respx.post("https://example.com/hook").mock(
            side_effect=[
                httpx.Response(500),
                httpx.Response(500),
                httpx.Response(200),
            ]
        )
        payload = json.dumps({"event": "test"}).encode()

        with patch("src.worker.webhook.asyncio.sleep", new_callable=AsyncMock):
            await _deliver_webhook(
                "https://example.com/hook", "secret", "review.started", "del-1", payload
            )

        assert route.call_count == 3

    @pytest.mark.asyncio
    @respx.mock
    async def test_retries_on_timeout(self):
        route = respx.post("https://example.com/hook").mock(
            side_effect=[
                httpx.TimeoutException("timeout"),
                httpx.Response(200),
            ]
        )
        payload = json.dumps({"event": "test"}).encode()

        with patch("src.worker.webhook.asyncio.sleep", new_callable=AsyncMock):
            await _deliver_webhook(
                "https://example.com/hook", "secret", "review.started", "del-1", payload
            )

        assert route.call_count == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_retries_on_connection_error(self):
        route = respx.post("https://example.com/hook").mock(
            side_effect=[
                httpx.ConnectError("refused"),
                httpx.Response(200),
            ]
        )
        payload = json.dumps({"event": "test"}).encode()

        with patch("src.worker.webhook.asyncio.sleep", new_callable=AsyncMock):
            await _deliver_webhook(
                "https://example.com/hook", "secret", "review.started", "del-1", payload
            )

        assert route.call_count == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_gives_up_after_max_retries(self):
        route = respx.post("https://example.com/hook").mock(
            return_value=httpx.Response(500)
        )
        payload = json.dumps({"event": "test"}).encode()

        with patch("src.worker.webhook.WEBHOOK_MAX_RETRIES", 3), \
             patch("src.worker.webhook.asyncio.sleep", new_callable=AsyncMock):
            await _deliver_webhook(
                "https://example.com/hook", "secret", "review.started", "del-1", payload
            )

        assert route.call_count == 3


class TestFireWebhook:
    """Tests for the public fire_webhook API."""

    def test_noop_when_webhook_is_none(self):
        # Should not raise
        fire_webhook(None, "review.started", "rev_1", {})

    def test_skips_unsubscribed_events(self):
        webhook = WebhookConfig(
            url="https://example.com/hook",
            secret="s",
            events=["review.completed"],
        )

        with patch("src.worker.webhook.asyncio.create_task") as mock_task:
            fire_webhook(webhook, "review.started", "rev_1", {})
            mock_task.assert_not_called()

    def test_spawns_background_task(self):
        webhook = WebhookConfig(
            url="https://example.com/hook",
            secret="s",
            events=["review.started"],
        )

        with patch("src.worker.webhook.asyncio.create_task") as mock_task:
            fire_webhook(webhook, "review.started", "rev_1", {"application_ref": "X"})
            mock_task.assert_called_once()

    @pytest.mark.asyncio
    @respx.mock
    async def test_delivery_failure_does_not_propagate(self):
        """fire_webhook should never raise even if delivery fails."""
        respx.post("https://example.com/hook").mock(
            return_value=httpx.Response(500)
        )

        webhook = WebhookConfig(
            url="https://example.com/hook",
            secret="s",
            events=["review.started"],
        )

        with patch("src.worker.webhook.WEBHOOK_MAX_RETRIES", 1), \
             patch("src.worker.webhook.asyncio.sleep", new_callable=AsyncMock):
            # Use a real event loop task
            fire_webhook(webhook, "review.started", "rev_1", {})
            # Let the task complete
            await asyncio.sleep(0.1)
