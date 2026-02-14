# Cherwell Scraper MCP Server -- API Reference

The Cherwell Scraper MCP server provides programmatic access to planning application data from the Cherwell District Council planning portal. It scrapes application metadata, lists associated documents, downloads files, and applies intelligent document filtering to exclude material not relevant to policy-based cycling reviews. The server runs on port **3001** (configurable) and exposes four tools over dual MCP transports.

---

## Transport & Authentication

All MCP servers in the stack share the same transport and authentication layer.

### Endpoints

| Path | Method(s) | Description |
|------|-----------|-------------|
| `/health` | GET | Health check. Returns `{"status": "ok"}`. No authentication required. |
| `/sse` | GET | SSE transport (legacy). Opens a long-lived Server-Sent Events stream for MCP communication. Used by the internal worker service. |
| `/messages/` | POST | SSE message posting endpoint. Clients send MCP messages here; responses arrive on the `/sse` stream. |
| `/mcp` | GET, POST, DELETE | Streamable HTTP transport (current MCP standard). Single endpoint for full request/response MCP communication. Used by external clients. |

### Authentication

Authentication is controlled by the `MCP_API_KEY` environment variable.

- **When `MCP_API_KEY` is set and non-empty:** all requests except `/health` must include an `Authorization: Bearer <token>` header whose token matches `MCP_API_KEY`.
- **When `MCP_API_KEY` is unset or empty:** authentication is disabled for backward compatibility.
- `/health` is always exempt from authentication.
- Token comparison uses `hmac.compare_digest` for constant-time, timing-attack-resistant comparison.
- Only the `Bearer` scheme is accepted. Other schemes (e.g. `Basic`) are rejected.
- Invalid or missing tokens return HTTP 401:
  ```json
  { "error": { "code": "unauthorized", "message": "..." } }
  ```
- Failed authentication attempts are logged at WARNING level with client IP, endpoint, and HTTP method.

---

## Tools

### `get_application_details`

Retrieves structured metadata for a planning application by scraping the Cherwell portal.

#### Input Parameters

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `application_ref` | string | Yes | -- | Planning application reference (e.g. `"25/01178/REM"`) |

#### Output (success)

```json
{
  "status": "success",
  "application": {
    "reference": "25/01178/REM",
    "address": "Land Adjacent To Braeburn, Ambrosden, OX25 2LT",
    "proposal": "Reserved matters application for 120 dwellings",
    "applicant": "Countryside Partnerships",
    "agent": "Turley Associates",
    "status": "Pending Consideration",
    "application_type": "Reserved Matters",
    "ward": "Ambrosden & Chesterton",
    "parish": "Ambrosden",
    "date_received": "2025-04-23",
    "date_validated": "2025-05-01",
    "target_date": "2025-07-31",
    "decision_date": null,
    "decision": null,
    "case_officer": "J Smith"
  }
}
```

#### Output (error)

```json
{
  "status": "error",
  "error_code": "application_not_found",
  "message": "Application not found: 25/99999/FAKE",
  "details": { "reference": "25/99999/FAKE" }
}
```

#### Behaviour

- Fetches the portal page at `{CHERWELL_PORTAL_URL}/Planning/Display/{reference}`.
- Parses HTML using multiple fallback strategies: Cherwell register format (`summaryTbl`), definition lists (`dl`/`dt`/`dd`), table format (`th`/`td`), and labelled spans/divs.
- Date fields are parsed from multiple formats: `DD/MM/YYYY`, `DD Mon YYYY`, `DD Month YYYY`, `YYYY-MM-DD`, `DD-MM-YYYY`.
- All metadata fields except `reference` are nullable; missing fields return `null` rather than failing.
- Detects "not found" pages via HTTP 404 or content heuristics (e.g. "application not found", "no results found", redirect to search page).

---

### `list_application_documents`

Lists all documents associated with a planning application, including paginated results. Documents are returned unfiltered.

#### Input Parameters

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `application_ref` | string | Yes | -- | Planning application reference (e.g. `"25/01178/REM"`) |

#### Output (success)

```json
{
  "status": "success",
  "application_ref": "25/01178/REM",
  "document_count": 42,
  "documents": [
    {
      "document_id": "a1b2c3d4e5f6",
      "description": "Transport Assessment",
      "document_type": "Supporting Documents",
      "date_published": "2025-05-15",
      "url": "https://planningregister.cherwell.gov.uk/Document/Download?module=PL&recordNumber=25/01178/REM&planId=...",
      "file_size": null
    }
  ]
}
```

#### Output (error)

```json
{
  "status": "error",
  "error_code": "application_not_found",
  "message": "Application not found: 25/99999/FAKE",
  "details": { "reference": "25/99999/FAKE" }
}
```

#### Behaviour

- Fetches all document pages, following pagination links up to a safety limit of 50 pages.
- `document_id` is a 12-character MD5 hash of the document download URL, providing stable IDs across invocations.
- `document_type` is populated from portal section headers (e.g. "Supporting Documents", "Public Comments", "Consultation Responses") when the portal uses a grouped table layout; `null` if no section header is found.
- `date_published` is parsed from the document table row when available.
- Multiple parser fallback strategies are applied in sequence: Cherwell register format (`singledownloadlink` elements in a table with section headers), flat link extraction, generic document table, list format (`ul`/`li`), and general PDF/document link discovery.
- Returns all documents unfiltered. Filtering is applied only by `download_all_documents`.

---

### `download_document`

Downloads a single document from a given URL to local storage.

#### Input Parameters

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `document_url` | string | Yes | -- | Full URL of the document to download |
| `output_dir` | string | Yes | -- | Directory to save the document |
| `filename` | string | No | URL-derived | Optional filename override; defaults to a name derived from the URL |

#### Output (success)

```json
{
  "status": "success",
  "file_path": "/data/raw/25_01178_REM/Transport_Assessment.pdf",
  "file_size": 1048576
}
```

#### Output (error)

```json
{
  "status": "error",
  "error_code": "download_failed",
  "message": "Download failed: HTTP 404 for https://..."
}
```

#### Behaviour

- If `filename` is not provided, extracts it from the `fileName` query parameter in the URL (Cherwell portal convention); falls back to the URL path segment; falls back to a hash-based name (`document_{hash}.pdf`).
- Disambiguates duplicate filenames in the same directory by appending `_1`, `_2`, etc. before the file extension.
- Creates the output directory if it does not exist.
- Downloads via streaming (8192-byte chunks) to handle large files without excessive memory use.
- Returns the actual file size in bytes after download.

---

### `download_all_documents`

Downloads all documents for a planning application with document filtering applied by default. Returns a detailed report of downloads and filtered documents.

#### Input Parameters

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `application_ref` | string | Yes | -- | Planning application reference |
| `output_dir` | string | Yes | -- | Base directory (a subdirectory named after the sanitised reference is created) |
| `skip_filter` | bool | No | `false` | Bypass all filtering and download every document |
| `include_consultation_responses` | bool | No | `false` | Include consultation/consultee responses that are normally filtered |
| `include_public_comments` | bool | No | `false` | Include public comments that are normally filtered |

#### Output (success)

```json
{
  "status": "success",
  "application_ref": "25/01178/REM",
  "output_dir": "/data/raw/25_01178_REM",
  "total_documents": 42,
  "downloaded_count": 28,
  "filtered_count": 14,
  "successful_downloads": 27,
  "failed_downloads": 1,
  "downloads": [
    {
      "document_id": "a1b2c3d4e5f6",
      "file_path": "/data/raw/25_01178_REM/001_Transport_Assessment.pdf",
      "file_size": 1048576,
      "success": true,
      "error": null,
      "description": "Transport Assessment",
      "document_type": "Supporting Documents",
      "url": "https://planningregister.cherwell.gov.uk/Document/Download?..."
    }
  ],
  "filtered_documents": [
    {
      "document_id": "f6e5d4c3b2a1",
      "description": "Comment from J Smith",
      "document_type": "Public Comments",
      "filter_reason": "Portal category: public comments - not relevant for policy review"
    }
  ]
}
```

#### Output (error)

```json
{
  "status": "error",
  "error_code": "application_not_found",
  "message": "Application not found: 25/99999/FAKE",
  "details": { "reference": "25/99999/FAKE" }
}
```

#### Behaviour

- Creates a subdirectory under `output_dir` using the sanitised reference (slashes replaced with underscores).
- Fetches and parses the full document list (with pagination) before applying the filter.
- Applies the 9-step document filter to separate documents into download and filtered lists (see Document Filtering below).
- Downloads are sequential, each respecting the configured rate limit.
- Downloaded files are named with a zero-padded index prefix and sanitised description: `{NNN}_{description}.pdf`.
- Filename sanitisation allows alphanumeric characters, dots, underscores, hyphens, and spaces; truncates to 100 characters.
- Individual download failures are captured in the response (with `success: false` and `error` message) rather than aborting the entire batch.
- When no documents are found, returns a success response with zero counts and empty arrays.

---

## Document Filtering

The `DocumentFilter` applies a 9-step filtering strategy to classify documents as relevant or irrelevant for policy-based cycling reviews. Filtering uses document metadata only (type/category and description text), never document content. All pattern matching is case-insensitive and uses substring containment.

### Filter Strategy

Steps are evaluated in order. The first matching step determines the outcome.

| Step | Rule | Decision | Override |
|------|------|----------|----------|
| 1 | **No metadata.** Both `document_type` and `description` are absent. | ALLOW | -- |
| 2 | **Portal category allowlist.** `document_type` exactly matches (case-insensitive) one of: `application forms`, `supporting documents`, `site plans`, `proposed plans`, `officer/committee consideration`, `decision and legal agreements`, `planning application documents`. | ALLOW | -- |
| 3 | **Portal category denylist -- consultation.** `document_type` matches `consultation responses` or `consultee responses`. | DENY | `include_consultation_responses=true` |
| 4 | **Portal category denylist -- public comments.** `document_type` matches `public comments`. | DENY | `include_public_comments=true` |
| 5 | **Title-based consultation response denylist.** Description or type contains: `consultation response`, `consultee response`, `statutory consultee`. Evaluated before the title-based allowlist to prevent false matches (e.g. "highway" in "OCC Highways consultation response"). | DENY | `include_consultation_responses=true` |
| 6 | **Title-based public comment denylist.** Description or type contains: `public comment`, `comment from`, `objection`, `representation from`, `letter from resident`, `letter from neighbour`, `letter of objection`, `letter of support`, `petition`. | DENY | `include_public_comments=true` |
| 7 | **Title-based allowlist.** Description or type contains any pattern from these groups: **Core documents** -- `planning statement`, `design and access`, `application form`, `proposed plan`, `site plan`, `location plan`, `elevation`, `floor plan`, `section`. **Transport** -- `transport assessment`, `transport statement`, `travel plan`, `highway`, `parking`. **Officer/decision** -- `officer report`, `committee report`, `decision notice`, `s106`, `legal agreement`. | ALLOW | -- |
| 8 | **Title-based non-transport denylist.** Description or type contains: `ecology`, `biodiversity`, `arboricultural`, `flood risk`, `drainage`, `noise`, `air quality`, `heritage statement`, `landscape`, `contamination`, and similar non-transport specialist topics. | DENY | -- |
| 9 | **Default (fail-safe).** No rule matched. | ALLOW | -- |

### Key Properties

- `skip_filter=true` bypasses all filtering and allows every document.
- Every filter decision is logged at INFO level with `application_ref`, `document_id`, `document_type`, `description`, `decision`, and `filter_reason`.
- Filtered documents include a human-readable `filter_reason` string in the response.
- Filtering is performed in-memory on already-fetched metadata. No additional HTTP requests are made. Filtering adds less than 10ms per document.

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `CHERWELL_PORTAL_URL` | `https://planningregister.cherwell.gov.uk` | Base URL of the Cherwell planning portal |
| `CHERWELL_SCRAPER_PORT` | `3001` | HTTP port for the MCP server |
| `SCRAPER_RATE_LIMIT` | `1.0` | Minimum seconds between portal requests (float) |
| `SCRAPER_TIMEOUT` | `30.0` | HTTP request timeout in seconds (float) |
| `SCRAPER_USER_AGENT` | `CherwellCycleReview/1.0` | User agent string (defined in docker-compose but not currently read by code; hardcoded value is used instead) |
| `MCP_API_KEY` | *(unset)* | Bearer token for MCP endpoint authentication; authentication is disabled when unset or empty |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

---

## Data Models

### ApplicationMetadata

Structured metadata for a planning application.

| Field | Type | Description |
|-------|------|-------------|
| `reference` | `str` | Application reference (always present) |
| `address` | `str \| null` | Site address |
| `proposal` | `str \| null` | Development proposal description |
| `applicant` | `str \| null` | Applicant name |
| `agent` | `str \| null` | Agent name |
| `status` | `str \| null` | Current application status |
| `application_type` | `str \| null` | Type of planning application |
| `ward` | `str \| null` | Electoral ward |
| `parish` | `str \| null` | Parish council area |
| `date_received` | `date \| null` | Date received (ISO 8601) |
| `date_validated` | `date \| null` | Date validated (ISO 8601) |
| `target_date` | `date \| null` | Target decision date (ISO 8601) |
| `decision_date` | `date \| null` | Actual decision date (ISO 8601) |
| `decision` | `str \| null` | Decision outcome |
| `case_officer` | `str \| null` | Assigned case officer |

### DocumentInfo

A single document associated with a planning application.

| Field | Type | Description |
|-------|------|-------------|
| `document_id` | `str` | 12-character MD5 hash of the download URL |
| `description` | `str` | Document description/title from the portal |
| `document_type` | `str \| null` | Portal section header category |
| `date_published` | `date \| null` | Publication date (ISO 8601) |
| `url` | `str \| null` | Full download URL |
| `file_size` | `int \| null` | File size in bytes (currently always `null`) |

### DownloadResult

Result of a single document download attempt.

| Field | Type | Description |
|-------|------|-------------|
| `document_id` | `str` | Document identifier |
| `file_path` | `str` | Local path where the file was saved |
| `file_size` | `int` | Size of downloaded file in bytes |
| `success` | `bool` | Whether the download succeeded |
| `error` | `str \| null` | Error message if the download failed |
| `description` | `str \| null` | Document description from the portal |
| `document_type` | `str \| null` | Document category from the portal |
| `url` | `str \| null` | Original download URL |

### FilteredDocumentInfo

A document that was excluded by the filter.

| Field | Type | Description |
|-------|------|-------------|
| `document_id` | `str` | Document identifier |
| `description` | `str` | Document description/title |
| `document_type` | `str \| null` | Document category |
| `filter_reason` | `str` | Human-readable reason for filtering |

---

## Error Codes

| Code | Description |
|------|-------------|
| `application_not_found` | The application reference does not exist on the portal |
| `rate_limited` | The portal returned HTTP 429 (Too Many Requests) |
| `request_failed` | Request to the portal failed after all retry attempts (3 retries with exponential backoff) |
| `download_failed` | Document download failed due to HTTP error or connection error |
| `internal_error` | Unexpected server-side error |

All error responses follow a consistent envelope:

```json
{
  "status": "error",
  "error_code": "<code>",
  "message": "<human-readable description>",
  "details": { ... }
}
```

The `details` object is optional and varies by error code. For `application_not_found` it contains the `reference` that was looked up. For `download_failed` it may contain the `url` that failed.
