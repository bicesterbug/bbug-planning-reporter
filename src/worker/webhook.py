"""
Webhook delivery for review job lifecycle events.

Delivers HTTP POST callbacks with HMAC-SHA256 signatures and exponential
backoff retries.  All errors are caught and logged — delivery never blocks
or crashes the worker.
"""

import asyncio
import hashlib
import hmac
import os
import time
import uuid
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

WEBHOOK_MAX_RETRIES = int(os.environ.get("WEBHOOK_MAX_RETRIES", "5"))
WEBHOOK_TIMEOUT = float(os.environ.get("WEBHOOK_TIMEOUT", "10"))


def _sign_payload(payload_bytes: bytes, secret: str) -> str:
    """Return ``sha256={hex}`` HMAC signature for *payload_bytes*."""
    mac = hmac.new(secret.encode(), payload_bytes, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def _build_payload(event: str, review_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Construct the webhook event payload."""
    return {
        "delivery_id": str(uuid.uuid4()),
        "event": event,
        "review_id": review_id,
        "timestamp": time.time(),
        "data": data,
    }


async def _deliver_webhook(
    url: str,
    secret: str,
    event: str,
    delivery_id: str,
    payload: bytes,
) -> None:
    """POST *payload* to *url* with retries and exponential backoff.

    Never raises — all errors are logged and swallowed.
    """
    signature = _sign_payload(payload, secret)
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature-256": signature,
        "X-Webhook-Event": event,
        "X-Webhook-Delivery-Id": delivery_id,
        "X-Webhook-Timestamp": str(int(time.time())),
    }

    for attempt in range(1, WEBHOOK_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT) as client:
                resp = await client.post(url, content=payload, headers=headers)

            if resp.status_code < 300:
                logger.info(
                    "Webhook delivered",
                    url=url,
                    webhook_event=event,
                    delivery_id=delivery_id,
                    status=resp.status_code,
                    attempt=attempt,
                )
                return

            logger.warning(
                "Webhook delivery got non-2xx response",
                url=url,
                webhook_event=event,
                delivery_id=delivery_id,
                status=resp.status_code,
                attempt=attempt,
            )
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            logger.warning(
                "Webhook delivery error",
                url=url,
                webhook_event=event,
                delivery_id=delivery_id,
                error=str(exc),
                attempt=attempt,
            )

        if attempt < WEBHOOK_MAX_RETRIES:
            delay = 2 ** (attempt - 1)  # 1, 2, 4, 8, 16
            await asyncio.sleep(delay)

    logger.error(
        "Webhook delivery failed after all retries",
        url=url,
        webhook_event=event,
        delivery_id=delivery_id,
        max_retries=WEBHOOK_MAX_RETRIES,
    )


def fire_webhook(
    webhook: Any | None,
    event: str,
    review_id: str,
    data: dict[str, Any],
) -> None:
    """Fire a webhook event as a background task (fire-and-forget).

    Does nothing if *webhook* is ``None`` or the event is not in the
    webhook's subscribed events list.
    """
    if webhook is None:
        return

    if event not in (webhook.events or []):
        logger.debug(
            "Webhook event not subscribed, skipping",
            webhook_event=event,
            subscribed=webhook.events,
        )
        return

    payload_dict = _build_payload(event, review_id, data)
    import json

    payload_bytes = json.dumps(payload_dict, default=str).encode()
    delivery_id = payload_dict["delivery_id"]

    asyncio.create_task(
        _deliver_webhook(webhook.url, webhook.secret, event, delivery_id, payload_bytes),
    )
