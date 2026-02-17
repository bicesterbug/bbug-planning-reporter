# Specification: Policy Ingestion Fix

**Version:** 1.0
**Date:** 2026-02-17
**Status:** Draft

---

## Problem Statement

The policy API endpoints for uploading revisions and reindexing both contain stubs that generate fake job IDs but never enqueue actual ingestion jobs. The `ingest_policy_revision` arq job function exists in `policy_jobs.py` but is not registered in the worker's function list. As a result, any policy revision uploaded via the API is saved to disk and registered in Redis with status `processing` but never actually ingested into ChromaDB — leaving it permanently stuck and unsearchable. Three policies on production (`BICESTER_LCWIP`, `BIC_LCWIP_SUMMARY`, `BUS`) are currently in this state.

## Beneficiaries

**Primary:**
- System operator adding policy documents via the API (currently has no way to get them ingested)

**Secondary:**
- Review consumers who miss policy citations because uploaded policies have no searchable chunks

---

## Outcomes

**Must Haves**
- Uploading a policy revision via `POST /api/v1/policies/{source}/revisions` triggers actual ingestion (text extraction, chunking, embedding, ChromaDB storage)
- Reindexing a revision via `POST .../reindex` triggers actual re-ingestion
- Stuck `processing` revisions can be re-triggered without requiring a fresh upload
- The three stuck production revisions are recovered after deployment

**Nice-to-haves**
- Ingestion status can be polled via the existing `GET .../status` endpoint with real progress

---

## Explicitly Out of Scope

- S3 storage of original PDFs (Spec 2: policy-s3-storage)
- Streaming upload / 50MB file size limit (Spec 2: policy-s3-storage)
- Authority model and bulk import (Spec 3: policy-authority-model)
- Changes to the MCP server's `ingest_policy_revision` tool (it works correctly; only the API routing is broken)
- Changes to the seed script (it calls `PolicyIngestionService` directly and works correctly)

---

## Functional Requirements

**FR-001: Enqueue ingestion job on revision upload**
- Description: When a revision is created via `POST /api/v1/policies/{source}/revisions`, the API must enqueue an `ingest_policy_revision` arq job with the source, revision_id, and file_path. The returned `ingestion_job_id` must be the real arq job ID.
- Acceptance criteria: After uploading a PDF revision via the API, the worker picks up the job and the revision transitions from `processing` to `active` with a non-zero chunk_count.
- Failure/edge cases: If the worker is not running, the job sits in the queue until the worker starts. If ingestion fails (corrupt PDF, extraction error), the revision status becomes `failed` with an error message.

**FR-002: Enqueue reindex job on reindex request**
- Description: When `POST /api/v1/policies/{source}/revisions/{revision_id}/reindex` is called, the API must enqueue an `ingest_policy_revision` arq job with `reindex=True`. The existing `TODO` stub must be replaced with a real enqueue call.
- Acceptance criteria: After calling reindex on an active revision, the worker re-runs the pipeline (deletes old chunks, re-extracts, re-chunks, re-embeds, re-stores) and the revision returns to `active` with updated chunk_count and ingested_at.
- Failure/edge cases: If the revision has no file_path recorded in the registry, the reindex should fail with a clear error.

**FR-003: Register ingestion job function in worker**
- Description: The `ingest_policy_revision` function from `policy_jobs.py` must be added to the worker's `WorkerSettings.functions` list so the arq worker can execute it.
- Acceptance criteria: The worker starts without error and recognises `ingest_policy_revision` as a valid job function.
- Failure/edge cases: None — this is a configuration fix.

**FR-004: Allow reindex of stuck `processing` revisions**
- Description: The reindex endpoint currently rejects revisions with status `processing` (409 error). This must be changed to allow reindex of `processing` revisions, since they may be permanently stuck (the current bug). The endpoint should reset the status to `processing` and enqueue the job.
- Acceptance criteria: Calling reindex on a revision with status `processing` succeeds (202) and triggers ingestion.
- Failure/edge cases: If a revision is genuinely being processed by a running job, reindexing it could cause a conflict. This is acceptable — the `reindex=True` flag will clear old chunks before re-storing, so the outcome is correct even if both jobs overlap.

---

## QA Plan

**QA-01: Upload a new policy revision and verify ingestion**
- Goal: Validate the end-to-end upload → ingestion pipeline
- Steps:
  1. Create a test policy: `POST /api/v1/policies` with source `TEST_POLICY`
  2. Upload a small PDF: `POST /api/v1/policies/TEST_POLICY/revisions` with a seed PDF
  3. Poll status: `GET /api/v1/policies/TEST_POLICY/revisions/{revision_id}/status`
  4. Wait until status is `active`
  5. Verify chunks exist: use `search_policy` MCP tool with `sources: ["TEST_POLICY"]`
- Expected: Revision transitions to `active` with chunk_count > 0. Search returns relevant chunks.

**QA-02: Reindex a stuck `processing` revision on production**
- Goal: Recover the stuck BIC_LCWIP_SUMMARY revision
- Steps:
  1. Deploy the fix
  2. Call `POST /api/v1/policies/BIC_LCWIP_SUMMARY/revisions/rev_BIC_LCWIP_SUMMARY_2020_08/reindex`
  3. Poll status until `active`
  4. Search for BIC_LCWIP_SUMMARY content
- Expected: Revision transitions to `active` with chunk_count > 0.

**QA-03: Reindex the BUS policy on production**
- Goal: Recover the stuck BUS revision
- Steps:
  1. Call `POST /api/v1/policies/BUS/revisions/rev_BUS_2025_01/reindex`
  2. Poll status until `active`
- Expected: Revision transitions to `active` with chunk_count > 0.

---

## Open Questions

None.

---

## Appendix

### Glossary
- **arq**: Async Redis Queue — Python job queue library used by the worker
- **Stuck revision**: A revision with status `processing` that will never complete because no ingestion job was enqueued

### References
- Stub code: `src/api/routes/policies.py` line 403 (fake job ID), line 799 (TODO reindex)
- Worker functions list: `src/worker/main.py` line 94
- arq job function: `src/worker/policy_jobs.py` line 345
- Production stuck revisions: `BIC_LCWIP_SUMMARY/rev_BIC_LCWIP_SUMMARY_2020_08`, `BUS/rev_BUS_2025_01`, `BICESTER_LCWIP/rev_BICESTER_LCWIP_2020_03`

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-17 | Claude | Initial specification |
