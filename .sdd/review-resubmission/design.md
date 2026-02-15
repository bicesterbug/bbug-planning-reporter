# Design: Review Resubmission

**Version:** 1.0
**Date:** 2026-02-15
**Status:** Draft
**Linked Specification:** `.sdd/review-resubmission/specification.md`

---

## Architecture Overview

### Current Architecture Context

Review submissions flow through: API route (`submit_review`) → Redis job store → arq worker → `AgentOrchestrator.run()`. The orchestrator runs an 8-phase workflow including document download from the Cherwell portal and upload to S3.

The current `submit_review` endpoint calls `has_active_job_for_ref()` which returns a boolean. If any QUEUED/PROCESSING job exists for the `application_ref`, it returns 409 — but this also blocks resubmission after a review is completed, since the check is the only gate.

Actually, re-reading the code: `has_active_job_for_ref` only checks for QUEUED/PROCESSING. The current 409 uses error code `review_already_exists`, which implies it was designed to block all duplicates. But it only blocks active ones. So currently, resubmission of terminal-state reviews already works at the API level — the issue is there's no document reuse, manifest, or superseded handling.

Wait — re-reading `submit_review` more carefully: it calls `has_active_job_for_ref` which only checks QUEUED/PROCESSING. So terminal-state reviews don't block. Let me verify this is correct by checking the actual behavior.

Correction after re-reading: The current `has_active_job_for_ref` (line 253-281 of redis_client.py) ONLY returns True for QUEUED/PROCESSING. So resubmission of completed reviews already goes through — it just treats every submission as completely fresh (re-downloads everything, no manifest, no previous review link). The changes needed are:

1. **API layer**: Return the active `review_id` in the 409 error details + add `force=true` to cancel-and-restart
2. **Orchestrator**: Add manifest persistence, document reuse from S3, superseded exclusion, and metadata about the previous review

### Proposed Architecture

1. **API layer** — Modify `submit_review` to return the active review_id in 409 details, add `force` query param that cancels the active review before proceeding. Find the most recent completed review to pass `previous_review_id` to the worker.

2. **Worker layer** — Thread `previous_review_id` through to the orchestrator.

3. **Orchestrator download phase** — Load previous manifest from S3 (if it exists), compare against freshly-fetched portal document list, download reused docs from S3 and new docs from Cherwell, persist a new manifest to S3 after download.

4. **Document filter** — Add "superseded" to the category denylist so documents under superseded sections or with "superseded" in the title are excluded.

---

## API Design

### Modified: POST /api/v1/reviews

**New query parameter:** `force` (boolean, default false)

**Changed behaviour:**
- When an active review exists and `force=false`: returns 409 with error code `review_in_progress` (changed from `review_already_exists`) and includes `review_id` of the active review in error details.
- When an active review exists and `force=true`: cancels the active review, then proceeds with submission.
- When submitting for a ref with a previous completed review: the worker receives `previous_review_id` so the orchestrator can load the manifest.

**409 response shape:**
```json
{
  "error": {
    "code": "review_in_progress",
    "message": "A review for application 25/00413/F is already processing",
    "details": {
      "application_ref": "25/00413/F",
      "active_review_id": "rev_01HQXK..."
    }
  }
}
```

---

## Modified Components

### Modified: `submit_review` endpoint

**File:** `src/api/routes/reviews.py`

**Change Description:** Currently calls `has_active_job_for_ref()` (bool) and returns 409. Change to: (1) call a new method that returns the active review_id, (2) handle `force` param to cancel-and-restart, (3) find previous completed review_id and pass it to the worker job.

**Dependants:** None

**Kind:** Function

**Details:**
- Add `force: bool = Query(False)` parameter
- Replace `has_active_job_for_ref()` with `get_active_review_id_for_ref()` which returns `str | None`
- If active review exists and `force=true`: cancel via `update_job_status(active_id, CANCELLED)`
- If active review exists and `force=false`: raise 409 with active review_id in details
- Call `get_latest_completed_review_id_for_ref()` to find `previous_review_id`
- Pass `previous_review_id` as extra kwarg to arq job

**Requirements References:**
- [review-resubmission:FR-001]: Allows resubmission when terminal
- [review-resubmission:FR-002]: Returns 409 with active review_id, force cancels

**Test Scenarios**

**TS-01: Resubmission accepted when previous review completed**
- Given: A completed review exists for application ref `25/00413/F`
- When: POST `/api/v1/reviews` with the same `application_ref`
- Then: Returns 202 with a new `review_id`

**TS-02: 409 with active review_id when review is processing**
- Given: A processing review `rev_active` exists for ref `25/00413/F`
- When: POST `/api/v1/reviews` with the same `application_ref` (no force)
- Then: Returns 409 with error code `review_in_progress` and `active_review_id: "rev_active"` in details

**TS-03: Force cancels active review and starts new**
- Given: A processing review `rev_active` exists for ref `25/00413/F`
- When: POST `/api/v1/reviews?force=true` with the same `application_ref`
- Then: Returns 202 with new `review_id`. The old review status is `cancelled`.

**TS-04: Previous review_id passed to worker job**
- Given: A completed review `rev_previous` exists for ref `25/00413/F`
- When: POST `/api/v1/reviews` with the same `application_ref`
- Then: The arq job is enqueued with `previous_review_id="rev_previous"`

---

### Modified: `RedisClient`

**File:** `src/shared/redis_client.py`

**Change Description:** Currently has `has_active_job_for_ref()` (returns bool). Add two new query methods: one that returns the active review_id (not just bool), and one that finds the most recent completed review_id for a ref.

**Dependants:** `submit_review` endpoint

**Kind:** Class methods

**Details:**
- `get_active_review_id_for_ref(application_ref) -> str | None`: Like `has_active_job_for_ref` but returns the review_id instead of bool.
- `get_latest_completed_review_id_for_ref(application_ref) -> str | None`: Iterates jobs for the ref, finds the most recent with COMPLETED status (by `completed_at`).

**Requirements References:**
- [review-resubmission:FR-001]: Need to find previous completed review
- [review-resubmission:FR-002]: Need active review_id for 409 details and force cancel

**Test Scenarios**

**TS-05: Returns active review_id when one exists**
- Given: A PROCESSING review `rev_123` exists for ref `25/00413/F`
- When: `get_active_review_id_for_ref("25/00413/F")` is called
- Then: Returns `"rev_123"`

**TS-06: Returns None when no active review**
- Given: Only COMPLETED reviews exist for ref `25/00413/F`
- When: `get_active_review_id_for_ref("25/00413/F")` is called
- Then: Returns None

**TS-07: Returns latest completed review_id**
- Given: Two completed reviews for ref `25/00413/F`, `rev_old` (completed 1pm) and `rev_new` (completed 2pm)
- When: `get_latest_completed_review_id_for_ref("25/00413/F")` is called
- Then: Returns `"rev_new"`

**TS-08: Returns None when no completed reviews**
- Given: Only a FAILED review exists for ref `25/00413/F`
- When: `get_latest_completed_review_id_for_ref("25/00413/F")` is called
- Then: Returns None

---

### Modified: `DocumentFilter`

**File:** `src/mcp_servers/cherwell_scraper/filters.py`

**Change Description:** Add "superseded" patterns to the category denylist and title denylist so documents under superseded sections or with "superseded" in descriptions are excluded.

**Dependants:** None (filter is already applied during orchestrator's document selection phase)

**Kind:** Class constants

**Details:**
- Add new class constant `CATEGORY_DENYLIST_SUPERSEDED` with entries like `"superseded documents"`, `"superseded"`.
- Add `"superseded"` to `DENYLIST_NON_TRANSPORT_PATTERNS` for title-based matching.
- Update `filter_document()` to check superseded category denylist (always denied, no toggle).

**Requirements References:**
- [review-resubmission:FR-005]: Superseded document exclusion

**Test Scenarios**

**TS-09: Document under superseded category is excluded**
- Given: A document with `document_type` "Superseded Documents"
- When: `filter_document()` is called
- Then: Returns `FilterResult` with `allowed=False` and reason indicating superseded

**TS-10: Document with "superseded" in title is excluded**
- Given: A document with description "Superseded Transport Assessment v1"
- When: `filter_document()` is called
- Then: Returns `FilterResult` with `allowed=False`

**TS-11: Non-superseded documents unaffected**
- Given: A document with `document_type` "Supporting Documents" and description "Transport Assessment"
- When: `filter_document()` is called
- Then: Returns `FilterResult` with `allowed=True` (existing behaviour preserved)

---

### Modified: `process_review` worker function

**File:** `src/worker/review_jobs.py`

**Change Description:** Accept and thread `previous_review_id` from the arq job kwargs to the orchestrator.

**Dependants:** `AgentOrchestrator`

**Kind:** Function

**Details:**
- Add `previous_review_id: str | None = None` parameter to `process_review()` and `review_job()`.
- Pass it to `AgentOrchestrator(previous_review_id=previous_review_id)`.

**Requirements References:**
- [review-resubmission:FR-004]: Orchestrator needs previous_review_id to find manifest
- [review-resubmission:FR-006]: Orchestrator records previous_review_id in metadata

**Test Scenarios**

**TS-12: Previous review_id threaded to orchestrator**
- Given: Worker receives job with `previous_review_id="rev_prev"`
- When: `process_review()` creates the orchestrator
- Then: Orchestrator is constructed with `previous_review_id="rev_prev"`

---

### Modified: `AgentOrchestrator`

**File:** `src/agent/orchestrator.py`

**Change Description:** Add `previous_review_id` parameter, manifest persistence after download, document reuse from S3 when manifest exists, and resubmission metadata tracking.

**Dependants:** None

**Kind:** Class

**Details:**

Constructor changes:
- New parameter: `previous_review_id: str | None = None`
- New state: `_previous_review_id`, `_resubmission_stats` (dict with `documents_reused`, `documents_new`, `documents_removed` counts)

`_phase_download_documents` changes:
1. Before downloading, if `_previous_review_id` is set and storage is remote, attempt to load manifest from S3 at key `{safe_ref}/manifest.json` using `storage.download_to()` and parse JSON.
2. Build a lookup `manifest_by_id: dict[document_id, manifest_entry]` from the previous manifest.
3. For each selected document:
   - If `document_id` is in `manifest_by_id`: download from S3 via `storage.download_to(entry["s3_key"], local_path)`, update metadata with S3 URL. Log "Reusing document from S3".
   - If S3 download fails: fall back to Cherwell download.
   - If not in manifest: download from Cherwell (existing logic).
4. Track counts: `documents_reused`, `documents_new`, `documents_removed` (manifest entries not in selected documents).
5. After all downloads: compute file hash for each downloaded doc, build manifest JSON, upload to S3 at `{safe_ref}/manifest.json`.

`run()` or result-building changes:
- Include `previous_review_id`, `documents_reused`, `documents_new`, `documents_removed` in `ReviewResult.metadata`.

**Requirements References:**
- [review-resubmission:FR-003]: Manifest persistence to S3
- [review-resubmission:FR-004]: Document reuse from S3
- [review-resubmission:FR-006]: Previous review reference in metadata
- [review-resubmission:NFR-001]: S3 downloads are faster than Cherwell

**Test Scenarios**

**TS-13: Manifest persisted to S3 after download**
- Given: Orchestrator completes download phase with 3 documents
- When: Download phase finishes
- Then: A file at `{safe_ref}/manifest.json` is uploaded to storage containing 3 document entries with `document_id`, `s3_key`, `file_hash`, `cherwell_url`

**TS-14: Documents reused from S3 when manifest exists**
- Given: Previous manifest in S3 with documents A (id=abc) and B (id=def). Selected documents include A (id=abc) and C (id=ghi, new).
- When: Download phase runs
- Then: Document A is downloaded from S3 (`storage.download_to`), document C from Cherwell (`mcp_client.call_tool`). Stats: reused=1, new=1, removed=1 (B).

**TS-15: Fallback to Cherwell on S3 download failure**
- Given: Previous manifest with document A. S3 `download_to` raises an exception.
- When: Download phase runs for document A
- Then: Falls back to downloading from Cherwell. Document still appears in result.

**TS-16: No manifest treated as fresh review**
- Given: `previous_review_id` is set but no manifest exists in S3
- When: Download phase runs
- Then: All documents downloaded from Cherwell (existing behaviour). Stats: reused=0, new=N, removed=0.

**TS-17: Manifest malformed treated as fresh review**
- Given: `previous_review_id` is set but manifest JSON is invalid
- When: Download phase runs
- Then: All documents downloaded from Cherwell. Warning logged.

**TS-18: Resubmission metadata included in result**
- Given: Orchestrator completes with `previous_review_id="rev_old"`, 2 reused, 1 new, 1 removed
- When: Review result is built
- Then: `metadata` contains `previous_review_id="rev_old"`, `documents_reused=2`, `documents_new=1`, `documents_removed=1`

**TS-19: Fresh review metadata has null previous_review_id**
- Given: Orchestrator runs without `previous_review_id`
- When: Review result is built
- Then: `metadata` contains `previous_review_id=None`, all documents counted as new

---

## Used Components

### StorageBackend.download_to()
**Location:** `src/shared/storage.py`

**Provides:** Downloads an S3 object to a local file path. Used to fetch reused documents from S3 instead of Cherwell, and to download the previous manifest.

**Used By:** Modified `AgentOrchestrator._phase_download_documents()`

### StorageBackend.upload()
**Location:** `src/shared/storage.py`

**Provides:** Uploads a local file to S3. Used to persist the document manifest.

**Used By:** Modified `AgentOrchestrator._phase_download_documents()`

### DocumentFilter
**Location:** `src/mcp_servers/cherwell_scraper/filters.py`

**Provides:** Category and title-based document filtering. Already applied in orchestrator's `_post_filter_consultation_documents()`. The superseded denylist will be checked in the same filter flow.

**Used By:** Already used by orchestrator via `_post_filter_consultation_documents()`

---

## Documentation Considerations

- Update `docs/API.md` with `force` query parameter on POST reviews endpoint and the changed 409 response shape

---

## Integration Test Scenarios

**ITS-01: Full resubmission flow with document reuse**
- Given: A completed review with manifest in S3, containing 3 documents
- When: A new review is submitted for the same application_ref, the portal still lists the same 3 documents
- Then: All 3 documents are fetched from S3 (not Cherwell). A new manifest is written. Result metadata shows `documents_reused=3, documents_new=0, documents_removed=0`.
- Components Involved: submit_review, worker, orchestrator, storage backend

---

## Risks and Dependencies

| Risk | Impact | Mitigation |
|------|--------|------------|
| Manifest JSON corrupted or from incompatible version | Review fails to load manifest | Catch JSON decode errors, treat as fresh review |
| S3 document deleted but manifest references it | Download fails for reused doc | Fall back to Cherwell download on any S3 error |
| Document content changed but document_id unchanged (same URL) | Stale document used | Acceptable — Cherwell document_ids are URL-derived; content changes typically get new URLs |
| Race condition: two force=true requests simultaneously | Both try to cancel same review | Second submission sees already-cancelled review, proceeds normally |
| Large manifests for applications with 100+ documents | Manifest upload/download time | Manifest is small JSON (< 100KB even for 1000 docs), negligible |

---

## QA Feasibility

**QA-01 (Resubmit completed review):** Fully testable — submit review, wait, resubmit, check both reviews.

**QA-02 (Reject while active):** Fully testable — submit and immediately resubmit.

**QA-03 (Force override):** Fully testable — submit, force resubmit, check cancelled + new.

**QA-04 (Document reuse from S3):** Testable via worker logs. Check for "Reusing document from S3" messages and compare download phase duration.

**QA-05 (Superseded exclusion):** Requires real portal with superseded docs. If unavailable, verified via unit tests (TS-09, TS-10).

---

## Task Breakdown

### Phase 1: API + Redis changes

**Task 1: Redis query methods for active/previous review lookup**
- Status: Done
- Requirements: [review-resubmission:FR-001], [review-resubmission:FR-002]
- Test Scenarios: [review-resubmission:RedisClient/TS-05], [review-resubmission:RedisClient/TS-06], [review-resubmission:RedisClient/TS-07], [review-resubmission:RedisClient/TS-08]
- Details:
  - Add `get_active_review_id_for_ref(application_ref) -> str | None` to RedisClient
  - Add `get_latest_completed_review_id_for_ref(application_ref) -> str | None` to RedisClient
  - Add tests in `tests/test_shared/test_redis_client.py`

**Task 2: Submit review resubmission logic with force parameter**
- Status: Done
- Requirements: [review-resubmission:FR-001], [review-resubmission:FR-002]
- Test Scenarios: [review-resubmission:submit_review/TS-01], [review-resubmission:submit_review/TS-02], [review-resubmission:submit_review/TS-03], [review-resubmission:submit_review/TS-04]
- Details:
  - Add `force: bool = Query(False)` to `submit_review`
  - Replace `has_active_job_for_ref()` with `get_active_review_id_for_ref()`
  - Handle force cancel and find previous review_id
  - Pass `previous_review_id` to arq job
  - Add tests in `tests/test_api/test_reviews.py`

### Phase 2: Filter + Orchestrator changes

**Task 3: Superseded document exclusion in DocumentFilter**
- Status: Done
- Requirements: [review-resubmission:FR-005]
- Test Scenarios: [review-resubmission:DocumentFilter/TS-09], [review-resubmission:DocumentFilter/TS-10], [review-resubmission:DocumentFilter/TS-11]
- Details:
  - Add `CATEGORY_DENYLIST_SUPERSEDED` constant with `"superseded documents"`, `"superseded"`
  - Add `"superseded"` to `DENYLIST_NON_TRANSPORT_PATTERNS`
  - Update `filter_document()` to check superseded denylist (always denied, no toggle)
  - Add tests in `tests/test_mcp_servers/test_cherwell_scraper/test_filters.py`

**Task 4: Thread previous_review_id from API to orchestrator**
- Status: Done
- Requirements: [review-resubmission:FR-004], [review-resubmission:FR-006]
- Test Scenarios: [review-resubmission:process_review/TS-12]
- Details:
  - Add `previous_review_id` param to `review_job()` and `process_review()` in `src/worker/review_jobs.py`
  - Add `previous_review_id` param to `AgentOrchestrator.__init__()`
  - Add test in `tests/test_worker/test_review_jobs.py`

**Task 5: Document manifest persistence and reuse**
- Status: Done
- Requirements: [review-resubmission:FR-003], [review-resubmission:FR-004], [review-resubmission:FR-006], [review-resubmission:NFR-001]
- Test Scenarios: [review-resubmission:AgentOrchestrator/TS-13], [review-resubmission:AgentOrchestrator/TS-14], [review-resubmission:AgentOrchestrator/TS-15], [review-resubmission:AgentOrchestrator/TS-16], [review-resubmission:AgentOrchestrator/TS-17], [review-resubmission:AgentOrchestrator/TS-18], [review-resubmission:AgentOrchestrator/TS-19], [review-resubmission:ITS-01]
- Details:
  - In `_phase_download_documents`: load manifest from S3 if `_previous_review_id` set
  - Compare selected documents against manifest by `document_id`
  - Reused docs: `storage.download_to()`, fallback to Cherwell on failure
  - New docs: download from Cherwell (existing logic)
  - After downloads: compute SHA256 hash per file, build manifest JSON, upload to S3
  - Track `_resubmission_stats` dict and include in `ReviewResult.metadata`
  - Add tests in `tests/test_agent/test_orchestrator.py`

**Task 6: Update API documentation**
- Status: Done
- Requirements: [review-resubmission:FR-002]
- Test Scenarios: None (documentation only)
- Details:
  - Update `docs/API.md` with `force` query parameter on POST reviews
  - Document changed 409 response shape with `active_review_id`
  - Document `previous_review_id` and document change counts in metadata

---

## Intermediate Dead Code Tracking

None expected — all code is used within the same phase it's introduced.

---

## Intermediate Stub Tracking

None expected.

---

## Appendix

### Manifest JSON schema
```json
{
  "review_id": "rev_xxx",
  "application_ref": "25/00413/F",
  "created_at": "2026-02-15T12:00:00Z",
  "documents": [
    {
      "document_id": "abc123def456",
      "description": "Transport Assessment",
      "document_type": "Supporting Documents",
      "date_published": "2025-01-15",
      "s3_key": "25_00413_F/001_Transport_Assessment.pdf",
      "file_hash": "sha256:abcdef...",
      "cherwell_url": "https://planningregister.cherwell.gov.uk/Document/Download?..."
    }
  ]
}
```

### References
- [Specification](specification.md)
- `src/api/routes/reviews.py` — Submit review endpoint
- `src/shared/redis_client.py` — Redis query methods
- `src/mcp_servers/cherwell_scraper/filters.py` — Document filter
- `src/worker/review_jobs.py` — Worker job processing
- `src/agent/orchestrator.py` — Agent orchestration

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-15 | SDD | Initial design |
