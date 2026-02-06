# Design: Policy Knowledge Base

**Version:** 1.0
**Date:** 2026-02-06
**Status:** Draft
**Linked Specification:** `.sdd/policy-knowledge-base/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

This feature builds upon the foundation-api and document-processing features. The existing system has:
- API Gateway (FastAPI) with review endpoints and webhook dispatch
- Redis for job queue and state store
- Worker with arq for async job processing
- ChromaDB with `application_docs` collection
- MCP server infrastructure (Cherwell Scraper, Document Store)

### Proposed Architecture

The Policy Knowledge Base adds a versioned policy document registry with temporal query support:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Docker Compose Stack                                   │
│                                                                               │
│  ┌─────────────────────┐      ┌───────────────────────────────────┐         │
│  │   API Gateway       │      │      Redis                        │         │
│  │   (FastAPI)         │◄────►│   - Job Queue (arq)               │         │
│  │   :8080             │      │   - Review State                  │         │
│  │                     │      │   - Policy Registry ◄─────────┐   │         │
│  │   /api/v1/reviews   │      │     - policy:{source}         │   │         │
│  │   /api/v1/policies  │      │     - policy_revision:{s}:{r} │   │         │
│  │   /api/v1/health    │      │     - policy_revisions:{s}    │   │         │
│  └─────────┬───────────┘      │     - policies_all            │   │         │
│            │                  └───────────────┬───────────────┘   │         │
│            │ enqueue                          │                     │         │
│            │                                  │ dequeue             │         │
│            ▼                                  ▼                     │         │
│  ┌──────────────────────────────────────────────────────────┐     │         │
│  │    Worker (arq)                                           │     │         │
│  │    - Review job processing                                │     │         │
│  │    - Policy ingestion jobs (PolicyIngestionJob)          │     │         │
│  │    - Webhook dispatch                                     │     │         │
│  └──────────────────────────────────────────────────────────┘     │         │
│            │                                                        │         │
│            │ MCP calls (SSE :3003)                                 │         │
│            ▼                                                        │         │
│  ┌──────────────────────────────────────────────────────────┐     │         │
│  │  Policy KB MCP (PolicyKBMCP)                              │     │         │
│  │  - search_policy (temporal filtering)                    │     │         │
│  │  - get_policy_section                                    │     │         │
│  │  - list_policy_documents                                  │     │         │
│  │  - list_policy_revisions                                  │     │         │
│  │  - ingest_policy_revision                                 │     │         │
│  │  - remove_policy_revision                                 │     │         │
│  └──────────────────────────────────────────────────────────┘     │         │
│            │                                                        │         │
│            │                                                        │         │
│            ▼                                                        │         │
│  ┌──────────────────────────────────────────────────────────┐     │         │
│  │  ChromaDB (Persistent)                                    │     │         │
│  │  ├── application_docs (existing)                         │     │         │
│  │  └── policy_docs (NEW)                                    │◄────┘         │
│  │      - Chunks with revision metadata                      │               │
│  │      - effective_from / effective_to for temporal queries │               │
│  └──────────────────────────────────────────────────────────┘               │
│                                                                               │
│  ┌──────────────────────────────────────────────────────────┐               │
│  │  Policy Init Container (PolicySeeder)                     │               │
│  │  - Runs once at first deployment                          │               │
│  │  - Seeds LTN 1/20, NPPF, Local Plan, etc.                │               │
│  └──────────────────────────────────────────────────────────┘               │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Data Flow for Policy Management:**
1. Admin POSTs new policy document to API Gateway
2. API creates registry entry in Redis, returns 201
3. Admin uploads revision with PDF via multipart POST
4. API saves file, creates revision record in Redis (status: processing), enqueues ingestion job
5. Worker picks up job, calls PolicyKBMCP.ingest_policy_revision
6. MCP extracts text, chunks, embeds, stores in ChromaDB with revision metadata
7. Worker updates revision status to "active" in Redis

**Data Flow for Temporal Policy Search:**
1. Agent calls PolicyKBMCP.search_policy with effective_date
2. MCP consults PolicyRegistry (Redis) to identify valid revision IDs
3. MCP queries ChromaDB with WHERE filter on effective_from/effective_to
4. Results returned from correct revision only

### Technology Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Registry Store | Redis | Already used for job state; fast lookups; sorted sets for date-based queries |
| Vector Store | ChromaDB | Already used for application_docs; supports metadata filtering |
| Temporal Filtering | ChromaDB WHERE clause | Avoids loading all results; efficient at query time |
| Revision ID Format | `rev_{SOURCE}_{YYYY}_{MM}` | Human-readable, sortable, unique per revision |
| File Storage | Docker volume `/data/policy` | Persistent storage for source PDFs |

### Quality Attributes

**Data Consistency:**
- Redis is source of truth for registry metadata
- ChromaDB stores embeddings tagged with revision metadata
- Deletion removes both Redis records and ChromaDB chunks atomically
- Consistency check mechanism available via health endpoint

**Temporal Accuracy:**
- EffectiveDateResolver guarantees correct revision selection
- Overlapping effective date ranges rejected at upload time
- Audit trail via review metadata shows which revision was used

**Scalability:**
- Policy documents are relatively static (tens of documents, tens of revisions)
- ChromaDB handles thousands of chunks efficiently
- Redis sorted sets enable O(log N) date lookups

---

## API Design

### Resource Model

The Policy API manages two related resources: **Policy Documents** (stable identities) and **Policy Revisions** (versioned content).

```
/api/v1/
├── policies                              # Collection of policy documents
│   ├── POST                              # Register new policy document (201)
│   ├── GET                               # List all policies with current revision
│   └── effective                         # Snapshot endpoint
│       └── GET ?date=YYYY-MM-DD          # Get revisions in force on date
├── policies/{source}                     # Individual policy document
│   ├── GET                               # Get policy with all revisions
│   ├── PATCH                             # Update metadata (title, description)
│   └── revisions                         # Collection of revisions
│       ├── POST (multipart)              # Upload new revision (202)
│       └── {revision_id}                 # Individual revision
│           ├── GET                       # Get revision details
│           ├── PATCH                     # Update revision metadata
│           ├── DELETE                    # Remove revision (200)
│           ├── status                    # Ingestion status
│           │   └── GET
│           └── reindex                   # Re-run ingestion
│               └── POST (202)
```

### Request/Response Contracts

**POST /api/v1/policies**

Registers a new policy document entry (no revisions yet).

Input:
- `source` (required): Unique slug in UPPER_SNAKE_CASE (e.g., `LTN_1_20`)
- `title` (required): Human-readable title
- `description` (optional): Description of the policy
- `category` (required): One of `national_policy`, `national_guidance`, `local_plan`, `local_guidance`, `county_strategy`, `supplementary`

Output (201 Created):
- `source`, `title`, `description`, `category`
- `current_revision`: null (no revisions yet)
- `revision_count`: 0
- `created_at`: ISO 8601 timestamp

Errors:
- 400 `invalid_source`: Source slug malformed
- 409 `policy_already_exists`: Duplicate source slug

**GET /api/v1/policies**

Lists all registered policy documents with current revision.

Query Parameters:
- `status`: Filter by revision status (active, all)
- `source`: Filter by source slug
- `category`: Filter by category

Output (200 OK):
- `policies`: Array of policy summaries with current_revision
- `total`: Total count

**GET /api/v1/policies/{source}**

Returns full detail for a policy document including all revisions.

Output (200 OK):
- `source`, `title`, `description`, `category`
- `revisions`: Array of all revisions ordered by effective_from DESC
- `created_at`, `updated_at`

Errors:
- 404 `policy_not_found`: Source slug does not exist

**PATCH /api/v1/policies/{source}**

Updates policy document metadata (not revisions).

Input:
- `title` (optional): New title
- `description` (optional): New description
- `category` (optional): New category

Output (200 OK):
- Updated policy document

Errors:
- 404 `policy_not_found`

**POST /api/v1/policies/{source}/revisions**

Uploads a new revision. Processed asynchronously.

Input (multipart/form-data):
- `file` (required): PDF file
- `version_label` (required): Human-readable version (e.g., "December 2024")
- `effective_from` (required): ISO date string
- `effective_to` (optional): ISO date string (null = currently in force)
- `notes` (optional): Notes about this revision

Output (202 Accepted):
- `source`, `revision_id`, `version_label`
- `effective_from`, `effective_to`
- `status`: "processing"
- `ingestion_job_id`: Job ID for status tracking
- `links`: Self and status URLs
- `side_effects`: Info about superseded revision if applicable

Errors:
- 404 `policy_not_found`: Source slug does not exist
- 409 `revision_overlap`: Effective dates overlap with existing revision
- 422 `unsupported_file_type`: Not a PDF

**GET /api/v1/policies/{source}/revisions/{revision_id}**

Returns revision details.

Output (200 OK):
- Full revision record including ingestion metadata

Errors:
- 404 `policy_not_found`, `revision_not_found`

**GET /api/v1/policies/{source}/revisions/{revision_id}/status**

Returns ingestion status for a processing revision.

Output (200 OK):
- `revision_id`, `status`
- `progress`: Phase, percent_complete, chunks_processed/total

**PATCH /api/v1/policies/{source}/revisions/{revision_id}**

Updates revision metadata.

Input:
- `version_label`, `effective_from`, `effective_to`, `notes` (all optional)

Output (200 OK):
- Updated revision record

Errors:
- 404 `policy_not_found`, `revision_not_found`
- 409 `revision_overlap`: New dates would overlap

**DELETE /api/v1/policies/{source}/revisions/{revision_id}**

Removes a revision and its chunks from ChromaDB.

Output (200 OK):
- `source`, `revision_id`, `status`: "deleted", `chunks_removed`

Errors:
- 404 `policy_not_found`, `revision_not_found`
- 409 `cannot_delete_sole_revision`: At least one active revision must remain

**POST /api/v1/policies/{source}/revisions/{revision_id}/reindex**

Re-runs ingestion pipeline for existing revision.

Output (202 Accepted):
- `revision_id`, `status`: "reindexing", `ingestion_job_id`

Errors:
- 404 `policy_not_found`, `revision_not_found`
- 409 `cannot_reindex`: Revision already processing

**GET /api/v1/policies/effective**

Returns policy snapshot for a given date.

Query Parameters:
- `date` (required): ISO date string

Output (200 OK):
- `effective_date`: Echo of input
- `policies`: Array with effective revision for each policy
- `policies_not_yet_effective`: Policies with no revision for that date
- `policies_with_no_revision_for_date`: Policies where date is before first revision

Errors:
- 400 `invalid_effective_date`: Malformed date

### MCP Tool Contracts

**search_policy**

Search policy documents with optional temporal filtering.

Input:
- `query` (required): Search query string
- `sources` (optional): Array of source slugs to filter
- `effective_date` (optional): ISO date for temporal filtering
- `n_results` (optional): Number of results (default 10)

Output:
- Array of PolicySearchResult with chunk content, metadata, relevance score

**get_policy_section**

Retrieve specific policy section by reference.

Input:
- `source` (required): Policy source slug
- `section_ref` (required): Section reference (e.g., "Chapter 5", "Table 5-2")
- `revision_id` (optional): Specific revision (default: latest effective)

Output:
- PolicySection with content and metadata

Errors:
- `section_not_found`: Section reference not found

**list_policy_documents**

List all registered policies with current revision info.

Output:
- Array of PolicyDocumentInfo

**list_policy_revisions**

List all revisions for a specific policy.

Input:
- `source` (required): Policy source slug

Output:
- Array of PolicyRevisionInfo ordered by effective_from DESC

**ingest_policy_revision**

Ingest a new revision (called by worker).

Input:
- `source`, `revision_id`, `file_path`, `effective_from`, `effective_to`, `metadata`

Output:
- IngestResult with chunk count and processing stats

**remove_policy_revision**

Remove revision chunks from ChromaDB (called by worker).

Input:
- `source`, `revision_id`

Output:
- RemoveResult with chunks_removed count

### Error Response Format

All errors follow project guidelines:

```json
{
    "error": {
        "code": "policy_not_found",
        "message": "No policy document found with source 'INVALID'",
        "details": {
            "source": "INVALID"
        }
    }
}
```

---

## Added Components

### PolicyRegistry

**Description:** Redis-backed registry for policy documents and revisions. Provides CRUD operations, effective date resolution, and consistency management. Acts as the source of truth for policy metadata.

**Users:** PolicyRouter, PolicyKBMCP, PolicyIngestionJob, PolicySeeder, EffectiveDateResolver

**Kind:** Class

**Location:** `src/shared/policy_registry.py`

**Requirements References:**
- [policy-knowledge-base:FR-001]: Create policy document entry
- [policy-knowledge-base:FR-002]: Store revision metadata
- [policy-knowledge-base:FR-007]: List all policies with current revision
- [policy-knowledge-base:FR-008]: List revisions for a policy
- [policy-knowledge-base:FR-010]: Update revision metadata
- [policy-knowledge-base:FR-014]: Redis as source of truth

**Redis Key Schema:**

| Key Pattern | Type | Description |
|-------------|------|-------------|
| `policy:{source}` | Hash | Policy document metadata |
| `policy_revision:{source}:{revision_id}` | Hash | Revision metadata |
| `policy_revisions:{source}` | Sorted Set | Revision IDs sorted by effective_from |
| `policies_all` | Set | All source slugs |

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Create policy document | Valid source "LTN_1_20" | Call create_policy | Policy stored in Redis, added to policies_all set |
| TS-02 | Duplicate policy prevention | Policy "LTN_1_20" exists | Call create_policy with same source | Raises PolicyAlreadyExistsError |
| TS-03 | Create revision | Policy exists, valid dates | Call create_revision | Revision stored, added to sorted set |
| TS-04 | Overlapping dates rejected | Revision with effective_from 2024-01-01 exists | Create revision with overlapping dates | Raises RevisionOverlapError |
| TS-05 | Get policy with revisions | Policy with 3 revisions | Call get_policy | Returns policy with all revisions sorted by date |
| TS-06 | List all policies | 5 policies registered | Call list_policies | Returns all 5 with current revision info |
| TS-07 | Update revision metadata | Existing revision | Call update_revision with new effective_to | Metadata updated |
| TS-08 | Delete revision | Revision with chunks | Call delete_revision | Revision removed from Redis |
| TS-09 | Cannot delete sole active revision | Policy with one active revision | Call delete_revision | Raises CannotDeleteSoleRevisionError |
| TS-10 | Get effective revision for date | Revisions: 2021-07-20, 2023-09-05, 2024-12-12 | Call get_effective_revision("2024-03-15") | Returns revision from 2023-09-05 |
| TS-11 | No revision for early date | First revision effective 2020-07-27 | Call get_effective_revision("2019-01-01") | Returns None |
| TS-12 | Auto-set previous revision effective_to | Revision A active (no effective_to) | Create Revision B with effective_from 2025-01-01 | Revision A's effective_to set to 2024-12-31 |

### PolicyRouter

**Description:** FastAPI router handling all `/api/v1/policies` endpoints. Validates requests, delegates to PolicyRegistry for data operations, enqueues ingestion jobs.

**Users:** External API consumers, system administrators

**Kind:** Module (FastAPI Router)

**Location:** `src/api/routes/policies.py`

**Requirements References:**
- [policy-knowledge-base:FR-001]: POST /policies endpoint
- [policy-knowledge-base:FR-002]: POST /policies/{source}/revisions endpoint
- [policy-knowledge-base:FR-007]: GET /policies endpoint
- [policy-knowledge-base:FR-008]: GET /policies/{source} endpoint
- [policy-knowledge-base:FR-009]: GET /policies/effective endpoint
- [policy-knowledge-base:FR-010]: PATCH /policies/{source}/revisions/{revision_id} endpoint
- [policy-knowledge-base:FR-011]: DELETE /policies/{source}/revisions/{revision_id} endpoint
- [policy-knowledge-base:FR-012]: POST /policies/{source}/revisions/{revision_id}/reindex endpoint

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Register new policy | Valid payload | POST /policies | Returns 201 with policy, current_revision null |
| TS-02 | Duplicate source rejected | Policy "NPPF" exists | POST /policies with source "NPPF" | Returns 409 policy_already_exists |
| TS-03 | Invalid source format | Source "invalid-format" | POST /policies | Returns 400 invalid_source |
| TS-04 | Upload revision | PDF file, valid dates | POST /policies/LTN_1_20/revisions | Returns 202 with status "processing" |
| TS-05 | Upload non-PDF rejected | DOCX file | POST /policies/LTN_1_20/revisions | Returns 422 unsupported_file_type |
| TS-06 | Overlapping dates rejected | Revision 2024-01-01 to 2024-12-31 exists | Upload revision effective_from 2024-06-01 | Returns 409 revision_overlap |
| TS-07 | List policies | 5 policies exist | GET /policies | Returns 200 with 5 policies |
| TS-08 | Get policy detail | Policy "NPPF" with 3 revisions | GET /policies/NPPF | Returns 200 with all 3 revisions |
| TS-09 | Policy not found | No policy "INVALID" | GET /policies/INVALID | Returns 404 policy_not_found |
| TS-10 | Get effective snapshot | Date 2024-03-15 | GET /policies/effective?date=2024-03-15 | Returns 200 with snapshot |
| TS-11 | Invalid date format | Date "invalid" | GET /policies/effective?date=invalid | Returns 400 invalid_effective_date |
| TS-12 | Update revision metadata | Existing revision | PATCH /policies/NPPF/revisions/rev_NPPF_2024_12 | Returns 200 with updated revision |
| TS-13 | Delete revision | Revision with multiple active | DELETE /policies/NPPF/revisions/rev_NPPF_2021_07 | Returns 200 with chunks_removed count |
| TS-14 | Cannot delete sole revision | Only one active revision | DELETE sole revision | Returns 409 cannot_delete_sole_revision |
| TS-15 | Reindex revision | Existing active revision | POST /policies/LTN_1_20/revisions/rev_LTN120_2020_07/reindex | Returns 202 with job_id |
| TS-16 | Get revision status | Processing revision | GET /policies/NPPF/revisions/rev_NPPF_2026_02/status | Returns 200 with progress |

### PolicyIngestionJob

**Description:** Async worker job for processing uploaded policy PDFs. Extracts text, chunks content, generates embeddings, stores in ChromaDB with revision metadata.

**Users:** Worker (arq job handler), PolicyRouter (via queue)

**Kind:** Module (arq job)

**Location:** `src/worker/policy_jobs.py`

**Requirements References:**
- [policy-knowledge-base:FR-003]: Async processing through extraction, chunking, embedding
- [policy-knowledge-base:FR-004]: Chunks include effective_from/effective_to metadata
- [policy-knowledge-base:FR-012]: Re-run ingestion pipeline for reindex
- [policy-knowledge-base:NFR-001]: Complete 100-page PDF within 2 minutes

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Successful ingestion | Valid PDF file path | Job processes | Status transitions processing -> active, chunks created in ChromaDB |
| TS-02 | Ingestion failure | Corrupted PDF | Job processes | Status transitions to "failed" with error details |
| TS-03 | Chunks have temporal metadata | Valid PDF | Job completes | All chunks have effective_from, effective_to in metadata |
| TS-04 | Reindex clears old chunks | Existing chunks for revision | Reindex job runs | Old chunks deleted before new ones created |
| TS-05 | Progress updates | Multi-page PDF | Job processes | Progress events published (extracting, chunking, embedding phases) |
| TS-06 | Processing time acceptable | 100-page PDF | Job completes | Finishes within 2 minutes |

### PolicyKBMCP

**Description:** MCP server providing tools for policy search and retrieval. Implements temporal filtering, section lookup, and ingestion operations.

**Users:** Agent Worker (via MCP protocol), Review jobs

**Kind:** Module (MCP Server)

**Location:** `src/mcp_servers/policy_kb/server.py`

**Requirements References:**
- [policy-knowledge-base:FR-005]: search_policy with effective_date filtering
- [policy-knowledge-base:FR-006]: get_policy_section tool
- [policy-knowledge-base:FR-007]: list_policy_documents tool
- [policy-knowledge-base:FR-008]: list_policy_revisions tool
- [policy-knowledge-base:NFR-002]: 100% correct revision selection
- [policy-knowledge-base:NFR-003]: Top 5 results contain relevant content

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Search without date filter | Policy chunks exist | search_policy("cycle lane width") | Returns relevant chunks from all revisions |
| TS-02 | Search with effective date | Revisions: 2021, 2023, 2024 | search_policy("cycle lane", effective_date="2024-03-15") | Returns only chunks from 2023 revision |
| TS-03 | Search filtered by sources | Multiple policies | search_policy("transport", sources=["LTN_1_20"]) | Returns only LTN 1/20 chunks |
| TS-04 | Date before any revision | First revision 2020-07-27 | search_policy("test", effective_date="2019-01-01") | Returns empty results |
| TS-05 | Get policy section exists | Table 5-2 indexed | get_policy_section("LTN_1_20", "Table 5-2") | Returns table content |
| TS-06 | Get policy section not found | No such section | get_policy_section("LTN_1_20", "Table 99") | Returns error section_not_found |
| TS-07 | Get section with specific revision | Multiple revisions | get_policy_section("NPPF", "Para 116", revision_id="rev_NPPF_2023_09") | Returns from specified revision |
| TS-08 | List policy documents | 5 policies registered | list_policy_documents() | Returns all 5 with current revision |
| TS-09 | List policy revisions | NPPF with 3 revisions | list_policy_revisions("NPPF") | Returns all 3 ordered by date |
| TS-10 | Ingest policy revision | Valid file path | ingest_policy_revision(...) | Chunks created with correct metadata |
| TS-11 | Remove policy revision | Existing chunks | remove_policy_revision("NPPF", "rev_NPPF_2021_07") | All chunks for revision deleted |
| TS-12 | Search relevance | Standard cycling query | search_policy("cycle parking standards") | Top 5 results contain relevant content |

### EffectiveDateResolver

**Description:** Temporal query logic for determining which revision was in force on a given date. Used by both MCP search and REST API effective snapshot endpoint.

**Users:** PolicyKBMCP, PolicyRouter, PolicyRegistry

**Kind:** Class

**Location:** `src/shared/effective_date_resolver.py`

**Requirements References:**
- [policy-knowledge-base:FR-005]: Automatic selection based on date
- [policy-knowledge-base:FR-009]: Get effective snapshot for date
- [policy-knowledge-base:NFR-002]: 100% correct revision selection

**Algorithm:**
```python
def get_effective_revision(source: str, date: str) -> Optional[str]:
    """
    Find the revision in force on a given date.

    Uses Redis sorted set (policy_revisions:{source}) scored by effective_from.
    Returns the latest revision where:
    - effective_from <= date AND
    - (effective_to is None OR effective_to >= date)
    """
```

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Single revision, date in range | Rev A: 2020-07-27 to None | Resolve 2024-01-01 | Returns Rev A |
| TS-02 | Multiple revisions, middle date | Rev A: 2020-07-27 to 2023-09-04, Rev B: 2023-09-05 to 2024-12-11, Rev C: 2024-12-12 to None | Resolve 2024-03-15 | Returns Rev B |
| TS-03 | Date before first revision | Rev A: 2020-07-27 | Resolve 2019-01-01 | Returns None |
| TS-04 | Date on exact effective_from | Rev A: 2020-07-27 | Resolve 2020-07-27 | Returns Rev A |
| TS-05 | Date on effective_to | Rev A: 2020-07-27 to 2023-09-04 | Resolve 2023-09-04 | Returns Rev A |
| TS-06 | Date in gap between revisions | Rev A: 2020-01-01 to 2020-06-30, Rev B: 2020-08-01 to None | Resolve 2020-07-15 | Returns None |
| TS-07 | Resolve all policies for date | 5 policies with various revisions | Get snapshot for 2024-03-15 | Returns correct revision for each |
| TS-08 | Policy with no revision for date | Policy created but no revision effective yet | Resolve 2024-01-01 | Returns None for that policy |

### PolicySeeder

**Description:** Initial policy document ingestion at first deployment. Processes seed PDFs for LTN 1/20, NPPF, Manual for Streets, Cherwell Local Plan, LTCP, LCWIP, etc. with correct effective dates.

**Users:** policy-init container (runs once at startup)

**Kind:** Module (script)

**Location:** `src/scripts/seed_policies.py`

**Requirements References:**
- [policy-knowledge-base:FR-013]: Seed initial policies at first deployment

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | First run seeds all policies | Empty registry | Run seeder | All configured policies created with revisions |
| TS-02 | Idempotent re-run | Policies already seeded | Run seeder again | No duplicates, no errors |
| TS-03 | Seed files present | Seed PDF files in /data/policy/seed | Run seeder | All files processed |
| TS-04 | Correct effective dates | Seed config with dates | Run seeder | Revisions have correct effective_from dates |
| TS-05 | Missing seed file | One PDF missing | Run seeder | Logs warning, continues with others |

### PolicyDocument / PolicyRevision Models

**Description:** Pydantic models for policy document and revision data. Used for API request/response validation, Redis serialization, and MCP tool contracts.

**Users:** PolicyRouter, PolicyRegistry, PolicyKBMCP

**Kind:** Module (Pydantic Models)

**Location:** `src/api/schemas/policy.py`

**Requirements References:**
- [policy-knowledge-base:FR-001]: PolicyDocument structure
- [policy-knowledge-base:FR-002]: PolicyRevision structure
- [policy-knowledge-base:FR-004]: Temporal metadata fields

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Valid source format | Source "LTN_1_20" | Validate | Passes |
| TS-02 | Invalid source format | Source "invalid-format" | Validate | Fails with pattern error |
| TS-03 | Valid effective date range | effective_from < effective_to | Validate | Passes |
| TS-04 | Invalid date range | effective_to < effective_from | Validate | Fails with validation error |
| TS-05 | Optional effective_to | effective_to None | Validate | Passes (currently in force) |
| TS-06 | Category enum validation | category "national_policy" | Validate | Passes |
| TS-07 | Invalid category | category "invalid" | Validate | Fails |

### ChromaDB Policy Collection Schema

**Description:** ChromaDB collection schema for policy_docs with metadata fields supporting temporal queries.

**Users:** PolicyKBMCP, PolicyIngestionJob

**Kind:** Data Schema

**Location:** ChromaDB collection `policy_docs`

**Requirements References:**
- [policy-knowledge-base:FR-004]: Temporal metadata
- [policy-knowledge-base:FR-014]: ChromaDB stores embeddings

**Schema:**

```python
{
    "id": "{source}__{revision_id}__{section}__{chunk_index}",
    "embedding": [float, ...],  # 384-dim (all-MiniLM-L6-v2)
    "document": "chunk text content...",
    "metadata": {
        "source": "LTN_1_20",
        "source_title": "Cycle Infrastructure Design (LTN 1/20)",
        "revision_id": "rev_LTN120_2020_07",
        "version_label": "July 2020",
        "effective_from": "2020-07-27",    # ISO date string
        "effective_to": "",                 # Empty string = currently in force
        "chapter": "5",
        "section": "5.3",
        "section_title": "Separation from motor traffic",
        "page_number": 42,
        "chunk_index": 7,
        "table_ref": "Table 5-2"           # If applicable, else ""
    }
}
```

**Temporal Query Pattern:**

```python
results = collection.query(
    query_texts=[query],
    n_results=n,
    where={
        "$and": [
            {"effective_from": {"$lte": effective_date}},
            {"$or": [
                {"effective_to": {"$eq": ""}},
                {"effective_to": {"$gte": effective_date}}
            ]}
        ]
    }
)
```

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Temporal filter returns correct revision | Chunks from 3 revisions | Query with effective_date 2024-03-15 | Only chunks from in-force revision returned |
| TS-02 | Source filter works | Chunks from multiple policies | Query with source filter | Only specified policy chunks returned |
| TS-03 | Empty effective_to means current | Chunk with effective_to="" | Query with current date | Chunk included in results |

---

## Used Components

### Redis (External)

**Location:** Docker image `redis:7-alpine`

**Provides:** Policy registry storage, sorted sets for date-based lookups, job queue backend

**Used By:** PolicyRegistry, PolicyRouter, PolicyIngestionJob, EffectiveDateResolver

### ChromaDB (Existing)

**Location:** `/data/chroma` volume

**Provides:** Vector storage for policy_docs collection with metadata filtering

**Used By:** PolicyKBMCP, PolicyIngestionJob

### Document Store MCP (Existing)

**Location:** `src/mcp_servers/document_store/`

**Provides:** Text extraction, chunking, embedding utilities (shared code)

**Used By:** PolicyIngestionJob (reuses processor.py, embeddings.py)

### arq (Library)

**Location:** Python package

**Provides:** Async job queue for policy ingestion jobs

**Used By:** PolicyRouter (enqueue), PolicyIngestionJob (handler)

### httpx (Library)

**Location:** Python package

**Provides:** Async file upload handling

**Used By:** PolicyRouter

---

## Documentation Considerations

- Policy management guide in README section
- API reference auto-generated via FastAPI OpenAPI at `/docs`
- Seed policy configuration documented
- Effective date resolution algorithm explained
- MCP tool usage examples for agent integration

---

## Instrumentation (if needed)

| Requirement | Observability Criteria | Implementation | Component |
|-------------|------------------------|----------------|-----------|
| [policy-knowledge-base:NFR-005] | Review metadata includes revision IDs and version labels used | Log policy revisions consulted during review | Agent Worker |
| [policy-knowledge-base:NFR-004] | No orphan chunks; no registry entries without embeddings | Consistency check in health endpoint | PolicyRegistry, HealthRouter |

---

## Integration Test Scenarios

| ID | Scenario | Given | When | Then | Components Involved |
|----|----------|-------|------|------|---------------------|
| ITS-01 | Policy CRUD lifecycle | Empty registry | Create policy, upload revision, wait for processing, get policy | Policy active with chunks in ChromaDB | PolicyRouter, PolicyRegistry, PolicyIngestionJob, ChromaDB |
| ITS-02 | Temporal search accuracy | NPPF with revisions 2021, 2023, 2024 | search_policy with date 2024-03-15 | Only 2023 revision chunks returned | PolicyKBMCP, PolicyRegistry, EffectiveDateResolver, ChromaDB |
| ITS-03 | Revision supersession | Active revision with no effective_to | Upload new revision | Previous revision's effective_to auto-set | PolicyRouter, PolicyRegistry |
| ITS-04 | Delete revision removes chunks | Revision with 100 chunks | DELETE revision | All chunks removed from ChromaDB | PolicyRouter, PolicyKBMCP, ChromaDB |
| ITS-05 | Reindex preserves data | Revision with chunks | POST reindex | New chunks created, old removed, same content | PolicyRouter, PolicyIngestionJob, ChromaDB |
| ITS-06 | Effective snapshot consistency | 5 policies with various revisions | GET /policies/effective?date=2024-03-15 | Correct revision for each, consistent with search | PolicyRouter, EffectiveDateResolver |
| ITS-07 | Policy seeder idempotent | Seeder run once | Run seeder again | No duplicates, no errors | PolicySeeder, PolicyRegistry |
| ITS-08 | Registry-ChromaDB consistency | Revision deleted from Redis | Run consistency check | Reports orphan chunks if any | PolicyRegistry, ChromaDB |

---

## E2E Test Scenarios

| ID | Scenario | Given | When | Then | User Journey |
|----|----------|-------|------|------|--------------|
| E2E-01 | Policy management workflow | Admin with PDF | Register policy, upload revision, check status, verify searchable | Policy available for agent queries | Admin creates -> Uploads -> Verifies |
| E2E-02 | Agent uses temporally correct policy | Review submitted for app validated 2024-03-15, NPPF has Dec 2024 revision | Review runs | Review cites Sep 2023 NPPF (correct for date) | Submit review -> Agent queries -> Verify citations |
| E2E-03 | Policy update workflow | LTN 1/20 update released | Upload new revision, verify old revision superseded, verify new revision searchable | Both revisions available, correct one selected by date | Upload -> Verify supersession -> Query by date |
| E2E-04 | First deployment seeding | Fresh system | Start stack | Policy seeder runs, all policies available | Deploy -> Verify policies -> Run review |

---

## Test Data

**Requirements:**
- Sample policy PDFs (LTN 1/20 excerpt, NPPF sections)
- Known section references for get_policy_section testing
- Historical effective dates for temporal query testing
- Seed configuration file

**Sources:**
- Subset PDFs in `tests/fixtures/policy/`
- Effective date test matrix documented in `tests/fixtures/policy/README.md`
- Mock ChromaDB for unit tests, real for integration

---

## Test Feasibility

- **Temporal query testing:** Create test revisions with known dates, verify correct selection
- **ChromaDB testing:** Use real ChromaDB in Docker for integration, mock for unit
- **Seed testing:** Use small subset of policy documents
- **MCP testing:** Use in-process MCP server for tests

---

## Risks and Dependencies

| Risk | Impact | Mitigation |
|------|--------|------------|
| Policy PDFs vary in structure | Inconsistent chunking | Tune chunking params per document; special handling for tables |
| Effective date logic edge cases | Wrong revision cited | Comprehensive test matrix covering all edge cases |
| ChromaDB WHERE filter limitations | Complex temporal queries fail | Validate ChromaDB supports required operators; fallback to post-filtering |
| Large policy documents | Slow ingestion | Batch embedding, progress tracking, timeout handling |
| Redis data loss | Registry lost | Redis AOF persistence; document seed config for recovery |

**External Dependencies:**
- Redis availability
- ChromaDB availability
- Source policy PDF files

**Assumptions:**
- Policy documents are PDFs (primary format)
- Effective dates are known and accurate
- Single revision per policy per time period (no overlapping)

---

## Feasibility Review

All dependencies are established libraries. Temporal filtering pattern is well-documented for ChromaDB. No major technical risks identified.

---

## Task Breakdown

> **CRITICAL: Tests are written WITH implementation, not after.**

### Phase 1: Policy Registry & Data Models

- Task 1: Implement PolicyDocument and PolicyRevision Pydantic models
  - Status: Backlog
  - Models for API requests/responses with validation
  - Source slug pattern validation (UPPER_SNAKE_CASE)
  - Effective date validation (from < to when both present)
  - Requirements: [policy-knowledge-base:FR-001], [policy-knowledge-base:FR-002], [policy-knowledge-base:FR-004]
  - Test Scenarios: [policy-knowledge-base:PolicyModels/TS-01], [policy-knowledge-base:PolicyModels/TS-02], [policy-knowledge-base:PolicyModels/TS-03], [policy-knowledge-base:PolicyModels/TS-04], [policy-knowledge-base:PolicyModels/TS-05], [policy-knowledge-base:PolicyModels/TS-06], [policy-knowledge-base:PolicyModels/TS-07]

- Task 2: Implement PolicyRegistry Redis operations
  - Status: Backlog
  - CRUD operations for policies and revisions
  - Sorted set management for revision ordering
  - Auto-supersession logic for effective_to
  - Requirements: [policy-knowledge-base:FR-001], [policy-knowledge-base:FR-002], [policy-knowledge-base:FR-007], [policy-knowledge-base:FR-008], [policy-knowledge-base:FR-010], [policy-knowledge-base:FR-014]
  - Test Scenarios: [policy-knowledge-base:PolicyRegistry/TS-01], [policy-knowledge-base:PolicyRegistry/TS-02], [policy-knowledge-base:PolicyRegistry/TS-03], [policy-knowledge-base:PolicyRegistry/TS-04], [policy-knowledge-base:PolicyRegistry/TS-05], [policy-knowledge-base:PolicyRegistry/TS-06], [policy-knowledge-base:PolicyRegistry/TS-07], [policy-knowledge-base:PolicyRegistry/TS-08], [policy-knowledge-base:PolicyRegistry/TS-09], [policy-knowledge-base:PolicyRegistry/TS-12]

- Task 3: Implement EffectiveDateResolver
  - Status: Backlog
  - Resolve single policy for date
  - Resolve all policies for date (snapshot)
  - Handle edge cases (no revision, gaps, boundaries)
  - Requirements: [policy-knowledge-base:FR-005], [policy-knowledge-base:FR-009], [policy-knowledge-base:NFR-002]
  - Test Scenarios: [policy-knowledge-base:PolicyRegistry/TS-10], [policy-knowledge-base:PolicyRegistry/TS-11], [policy-knowledge-base:EffectiveDateResolver/TS-01], [policy-knowledge-base:EffectiveDateResolver/TS-02], [policy-knowledge-base:EffectiveDateResolver/TS-03], [policy-knowledge-base:EffectiveDateResolver/TS-04], [policy-knowledge-base:EffectiveDateResolver/TS-05], [policy-knowledge-base:EffectiveDateResolver/TS-06], [policy-knowledge-base:EffectiveDateResolver/TS-07], [policy-knowledge-base:EffectiveDateResolver/TS-08]

### Phase 2: Policy REST API Endpoints

- Task 4: Implement POST /policies and GET /policies endpoints
  - Status: Backlog
  - Create policy document, list all policies
  - Duplicate prevention, category validation
  - Requirements: [policy-knowledge-base:FR-001], [policy-knowledge-base:FR-007]
  - Test Scenarios: [policy-knowledge-base:PolicyRouter/TS-01], [policy-knowledge-base:PolicyRouter/TS-02], [policy-knowledge-base:PolicyRouter/TS-03], [policy-knowledge-base:PolicyRouter/TS-07]

- Task 5: Implement GET /policies/{source} and PATCH /policies/{source}
  - Status: Backlog
  - Get policy detail with revisions, update metadata
  - Requirements: [policy-knowledge-base:FR-008]
  - Test Scenarios: [policy-knowledge-base:PolicyRouter/TS-08], [policy-knowledge-base:PolicyRouter/TS-09]

- Task 6: Implement POST /policies/{source}/revisions endpoint
  - Status: Backlog
  - Multipart file upload, validation, job enqueue
  - Overlap detection, file storage
  - Requirements: [policy-knowledge-base:FR-002]
  - Test Scenarios: [policy-knowledge-base:PolicyRouter/TS-04], [policy-knowledge-base:PolicyRouter/TS-05], [policy-knowledge-base:PolicyRouter/TS-06]

- Task 7: Implement revision management endpoints (GET, PATCH, DELETE, reindex)
  - Status: Backlog
  - Get revision, update metadata, delete, trigger reindex
  - Sole revision protection
  - Requirements: [policy-knowledge-base:FR-010], [policy-knowledge-base:FR-011], [policy-knowledge-base:FR-012]
  - Test Scenarios: [policy-knowledge-base:PolicyRouter/TS-12], [policy-knowledge-base:PolicyRouter/TS-13], [policy-knowledge-base:PolicyRouter/TS-14], [policy-knowledge-base:PolicyRouter/TS-15], [policy-knowledge-base:PolicyRouter/TS-16]

- Task 8: Implement GET /policies/effective endpoint
  - Status: Backlog
  - Snapshot of effective revisions for date
  - Requirements: [policy-knowledge-base:FR-009]
  - Test Scenarios: [policy-knowledge-base:PolicyRouter/TS-10], [policy-knowledge-base:PolicyRouter/TS-11]

### Phase 3: Policy Ingestion Pipeline

- Task 9: Implement PolicyIngestionJob worker
  - Status: Backlog
  - Job handler for revision ingestion and reindex
  - PDF extraction, chunking, embedding
  - Status transitions, progress events
  - Requirements: [policy-knowledge-base:FR-003], [policy-knowledge-base:FR-004], [policy-knowledge-base:FR-012], [policy-knowledge-base:NFR-001]
  - Test Scenarios: [policy-knowledge-base:PolicyIngestionJob/TS-01], [policy-knowledge-base:PolicyIngestionJob/TS-02], [policy-knowledge-base:PolicyIngestionJob/TS-03], [policy-knowledge-base:PolicyIngestionJob/TS-04], [policy-knowledge-base:PolicyIngestionJob/TS-05], [policy-knowledge-base:PolicyIngestionJob/TS-06]

- Task 10: Implement ChromaDB policy_docs collection operations
  - Status: Backlog
  - Insert chunks with revision metadata
  - Delete chunks by revision_id
  - Temporal WHERE clause filtering
  - Requirements: [policy-knowledge-base:FR-004], [policy-knowledge-base:FR-014], [policy-knowledge-base:NFR-004]
  - Test Scenarios: [policy-knowledge-base:ChromaDBSchema/TS-01], [policy-knowledge-base:ChromaDBSchema/TS-02], [policy-knowledge-base:ChromaDBSchema/TS-03]

### Phase 4: Policy KB MCP Server

- Task 11: Implement PolicyKBMCP server with search_policy tool
  - Status: Backlog
  - MCP server setup, SSE transport
  - search_policy with temporal filtering
  - Source filtering
  - Requirements: [policy-knowledge-base:FR-005], [policy-knowledge-base:NFR-002], [policy-knowledge-base:NFR-003]
  - Test Scenarios: [policy-knowledge-base:PolicyKBMCP/TS-01], [policy-knowledge-base:PolicyKBMCP/TS-02], [policy-knowledge-base:PolicyKBMCP/TS-03], [policy-knowledge-base:PolicyKBMCP/TS-04], [policy-knowledge-base:PolicyKBMCP/TS-12]

- Task 12: Implement get_policy_section tool
  - Status: Backlog
  - Section lookup by reference
  - Revision selection (specific or latest)
  - Requirements: [policy-knowledge-base:FR-006]
  - Test Scenarios: [policy-knowledge-base:PolicyKBMCP/TS-05], [policy-knowledge-base:PolicyKBMCP/TS-06], [policy-knowledge-base:PolicyKBMCP/TS-07]

- Task 13: Implement list_policy_documents and list_policy_revisions tools
  - Status: Backlog
  - Delegate to PolicyRegistry
  - Requirements: [policy-knowledge-base:FR-007], [policy-knowledge-base:FR-008]
  - Test Scenarios: [policy-knowledge-base:PolicyKBMCP/TS-08], [policy-knowledge-base:PolicyKBMCP/TS-09]

- Task 14: Implement ingest_policy_revision and remove_policy_revision tools
  - Status: Backlog
  - Worker-callable tools for ingestion
  - Requirements: [policy-knowledge-base:FR-003], [policy-knowledge-base:FR-011]
  - Test Scenarios: [policy-knowledge-base:PolicyKBMCP/TS-10], [policy-knowledge-base:PolicyKBMCP/TS-11]

### Phase 5: Policy Seeder & Docker Integration

- Task 15: Implement PolicySeeder script
  - Status: Backlog
  - Seed configuration file parsing
  - Idempotent seeding logic
  - Error handling for missing files
  - Requirements: [policy-knowledge-base:FR-013]
  - Test Scenarios: [policy-knowledge-base:PolicySeeder/TS-01], [policy-knowledge-base:PolicySeeder/TS-02], [policy-knowledge-base:PolicySeeder/TS-03], [policy-knowledge-base:PolicySeeder/TS-04], [policy-knowledge-base:PolicySeeder/TS-05]

- Task 16: Docker configuration for policy-kb-mcp and policy-init
  - Status: Backlog
  - Dockerfile.policy for MCP server
  - Dockerfile.policy-init for seeder
  - Docker Compose updates
  - Requirements: [policy-knowledge-base:FR-013]
  - Test Scenarios: N/A (deployment config)

### Phase 6: Integration & Testing

- Task 17: Integration tests for policy lifecycle
  - Status: Backlog
  - Full CRUD workflow
  - Temporal search accuracy
  - Consistency checks
  - Requirements: [policy-knowledge-base:NFR-002], [policy-knowledge-base:NFR-004]
  - Test Scenarios: [policy-knowledge-base:ITS-01], [policy-knowledge-base:ITS-02], [policy-knowledge-base:ITS-03], [policy-knowledge-base:ITS-04], [policy-knowledge-base:ITS-05], [policy-knowledge-base:ITS-06], [policy-knowledge-base:ITS-07], [policy-knowledge-base:ITS-08]

- Task 18: E2E tests with agent integration
  - Status: Backlog
  - Policy management workflow
  - Agent temporal query verification
  - First deployment seeding
  - Requirements: [policy-knowledge-base:NFR-002], [policy-knowledge-base:NFR-005]
  - Test Scenarios: [policy-knowledge-base:E2E-01], [policy-knowledge-base:E2E-02], [policy-knowledge-base:E2E-03], [policy-knowledge-base:E2E-04]

- Task 19: Consistency check implementation
  - Status: Backlog
  - Health endpoint policy registry checks
  - Orphan chunk detection
  - Requirements: [policy-knowledge-base:NFR-004]
  - Test Scenarios: [policy-knowledge-base:ITS-08]

- Task 20: Audit trail implementation
  - Status: Backlog
  - Log policy revisions used in reviews
  - Include revision IDs in review metadata
  - Requirements: [policy-knowledge-base:NFR-005]
  - Test Scenarios: N/A (verified by log/metadata inspection)

---

## Intermediate Dead Code Tracking

| Phase Introduced | Description | Used In Phase | Status |
|------------------|-------------|---------------|--------|
| N/A | No intermediate dead code expected | N/A | N/A |

---

## Intermediate Stub Tracking

| Phase Introduced | Test Name | Reason for Stub | Implemented In Phase | Status |
|------------------|-----------|-----------------|----------------------|--------|
| N/A | No stubs expected | N/A | N/A | N/A |

---

## Requirements Validation

### Functional Requirements

- [policy-knowledge-base:FR-001]: Phase 1 Task 1, Phase 1 Task 2, Phase 2 Task 4
- [policy-knowledge-base:FR-002]: Phase 1 Task 1, Phase 1 Task 2, Phase 2 Task 6
- [policy-knowledge-base:FR-003]: Phase 3 Task 9, Phase 4 Task 14
- [policy-knowledge-base:FR-004]: Phase 1 Task 1, Phase 3 Task 9, Phase 3 Task 10
- [policy-knowledge-base:FR-005]: Phase 1 Task 3, Phase 4 Task 11
- [policy-knowledge-base:FR-006]: Phase 4 Task 12
- [policy-knowledge-base:FR-007]: Phase 1 Task 2, Phase 2 Task 4, Phase 4 Task 13
- [policy-knowledge-base:FR-008]: Phase 1 Task 2, Phase 2 Task 5, Phase 4 Task 13
- [policy-knowledge-base:FR-009]: Phase 1 Task 3, Phase 2 Task 8
- [policy-knowledge-base:FR-010]: Phase 1 Task 2, Phase 2 Task 7
- [policy-knowledge-base:FR-011]: Phase 2 Task 7, Phase 4 Task 14
- [policy-knowledge-base:FR-012]: Phase 2 Task 7, Phase 3 Task 9
- [policy-knowledge-base:FR-013]: Phase 5 Task 15, Phase 5 Task 16
- [policy-knowledge-base:FR-014]: Phase 1 Task 2, Phase 3 Task 10

### Non-Functional Requirements

- [policy-knowledge-base:NFR-001]: Phase 3 Task 9
- [policy-knowledge-base:NFR-002]: Phase 1 Task 3, Phase 4 Task 11, Phase 6 Task 17
- [policy-knowledge-base:NFR-003]: Phase 4 Task 11
- [policy-knowledge-base:NFR-004]: Phase 3 Task 10, Phase 6 Task 17, Phase 6 Task 19
- [policy-knowledge-base:NFR-005]: Phase 6 Task 18, Phase 6 Task 20

---

## Test Scenario Validation

### Component Scenarios

- [policy-knowledge-base:PolicyModels/TS-01]: Phase 1 Task 1
- [policy-knowledge-base:PolicyModels/TS-02]: Phase 1 Task 1
- [policy-knowledge-base:PolicyModels/TS-03]: Phase 1 Task 1
- [policy-knowledge-base:PolicyModels/TS-04]: Phase 1 Task 1
- [policy-knowledge-base:PolicyModels/TS-05]: Phase 1 Task 1
- [policy-knowledge-base:PolicyModels/TS-06]: Phase 1 Task 1
- [policy-knowledge-base:PolicyModels/TS-07]: Phase 1 Task 1
- [policy-knowledge-base:PolicyRegistry/TS-01]: Phase 1 Task 2
- [policy-knowledge-base:PolicyRegistry/TS-02]: Phase 1 Task 2
- [policy-knowledge-base:PolicyRegistry/TS-03]: Phase 1 Task 2
- [policy-knowledge-base:PolicyRegistry/TS-04]: Phase 1 Task 2
- [policy-knowledge-base:PolicyRegistry/TS-05]: Phase 1 Task 2
- [policy-knowledge-base:PolicyRegistry/TS-06]: Phase 1 Task 2
- [policy-knowledge-base:PolicyRegistry/TS-07]: Phase 1 Task 2
- [policy-knowledge-base:PolicyRegistry/TS-08]: Phase 1 Task 2
- [policy-knowledge-base:PolicyRegistry/TS-09]: Phase 1 Task 2
- [policy-knowledge-base:PolicyRegistry/TS-10]: Phase 1 Task 3
- [policy-knowledge-base:PolicyRegistry/TS-11]: Phase 1 Task 3
- [policy-knowledge-base:PolicyRegistry/TS-12]: Phase 1 Task 2
- [policy-knowledge-base:EffectiveDateResolver/TS-01]: Phase 1 Task 3
- [policy-knowledge-base:EffectiveDateResolver/TS-02]: Phase 1 Task 3
- [policy-knowledge-base:EffectiveDateResolver/TS-03]: Phase 1 Task 3
- [policy-knowledge-base:EffectiveDateResolver/TS-04]: Phase 1 Task 3
- [policy-knowledge-base:EffectiveDateResolver/TS-05]: Phase 1 Task 3
- [policy-knowledge-base:EffectiveDateResolver/TS-06]: Phase 1 Task 3
- [policy-knowledge-base:EffectiveDateResolver/TS-07]: Phase 1 Task 3
- [policy-knowledge-base:EffectiveDateResolver/TS-08]: Phase 1 Task 3
- [policy-knowledge-base:PolicyRouter/TS-01]: Phase 2 Task 4
- [policy-knowledge-base:PolicyRouter/TS-02]: Phase 2 Task 4
- [policy-knowledge-base:PolicyRouter/TS-03]: Phase 2 Task 4
- [policy-knowledge-base:PolicyRouter/TS-04]: Phase 2 Task 6
- [policy-knowledge-base:PolicyRouter/TS-05]: Phase 2 Task 6
- [policy-knowledge-base:PolicyRouter/TS-06]: Phase 2 Task 6
- [policy-knowledge-base:PolicyRouter/TS-07]: Phase 2 Task 4
- [policy-knowledge-base:PolicyRouter/TS-08]: Phase 2 Task 5
- [policy-knowledge-base:PolicyRouter/TS-09]: Phase 2 Task 5
- [policy-knowledge-base:PolicyRouter/TS-10]: Phase 2 Task 8
- [policy-knowledge-base:PolicyRouter/TS-11]: Phase 2 Task 8
- [policy-knowledge-base:PolicyRouter/TS-12]: Phase 2 Task 7
- [policy-knowledge-base:PolicyRouter/TS-13]: Phase 2 Task 7
- [policy-knowledge-base:PolicyRouter/TS-14]: Phase 2 Task 7
- [policy-knowledge-base:PolicyRouter/TS-15]: Phase 2 Task 7
- [policy-knowledge-base:PolicyRouter/TS-16]: Phase 2 Task 7
- [policy-knowledge-base:PolicyIngestionJob/TS-01]: Phase 3 Task 9
- [policy-knowledge-base:PolicyIngestionJob/TS-02]: Phase 3 Task 9
- [policy-knowledge-base:PolicyIngestionJob/TS-03]: Phase 3 Task 9
- [policy-knowledge-base:PolicyIngestionJob/TS-04]: Phase 3 Task 9
- [policy-knowledge-base:PolicyIngestionJob/TS-05]: Phase 3 Task 9
- [policy-knowledge-base:PolicyIngestionJob/TS-06]: Phase 3 Task 9
- [policy-knowledge-base:ChromaDBSchema/TS-01]: Phase 3 Task 10
- [policy-knowledge-base:ChromaDBSchema/TS-02]: Phase 3 Task 10
- [policy-knowledge-base:ChromaDBSchema/TS-03]: Phase 3 Task 10
- [policy-knowledge-base:PolicyKBMCP/TS-01]: Phase 4 Task 11
- [policy-knowledge-base:PolicyKBMCP/TS-02]: Phase 4 Task 11
- [policy-knowledge-base:PolicyKBMCP/TS-03]: Phase 4 Task 11
- [policy-knowledge-base:PolicyKBMCP/TS-04]: Phase 4 Task 11
- [policy-knowledge-base:PolicyKBMCP/TS-05]: Phase 4 Task 12
- [policy-knowledge-base:PolicyKBMCP/TS-06]: Phase 4 Task 12
- [policy-knowledge-base:PolicyKBMCP/TS-07]: Phase 4 Task 12
- [policy-knowledge-base:PolicyKBMCP/TS-08]: Phase 4 Task 13
- [policy-knowledge-base:PolicyKBMCP/TS-09]: Phase 4 Task 13
- [policy-knowledge-base:PolicyKBMCP/TS-10]: Phase 4 Task 14
- [policy-knowledge-base:PolicyKBMCP/TS-11]: Phase 4 Task 14
- [policy-knowledge-base:PolicyKBMCP/TS-12]: Phase 4 Task 11
- [policy-knowledge-base:PolicySeeder/TS-01]: Phase 5 Task 15
- [policy-knowledge-base:PolicySeeder/TS-02]: Phase 5 Task 15
- [policy-knowledge-base:PolicySeeder/TS-03]: Phase 5 Task 15
- [policy-knowledge-base:PolicySeeder/TS-04]: Phase 5 Task 15
- [policy-knowledge-base:PolicySeeder/TS-05]: Phase 5 Task 15

### Integration Scenarios

- [policy-knowledge-base:ITS-01]: Phase 6 Task 17
- [policy-knowledge-base:ITS-02]: Phase 6 Task 17
- [policy-knowledge-base:ITS-03]: Phase 6 Task 17
- [policy-knowledge-base:ITS-04]: Phase 6 Task 17
- [policy-knowledge-base:ITS-05]: Phase 6 Task 17
- [policy-knowledge-base:ITS-06]: Phase 6 Task 17
- [policy-knowledge-base:ITS-07]: Phase 6 Task 17
- [policy-knowledge-base:ITS-08]: Phase 6 Task 17, Phase 6 Task 19

### E2E Scenarios

- [policy-knowledge-base:E2E-01]: Phase 6 Task 18
- [policy-knowledge-base:E2E-02]: Phase 6 Task 18
- [policy-knowledge-base:E2E-03]: Phase 6 Task 18
- [policy-knowledge-base:E2E-04]: Phase 6 Task 18

---

## Appendix

### Glossary

- **Effective Date:** The date from which a policy revision comes into force
- **Revision:** A specific version of a policy document with its own effective date range
- **Source Slug:** Unique identifier for a policy document (e.g., `LTN_1_20`)
- **Temporal Query:** Search filtered by a point-in-time effective date
- **Supersession:** When a new revision automatically sets the previous revision's effective_to date

### References

- [Master Design Document](../../docs/DESIGN.md) - Sections 3.3, 6.3, 10.4
- [Policy Knowledge Base Specification](specification.md)
- [Project Guidelines](../project-guidelines.md)
- [ChromaDB Documentation](https://docs.trychroma.com/)
- [MCP Specification](https://modelcontextprotocol.io/)

### Change History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-06 | SDD Agent | Initial design |
