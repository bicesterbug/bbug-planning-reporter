# Specification: Cherwell Scraper MCP Server API

**Version:** 1.0
**Date:** 2026-02-14
**Status:** As-Built

---

## Problem Statement

The BBug Planning Reporter system needs to programmatically access planning application data from the Cherwell District Council planning portal. The portal is a server-rendered HTML website with no public API. An MCP (Model Context Protocol) server is needed to scrape application metadata, list associated documents, and download document files, exposing these capabilities as structured tool calls that the agent orchestrator and external MCP clients can invoke.

## Beneficiaries

**Primary:**
- Worker service (agent orchestrator) that invokes scraper tools via SSE transport to gather application data for policy reviews
- External MCP clients connecting via Streamable HTTP transport for ad-hoc queries

**Secondary:**
- System operators who need health monitoring of the scraper container
- Developers extending the system with new scraping capabilities or portal format changes

---

## Outcomes

**Must Haves**
- Structured application metadata (address, proposal, status, dates, decision, case officer) extracted from portal HTML
- Complete document listings with download URLs, descriptions, types, and publication dates
- Reliable document downloading with filename derivation and deduplication
- Intelligent document filtering that excludes public comments and consultation responses by default, with per-request override toggles
- Dual MCP transport (SSE for internal worker, Streamable HTTP for external clients) on a single port
- Bearer token authentication on protocol endpoints with health check exemption
- Rate-limited, polite scraping with retry logic for transient errors

**Nice-to-haves**
- Configurable user agent string (env var defined but not yet wired to client)
- Filter effectiveness metrics

---

## Explicitly Out of Scope

- REST API endpoints for the main application (separate specification)
- Other MCP servers (document-store-mcp, policy-kb-mcp, cycle-route-mcp)
- ChromaDB vector storage and document embedding
- Agent orchestration logic and review workflow
- Support for planning portals other than Cherwell
- Content-based document classification (filtering uses metadata only)
- Retroactive filtering of already-downloaded documents

---

## Functional Requirements

### [cherwell-scraper-api:FR-001] Get Application Details

**Description:** The `get_application_details` tool accepts a planning application reference string and returns structured metadata scraped from the Cherwell planning portal HTML page.

**Input:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `application_ref` | string | Yes | Planning application reference (e.g., `"25/01178/REM"`) |

**Output (success):**
```json
{
  "status": "success",
  "application": {
    "reference": "25/01178/REM",
    "address": "string | null",
    "proposal": "string | null",
    "applicant": "string | null",
    "agent": "string | null",
    "status": "string | null",
    "application_type": "string | null",
    "ward": "string | null",
    "parish": "string | null",
    "date_received": "YYYY-MM-DD | null",
    "date_validated": "YYYY-MM-DD | null",
    "target_date": "YYYY-MM-DD | null",
    "decision_date": "YYYY-MM-DD | null",
    "decision": "string | null",
    "case_officer": "string | null"
  }
}
```

**Output (error -- application not found):**
```json
{
  "status": "error",
  "error_code": "application_not_found",
  "message": "Application not found: 25/99999/FAKE",
  "details": { "reference": "25/99999/FAKE" }
}
```

**Behaviour:**
- Fetches the portal page at `{CHERWELL_PORTAL_URL}/Planning/Display/{reference}`
- Parses HTML using multiple fallback strategies: Cherwell register format (summaryTbl), definition lists (dl/dt/dd), table format (th/td), and labelled spans/divs
- Date fields are parsed from multiple formats: `DD/MM/YYYY`, `DD Mon YYYY`, `DD Month YYYY`, `YYYY-MM-DD`, `DD-MM-YYYY`
- All metadata fields except `reference` are nullable; missing fields return `null` rather than failing
- Detects "not found" pages via HTTP 404 or content heuristics (e.g., "application not found", "no results found", redirect to search page)

### [cherwell-scraper-api:FR-002] List Application Documents

**Description:** The `list_application_documents` tool accepts a planning application reference and returns a list of all documents associated with that application, including paginated results.

**Input:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `application_ref` | string | Yes | Planning application reference (e.g., `"25/01178/REM"`) |

**Output (success):**
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
      "date_published": "YYYY-MM-DD | null",
      "url": "https://planningregister.cherwell.gov.uk/Document/Download?...",
      "file_size": null
    }
  ]
}
```

**Behaviour:**
- Fetches all document pages, following pagination links up to a safety limit of 50 pages
- `document_id` is a 12-character MD5 hash of the document download URL, providing stable IDs across invocations
- `document_type` is populated from portal section headers (e.g., "Supporting Documents", "Public Comments", "Consultation Responses") when the portal uses grouped table layout; `null` if no section header is found
- `date_published` is parsed from the document table row when available
- Multiple parser fallback strategies: Cherwell register format (singledownloadlink elements in a table with section headers), flat link extraction, generic document table, list format (ul/li), and general PDF/document link discovery
- Returns all documents unfiltered (filtering is applied only by `download_all_documents`)

### [cherwell-scraper-api:FR-003] Download Document

**Description:** The `download_document` tool downloads a single document from a given URL to local storage.

**Input:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `document_url` | string | Yes | Full URL of the document to download |
| `output_dir` | string | Yes | Directory to save the document |
| `filename` | string | No | Optional filename; defaults to URL-derived name |

**Output (success):**
```json
{
  "status": "success",
  "file_path": "/data/raw/25_01178_REM/document.pdf",
  "file_size": 1048576
}
```

**Behaviour:**
- If `filename` is not provided, extracts it from the `fileName` query parameter in the URL (Cherwell portal convention); falls back to the URL path segment; falls back to a hash-based name (`document_{hash}.pdf`)
- Disambiguates duplicate filenames in the same directory by appending `_1`, `_2`, etc. before the file extension
- Creates output directory if it does not exist
- Downloads via streaming (8192-byte chunks) to handle large files without excessive memory use
- Returns the actual file size in bytes after download

### [cherwell-scraper-api:FR-004] Download All Documents

**Description:** The `download_all_documents` tool downloads all documents for a planning application, applying document filtering by default, and returns a detailed report of downloads and filtered documents.

**Input:**
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `application_ref` | string | Yes | -- | Planning application reference |
| `output_dir` | string | Yes | -- | Base directory (a subdirectory named after the sanitised reference is created) |
| `skip_filter` | bool | No | `false` | Bypass all filtering and download every document |
| `include_consultation_responses` | bool | No | `false` | Include consultation/consultee responses that are normally filtered |
| `include_public_comments` | bool | No | `false` | Include public comments that are normally filtered |

**Output (success):**
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
      "url": "https://..."
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

**Behaviour:**
- Creates a subdirectory under `output_dir` using the sanitised reference (slashes replaced with underscores)
- Fetches and parses the full document list (with pagination) before applying the filter
- Applies `DocumentFilter` to separate documents into download and filtered lists (see [cherwell-scraper-api:FR-005])
- Downloads are sequential, each respecting the configured rate limit
- Downloaded files are named with a zero-padded index prefix and sanitised description: `{NNN}_{description}.pdf`
- Filename sanitisation allows alphanumeric characters, dots, underscores, hyphens, and spaces; truncates to 100 characters
- Individual download failures are captured in the response (with `success: false` and `error` message) rather than aborting the entire batch
- When no documents are found, returns a success response with zero counts and empty arrays

### [cherwell-scraper-api:FR-005] Document Filtering

**Description:** The `DocumentFilter` applies a multi-layer filtering strategy to classify documents as relevant or irrelevant for policy-based cycling reviews. Filtering uses document metadata only (type/category and description text), not document content.

**Filter Strategy (evaluated in order):**

1. **No metadata (fail-safe):** If both `document_type` and `description` are absent, the document is ALLOWED.
2. **Portal category allowlist:** If `document_type` exactly matches (case-insensitive) a known allowlist category, the document is ALLOWED. Categories: `application forms`, `supporting documents`, `site plans`, `proposed plans`, `officer/committee consideration`, `decision and legal agreements`, `planning application documents`.
3. **Portal category denylist -- consultation:** If `document_type` matches `consultation responses` or `consultee responses`, the document is DENIED (unless `include_consultation_responses=true`).
4. **Portal category denylist -- public comments:** If `document_type` matches `public comments`, the document is DENIED (unless `include_public_comments=true`).
5. **Title-based consultation response denylist:** If any match text contains patterns like `consultation response`, `consultee response`, `statutory consultee`, the document is DENIED (unless `include_consultation_responses=true`). Checked before allowlist to prevent false matches (e.g., "highway" in "OCC Highways consultation response").
6. **Title-based public comment denylist:** If any match text contains patterns like `public comment`, `comment from`, `objection`, `representation from`, `letter from resident`, `letter from neighbour`, `letter of objection`, `letter of support`, `petition`, the document is DENIED (unless `include_public_comments=true`).
7. **Title-based allowlist:** If any match text contains a pattern from core documents (e.g., `planning statement`, `design and access`, `application form`, `proposed plan`, `site plan`, `location plan`, `elevation`, `floor plan`, `section`), transport assessments (e.g., `transport assessment`, `transport statement`, `travel plan`, `highway`, `parking`), or officer/decision documents (e.g., `officer report`, `committee report`, `decision notice`, `s106`, `legal agreement`), the document is ALLOWED.
8. **Title-based non-transport denylist:** If any match text contains patterns like `ecology`, `biodiversity`, `arboricultural`, `flood risk`, `drainage`, `noise`, `air quality`, `heritage statement`, `landscape`, `contamination`, etc., the document is DENIED.
9. **Default (fail-safe):** If no rule matches, the document is ALLOWED.

**Key properties:**
- All pattern matching is case-insensitive and uses substring containment
- `skip_filter=true` bypasses all filtering and allows every document
- Every filter decision is logged at INFO level with `application_ref`, `document_id`, `document_type`, `description`, `decision`, and `filter_reason`
- Filtered documents include a human-readable `filter_reason` string in the response

### [cherwell-scraper-api:FR-006] Transport Protocol

**Description:** The server exposes dual MCP transports on a single HTTP port, plus a health endpoint.

**Endpoints:**
| Path | Method(s) | Description |
|------|-----------|-------------|
| `/health` | GET | Health check, returns `{"status": "ok"}`. Exempt from authentication. |
| `/sse` | GET | SSE transport endpoint (legacy). Used by the internal worker service. Establishes a Server-Sent Events stream for MCP communication. |
| `/messages/` | POST | SSE message posting endpoint. Client sends MCP messages to this endpoint; responses arrive via the `/sse` stream. |
| `/mcp` | GET, POST, DELETE | Streamable HTTP transport endpoint (current MCP standard). Used by external clients. |

**Behaviour:**
- Server runs on port 3001 (configurable via `CHERWELL_SCRAPER_PORT` env var, default `3001`)
- Hosted by Uvicorn with `host=0.0.0.0`
- SSE transport uses `SseServerTransport` from `mcp.server.sse`
- Streamable HTTP transport uses `StreamableHTTPSessionManager` from `mcp.server.streamable_http_manager`
- The Starlette app lifecycle manages the `StreamableHTTPSessionManager` run context

### [cherwell-scraper-api:FR-007] Authentication

**Description:** Bearer token authentication is enforced on all MCP protocol endpoints when the `MCP_API_KEY` environment variable is set.

**Behaviour:**
- When `MCP_API_KEY` is set and non-empty, all requests (except `/health`) must include an `Authorization: Bearer <token>` header with a token matching `MCP_API_KEY`
- When `MCP_API_KEY` is not set or empty, authentication is disabled (no-op middleware) for backward compatibility
- `/health` is always exempt from authentication
- Token comparison uses `hmac.compare_digest` for constant-time comparison (timing-attack resistant)
- Invalid or missing tokens return HTTP 401 with `{"error": {"code": "unauthorized", "message": "..."}}`
- Only the `Bearer` auth scheme is accepted; other schemes (e.g., `Basic`) are rejected
- Failed authentication attempts are logged at WARNING level with client IP, endpoint, and HTTP method

---

## Non-Functional Requirements

### [cherwell-scraper-api:NFR-001] Rate Limiting

**Category:** Politeness / External Service Protection
**Description:** All HTTP requests to the Cherwell portal are rate-limited to avoid overloading the council's server. An async lock ensures only one request is in flight at a time, with a configurable minimum interval between requests.
**Configuration:** `SCRAPER_RATE_LIMIT` environment variable (float, seconds). Default: `1.0` (one request per second).
**Implementation:** `CherwellClient._wait_for_rate_limit()` uses `asyncio.Lock` and `time.monotonic()` to enforce the interval.

### [cherwell-scraper-api:NFR-002] User Agent

**Category:** Identification
**Description:** All HTTP requests to the Cherwell portal include a descriptive User-Agent header identifying the bot.
**Current value:** `BBug-Planning-Reporter/1.0 (Planning Application Review Bot; +https://github.com/example/bbug)` (hardcoded in `CherwellClient.USER_AGENT`).
**Note:** The `SCRAPER_USER_AGENT` environment variable is defined in docker-compose but is not currently read by the client code. The hardcoded value is used.

### [cherwell-scraper-api:NFR-003] Robustness

**Category:** Reliability
**Description:** The scraper handles common web scraping challenges including session management, anti-scraping tokens, transient errors, and paginated content.
**Details:**
- **Retry with backoff:** Requests are retried up to 3 times on 5xx errors, HTTP 429 (rate limit), timeouts, and connection errors. Backoff starts at 1 second and doubles each attempt (exponential backoff), capped at 60 seconds for 429 responses.
- **Redirect handling:** `httpx.AsyncClient` is configured with `follow_redirects=True`.
- **Timeout:** Configurable request timeout (default 30 seconds) via `SCRAPER_TIMEOUT` env var.
- **Pagination:** Document lists are fetched across up to 50 pages, following "Next" links or page number navigation.
- **HTML parsing resilience:** Multiple parser strategies are tried in sequence; partial data is returned rather than failing entirely.
- **Not-found detection:** Both HTTP 404 and content-based heuristics detect invalid application references.

### [cherwell-scraper-api:NFR-004] Filter Performance

**Category:** Performance
**Description:** Document filtering decisions are made in-memory using string pattern matching on already-fetched metadata. No additional HTTP requests are made during filtering.
**Acceptance Threshold:** Filtering adds less than 10ms per document.

### [cherwell-scraper-api:NFR-005] Memory

**Category:** Resource Limits
**Description:** The cherwell-scraper-mcp container is limited to 2GB of memory in the production deployment configuration.
**Verification:** Deploy resource limit in `deploy/docker-compose.yml`.

---

## Open Questions

1. The `SCRAPER_USER_AGENT` environment variable is defined in docker-compose but not wired to `CherwellClient`. Should it replace the hardcoded `USER_AGENT` class attribute?
2. The pagination safety limit is hardcoded at 50 pages. Is this sufficient for all applications, and should it be configurable?
3. The `file_size` field on `DocumentInfo` is always `null` in practice (the portal does not expose file size in the document table). Should this field be removed from the listing response, or is it kept for future use?

---

## Appendix

### Glossary

- **MCP (Model Context Protocol):** A protocol for exposing tool capabilities to AI agents. Servers register tools with typed input schemas; clients invoke them by name.
- **SSE (Server-Sent Events):** A legacy MCP transport where the client opens a long-lived event stream (`/sse`) and posts messages to a separate endpoint (`/messages/`).
- **Streamable HTTP:** The current MCP standard transport using standard HTTP request/response on a single endpoint (`/mcp`).
- **Portal category:** The section header in the Cherwell planning register's document table (e.g., "Supporting Documents", "Public Comments"). Used as `document_type` on `DocumentInfo`.
- **Fail-safe filtering:** The principle that unknown or unclassifiable documents are allowed through rather than blocked, to avoid missing relevant material.

### Data Models

**ApplicationMetadata** (`src/mcp_servers/cherwell_scraper/models.py`):
- `reference: str` -- Application reference (always present)
- `address: str | None` -- Site address
- `proposal: str | None` -- Development proposal description
- `applicant: str | None` -- Applicant name
- `agent: str | None` -- Agent name
- `status: str | None` -- Current application status
- `application_type: str | None` -- Type of planning application
- `ward: str | None` -- Electoral ward
- `parish: str | None` -- Parish council area
- `date_received: date | None` -- Date received (serialised as ISO 8601)
- `date_validated: date | None` -- Date validated
- `target_date: date | None` -- Target decision date
- `decision_date: date | None` -- Actual decision date
- `decision: str | None` -- Decision outcome
- `case_officer: str | None` -- Assigned case officer

**DocumentInfo** (`src/mcp_servers/cherwell_scraper/models.py`):
- `document_id: str` -- 12-char MD5 hash of the download URL
- `description: str` -- Document description/title from the portal
- `document_type: str | None` -- Portal section header category
- `date_published: date | None` -- Publication date (serialised as ISO 8601)
- `url: str | None` -- Full download URL
- `file_size: int | None` -- File size in bytes (currently always `null`)

**DownloadResult** (`src/mcp_servers/cherwell_scraper/models.py`):
- `document_id: str` -- Document identifier
- `file_path: str` -- Local path where file was saved
- `file_size: int` -- Size of downloaded file in bytes
- `success: bool` -- Whether download succeeded
- `error: str | None` -- Error message if download failed
- `description: str | None` -- Document description from portal
- `document_type: str | None` -- Document category from portal
- `url: str | None` -- Original download URL

**FilteredDocumentInfo** (`src/mcp_servers/cherwell_scraper/filters.py`):
- `document_id: str` -- Document identifier
- `description: str` -- Document description/title
- `document_type: str | None` -- Document category
- `filter_reason: str` -- Human-readable reason for filtering

### Error Codes

| Code | Description |
|------|-------------|
| `application_not_found` | The application reference does not exist on the portal |
| `rate_limited` | The portal returned HTTP 429 |
| `request_failed` | Request failed after all retry attempts |
| `download_failed` | Document download failed (HTTP error or connection error) |
| `internal_error` | Unexpected server-side error |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CHERWELL_PORTAL_URL` | `https://planningregister.cherwell.gov.uk` | Base URL of the Cherwell planning portal |
| `CHERWELL_SCRAPER_PORT` | `3001` | HTTP port for the MCP server |
| `SCRAPER_RATE_LIMIT` | `1.0` | Minimum seconds between portal requests |
| `SCRAPER_TIMEOUT` | `30.0` | HTTP request timeout in seconds |
| `SCRAPER_USER_AGENT` | `CherwellCycleReview/1.0` | User agent string (defined in docker-compose but not currently read by code) |
| `MCP_API_KEY` | *(unset)* | Bearer token for MCP endpoint authentication; auth disabled when unset |
| `LOG_LEVEL` | `INFO` | Logging level |

### Source Files

| File | Purpose |
|------|---------|
| `src/mcp_servers/cherwell_scraper/server.py` | MCP server setup, tool registration, tool handlers |
| `src/mcp_servers/cherwell_scraper/client.py` | HTTP client with rate limiting, retry, and portal interaction |
| `src/mcp_servers/cherwell_scraper/parsers.py` | HTML parsers for application details and document tables |
| `src/mcp_servers/cherwell_scraper/models.py` | Data models (ApplicationMetadata, DocumentInfo, DownloadResult) |
| `src/mcp_servers/cherwell_scraper/filters.py` | Document filtering logic (DocumentFilter, FilteredDocumentInfo) |
| `src/mcp_servers/shared/transport.py` | Shared dual-transport Starlette app factory |
| `src/mcp_servers/shared/auth.py` | Bearer token authentication middleware |

### References

- [Cherwell Planning Register](https://planningregister.cherwell.gov.uk/) -- Target portal
- [Model Context Protocol](https://modelcontextprotocol.io/) -- MCP specification
- [document-filtering specification](../document-filtering/specification.md) -- Original filtering requirements
- [review-scope-control specification](../review-scope-control/specification.md) -- Consultation/public comment toggle requirements
- [foundation-api specification](../foundation-api/specification.md) -- Original scraper requirements

### Change History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-14 | Claude Opus 4.6 | Initial as-built specification |
