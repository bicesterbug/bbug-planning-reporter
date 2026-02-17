# Specification: Policy S3 Storage & Upload Improvements

**Version:** 1.0
**Date:** 2026-02-17
**Status:** Draft

---

## Problem Statement

Policy PDFs uploaded via the API are stored only on the container's local filesystem, which is ephemeral and not backed up. If the volume is lost, the original documents are gone and revisions cannot be reindexed. Additionally, there is no file size limit on uploads — the API reads the entire file into memory (`await file.read()`), which could cause OOM on large documents. The system needs S3 as the primary store for policy PDFs (matching the existing S3 setup used for planning application documents), a 50MB file size limit, and streaming upload to avoid buffering large files in memory.

## Beneficiaries

**Primary:**
- System operator who needs durable storage for policy documents and the ability to reindex from S3

**Secondary:**
- Future API consumers who may want to download original policy PDFs via a URL

---

## Outcomes

**Must Haves**
- Uploaded policy PDFs are stored in S3 as the primary store, with the S3 URL recorded in the revision metadata
- The local file is used only as a temporary working copy for ingestion and deleted after successful ingestion
- Uploads larger than 50MB are rejected with a clear error
- Uploads are streamed to disk (then to S3) rather than buffered entirely in memory
- The S3 URL for a policy PDF is returned in the revision detail API response
- Reindex uses the S3 URL to re-download the PDF if the local file is missing

**Nice-to-haves**
- Public/signed URL for policy PDFs so external consumers can download originals

---

## Explicitly Out of Scope

- Migrating existing seed PDFs to S3 (they are fetched from gov.uk URLs and can be re-downloaded)
- Changes to the ingestion pipeline itself (extraction, chunking, embedding)
- Authority model (Spec 3)
- Changes to the S3 bucket configuration (reuse existing `S3_*` env vars and bucket)

---

## Functional Requirements

**FR-001: Stream upload to temporary file**
- Description: The revision upload endpoint must stream the uploaded file to a temporary file on disk rather than reading the entire content into memory. This prevents OOM for large PDFs.
- Acceptance criteria: Uploading a 40MB PDF does not cause memory usage to spike by 40MB in the API process. The file is written to disk incrementally.
- Failure/edge cases: If the disk is full, the upload fails with 500 and the partial file is cleaned up.

**FR-002: Enforce 50MB file size limit**
- Description: The revision upload endpoint must reject files larger than 50MB with a 422 error. The limit should be checked during streaming (abort early if the stream exceeds the limit) rather than after completing the upload.
- Acceptance criteria: Uploading a 60MB PDF returns 422 with error code `file_too_large` and message indicating the 50MB limit. The partial file is cleaned up.
- Failure/edge cases: If Content-Length header is present and exceeds 50MB, reject immediately without reading the body. If no Content-Length, enforce during streaming.

**FR-003: Upload to S3 after local save**
- Description: After the PDF is saved to the temporary local file and passes validation, upload it to S3 using the existing `StorageService` (`src/shared/storage.py`). The S3 key should follow the pattern `policy/{source}/{revision_id}/{filename}`. Store the S3 URL in the revision metadata.
- Acceptance criteria: After uploading a revision, the revision detail response includes an `s3_url` field with the S3 object URL. The object exists in S3.
- Failure/edge cases: If S3 upload fails, the revision creation should still succeed (file is on local disk). Log a warning. Reindex can retry the S3 upload.

**FR-004: Delete local file after successful ingestion**
- Description: After the ingestion job completes successfully (revision status = active), the local temporary PDF file should be deleted. The S3 copy is the durable store.
- Acceptance criteria: After a revision is successfully ingested, the local PDF file no longer exists on disk. The S3 URL remains valid.
- Failure/edge cases: If local file deletion fails, log a warning but don't fail the job.

**FR-005: Reindex downloads from S3 if local file missing**
- Description: When a reindex is requested and the local file doesn't exist, the ingestion job must download the PDF from the S3 URL stored in the revision metadata before running the pipeline.
- Acceptance criteria: Reindexing a revision where the local file has been deleted succeeds by downloading from S3 first.
- Failure/edge cases: If no S3 URL is recorded and no local file exists, the reindex fails with a clear error message.

**FR-006: Return S3 URL in revision detail**
- Description: The revision detail response (`GET /api/v1/policies/{source}/revisions/{revision_id}`) must include the S3 URL when available.
- Acceptance criteria: The `PolicyRevisionDetail` response model includes an `s3_url` field (nullable string).
- Failure/edge cases: For revisions created before this feature (seeded revisions), `s3_url` is null.

---

## QA Plan

**QA-01: Upload a PDF and verify S3 storage**
- Goal: Validate end-to-end upload with S3
- Steps:
  1. Upload a policy revision PDF via the API
  2. Check revision detail: verify `s3_url` is present
  3. Download the file from the S3 URL
  4. Verify the downloaded file matches the original
  5. After ingestion completes, verify the local file is gone
- Expected: S3 URL is valid, file is downloadable, local copy is deleted.

**QA-02: Upload a file exceeding 50MB**
- Goal: Validate file size enforcement
- Steps:
  1. Create a dummy 60MB file
  2. Upload it as a policy revision
- Expected: 422 error with `file_too_large` code.

**QA-03: Reindex when local file is missing**
- Goal: Validate S3 fallback on reindex
- Steps:
  1. Upload a revision (S3 URL is stored)
  2. Wait for ingestion to complete (local file deleted)
  3. Call reindex
- Expected: Reindex downloads from S3 and completes successfully.

---

## Open Questions

None.

---

## Appendix

### Glossary
- **StorageService**: Existing S3 client in `src/shared/storage.py` used for planning application documents

### References
- Existing S3 integration: `src/shared/storage.py`
- Revision upload endpoint: `src/api/routes/policies.py` line 300+
- S3 env vars: `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_BUCKET_NAME`, `S3_REGION`

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-17 | Claude | Initial specification |
