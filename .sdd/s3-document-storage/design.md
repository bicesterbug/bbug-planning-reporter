# Design: S3-Compatible Document Storage

**Version:** 1.0
**Date:** 2026-02-07
**Status:** Draft
**Linked Specification** `.sdd/s3-document-storage/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

Documents flow through four stages:

1. **Download** — `AgentOrchestrator._phase_download_documents()` calls the `cherwell-scraper-mcp` tool `download_all_documents` with `output_dir="/data/raw"`. The scraper's `CherwellClient.download_document()` streams bytes to disk at `/data/raw/{safe_ref}/{index}_{name}.pdf`. Each `DownloadResult` carries the original Cherwell portal `url`.

2. **Metadata capture** — The orchestrator builds `DocumentIngestionResult.document_metadata`, a dict mapping `file_path → {description, document_type, url}` where `url` is the Cherwell download link.

3. **Ingestion** — The orchestrator calls `document-store-mcp` tool `ingest_document` for each `file_path`. The document store reads the local file, extracts text (PyMuPDF/Tesseract), chunks, embeds, and stores in ChromaDB. The file is never accessed again after ingestion.

4. **URL rendering** — The orchestrator formats `document_metadata` into a text block for the Claude prompt. Claude embeds these URLs as markdown links in the review. The `key_documents_json` block also carries URLs. The letter job receives the completed review (with embedded URLs) from Redis and passes it to Claude for letter generation.

All raw files live on an external drive mounted as Docker bind mounts at `/media/pete/Files/bbug-reports/raw:/data/raw`. Reports/letters are stored in Redis and optionally written to `/data/output`.

### Proposed Architecture

A new `StorageBackend` abstraction provides two implementations: `LocalStorageBackend` (existing behaviour) and `S3StorageBackend` (new). The backend is selected at startup based on environment variables and injected into services that need it.

```
                          ┌──────────────────┐
                          │  StorageBackend   │  (Protocol)
                          │  .upload()        │
                          │  .public_url()    │
                          │  .download_to()   │
                          │  .delete()        │
                          └────────┬──────────┘
                       ┌───────────┴───────────┐
                       │                       │
              ┌────────┴────────┐   ┌──────────┴────────┐
              │ LocalStorage    │   │ S3Storage          │
              │ (noop upload,   │   │ (boto3 upload,     │
              │  file:// URLs)  │   │  public URLs,      │
              │                 │   │  download_to,      │
              │                 │   │  delete)           │
              └─────────────────┘   └───────────────────┘
```

**Modified flow when S3 is enabled:**

1. **Download** — Scraper downloads to a **temp directory** (`/tmp/raw/{safe_ref}/`) instead of `/data/raw`. After each file is written, the orchestrator uploads it to S3 via `StorageBackend.upload()`.

2. **Metadata capture** — `document_metadata[file_path]["url"]` is replaced with the S3 public URL from `StorageBackend.public_url()` instead of the Cherwell portal URL.

3. **Ingestion** — The `ingest_document` tool still receives the local temp `file_path` (unchanged). After successful ingestion, the orchestrator deletes the local temp file.

4. **URL rendering** — URLs in the prompt are now S3 public URLs. Claude renders them into the review markdown and `key_documents_json`. The letter inherits these URLs from the stored review.

5. **Output upload** — After review completion, the worker uploads `{review_id}_review.json` and `{review_id}_review.md` to S3. After letter completion, uploads `{letter_id}_letter.json` and `{letter_id}_letter.md`.

### Technology Decisions

- **boto3** — The standard Python SDK for S3-compatible APIs. Well-tested with DigitalOcean Spaces. Already compatible with the project's async pattern (S3 operations are fast enough that sync calls within `asyncio.to_thread()` are acceptable; no need for aioboto3).
- **Protocol-based abstraction** — Python `typing.Protocol` for `StorageBackend` rather than ABC. Keeps it lightweight, allows duck-typing, and avoids inheritance complexity.
- **Temp directory** — When S3 is enabled, downloads go to `/tmp/raw/` (container-local tmpfs) rather than the persistent volume. Keeps the persistent raw volume optional when S3 is active.
- **Upload happens in orchestrator** — Not in the scraper MCP server. The scraper remains a pure download tool. The orchestrator, which already owns the document metadata dict, is the right place to add S3 upload + URL rewriting logic. This avoids modifying the MCP tool interface.

### Quality Attributes

- **Backwards compatibility** — When no S3 env vars are set, `create_storage_backend()` returns `LocalStorageBackend` which is a passthrough (no-ops for upload/delete, returns original Cherwell URLs). Zero impact on existing behaviour.
- **Testability** — Protocol-based backend is trivially mockable. `InMemoryStorageBackend` provided for tests.
- **Reliability** — Upload retries (3 attempts with exponential backoff) in S3 backend. Local file retained on upload failure. Delete-after-ingest pattern ensures no data loss.

---

## API Design

No public API changes. The existing REST endpoints return the same response shapes. The only observable difference is that document URLs in review/letter output will be S3 public URLs when S3 is enabled.

---

## Modified Components

### AgentOrchestrator
**Change Description** Currently downloads documents to `/data/raw` and captures Cherwell URLs in `document_metadata`. Must be modified to: (a) accept a `StorageBackend` instance, (b) upload each downloaded file to S3 after download, (c) replace Cherwell URLs with S3 public URLs in `document_metadata`, (d) delete local temp files after successful ingestion.

**Dependants** `review_jobs.py` (must pass `StorageBackend` when constructing orchestrator)

**Kind** Class

**Requirements References**
- [s3-document-storage:FR-002]: Upload documents to S3 after download
- [s3-document-storage:FR-003]: Delete local temp files after ingestion
- [s3-document-storage:FR-004]: Replace Cherwell URLs with S3 public URLs
- [s3-document-storage:NFR-002]: Never delete local file until S3 upload AND ingestion succeed

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| AgentOrchestrator/TS-01 | S3 upload after download | S3 backend configured, scraper returns download results | Download phase completes | Each file uploaded to S3 via backend.upload(), document_metadata URLs are S3 public URLs |
| AgentOrchestrator/TS-02 | Local cleanup after ingestion | S3 enabled, files uploaded and ingested | Ingestion phase completes for a document | Local temp file is deleted |
| AgentOrchestrator/TS-03 | Retain file on ingestion failure | S3 enabled, file uploaded to S3 | Ingestion fails for a document | Local temp file is NOT deleted, warning logged |
| AgentOrchestrator/TS-04 | S3 upload failure | S3 enabled, upload raises exception after retries | Upload fails for a document | OrchestratorError raised or error recorded, local file retained, Cherwell URL kept as fallback |
| AgentOrchestrator/TS-05 | Local backend passthrough | No S3 configured (LocalStorageBackend) | Download and ingestion phases run | No upload/delete calls, Cherwell URLs preserved in metadata, identical to current behaviour |

### review_jobs.process_review
**Change Description** Currently creates `AgentOrchestrator` without storage backend. Must be modified to: (a) create the appropriate `StorageBackend` from environment, (b) pass it to the orchestrator, (c) after review completes, upload review JSON and markdown to S3.

**Dependants** None

**Kind** Function

**Requirements References**
- [s3-document-storage:FR-001]: Toggle between local and S3 based on environment
- [s3-document-storage:FR-005]: Upload review output files to S3

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| ReviewJobs/TS-01 | S3 output upload on completion | S3 configured, review succeeds | _handle_success runs | review.json and review.md uploaded to S3 at `{prefix}/{app_ref}/output/{review_id}_review.{ext}` |
| ReviewJobs/TS-02 | Output upload failure non-fatal | S3 configured, review succeeds, S3 upload fails | _handle_success runs | Review still stored in Redis, warning logged, no exception raised |
| ReviewJobs/TS-03 | No S3 output upload when local | No S3 configured | Review completes | No S3 upload attempted, behaviour unchanged |

### letter_jobs.letter_job
**Change Description** Currently generates a letter and stores it in Redis. Must be modified to also upload letter JSON and markdown to S3 when enabled.

**Dependants** None

**Kind** Function

**Requirements References**
- [s3-document-storage:FR-005]: Upload letter output files to S3

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| LetterJobs/TS-01 | S3 letter output upload | S3 configured, letter generation succeeds | Letter stored in Redis | letter.json and letter.md uploaded to S3 at `{prefix}/{app_ref}/output/{letter_id}_letter.{ext}` |
| LetterJobs/TS-02 | Letter upload failure non-fatal | S3 configured, letter succeeds, S3 upload fails | Letter stored in Redis | Warning logged, letter still available in Redis |

### docker-compose.yml
**Change Description** Must add optional S3 environment variables to the `worker` service. The `/data/raw` volume mount should remain (needed when S3 is not configured) but the worker should use `/tmp/raw` as its download target when S3 is enabled.

**Dependants** None

**Kind** Configuration

**Requirements References**
- [s3-document-storage:FR-001]: Environment variable toggle
- [s3-document-storage:FR-007]: S3_KEY_PREFIX configuration

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| DockerCompose/TS-01 | S3 env vars passed to worker | S3 variables in .env or compose override | Worker container starts | All S3_* variables visible in worker environment |

### AgentOrchestrator._phase_download_documents
**Change Description** Currently passes `"output_dir": "/data/raw"` to the scraper MCP tool. When S3 is enabled, must pass `"output_dir": "/tmp/raw"` instead so files go to a transient location. After download results are captured, must iterate through successful downloads, upload each to S3, and rewrite `document_metadata` URLs.

**Dependants** None (change is internal to the orchestrator download phase)

**Kind** Method

**Requirements References**
- [s3-document-storage:FR-002]: Upload to S3 after download
- [s3-document-storage:FR-004]: Rewrite URLs to S3 public URLs
- [s3-document-storage:NFR-001]: Upload overhead under 120 seconds

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| DownloadPhase/TS-01 | Output dir switched to /tmp/raw | S3 backend is S3StorageBackend | Download args are built | output_dir is "/tmp/raw" |
| DownloadPhase/TS-02 | Output dir stays /data/raw | Backend is LocalStorageBackend | Download args are built | output_dir is "/data/raw" |

---

## Added Components

### StorageBackend (Protocol)
**Description** Defines the interface for document storage operations. Four methods: `upload(local_path, key) → None`, `public_url(key) → str`, `download_to(key, local_path) → None`, `delete(local_path) → None`. Also a property `is_remote → bool` to determine whether temp directory cleanup and URL rewriting should occur.

**Users** `AgentOrchestrator`, `review_jobs`, `letter_jobs`

**Kind** Protocol (class)

**Location** `src/shared/storage.py`

**Requirements References**
- [s3-document-storage:FR-001]: Toggle mechanism — `is_remote` property distinguishes backends
- [s3-document-storage:FR-002]: `upload()` method for S3 upload
- [s3-document-storage:FR-004]: `public_url()` method for URL generation
- [s3-document-storage:FR-003]: `delete()` method for temp cleanup

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| StorageBackend/TS-01 | Protocol compliance | A class implementing all methods | isinstance check or Protocol usage | No type errors |

### S3StorageBackend
**Description** S3-compatible implementation of `StorageBackend`. Uses boto3 to upload files with `public-read` ACL. Generates public URLs in the format `https://{bucket}.{endpoint_host}/{prefix}/{key}`. Retries uploads 3 times with exponential backoff. Validates configuration on construction (all required env vars present, bucket reachable).

**Users** `create_storage_backend()` factory

**Kind** Class

**Location** `src/shared/storage.py`

**Requirements References**
- [s3-document-storage:FR-002]: Upload with public-read ACL
- [s3-document-storage:FR-004]: Public URL generation
- [s3-document-storage:FR-006]: Startup validation (connectivity check in constructor)
- [s3-document-storage:FR-007]: Configurable key prefix
- [s3-document-storage:NFR-001]: Multipart upload for files > 8MB
- [s3-document-storage:NFR-002]: Upload retries for reliability
- [s3-document-storage:NFR-003]: No credentials in logs

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| S3StorageBackend/TS-01 | Upload with public-read | Valid S3 config, local file exists | upload(local_path, key) | boto3 put_object called with ACL='public-read', correct bucket, correct key |
| S3StorageBackend/TS-02 | Public URL format | Endpoint=nyc3.digitaloceanspaces.com, bucket=mybucket, prefix=planning | public_url("25_00284_F/001_Transport.pdf") | Returns "https://mybucket.nyc3.digitaloceanspaces.com/planning/25_00284_F/001_Transport.pdf" |
| S3StorageBackend/TS-03 | Upload retry on failure | Upload fails twice then succeeds | upload() called | Three attempts made, success on third, no exception |
| S3StorageBackend/TS-04 | Upload permanent failure | Upload fails 3 times | upload() called | Raises StorageUploadError after 3 attempts |
| S3StorageBackend/TS-05 | Multipart for large files | File is 10MB | upload() called | boto3 uses multipart upload (TransferConfig threshold) |
| S3StorageBackend/TS-06 | Startup validation - missing var | S3_ENDPOINT_URL set but S3_BUCKET missing | S3StorageBackend constructed | Raises StorageConfigError with clear message naming missing var |
| S3StorageBackend/TS-07 | Startup validation - unreachable | All vars set but endpoint unreachable | S3StorageBackend constructed | Raises StorageConfigError with connectivity details |
| S3StorageBackend/TS-08 | No credentials in logs | Upload fails with auth error | Exception is logged | Log message does not contain access key or secret key |
| S3StorageBackend/TS-09 | Custom prefix | S3_KEY_PREFIX=bbug-prod | public_url("25_00284_F/doc.pdf") | Returns URL with "bbug-prod/25_00284_F/doc.pdf" path |
| S3StorageBackend/TS-10 | Default prefix | S3_KEY_PREFIX not set | public_url("25_00284_F/doc.pdf") | Returns URL with "planning/25_00284_F/doc.pdf" path |

### LocalStorageBackend
**Description** No-op implementation of `StorageBackend` for when S3 is not configured. `upload()` is a no-op. `public_url()` returns `None` (caller uses original Cherwell URL). `delete()` is a no-op (files remain on persistent volume). `is_remote` returns `False`.

**Users** `create_storage_backend()` factory

**Kind** Class

**Location** `src/shared/storage.py`

**Requirements References**
- [s3-document-storage:FR-001]: Default backend when S3 not configured
- [s3-document-storage:NFR-004]: Identical behaviour to current system

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| LocalStorageBackend/TS-01 | Upload is no-op | LocalStorageBackend instance | upload() called | No exception, no side effects |
| LocalStorageBackend/TS-02 | public_url returns None | LocalStorageBackend instance | public_url(key) | Returns None |
| LocalStorageBackend/TS-03 | delete is no-op | LocalStorageBackend instance | delete() called | No exception, no file deletion |
| LocalStorageBackend/TS-04 | is_remote is False | LocalStorageBackend instance | is_remote checked | Returns False |

### create_storage_backend factory function
**Description** Factory function that reads S3 environment variables and returns the appropriate `StorageBackend` implementation. If `S3_ENDPOINT_URL` is set, constructs and returns `S3StorageBackend` (which validates all other required vars and connectivity). If not set, returns `LocalStorageBackend`.

**Users** `review_jobs.process_review`, `letter_jobs.letter_job`, worker startup

**Kind** Function

**Location** `src/shared/storage.py`

**Requirements References**
- [s3-document-storage:FR-001]: Toggle based on S3_ENDPOINT_URL presence
- [s3-document-storage:FR-006]: Validation delegated to S3StorageBackend constructor
- [s3-document-storage:NFR-005]: Fail-fast at startup

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| Factory/TS-01 | S3 vars set | All S3_* env vars present | create_storage_backend() | Returns S3StorageBackend instance |
| Factory/TS-02 | No S3 vars | No S3_ENDPOINT_URL | create_storage_backend() | Returns LocalStorageBackend instance |
| Factory/TS-03 | Partial S3 config | S3_ENDPOINT_URL set but S3_BUCKET missing | create_storage_backend() | Raises StorageConfigError |

### StorageConfigError
**Description** Exception raised when S3 configuration is incomplete or invalid. Includes a message naming the specific missing variable or connectivity issue.

**Users** `S3StorageBackend`, `create_storage_backend`

**Kind** Class (Exception)

**Location** `src/shared/storage.py`

**Requirements References**
- [s3-document-storage:FR-006]: Clear error on bad config

**Test Scenarios**

N/A — tested through S3StorageBackend and factory scenarios.

### StorageUploadError
**Description** Exception raised when an S3 upload fails after all retries. Contains the key, number of attempts, and last error.

**Users** `S3StorageBackend`, `AgentOrchestrator`

**Kind** Class (Exception)

**Location** `src/shared/storage.py`

**Requirements References**
- [s3-document-storage:FR-002]: Upload failure handling

**Test Scenarios**

N/A — tested through S3StorageBackend/TS-04.

### InMemoryStorageBackend
**Description** Test-only implementation of `StorageBackend` that stores uploads in a dict. Useful for verifying upload calls and URL generation in unit tests without mocking boto3.

**Users** Test suites

**Kind** Class

**Location** `tests/conftest.py` (or `src/shared/storage.py` if useful for dev)

**Requirements References**
- [s3-document-storage:NFR-004]: Enables testing without S3

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| InMemoryBackend/TS-01 | Upload stores content | InMemoryStorageBackend instance | upload(path, key) | key present in backend.uploads dict |
| InMemoryBackend/TS-02 | public_url returns predictable URL | InMemoryStorageBackend | public_url(key) | Returns "https://test-bucket.example.com/{key}" |

---

## Used Components

### CherwellClient.download_document
**Location** `src/mcp_servers/cherwell_scraper/client.py`

**Provides** Streams document bytes from Cherwell portal to a local file path. Handles rate limiting, retries, and error handling.

**Used By** `AgentOrchestrator` (indirectly via MCP tool call). No changes required — it already writes to whatever `output_dir` is specified.

### DocumentStoreMCP._ingest_document
**Location** `src/mcp_servers/document_store/server.py`

**Provides** Reads a local PDF file, extracts text, chunks, embeds, and stores in ChromaDB. Accepts `file_path` parameter.

**Used By** `AgentOrchestrator._phase_ingest_documents`. No changes required — it reads from whatever local path is given. When S3 is enabled, the path will be `/tmp/raw/...` instead of `/data/raw/...` but the ingestion logic is identical.

### RedisClient.store_result / store_letter
**Location** `src/shared/redis_client.py`

**Provides** Stores review results and letter records in Redis with TTL.

**Used By** `review_jobs._handle_success`, `letter_jobs.letter_job`. No changes required — S3 upload is additive (output goes to both Redis and S3).

---

## Documentation Considerations

- Add S3 configuration section to `.env.example` documenting all `S3_*` variables
- Update `docker-compose.yml` comments to note S3 as an alternative to the raw volume
- Add a brief section in `docs/DESIGN.md` about the storage backend abstraction

---

## Instrumentation

| Requirement | Observability Criteria | Implementation | Component |
|-------------|------------------------|----------------|-----------|
| [s3-document-storage:NFR-001] | Total upload time per job must be measurable | Log `s3_upload_total_seconds` at INFO level after all uploads complete in download phase, and per-file `s3_upload_seconds` at DEBUG | `AgentOrchestrator._phase_download_documents` |
| [s3-document-storage:NFR-003] | No credentials in any log output | Scrub S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY from all exception messages before logging; never log these env var values | `S3StorageBackend` |

---

## Integration Test Scenarios

| ID | Scenario | Given | When | Then | Components Involved |
|----|----------|-------|------|------|---------------------|
| ITS-01 | Full review with S3 storage | S3 configured (InMemoryStorageBackend), mock MCP client returns download results | Review job runs to completion | All documents uploaded to backend, URLs in review are S3 URLs, temp files cleaned up, review JSON+MD uploaded to backend | AgentOrchestrator, StorageBackend, review_jobs |
| ITS-02 | Full review with local storage | No S3 configured | Review job runs to completion | No upload calls, Cherwell URLs in review, files remain at /data/raw, no temp cleanup | AgentOrchestrator, LocalStorageBackend, review_jobs |
| ITS-03 | Letter output upload | S3 configured (InMemoryStorageBackend), completed review in Redis | Letter job runs | Letter JSON+MD uploaded to backend at correct S3 key | letter_jobs, StorageBackend |
| ITS-04 | S3 upload failure mid-job | S3 backend configured to fail on 3rd upload | Review job runs | First 2 docs have S3 URLs, 3rd falls back to Cherwell URL, error logged, job continues | AgentOrchestrator, S3StorageBackend |

---

## E2E Test Scenarios

| ID | Scenario | Given | When | Then | User Journey |
|----|----------|-------|------|------|--------------|
| E2E-01 | Review with S3 produces public URLs | Full Docker stack with S3 env vars pointing to test bucket | POST /api/v1/reviews, wait for completion, GET result | Review JSON key_documents[].url values are S3 public URLs, not Cherwell URLs | Submit → poll → retrieve → verify URLs |
| E2E-02 | Review without S3 unchanged | Full Docker stack, no S3 env vars | POST /api/v1/reviews, wait for completion, GET result | Review JSON key_documents[].url values are Cherwell portal URLs (identical to today) | Submit → poll → retrieve → verify URLs |

---

## Test Data

- Existing mock download results from `tests/test_agent/test_orchestrator.py` can be extended with S3 URL assertions
- Small test PDFs in `tests/fixtures/` for upload size testing
- `InMemoryStorageBackend` eliminates need for real S3 in unit/integration tests
- E2E tests require a test bucket (DigitalOcean Spaces or MinIO in Docker for CI)

---

## Test Feasibility

- Unit and integration tests are fully feasible with `InMemoryStorageBackend` — no external dependencies
- E2E test with real S3 requires either a test DO Spaces bucket or a MinIO container. For CI, MinIO in Docker is recommended but is **not a blocker** for this iteration — E2E-01 can be run manually against a real DO Spaces bucket
- Multipart upload testing (S3StorageBackend/TS-05) requires a file > 8MB in test fixtures — use `os.urandom()` to generate one in the test

---

## Risks and Dependencies

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| boto3 not compatible with DO Spaces edge case | Upload fails for certain file types or sizes | Low | DO Spaces is broadly S3-compatible; test with real bucket early |
| Container `/tmp` runs out of space for large applications (400+ docs) | Download phase fails | Medium | Use a dedicated temp volume or fall back to `/data/raw` as temp location; document container tmpfs sizing |
| S3 upload latency slows job significantly | Job time increases beyond acceptable threshold | Low | Uploads happen serially after download batch; could parallelise uploads if needed |
| Credential rotation requires container restart | Brief downtime during rotation | Low | Acceptable for current deployment; document the requirement |

**External dependencies:**
- `boto3` Python package (new dependency)
- DigitalOcean Spaces account with bucket provisioned

**Assumptions:**
- DO Spaces supports `public-read` ACL on individual objects
- The S3 endpoint URL does not require region-specific handling beyond what boto3 provides via `endpoint_url`
- Container `/tmp` has sufficient space for one application's documents at a time (typically < 1GB)

---

## Feasibility Review

No large missing features or infrastructure blocking implementation. All components can be built incrementally. MinIO for CI E2E testing is nice-to-have but not required for the initial iteration.

---

## Task Breakdown

### Phase 1: Storage Backend Abstraction

- Task 1: Create `StorageBackend` protocol, `LocalStorageBackend`, `S3StorageBackend`, `InMemoryStorageBackend`, factory function, and exception classes in `src/shared/storage.py`
  - Status: Done
  - Implement the protocol with `upload()`, `public_url()`, `download_to()`, `delete()`, `is_remote` property
  - Implement `LocalStorageBackend` as passthrough no-ops
  - Implement `S3StorageBackend` with boto3, public-read ACL, retry logic, multipart config, startup validation
  - Implement `InMemoryStorageBackend` for testing
  - Implement `create_storage_backend()` factory
  - Add `boto3` to pyproject.toml dependencies
  - Write tests in `tests/test_shared/test_storage.py`
  - Requirements: [s3-document-storage:FR-001], [s3-document-storage:FR-002], [s3-document-storage:FR-006], [s3-document-storage:FR-007], [s3-document-storage:NFR-001], [s3-document-storage:NFR-002], [s3-document-storage:NFR-003], [s3-document-storage:NFR-005]
  - Test Scenarios: [s3-document-storage:StorageBackend/TS-01], [s3-document-storage:S3StorageBackend/TS-01], [s3-document-storage:S3StorageBackend/TS-02], [s3-document-storage:S3StorageBackend/TS-03], [s3-document-storage:S3StorageBackend/TS-04], [s3-document-storage:S3StorageBackend/TS-05], [s3-document-storage:S3StorageBackend/TS-06], [s3-document-storage:S3StorageBackend/TS-07], [s3-document-storage:S3StorageBackend/TS-08], [s3-document-storage:S3StorageBackend/TS-09], [s3-document-storage:S3StorageBackend/TS-10], [s3-document-storage:LocalStorageBackend/TS-01], [s3-document-storage:LocalStorageBackend/TS-02], [s3-document-storage:LocalStorageBackend/TS-03], [s3-document-storage:LocalStorageBackend/TS-04], [s3-document-storage:Factory/TS-01], [s3-document-storage:Factory/TS-02], [s3-document-storage:Factory/TS-03], [s3-document-storage:InMemoryBackend/TS-01], [s3-document-storage:InMemoryBackend/TS-02]

### Phase 2: Orchestrator Integration

- Task 2: Modify `AgentOrchestrator` to accept and use `StorageBackend`
  - Status: Done
  - Add `storage_backend` parameter to `__init__` (default: `LocalStorageBackend()`)
  - In `_phase_download_documents`: use `/tmp/raw` as output_dir when `backend.is_remote`, else `/data/raw`
  - After download results captured: iterate successful downloads, call `backend.upload(file_path, s3_key)` for each, replace `document_metadata[file_path]["url"]` with `backend.public_url(s3_key)` (or keep Cherwell URL if `public_url` returns None)
  - Log total upload time at INFO level
  - In `_phase_ingest_documents`: after each successful ingestion, if `backend.is_remote`, call `backend.delete(local_path)` to clean up temp file
  - Handle `StorageUploadError` per-file: log warning, keep Cherwell URL, continue with remaining files
  - Write/update tests in `tests/test_agent/test_orchestrator.py`
  - Requirements: [s3-document-storage:FR-002], [s3-document-storage:FR-003], [s3-document-storage:FR-004], [s3-document-storage:NFR-001], [s3-document-storage:NFR-002]
  - Test Scenarios: [s3-document-storage:AgentOrchestrator/TS-01], [s3-document-storage:AgentOrchestrator/TS-02], [s3-document-storage:AgentOrchestrator/TS-03], [s3-document-storage:AgentOrchestrator/TS-04], [s3-document-storage:AgentOrchestrator/TS-05], [s3-document-storage:DownloadPhase/TS-01], [s3-document-storage:DownloadPhase/TS-02]

### Phase 3: Worker Job Integration and Output Upload

- Task 3: Modify `review_jobs.py` and `letter_jobs.py` to use StorageBackend for output upload
  - Status: Done
  - In `process_review`: call `create_storage_backend()`, pass to `AgentOrchestrator`
  - In `_handle_success`: if `backend.is_remote`, upload review JSON and review markdown to S3 at `{prefix}/{safe_ref}/output/{review_id}_review.json` and `.md`. Wrap in try/except — upload failure must not fail the review.
  - In `letter_job`: call `create_storage_backend()`. After successful letter generation, if `backend.is_remote`, upload letter JSON and letter markdown similarly. Wrap in try/except.
  - Write/update tests in `tests/test_worker/test_review_jobs.py` and `tests/test_worker/test_letter_jobs.py`
  - Requirements: [s3-document-storage:FR-001], [s3-document-storage:FR-005], [s3-document-storage:NFR-004]
  - Test Scenarios: [s3-document-storage:ReviewJobs/TS-01], [s3-document-storage:ReviewJobs/TS-02], [s3-document-storage:ReviewJobs/TS-03], [s3-document-storage:LetterJobs/TS-01], [s3-document-storage:LetterJobs/TS-02]

- Task 4: Update Docker Compose and environment configuration
  - Status: Done
  - Add S3_* env vars to worker service in docker-compose.yml (commented out with documentation)
  - Create/update `.env.example` with S3 variables and descriptions
  - Rebuild base image if `boto3` added to dependencies
  - Requirements: [s3-document-storage:FR-001], [s3-document-storage:FR-007]
  - Test Scenarios: [s3-document-storage:DockerCompose/TS-01]

### Phase 4: Integration Testing

- Task 5: Write integration tests for full review flow with S3
  - Status: Done
  - Test full review flow with InMemoryStorageBackend verifying URLs, uploads, and cleanup
  - Test full review flow without S3 verifying unchanged behaviour
  - Test letter output upload
  - Test partial S3 failure (upload fails mid-job)
  - Requirements: [s3-document-storage:NFR-002], [s3-document-storage:NFR-004]
  - Test Scenarios: [s3-document-storage:ITS-01], [s3-document-storage:ITS-02], [s3-document-storage:ITS-03], [s3-document-storage:ITS-04]

---

## Intermediate Dead Code Tracking

| Phase Introduced | Description | Used In Phase | Status |
|------------------|-------------|---------------|--------|
| Phase 1 | `S3StorageBackend.download_to()` method | Future: migration script or re-processing | Accepted — part of StorageBackend protocol for completeness |

---

## Intermediate Stub Tracking

| Phase Introduced | Test Name | Reason for Stub | Implemented In Phase | Status |
|------------------|-----------|-----------------|----------------------|--------|
| — | — | — | — | — |

No stubs expected. All test scenarios use InMemoryStorageBackend or mocks.

---

## Requirements Validation

- [s3-document-storage:FR-001] Storage Backend Toggle
  - Phase 1 Task 1 (factory function), Phase 3 Task 3 (worker uses factory), Phase 3 Task 4 (Docker env vars)
- [s3-document-storage:FR-002] Document Upload After Download
  - Phase 1 Task 1 (S3StorageBackend.upload), Phase 2 Task 2 (orchestrator calls upload)
- [s3-document-storage:FR-003] Local Temp File Cleanup
  - Phase 2 Task 2 (orchestrator deletes after ingestion)
- [s3-document-storage:FR-004] Public S3 URLs in Reports
  - Phase 1 Task 1 (public_url method), Phase 2 Task 2 (URL rewriting in orchestrator)
- [s3-document-storage:FR-005] Report and Letter Upload to S3
  - Phase 3 Task 3 (review_jobs and letter_jobs upload output)
- [s3-document-storage:FR-006] S3 Configuration Validation
  - Phase 1 Task 1 (S3StorageBackend constructor validation)
- [s3-document-storage:FR-007] S3 Key Prefix Configuration
  - Phase 1 Task 1 (S3StorageBackend reads S3_KEY_PREFIX), Phase 3 Task 4 (Docker env var)

- [s3-document-storage:NFR-001] Upload Performance
  - Phase 1 Task 1 (multipart config), Phase 2 Task 2 (upload timing log)
- [s3-document-storage:NFR-002] No Data Loss
  - Phase 2 Task 2 (delete-after-ingest pattern), Phase 4 Task 5 (integration tests)
- [s3-document-storage:NFR-003] Credential Security
  - Phase 1 Task 1 (S3StorageBackend log scrubbing)
- [s3-document-storage:NFR-004] Backwards Compatibility
  - Phase 1 Task 1 (LocalStorageBackend), Phase 3 Task 3 (no-S3 path), Phase 4 Task 5 (ITS-02)
- [s3-document-storage:NFR-005] Startup Validation
  - Phase 1 Task 1 (constructor validation, factory)

---

## Test Scenario Validation

### Component Scenarios
- [s3-document-storage:StorageBackend/TS-01]: Phase 1 Task 1
- [s3-document-storage:S3StorageBackend/TS-01]: Phase 1 Task 1
- [s3-document-storage:S3StorageBackend/TS-02]: Phase 1 Task 1
- [s3-document-storage:S3StorageBackend/TS-03]: Phase 1 Task 1
- [s3-document-storage:S3StorageBackend/TS-04]: Phase 1 Task 1
- [s3-document-storage:S3StorageBackend/TS-05]: Phase 1 Task 1
- [s3-document-storage:S3StorageBackend/TS-06]: Phase 1 Task 1
- [s3-document-storage:S3StorageBackend/TS-07]: Phase 1 Task 1
- [s3-document-storage:S3StorageBackend/TS-08]: Phase 1 Task 1
- [s3-document-storage:S3StorageBackend/TS-09]: Phase 1 Task 1
- [s3-document-storage:S3StorageBackend/TS-10]: Phase 1 Task 1
- [s3-document-storage:LocalStorageBackend/TS-01]: Phase 1 Task 1
- [s3-document-storage:LocalStorageBackend/TS-02]: Phase 1 Task 1
- [s3-document-storage:LocalStorageBackend/TS-03]: Phase 1 Task 1
- [s3-document-storage:LocalStorageBackend/TS-04]: Phase 1 Task 1
- [s3-document-storage:Factory/TS-01]: Phase 1 Task 1
- [s3-document-storage:Factory/TS-02]: Phase 1 Task 1
- [s3-document-storage:Factory/TS-03]: Phase 1 Task 1
- [s3-document-storage:InMemoryBackend/TS-01]: Phase 1 Task 1
- [s3-document-storage:InMemoryBackend/TS-02]: Phase 1 Task 1
- [s3-document-storage:AgentOrchestrator/TS-01]: Phase 2 Task 2
- [s3-document-storage:AgentOrchestrator/TS-02]: Phase 2 Task 2
- [s3-document-storage:AgentOrchestrator/TS-03]: Phase 2 Task 2
- [s3-document-storage:AgentOrchestrator/TS-04]: Phase 2 Task 2
- [s3-document-storage:AgentOrchestrator/TS-05]: Phase 2 Task 2
- [s3-document-storage:DownloadPhase/TS-01]: Phase 2 Task 2
- [s3-document-storage:DownloadPhase/TS-02]: Phase 2 Task 2
- [s3-document-storage:ReviewJobs/TS-01]: Phase 3 Task 3
- [s3-document-storage:ReviewJobs/TS-02]: Phase 3 Task 3
- [s3-document-storage:ReviewJobs/TS-03]: Phase 3 Task 3
- [s3-document-storage:LetterJobs/TS-01]: Phase 3 Task 3
- [s3-document-storage:LetterJobs/TS-02]: Phase 3 Task 3
- [s3-document-storage:DockerCompose/TS-01]: Phase 3 Task 4

### Integration Scenarios
- [s3-document-storage:ITS-01]: Phase 4 Task 5
- [s3-document-storage:ITS-02]: Phase 4 Task 5
- [s3-document-storage:ITS-03]: Phase 4 Task 5
- [s3-document-storage:ITS-04]: Phase 4 Task 5

### E2E Scenarios
- [s3-document-storage:E2E-01]: Manual testing (requires real S3 bucket)
- [s3-document-storage:E2E-02]: Manual testing (existing test with no S3 vars)

---

## Appendix

### Glossary
- **StorageBackend:** Protocol defining the interface for document storage operations (upload, URL generation, download, delete)
- **S3 key:** The full path to an object within an S3 bucket (e.g., `planning/25_00284_F/001_Transport.pdf`)
- **Public-read ACL:** S3 access control that allows unauthenticated HTTP GET for an object
- **Multipart upload:** S3 feature for uploading large files in parts, improving reliability and enabling parallel transfers

### References
- [DigitalOcean Spaces S3 compatibility](https://docs.digitalocean.com/products/spaces/reference/s3-compatibility/)
- [boto3 S3 Transfer Configuration](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/customizations/s3.html#boto3.s3.transfer.TransferConfig)
- `.sdd/s3-document-storage/specification.md` — linked specification

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-07 | Claude | Initial design |
