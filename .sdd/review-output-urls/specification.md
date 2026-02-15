# Specification: Review Output URLs

**Version:** 1.0
**Date:** 2026-02-15
**Status:** Draft

---

## Problem Statement

Review outputs (review JSON, review markdown, route assessments, consultation letter) are currently stored only in Redis with a 30-day TTL, and optionally uploaded to S3 as a side-effect. The frontend has no stable, addressable URLs to fetch individual output artefacts — it must parse the full inline response. When Redis TTL expires, the data is lost even if S3 copies exist. The system needs stable, public URLs for each output artefact that serve as the source of truth, with the API able to serve files from local storage when S3 is not configured.

## Beneficiaries

**Primary:**
- Frontend (bbug-website on Vercel) — fetches review artefacts directly from stable URLs instead of parsing the full inline API response

**Secondary:**
- Operators — output files persist beyond Redis TTL, giving a durable archive
- Other consumers (webhooks, CMS) — can link directly to artefacts

---

## Outcomes

**Must Haves**
- Each completed review has stable public URLs for: `review_json`, `review_md`, `routes_json`, and (when generated) `letter_md`
- Files are stored alongside downloaded documents in the same storage location (S3 prefix or local output directory)
- Each review_id gets its own set of files — no collisions between reviews of the same application
- For S3: files are public-read and the URL is the S3 public URL
- For local storage: the API serves files via a new endpoint
- `GET /api/v1/reviews/{review_id}` accepts a `urls_only` query parameter; when true, inline review data is omitted and replaced with a `urls` object containing the artefact URLs
- API documentation at `/docs` and `docs/API.md` is updated

**Nice-to-haves**
- The `urls` object is included in the response even when `urls_only` is false (alongside inline data)

---

## Explicitly Out of Scope

- Migrating existing reviews to file-based storage (only new reviews get output files)
- Serving files from S3 through the API as a proxy (S3 URLs are used directly)
- Versioning or revising output files after initial creation
- PDF rendering of review/letter outputs (existing `/download?format=pdf` endpoint handles this)
- Authentication on the file-serving endpoint (files are public, matching S3 public-read behaviour)
- Changing the letter generation flow (already implemented in `response-letter` feature)

---

## Functional Requirements

**FR-001: Persist Review Output Files**
- Description: When a review completes, the worker must write three output files alongside the application's downloaded documents: `{review_id}_review.json` (full review result), `{review_id}_review.md` (review markdown), and `{review_id}_routes.json` (route assessments array). Files are written via the existing `StorageBackend` abstraction — for S3, `storage.upload()` applies the `S3_KEY_PREFIX` (default `"planning"`) automatically, so the full S3 key is `{S3_KEY_PREFIX}/{safe_ref}/output/{filename}` and public URLs are generated via `storage.public_url()` which also includes the prefix. For local storage, files are written to `/data/output/{safe_ref}/`.
- Acceptance criteria: After a review completes, the three files exist in the storage backend. The `routes.json` file contains only the `route_assessments` array from the review result (or an empty array if none). Files use the key `{safe_ref}/output/{filename}` passed to `StorageBackend.upload()`, which prepends `S3_KEY_PREFIX` when using S3.
- Failure/edge cases: If file write fails, the review still completes (non-fatal, logged as warning). If route_assessments is empty/null, `routes.json` is still written with an empty array.

**FR-002: Persist Letter Output File**
- Description: When a letter completes, the worker must write `{letter_id}_letter.md` alongside the review outputs via the `StorageBackend` abstraction. This extends the existing letter job upload logic to also work for local storage. For S3, `S3_KEY_PREFIX` is applied automatically by the storage backend.
- Acceptance criteria: After a letter completes, the `letter.md` file exists in the storage backend at key `{safe_ref}/output/{letter_id}_letter.md` (with `S3_KEY_PREFIX` prepended for S3).
- Failure/edge cases: If file write fails, the letter still completes (non-fatal). If no letter has been generated for a review, the `letter_md` URL is null in the response.

**FR-003: Record Output URLs in Review Result**
- Description: After writing output files, the worker must record the public URLs in the review result stored in Redis, under a new `output_urls` dict with keys: `review_json`, `review_md`, `routes_json`. For S3, URLs are obtained from `storage.public_url(key)` which returns the full public URL including the `S3_KEY_PREFIX` (e.g. `https://bucket.nyc3.digitaloceanspaces.com/planning/25_01178_REM/output/{review_id}_review.json`). For local storage, URLs are API-relative paths (e.g. `/api/v1/files/{safe_ref}/output/{review_id}_review.json`).
- Acceptance criteria: A completed review's Redis result contains an `output_urls` dict with three string URLs. S3 URLs include the configured `S3_KEY_PREFIX` in the path. The URLs are resolvable and return the correct content.
- Failure/edge cases: If file persistence failed, the corresponding URL is null.

**FR-004: Record Letter URL**
- Description: After writing the letter file, the worker must record the letter URL. Since letters are stored separately in Redis (under `letter:{letter_id}`), the URL is stored in the letter record. The review endpoint assembles the letter URL by looking up the most recent completed letter for the review.
- Acceptance criteria: The letter record in Redis contains an `output_url` string field with the letter markdown URL.
- Failure/edge cases: If no letter exists for the review, `letter_md` is null in the URLs response.

**FR-005: `urls_only` Query Parameter**
- Description: `GET /api/v1/reviews/{review_id}` accepts an optional boolean query parameter `urls_only` (default: false). When true and the review is completed, the response omits the `review`, `metadata`, and `site_boundary` fields and instead includes a `urls` object with keys: `review_json`, `review_md`, `routes_json`, `letter_md` (each a string URL or null).
- Acceptance criteria: When `urls_only=true`, the response body contains `urls` with the four URL fields and does not contain `review` or `metadata`. Status, application info, timestamps, and error fields are still present. When `urls_only=false` (or omitted), the response is unchanged from current behaviour (but also includes the `urls` object as a nice-to-have).
- Failure/edge cases: If the review is not yet completed, `urls_only` has no effect (response is the same as today — status/progress only). If output files were not persisted (e.g. older reviews), URLs are null.

**FR-006: Local File Serving Endpoint**
- Description: A new endpoint `GET /api/v1/files/{file_path:path}` serves files from the local output directory (`/data/output/`). The endpoint streams the file with the appropriate `Content-Type` (`.json` → `application/json`, `.md` → `text/markdown`). This endpoint is only active when S3 is not configured (local storage mode).
- Acceptance criteria: Requesting a valid file path returns 200 with the file content and correct content type. Requesting a non-existent path returns 404. Path traversal attempts (e.g. `../../etc/passwd`) are rejected with 400.
- Failure/edge cases: The endpoint must validate that the resolved path stays within `/data/output/`. When S3 is configured, the endpoint returns 404 for all requests (files are served directly from S3).

**FR-007: Update API Documentation**
- Description: Update the OpenAPI schema (visible at `/docs`) and the manual `docs/API.md` to document: the `urls_only` query parameter, the `urls` response object, and the new `/api/v1/files/{file_path}` endpoint.
- Acceptance criteria: `/docs` shows the `urls_only` parameter on the review GET endpoint with description and type. `docs/API.md` contains a section for the files endpoint and documents the `urls` response shape.
- Failure/edge cases: None.

---

## QA Plan

**QA-01: Review output files created (S3)**
- Goal: Verify that completing a review creates output files in S3 under the configured key prefix
- Steps:
  1. Submit a review via `POST /api/v1/reviews` with S3 configured (e.g. `S3_KEY_PREFIX=planning`)
  2. Wait for the review to complete
  3. Check S3 bucket for `{S3_KEY_PREFIX}/{safe_ref}/output/{review_id}_review.json`, `_review.md`, `_routes.json`
  4. Verify the URLs in the response include the prefix (e.g. `https://bucket.region.digitaloceanspaces.com/planning/...`)
- Expected: All three files exist in S3 at the prefixed path with public-read ACL and correct content. URLs in the `output_urls` dict match the S3 public URL format including the prefix.

**QA-02: Review output files created (local)**
- Goal: Verify that completing a review creates output files locally
- Steps:
  1. Submit a review with S3 disabled (local storage)
  2. Wait for the review to complete
  3. Check `/data/output/{safe_ref}/` for the three files
- Expected: All three files exist on disk with correct content

**QA-03: urls_only=true response**
- Goal: Verify the urls_only parameter omits inline data and returns URLs
- Steps:
  1. Complete a review
  2. `GET /api/v1/reviews/{review_id}?urls_only=true`
  3. Inspect the response body
- Expected: Response contains `urls` object with `review_json`, `review_md`, `routes_json`, `letter_md` (letter_md is null if no letter generated). Response does not contain `review` or `metadata` keys.

**QA-04: urls_only=false response**
- Goal: Verify default behaviour is preserved
- Steps:
  1. Complete a review
  2. `GET /api/v1/reviews/{review_id}` (no urls_only parameter)
  3. Inspect the response body
- Expected: Response contains `review`, `metadata`, and `urls` (nice-to-have). Same shape as current response plus the `urls` field.

**QA-05: Local file serving**
- Goal: Verify the file serving endpoint works for local storage
- Steps:
  1. Complete a review with local storage
  2. Copy a URL from the `urls` response
  3. `GET` the URL directly
- Expected: Returns the file content with correct Content-Type header

**QA-06: Path traversal rejected**
- Goal: Verify the file serving endpoint rejects path traversal
- Steps:
  1. `GET /api/v1/files/../../etc/passwd`
- Expected: Returns 400 or 404, does not serve the file

**QA-07: Letter URL included after letter generation**
- Goal: Verify letter_md URL appears after generating a letter
- Steps:
  1. Complete a review
  2. Generate a letter via `POST /api/v1/reviews/{review_id}/letter`
  3. Wait for letter to complete
  4. `GET /api/v1/reviews/{review_id}?urls_only=true`
- Expected: `urls.letter_md` contains a resolvable URL to the letter markdown

---

## Open Questions

None — all questions resolved during discovery.

---

## Appendix

### Glossary
- **Output artefact:** One of the four files produced from a review: review JSON, review markdown, routes JSON, or letter markdown
- **safe_ref:** The application reference with `/` replaced by `_` (e.g. `25/01178/REM` becomes `25_01178_REM`)
- **S3_KEY_PREFIX:** Environment variable controlling the top-level prefix for all S3 keys (default: `"planning"`). Applied automatically by `S3StorageBackend` — callers pass keys without the prefix.
- **urls_only:** Query parameter mode that returns only artefact URLs without inline review content

### References
- [docs/API.md](../../docs/API.md) — Current API reference
- [.sdd/s3-document-storage/specification.md](../s3-document-storage/specification.md) — S3 storage feature
- [.sdd/response-letter/specification.md](../response-letter/specification.md) — Letter generation feature
- [src/shared/storage.py](../../src/shared/storage.py) — Storage abstraction layer
- [src/worker/review_jobs.py](../../src/worker/review_jobs.py) — Current review output upload logic

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-15 | SDD | Initial specification |
