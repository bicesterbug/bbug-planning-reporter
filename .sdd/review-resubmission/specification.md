# Specification: Review Resubmission

**Version:** 1.0
**Date:** 2026-02-15
**Status:** Draft

---

## Problem Statement

When a planning application's document set changes after a review has been completed (new documents added, existing documents superseded), users have no way to refresh the review. Submitting the same application reference is rejected with a 409 because a completed review already exists. Users must manually work around this by deleting Redis state, losing the previous review entirely, and re-downloading all documents from the Cherwell portal even though most haven't changed.

## Beneficiaries

**Primary:**
- Website operators (Bicester BUG) who need to keep reviews current as applications evolve through the planning process

**Secondary:**
- End users reading reviews on the website, who benefit from reviews that reflect the latest document set

---

## Outcomes

**Must Haves**
- Users can resubmit a review for an application that already has a completed, failed, or cancelled review
- The system reuses documents from S3 that haven't changed, only downloading new documents from the Cherwell portal
- Documents no longer listed on the portal or marked/titled as "superseded" are excluded from the new review
- Non-terminal reviews (queued/processing) return 409 unless explicitly forced
- A document manifest is persisted to S3 alongside review documents so resubmission can identify reusable documents
- Each resubmission creates a new review_id, preserving the previous review as history

**Nice-to-haves**
- ChromaDB embedding reuse for unchanged documents (already handled by existing idempotency via file hash)

---

## Explicitly Out of Scope

- Exporting/importing embeddings to/from S3 (ChromaDB's hash-based idempotency already skips re-ingestion for unchanged documents; S3 embedding cache is a future optimisation for disaster recovery only)
- Automatic resubmission (scheduled/triggered by portal changes)
- Diffing or highlighting what changed between reviews
- Backfilling document manifests for reviews completed before this feature ships
- Merging partial results from a failed review into a resubmission

---

## Functional Requirements

**FR-001: Resubmission of terminal-state reviews**
- Description: When a POST to `/api/v1/reviews` specifies an `application_ref` that has an existing review in a terminal state (completed, failed, or cancelled), the system accepts the request, creates a new review with a fresh `review_id`, and queues it for processing.
- Acceptance criteria: A POST with a previously-reviewed `application_ref` returns 202 with a new `review_id`. The previous review remains accessible via its original `review_id`.
- Failure/edge cases: If multiple terminal reviews exist for the same ref, the system uses the most recent completed review's manifest (falling back to the most recent of any terminal state). If no manifest exists in S3 (pre-feature review), the review proceeds as a fresh submission with no document reuse.

**FR-002: Conflict on non-terminal reviews with force override**
- Description: When a POST specifies an `application_ref` that has an active review (queued or processing), the system returns 409 Conflict. If the request includes `force=true` query parameter, the system cancels the active review and accepts the new submission.
- Acceptance criteria: POST without `force` returns 409 with error code `review_in_progress` and includes the active `review_id` in error details. POST with `force=true` cancels the active review (status set to `cancelled`) and returns 202 with a new `review_id`.
- Failure/edge cases: If the active review completes between the check and the cancellation attempt, the system proceeds with normal resubmission (no error). Race conditions between concurrent force requests for the same ref should result in only one review being queued.

**FR-003: Document manifest persistence to S3**
- Description: After the download phase completes, the orchestrator persists a JSON document manifest to S3 at a well-known key within the application's directory. The manifest records each document's Cherwell `document_id`, description, `document_type`, `date_published`, S3 key, file hash, and download URL.
- Acceptance criteria: After a successful download phase, a file `{app_ref}/manifest.json` exists in S3 containing an array of document records. Each record includes at minimum: `document_id`, `description`, `document_type`, `s3_key`, `file_hash`, `cherwell_url`. The manifest is overwritten on each review for that application.
- Failure/edge cases: If manifest upload fails, the review continues (manifest is an optimisation, not critical). Logged as a warning.

**FR-004: Document reuse from S3 on resubmission**
- Description: When processing a resubmission, the orchestrator fetches the fresh document list from the Cherwell portal and compares it against the previous manifest from S3. Documents present in both lists (matched by `document_id`) are downloaded from S3 instead of the Cherwell portal. New documents (in portal list but not in manifest) are downloaded from Cherwell. Removed documents (in manifest but not in portal list) are excluded from the review.
- Acceptance criteria: Resubmission with 10 unchanged documents and 2 new documents results in 10 S3 downloads and 2 Cherwell downloads. Removed documents do not appear in the review's evidence context. Download phase progress reflects the mixed source.
- Failure/edge cases: If an S3 download fails for a reused document, fall back to downloading from Cherwell. If the manifest doesn't exist or is malformed, treat all documents as new (full download from Cherwell).

**FR-005: Superseded document exclusion**
- Description: Documents that appear on the Cherwell portal with "superseded" in their `document_type` (section header) or in their description/title are excluded from the review, regardless of whether they appeared in a previous manifest.
- Acceptance criteria: A document with `document_type` "Superseded Documents" or description containing "superseded" is filtered out during the document selection phase. The exclusion applies to both fresh reviews and resubmissions.
- Failure/edge cases: Case-insensitive matching. Documents that are superseded but were previously included in an older review are simply excluded from the new review (no special handling needed).

**FR-006: Previous review reference in new review metadata**
- Description: When a resubmission is based on a previous review, the new review's metadata includes a reference to the previous review_id and a summary of document changes (documents reused, new, removed).
- Acceptance criteria: The completed review result's `metadata` object includes `previous_review_id` (string or null), `documents_reused` (count), `documents_new` (count), `documents_removed` (count).
- Failure/edge cases: For fresh reviews with no previous review, `previous_review_id` is null and all documents are counted as new.

---

## Non-Functional Requirements

**NFR-001: Resubmission download speed**
- Resubmission with unchanged documents must complete the download phase significantly faster than a fresh review, since S3 downloads are not rate-limited (unlike the 1 req/sec Cherwell scraper rate limit). For 10 reused documents, the download phase should complete in under 10 seconds (vs ~10+ seconds per document from Cherwell).

---

## QA Plan

**QA-01: Resubmit a completed review**
- Goal: Verify that a previously-reviewed application can be resubmitted and creates a new review
- Steps:
  1. Submit a review for an application (e.g. `25/00413/F`) and wait for completion
  2. POST the same `application_ref` again to `/api/v1/reviews`
  3. GET the new review by its `review_id` and wait for completion
  4. GET the old review by its original `review_id`
- Expected: Step 2 returns 202 with a different `review_id`. Step 3 shows a completed review with `previous_review_id` in metadata. Step 4 still returns the original completed review.

**QA-02: Reject resubmission while review is active**
- Goal: Verify that concurrent reviews for the same application are prevented
- Steps:
  1. Submit a review for an application
  2. Immediately POST the same `application_ref` again (before it completes)
- Expected: Step 2 returns 409 with `review_in_progress` error code and the active review_id in details.

**QA-03: Force override of active review**
- Goal: Verify that `force=true` cancels the active review and starts a new one
- Steps:
  1. Submit a review for an application
  2. POST the same `application_ref` with `?force=true` (before it completes)
  3. GET the original review_id
  4. GET the new review_id
- Expected: Step 2 returns 202 with a new `review_id`. Step 3 shows status `cancelled`. Step 4 shows the new review processing/completed.

**QA-04: Document reuse from S3**
- Goal: Verify that unchanged documents are fetched from S3, not re-downloaded from Cherwell
- Steps:
  1. Submit a review and wait for completion
  2. Resubmit the same application
  3. Check worker logs during the download phase
- Expected: Logs show "Reusing document from S3" (or similar) for unchanged documents. Download phase completes faster than the original.

**QA-05: Superseded document exclusion**
- Goal: Verify that superseded documents are excluded from the review
- Steps:
  1. Submit a review for an application where the portal includes a superseded document section
  2. Check the completed review's document list
- Expected: Documents from the superseded section do not appear in the review evidence.
- White-box setup: Requires a real application on the Cherwell portal with superseded documents. If unavailable, verify via unit tests that the filter logic correctly identifies and excludes superseded documents.

---

## Open Questions

- What exact text does the Cherwell portal use for superseded section headers? (e.g. "Superseded Documents", "Superseded", "Superseded Plans"). Implementation should use case-insensitive substring matching on "superseded" to handle variations.

---

## Appendix

### Glossary
- **Terminal state**: A review with status `completed`, `failed`, or `cancelled` — not actively processing
- **Document manifest**: A JSON file stored in S3 that records the documents included in a review, their metadata, and S3 keys
- **Resubmission**: Submitting a new review for an `application_ref` that already has a previous review

### Embedding reuse analysis
ChromaDB's existing `ingest_document` tool has hash-based idempotency: if a document with the same SHA256 hash has already been ingested for the same `application_ref`, it returns `already_ingested` without re-computing embeddings. This means that when we download an unchanged document from S3 and pass it to `ingest_document`, the embeddings are automatically reused with zero additional cost. Storing embeddings separately in S3 would add significant complexity (custom export/import, embedding model version tracking, ChromaDB collection management) for negligible benefit, since the ChromaDB volume persists between reviews. This is deferred as a future optimisation for disaster recovery scenarios where the ChromaDB volume is lost.

### References
- `src/api/routes/reviews.py` — Review API endpoints
- `src/worker/review_jobs.py` — Worker job processing
- `src/agent/orchestrator.py` — Agent orchestration and document download
- `src/shared/redis_client.py` — Redis state management (`has_active_job_for_ref`)
- `src/shared/storage.py` — S3/local storage backend with `download_to()` method
- `src/mcp_servers/cherwell_scraper/server.py` — Cherwell document listing and download
- `src/mcp_servers/cherwell_scraper/filters.py` — Document category filtering
- `src/mcp_servers/document_store/chroma_client.py` — ChromaDB idempotency via file hash

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-15 | SDD | Initial specification |
