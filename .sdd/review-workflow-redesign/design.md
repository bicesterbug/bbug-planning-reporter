# Design: Review Workflow Redesign

**Version:** 1.0
**Date:** 2026-02-13
**Status:** Draft
**Linked Specification** `.sdd/review-workflow-redesign/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The review workflow in `AgentOrchestrator.run()` executes 5 sequential phases:

1. **Fetch metadata** (5%) - MCP call to cherwell-scraper `get_application_details` -> `ApplicationMetadata`
2. **Download documents** (20%) - MCP call to cherwell-scraper `download_all_documents` -> keyword-based `DocumentFilter` decides what to download -> sequential 1 req/sec downloads
3. **Ingest documents** (30%) - MCP calls to document-store `ingest_document` (4 concurrent) -> PyMuPDF text extraction -> 4000-char chunking -> all-MiniLM-L6-v2 embedding (truncates at 1024 chars) -> ChromaDB storage
4. **Analyse application** (30%) - 4 hardcoded application doc search queries + 3 hardcoded policy search queries -> evidence chunks list
5. **Generate review** (15%) - Two Claude API calls (structure JSON + report markdown) -> `ReviewResult` with `summary = review_markdown[:500]`

Key problems in this pipeline:
- Phase 2 downloads 100+ documents using keyword pattern matching that misses inconsistently named docs and includes irrelevant ones
- Phase 3 chunks at 4000 chars but embeddings truncate at 1024 chars, losing 75% of content from the embedding vector
- Phase 4 uses static search queries regardless of application type or proposal
- Phase 5 has no verification, and the summary is a naive character truncation
- ~1200 lines of dead code from an unused alternative pipeline (assessor, generator, policy_comparer, templates, claude_client)

### Proposed Architecture

The 5-phase pipeline is restructured to 7 phases:

1. **Fetch metadata** (5%) - Unchanged
2. **Filter documents** (5%) - NEW: Fetch document list, then send it + application metadata to Haiku for LLM-based relevance classification. Returns selected document IDs only.
3. **Download documents** (15%) - Modified: Downloads only LLM-selected documents instead of keyword-filtered documents
4. **Ingest documents** (25%) - Modified: Chunk size reduced to 800 chars with 200 char overlap to fit embedding model's 1024-char limit
5. **Analyse application** (25%) - Modified: LLM generates targeted search queries based on proposal metadata instead of using hardcoded strings
6. **Generate review** (15%) - Modified: Structure prompt includes a `summary` field; report prompt unchanged
7. **Verify review** (10%) - NEW: Post-generation LLM call validates citations and factual claims against evidence. Verification metadata included in output.

Phase flow:
```
Phase 1: MCP -> cherwell-scraper -> get_application_details -> ApplicationMetadata
Phase 2: MCP -> cherwell-scraper -> list_application_documents -> LLM (Haiku) -> selected doc IDs
Phase 3: MCP -> cherwell-scraper -> download_document (per selected doc) -> DocumentIngestionResult
Phase 4: MCP -> document-store -> ingest_document (concurrent) -> ChromaDB
Phase 5: LLM (Haiku) -> search queries -> MCP -> document-store + policy-kb -> evidence_chunks
Phase 6: LLM (Sonnet) -> structure JSON (with summary) + report markdown -> review dict
Phase 7: LLM (Haiku) -> verification check -> verification metadata
```

### Technology Decisions

- **Haiku for classification tasks** (Phases 2, 5, 7): Document filtering, query generation, and verification are classification/extraction tasks that don't require Sonnet's generation capabilities. Using Haiku reduces cost (~10x cheaper) and latency.
- **Anthropic Python SDK**: Already a dependency. The Haiku calls use the same `anthropic.Anthropic` client as the existing Sonnet calls. The model is specified per-call.
- **No new MCP tools needed**: Phase 2 uses the existing `list_application_documents` tool to get the document list, then downloads individual docs with `download_document`. The `download_all_documents` tool is no longer used by the orchestrator (it remains available for other callers).
- **Chunk size reduction**: Change `DEFAULT_CHUNK_SIZE` from 1000 tokens (4000 chars) to 200 tokens (800 chars), and `DEFAULT_CHUNK_OVERLAP` from 200 tokens (800 chars) to 50 tokens (200 chars). This ensures chunks fit within the embedding model's 1024-char limit with margin.

### Quality Attributes

- **Efficiency**: 60-80% fewer documents downloaded and vectorised; Haiku calls are fast and cheap
- **Accuracy**: Properly-sized embeddings capture full chunk content; dynamic queries match the application; verification catches hallucinations
- **Reliability**: LLM filter failure is a hard error (no silent degradation); verification metadata provides transparency
- **Maintainability**: Dead code removed; workflow stages clearly delineated

---

## API Design

No public API changes. The `ReviewRequest`, `ReviewResponse`, and `ReviewContent` schemas are unchanged. The `verification` metadata is added to the `metadata` dict within the review output, which is already a free-form dict.

The verification metadata shape within `review.metadata`:
```
{
    "verification": {
        "status": "verified" | "partial" | "failed",
        "verified_claims": <int>,
        "unverified_claims": <int>,
        "total_claims": <int>,
        "details": [
            {
                "claim": <string>,
                "verified": <bool>,
                "source": <string | null>
            }
        ],
        "duration_seconds": <float>
    }
}
```

---

## Modified Components

### AgentOrchestrator
**Change Description** Currently runs 5 phases with hardcoded search queries, keyword-based filtering (delegated to scraper), and no verification. Changes to 7 phases: adds LLM document filtering (Phase 2), uses LLM-generated search queries (Phase 5), adds verification (Phase 7), and generates an LLM summary (Phase 6). The `_phase_download_documents` method is split into `_phase_filter_documents` (new) and `_phase_download_documents` (modified to accept pre-filtered list). The `_phase_analyse_application` method is rewritten to generate queries dynamically.

**Dependants** `src/worker/review_jobs.py` (no changes needed - it calls `orchestrator.run()` and receives `ReviewResult`)

**Kind** Class

**Requirements References**
- [review-workflow-redesign:FR-001]: LLM document filtering requires a new phase between metadata fetch and download
- [review-workflow-redesign:FR-002]: Filter failure handling requires the new phase to raise non-recoverable error
- [review-workflow-redesign:FR-004]: Dynamic query generation requires rewriting the analysis phase
- [review-workflow-redesign:FR-005]: Verification requires a new phase after review generation
- [review-workflow-redesign:FR-006]: LLM summary requires modifying the review dict construction
- [review-workflow-redesign:FR-008]: Application-aware filtering requires passing metadata to the filter prompt
- [review-workflow-redesign:NFR-001]: Filter latency logging
- [review-workflow-redesign:NFR-002]: Document count comparison logging
- [review-workflow-redesign:NFR-003]: Verification duration logging
- [review-workflow-redesign:NFR-005]: Verification metadata in output

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| AgentOrchestrator/TS-01 | LLM filter selects subset of documents | Application with 50 documents listed | Phase 2 runs with mocked Haiku returning 10 document IDs | Only 10 documents are passed to Phase 3 for download |
| AgentOrchestrator/TS-02 | LLM filter failure aborts review | Application with documents listed | Phase 2 LLM call raises API error | Review fails with error code `document_filter_failed`, no download attempted |
| AgentOrchestrator/TS-03 | LLM filter receives application context | Application with known proposal text | Phase 2 runs | The prompt sent to Haiku includes proposal description, address, and application type |
| AgentOrchestrator/TS-04 | Dynamic query generation produces tailored queries | Application for "200-dwelling residential development" | Phase 5 runs with mocked Haiku | Search queries are proposal-specific, not the hardcoded defaults |
| AgentOrchestrator/TS-05 | Verification phase populates metadata | Review generated successfully with evidence | Phase 7 runs | `ReviewResult.metadata` contains `verification` dict with status, counts, and details |
| AgentOrchestrator/TS-06 | LLM-generated summary replaces truncation | Review markdown generated | Review dict is built | `review["summary"]` is a 2-4 sentence LLM-generated summary, not `markdown[:500]` |
| AgentOrchestrator/TS-07 | Seven phases execute in order | Valid application ref | `orchestrator.run()` completes | All 7 phases executed: fetch_metadata, filter_documents, download_documents, ingest_documents, analyse_application, generate_review, verify_review |
| AgentOrchestrator/TS-08 | Verification with unverified claims returns partial | Review cites a document not in ingested list | Phase 7 runs | Verification status is "partial", unverified_claims > 0, review is still returned |
| AgentOrchestrator/TS-09 | Empty query results handled gracefully | LLM generates 5 queries, 2 return zero results | Phase 5 completes | Evidence from the 3 successful queries is used; no error raised |
| AgentOrchestrator/TS-10 | Document count comparison logged | Application with 80 documents, 12 selected | Phases 2-4 complete | Structured log emitted with total_listed=80, selected=12, ingested=12 |

### TextChunker
**Change Description** Currently uses `DEFAULT_CHUNK_SIZE = 1000` tokens (4000 chars) and `DEFAULT_CHUNK_OVERLAP = 200` tokens (800 chars). Change to `DEFAULT_CHUNK_SIZE = 200` tokens (800 chars) and `DEFAULT_CHUNK_OVERLAP = 50` tokens (200 chars) to fit within the embedding model's 1024-char input limit.

**Dependants** `src/mcp_servers/document_store/server.py` (uses TextChunker with defaults), `src/mcp_servers/document_store/embeddings.py` (no longer truncates)

**Kind** Class

**Requirements References**
- [review-workflow-redesign:FR-003]: Chunk sizes must not exceed embedding model's max input length
- [review-workflow-redesign:NFR-004]: Zero truncation warnings during embedding

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TextChunker/TS-01 | Chunks fit embedding limit | 10,000-character document | Chunked with new defaults | All chunks are <= 1024 characters |
| TextChunker/TS-02 | Overlap preserved between chunks | Document with clear sentence boundaries | Chunked with new defaults | Adjacent chunks share ~200 chars of overlapping text |
| TextChunker/TS-03 | Short documents produce single chunk | 500-character document | Chunked with new defaults | Single chunk containing full text |

### build_structure_prompt
**Change Description** Currently the JSON schema does not include a `summary` field. Add a `summary` field (string, 2-4 sentences) to the required JSON output schema. The prompt should instruct the LLM to generate a concise review summary as part of the structured output.

**Dependants** `src/agent/orchestrator.py` (reads summary from structure JSON), `src/agent/review_schema.py` (validates summary field)

**Kind** Function

**Requirements References**
- [review-workflow-redesign:FR-006]: LLM-generated summary replaces naive truncation

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| build_structure_prompt/TS-01 | Summary field in schema | N/A | `build_structure_prompt()` called | System prompt JSON schema includes `summary` field described as 2-4 sentence summary |
| build_structure_prompt/TS-02 | Summary in example output | N/A | System prompt inspected | The schema description instructs a concise summary including overall rating |

### ReviewStructure (review_schema.py)
**Change Description** Currently has no `summary` field. Add `summary: str` field to the Pydantic model so the structure call's JSON response is validated to include a summary.

**Dependants** `src/agent/orchestrator.py` (reads `structure.summary`)

**Kind** Class

**Requirements References**
- [review-workflow-redesign:FR-006]: Validation ensures summary is always present in structure output

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| ReviewStructure/TS-01 | Valid structure with summary | JSON with all fields including summary | Parsed by ReviewStructure | Model validates successfully, `structure.summary` accessible |
| ReviewStructure/TS-02 | Missing summary fails validation | JSON without summary field | Parsed by ReviewStructure | ValidationError raised |

### src/agent/__init__.py
**Change Description** Currently re-exports `ReviewAssessor`, `ReviewGenerator`, `PolicyComparer`, `ReviewTemplates`, and `ClaudeClient`. These re-exports are removed along with the corresponding imports.

**Dependants** None (no production code imports from `src.agent` directly)

**Kind** Module

**Requirements References**
- [review-workflow-redesign:FR-007]: Dead code removal includes cleaning up re-exports

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| agent_init/TS-01 | No dead imports remain | After dead code removal | `grep -r "from src.agent import" src/` | Zero matches for removed symbols |

---

## Added Components

### document_filter_prompt (build_document_filter_prompt)
**Description** Builds the system and user prompts for the LLM document filter. The system prompt instructs Haiku to act as a planning document relevance classifier for cycling/transport advocacy reviews. The user prompt includes the application metadata (reference, address, proposal, type) and the full document list (ID, description, type, date) and asks the LLM to return a JSON array of document IDs that are relevant. The prompt specifies categories of relevant documents (transport assessments, design statements, site plans, highway reports, travel plans, officer reports, decision notices) and instructs the LLM to include documents with ambiguous names.

**Users** `AgentOrchestrator._phase_filter_documents`

**Kind** Function

**Location** `src/agent/prompts/document_filter_prompt.py`

**Requirements References**
- [review-workflow-redesign:FR-001]: Builds the prompt for LLM-based document filtering
- [review-workflow-redesign:FR-008]: Includes application metadata in the prompt for context-aware filtering

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| document_filter_prompt/TS-01 | Prompt includes application metadata | Application with proposal "200 dwellings" | `build_document_filter_prompt()` called | User prompt contains proposal text, address, and reference |
| document_filter_prompt/TS-02 | Prompt includes full document list | 50 documents with IDs, descriptions, types | `build_document_filter_prompt()` called | User prompt lists all 50 documents with their metadata |
| document_filter_prompt/TS-03 | System prompt specifies JSON array output | N/A | System prompt inspected | Instructs LLM to return only a JSON array of document ID strings |

### search_query_prompt (build_search_query_prompt)
**Description** Builds the system and user prompts for LLM-based search query generation. The system prompt instructs Haiku to generate targeted semantic search queries for a planning application review from a cycling advocacy perspective. The user prompt includes the application metadata and the list of ingested document descriptions. The LLM returns a JSON object with two arrays: `application_queries` (4-6 queries for searching application documents) and `policy_queries` (3-5 queries for searching policy documents, each with a list of relevant policy source filters).

**Users** `AgentOrchestrator._phase_analyse_application`

**Kind** Function

**Location** `src/agent/prompts/search_query_prompt.py`

**Requirements References**
- [review-workflow-redesign:FR-004]: Builds the prompt for dynamic search query generation

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| search_query_prompt/TS-01 | Prompt includes proposal context | Application for "residential development with new access" | `build_search_query_prompt()` called | User prompt contains the proposal description |
| search_query_prompt/TS-02 | Returns structured JSON schema | N/A | System prompt inspected | Specifies JSON with `application_queries` (list of strings) and `policy_queries` (list of `{query, sources}` objects) |
| search_query_prompt/TS-03 | Includes ingested document list | 10 ingested documents | `build_search_query_prompt()` called | User prompt lists the ingested documents so queries can target them |

### verification_prompt (build_verification_prompt)
**Description** Builds the system and user prompts for post-generation verification. The system prompt instructs Haiku to act as a fact-checker, comparing the generated review against the source evidence. The user prompt includes the review markdown, the structured review data (key documents, aspects, claims), the list of ingested documents, and the evidence chunks. The LLM returns a JSON object with verification results: for each major factual claim in the review, whether it can be traced to a specific evidence chunk, and which source document supports it.

**Users** `AgentOrchestrator._phase_verify_review`

**Kind** Function

**Location** `src/agent/prompts/verification_prompt.py`

**Requirements References**
- [review-workflow-redesign:FR-005]: Builds the prompt for post-generation verification

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| verification_prompt/TS-01 | Prompt includes review and evidence | Review markdown + 15 evidence chunks | `build_verification_prompt()` called | User prompt contains the review text and evidence chunks |
| verification_prompt/TS-02 | Specifies claim verification output | N/A | System prompt inspected | Specifies JSON with claims array, each containing claim text, verified boolean, and source |
| verification_prompt/TS-03 | Includes ingested document list for citation check | 8 ingested documents | `build_verification_prompt()` called | User prompt includes document titles so citation existence can be verified |

---

## Used Components

### MCPClientManager
**Location** `src/agent/mcp_client.py`

**Provides** `call_tool(tool_name, arguments, timeout)` for invoking MCP server tools. Routes to the correct server based on tool name.

**Used By** AgentOrchestrator (all phases that interact with MCP servers)

### Anthropic Python SDK
**Location** `anthropic` package (external dependency)

**Provides** `Anthropic().messages.create()` for Claude API calls. Supports model selection per-call (Haiku vs Sonnet).

**Used By** AgentOrchestrator (Phases 2, 5, 6, 7 for LLM calls)

### EmbeddingService
**Location** `src/mcp_servers/document_store/embeddings.py`

**Provides** `embed()` and `embed_batch()` for generating sentence embeddings. Uses all-MiniLM-L6-v2 with 1024-char max input.

**Used By** Document-store MCP server (ingestion pipeline). Benefits from TextChunker changes since chunks will now fit within the 1024-char limit.

### ChromaClient
**Location** `src/mcp_servers/document_store/chroma_client.py`

**Provides** Vector storage and semantic search for document chunks and policy chunks.

**Used By** Document-store MCP server and Policy-KB MCP server (via search tools called by orchestrator)

### DocumentFilter (existing, retained)
**Location** `src/mcp_servers/cherwell_scraper/filters.py`

**Provides** Keyword-based document filtering. Retained for use by the `download_all_documents` MCP tool (which other callers may use), but no longer invoked by the orchestrator's workflow.

**Used By** `CherwellScraperMCP._download_all_documents` (retained for backward compatibility)

---

## Documentation Considerations
- `.env.example`: Add `DOCUMENT_FILTER_MODEL` env var (default: `claude-haiku-4-5-20251001`)
- `MEMORY.md`: Update known issues to remove the truncation warning note (will be fixed by chunk size change)

---

## Instrumentation

| Requirement | Observability Criteria | Implementation | Component |
|-------------|------------------------|----------------|-----------|
| [review-workflow-redesign:NFR-001] | Filter call duration < 15s | `logger.info("Document filter completed", duration_seconds=..., total_documents=..., selected_documents=...)` | AgentOrchestrator._phase_filter_documents |
| [review-workflow-redesign:NFR-002] | Documents selected vs total | `logger.info("Document selection summary", total_listed=..., selected=..., ingested=..., reduction_pct=...)` | AgentOrchestrator._phase_download_documents |
| [review-workflow-redesign:NFR-003] | Verification duration < 30s | `logger.info("Verification completed", duration_seconds=..., status=..., verified_claims=..., unverified_claims=...)` | AgentOrchestrator._phase_verify_review |
| [review-workflow-redesign:NFR-004] | Zero truncation warnings | Existing truncation warning in `EmbeddingService.embed()` will cease firing because chunks fit the limit | EmbeddingService (unchanged, but behavior changes due to TextChunker fix) |
| [review-workflow-redesign:NFR-005] | Verification metadata in output | Verification dict added to `ReviewResult.metadata` with status, counts, and claim details | AgentOrchestrator._phase_verify_review |

---

## Integration Test Scenarios

| ID | Scenario | Given | When | Then | Components Involved |
|----|----------|-------|------|------|---------------------|
| ITS-01 | LLM filter to download pipeline | Application with 30 documents, mocked Haiku selects 5 | Phases 2-3 run | Only 5 documents downloaded, download count matches filter output | AgentOrchestrator, document_filter_prompt, MCPClient, cherwell-scraper |
| ITS-02 | Dynamic queries to evidence retrieval | Application with ingested docs, mocked Haiku returns queries | Phases 5 runs with mocked MCP search | Search calls use LLM-generated queries, evidence_chunks populated | AgentOrchestrator, search_query_prompt, MCPClient |
| ITS-03 | Review generation with summary | Mocked evidence context | Phase 6 runs with mocked Claude | Review dict contains LLM-generated summary (not truncated markdown) | AgentOrchestrator, build_structure_prompt, ReviewStructure |
| ITS-04 | Verification against evidence | Generated review with known citations | Phase 7 runs with mocked Haiku | Verification metadata correctly identifies verified and unverified claims | AgentOrchestrator, verification_prompt |
| ITS-05 | Chunk size prevents truncation | PDF document ingested | Ingestion pipeline runs | All chunks <= 1024 chars, zero truncation warnings | TextChunker, EmbeddingService |
| ITS-06 | Full workflow with new phases | Valid application ref, all MCP and LLM calls mocked | `orchestrator.run()` | All 7 phases complete, result contains verification metadata and LLM summary | AgentOrchestrator, all prompt modules |

---

## E2E Test Scenarios

| ID | Scenario | Given | When | Then | User Journey |
|----|----------|-------|------|------|--------------|
| E2E-01 | Complete review with LLM filtering | Valid application ref, MCP servers running, Claude API available | POST /api/v1/reviews with application_ref | Review completes with fewer documents, verification metadata present, LLM summary in output | 1. Submit review 2. Worker picks up job 3. Metadata fetched 4. Documents filtered by LLM 5. Selected docs downloaded 6. Docs ingested with correct chunk sizes 7. Dynamic queries generated 8. Evidence retrieved 9. Review generated with summary 10. Review verified 11. Result stored with verification metadata |

---

## Test Data

- Existing test fixtures in `tests/fixtures/` for HTML parsing and document lists
- Mock Haiku responses for document filter (JSON arrays of document IDs)
- Mock Haiku responses for search query generation (JSON with query arrays)
- Mock Haiku responses for verification (JSON with claim verification results)
- Existing mocked Claude responses for structure and report calls
- Sample documents of varying lengths for chunk size testing

---

## Test Feasibility

- All LLM calls (Haiku and Sonnet) will be mocked in unit and integration tests using `unittest.mock.patch`
- MCP calls will be mocked as in existing tests (patching `MCPClientManager.call_tool`)
- E2E tests require running MCP servers and Claude API access; these are already skipped in CI (marked with `@pytest.mark.skip` or conditional skip)
- No new test infrastructure needed

---

## Risks and Dependencies

**Risks:**
1. **Haiku model availability**: If `claude-haiku-4-5-20251001` is unavailable or deprecated, the filter, query generation, and verification calls fail. Mitigation: Model name configurable via env var `DOCUMENT_FILTER_MODEL`.
2. **LLM filter quality**: Haiku may miss relevant documents or include irrelevant ones. Mitigation: Prompt engineering with clear instructions to err on the side of inclusion for ambiguous documents; operational monitoring via structured logs.
3. **Chunk size reduction increases chunk count**: Reducing from 4000 to 800 chars per chunk means ~5x more chunks per document. This increases ChromaDB storage and may slow search. Mitigation: With 60-80% fewer documents being ingested, the total chunk count should remain comparable or lower than before.
4. **Verification adding latency**: An additional LLM call adds 5-20 seconds. Mitigation: Uses Haiku (fast); bounded by 30-second threshold; runs after the review is already generated so doesn't block the core output.
5. **Dynamic query quality**: LLM-generated queries may be less focused than the curated hardcoded ones. Mitigation: Prompt specifies cycling advocacy context and provides examples of good queries; results validated by the evidence retrieval count.

**Dependencies:**
- Anthropic API with Haiku model access
- Existing MCP servers (cherwell-scraper, document-store, policy-kb) unchanged
- ChromaDB (unchanged)

**Assumptions:**
- The `list_application_documents` MCP tool returns document IDs consistent with `download_document` input expectations
- Haiku can handle document lists of 500+ items within its context window (~200K tokens for Haiku)
- The existing `download_document` MCP tool can download individual documents by URL

---

## Feasibility Review

No large missing features or infrastructure. All dependencies are already available:
- Anthropic SDK supports Haiku model selection per-call
- MCP tools for listing and downloading individual documents already exist
- The orchestrator's phase pattern is well-established and extensible

---

## Task Breakdown

> **CRITICAL: Tests are written WITH implementation, not after.**

### Phase 1: Foundation - chunk size fix and dead code removal

- Task 1: Fix chunk size defaults in TextChunker
  - Status: Done
  - Change `DEFAULT_CHUNK_SIZE` from 1000 to 200 tokens (800 chars) and `DEFAULT_CHUNK_OVERLAP` from 200 to 50 tokens (200 chars) in `src/mcp_servers/document_store/chunker.py`. Update existing tests to use new defaults. Add test asserting all chunks fit within 1024-char embedding limit.
  - Requirements: [review-workflow-redesign:FR-003], [review-workflow-redesign:NFR-004]
  - Test Scenarios: [review-workflow-redesign:TextChunker/TS-01], [review-workflow-redesign:TextChunker/TS-02], [review-workflow-redesign:TextChunker/TS-03], [review-workflow-redesign:ITS-05]

- Task 2: Remove dead code (assessor, generator, policy_comparer, templates, claude_client)
  - Status: Done
  - Delete `src/agent/assessor.py`, `src/agent/generator.py`, `src/agent/policy_comparer.py`, `src/agent/templates.py`, `src/agent/claude_client.py`. Remove their re-exports from `src/agent/__init__.py`. Delete associated test files. Verify no remaining imports with grep.
  - Requirements: [review-workflow-redesign:FR-007], [review-workflow-redesign:NFR-006]
  - Test Scenarios: [review-workflow-redesign:agent_init/TS-01]

### Phase 2: LLM document filtering

- Task 3: Create document filter prompt module
  - Status: Done
  - Create `src/agent/prompts/document_filter_prompt.py` with `build_document_filter_prompt(application_metadata, document_list) -> tuple[str, str]`. System prompt instructs Haiku to classify documents for cycling/transport relevance. User prompt includes application metadata and full document list. Returns system and user prompts. Write tests.
  - Requirements: [review-workflow-redesign:FR-001], [review-workflow-redesign:FR-008]
  - Test Scenarios: [review-workflow-redesign:document_filter_prompt/TS-01], [review-workflow-redesign:document_filter_prompt/TS-02], [review-workflow-redesign:document_filter_prompt/TS-03]

- Task 4: Add `_phase_filter_documents` to AgentOrchestrator and restructure download phase
  - Status: Done
  - Add new `_phase_filter_documents` method: calls `list_application_documents` MCP tool, builds prompt, calls Haiku, parses JSON response to get selected document IDs. Stores selected docs on `self._selected_documents`. On failure (API error, malformed JSON), raises non-recoverable `OrchestratorError` with code `document_filter_failed`. Modify `_phase_download_documents` to download only `self._selected_documents` using individual `download_document` MCP calls instead of `download_all_documents`. Update phase list to insert new phase. Add `DOCUMENT_FILTER_MODEL` env var (default `claude-haiku-4-5-20251001`). Add structured logging for filter duration and document counts.
  - Requirements: [review-workflow-redesign:FR-001], [review-workflow-redesign:FR-002], [review-workflow-redesign:FR-008], [review-workflow-redesign:NFR-001], [review-workflow-redesign:NFR-002]
  - Test Scenarios: [review-workflow-redesign:AgentOrchestrator/TS-01], [review-workflow-redesign:AgentOrchestrator/TS-02], [review-workflow-redesign:AgentOrchestrator/TS-03], [review-workflow-redesign:AgentOrchestrator/TS-10], [review-workflow-redesign:ITS-01]

### Phase 3: Dynamic search queries and LLM summary

- Task 5: Create search query prompt module
  - Status: Done
  - Create `src/agent/prompts/search_query_prompt.py` with `build_search_query_prompt(application_metadata, ingested_documents) -> tuple[str, str]`. System prompt instructs Haiku to generate targeted search queries for cycling advocacy review. User prompt includes application metadata and ingested document list. Specifies JSON output with `application_queries` and `policy_queries` arrays. Write tests.
  - Requirements: [review-workflow-redesign:FR-004]
  - Test Scenarios: [review-workflow-redesign:search_query_prompt/TS-01], [review-workflow-redesign:search_query_prompt/TS-02], [review-workflow-redesign:search_query_prompt/TS-03]

- Task 6: Rewrite `_phase_analyse_application` to use LLM-generated queries
  - Status: Done
  - Replace the 4 hardcoded application queries and 3 hardcoded policy queries with LLM-generated queries from `build_search_query_prompt`. Call Haiku to generate queries, parse JSON response, then execute each query via existing MCP search tools. Handle empty results gracefully (proceed with queries that returned results). Keep the same evidence accumulation pattern (`self._evidence_chunks`).
  - Requirements: [review-workflow-redesign:FR-004]
  - Test Scenarios: [review-workflow-redesign:AgentOrchestrator/TS-04], [review-workflow-redesign:AgentOrchestrator/TS-09], [review-workflow-redesign:ITS-02]

- Task 7: Add summary field to structure prompt and ReviewStructure schema
  - Status: Done
  - Add `summary` field to the JSON schema in `build_structure_prompt`'s system prompt, described as "A concise 2-4 sentence summary of the review including the overall rating." Add `summary: str` field to `ReviewStructure` Pydantic model. In `_phase_generate_review`, read `structure.summary` and use it for `review["summary"]` instead of `review_markdown[:500]`.
  - Requirements: [review-workflow-redesign:FR-006]
  - Test Scenarios: [review-workflow-redesign:build_structure_prompt/TS-01], [review-workflow-redesign:build_structure_prompt/TS-02], [review-workflow-redesign:ReviewStructure/TS-01], [review-workflow-redesign:ReviewStructure/TS-02], [review-workflow-redesign:AgentOrchestrator/TS-06], [review-workflow-redesign:ITS-03]

### Phase 4: Verification stage

- Task 8: Create verification prompt module
  - Status: Done
  - Create `src/agent/prompts/verification_prompt.py` with `build_verification_prompt(review_markdown, review_structure, ingested_documents, evidence_chunks) -> tuple[str, str]`. System prompt instructs Haiku to verify claims against evidence. User prompt includes the review, ingested document list, and evidence chunks. Specifies JSON output with verification results. Write tests.
  - Requirements: [review-workflow-redesign:FR-005]
  - Test Scenarios: [review-workflow-redesign:verification_prompt/TS-01], [review-workflow-redesign:verification_prompt/TS-02], [review-workflow-redesign:verification_prompt/TS-03]

- Task 9: Add `_phase_verify_review` to AgentOrchestrator
  - Status: Done
  - Add new `_phase_verify_review` method: builds verification prompt, calls Haiku, parses JSON response, constructs verification metadata dict. Stores verification dict in `self._verification`. In `run()`, after `_phase_generate_review`, run `_phase_verify_review` and merge verification into `ReviewResult.metadata`. Verification failure is logged but does not fail the review (verification is best-effort). Add structured logging for verification duration and results. Update phase list to include 7 phases. Write integration test for full 7-phase workflow.
  - Requirements: [review-workflow-redesign:FR-005], [review-workflow-redesign:NFR-003], [review-workflow-redesign:NFR-005]
  - Test Scenarios: [review-workflow-redesign:AgentOrchestrator/TS-05], [review-workflow-redesign:AgentOrchestrator/TS-07], [review-workflow-redesign:AgentOrchestrator/TS-08], [review-workflow-redesign:ITS-04], [review-workflow-redesign:ITS-06]

### Phase 5: Environment config and cleanup

- Task 10: Update environment configuration and documentation
  - Status: Done
  - Add `DOCUMENT_FILTER_MODEL` to `.env.example` and `deploy/.env.example`. Add it to `docker-compose.yml` and `deploy/docker-compose.yml` worker service environment. Update MEMORY.md to remove truncation warning note.
  - Requirements: [review-workflow-redesign:NFR-001]
  - Test Scenarios: N/A (configuration-only)

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

- [review-workflow-redesign:FR-001]
  - Phase 2 Task 3 (document filter prompt)
  - Phase 2 Task 4 (filter phase in orchestrator)
- [review-workflow-redesign:FR-002]
  - Phase 2 Task 4 (filter failure handling)
- [review-workflow-redesign:FR-003]
  - Phase 1 Task 1 (chunk size fix)
- [review-workflow-redesign:FR-004]
  - Phase 3 Task 5 (search query prompt)
  - Phase 3 Task 6 (dynamic queries in orchestrator)
- [review-workflow-redesign:FR-005]
  - Phase 4 Task 8 (verification prompt)
  - Phase 4 Task 9 (verification phase in orchestrator)
- [review-workflow-redesign:FR-006]
  - Phase 3 Task 7 (summary in structure prompt and schema)
- [review-workflow-redesign:FR-007]
  - Phase 1 Task 2 (dead code removal)
- [review-workflow-redesign:FR-008]
  - Phase 2 Task 3 (application context in filter prompt)
  - Phase 2 Task 4 (application metadata passed to filter)

- [review-workflow-redesign:NFR-001]
  - Phase 2 Task 4 (filter latency logging)
  - Phase 5 Task 10 (DOCUMENT_FILTER_MODEL env var)
- [review-workflow-redesign:NFR-002]
  - Phase 2 Task 4 (document count comparison logging)
- [review-workflow-redesign:NFR-003]
  - Phase 4 Task 9 (verification duration logging)
- [review-workflow-redesign:NFR-004]
  - Phase 1 Task 1 (chunk size fix eliminates truncation)
- [review-workflow-redesign:NFR-005]
  - Phase 4 Task 9 (verification metadata in output)
- [review-workflow-redesign:NFR-006]
  - Phase 1 Task 2 (dead code removal)

---

## Test Scenario Validation

### Component Scenarios
- [review-workflow-redesign:AgentOrchestrator/TS-01]: Phase 2 Task 4
- [review-workflow-redesign:AgentOrchestrator/TS-02]: Phase 2 Task 4
- [review-workflow-redesign:AgentOrchestrator/TS-03]: Phase 2 Task 4
- [review-workflow-redesign:AgentOrchestrator/TS-04]: Phase 3 Task 6
- [review-workflow-redesign:AgentOrchestrator/TS-05]: Phase 4 Task 9
- [review-workflow-redesign:AgentOrchestrator/TS-06]: Phase 3 Task 7
- [review-workflow-redesign:AgentOrchestrator/TS-07]: Phase 4 Task 9
- [review-workflow-redesign:AgentOrchestrator/TS-08]: Phase 4 Task 9
- [review-workflow-redesign:AgentOrchestrator/TS-09]: Phase 3 Task 6
- [review-workflow-redesign:AgentOrchestrator/TS-10]: Phase 2 Task 4
- [review-workflow-redesign:TextChunker/TS-01]: Phase 1 Task 1
- [review-workflow-redesign:TextChunker/TS-02]: Phase 1 Task 1
- [review-workflow-redesign:TextChunker/TS-03]: Phase 1 Task 1
- [review-workflow-redesign:build_structure_prompt/TS-01]: Phase 3 Task 7
- [review-workflow-redesign:build_structure_prompt/TS-02]: Phase 3 Task 7
- [review-workflow-redesign:ReviewStructure/TS-01]: Phase 3 Task 7
- [review-workflow-redesign:ReviewStructure/TS-02]: Phase 3 Task 7
- [review-workflow-redesign:document_filter_prompt/TS-01]: Phase 2 Task 3
- [review-workflow-redesign:document_filter_prompt/TS-02]: Phase 2 Task 3
- [review-workflow-redesign:document_filter_prompt/TS-03]: Phase 2 Task 3
- [review-workflow-redesign:search_query_prompt/TS-01]: Phase 3 Task 5
- [review-workflow-redesign:search_query_prompt/TS-02]: Phase 3 Task 5
- [review-workflow-redesign:search_query_prompt/TS-03]: Phase 3 Task 5
- [review-workflow-redesign:verification_prompt/TS-01]: Phase 4 Task 8
- [review-workflow-redesign:verification_prompt/TS-02]: Phase 4 Task 8
- [review-workflow-redesign:verification_prompt/TS-03]: Phase 4 Task 8
- [review-workflow-redesign:agent_init/TS-01]: Phase 1 Task 2

### Integration Scenarios
- [review-workflow-redesign:ITS-01]: Phase 2 Task 4
- [review-workflow-redesign:ITS-02]: Phase 3 Task 6
- [review-workflow-redesign:ITS-03]: Phase 3 Task 7
- [review-workflow-redesign:ITS-04]: Phase 4 Task 9
- [review-workflow-redesign:ITS-05]: Phase 1 Task 1
- [review-workflow-redesign:ITS-06]: Phase 4 Task 9

### E2E Scenarios
- [review-workflow-redesign:E2E-01]: Phase 4 Task 9 (full workflow validated after all phases implemented)

---

## Appendix

### Glossary
- **Haiku**: Claude's fastest/cheapest model (`claude-haiku-4-5-20251001`), used for classification tasks (filtering, query generation, verification)
- **Sonnet**: Claude's balanced model (`claude-sonnet-4-5-20250929`), used for review generation (structure + report calls)
- **Evidence chunks**: Text segments stored in ChromaDB, retrieved by semantic search during analysis
- **Structure call**: First Sonnet call producing JSON review structure
- **Report call**: Second Sonnet call producing markdown report from structure
- **Verification pass**: Haiku call cross-referencing review output against evidence

### References
- Specification: `.sdd/review-workflow-redesign/specification.md`
- Current orchestrator: `src/agent/orchestrator.py`
- Current chunker: `src/mcp_servers/document_store/chunker.py`
- Current embeddings: `src/mcp_servers/document_store/embeddings.py`
- Embedding model: https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-13 | Claude | Initial design |
