# Specification: Policy KB MCP Server API

**Version:** 1.0
**Date:** 2026-02-14
**Status:** As-Built

---

## Problem Statement

The review agent and external MCP clients (Claude Desktop, n8n) need a well-defined tool interface to search, retrieve, and manage policy documents stored in the Policy Knowledge Base. The MCP server must expose tools that handle temporal filtering (selecting the correct policy revision for a given date), semantic search across chunked policy text, section-level retrieval, and ingestion lifecycle management. Without a stable, documented tool contract, callers cannot reliably query policies or reason about which revision was in force when a planning application was submitted.

## Beneficiaries

**Primary:**
- AI review agent that calls policy tools via MCP during planning application assessment
- API worker that invokes `ingest_policy_revision` and `remove_policy_revision` during revision upload and reindex jobs

**Secondary:**
- External MCP clients (Claude Desktop, n8n) connecting over Streamable HTTP for ad-hoc policy queries
- System administrators managing the policy knowledge base
- Auditors verifying that reviews cited the correct policy revision for the application's validation date

---

## Outcomes

**Must Haves**
- Six MCP tools exposed: `search_policy`, `get_policy_section`, `list_policy_documents`, `list_policy_revisions`, `ingest_policy_revision`, `remove_policy_revision`
- Temporal filtering on `search_policy` restricts results to chunks from revisions in force on the supplied `effective_date`
- Redis-backed policy registry is the source of truth for policy metadata; ChromaDB `policy_docs` collection stores embeddings and chunk text
- Dual transport: SSE (legacy, `/sse`) and Streamable HTTP (current, `/mcp`)
- Bearer token authentication via `MCP_API_KEY` environment variable with `/health` exempt

**Nice-to-haves**
- Batch search across multiple queries in a single tool call
- Streaming chunk results for large result sets

---

## Explicitly Out of Scope

- REST API policy endpoints (covered by the `policy-knowledge-base` specification; these are Starlette/FastAPI routes, not MCP tools)
- Document Store MCP server for application documents (separate service on port 3002)
- Policy PDF fetching from gov.uk (manual or scripted upload only)
- AI-generated policy summaries or diff analysis between revisions
- OAuth 2.1 support (bearer token only at this time)

---

## Functional Requirements

### [policy-kb-api:FR-001] search_policy Tool

**Description:** Semantic search across policy document chunks with optional temporal and source filtering. Returns ranked results ordered by relevance score.

**Input Schema:**
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | Yes | - | Natural language search query |
| `sources` | string[] | No | null | Filter results to specific policy source slugs (e.g. `["LTN_1_20", "NPPF"]`) |
| `effective_date` | string (YYYY-MM-DD) | No | null | ISO date for temporal filtering; only returns chunks from revisions in force on this date |
| `n_results` | int | No | 10 | Maximum number of results to return |

**Output Schema:**
```json
{
  "status": "success",
  "query": "<original query>",
  "effective_date": "<date or null>",
  "results_count": 5,
  "results": [
    {
      "chunk_id": "LTN_1_20__rev_LTN_1_20_2020_07__Chapter_5__042",
      "text": "...",
      "relevance_score": 0.87,
      "source": "LTN_1_20",
      "revision_id": "rev_LTN_1_20_2020_07",
      "version_label": "July 2020",
      "section_ref": "Chapter 5",
      "page_number": 42
    }
  ]
}
```

**Behaviour:**
- Query text is embedded using the configured embedding model (`all-MiniLM-L6-v2` by default) and searched against the `policy_docs` ChromaDB collection.
- When `effective_date` is provided, a ChromaDB `$and` where filter is applied: `effective_from <= date_int AND effective_to >= date_int`. Dates are stored as integer YYYYMMDD; `effective_to` of `99991231` represents "currently in force".
- When `sources` is provided, an additional `source` filter (single value or `$in` for multiple) is appended to the where clause.
- Relevance score is computed as `max(0, 1 - (distance / 2))` from ChromaDB's L2 distance.
- Invalid `effective_date` format returns `{"status": "error", "error_type": "invalid_date", "message": "..."}`.

**Examples:**
- Positive: Search with `effective_date="2024-03-15"` returns only chunks from revisions valid on that date.
- Positive: Search with `sources=["LTN_1_20"]` returns only LTN 1/20 chunks.
- Edge case: Date before any revision exists returns `results_count: 0` with empty results array.
- Edge case: No `effective_date` returns chunks from all revisions (including superseded).

### [policy-kb-api:FR-002] get_policy_section Tool

**Description:** Retrieve a specific policy section by source slug and section reference. Combines text from all matching chunks for the section.

**Input Schema:**
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source` | string | Yes | - | Policy source slug (e.g. `LTN_1_20`) |
| `section_ref` | string | Yes | - | Section reference (e.g. `Chapter 5`, `Table 5-2`, `Para 116`) |
| `revision_id` | string | No | null | Specific revision ID; defaults to latest effective revision |

**Output Schema (success):**
```json
{
  "status": "success",
  "source": "LTN_1_20",
  "section_ref": "Chapter 5",
  "revision_id": "rev_LTN_1_20_2020_07",
  "version_label": "July 2020",
  "text": "...",
  "page_numbers": [38, 39, 40]
}
```

**Behaviour:**
- The `section_ref` string is embedded and searched against the `policy_docs` collection filtered by `source` (and optionally `revision_id`), retrieving up to 20 results.
- Results are post-filtered for exact `section_ref` match on chunk metadata.
- Matching chunks are concatenated (joined by double newline) and deduplicated page numbers are returned sorted.
- If no chunks match, returns `{"status": "error", "error_type": "section_not_found", "message": "..."}`.

**Examples:**
- Positive: `get_policy_section("LTN_1_20", "Table 5-2")` returns the table content with page numbers.
- Positive: `get_policy_section("NPPF", "Para 116", revision_id="rev_NPPF_2023_09")` returns the paragraph from the September 2023 NPPF.
- Edge case: Non-existent section returns `section_not_found` error.

### [policy-kb-api:FR-003] list_policy_documents Tool

**Description:** List all registered policy documents with source slug, title, and category. Takes no input parameters.

**Input Schema:** Empty object `{}`.

**Output Schema:**
```json
{
  "status": "success",
  "policy_count": 6,
  "policies": [
    {
      "source": "LTN_1_20",
      "title": "Cycle Infrastructure Design (LTN 1/20)",
      "category": "national_guidance"
    }
  ]
}
```

**Behaviour:**
- Reads from the Redis policy registry via `PolicyRegistry.list_policies()`.
- The `policies_all` Redis set provides the list of all source slugs; each policy's metadata is read from the `policy:{source}` hash.
- Category is serialized from the `PolicyCategory` enum: `national_policy`, `national_guidance`, `local_plan`, `local_guidance`, `county_strategy`.
- If the registry is unavailable (not configured), returns `{"status": "error", "error_type": "registry_unavailable", "message": "..."}`.

**Examples:**
- Positive: Returns all 6 seed policies with correct titles and categories.
- Edge case: Empty registry returns `policy_count: 0` with empty array.

### [policy-kb-api:FR-004] list_policy_revisions Tool

**Description:** List all revisions for a specific policy document, ordered by `effective_from` descending (newest first).

**Input Schema:**
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source` | string | Yes | - | Policy source slug |

**Output Schema:**
```json
{
  "status": "success",
  "source": "NPPF",
  "revision_count": 2,
  "revisions": [
    {
      "revision_id": "rev_NPPF_2024_12",
      "version_label": "December 2024",
      "effective_from": "2024-12-12",
      "effective_to": null,
      "status": "active",
      "chunk_count": 73
    },
    {
      "revision_id": "rev_NPPF_2023_09",
      "version_label": "September 2023",
      "effective_from": "2023-09-05",
      "effective_to": "2024-12-11",
      "status": "superseded",
      "chunk_count": 73
    }
  ]
}
```

**Behaviour:**
- Reads from the Redis sorted set `policy_revisions:{source}` (sorted by `effective_from` as days-since-epoch score) in reverse order.
- Each revision's metadata is read from the `policy_revision:{source}:{revision_id}` hash.
- Status values are from the `RevisionStatus` enum: `processing`, `active`, `superseded`, `failed`.
- `effective_to` is `null` for the currently-in-force revision.

**Examples:**
- Positive: NPPF returns 2 revisions (Dec 2024 active, Sep 2023 superseded).
- Edge case: Policy with single revision returns array of one.

### [policy-kb-api:FR-005] ingest_policy_revision Tool

**Description:** Ingest a policy revision PDF into the ChromaDB vector store. Extracts text, chunks, embeds, and stores with temporal metadata. Called by the API worker during revision upload or reindex.

**Input Schema:**
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source` | string | Yes | - | Policy source slug |
| `revision_id` | string | Yes | - | Revision ID |
| `file_path` | string | Yes | - | Path to PDF file on disk |
| `reindex` | bool | No | false | If true, delete existing chunks before re-ingesting |

**Output Schema (success):**
```json
{
  "status": "success",
  "source": "LTN_1_20",
  "revision_id": "rev_LTN_1_20_2020_07",
  "chunks_created": 160,
  "page_count": 132,
  "extraction_method": "pdfplumber"
}
```

**Behaviour:**
- Validates that the file exists and the revision is registered in the policy registry.
- If `reindex` is true, calls `PolicyChromaClient.delete_revision_chunks()` to clear existing chunks first.
- Text extraction uses `DocumentProcessor` with OCR enabled (`enable_ocr=True`).
- Text is chunked via `TextChunker` operating on page-level text.
- Embeddings are generated in batch via `EmbeddingService.embed_batch()`.
- Each chunk's metadata includes: `source`, `source_title`, `revision_id`, `version_label`, `effective_from` (int YYYYMMDD), `effective_to` (int YYYYMMDD, `99991231` for current), `section_ref`, `page_number`, `chunk_index`.
- Chunk IDs are deterministic: `{source}__{revision_id}__{section_ref}__{chunk_index:03d}`.
- Chunks are upserted to ChromaDB in a single batch call.
- Returns error if file not found, revision not found in registry, or no text extracted.

**Examples:**
- Positive: Ingest LTN 1/20 PDF produces 160 chunks across 132 pages.
- Positive: Reindex clears existing chunks then re-creates them.
- Edge case: PDF with no extractable text returns `{"status": "error", "error_type": "no_content"}`.

### [policy-kb-api:FR-006] remove_policy_revision Tool

**Description:** Remove all chunks for a policy revision from the ChromaDB vector store.

**Input Schema:**
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source` | string | Yes | - | Policy source slug |
| `revision_id` | string | Yes | - | Revision ID |

**Output Schema:**
```json
{
  "status": "success",
  "source": "NPPF",
  "revision_id": "rev_NPPF_2023_09",
  "chunks_removed": 73
}
```

**Behaviour:**
- Queries ChromaDB for all chunks matching `source` AND `revision_id` metadata, then deletes them by ID.
- Returns `chunks_removed: 0` if no chunks found (not an error).
- Does not modify the policy registry; registry deletion is handled separately by the REST API.

**Examples:**
- Positive: Removing NPPF Sep 2023 revision deletes 73 chunks.
- Edge case: Removing a revision with no chunks returns `chunks_removed: 0`.

### [policy-kb-api:FR-007] Temporal Query Resolution

**Description:** The system resolves which policy revision was in force on a given date using effective date ranges stored on each revision and encoded into ChromaDB chunk metadata.

**Mechanism:**
- Each revision has `effective_from` (date) and `effective_to` (date or null). A null `effective_to` means the revision is currently in force.
- When a new revision is created with `effective_from` after an existing open-ended revision, auto-supersession sets the existing revision's `effective_to` to `effective_from - 1 day`.
- ChromaDB stores dates as integer YYYYMMDD format. A null `effective_to` is stored as `99991231` (far future sentinel).
- Temporal search filter: `effective_from <= date_int AND effective_to >= date_int` applied as a ChromaDB `$and` where clause.
- The `EffectiveDateResolver` class provides higher-level resolution: `resolve_for_policy()` finds the effective revision for a single policy, `resolve_snapshot()` resolves all policies for a date, and `get_revision_ids_for_date()` returns a source-to-revision-ID mapping for ChromaDB filtering.
- Resolution considers only `active` and `superseded` status revisions; `processing` and `failed` are skipped.

**Redis Sorted Set for Efficient Lookup:**
- Key: `policy_revisions:{source}` - sorted set with revision IDs scored by `effective_from` as days since Unix epoch.
- Lookup uses `ZREVRANGEBYSCORE` with max score = target date to find candidate revisions, then checks `effective_to` on each.

**Edge Cases:**
- Date before any revision: returns no results / `reason: "date_before_first_revision"`.
- Date in gap between revisions: returns no results / `reason: "date_in_gap"`.
- Date on exact `effective_from` boundary: revision is included (inclusive).
- Date on exact `effective_to` boundary: revision is included (inclusive).

### [policy-kb-api:FR-008] Policy Registry

**Description:** Redis-backed catalogue of policies and revisions. Acts as the source of truth for policy metadata; ChromaDB stores only embeddings and chunk text.

**Redis Key Schema:**
| Key Pattern | Type | Description |
|-------------|------|-------------|
| `policy:{source}` | Hash | Policy document metadata (source, title, description, category, created_at, updated_at) |
| `policy_revision:{source}:{revision_id}` | Hash | Revision metadata (revision_id, source, version_label, effective_from, effective_to, status, file_path, file_size_bytes, page_count, chunk_count, notes, created_at, ingested_at, error) |
| `policy_revisions:{source}` | Sorted Set | Revision IDs scored by effective_from (days since epoch) |
| `policies_all` | Set | All registered source slugs |

**Revision Status Values:** `processing`, `active`, `superseded`, `failed`.

**Policy Categories:** `national_policy`, `national_guidance`, `local_plan`, `local_guidance`, `county_strategy`.

**Operations:** Create policy (with duplicate prevention), create revision (with overlap detection and auto-supersession), update revision metadata, delete revision (with sole-active-revision guard), list policies, list revisions, get effective revision for date.

**Overlap Detection:**
- When creating a revision, existing revisions are checked for date overlap.
- If an existing open-ended revision starts before the new revision, auto-supersession closes it with `effective_to = new_effective_from - 1 day`.
- If an existing open-ended revision starts after the new revision and the new revision has a defined `effective_to` before the existing revision's start, no overlap (historical insertion).
- True overlaps with bounded revisions raise `RevisionOverlapError`.

### [policy-kb-api:FR-009] Transport Protocol

**Description:** The server exposes dual MCP transport endpoints via a Starlette application.

**Endpoints:**
| Path | Method | Description |
|------|--------|-------------|
| `/health` | GET | Health check; returns `{"status": "ok"}`; exempt from authentication |
| `/sse` | GET | SSE transport (legacy, for internal worker connections) |
| `/messages/` | POST | SSE message posting endpoint |
| `/mcp` | GET, POST, DELETE | Streamable HTTP transport (current MCP standard) |

**Implementation:**
- SSE transport uses `mcp.server.sse.SseServerTransport`.
- Streamable HTTP uses `mcp.server.streamable_http_manager.StreamableHTTPSessionManager`.
- Both transports share the same underlying `mcp.server.Server` instance named `policy-kb-mcp`.
- Server runs on port 3003 via uvicorn, binding `0.0.0.0`.

### [policy-kb-api:FR-010] Authentication

**Description:** Bearer token authentication on all MCP endpoints, configured via the `MCP_API_KEY` environment variable.

**Behaviour:**
- When `MCP_API_KEY` is set and non-empty, all requests (except `/health`) must include `Authorization: Bearer <token>`.
- Token comparison uses `hmac.compare_digest` for constant-time comparison.
- Missing header returns 401 with `{"error": {"code": "unauthorized", "message": "Missing Authorization header"}}`.
- Invalid token returns 401 with `{"error": {"code": "unauthorized", "message": "Invalid bearer token"}}`.
- Non-Bearer scheme (e.g. Basic) returns 401 with format error message.
- When `MCP_API_KEY` is not set or empty, authentication is disabled (no-op middleware for backward compatibility).
- Failed authentication attempts are logged at WARNING level with client IP, endpoint, and method.

---

## Non-Functional Requirements

### NFR-001: Temporal Accuracy
**Category:** Reliability
**Description:** Effective date filtering must select the correct revision for any valid date query. The `EffectiveDateResolver` and ChromaDB temporal where-filter must agree on which revision is in force.
**Acceptance Threshold:** 100% correct revision selection for any valid date, including boundary dates (`effective_from` and `effective_to` are both inclusive).
**Verification:** Unit tests covering single revision in range, multiple revisions with middle date, date before first revision, date on exact boundaries, date in gap between revisions, and snapshot resolution across all policies.

### NFR-002: Memory
**Category:** Resource Limits
**Description:** The `policy-kb-mcp` container must operate within its allocated memory limit. ChromaDB persistent storage is on an external volume; the embedding model and runtime state must fit in container memory.
**Acceptance Threshold:** Container memory limited to 2GB (as configured in `deploy/docker-compose.yml`).
**Verification:** Docker resource monitoring under load with all seed policies ingested.

### NFR-003: Consistency
**Category:** Reliability
**Description:** The policy registry in Redis and the vector store in ChromaDB must remain synchronized. No orphan chunks in ChromaDB without a corresponding registry entry; no registry entries claiming chunks that do not exist in ChromaDB.
**Acceptance Threshold:** Zero orphan chunks after any ingestion, reindex, or deletion operation. Revision `chunk_count` in registry matches actual ChromaDB chunk count.
**Verification:** Integration tests and consistency check operations.

---

## Open Questions

None at this time.

---

## Appendix

### Glossary

- **Effective Date** -- The date from which a policy revision comes into force. Used to filter search results to the revision that was in force on a planning application's validation date.
- **Revision** -- A specific version of a policy document with its own effective date range and associated chunks in ChromaDB.
- **Source Slug** -- Unique UPPER_SNAKE_CASE identifier for a policy document (e.g. `LTN_1_20`, `NPPF`, `CHERWELL_LOCAL_PLAN`). Pattern: `^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$`.
- **Temporal Query** -- A search filtered by a point-in-time effective date to retrieve only chunks from revisions in force on that date.
- **Auto-supersession** -- When a new revision is created with `effective_from` after an existing open-ended revision, the system automatically sets the existing revision's `effective_to` to `new_effective_from - 1 day`.
- **Chunk ID** -- Deterministic identifier in the format `{source}__{revision_id}__{section_ref}__{chunk_index:03d}`.
- **Date Integer** -- ChromaDB metadata stores dates as integer YYYYMMDD (e.g. `20241212`). A null `effective_to` is stored as `99991231`.

### Seed Policies

| Source Slug | Title | Category | Revisions |
|-------------|-------|----------|-----------|
| `LTN_1_20` | Cycle Infrastructure Design (LTN 1/20) | national_guidance | 1 (July 2020, ~160 chunks) |
| `NPPF` | National Planning Policy Framework | national_policy | 2 (Dec 2024, ~73 chunks; Sep 2023, ~73 chunks) |
| `MANUAL_FOR_STREETS` | Manual for Streets | national_guidance | 1 (~135 chunks) |
| `CHERWELL_LOCAL_PLAN` | Cherwell Local Plan 2011-2031 | local_plan | 1 (~274 chunks) |
| `OCC_LTCP` | Oxfordshire Local Transport and Connectivity Plan | county_strategy | 1 (~159 chunks) |
| `BICESTER_LCWIP` | Bicester Local Cycling and Walking Infrastructure Plan | local_guidance | 1 (~53 chunks) |

### ChromaDB Collection Schema

**Collection name:** `policy_docs`

**Chunk metadata fields:**
| Field | Type | Description |
|-------|------|-------------|
| `source` | string | Policy source slug |
| `source_title` | string | Policy title |
| `revision_id` | string | Revision identifier |
| `version_label` | string | Human-readable version label |
| `effective_from` | int | Start date as YYYYMMDD integer |
| `effective_to` | int | End date as YYYYMMDD integer; `99991231` = currently in force |
| `section_ref` | string | Extracted section reference (e.g. `Chapter 5`) |
| `page_number` | int | Source PDF page number |
| `chunk_index` | int | Sequential index within the revision |

### Key Source Files

| File | Responsibility |
|------|---------------|
| `src/mcp_servers/policy_kb/server.py` | MCP server, tool registration, tool handlers |
| `src/shared/policy_registry.py` | Redis-backed policy and revision CRUD, overlap detection, auto-supersession |
| `src/shared/effective_date_resolver.py` | Temporal resolution logic for single-policy and snapshot queries |
| `src/shared/policy_chroma_client.py` | ChromaDB client for the `policy_docs` collection: search, upsert, delete |
| `src/mcp_servers/shared/transport.py` | Dual SSE + Streamable HTTP Starlette app factory |
| `src/mcp_servers/shared/auth.py` | Bearer token authentication middleware |

### References

- [Policy Knowledge Base specification](../policy-knowledge-base/specification.md) -- The broader system specification covering REST API, seeding, and full lifecycle
- [Cycle Route Assessment specification](../cycle-route-assessment/specification.md) -- Shared transport and auth middleware origin

### Change History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-14 | SDD Agent | Initial as-built specification documenting MCP tool contracts |
