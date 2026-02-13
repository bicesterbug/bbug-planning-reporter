# Design: Global Webhooks

**Version:** 1.0
**Date:** 2026-02-13
**Status:** Implemented
**Linked Specification** `.sdd/global-webhooks/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The webhook system is per-request: callers include a `WebhookConfig(url, secret, events)` in the `ReviewRequest` body. This config is stored in the `ReviewJob` Redis model and threaded through to `fire_webhook()` calls in `review_jobs.py`. The `fire_webhook()` function checks that the event is in the config's subscribed events list, builds a payload envelope, signs it with the secret via `X-Webhook-Secret` header, and delivers via `_deliver_webhook()` with exponential backoff retries.

Letter jobs (`letter_jobs.py`) have no webhook integration at all — there is no mechanism to notify downstream consumers when a letter completes or fails.

The only webhook consumer (bbug-website) has now removed its webhook handler and polls via a cron job. The per-request webhook model is unused.

### Proposed Architecture

Replace the per-request webhook with a global, environment-variable-configured webhook:

1. **`WEBHOOK_URL` env var** — read once at module level in `webhook.py`. When set, all events fire to this URL. When unset, the entire webhook path is skipped.
2. **`fire_webhook()` simplified** — no longer accepts a `webhook` config object. Reads the global URL. No event filtering (all events always fire). No secret header.
3. **New events** — `review.completed.markdown` and `letter.completed` added alongside existing `review.completed` and `review.failed`.
4. **Letter job integration** — `letter_jobs.py` calls `fire_webhook()` on success and does not fire on failure (letter failures are not a specified event).
5. **Cleanup** — `WebhookConfig` removed from `ReviewJob` model, `WebhookConfigRequest` removed from API schemas, `webhook` field removed from `ReviewRequest`, webhook URL validator simplified.

Flow:
- **Before:** Caller → POST review with WebhookConfig → Worker reads config from job → fires if event matches
- **After:** Env var set at deploy time → Worker calls `fire_webhook(event, review_id, data)` → fires to global URL if configured

### Technology Decisions

- No new dependencies required
- Reuses existing `_build_payload()` and `_deliver_webhook()` machinery
- `WEBHOOK_URL` read from `os.environ` at module level (same pattern as `WEBHOOK_MAX_RETRIES`)

### Quality Attributes

- **Simplicity:** Single env var replaces per-request config, secret management, event filtering
- **Reliability:** Existing retry/backoff preserved; delivery never blocks the worker
- **Backward compatibility:** `ReviewRequest` with a `webhook` field silently ignores it (field removed from schema, Pydantic `model_config` with `extra="ignore"` or simply no field)

---

## API Design

### Removed from API

The `webhook` field is removed from `ReviewRequest`. Consumers that still send it will have it silently ignored (Pydantic's default behaviour when the field isn't defined in the model).

The `WebhookConfigRequest` schema class is removed.

### Webhook HTTP Delivery

Each webhook is an HTTP POST to `WEBHOOK_URL` with:

**Headers:**

| Header | Value |
|--------|-------|
| `Content-Type` | `application/json` |
| `X-Webhook-Event` | Event name string |
| `X-Webhook-Delivery-Id` | UUID |
| `X-Webhook-Timestamp` | Unix epoch string |

No `X-Webhook-Secret` header.

**Envelope (all events):**

```
{
  delivery_id: UUID string,
  event: event name string,
  review_id: string,
  timestamp: Unix epoch number,
  data: { ...event-specific fields }
}
```

**Events and their `data` fields:**

- `review.completed` — `application_ref`, `overall_rating`, `review_url`, `application` (object), `review` (object), `metadata` (object)
- `review.completed.markdown` — `application_ref`, `full_markdown`
- `letter.completed` — `letter_id`, `review_id`, `application_ref`, `stance`, `tone`, `content`, `metadata` (object)
- `review.failed` — `application_ref`, `error` (object with `code` and `message`)

---

## Modified Components

### fire_webhook
**Change Description:** Currently accepts a `webhook: Any | None` config object, checks `webhook.events` for the event, and uses `webhook.url` and `webhook.secret` for delivery. Must be changed to: accept no webhook config; read `WEBHOOK_URL` from environment; skip if unset; always fire (no event filtering); pass no secret to `_deliver_webhook`.

**Dependants:** All callers (`_handle_success`, `_handle_failure`, `process_review` exception handler, and new letter job call sites) must update their call signatures.

**Kind:** Function

**Requirements References**
- [global-webhooks:FR-001]: Read webhook URL from environment variable
- [global-webhooks:FR-007]: Maintain envelope structure
- [global-webhooks:FR-008]: No authentication headers

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | No-op when WEBHOOK_URL unset | `WEBHOOK_URL` env var is not set | `fire_webhook` is called | No background task is created, no error |
| TS-02 | No-op when WEBHOOK_URL empty | `WEBHOOK_URL` is set to empty string | `fire_webhook` is called | No background task is created |
| TS-03 | Fires when WEBHOOK_URL set | `WEBHOOK_URL=https://example.com/hook` | `fire_webhook` is called with event and data | Background delivery task is created targeting the URL |
| TS-04 | All events fire (no filtering) | `WEBHOOK_URL` is set | `fire_webhook` called with any event name | Delivery is always attempted (no event filter check) |

### _deliver_webhook
**Change Description:** Currently accepts a `secret` parameter and includes it as `X-Webhook-Secret` header. Must be changed to: remove the `secret` parameter; omit the `X-Webhook-Secret` header from requests.

**Dependants:** `fire_webhook` (the only caller)

**Kind:** Function

**Requirements References**
- [global-webhooks:FR-008]: No authentication headers
- [global-webhooks:NFR-001]: Retain retry/backoff behaviour

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | No secret header sent | Webhook delivery to a URL | `_deliver_webhook` sends the POST | Request headers do not contain `X-Webhook-Secret` |
| TS-02 | Other headers preserved | Webhook delivery | POST is sent | Headers include `Content-Type`, `X-Webhook-Event`, `X-Webhook-Delivery-Id`, `X-Webhook-Timestamp` |

### _handle_success (review_jobs.py)
**Change Description:** Currently calls `fire_webhook(webhook, "review.completed", ...)` passing the per-request webhook config. Must be changed to: (1) call `fire_webhook("review.completed", ...)` without webhook config, (2) add a second call `fire_webhook("review.completed.markdown", ...)` with the markdown payload. Remove `webhook` parameter from function signature.

**Dependants:** `process_review` (caller)

**Kind:** Function

**Requirements References**
- [global-webhooks:FR-003]: review.completed event with structured JSON data
- [global-webhooks:FR-004]: review.completed.markdown event

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Fires review.completed with full data | A successful review with application and review content | `_handle_success` is called | `fire_webhook` called with event `review.completed` and data containing `application`, `review`, `metadata` |
| TS-02 | Fires review.completed.markdown | A successful review with `full_markdown` in review | `_handle_success` is called | `fire_webhook` called with event `review.completed.markdown` and data containing `application_ref` and `full_markdown` |
| TS-03 | Null fields preserved | Review completes but `suggested_conditions` is null | `_handle_success` fires webhooks | Payload `data.review.suggested_conditions` is null (not omitted) |

### _handle_failure (review_jobs.py)
**Change Description:** Currently calls `fire_webhook(webhook, "review.failed", ...)` passing the per-request webhook config. Must be changed to call `fire_webhook("review.failed", ...)` without webhook config. Remove `webhook` parameter from function signature.

**Dependants:** `process_review` (caller)

**Kind:** Function

**Requirements References**
- [global-webhooks:FR-006]: review.failed event with structured error

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Fires review.failed with structured error | A review fails with scraper error | `_handle_failure` is called | `fire_webhook` called with event `review.failed` and `data.error` containing `code` and `message` |

### process_review (review_jobs.py)
**Change Description:** Currently reads `webhook` from the Redis job and passes it to `fire_webhook` and to `_handle_success`/`_handle_failure`. Must be changed to: remove webhook reading from job, remove `review.started` webhook call, remove webhook parameter from `_handle_success`/`_handle_failure` calls, update exception handler to call `fire_webhook("review.failed", ...)` without webhook config.

**Dependants:** None

**Kind:** Function

**Requirements References**
- [global-webhooks:FR-001]: No per-request config; uses global env var
- [global-webhooks:FR-002]: Remove per-request webhook from job
- [global-webhooks:FR-006]: Structured error in exception handler webhook

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | No webhook read from job | `WEBHOOK_URL` is set | `process_review` runs | Webhook config is not read from the Redis job |
| TS-02 | No review.started event fired | `WEBHOOK_URL` is set | `process_review` starts | No `review.started` webhook is fired |
| TS-03 | Exception handler fires review.failed | An unexpected exception occurs | Exception handler runs | `fire_webhook` called with `review.failed` and structured error `{code: "internal_error", message: "..."}` |

### letter_job (letter_jobs.py)
**Change Description:** Currently has no webhook integration. Must be modified to call `fire_webhook("letter.completed", ...)` after successful letter generation, passing the letter content, metadata, and identifying fields.

**Dependants:** None

**Kind:** Function

**Requirements References**
- [global-webhooks:FR-005]: letter.completed event

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Fires letter.completed on success | Letter generates successfully | `letter_job` completes | `fire_webhook` called with event `letter.completed` and data containing `letter_id`, `review_id`, `application_ref`, `stance`, `tone`, `content`, `metadata` |
| TS-02 | No webhook on letter failure | Letter generation fails | `letter_job` catches exception | No `fire_webhook` call is made (letter failures are not a specified event) |

### ReviewJob (models.py)
**Change Description:** Currently has `webhook: WebhookConfig | None = None` field. Must remove this field. The `WebhookConfig` class itself should be removed from `models.py`.

**Dependants:** `process_review` (no longer reads webhook from job), `submit_review` API route (no longer stores webhook config), tests that construct `ReviewJob` or `WebhookConfig`.

**Kind:** Class

**Requirements References**
- [global-webhooks:FR-002]: Remove per-request webhook configuration

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | ReviewJob without webhook field | A ReviewJob is created | The model is instantiated | No `webhook` attribute exists |

### ReviewRequest (schemas.py)
**Change Description:** Currently has `webhook: WebhookConfigRequest | None = None` field. Must remove this field. The `WebhookConfigRequest` class should be removed. Existing callers sending a `webhook` field will have it silently ignored by Pydantic.

**Dependants:** `submit_review` route (no longer reads webhook from request), schema `__init__.py` re-exports.

**Kind:** Class

**Requirements References**
- [global-webhooks:FR-002]: Remove per-request webhook from API

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | ReviewRequest without webhook field | A review is submitted with no `webhook` field | Request is parsed | Request is accepted normally |
| TS-02 | Unknown webhook field silently ignored | A review is submitted with a `webhook` field (old client) | Request is parsed | Request is accepted; `webhook` is ignored |

### submit_review (reviews.py)
**Change Description:** Currently constructs `WebhookConfig` from `request.webhook` and stores it in the `ReviewJob`. Must remove the webhook construction block. No longer imports `WebhookConfig`.

**Dependants:** None

**Kind:** Function

**Requirements References**
- [global-webhooks:FR-002]: Remove per-request webhook from API route

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | No webhook stored in job | A review is submitted | Job is created in Redis | Job has no webhook field |

### schemas/__init__.py
**Change Description:** Currently re-exports `WebhookConfigRequest`. Must remove the import and re-export of `WebhookConfigRequest`.

**Dependants:** Any module importing `WebhookConfigRequest` from `src.api.schemas`

**Kind:** Module

**Requirements References**
- [global-webhooks:FR-002]: Clean up removed schema

**Test Scenarios**

N/A (structural change, covered by other tests compiling/importing correctly)

---

## Added Components

N/A — No new components needed. All changes are modifications to existing functions and models.

---

## Used Components

### _build_payload
**Location** `src/worker/webhook.py:24`

**Provides** Constructs the webhook envelope dict with `delivery_id`, `event`, `review_id`, `timestamp`, and `data`

**Used By** `fire_webhook` (unchanged usage)

### _serialize_application
**Location** `src/worker/review_jobs.py:274`

**Provides** Serialises `ApplicationMetadata` to dict with standard fields

**Used By** `_handle_success` (unchanged usage)

### RedisClient
**Location** `src/shared/redis_client.py`

**Provides** `get_letter()` for retrieving letter records (used by `letter_job` to get `application_ref`, `stance`, `tone`)

**Used By** `letter_job` (already used; no change needed)

---

## Documentation Considerations

- `deploy/.env.example` should document `WEBHOOK_URL` env var
- `.env.example` at project root should document `WEBHOOK_URL`
- `docker-compose.yml` and `deploy/docker-compose.yml` should pass `WEBHOOK_URL` to worker service

---

## Instrumentation (if needed)

| Requirement | Observability Criteria | Implementation | Component |
|-------------|------------------------|----------------|-----------|
| [global-webhooks:NFR-001] | Delivery attempts, status, and failures observable | Existing structured logging in `_deliver_webhook` already records `url`, `webhook_event`, `delivery_id`, `status`, `attempt`, `payload_size_bytes` | `_deliver_webhook` |
| [global-webhooks:NFR-003] | Payload size observable | Existing `payload_size_bytes` field in "Webhook delivered" log entry | `_deliver_webhook` |

---

## Integration Test Scenarios (if needed)

| ID | Scenario | Given | When | Then | Components Involved |
|----|----------|-------|------|------|---------------------|
| ITS-01 | End-to-end review completed webhooks | `WEBHOOK_URL` is set; a review completes successfully | `_handle_success` is called with full ReviewResult | `fire_webhook` is called twice: once with `review.completed` (full JSON data) and once with `review.completed.markdown` (markdown only); both payloads are JSON-serialisable | `_handle_success`, `fire_webhook`, `_build_payload` |
| ITS-02 | End-to-end review failed webhook | `WEBHOOK_URL` is set; a review fails | `_handle_failure` is called | `fire_webhook` is called with `review.failed` and structured error dict | `_handle_failure`, `fire_webhook` |
| ITS-03 | End-to-end letter completed webhook | `WEBHOOK_URL` is set; a letter generates successfully | `letter_job` completes | `fire_webhook` is called with `letter.completed` and letter data | `letter_job`, `fire_webhook` |
| ITS-04 | No webhooks when URL unset | `WEBHOOK_URL` is not set; a review completes | `_handle_success` is called | `fire_webhook` returns immediately, no delivery attempted | `_handle_success`, `fire_webhook` |

---

## E2E Test Scenarios (if needed)

N/A — E2E testing requires the full webhook delivery chain including the bbug-website, which is in a separate repo.

---

## Test Data

- Existing test fixtures for `ReviewResult` with populated application, review, and metadata fields
- Edge case: `ReviewResult` with null optional fields (`suggested_conditions`, `key_documents`, `full_markdown`)
- Sample letter record dict with `letter_id`, `review_id`, `application_ref`, `stance`, `tone`, `content`, `metadata`
- Use `respx` for mocking HTTP delivery in webhook tests (existing pattern)

---

## Test Feasibility

- All tests can be written as unit tests mocking `fire_webhook` or using `respx` for HTTP
- No missing infrastructure
- Existing test patterns in `tests/test_worker/test_webhook.py` and `tests/test_worker/test_review_jobs.py` provide templates

---

## Risks and Dependencies

- **Backward compatibility:** Old clients sending `webhook` in `ReviewRequest` body will have it silently ignored. Pydantic v2 ignores extra fields by default, so no action needed.
- **bbug-website consumer update:** The bbug-website must be updated to consume the new webhook events if/when it wants to move from cron polling to push. This is tracked separately.
- **Letter data availability:** `letter_job` already has access to `letter_record` (with `stance`, `tone`, `application_ref`) and `letter_content` at the point of success, so the webhook data is readily available.

---

## Feasability Review

- No blockers. All changes are within existing files. The letter webhook is a straightforward addition following the existing pattern in `review_jobs.py`.

---

## Task Breakdown

> **CRITICAL: Tests are written WITH implementation, not after.**

### Phase 1: Simplify webhook module and remove per-request config

- Task 1: Refactor `fire_webhook` and `_deliver_webhook` to use global `WEBHOOK_URL` env var
  - Status: Done
  - Change `fire_webhook` to read `WEBHOOK_URL` from env, remove `webhook` config parameter, remove event filtering logic. Change `_deliver_webhook` to remove `secret` parameter and `X-Webhook-Secret` header. Update existing tests in `test_webhook.py`.
  - Requirements: [global-webhooks:FR-001], [global-webhooks:FR-008], [global-webhooks:NFR-001]
  - Test Scenarios: [global-webhooks:fire_webhook/TS-01], [global-webhooks:fire_webhook/TS-02], [global-webhooks:fire_webhook/TS-03], [global-webhooks:fire_webhook/TS-04], [global-webhooks:_deliver_webhook/TS-01], [global-webhooks:_deliver_webhook/TS-02]

- Task 2: Remove `WebhookConfig` from models, `WebhookConfigRequest` from schemas, and webhook handling from API route
  - Status: Done
  - Remove `WebhookConfig` class from `models.py`. Remove `webhook` field from `ReviewJob`. Remove `WebhookConfigRequest` class from `schemas.py`. Remove `WebhookConfigRequest` re-export from `schemas/__init__.py`. Remove webhook construction and storage from `submit_review` route. Update all affected tests.
  - Requirements: [global-webhooks:FR-002]
  - Test Scenarios: [global-webhooks:ReviewJob/TS-01], [global-webhooks:ReviewRequest/TS-01], [global-webhooks:ReviewRequest/TS-02], [global-webhooks:submit_review/TS-01]

- Task 3: Update `process_review`, `_handle_success`, and `_handle_failure` to use simplified `fire_webhook`
  - Status: Done
  - Remove webhook reading from Redis job in `process_review`. Remove `review.started` fire call. Remove `webhook` parameter from `_handle_success` and `_handle_failure`. Update all `fire_webhook` calls to new signature. Add `review.completed.markdown` webhook call in `_handle_success`. Update exception handler. Update tests in `test_review_jobs.py`.
  - Requirements: [global-webhooks:FR-001], [global-webhooks:FR-003], [global-webhooks:FR-004], [global-webhooks:FR-006], [global-webhooks:FR-007], [global-webhooks:NFR-002]
  - Test Scenarios: [global-webhooks:_handle_success/TS-01], [global-webhooks:_handle_success/TS-02], [global-webhooks:_handle_success/TS-03], [global-webhooks:_handle_failure/TS-01], [global-webhooks:process_review/TS-01], [global-webhooks:process_review/TS-02], [global-webhooks:process_review/TS-03], [global-webhooks:ITS-01], [global-webhooks:ITS-02], [global-webhooks:ITS-04]

- Task 4: Add `letter.completed` webhook to `letter_job`
  - Status: Done
  - Import `fire_webhook` in `letter_jobs.py`. After successful letter generation (after Redis update), call `fire_webhook("letter.completed", review_id, {...})` with letter data. No webhook on failure.
  - Requirements: [global-webhooks:FR-005]
  - Test Scenarios: [global-webhooks:letter_job/TS-01], [global-webhooks:letter_job/TS-02], [global-webhooks:ITS-03]

- Task 5: Update environment configuration files
  - Status: Done
  - Add `WEBHOOK_URL` to `.env.example`, `deploy/.env.example`, `docker-compose.yml`, and `deploy/docker-compose.yml` worker service environment. Removed `WEBHOOK_REQUIRE_HTTPS` from docker-compose files and env examples. Removed dead `src/api/validators/webhook.py` and its tests (no longer used after per-request webhook removal).
  - Requirements: [global-webhooks:FR-001]
  - Test Scenarios: N/A (configuration-only)

---

## Intermediate Dead Code Tracking

| Phase Introduced | Description | Used In Phase | Status |
|------------------|-------------|---------------|--------|
| N/A | N/A | N/A | N/A |

---

## Intermediate Stub Tracking

| Phase Introduced | Test Name | Reason for Stub | Implemented In Phase | Status |
|------------------|-----------|-----------------|----------------------|--------|
| N/A | N/A | N/A | N/A | N/A |

---

## Requirements Validation

- [global-webhooks:FR-001]
  - Phase 1 Task 1
  - Phase 1 Task 3
  - Phase 1 Task 5
- [global-webhooks:FR-002]
  - Phase 1 Task 2
- [global-webhooks:FR-003]
  - Phase 1 Task 3
- [global-webhooks:FR-004]
  - Phase 1 Task 3
- [global-webhooks:FR-005]
  - Phase 1 Task 4
- [global-webhooks:FR-006]
  - Phase 1 Task 3
- [global-webhooks:FR-007]
  - Phase 1 Task 3
- [global-webhooks:FR-008]
  - Phase 1 Task 1

- [global-webhooks:NFR-001]
  - Phase 1 Task 1
- [global-webhooks:NFR-002]
  - Phase 1 Task 3
- [global-webhooks:NFR-003]
  - Phase 1 Task 1 (existing instrumentation preserved)

---

## Test Scenario Validation

### Component Scenarios
- [global-webhooks:fire_webhook/TS-01]: Phase 1 Task 1
- [global-webhooks:fire_webhook/TS-02]: Phase 1 Task 1
- [global-webhooks:fire_webhook/TS-03]: Phase 1 Task 1
- [global-webhooks:fire_webhook/TS-04]: Phase 1 Task 1
- [global-webhooks:_deliver_webhook/TS-01]: Phase 1 Task 1
- [global-webhooks:_deliver_webhook/TS-02]: Phase 1 Task 1
- [global-webhooks:ReviewJob/TS-01]: Phase 1 Task 2
- [global-webhooks:ReviewRequest/TS-01]: Phase 1 Task 2
- [global-webhooks:ReviewRequest/TS-02]: Phase 1 Task 2
- [global-webhooks:submit_review/TS-01]: Phase 1 Task 2
- [global-webhooks:_handle_success/TS-01]: Phase 1 Task 3
- [global-webhooks:_handle_success/TS-02]: Phase 1 Task 3
- [global-webhooks:_handle_success/TS-03]: Phase 1 Task 3
- [global-webhooks:_handle_failure/TS-01]: Phase 1 Task 3
- [global-webhooks:process_review/TS-01]: Phase 1 Task 3
- [global-webhooks:process_review/TS-02]: Phase 1 Task 3
- [global-webhooks:process_review/TS-03]: Phase 1 Task 3
- [global-webhooks:letter_job/TS-01]: Phase 1 Task 4
- [global-webhooks:letter_job/TS-02]: Phase 1 Task 4

### Integration Scenarios
- [global-webhooks:ITS-01]: Phase 1 Task 3
- [global-webhooks:ITS-02]: Phase 1 Task 3
- [global-webhooks:ITS-03]: Phase 1 Task 4
- [global-webhooks:ITS-04]: Phase 1 Task 3

### E2E Scenarios
- N/A

---

## Appendix

### Glossary
- **Global webhook:** A single webhook URL configured via `WEBHOOK_URL` environment variable
- **Per-request webhook:** The previous system where each review submission included its own URL and secret (being removed)
- **Envelope:** The common wrapper structure (delivery_id, event, review_id, timestamp, data) shared by all webhook events

### References
- Current webhook module: `src/worker/webhook.py`
- Current review job handler: `src/worker/review_jobs.py`
- Letter job handler: `src/worker/letter_jobs.py`
- Shared models: `src/shared/models.py`
- API schemas: `src/api/schemas.py`
- API review route: `src/api/routes/reviews.py`
- Webhook URL validator: `src/api/validators/webhook.py`
- Existing webhook tests: `tests/test_worker/test_webhook.py`
- Existing review job tests: `tests/test_worker/test_review_jobs.py`
- Specification: `.sdd/global-webhooks/specification.md`

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-13 | Claude | Initial design |
