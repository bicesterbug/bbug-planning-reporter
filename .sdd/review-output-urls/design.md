# Design: Review Output URLs

**Version:** 1.0
**Date:** 2026-02-15
**Status:** Draft
**Linked Specification:** `.sdd/review-output-urls/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

Review outputs are generated in the worker (`orchestrator.py`) and stored as a single JSON blob in Redis (`review_result:{review_id}`, 30-day TTL). When S3 is configured, the worker additionally uploads `_review.json` and `_review.md` files as a non-critical side-effect, but the URLs are discarded — neither recorded in Redis nor exposed to the API.

The `LocalStorageBackend` is a null-object implementation: `upload()` is a no-op, `public_url()` returns `None`. All callers guard upload calls behind `if storage.is_remote`, so local deployments never persist output files at all.

The `GET /api/v1/reviews/{review_id}` endpoint returns the full review inline — there's no way for the frontend to fetch individual artefacts (review JSON, markdown, route assessments, letter) by URL.

### Proposed Architecture

1. **LocalStorageBackend becomes active** — `upload()` copies files to `/data/output/{key}`, `public_url()` returns API-relative paths (`/api/v1/files/{key}`).
2. **Output upload is unconditional** — the `if storage.is_remote` guards are removed from `_upload_review_output()` and `_upload_letter_output()`, so both S3 and local backends persist output files.
3. **URLs are recorded in Redis** — the upload functions return URL dicts which are stored in the review result (under `output_urls`) and letter record (under `output_url`).
4. **Result is stored AFTER upload** — reorder `_handle_success()` so URLs are available in the Redis result from the first read.
5. **File serving endpoint** — new `GET /api/v1/files/{path}` endpoint streams files from `/data/output/` with path-traversal protection. Only active when `LocalStorageBackend` is in use.
6. **`urls_only` parameter** — `get_review()` accepts `urls_only=true` to omit inline `review`/`metadata`/`site_boundary` and return a `urls` object instead. The `urls` object is always present on completed reviews (nice-to-have).

### Sequence: Review Completion

```
Worker: orchestrator completes review
  → _handle_success() called with ReviewResult
    → _upload_review_output(result, review_data, storage)
      → writes review.json, review.md, routes.json via storage.upload()
      → returns {review_json: url, review_md: url, routes_json: url}
    → review_data["output_urls"] = urls
    → redis.store_result(review_id, review_data)  ← now includes URLs
    → fire webhooks
```

### Sequence: Letter Completion

```
Worker: letter_job completes
  → _upload_letter_output(..., storage) → returns letter_url
  → redis.update_letter_status(letter_id, output_url=letter_url)
  → redis.set(review_letter_url:{review_id}, letter_url)  ← reverse lookup
```

### Sequence: GET /reviews/{id}?urls_only=true

```
API: get_review(review_id, urls_only=true)
  → redis.get_job(review_id) → job
  → redis.get_result(review_id) → result
  → output_urls = result["output_urls"]
  → letter_url = redis.get("review_letter_url:{review_id}")
  → build OutputUrls(review_json, review_md, routes_json, letter_md=letter_url)
  → return ReviewResponse(urls=urls, review=None, metadata=None, ...)
```

### Technology Decisions

- **No new dependencies** — uses existing `shutil.copy2` for local file writes, existing `StorageBackend` protocol, existing FastAPI `FileResponse`.
- **Reverse letter URL lookup via simple Redis key** — `review_letter_url:{review_id}` stores the latest letter URL string. Simpler than indexing letter records by review_id. Overwritten when a new letter is generated for the same review.
- **API-relative URLs for local storage** — `/api/v1/files/{key}` rather than absolute URLs. The frontend already knows the API base URL.

### Quality Attributes

- **Backwards compatible** — the `urls` field is additive; existing clients ignoring it see no change. The `urls_only` parameter defaults to `false`.
- **Idempotent** — each `review_id` produces uniquely-named files; re-reviews of the same application don't collide.
- **Failure-tolerant** — file persistence failures are logged but don't fail the review. Missing URLs are `null` in the response.

---

## API Design

### Modified Endpoint: GET /api/v1/reviews/{review_id}

New optional query parameter:
- `urls_only` (boolean, default `false`) — when `true` and review is completed, omit `review`, `metadata`, and `site_boundary` from the response.

New response field (always present on completed reviews):
- `urls` — object containing artefact URLs, or `null` if review is not completed.

Response shape when `urls_only=true`:
```json
{
  "review_id": "rev_01JMABCDEF",
  "application_ref": "25/01178/REM",
  "status": "completed",
  "created_at": "2026-02-15T10:00:00Z",
  "started_at": "2026-02-15T10:00:01Z",
  "completed_at": "2026-02-15T10:05:00Z",
  "application": { "...": "..." },
  "urls": {
    "review_json": "https://bucket.nyc3.digitaloceanspaces.com/planning/25_01178_REM/output/rev_01JMABCDEF_review.json",
    "review_md": "https://bucket.nyc3.digitaloceanspaces.com/planning/25_01178_REM/output/rev_01JMABCDEF_review.md",
    "routes_json": "https://bucket.nyc3.digitaloceanspaces.com/planning/25_01178_REM/output/rev_01JMABCDEF_routes.json",
    "letter_md": null
  },
  "error": null
}
```

Local storage URLs use API-relative paths:
```json
{
  "urls": {
    "review_json": "/api/v1/files/25_01178_REM/output/rev_01JMABCDEF_review.json",
    "review_md": "/api/v1/files/25_01178_REM/output/rev_01JMABCDEF_review.md",
    "routes_json": "/api/v1/files/25_01178_REM/output/rev_01JMABCDEF_routes.json",
    "letter_md": null
  }
}
```

### New Endpoint: GET /api/v1/files/{file_path:path}

Serves output files from local storage. Returns 404 when S3 is configured.

- Success: 200 with file content, `Content-Type` based on extension
- Not found: 404
- Path traversal: 400 with `invalid_path` error code
- S3 mode: 404 with `local_files_not_available` error code

Content types: `.json` → `application/json`, `.md` → `text/markdown`, default → `application/octet-stream`

No authentication required (matches S3 public-read behaviour, per spec out-of-scope decision).

---

## Modified Components

### LocalStorageBackend

**Change Description:** Currently a null-object (all methods are no-ops). Must become active: `upload()` copies files to `/data/output/{key}`, `public_url()` returns API-relative paths.

**Dependants:** `_upload_review_output()`, `_upload_letter_output()` — both currently guard uploads behind `if storage.is_remote`; that guard is removed.

**Kind:** Class

**Details:**
```
class LocalStorageBackend:
    _output_dir: Path = Path("/data/output")  # NEW

    upload(local_path: Path, key: str) -> None:
        # NEW: copy file to _output_dir / key, creating parent dirs
        dest = self._output_dir / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dest)

    public_url(key: str) -> str | None:
        # CHANGED: return API-relative path instead of None
        return f"/api/v1/files/{key}"

    # is_remote, download_to, delete_local: unchanged
```

**Requirements References:**
- [review-output-urls:FR-001]: Local storage must persist output files
- [review-output-urls:FR-003]: Local storage must generate API-relative URLs

**Test Scenarios**

**TS-01: Upload writes file to output directory**
- Given: A LocalStorageBackend with a temp output directory
- When: `upload(local_path, "25_01178_REM/output/rev_xxx_review.json")` is called
- Then: File exists at `{output_dir}/25_01178_REM/output/rev_xxx_review.json` with same content

**TS-02: Upload creates parent directories**
- Given: A LocalStorageBackend with an empty output directory
- When: `upload()` is called with a nested key path
- Then: Intermediate directories are created automatically

**TS-03: Public URL returns API-relative path**
- Given: A LocalStorageBackend
- When: `public_url("25_01178_REM/output/rev_xxx_review.json")` is called
- Then: Returns `"/api/v1/files/25_01178_REM/output/rev_xxx_review.json"`

**TS-04: is_remote remains False**
- Given: A LocalStorageBackend
- When: `is_remote` is accessed
- Then: Returns `False`

---

### _upload_review_output()

**Change Description:** Currently uploads only `review.json` and `review.md` to S3. Must also write `routes.json`, and return a dict of URLs. The function is currently only called when `storage.is_remote` is True — that guard is removed by the caller.

**Dependants:** `_handle_success()` — uses the returned URLs dict.

**Kind:** Function in `src/worker/review_jobs.py`

**Details:**
```
def _upload_review_output(
    result: ReviewResult,
    review_data: dict[str, Any],
    storage: StorageBackend,
) -> dict[str, str | None]:                   # CHANGED: returns URL dict
    safe_ref = result.application_ref.replace("/", "_")
    prefix = f"{safe_ref}/output"
    urls = {"review_json": None, "review_md": None, "routes_json": None}

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Review JSON (existing)
            key = f"{prefix}/{result.review_id}_review.json"
            ... write and upload ...
            urls["review_json"] = storage.public_url(key)

            # Review markdown (existing)
            key = f"{prefix}/{result.review_id}_review.md"
            ... write and upload ...
            urls["review_md"] = storage.public_url(key)

            # Routes JSON (NEW)
            route_assessments = (review_data.get("review") or {}).get("route_assessments", [])
            key = f"{prefix}/{result.review_id}_routes.json"
            ... write route_assessments (or []) and upload ...
            urls["routes_json"] = storage.public_url(key)
    except Exception:
        logger.warning(...)

    return urls
```

**Requirements References:**
- [review-output-urls:FR-001]: Must write review_json, review_md, routes_json
- [review-output-urls:FR-003]: Must return URLs from storage.public_url()

**Test Scenarios**

**TS-05: Three files uploaded with correct keys**
- Given: A ReviewResult and InMemoryStorageBackend
- When: `_upload_review_output()` is called
- Then: Backend contains keys ending in `_review.json`, `_review.md`, `_routes.json`

**TS-06: Routes JSON contains route_assessments array**
- Given: A review_data with `review.route_assessments = [{"route": "A"}]`
- When: `_upload_review_output()` is called
- Then: The `_routes.json` file in storage contains `[{"route": "A"}]`

**TS-07: Routes JSON is empty array when no assessments**
- Given: A review_data with no `route_assessments` key
- When: `_upload_review_output()` is called
- Then: The `_routes.json` file in storage contains `[]`

**TS-08: Returns URL dict from storage.public_url()**
- Given: An InMemoryStorageBackend with base_url "https://test.com"
- When: `_upload_review_output()` is called
- Then: Returned dict has three non-null URLs matching the base_url pattern

**TS-09: Upload failure returns dict with null URLs**
- Given: A storage backend whose upload() raises an exception
- When: `_upload_review_output()` is called
- Then: Returns `{"review_json": None, "review_md": None, "routes_json": None}`, no exception raised

---

### _handle_success()

**Change Description:** Currently stores the result in Redis first, then uploads to S3 (if remote). Must reorder: upload first (to get URLs), add `output_urls` to result dict, then store in Redis.

**Dependants:** None (called by `process_review`)

**Kind:** Function in `src/worker/review_jobs.py`

**Details:**
```
async def _handle_success(result, redis_wrapper, storage=None) -> dict[str, Any]:
    review_data = { ... existing fields ... }

    # NEW: Upload output files (unconditional, both local and S3)
    output_urls = {"review_json": None, "review_md": None, "routes_json": None}
    if storage:
        output_urls = _upload_review_output(result, review_data, storage)
    review_data["output_urls"] = output_urls    # NEW field in result

    # Store result in Redis (NOW includes output_urls)
    if redis_wrapper:
        await redis_wrapper.store_result(result.review_id, review_data)

    # Fire webhooks (unchanged)
    ...
```

**Requirements References:**
- [review-output-urls:FR-001]: Upload is now unconditional (not gated on is_remote)
- [review-output-urls:FR-003]: output_urls stored in Redis result

**Test Scenarios**

**TS-10: output_urls included in stored result**
- Given: A successful ReviewResult and InMemoryStorageBackend
- When: `_handle_success()` is called
- Then: The result dict stored in Redis contains `output_urls` with three URL strings

**TS-11: output_urls has null values when no storage backend**
- Given: A successful ReviewResult with `storage=None`
- When: `_handle_success()` is called
- Then: `output_urls` in stored result has all null values

---

### _upload_letter_output()

**Change Description:** Currently uploads `letter.json` and `letter.md` to S3 only. Must also work for local storage and return the letter markdown URL.

**Dependants:** `letter_job()` — uses the returned URL.

**Kind:** Function in `src/worker/letter_jobs.py`

**Details:**
```
def _upload_letter_output(
    letter_id: str,
    application_ref: str,
    letter_content: str,
    metadata: dict[str, Any],
    storage: StorageBackend,
) -> str | None:                                # CHANGED: returns letter_md URL
    safe_ref = application_ref.replace("/", "_")
    prefix = f"{safe_ref}/output"
    letter_url = None

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Letter JSON (existing)
            ...

            # Letter markdown (existing)
            key = f"{prefix}/{letter_id}_letter.md"
            ... write and upload ...
            letter_url = storage.public_url(key)
    except Exception:
        logger.warning(...)

    return letter_url
```

**Requirements References:**
- [review-output-urls:FR-002]: Must persist letter.md for both backends
- [review-output-urls:FR-004]: Must return the letter URL

**Test Scenarios**

**TS-12: Letter files uploaded for both backends**
- Given: An InMemoryStorageBackend (or LocalStorageBackend)
- When: `_upload_letter_output()` is called
- Then: `_letter.json` and `_letter.md` files exist in storage

**TS-13: Returns letter markdown URL**
- Given: An InMemoryStorageBackend
- When: `_upload_letter_output()` is called
- Then: Returns a URL string ending in `_letter.md`

---

### letter_job()

**Change Description:** Currently gates upload on `if storage.is_remote`. Must remove that guard, capture the returned URL, store it in the letter record, and set the reverse-lookup Redis key.

**Dependants:** None (arq entry point)

**Kind:** Function in `src/worker/letter_jobs.py`

**Details:**
```
async def letter_job(ctx, letter_id, review_id) -> dict[str, Any]:
    ...
    # CHANGED: unconditional upload, capture URL
    letter_url = _upload_letter_output(
        letter_id, application_ref, letter_content, metadata, storage
    )

    # Update letter record with content AND output_url
    if redis_client:
        await redis_client.update_letter_status(
            letter_id, status="completed", content=letter_content,
            metadata=metadata, completed_at=now,
            output_url=letter_url,          # NEW parameter
        )
        # NEW: reverse lookup for review → letter URL
        if letter_url:
            await redis_client.set_review_letter_url(review_id, letter_url)
    ...
```

**Requirements References:**
- [review-output-urls:FR-002]: Unconditional upload
- [review-output-urls:FR-004]: Store letter URL in Redis

**Test Scenarios**

**TS-14: Letter URL stored in letter record**
- Given: A completed letter job with storage backend
- When: `letter_job()` completes
- Then: The letter record in Redis contains `output_url` field

**TS-15: Review letter URL reverse-lookup set**
- Given: A completed letter job
- When: `letter_job()` completes
- Then: Redis key `review_letter_url:{review_id}` contains the letter URL string

---

### update_letter_status()

**Change Description:** Must accept an optional `output_url` parameter and store it in the letter record.

**Dependants:** `letter_job()` passes the new parameter.

**Kind:** Method on `RedisClient` in `src/shared/redis_client.py`

**Details:**
```
async def update_letter_status(
    self, letter_id, status,
    content=None, metadata=None, error=None, completed_at=None,
    output_url: str | None = None,    # NEW
) -> bool:
    ...
    if output_url is not None:
        letter["output_url"] = output_url
    ...
```

**Requirements References:**
- [review-output-urls:FR-004]: Letter record stores output_url

**Test Scenarios**

**TS-16: output_url stored in letter record**
- Given: A letter record in Redis
- When: `update_letter_status(letter_id, "completed", output_url="/api/v1/files/...")` is called
- Then: `get_letter(letter_id)["output_url"]` returns the URL string

**TS-17: output_url omitted preserves existing record**
- Given: A letter record in Redis
- When: `update_letter_status(letter_id, "completed")` is called without output_url
- Then: The letter record does not have an `output_url` key (no None insertion)

---

### get_review()

**Change Description:** Must accept `urls_only` query parameter. Must build an `OutputUrls` object from the result's `output_urls` and the letter reverse-lookup. When `urls_only=true`, omit `review`, `metadata`, `site_boundary`.

**Dependants:** None (API endpoint)

**Kind:** Function in `src/api/routes/reviews.py`

**Details:**
```
@router.get("/reviews/{review_id}", response_model=ReviewResponse)
async def get_review(
    review_id: str,
    redis: RedisClientDep,
    urls_only: bool = False,       # NEW query parameter
) -> ReviewResponse:
    ...
    urls = None
    if job.status == COMPLETED:
        result = await redis.get_result(review_id)
        if result:
            application_info = result.get("application")

            # Build URLs (always, for nice-to-have)
            raw_urls = result.get("output_urls") or {}
            letter_url = await redis.get_review_letter_url(review_id)
            urls = OutputUrls(
                review_json=raw_urls.get("review_json"),
                review_md=raw_urls.get("review_md"),
                routes_json=raw_urls.get("routes_json"),
                letter_md=letter_url,
            )

            if not urls_only:
                review_content = result.get("review")
                metadata = result.get("metadata")
                if metadata:
                    site_boundary = metadata.get("site_boundary")

    return ReviewResponse(..., urls=urls)
```

**Requirements References:**
- [review-output-urls:FR-005]: urls_only parameter omits inline data, returns URLs

**Test Scenarios**

**TS-18: urls_only=true omits review and metadata**
- Given: A completed review with output_urls in Redis
- When: `GET /api/v1/reviews/{id}?urls_only=true`
- Then: Response has `urls` object with four keys; `review`, `metadata`, `site_boundary` are null

**TS-19: urls_only=false includes both data and URLs**
- Given: A completed review with output_urls in Redis
- When: `GET /api/v1/reviews/{id}` (default)
- Then: Response has `review`, `metadata`, `site_boundary`, AND `urls` object

**TS-20: urls_only has no effect on non-completed review**
- Given: A review with status "processing"
- When: `GET /api/v1/reviews/{id}?urls_only=true`
- Then: Response has status "processing", no `urls` field (null)

**TS-21: Older review without output_urls returns null URLs**
- Given: A completed review stored WITHOUT output_urls in Redis (pre-migration)
- When: `GET /api/v1/reviews/{id}?urls_only=true`
- Then: `urls` object has all null values

**TS-22: Letter URL included when letter exists**
- Given: A completed review AND a completed letter with `review_letter_url:{review_id}` set
- When: `GET /api/v1/reviews/{id}?urls_only=true`
- Then: `urls.letter_md` is non-null

---

### ReviewResponse schema

**Change Description:** Add optional `urls` field of type `OutputUrls`.

**Dependants:** `get_review()` populates this field.

**Kind:** Pydantic model in `src/api/schemas.py`

**Details:**
```
class ReviewResponse(BaseModel):
    ...existing fields...
    urls: OutputUrls | None = None    # NEW — before error field
    error: dict[str, Any] | None = None
```

**Requirements References:**
- [review-output-urls:FR-005]: Response includes URLs object

**Test Scenarios**

**TS-23: ReviewResponse serialises urls field**
- Given: A ReviewResponse with urls=OutputUrls(review_json="https://...")
- When: Serialised to JSON
- Then: JSON contains `"urls": {"review_json": "https://...", ...}`

**TS-24: ReviewResponse omits urls when None**
- Given: A ReviewResponse with urls=None
- When: Serialised to JSON
- Then: `"urls"` is `null` in the JSON

---

## Added Components

### OutputUrls

**Description:** Pydantic model for the four artefact URLs returned in the review response.

**Users:** `ReviewResponse` schema, `get_review()` handler.

**Kind:** Pydantic model

**Location:** `src/api/schemas.py`

**Details:**
```
class OutputUrls(BaseModel):
    review_json: str | None = None
    review_md: str | None = None
    routes_json: str | None = None
    letter_md: str | None = None
```

**Requirements References:**
- [review-output-urls:FR-005]: Response shape for URLs object

**Test Scenarios**

**TS-25: All fields default to None**
- Given: `OutputUrls()` with no arguments
- When: Serialised to JSON
- Then: All four fields are `null`

---

### Files router

**Description:** Serves output files from the local `/data/output/` directory. Returns 404 for all requests when S3 is configured. Validates paths against traversal attacks.

**Users:** Frontend fetching artefact URLs in local-storage deployments.

**Kind:** FastAPI router module

**Location:** `src/api/routes/files.py`

**Details:**
```
router = APIRouter()

OUTPUT_BASE_DIR = Path("/data/output")
CONTENT_TYPES = {".json": "application/json", ".md": "text/markdown"}

@router.get("/files/{file_path:path}")
async def serve_file(file_path: str) -> FileResponse:
    # Check if S3 is configured — if so, local files not served
    if os.getenv("S3_ENDPOINT_URL"):
        raise HTTPException(404, detail=error("local_files_not_available", ...))

    # Resolve and validate path
    resolved = (OUTPUT_BASE_DIR / file_path).resolve()
    if not resolved.is_relative_to(OUTPUT_BASE_DIR.resolve()):
        raise HTTPException(400, detail=error("invalid_path", ...))

    if not resolved.is_file():
        raise HTTPException(404, detail=error("file_not_found", ...))

    content_type = CONTENT_TYPES.get(resolved.suffix, "application/octet-stream")
    return FileResponse(resolved, media_type=content_type)
```

**Requirements References:**
- [review-output-urls:FR-006]: Serve files from /data/output with path validation

**Test Scenarios**

**TS-26: Serves existing JSON file with correct content type**
- Given: A file at `/data/output/25_01178_REM/output/rev_xxx_review.json`
- When: `GET /api/v1/files/25_01178_REM/output/rev_xxx_review.json`
- Then: 200 with `Content-Type: application/json` and file content

**TS-27: Serves existing markdown file**
- Given: A file at `/data/output/.../rev_xxx_review.md`
- When: `GET /api/v1/files/.../rev_xxx_review.md`
- Then: 200 with `Content-Type: text/markdown`

**TS-28: Returns 404 for non-existent file**
- Given: No file at the requested path
- When: `GET /api/v1/files/nonexistent/file.json`
- Then: 404 with `file_not_found` error code

**TS-29: Rejects path traversal**
- Given: A traversal path like `../../etc/passwd`
- When: `GET /api/v1/files/../../etc/passwd`
- Then: 400 with `invalid_path` error code

**TS-30: Returns 404 when S3 is configured**
- Given: `S3_ENDPOINT_URL` environment variable is set
- When: `GET /api/v1/files/any/path.json`
- Then: 404 with `local_files_not_available` error code

---

### set_review_letter_url() / get_review_letter_url()

**Description:** Simple Redis key helpers for the review → letter URL reverse lookup. Key: `review_letter_url:{review_id}`, value: URL string, TTL: 30 days (matching result TTL).

**Users:** `letter_job()` writes; `get_review()` reads.

**Kind:** Methods on `RedisClient`

**Location:** `src/shared/redis_client.py`

**Details:**
```
_LETTER_URL_TTL = 30 * 24 * 60 * 60  # 30 days

async def set_review_letter_url(self, review_id: str, url: str) -> None:
    client = await self._ensure_connected()
    await client.setex(f"review_letter_url:{review_id}", _LETTER_URL_TTL, url)

async def get_review_letter_url(self, review_id: str) -> str | None:
    client = await self._ensure_connected()
    val = await client.get(f"review_letter_url:{review_id}")
    return val.decode() if val else None
```

**Requirements References:**
- [review-output-urls:FR-004]: Letter URL reverse lookup from review_id

**Test Scenarios**

**TS-31: Set and get letter URL**
- Given: A RedisClient connected to fakeredis
- When: `set_review_letter_url("rev_123", "/api/v1/files/.../ltr_456_letter.md")` then `get_review_letter_url("rev_123")`
- Then: Returns the URL string

**TS-32: Returns None when no letter URL exists**
- Given: A RedisClient connected to fakeredis
- When: `get_review_letter_url("rev_nonexistent")`
- Then: Returns `None`

---

## Used Components

### StorageBackend Protocol
**Location:** `src/shared/storage.py:63-107`
**Provides:** `upload()`, `public_url()`, `is_remote` interface
**Used By:** Modified `LocalStorageBackend`, `_upload_review_output()`, `_upload_letter_output()`

### S3StorageBackend
**Location:** `src/shared/storage.py:165-393`
**Provides:** S3 upload with `S3_KEY_PREFIX`, public URL generation, retry logic
**Used By:** `_upload_review_output()`, `_upload_letter_output()` (unchanged)

### InMemoryStorageBackend
**Location:** `src/shared/storage.py:400-433`
**Provides:** In-memory storage for testing with predictable URLs
**Used By:** All unit tests for upload functions

### create_storage_backend()
**Location:** `src/shared/storage.py:441-493`
**Provides:** Factory selecting backend from environment variables
**Used By:** `process_review()`, `letter_job()` (unchanged)

### FastAPI FileResponse
**Location:** `fastapi.responses.FileResponse`
**Provides:** Streaming file response with content-type
**Used By:** Files router

---

## Documentation Considerations

- **`docs/API.md`**: Add section for `GET /api/v1/files/{file_path}` endpoint under a new "Files" heading in the table of contents. Update the `GET /reviews/{review_id}` section to document the `urls_only` query parameter and the `urls` response field with examples for both S3 and local URLs.
- **OpenAPI (`/docs`)**: FastAPI auto-generates from type annotations and docstrings. The `urls_only: bool = False` parameter, `OutputUrls` model, and `FileResponse` return type will appear automatically. Add `description` to the Query parameter for clarity.

---

## Integration Test Scenarios

**ITS-01: Review completion stores resolvable URLs (local storage)**
- Given: Local storage backend, no S3 configured
- When: A review is submitted and completes
- Then: `GET /reviews/{id}` returns `urls` with API-relative paths, and `GET` on each URL returns the correct file content
- Components Involved: review_jobs._handle_success, _upload_review_output, LocalStorageBackend, get_review, serve_file

**ITS-02: Letter URL appears in review response after letter generation**
- Given: A completed review
- When: A letter is generated via `POST /reviews/{id}/letter` and completes
- Then: `GET /reviews/{id}?urls_only=true` returns `urls.letter_md` pointing to the letter markdown
- Components Involved: letter_job, _upload_letter_output, set_review_letter_url, get_review

---

## Test Data

- Reuse existing `InMemoryStorageBackend` for storage tests
- Use `fakeredis` for Redis tests (existing pattern)
- Create minimal `ReviewResult` fixtures with `review.route_assessments` and `review.full_markdown`
- For file-serving tests, write temp files to a temp directory and override `OUTPUT_BASE_DIR`

---

## Test Feasibility

- All unit tests use existing test infrastructure (fakeredis, InMemoryStorageBackend, httpx TestClient)
- For `serve_file()` tests, `OUTPUT_BASE_DIR` can be overridden via monkeypatch or by making it configurable
- Integration tests ITS-01 and ITS-02 require a running API + worker with local storage — feasible in CI with docker-compose but may be QA-only initially

---

## Risks and Dependencies

| Risk | Impact | Mitigation |
|------|--------|------------|
| Reordering upload before Redis store in `_handle_success()` means a slow upload delays result availability | Low — local copies are fast, S3 uploads already non-blocking (fire-and-forget pattern stays) | Upload function has try/except; failures return null URLs and don't block |
| `LocalStorageBackend.upload()` change could affect document download flow | Medium — if `is_remote` guards are incorrectly removed from orchestrator | Only remove guards in `_upload_review_output` and `_upload_letter_output`; orchestrator document upload guard is not touched |
| `/data/output` volume not mounted in dev/test environments | Low — files fail to write, URLs are null, review still succeeds | Document in env setup; mkdir -p in LocalStorageBackend.upload() |
| Redis key `review_letter_url:{review_id}` TTL (30 days) could expire before letter record | Low — both use same TTL | Acceptable; expired data returns null URL |

---

## Feasibility Review

No blocking dependencies. All required infrastructure (storage abstraction, Redis, FastAPI) exists. The `shutil` module for local file copies is in the Python standard library.

---

## Task Breakdown

### Phase 1: Storage and Worker

**Task 1: Make LocalStorageBackend persist files and generate URLs**
- Status: Backlog
- Requirements: [review-output-urls:FR-001], [review-output-urls:FR-003]
- Test Scenarios: [review-output-urls:LocalStorageBackend/TS-01], [review-output-urls:LocalStorageBackend/TS-02], [review-output-urls:LocalStorageBackend/TS-03], [review-output-urls:LocalStorageBackend/TS-04]
- Details:
  - Add `__init__(self, output_dir="/data/output")` storing `self._output_dir = Path(output_dir)`
  - Implement `upload()`: resolve dest, mkdir parents, shutil.copy2
  - Change `public_url()`: return `/api/v1/files/{key}`
  - Update `create_storage_backend()` to pass output_dir to LocalStorageBackend
  - Add `import shutil` to storage.py

**Task 2: Update review output persistence**
- Status: Backlog
- Requirements: [review-output-urls:FR-001], [review-output-urls:FR-003]
- Test Scenarios: [review-output-urls:_upload_review_output/TS-05], [review-output-urls:_upload_review_output/TS-06], [review-output-urls:_upload_review_output/TS-07], [review-output-urls:_upload_review_output/TS-08], [review-output-urls:_upload_review_output/TS-09], [review-output-urls:_handle_success/TS-10], [review-output-urls:_handle_success/TS-11]
- Details:
  - Change `_upload_review_output()` return type to `dict[str, str | None]`
  - Add routes.json writing (extract `route_assessments` or `[]`)
  - Call `storage.public_url(key)` for each file and populate return dict
  - In `_handle_success()`: remove `if storage.is_remote` guard, capture returned URLs dict, add `review_data["output_urls"] = urls`, move `store_result()` AFTER upload

**Task 3: Update letter output persistence**
- Status: Backlog
- Requirements: [review-output-urls:FR-002], [review-output-urls:FR-004]
- Test Scenarios: [review-output-urls:_upload_letter_output/TS-12], [review-output-urls:_upload_letter_output/TS-13], [review-output-urls:letter_job/TS-14], [review-output-urls:letter_job/TS-15], [review-output-urls:update_letter_status/TS-16], [review-output-urls:update_letter_status/TS-17], [review-output-urls:set_review_letter_url/TS-31], [review-output-urls:set_review_letter_url/TS-32]
- Details:
  - Change `_upload_letter_output()` return type to `str | None`
  - Capture and return `storage.public_url(key)` for `_letter.md`
  - Add `output_url` parameter to `update_letter_status()`
  - Add `set_review_letter_url()` and `get_review_letter_url()` to `RedisClient`
  - In `letter_job()`: remove `if storage.is_remote` guard, capture returned URL, pass `output_url` to `update_letter_status()`, call `set_review_letter_url()`

### Phase 2: API Layer

**Task 4: Add OutputUrls schema and urls_only parameter**
- Status: Backlog
- Requirements: [review-output-urls:FR-005]
- Test Scenarios: [review-output-urls:OutputUrls/TS-25], [review-output-urls:ReviewResponse/TS-23], [review-output-urls:ReviewResponse/TS-24], [review-output-urls:get_review/TS-18], [review-output-urls:get_review/TS-19], [review-output-urls:get_review/TS-20], [review-output-urls:get_review/TS-21], [review-output-urls:get_review/TS-22]
- Details:
  - Add `OutputUrls` Pydantic model to `schemas.py`
  - Add `urls: OutputUrls | None = None` field to `ReviewResponse`
  - Add `urls_only: bool = False` query parameter to `get_review()`
  - Build `OutputUrls` from `result["output_urls"]` and `redis.get_review_letter_url()`
  - When `urls_only=True`: skip populating `review_content`, `metadata`, `site_boundary`

**Task 5: Add file serving endpoint**
- Status: Backlog
- Requirements: [review-output-urls:FR-006]
- Test Scenarios: [review-output-urls:FilesRouter/TS-26], [review-output-urls:FilesRouter/TS-27], [review-output-urls:FilesRouter/TS-28], [review-output-urls:FilesRouter/TS-29], [review-output-urls:FilesRouter/TS-30]
- Details:
  - Create `src/api/routes/files.py` with router
  - Implement `GET /files/{file_path:path}` handler with path validation
  - Register router in `create_app()` in `main.py` with `prefix="/api/v1"` and `tags=["files"]`
  - Use `FileResponse` from `fastapi.responses`

**Task 6: Update API documentation**
- Status: Backlog
- Requirements: [review-output-urls:FR-007]
- Test Scenarios: None (documentation task)
- Details:
  - Update `docs/API.md`: add Files section, update Reviews section with `urls_only` parameter and `urls` response field, add examples for S3 and local URLs
  - OpenAPI schema updates are automatic from type annotations

---

## Intermediate Dead Code Tracking

No dead code introduced — all changes are additive and immediately consumed.

---

## Intermediate Stub Tracking

No stubs required — all functionality can be fully implemented and tested in each phase.

---

## Appendix

### Glossary
- **Output artefact:** One of the four review output files (review_json, review_md, routes_json, letter_md)
- **safe_ref:** Application reference with `/` replaced by `_`
- **Reverse lookup key:** Redis key `review_letter_url:{review_id}` mapping a review to its latest letter URL

### References
- [Specification](specification.md)
- [src/shared/storage.py](../../src/shared/storage.py) — Storage abstraction
- [src/worker/review_jobs.py](../../src/worker/review_jobs.py) — Review worker
- [src/worker/letter_jobs.py](../../src/worker/letter_jobs.py) — Letter worker
- [src/api/routes/reviews.py](../../src/api/routes/reviews.py) — Reviews API
- [src/api/schemas.py](../../src/api/schemas.py) — API schemas

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-15 | SDD | Initial design |
