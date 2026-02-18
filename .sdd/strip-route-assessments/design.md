# Design: Strip Route Assessments from Review Output

**Version:** 1.0
**Date:** 2026-02-18
**Status:** Implemented
**Linked Specification** `.sdd/strip-route-assessments/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The review pipeline produces route assessment data in the orchestrator (`self._route_assessments`) and embeds it in the `review` dict at key `route_assessments`. This dict flows unchanged through:
1. `_handle_success()` → `review_data["review"]` → `_upload_review_output()` → `_review.json` file
2. `_handle_success()` → `redis_wrapper.store_result()` → Redis → API GET endpoint
3. `_handle_success()` → `fire_webhook("review.completed")` → webhook payload
4. `_upload_review_output()` also extracts `route_assessments` separately → `_routes.json` file

The route data is therefore duplicated: once in `_review.json`/Redis/webhooks and once in `_routes.json`.

### Proposed Architecture

Pop `route_assessments` from the review dict in `_handle_success()` before any downstream consumption. Pass the extracted data explicitly to `_upload_review_output()` for the routes JSON file. This single extraction point ensures all downstream consumers (file, Redis, webhooks) see the review without route_assessments.

Remove `route_assessments`, `RouteAssessment`, and `RouteData` from the API schema since the field will always be absent.

### Technology Decisions

No new technology. Pure data-flow change in existing Python code.

---

## Modified Components

### Modified: `_handle_success()` (`src/worker/review_jobs.py`)

**Change Description:** Currently builds `review_data` from `result.review` which contains `route_assessments`. Change to pop `route_assessments` from the review dict before building `review_data`, and pass it explicitly to `_upload_review_output()`.

**Dependants:** `_upload_review_output()` signature changes.

**Kind:** Function

**Details**

After building `review_data`, pop `route_assessments` from `review_data["review"]` into a local variable. Pass it as a parameter to `_upload_review_output()`. All subsequent consumers (`store_result`, `fire_webhook`) see the review without `route_assessments`.

**Requirements References**
- [strip-route-assessments:FR-001]: Stripping before file upload
- [strip-route-assessments:FR-002]: Stripping before Redis storage
- [strip-route-assessments:FR-003]: Stripping before webhook fire

**Test Scenarios**

**TS-01: Review data stored in Redis has no route_assessments**
- Given: A successful review result with `route_assessments` in the review dict
- When: `_handle_success()` stores the result
- Then: The data passed to `store_result()` has no `route_assessments` key in `review`

**TS-02: Webhook payload has no route_assessments**
- Given: A successful review result with `route_assessments` in the review dict
- When: `_handle_success()` fires the webhook
- Then: The `data.review` in the `review.completed` webhook has no `route_assessments` key

---

### Modified: `_upload_review_output()` (`src/worker/review_jobs.py`)

**Change Description:** Currently extracts `route_assessments` from `review_data["review"]`. Change to accept `route_assessments` as an explicit parameter since it has been popped from `review_data` before this function is called.

**Dependants:** None (only called by `_handle_success`).

**Kind:** Function

**Details**

Add `route_assessments: list[dict] | None` parameter. Use this parameter directly instead of extracting from `review_data`. The review JSON file is written from `review_data` (which no longer contains `route_assessments`). The routes JSON file is written from the explicit parameter.

**Requirements References**
- [strip-route-assessments:FR-001]: Review JSON file has no route_assessments

**Test Scenarios**

**TS-03: Review JSON file does not contain route_assessments**
- Given: `review_data` with no `route_assessments` in review, and route data passed as separate param
- When: `_upload_review_output()` writes files
- Then: The `_review.json` content has no `route_assessments` key in `review`

**TS-04: Routes JSON file still contains route data**
- Given: Route assessments passed as separate parameter
- When: `_upload_review_output()` writes files
- Then: The `_routes.json` file contains the route assessment array

---

### Modified: `ReviewContent` (`src/api/schemas.py`)

**Change Description:** Remove the `route_assessments` field. Remove `RouteAssessment` and `RouteData` models. Remove their re-exports from `src/api/schemas/__init__.py`.

**Dependants:** Tests importing these models.

**Kind:** Pydantic Model

**Requirements References**
- [strip-route-assessments:FR-004]: API schema no longer declares route_assessments

**Test Scenarios**

**TS-05: ReviewContent has no route_assessments attribute**
- Given: A ReviewContent instance
- When: Checking its fields
- Then: No `route_assessments` attribute exists

**TS-06: Old data with route_assessments still parses**
- Given: A dict with `route_assessments` key passed to ReviewContent
- When: Pydantic parses the dict
- Then: No error raised (extra fields ignored), field not present on model

---

### Modified: API Docs (`docs/API.md`)

**Change Description:** Remove `route_assessments` from the review response example. Add a note that route data is available at `output_urls.routes_json`.

**Dependants:** None.

**Kind:** Documentation

**Requirements References**
- [strip-route-assessments:FR-004]: Documentation reflects removal

---

## Used Components

### StorageBackend (`src/shared/storage.py`)

**Location:** `src/shared/storage.py`

**Provides:** `upload()` and `public_url()` methods for writing files to S3/local storage.

**Used By:** `_upload_review_output()` — unchanged, just receives different data.

### InMemoryStorageBackend (`src/shared/storage.py`)

**Location:** `src/shared/storage.py`

**Provides:** In-memory storage backend for testing file upload behaviour.

**Used By:** Test scenarios TS-03, TS-04.

---

## Documentation Considerations

- Update `docs/API.md` to remove `route_assessments` from the review response example and note that route data is at `output_urls.routes_json`

---

## QA Feasibility

**QA-01 (Review JSON file stripped):** Fully testable. Submit review, download file, check contents.

**QA-02 (Routes JSON unchanged):** Fully testable. Same review, download routes file.

**QA-03 (API response stripped):** Fully testable. Same review, GET endpoint, check response.

---

## Risks and Dependencies

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Vercel website expects `route_assessments` in webhook | Low | Medium | Website already uses `route_narrative` for display; `route_assessments` was raw MCP data not rendered on the site |
| Old Redis data has `route_assessments` | Low | None | Pydantic ignores extra fields; no migration needed |

---

## Task Breakdown

### Phase 1: Strip route_assessments from pipeline

**Task 1: Strip route_assessments in _handle_success and _upload_review_output**
- Status: Backlog
- Requirements: [strip-route-assessments:FR-001], [strip-route-assessments:FR-002], [strip-route-assessments:FR-003]
- Test Scenarios: [strip-route-assessments:_handle_success/TS-01], [strip-route-assessments:_handle_success/TS-02], [strip-route-assessments:_upload_review_output/TS-03], [strip-route-assessments:_upload_review_output/TS-04]
- Details: Pop `route_assessments` from `review_data["review"]` in `_handle_success()`. Pass as explicit param to `_upload_review_output()`. Update existing tests.

**Task 2: Remove route_assessments from API schema and docs**
- Status: Backlog
- Requirements: [strip-route-assessments:FR-004]
- Test Scenarios: [strip-route-assessments:ReviewContent/TS-05], [strip-route-assessments:ReviewContent/TS-06]
- Details: Remove `route_assessments` from `ReviewContent`, remove `RouteAssessment` and `RouteData` models and re-exports, update `docs/API.md`.

---

## Appendix

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-18 | Claude | Initial design |
