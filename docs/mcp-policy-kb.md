# Policy KB MCP Server -- API Reference

## 1. Overview

The Policy KB MCP server (`policy-kb-mcp`) exposes six tools for searching, retrieving, and managing planning policy documents stored in a ChromaDB vector store with metadata tracked in a Redis registry. It runs on **port 3003** and serves both the AI review agent (which calls tools via MCP during planning application assessment) and external MCP clients such as Claude Desktop and n8n. The server supports temporal filtering so that callers can query the exact policy revision that was in force on a given date -- typically a planning application's validation date.

---

## 2. Transport & Authentication

### Endpoints

| Path | Method(s) | Auth Required | Description |
|------|-----------|---------------|-------------|
| `/health` | GET | No | Health check. Returns `{"status": "ok"}`. |
| `/sse` | GET | Yes | SSE transport (legacy, for internal worker connections). |
| `/messages/` | POST | Yes | SSE message posting endpoint. |
| `/mcp` | GET, POST, DELETE | Streamable HTTP transport (current MCP standard). |

Both transports share a single `mcp.server.Server` instance named `policy-kb-mcp`. The SSE transport uses `mcp.server.sse.SseServerTransport` and the Streamable HTTP transport uses `mcp.server.streamable_http_manager.StreamableHTTPSessionManager`. The server binds `0.0.0.0:3003` via uvicorn.

### Authentication

Authentication is controlled by the `MCP_API_KEY` environment variable and enforced by the `MCPAuthMiddleware` Starlette middleware.

| Behaviour | Detail |
|-----------|--------|
| **When `MCP_API_KEY` is set** | All requests except `/health` must include `Authorization: Bearer <token>`. |
| **When `MCP_API_KEY` is unset or empty** | Authentication is disabled (no-op middleware for backward compatibility). |
| **Token comparison** | Uses `hmac.compare_digest` for constant-time comparison. |
| **Missing header** | 401 `{"error": {"code": "unauthorized", "message": "Missing Authorization header"}}` |
| **Invalid token** | 401 `{"error": {"code": "unauthorized", "message": "Invalid bearer token"}}` |
| **Wrong scheme (e.g. Basic)** | 401 `{"error": {"code": "unauthorized", "message": "Invalid Authorization header format. Expected: Bearer <token>"}}` |
| **Logging** | Failed authentication attempts are logged at WARNING level with client IP, endpoint, and method. |

---

## 3. Tools

### 3.1 `search_policy`

Semantic search across policy document chunks with optional temporal and source filtering. Returns ranked results ordered by relevance score.

#### Input

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | Yes | -- | Natural language search query. |
| `sources` | string[] | No | null | Filter results to specific policy source slugs (e.g. `["LTN_1_20", "NPPF"]`). |
| `effective_date` | string | No | null | ISO date (`YYYY-MM-DD`) for temporal filtering. Only returns chunks from revisions in force on this date. |
| `n_results` | int | No | 10 | Maximum number of results to return. |

#### Output

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

#### Behaviour

- The query text is embedded using the configured embedding model (`all-MiniLM-L6-v2`) and searched against the `policy_docs` ChromaDB collection.
- When `effective_date` is provided, a ChromaDB `$and` where filter is applied: `effective_from <= date_int AND effective_to >= date_int`. See [Temporal Query Resolution](#4-temporal-query-resolution) for details.
- When `sources` contains a single value, an equality filter is used. When it contains multiple values, a `$in` filter is applied.
- Relevance score is computed as `max(0, 1 - (distance / 2))` from ChromaDB's L2 distance.
- When no `effective_date` is provided, chunks from all revisions (including superseded) are returned.
- An invalid `effective_date` format returns `{"status": "error", "error_type": "invalid_date", "message": "..."}`.
- A date before any revision's `effective_from` returns `results_count: 0` with an empty results array.

---

### 3.2 `get_policy_section`

Retrieve a specific policy section by source slug and section reference. Combines text from all matching chunks for the section.

#### Input

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source` | string | Yes | -- | Policy source slug (e.g. `LTN_1_20`). |
| `section_ref` | string | Yes | -- | Section reference (e.g. `Chapter 5`, `Table 5-2`, `Para 116`). |
| `revision_id` | string | No | null | Specific revision ID. Defaults to the latest effective revision. |

#### Output

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

#### Behaviour

- The `section_ref` string is embedded and searched against the `policy_docs` collection filtered by `source` (and optionally `revision_id`), retrieving up to 20 results.
- Results are post-filtered for an exact `section_ref` match on chunk metadata.
- Matching chunks are concatenated (joined by double newline) and deduplicated page numbers are returned sorted.
- If no chunks match, returns `{"status": "error", "error_type": "section_not_found", "message": "Section '<section_ref>' not found in policy '<source>'"}`.

---

### 3.3 `list_policy_documents`

List all registered policy documents with source slug, title, and category. Takes no input parameters.

#### Input

Empty object `{}`.

#### Output

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

#### Behaviour

- Reads from the Redis policy registry via `PolicyRegistry.list_policies()`.
- The `policies_all` Redis set provides the list of all source slugs; each policy's metadata is read from the `policy:{source}` hash.
- Category is serialized from the `PolicyCategory` enum. Valid values: `national_policy`, `national_guidance`, `local_plan`, `local_guidance`, `county_strategy`.
- If the registry is not configured, returns `{"status": "error", "error_type": "registry_unavailable", "message": "PolicyRegistry not configured"}`.

---

### 3.4 `list_policy_revisions`

List all revisions for a specific policy document, ordered by `effective_from` descending (newest first).

#### Input

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source` | string | Yes | -- | Policy source slug. |

#### Output

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

#### Behaviour

- Reads from the Redis sorted set `policy_revisions:{source}` (sorted by `effective_from` as days-since-epoch score) in reverse order.
- Each revision's metadata is read from the `policy_revision:{source}:{revision_id}` hash.
- `effective_to` is `null` in the JSON output for the currently-in-force revision.
- Status values are from the `RevisionStatus` enum: `processing`, `active`, `superseded`, `failed`.
- If the registry is not configured, returns `registry_unavailable` error.

---

### 3.5 `ingest_policy_revision`

Ingest a policy revision PDF into the ChromaDB vector store. Extracts text, chunks, embeds, and stores with temporal metadata. Called by the API worker during revision upload or reindex.

#### Input

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source` | string | Yes | -- | Policy source slug. |
| `revision_id` | string | Yes | -- | Revision ID. |
| `file_path` | string | Yes | -- | Absolute path to PDF file on disk. |
| `reindex` | bool | No | false | If true, delete existing chunks before re-ingesting. |

#### Output

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

#### Behaviour

- Validates that the file exists and the revision is registered in the policy registry.
- If `reindex` is true, calls `PolicyChromaClient.delete_revision_chunks()` to clear existing chunks first.
- Text extraction uses `DocumentProcessor` with OCR enabled (`enable_ocr=True`).
- Text is chunked via `TextChunker` operating on page-level text.
- Embeddings are generated in batch via `EmbeddingService.embed_batch()`.
- Each chunk's metadata includes: `source`, `source_title`, `revision_id`, `version_label`, `effective_from` (int YYYYMMDD), `effective_to` (int YYYYMMDD, `99991231` for current), `section_ref`, `page_number`, `chunk_index`.
- Chunk IDs are deterministic: `{source}__{revision_id}__{section_ref}__{chunk_index:03d}` (spaces in section_ref replaced with underscores).
- Chunks are upserted to ChromaDB in a single batch call.
- Returns `file_not_found` error if the file does not exist, `revision_not_found` if the revision is not registered, `registry_unavailable` if the registry is not configured, or `no_content` if no text could be extracted from the PDF.

---

### 3.6 `remove_policy_revision`

Remove all chunks for a policy revision from the ChromaDB vector store.

#### Input

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source` | string | Yes | -- | Policy source slug. |
| `revision_id` | string | Yes | -- | Revision ID. |

#### Output

```json
{
  "status": "success",
  "source": "NPPF",
  "revision_id": "rev_NPPF_2023_09",
  "chunks_removed": 73
}
```

#### Behaviour

- Queries ChromaDB for all chunks matching `source` AND `revision_id` metadata, then deletes them by ID.
- Returns `chunks_removed: 0` if no chunks are found (this is not treated as an error).
- Does not modify the policy registry; registry deletion is handled separately by the REST API.

---

## 4. Temporal Query Resolution

Policy revisions have effective date ranges that determine when they were in force. The system resolves which revision applies for a given date using integer date encoding in ChromaDB metadata and the `EffectiveDateResolver` class.

### Date Storage Format

ChromaDB requires numeric metadata values for comparison operators. Dates are stored as **integer YYYYMMDD** format:

| Date | Integer |
|------|---------|
| 2024-12-12 | `20241212` |
| 2023-09-05 | `20230905` |
| Currently in force (null `effective_to`) | `99991231` |

The sentinel value `99991231` represents a revision that has no end date and is currently in force.

### ChromaDB Where Filter

When `effective_date` is provided to `search_policy`, the following `$and` where clause is applied:

```json
{
  "$and": [
    {"effective_from": {"$lte": 20240315}},
    {"effective_to":   {"$gte": 20240315}}
  ]
}
```

This selects only chunks from revisions where `effective_from <= date_int AND effective_to >= date_int`. Because currently-in-force revisions have `effective_to` of `99991231`, they satisfy the `$gte` condition for any reasonable query date.

### EffectiveDateResolver Class

The `EffectiveDateResolver` class (in `src/shared/effective_date_resolver.py`) provides higher-level resolution on top of the registry:

| Method | Description |
|--------|-------------|
| `resolve_for_policy(source, date)` | Returns an `EffectivePolicyResult` with the revision in force for a single policy, or a reason string if none found. |
| `resolve_snapshot(date)` | Resolves all registered policies for a date. Returns an `EffectiveSnapshotResult` with categorised lists: policies with a revision, policies not yet effective, and policies in a gap. |
| `get_revision_ids_for_date(date, sources?)` | Returns a `dict[str, str | None]` mapping source slug to revision ID for ChromaDB filtering. |
| `validate_revision_for_date(source, revision_id, date)` | Returns `True` if the specified revision was in force on the given date. Useful for verifying review citations. |

Resolution considers only `active` and `superseded` status revisions; `processing` and `failed` revisions are skipped.

### Auto-Supersession

When a new revision is created with `effective_from` after an existing open-ended revision, auto-supersession automatically sets the existing revision's `effective_to` to `new_effective_from - 1 day`. For example, creating an NPPF revision effective 2024-12-12 auto-supersedes the September 2023 revision by setting its `effective_to` to 2024-12-11.

### Edge Cases

| Scenario | Behaviour |
|----------|-----------|
| Date before first revision | Returns no results. `EffectiveDateResolver` reports `reason: "date_before_first_revision"`. |
| Date on exact `effective_from` boundary | Revision **is** included (boundary is inclusive). |
| Date on exact `effective_to` boundary | Revision **is** included (boundary is inclusive). |
| Date in gap between revisions | Returns no results. `EffectiveDateResolver` reports `reason: "date_in_gap"`. |
| No `effective_date` supplied | Chunks from all revisions (including superseded) are returned. |

---

## 5. Policy Registry

The Redis-backed policy registry is the source of truth for policy and revision metadata. ChromaDB stores only embeddings and chunk text; all lifecycle state lives in Redis.

### Redis Key Schema

| Key Pattern | Type | Description |
|-------------|------|-------------|
| `policy:{source}` | Hash | Policy document metadata: `source`, `title`, `description`, `category`, `created_at`, `updated_at`. |
| `policy_revision:{source}:{revision_id}` | Hash | Revision metadata: `revision_id`, `source`, `version_label`, `effective_from`, `effective_to`, `status`, `file_path`, `file_size_bytes`, `page_count`, `chunk_count`, `notes`, `created_at`, `ingested_at`, `error`. |
| `policy_revisions:{source}` | Sorted Set | Revision IDs scored by `effective_from` as days since Unix epoch. Enables efficient temporal lookup via `ZREVRANGEBYSCORE`. |
| `policies_all` | Set | All registered policy source slugs. |

### Revision Statuses

| Status | Description |
|--------|-------------|
| `processing` | Revision created, ingestion in progress. |
| `active` | Ingestion complete, revision is queryable. |
| `superseded` | A newer revision has taken over; still queryable for historical dates. |
| `failed` | Ingestion failed. Excluded from temporal resolution. |

### Overlap Detection and Auto-Supersession

When creating a revision, the registry checks all existing revisions (excluding `failed`) for date overlap:

1. **Existing open-ended revision starts before the new revision** -- Auto-supersession is applied: the existing revision's `effective_to` is set to `new_effective_from - 1 day`, and its status changes to `superseded`.
2. **New revision has a defined `effective_to` that ends before an existing open-ended revision starts** -- No overlap (this is a valid historical insertion).
3. **New revision starts on or before an existing open-ended revision's `effective_from`** -- Raises `RevisionOverlapError`.
4. **Both revisions have bounded date ranges that overlap** -- Raises `RevisionOverlapError`.

The sole-active-revision guard prevents deleting the only `active` revision for a policy (raises `CannotDeleteSoleRevisionError`).

---

## 6. Seed Policies

The knowledge base ships with 6 policies and 7 revisions, loaded via `scripts/fetch_policies.sh` and `scripts/seed_policies.py`.

| Source Slug | Title | Category | Revisions | Approx. Chunks |
|-------------|-------|----------|-----------|-----------------|
| `LTN_1_20` | Cycle Infrastructure Design (LTN 1/20) | national_guidance | 1 (July 2020) | 160 |
| `NPPF` | National Planning Policy Framework | national_policy | 2 (Dec 2024, Sep 2023) | 73 each |
| `MANUAL_FOR_STREETS` | Manual for Streets | national_guidance | 1 | 135 |
| `CHERWELL_LOCAL_PLAN` | Cherwell Local Plan 2011-2031 | local_plan | 1 | 274 |
| `OCC_LTCP` | Oxfordshire Local Transport and Connectivity Plan | county_strategy | 1 | 159 |
| `BICESTER_LCWIP` | Bicester Local Cycling and Walking Infrastructure Plan | local_guidance | 1 | 53 |

The NPPF is the only policy with multiple revisions. The December 2024 revision is `active` (open-ended); the September 2023 revision is `superseded` with `effective_to` of 2024-12-11.

### ChromaDB Collection

**Collection name:** `policy_docs`

Each chunk carries the following metadata fields:

| Field | Type | Description |
|-------|------|-------------|
| `source` | string | Policy source slug (e.g. `LTN_1_20`). |
| `source_title` | string | Policy title. |
| `revision_id` | string | Revision identifier (e.g. `rev_LTN_1_20_2020_07`). |
| `version_label` | string | Human-readable version label (e.g. `July 2020`). |
| `effective_from` | int | Start date as YYYYMMDD integer. |
| `effective_to` | int | End date as YYYYMMDD integer. `99991231` = currently in force. |
| `section_ref` | string | Extracted section reference (e.g. `Chapter 5`). |
| `page_number` | int | Source PDF page number. |
| `chunk_index` | int | Sequential index within the revision. |

---

## 7. Configuration

All configuration is via environment variables. No configuration files are required.

| Variable | Default | Description |
|----------|---------|-------------|
| `POLICY_KB_PORT` | `3003` | Port the MCP server listens on. |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL for the policy registry. |
| `CHROMA_PERSIST_DIR` | `/data/chroma` | Directory for ChromaDB persistent storage. |
| `MCP_API_KEY` | *(unset)* | Bearer token for authentication. When unset or empty, authentication is disabled. |

### Key Source Files

| File | Responsibility |
|------|---------------|
| `src/mcp_servers/policy_kb/server.py` | MCP server, tool registration, tool handlers. |
| `src/shared/policy_registry.py` | Redis-backed policy and revision CRUD, overlap detection, auto-supersession. |
| `src/shared/effective_date_resolver.py` | Temporal resolution logic for single-policy and snapshot queries. |
| `src/shared/policy_chroma_client.py` | ChromaDB client for the `policy_docs` collection: search, upsert, delete. |
| `src/mcp_servers/shared/transport.py` | Dual SSE + Streamable HTTP Starlette app factory. |
| `src/mcp_servers/shared/auth.py` | Bearer token authentication middleware. |
