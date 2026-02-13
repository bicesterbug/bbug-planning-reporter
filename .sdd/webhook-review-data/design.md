# Design: Webhook Review Data

**Version:** 1.0
**Date:** 2026-02-13
**Status:** Implemented
**Linked Specification** `.sdd/webhook-review-data/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The review workflow fires webhooks at lifecycle events via `fire_webhook()` in `src/worker/webhook.py`. The `review.completed` event currently sends a minimal payload:

```
data: { application_ref, overall_rating, review_url }
```

The bbug-website webhook handler (Vercel-hosted) receives this, then calls **back** to the Review Agent API at `REVIEW_AGENT_URL` to fetch full review data via `GET /api/v1/reviews/{id}`. This callback fails because the Review Agent runs on a private network (192.168.1.18:8180) unreachable from Vercel.

### Proposed Architecture

Eliminate the callback dependency by including the full review data directly in the webhook payload. The `_handle_success` function in `review_jobs.py` already has access to the complete `ReviewResult` and the serialised `review_data` dict. Instead of sending a minimal summary, pass the full `review_data` as the webhook payload data.

Flow changes:
- **Before:** Worker sends minimal webhook -> Website receives -> Website calls back to Agent API -> Website updates Strapi
- **After:** Worker sends enriched webhook -> Website receives -> Website updates Strapi directly from payload

### Technology Decisions

- No new dependencies required
- Reuses existing `review_data` dict already constructed for Redis storage
- JSON serialisation uses existing `default=str` handler for datetime safety

### Quality Attributes

- **Reliability:** Removes the network round-trip that was the single point of failure
- **Simplicity:** Fewer moving parts in the webhook processing path
- **Backward compatibility:** Additive-only changes to payload structure

---

## API Design

The webhook payload structure for `review.completed` changes from:

```
{
  "delivery_id": "uuid",
  "event": "review.completed",
  "review_id": "rev_...",
  "timestamp": 1234567890,
  "data": {
    "application_ref": "25/01784/DISC",
    "overall_rating": "major_concerns",
    "review_url": "/api/reviews/rev_..."
  }
}
```

To:

```
{
  "delivery_id": "uuid",
  "event": "review.completed",
  "review_id": "rev_...",
  "timestamp": 1234567890,
  "data": {
    "application_ref": "25/01784/DISC",
    "overall_rating": "major_concerns",
    "review_url": "/api/reviews/rev_...",
    "application": { reference, address, proposal, applicant, status, date_validated, consultation_end, documents_fetched },
    "review": { overall_rating, summary, key_documents, aspects, policy_compliance, recommendations, suggested_conditions, full_markdown },
    "metadata": { model, total_tokens_used, processing_time_seconds, documents_analysed, policy_sources_referenced, policy_effective_date, policy_revisions_used }
  }
}
```

The `review.failed` payload changes from:

```
data: { application_ref, error: "string" }
```

To:

```
data: {
  application_ref: "25/01784/DISC",
  error: { code: "scraper_error", message: "Scraper error: ..." }
}
```

---

## Modified Components

### _handle_success
**Change Description:** Currently constructs `review_data` dict for Redis and S3, then fires webhook with only `application_ref`, `overall_rating`, and `review_url`. Must be modified to include the full `application`, `review`, and `metadata` from `review_data` in the webhook payload.

**Dependants:** None (webhook consumers are additive-compatible)

**Kind:** Function

**Requirements References**
- [webhook-review-data:FR-001]: Include full review data in completed webhook
- [webhook-review-data:FR-003]: Preserve existing fields

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Enriched completed payload | A successful review with application metadata and review content | `_handle_success` fires the webhook | Webhook payload `data` contains `application`, `review`, `metadata` alongside `application_ref`, `overall_rating`, `review_url` |
| TS-02 | Null review fields preserved | A review completes but `suggested_conditions` is null | `_handle_success` fires the webhook | Payload `data.review.suggested_conditions` is null (not omitted) |

### _handle_failure
**Change Description:** Currently fires webhook with `application_ref` and `error` as a plain string. Must be modified to include the structured `error_data` dict (with `code` and `message`) instead of just the string message.

**Dependants:** None

**Kind:** Function

**Requirements References**
- [webhook-review-data:FR-002]: Include structured error in failed webhook
- [webhook-review-data:FR-003]: Preserve existing `application_ref` field

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Structured error in failed payload | A review fails with scraper error | `_handle_failure` fires the webhook | Payload `data.error` is `{"code": "scraper_error", "message": "..."}` |
| TS-02 | Internal error code | A review fails with unexpected exception | The exception handler fires the webhook | Payload `data.error` is `{"code": "internal_error", "message": "..."}` |

### process_review (exception handler)
**Change Description:** The `except Exception` block currently fires a webhook with `error` as a plain string. Must be modified to include a structured error dict with `code: "internal_error"`.

**Dependants:** None

**Kind:** Function

**Requirements References**
- [webhook-review-data:FR-002]: Structured error in failed webhook

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Exception handler structured error | An unexpected exception is raised during review processing | Exception handler fires the webhook | Payload `data.error` is `{"code": "internal_error", "message": "<exception message>"}` |

---

## Added Components

N/A - No new components needed. Changes are to existing functions only.

---

## Used Components

### fire_webhook
**Location** `src/worker/webhook.py:101`

**Provides** Accepts a webhook config, event name, review_id, and data dict; serialises to JSON and delivers via HTTP POST with retries

**Used By** _handle_success, _handle_failure, process_review exception handler

### _serialize_application
**Location** `src/worker/review_jobs.py:267`

**Provides** Serialises ApplicationMetadata to dict with standard fields

**Used By** _handle_success (already used for review_data construction)

---

## Documentation Considerations
- deploy/.env.example should document the production deployment target
- MEMORY.md should be updated with production server info

---

## Instrumentation (if needed)

| Requirement | Observability Criteria | Implementation | Component |
|-------------|------------------------|----------------|-----------|
| [webhook-review-data:NFR-001] | Payload size and delivery time observable | Existing webhook delivery logging already records `status` and `attempt`; add `payload_size_bytes` to the "Webhook delivered" log entry | `_deliver_webhook` |

---

## Integration Test Scenarios (if needed)

| ID | Scenario | Given | When | Then | Components Involved |
|----|----------|-------|------|------|---------------------|
| ITS-01 | End-to-end completed webhook | A review completes via orchestrator mock | `_handle_success` is called with full ReviewResult | fire_webhook is called with enriched data; payload is JSON-serialisable and contains all required fields | _handle_success, fire_webhook, _serialize_application |
| ITS-02 | End-to-end failed webhook | A review fails with scraper error | `_handle_failure` is called | fire_webhook is called with structured error dict | _handle_failure, fire_webhook |

---

## E2E Test Scenarios (if needed)

N/A - E2E testing requires the full webhook delivery chain including the bbug-website, which is in a separate repo.

---

## Test Data
- Existing test fixtures for ReviewResult with populated application and review fields
- Edge case: ReviewResult with null optional fields (suggested_conditions, key_documents)

---

## Test Feasibility
- All tests can be written as unit tests mocking fire_webhook to inspect the data dict
- No missing infrastructure

---

## Risks and Dependencies
- **Payload size:** Large reviews with many aspects/documents could produce large payloads. Mitigated by NFR-001 (5MB limit check) and the fact that current review JSON is typically under 100KB.
- **Downstream consumer:** The bbug-website webhook handler must be updated to use the enriched payload instead of calling back. This is tracked separately in the bbug-website repo.

---

## Feasability Review
- No blockers. All changes are within a single file (`review_jobs.py`) with optional logging enhancement in `webhook.py`.

---

## Task Breakdown

> **CRITICAL: Tests are written WITH implementation, not after.**

### Phase 1: Enrich webhook payloads

- Task 1: Enrich `_handle_success` webhook payload with full review data
  - Status: Done
  - Modify `fire_webhook` call in `_handle_success` to include `application`, `review`, and `metadata` from the already-constructed `review_data` dict. Preserve existing `application_ref`, `overall_rating`, `review_url` fields.
  - Requirements: [webhook-review-data:FR-001], [webhook-review-data:FR-003], [webhook-review-data:NFR-002]
  - Test Scenarios: [webhook-review-data:_handle_success/TS-01], [webhook-review-data:_handle_success/TS-02], [webhook-review-data:ITS-01]

- Task 2: Enrich `_handle_failure` and exception handler webhook payloads with structured error
  - Status: Done
  - Modify `fire_webhook` calls in `_handle_failure` and `process_review` exception handler to pass structured `error_data` dict instead of plain string.
  - Requirements: [webhook-review-data:FR-002], [webhook-review-data:FR-003]
  - Test Scenarios: [webhook-review-data:_handle_failure/TS-01], [webhook-review-data:_handle_failure/TS-02], [webhook-review-data:process_review/TS-01], [webhook-review-data:ITS-02]

- Task 3: Add payload size logging to webhook delivery
  - Status: Done
  - Add `payload_size_bytes=len(payload_bytes)` to the "Webhook delivered" and "Webhook delivery failed" log entries in `_deliver_webhook`.
  - Requirements: [webhook-review-data:NFR-001]
  - Test Scenarios: N/A (observability-only, verified by log inspection)

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

- [webhook-review-data:FR-001]
  - Phase 1 Task 1
- [webhook-review-data:FR-002]
  - Phase 1 Task 2
- [webhook-review-data:FR-003]
  - Phase 1 Task 1
  - Phase 1 Task 2

- [webhook-review-data:NFR-001]
  - Phase 1 Task 3
- [webhook-review-data:NFR-002]
  - Phase 1 Task 1

---

## Test Scenario Validation

### Component Scenarios
- [webhook-review-data:_handle_success/TS-01]: Phase 1 Task 1
- [webhook-review-data:_handle_success/TS-02]: Phase 1 Task 1
- [webhook-review-data:_handle_failure/TS-01]: Phase 1 Task 2
- [webhook-review-data:_handle_failure/TS-02]: Phase 1 Task 2
- [webhook-review-data:process_review/TS-01]: Phase 1 Task 2

### Integration Scenarios
- [webhook-review-data:ITS-01]: Phase 1 Task 1
- [webhook-review-data:ITS-02]: Phase 1 Task 2

### E2E Scenarios
- N/A

---

## Appendix

### Glossary
- **Enriched payload:** Webhook payload that includes the full review data, eliminating the need for a callback
- **Callback:** HTTP request from the webhook consumer back to the Review Agent API

### References
- Current webhook module: `src/worker/webhook.py`
- Current review job handler: `src/worker/review_jobs.py`
- Website webhook handler: `bbug-website/src/app/api/webhooks/review-complete/route.ts`

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-13 | Claude | Initial design |
