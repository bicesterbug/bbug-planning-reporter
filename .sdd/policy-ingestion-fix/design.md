# Design: Policy Ingestion Fix

**Version:** 1.0
**Date:** 2026-02-17
**Status:** Draft
**Linked Specification** `.sdd/policy-ingestion-fix/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The policy revision upload and reindex API endpoints in `src/api/routes/policies.py` contain stubs from the original Phase 2 implementation. Both endpoints generate fake job IDs but never enqueue arq jobs. The `ingest_policy_revision` arq job function exists in `src/worker/policy_jobs.py` and works correctly (the seed script uses `PolicyIngestionService` directly), but it is not registered in the worker's `WorkerSettings.functions` list.

The review submission endpoint (`src/api/routes/reviews.py`) already demonstrates the correct pattern: inject `ArqPoolDep`, call `arq_pool.enqueue_job("review_job", ...)`, and return the job object's ID.

### Proposed Architecture

Wire the two policy endpoints to follow the same pattern as review submission:

1. Add `ArqPoolDep` as a parameter to `upload_revision()` and `reindex_revision()`
2. Replace the fake job ID with a real `arq_pool.enqueue_job("ingest_policy_revision", ...)` call
3. Register `ingest_policy_revision` in the worker's functions list
4. Remove the `processing` status guard on the reindex endpoint so stuck revisions can be recovered

### Technology Decisions

- Follow the existing arq enqueue pattern — no new libraries or patterns needed
- The arq job function `ingest_policy_revision` already exists and is fully implemented — it just needs to be reachable

### Quality Attributes

- **Backward compatibility**: No API contract changes — the response schema is identical, only the `ingestion_job_id` value changes from a fake UUID to a real arq job ID
- **Idempotency**: Reindexing a stuck revision is safe because `reindex=True` clears old chunks before re-storing

---

## Modified Components

### src/worker/main.py
**Change Description** The worker's `WorkerSettings.functions` list does not include `ingest_policy_revision`. Add it so the worker can process policy ingestion jobs.

**Dependants** None

**Kind** Module

**Details**

Add `ingest_policy_revision` import and include it in the `functions` list.

**Requirements References**
- [policy-ingestion-fix:FR-003]: Register ingestion job function in worker

**Test Scenarios**

**TS-01: Worker includes policy ingestion function**
- Given: The worker settings are configured
- When: The functions list is inspected
- Then: `ingest_policy_revision` is in the list

---

### src/api/routes/policies.py — upload_revision()
**Change Description** Currently generates a fake job ID (`job_{uuid}`) and never enqueues an arq job. Change to inject `ArqPoolDep`, enqueue `ingest_policy_revision` with the source, revision_id, and file_path, and return the real arq job ID.

**Dependants** Tests in `tests/test_api/test_policies.py`

**Kind** Function

**Details**

Add `arq_pool: ArqPoolDep` parameter. Replace lines 403-404 with:
- `job = await arq_pool.enqueue_job("ingest_policy_revision", source, revision_id, str(file_path))`
- Use `job.job_id` as the `ingestion_job_id`

**Requirements References**
- [policy-ingestion-fix:FR-001]: Enqueue ingestion job on revision upload

**Test Scenarios**

**TS-02: Upload revision enqueues arq job**
- Given: A valid policy and PDF file
- When: `POST /api/v1/policies/{source}/revisions` is called
- Then: `arq_pool.enqueue_job` is called with "ingest_policy_revision", the source, revision_id, and file_path
- And: The response `ingestion_job_id` matches the arq job's ID

---

### src/api/routes/policies.py — reindex_revision()
**Change Description** Currently has `# TODO: Enqueue reindex job (Phase 3)` stub. Replace with real arq job enqueue. Also remove the guard that rejects `processing` revisions (line 781-790), since stuck revisions need to be recoverable.

**Dependants** Tests in `tests/test_api/test_policies.py`

**Kind** Function

**Details**

Add `arq_pool: ArqPoolDep` parameter. Replace the TODO with:
- `job = await arq_pool.enqueue_job("ingest_policy_revision", source, revision_id, str(revision.file_path), True)`
- Remove the `if revision.status == RevisionStatus.PROCESSING` guard block
- Return the job_id in a field if needed, or keep existing response

The reindex endpoint must also fail gracefully if `file_path` is not recorded in the revision metadata.

**Requirements References**
- [policy-ingestion-fix:FR-002]: Enqueue reindex job on reindex request
- [policy-ingestion-fix:FR-004]: Allow reindex of stuck `processing` revisions

**Test Scenarios**

**TS-03: Reindex enqueues arq job**
- Given: An existing active revision with a file_path
- When: `POST /api/v1/policies/{source}/revisions/{revision_id}/reindex` is called
- Then: `arq_pool.enqueue_job` is called with "ingest_policy_revision", the source, revision_id, file_path, and reindex=True

**TS-04: Reindex allows stuck processing revisions**
- Given: A revision with status `processing`
- When: `POST /api/v1/policies/{source}/revisions/{revision_id}/reindex` is called
- Then: The request succeeds (202) and enqueues an ingestion job

**TS-05: Reindex fails if no file_path**
- Given: A revision with no file_path
- When: `POST /api/v1/policies/{source}/revisions/{revision_id}/reindex` is called
- Then: Returns 422 with error indicating the revision has no file to reindex

---

## Used Components

### src/worker/policy_jobs.py — ingest_policy_revision
**Location** `src/worker/policy_jobs.py`

**Provides** The arq job function that coordinates the full ingestion pipeline: text extraction, chunking, embedding, ChromaDB storage, and status updates.

**Used By** Modified `upload_revision()` and `reindex_revision()` endpoints (via arq queue), and `WorkerSettings.functions`

### src/api/dependencies.py — ArqPoolDep
**Location** `src/api/dependencies.py`

**Provides** FastAPI dependency that provides an arq Redis connection pool for enqueuing jobs.

**Used By** Modified `upload_revision()` and `reindex_revision()` endpoints

---

## Documentation Considerations

- No API documentation changes needed — the response schema is unchanged
- The `ingestion_job_id` field now returns a real arq job ID instead of a fake UUID, but the field was already documented

---

## Risks and Dependencies

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Worker not running when job is enqueued | Low | Low | arq queues jobs in Redis; they'll be processed when the worker starts |
| Production stuck revisions have no file_path | Medium | Medium | Check file_path before reindex; if missing, upload must be repeated |
| Reindex of genuinely-running job causes conflict | Low | Low | `reindex=True` clears old chunks atomically; the last job to finish wins |

---

## Feasibility Review

No blockers. All components exist — this is wiring work only.

---

## QA Feasibility

**QA-01 (Upload and verify):** Fully feasible — requires the worker to be running.

**QA-02 (Reindex stuck BIC_LCWIP_SUMMARY):** Feasible if the revision has a file_path recorded. If not, the PDF must be re-uploaded. White-box check: query the revision detail to confirm file_path exists before attempting reindex.

**QA-03 (Reindex stuck BUS):** Same as QA-02.

---

## Task Breakdown

### Phase 1: Wire up ingestion

**Task 1: Register ingest_policy_revision in worker and add ArqPoolDep to upload endpoint**
- Status: Done
- Requirements: [policy-ingestion-fix:FR-001], [policy-ingestion-fix:FR-003]
- Test Scenarios: [policy-ingestion-fix:main.py/TS-01], [policy-ingestion-fix:upload_revision/TS-02]
- Details:
  - Add `ingest_policy_revision` import and registration in `src/worker/main.py`
  - Add `arq_pool: ArqPoolDep` to `upload_revision()` signature
  - Replace fake job ID with real `arq_pool.enqueue_job()` call
  - Update existing upload test to mock arq_pool and verify `enqueue_job` is called

**Task 2: Wire reindex endpoint and allow stuck revisions**
- Status: Done
- Requirements: [policy-ingestion-fix:FR-002], [policy-ingestion-fix:FR-004]
- Test Scenarios: [policy-ingestion-fix:reindex_revision/TS-03], [policy-ingestion-fix:reindex_revision/TS-04], [policy-ingestion-fix:reindex_revision/TS-05]
- Details:
  - Add `arq_pool: ArqPoolDep` to `reindex_revision()` signature
  - Replace TODO stub with real `arq_pool.enqueue_job()` call
  - Remove the `processing` status guard (allow stuck revisions to be reindexed)
  - Add file_path validation (reject reindex if no file_path)
  - Update existing reindex test to mock arq_pool and verify `enqueue_job` is called
  - Change test for `processing` status from expecting 409 to expecting 202
  - Add test for missing file_path case

---

## Intermediate Dead Code Tracking

None expected.

---

## Intermediate Stub Tracking

None expected.

---

## Appendix

### References
- Review job enqueue pattern: `src/api/routes/reviews.py` line 154
- Existing arq mock pattern: `tests/test_api/test_letter_routes.py` line 37

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-17 | Claude | Initial design |
