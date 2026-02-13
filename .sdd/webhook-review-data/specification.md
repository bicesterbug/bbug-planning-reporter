# Specification: Webhook Review Data

**Version:** 1.1
**Date:** 2026-02-13
**Status:** Implemented

---

## Problem Statement

When a review completes, the worker sends a `review.completed` webhook to the bbug-website (Vercel). The webhook handler fetches the full review from the Agent API, then updates Strapi. Two issues cause failures: (1) The Strapi `title` and `application_address` columns are `varchar(255)`, but planning proposal descriptions can exceed 255 characters, causing `value too long for type character varying(255)` on the Strapi PUT and a cascading HTTP 500 back to the webhook delivery. (2) The webhook payload contains only minimal data, forcing an unnecessary callback round-trip from Vercel to the Agent API.

## Beneficiaries

**Primary:**
- BBUG admins who submit reviews and expect Strapi to be updated with review data automatically

**Secondary:**
- Operations/maintainers who must otherwise manually trigger review data sync

---

## Outcomes

**Must Haves**
- Strapi schema supports planning proposals and addresses longer than 255 characters
- Webhook `review.completed` payload includes all data the website needs to update Strapi without a callback
- Webhook `review.failed` payload includes structured error details sufficient for Strapi status update
- No breaking change to existing webhook consumers that ignore unknown fields

**Nice-to-haves**
- Production deployment target (`pete@192.168.1.18`) documented in project guidelines

---

## Explicitly Out of Scope

- Exposing the Review Agent API publicly (e.g. via tunnel or public domain)
- Changing the webhook transport or authentication mechanism
- Modifying the bbug-website webhook handler (separate repo; must be updated to consume enriched payload)
- Adding new webhook event types

---

## Functional Requirements

### FR-001: Enrich review.completed webhook payload
**Description:** The `review.completed` webhook event payload must include the full review data in its `data` field: `application` metadata (reference, address, proposal, applicant, status, consultation_end, documents_fetched) and `review` content (overall_rating, summary, key_documents, aspects, policy_compliance, recommendations, suggested_conditions, full_markdown), plus `metadata` (model, tokens, processing time, documents analysed, policy sources).

**Examples:**
- Positive case: Review completes successfully; webhook payload `data` includes `application`, `review`, and `metadata` objects alongside existing `application_ref`, `overall_rating`, `review_url`
- Edge case: Review completes but some review fields are null (e.g. no suggested_conditions); null fields are included as null in payload

### FR-002: Enrich review.failed webhook payload
**Description:** The `review.failed` webhook event payload `data` field must include a structured `error` object with `code` and `message` fields matching the error stored in Redis.

**Examples:**
- Positive case: Review fails with scraper error; webhook payload `data.error` is `{"code": "scraper_error", "message": "Scraper error: connection timeout"}`
- Edge case: Review fails with unexpected exception; `data.error` has `code: "internal_error"`

### FR-003: Backward compatibility
**Description:** Existing fields in the webhook payload (`application_ref`, `overall_rating`, `review_url` for completed; `application_ref`, `error` string for failed) must remain at their current positions. New fields are additive only.

**Examples:**
- Positive case: A consumer reading only `data.overall_rating` continues to work unchanged
- Negative case: The `data.application_ref` field must NOT be removed or renamed

### FR-004: Strapi schema supports long text fields
**Description:** The Strapi Planning content type `title` and `application_address` fields must use `text` type (unlimited length) instead of `string` (varchar(255)) to accommodate planning proposals and addresses that exceed 255 characters.

**Examples:**
- Positive case: A planning proposal of 318 characters is stored successfully in the `title` field
- Edge case: A very short proposal (10 chars) still works correctly with the text type

---

## Non-Functional Requirements

### NFR-001: Webhook payload size
**Category:** Performance
**Description:** The enriched webhook payload must be deliverable within the existing WEBHOOK_TIMEOUT (10s default). Payload size must not exceed 5MB.
**Acceptance Threshold:** 99% of review.completed webhooks deliver within 10 seconds
**Verification:** Observability (structured log of payload size and delivery duration)

### NFR-002: Serialisation safety
**Category:** Reliability
**Description:** All fields in the webhook payload must be JSON-serialisable. Non-serialisable values (datetime objects, custom types) must be converted to strings via the existing `default=str` JSON serialiser.
**Acceptance Threshold:** Zero serialisation errors in webhook delivery
**Verification:** Testing (unit tests with representative review data containing datetime fields)

---

## Open Questions

None.

---

## Appendix

### Glossary
- **Review Agent:** The bbug-planning-reporter system that processes planning application reviews
- **Webhook:** HTTP POST callback sent to a configured URL when a review lifecycle event occurs
- **Strapi:** CMS used by bbug-website to store planning review data
- **Vercel:** Cloud hosting platform where the bbug-website runs

### References
- Worker webhook module: `src/worker/webhook.py`
- Review job handler: `src/worker/review_jobs.py`
- Website webhook handler: `bbug-website/src/app/api/webhooks/review-complete/route.ts`
- Website agent client: `bbug-website/src/lib/admin/reviewAgentClient.ts`
- Strapi planning schema: `bbug-strapi/src/api/planning/content-types/planning/schema.json`

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-13 | Claude | Initial specification (incorrect root cause diagnosis) |
| 1.1 | 2026-02-13 | Claude | Corrected root cause: Strapi varchar(255) overflow, not network issue. Added FR-004. |
