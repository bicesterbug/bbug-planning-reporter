# Specification: S3-Compatible Document Storage

**Version:** 1.0
**Date:** 2026-02-07
**Status:** Draft

---

## Problem Statement

All downloaded application documents and generated reports are stored on the local filesystem, requiring a large external disk mounted at `/media/pete/Files/bbug-reports/`. This prevents deployment to cloud or headless environments, ties storage to a single machine, and means document links in reports point to the Cherwell portal rather than a permanent, self-controlled location. When S3-compatible object storage (DigitalOcean Spaces) is enabled, documents and reports should be stored remotely with public URLs replacing Cherwell links in output.

## Beneficiaries

**Primary:**
- Operator deploying the system — eliminates dependency on local disk for raw document and report storage

**Secondary:**
- Report consumers — get permanent self-hosted document links instead of ephemeral Cherwell portal URLs
- Future cloud deployments — enables containerised deployment without persistent local volumes for documents

---

## Outcomes

**Must Haves**
- When S3 is configured, downloaded documents are uploaded to S3 with public-read access and removed from local disk after processing
- Report and letter URLs reference public S3 URLs instead of Cherwell portal URLs
- Generated review and letter output files are uploaded to S3
- When S3 is not configured, the system behaves exactly as it does today (local filesystem)
- Configuration via environment variables with no code changes needed to switch modes

**Nice-to-haves**
- A CLI/script to migrate existing local raw documents to S3
- Configurable retention/lifecycle rules hint in documentation

---

## Explicitly Out of Scope

- Migrating existing local documents to S3 (new reviews only)
- Pre-signed URL or CDN endpoint support (public-read ACL only for this iteration)
- Encrypting objects at rest (rely on DO Spaces server-side encryption defaults)
- Multi-region or cross-bucket replication
- Serving documents through the API (direct S3 public URLs only)
- Changes to ChromaDB vector storage (remains local)
- Changes to policy document storage (remains local bind mount)

---

## Functional Requirements

### FR-001: Storage Backend Toggle
**Description:** The system must support two storage backends — local filesystem (default) and S3-compatible object storage — selected by the presence of the `S3_ENDPOINT_URL` environment variable. When set (along with required credentials), S3 is used. When absent, local filesystem is used with no behavioural change from today.

**Examples:**
- Positive case: `S3_ENDPOINT_URL=https://nyc3.digitaloceanspaces.com` plus `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` are set → documents stored in S3
- Positive case: No S3 environment variables set → documents stored on local filesystem at `/data/raw/`
- Edge case: `S3_ENDPOINT_URL` set but `S3_BUCKET` missing → startup error with clear message

### FR-002: Document Upload After Download
**Description:** When S3 is enabled, the scraper's downloaded documents must be uploaded to the S3 bucket immediately after download completes. Each document is uploaded with `public-read` ACL. The S3 object key follows the pattern `{prefix}/{app_ref}/{index}_{name}.pdf` where `prefix` defaults to `planning` and is configurable via `S3_KEY_PREFIX`.

**Examples:**
- Positive case: Document downloaded to `/tmp/25_00284_F/001_Transport.pdf` → uploaded to `s3://bucket/planning/25_00284_F/001_Transport.pdf` with public-read
- Edge case: Upload fails after 3 retries → job fails with `s3_upload_failed` error, local temp file is retained for manual recovery

### FR-003: Local Temp File Cleanup
**Description:** When S3 is enabled, local copies of raw documents must be deleted immediately after successful text extraction and ingestion into ChromaDB. If ingestion fails, the local file is retained and the error is logged.

**Examples:**
- Positive case: Document ingested successfully → local temp file at `/tmp/raw/25_00284_F/001_Transport.pdf` deleted
- Negative case: Ingestion fails for a document → local temp file retained, warning logged with file path

### FR-004: Public S3 URLs in Reports
**Description:** When S3 is enabled, all document URLs in review markdown, review JSON, and letter output must be public S3 URLs (e.g., `https://{bucket}.{region}.digitaloceanspaces.com/{prefix}/{app_ref}/{filename}`) instead of Cherwell portal download URLs.

**Examples:**
- Positive case: Review markdown contains `[Transport Assessment](https://mybucket.nyc3.digitaloceanspaces.com/planning/25_00284_F/001_Transport.pdf)` instead of `[Transport Assessment](https://planningregister.cherwell.gov.uk/Document/Download?...)`
- Positive case: Review JSON `key_documents[].url` field contains S3 URL
- Edge case: Document that failed S3 upload → falls back to Cherwell URL with a note

### FR-005: Report and Letter Upload to S3
**Description:** When S3 is enabled, generated review JSON/markdown and letter JSON/markdown outputs must be uploaded to S3 under the key pattern `{prefix}/{app_ref}/output/{review_id}_review.json`, `{prefix}/{app_ref}/output/{review_id}_review.md`, `{prefix}/{app_ref}/output/{letter_id}_letter.json`, `{prefix}/{app_ref}/output/{letter_id}_letter.md`. Files are uploaded with `public-read` ACL.

**Examples:**
- Positive case: Review completes → 2 files uploaded: `planning/25_00284_F/output/rev_01KG.._review.json` and `planning/25_00284_F/output/rev_01KG.._review.md`
- Positive case: Letter completes → 2 files uploaded similarly
- Edge case: S3 upload of output fails → review/letter still completes successfully (output in Redis), warning logged

### FR-006: S3 Configuration Validation
**Description:** On service startup, if any S3 environment variable is set, the system must validate that all required variables are present (`S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`) and attempt a lightweight connectivity check (e.g., HEAD bucket). Missing variables or failed connectivity must produce a clear error log and prevent the service from starting.

**Examples:**
- Positive case: All variables set and bucket reachable → service starts normally with `"S3 storage enabled"` log
- Negative case: `S3_BUCKET` missing → service fails with `"S3 configuration incomplete: missing S3_BUCKET"`
- Edge case: Bucket unreachable at startup → service fails with `"S3 connectivity check failed: [error details]"`

### FR-007: S3 Key Prefix Configuration
**Description:** The S3 object key prefix must be configurable via `S3_KEY_PREFIX` environment variable, defaulting to `planning`. This allows multiple deployments to share a bucket via distinct prefixes.

**Examples:**
- Positive case: `S3_KEY_PREFIX=bbug-prod` → keys like `bbug-prod/25_00284_F/001_Transport.pdf`
- Positive case: `S3_KEY_PREFIX` not set → keys like `planning/25_00284_F/001_Transport.pdf`

---

## Non-Functional Requirements

### NFR-001: Upload Performance
**Category:** Performance
**Description:** S3 upload must not significantly increase overall job time. Document uploads should use concurrent multipart upload for files larger than 8MB.
**Acceptance Threshold:** Total upload overhead for a 400-document application (typical mix of PDF sizes) must be under 120 seconds.
**Verification:** Observability — log total upload time per job; manual load test on representative application.

### NFR-002: No Data Loss
**Category:** Reliability
**Description:** A document must not be deleted from local disk until both (a) it has been successfully uploaded to S3 and (b) it has been successfully ingested into ChromaDB. Failure at either step must retain the local file.
**Acceptance Threshold:** Zero data loss scenarios in unit and integration tests covering upload failure, ingestion failure, and partial completion.
**Verification:** Testing — unit tests for each failure mode; integration test with simulated S3 failure.

### NFR-003: Credential Security
**Category:** Security
**Description:** S3 credentials must never appear in logs, error messages, or API responses. Environment variables are the only accepted credential source.
**Acceptance Threshold:** No credential leakage in any log output or error response across all test scenarios.
**Verification:** Code review — grep for credential variable names in log statements; integration test verifying error messages on auth failure do not contain secrets.

### NFR-004: Backwards Compatibility
**Category:** Maintainability
**Description:** When no S3 environment variables are set, the system must behave identically to the current implementation with zero configuration changes required. No existing Docker volume mounts, environment variables, or API contracts may change.
**Acceptance Threshold:** All existing tests pass without modification when S3 is not configured.
**Verification:** Testing — full test suite run with S3 variables unset.

### NFR-005: Startup Validation
**Category:** Reliability
**Description:** Incomplete or invalid S3 configuration must be detected at startup, not at first use during a job.
**Acceptance Threshold:** Service refuses to start within 10 seconds if S3 config is incomplete or unreachable.
**Verification:** Testing — integration test with partial config; integration test with unreachable endpoint.

___



## Open Questions

None — all requirements clarified during discovery.

---

## Appendix

### Glossary
- **S3-compatible storage:** Object storage implementing the AWS S3 API (e.g., DigitalOcean Spaces, MinIO, AWS S3)
- **Public-read ACL:** S3 access control setting that allows unauthenticated HTTP GET access to objects
- **S3 key prefix:** The leading path segment in S3 object keys, acting as a virtual directory
- **Raw documents:** PDF files downloaded from the Cherwell planning portal for a given application

### References
- [DigitalOcean Spaces documentation](https://docs.digitalocean.com/products/spaces/)
- [boto3 S3 client documentation](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html)
- Existing architecture: `.sdd/document-processing/specification.md`, `.sdd/agent-integration/specification.md`

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-07 | Claude | Initial specification |
