# Specification: Global Webhooks

**Version:** 1.0
**Date:** 2026-02-13
**Status:** Draft

---

## Problem Statement

The current webhook system requires callers to provide a per-request `WebhookConfig` (url, secret, event list) when submitting a review. The only consumer (bbug-website) has removed its webhook handler and now polls the API via a cron job. The system needs a simpler, globally-configured webhook that fires for every review and letter lifecycle event, configured via environment variables rather than per-request payloads.

## Beneficiaries

**Primary:**
- BBUG website (or any single downstream consumer) that needs push notifications when reviews and letters complete or fail

**Secondary:**
- Operations/maintainers who benefit from a simpler configuration model (one env var instead of per-request config)

---

## Outcomes

**Must Haves**
- Webhook URL configured via a single environment variable, firing for every review and letter
- No authentication required on webhook delivery (no secrets, no HMAC signing)
- Webhook events for: review completed (structured JSON data), review markdown, letter completed, and review failed
- Fully documented JSON schemas for each webhook event payload
- Per-request webhook configuration removed from the API (no `webhook` field in `ReviewRequest`)

**Nice-to-haves**
- Existing retry and backoff behaviour preserved

---

## Explicitly Out of Scope

- Adding new webhook event types beyond the four specified (no progress or started events)
- Webhook authentication or signing mechanisms
- Webhook subscription management API (register/unregister endpoints)
- Multiple webhook URLs (only one global URL is supported)
- Modifying the bbug-website to consume these webhooks (separate repo)

---

## Functional Requirements

### FR-001: Environment-variable webhook configuration
**Description:** The system must read a global webhook URL from the `WEBHOOK_URL` environment variable. When set, all review and letter lifecycle events fire webhooks to this URL. When unset or empty, no webhooks are fired.

**Examples:**
- Positive case: `WEBHOOK_URL=https://www.bicesterbug.org/api/webhooks/agent` is set; all reviews fire webhooks to that URL
- Positive case: `WEBHOOK_URL` is unset; no webhooks are fired, no errors logged
- Edge case: `WEBHOOK_URL` is set to empty string; treated as unset, no webhooks fired

### FR-002: Remove per-request webhook configuration
**Description:** The `webhook` field must be removed from the `ReviewRequest` API schema. The `WebhookConfig` model must be removed from `ReviewJob`. The `WebhookConfigRequest` API schema must be removed. The webhook URL validation endpoint (if any) is no longer needed.

**Examples:**
- Positive case: A POST to `/api/v1/reviews` without a `webhook` field works as before
- Negative case: A POST to `/api/v1/reviews` with a `webhook` field must be silently ignored (not rejected) for backward compatibility

### FR-003: review.completed event with structured JSON data
**Description:** When a review completes successfully, the system must fire a `review.completed` webhook containing the full structured review data: `application` metadata, `review` content (overall_rating, summary, key_documents, aspects, policy_compliance, recommendations, suggested_conditions), and `metadata` (model, tokens, processing time, documents analysed, policy sources).

**Examples:**
- Positive case: Review completes; webhook payload `data` contains `application`, `review`, and `metadata` objects
- Edge case: Review completes but `suggested_conditions` is null; null fields are present in payload (not omitted)

### FR-004: review.completed.markdown event with full markdown
**Description:** When a review completes successfully, the system must fire a separate `review.completed.markdown` webhook containing the review's `full_markdown` content alongside identifying fields (`review_id`, `application_ref`).

**Examples:**
- Positive case: Review completes with markdown; webhook fires with `data.full_markdown` containing the full review markdown
- Edge case: Review completes but `full_markdown` is null; the event is still fired with `data.full_markdown` as null

### FR-005: letter.completed event
**Description:** When a letter is generated successfully, the system must fire a `letter.completed` webhook containing the letter content, metadata, and identifying fields (`letter_id`, `review_id`, `application_ref`, `stance`, `tone`).

**Examples:**
- Positive case: Letter completes; webhook payload includes `letter_id`, `review_id`, `application_ref`, `content` (markdown), `stance`, `tone`, and `metadata`
- Edge case: Letter metadata has null `processing_time_seconds`; included as null in payload

### FR-006: review.failed event with structured error
**Description:** When a review fails (either via handled failure or unexpected exception), the system must fire a `review.failed` webhook containing a structured error object with `code` and `message` fields.

**Examples:**
- Positive case: Review fails with scraper error; payload `data.error` is `{"code": "scraper_error", "message": "..."}`
- Positive case: Review fails with unexpected exception; payload `data.error` is `{"code": "internal_error", "message": "..."}`

### FR-007: Webhook payload envelope
**Description:** All webhook events must use a consistent envelope structure containing `delivery_id` (UUID), `event` (event name string), `review_id`, `timestamp` (Unix epoch), and `data` (event-specific payload).

**Examples:**
- Positive case: Every webhook has `delivery_id`, `event`, `review_id`, `timestamp`, and `data` fields at the top level

### FR-008: No authentication headers
**Description:** Webhook deliveries must not include any authentication headers (`X-Webhook-Secret`). The only headers sent are `Content-Type: application/json`, `X-Webhook-Event`, `X-Webhook-Delivery-Id`, and `X-Webhook-Timestamp`.

**Examples:**
- Positive case: Webhook POST does not include `X-Webhook-Secret` header
- Positive case: Webhook POST includes `X-Webhook-Event: review.completed` header

---

## Non-Functional Requirements

### NFR-001: Webhook delivery reliability
**Category:** Reliability
**Description:** Webhook delivery must use exponential backoff retries (existing behaviour). Failed deliveries must not block or crash the worker. All delivery attempts and failures must be logged with structured context.
**Acceptance Threshold:** Zero worker crashes due to webhook delivery failures
**Verification:** Observability (structured logs with delivery_id, event, attempt, status, payload_size_bytes)

### NFR-002: Serialisation safety
**Category:** Reliability
**Description:** All fields in webhook payloads must be JSON-serialisable. Non-serialisable values (datetime objects, custom types) must be converted to strings via `default=str` JSON serialiser.
**Acceptance Threshold:** Zero serialisation errors in webhook delivery
**Verification:** Testing (unit tests with representative data containing datetime fields)

### NFR-003: Webhook payload size
**Category:** Performance
**Description:** Webhook payloads must be deliverable within the existing `WEBHOOK_TIMEOUT` (10s default). Payload size must not exceed 5MB.
**Acceptance Threshold:** 99% of webhooks deliver within 10 seconds
**Verification:** Observability (structured log of payload_size_bytes and delivery duration)

---

## Open Questions

None.

---

## Appendix

### Webhook Payload Schemas

#### Envelope (all events)

```json
{
  "delivery_id": "string (UUID)",
  "event": "string (event name)",
  "review_id": "string (rev_... ULID)",
  "timestamp": "number (Unix epoch seconds)",
  "data": { "...event-specific fields..." }
}
```

#### review.completed

```json
{
  "delivery_id": "a1b2c3d4-...",
  "event": "review.completed",
  "review_id": "rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
  "timestamp": 1707840000,
  "data": {
    "application_ref": "25/01784/DISC",
    "overall_rating": "major_concerns",
    "review_url": "/api/reviews/rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
    "application": {
      "reference": "25/01784/DISC",
      "address": "Land to the north of ...",
      "proposal": "Discharge of conditions ...",
      "applicant": "Acme Developments Ltd",
      "status": "Under Consideration",
      "date_validated": "2025-01-15",
      "consultation_end": "2025-02-15",
      "documents_fetched": 42
    },
    "review": {
      "overall_rating": "major_concerns",
      "summary": "The application ...",
      "key_documents": [
        {
          "title": "Transport Assessment",
          "category": "Transport & Access",
          "summary": "...",
          "url": "https://..."
        }
      ],
      "aspects": [
        {
          "name": "Cycle Parking",
          "rating": "minor_concerns",
          "key_issue": "...",
          "detail": "...",
          "policy_refs": ["LTN 1/20 Table 11-1"]
        }
      ],
      "policy_compliance": [
        {
          "requirement": "...",
          "policy_source": "LTN_1_20",
          "compliant": false,
          "notes": "..."
        }
      ],
      "recommendations": ["Increase cycle parking to ..."],
      "suggested_conditions": ["Prior to occupation, a cycle parking management plan ..."],
      "full_markdown": "# Review of 25/01784/DISC\n\n..."
    },
    "metadata": {
      "model": "claude-sonnet-4-5-20250929",
      "total_tokens_used": 15234,
      "processing_time_seconds": 120,
      "documents_analysed": 12,
      "policy_sources_referenced": 4,
      "policy_effective_date": "2025-01-15",
      "policy_revisions_used": [
        {
          "source": "LTN_1_20",
          "revision_id": "rev_LTN_1_20_2020_07",
          "version_label": "LTN 1/20 (July 2020)"
        }
      ]
    }
  }
}
```

#### review.completed.markdown

```json
{
  "delivery_id": "a1b2c3d4-...",
  "event": "review.completed.markdown",
  "review_id": "rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
  "timestamp": 1707840000,
  "data": {
    "application_ref": "25/01784/DISC",
    "full_markdown": "# Review of 25/01784/DISC\n\n## Summary\n\nThe application ..."
  }
}
```

#### letter.completed

```json
{
  "delivery_id": "a1b2c3d4-...",
  "event": "letter.completed",
  "review_id": "rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
  "timestamp": 1707840000,
  "data": {
    "letter_id": "ltr_01HQXK7V3WNPB8MTJF2R5ADGX9",
    "review_id": "rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
    "application_ref": "25/01784/DISC",
    "stance": "object",
    "tone": "formal",
    "content": "Dear Ms Smith,\n\n...",
    "metadata": {
      "model": "claude-sonnet-4-5-20250929",
      "input_tokens": 8234,
      "output_tokens": 2100,
      "processing_time_seconds": 15.3
    }
  }
}
```

#### review.failed

```json
{
  "delivery_id": "a1b2c3d4-...",
  "event": "review.failed",
  "review_id": "rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
  "timestamp": 1707840000,
  "data": {
    "application_ref": "25/01784/DISC",
    "error": {
      "code": "scraper_error",
      "message": "Scraper error: connection timeout after 30s"
    }
  }
}
```

### HTTP Headers (all events)

| Header | Value | Description |
|--------|-------|-------------|
| `Content-Type` | `application/json` | Payload format |
| `X-Webhook-Event` | Event name (e.g. `review.completed`) | Event type identifier |
| `X-Webhook-Delivery-Id` | UUID | Unique delivery identifier |
| `X-Webhook-Timestamp` | Unix epoch string | Delivery timestamp |

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `WEBHOOK_URL` | No | (unset) | Global webhook destination URL. When unset, no webhooks are fired. |
| `WEBHOOK_MAX_RETRIES` | No | `5` | Maximum retry attempts for failed deliveries |
| `WEBHOOK_TIMEOUT` | No | `10` | HTTP timeout in seconds per delivery attempt |

### Glossary
- **Global webhook:** A single webhook URL configured via environment variable, fired for all review and letter events
- **Per-request webhook:** The previous system where each review submission included its own webhook URL and secret (being removed)
- **Envelope:** The common wrapper structure shared by all webhook event payloads

### References
- Current webhook module: `src/worker/webhook.py`
- Current review job handler: `src/worker/review_jobs.py`
- Letter job handler: `src/worker/letter_jobs.py`
- Shared models: `src/shared/models.py`
- API schemas: `src/api/schemas.py`
- Previous webhook-review-data spec: `.sdd/webhook-review-data/specification.md`

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-13 | Claude | Initial specification |
