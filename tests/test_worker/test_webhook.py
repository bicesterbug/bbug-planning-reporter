"""Tests for webhook delivery module.

Verifies [global-webhooks:fire_webhook/TS-01] through [TS-04]
Verifies [global-webhooks:_deliver_webhook/TS-01], [TS-02]
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from src.worker.webhook import (
    _build_payload,
    _deliver_webhook,
    fire_webhook,
)


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
        p1 = _build_payload("review.completed", "rev_1", {})
        p2 = _build_payload("review.completed", "rev_1", {})
        assert p1["delivery_id"] != p2["delivery_id"]


class TestDeliverWebhook:
    """Tests for HTTP delivery with retries."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_successful_delivery(self):
        route = respx.post("https://example.com/hook").mock(return_value=httpx.Response(200))
        payload = json.dumps({"event": "test"}).encode()

        await _deliver_webhook("https://example.com/hook", "review.completed", "del-1", payload)

        assert route.called
        assert route.call_count == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_secret_header(self):
        """Verifies [global-webhooks:_deliver_webhook/TS-01] - No secret header sent."""
        route = respx.post("https://example.com/hook").mock(return_value=httpx.Response(200))
        payload = json.dumps({"event": "test"}).encode()

        await _deliver_webhook("https://example.com/hook", "review.completed", "del-1", payload)

        request = route.calls[0].request
        assert "X-Webhook-Secret" not in request.headers

    @pytest.mark.asyncio
    @respx.mock
    async def test_correct_headers(self):
        """Verifies [global-webhooks:_deliver_webhook/TS-02] - Standard headers preserved."""
        route = respx.post("https://example.com/hook").mock(return_value=httpx.Response(200))
        payload = json.dumps({"event": "test"}).encode()

        await _deliver_webhook("https://example.com/hook", "review.completed", "del-1", payload)

        request = route.calls[0].request
        assert request.headers["X-Webhook-Event"] == "review.completed"
        assert request.headers["X-Webhook-Delivery-Id"] == "del-1"
        assert "X-Webhook-Timestamp" in request.headers
        assert request.headers["Content-Type"] == "application/json"

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
            await _deliver_webhook("https://example.com/hook", "review.completed", "del-1", payload)

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
            await _deliver_webhook("https://example.com/hook", "review.completed", "del-1", payload)

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
            await _deliver_webhook("https://example.com/hook", "review.completed", "del-1", payload)

        assert route.call_count == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_gives_up_after_max_retries(self):
        route = respx.post("https://example.com/hook").mock(return_value=httpx.Response(500))
        payload = json.dumps({"event": "test"}).encode()

        with (
            patch("src.worker.webhook.WEBHOOK_MAX_RETRIES", 3),
            patch("src.worker.webhook.asyncio.sleep", new_callable=AsyncMock),
        ):
            await _deliver_webhook("https://example.com/hook", "review.completed", "del-1", payload)

        assert route.call_count == 3


class TestFireWebhook:
    """Tests for the public fire_webhook API."""

    def test_noop_when_webhook_url_unset(self):
        """Verifies [global-webhooks:fire_webhook/TS-01] - No-op when WEBHOOK_URL unset."""
        with (
            patch("src.worker.webhook.WEBHOOK_URL", None),
            patch("src.worker.webhook.asyncio.create_task") as mock_task,
        ):
            fire_webhook("review.completed", "rev_1", {})
            mock_task.assert_not_called()

    def test_noop_when_webhook_url_empty(self):
        """Verifies [global-webhooks:fire_webhook/TS-02] - No-op when WEBHOOK_URL empty."""
        with (
            patch("src.worker.webhook.WEBHOOK_URL", None),
            patch("src.worker.webhook.asyncio.create_task") as mock_task,
        ):
            fire_webhook("review.completed", "rev_1", {})
            mock_task.assert_not_called()

    def test_fires_when_webhook_url_set(self):
        """Verifies [global-webhooks:fire_webhook/TS-03] - Fires when WEBHOOK_URL set."""
        with (
            patch("src.worker.webhook.WEBHOOK_URL", "https://example.com/hook"),
            patch("src.worker.webhook.asyncio.create_task") as mock_task,
        ):
            fire_webhook("review.completed", "rev_1", {"application_ref": "X"})
            mock_task.assert_called_once()

    def test_all_events_fire_no_filtering(self):
        """Verifies [global-webhooks:fire_webhook/TS-04] - All events fire (no filtering)."""
        with (
            patch("src.worker.webhook.WEBHOOK_URL", "https://example.com/hook"),
            patch("src.worker.webhook.asyncio.create_task") as mock_task,
        ):
            fire_webhook("review.completed", "rev_1", {})
            fire_webhook("review.completed.markdown", "rev_1", {})
            fire_webhook("letter.completed", "rev_1", {})
            fire_webhook("review.failed", "rev_1", {})
            assert mock_task.call_count == 4

    @pytest.mark.asyncio
    @respx.mock
    async def test_delivery_failure_does_not_propagate(self):
        """fire_webhook should never raise even if delivery fails."""
        respx.post("https://example.com/hook").mock(return_value=httpx.Response(500))

        with (
            patch("src.worker.webhook.WEBHOOK_URL", "https://example.com/hook"),
            patch("src.worker.webhook.WEBHOOK_MAX_RETRIES", 1),
            patch("src.worker.webhook.asyncio.sleep", new_callable=AsyncMock),
        ):
            fire_webhook("review.completed", "rev_1", {})
            # Let the task complete
            await asyncio.sleep(0.1)
