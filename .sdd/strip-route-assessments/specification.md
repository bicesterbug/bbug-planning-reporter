# Specification: Strip Route Assessments from Review Output

**Version:** 1.0
**Date:** 2026-02-18
**Status:** Implemented

---

## Problem Statement

The `route_assessments` field (containing full segment-level cycling route data, ~500KB) is duplicated across the review JSON file, the API response, the Redis-stored result, and webhook payloads. This data already has a dedicated `_routes.json` file. Including it in the review output bloats the review JSON from ~65KB to ~565KB and forces consumers to download route data they may not need.

## Beneficiaries

**Primary:**
- Website consumers (Vercel) — smaller payloads, faster page loads
- API consumers — cleaner review response without embedded route data

**Secondary:**
- S3 storage — reduced file sizes

---

## Outcomes

**Must Haves**
- Route assessments data is only available in `_routes.json` and via `output_urls.routes_json`
- Review JSON file, API response, and webhook payloads no longer contain `route_assessments`

**Nice-to-haves**
- None

---

## Explicitly Out of Scope

- Changing the `_routes.json` file format or content
- Changing the `route_narrative` field (LLM-generated prose summaries — these stay in the review)
- Changing how the orchestrator collects route assessment data internally

---

## Functional Requirements

**FR-001: Strip route_assessments from review JSON file**
- Description: The `_review.json` file uploaded to S3 must not contain the `route_assessments` field within the `review` object.
- Acceptance criteria: After a review completes, the `_review.json` file's `review` object has no `route_assessments` key. The `_routes.json` file still contains the route assessment data.
- Failure/edge cases: When no route assessments exist, both files work as before (review JSON has no field, routes JSON has `[]`).

**FR-002: Strip route_assessments from Redis-stored result**
- Description: The `review_data` stored in Redis via `store_result()` must not contain `route_assessments` in the `review` object.
- Acceptance criteria: The Redis-stored review result has no `route_assessments` key in its `review` field. The API GET `/reviews/{id}` response reflects this.
- Failure/edge cases: Existing reviews already stored in Redis with `route_assessments` will still deserialise (Pydantic allows extra fields or the field is removed from the model).

**FR-003: Strip route_assessments from webhook payloads**
- Description: The `review.completed` webhook payload must not contain `route_assessments` in its `review` data.
- Acceptance criteria: The webhook `data.review` object has no `route_assessments` key.
- Failure/edge cases: None — webhooks are fire-and-forget.

**FR-004: Remove route_assessments from API schema**
- Description: The `ReviewContent` Pydantic model must no longer declare the `route_assessments` field. The `RouteAssessment` and `RouteData` models can be removed from the API schemas if no longer referenced elsewhere.
- Acceptance criteria: The `ReviewContent` model has no `route_assessments` attribute. The API docs reflect the removal.
- Failure/edge cases: Old reviews fetched from Redis that contain `route_assessments` must not cause deserialisation errors — Pydantic ignores extra fields by default.

---

## QA Plan

**QA-01: Verify review JSON file stripped**
- Goal: Confirm `_review.json` no longer contains route data
- Steps:
  1. Submit a review for an application with route destinations configured
  2. Wait for completion
  3. Download the `_review.json` from the output URL
  4. Inspect the JSON
- Expected: `review` object has `route_narrative` but no `route_assessments`. File size is significantly smaller (~65KB not ~565KB).

**QA-02: Verify routes JSON file unchanged**
- Goal: Confirm `_routes.json` still contains the full route data
- Steps:
  1. Same review as QA-01
  2. Download the `_routes.json` from the output URL
- Expected: Contains the full route assessment array with segment data.

**QA-03: Verify API response stripped**
- Goal: Confirm GET `/api/v1/reviews/{id}` no longer returns route_assessments
- Steps:
  1. Same review as QA-01
  2. GET the review via API
- Expected: Response `review` object has no `route_assessments` field. Has `output_urls.routes_json` URL.

---

## Open Questions

None.

---

## Appendix

### References
- [route-narrative-report spec](.sdd/route-narrative-report/specification.md)
- [cycle-route-assessment spec](.sdd/cycle-route-assessment/specification.md)

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-18 | Claude | Initial specification |
