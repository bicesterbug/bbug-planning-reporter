# Design: Key Documents Listing

**Version:** 1.0
**Date:** 2026-02-07
**Status:** Draft
**Linked Specification** `.sdd/key-documents/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The review pipeline runs in 5 phases inside `AgentOrchestrator`:

1. **FETCHING_METADATA** — calls `get_application_details` (scraper MCP), stores `ApplicationMetadata` with a `documents` list containing `{document_id, description, document_type, url, ...}` from the Cherwell portal.
2. **DOWNLOADING_DOCUMENTS** — calls `download_all_documents` (scraper MCP), which internally re-fetches the document list, applies `DocumentFilter`, downloads allowed documents, and returns download results with `file_path` and `document_id`.
3. **INGESTING_DOCUMENTS** — calls `ingest_document` for each downloaded PDF, producing chunks in ChromaDB. The document store's `list_ingested_documents` tool returns `{document_id, file_path, document_type, chunk_count, ...}` per document.
4. **ANALYSING_APPLICATION** — performs vector searches against ingested docs and policy, gathering evidence chunks.
5. **GENERATING_REVIEW** — sends evidence to Claude, receives markdown review, stores result as `ReviewResult.review` dict.

The review dict (`result.review`) is stored in Redis and returned by `GET /api/v1/reviews/{id}` via the `ReviewContent` Pydantic schema. Currently it contains: `overall_rating`, `summary`, `aspects`, `policy_compliance`, `recommendations`, `suggested_conditions`, `full_markdown`.

### Proposed Architecture

The feature adds a new data path through the existing pipeline:

1. **Phase 1 (FETCHING_METADATA)** — already fetches document list with `description`, `document_type`, and `url`. No change needed.
2. **Phase 2 (DOWNLOADING_DOCUMENTS)** — the orchestrator captures the downloaded document metadata (description, type, url) alongside file paths. Currently only `file_path` is retained in `DocumentIngestionResult`. This must be extended.
3. **Phase 3 (INGESTING_DOCUMENTS)** — after ingestion completes, the orchestrator builds a list of documents that were successfully ingested, matched against the metadata from Phase 2 to associate `description`, `document_type`, and `url`.
4. **Phase 4 (ANALYSING_APPLICATION)** — no changes.
5. **Phase 5 (GENERATING_REVIEW)** — the Claude prompt is extended with the ingested document list and asked to produce a `key_documents` JSON array in addition to the markdown. Each entry contains `title`, `category`, `summary`, and `url`. The LLM determines category assignment, ordering, and writes the 1-2 sentence summaries. The markdown report is updated to include the "Key Documents" section.

The `ReviewContent` schema gains a `key_documents` field (list of `KeyDocument` objects). The field is `None` for pre-existing reviews (backward compatible).

### Technology Decisions

- **LLM-driven categorisation and summarisation**: Rather than adding hardcoded category mapping logic, the Claude prompt instructs the model to assign categories and write summaries. This is simpler to implement and produces more contextual summaries. The category mapping rules from the spec (Transport & Access, Design & Layout, Application Core) are provided in the prompt as guidance.
- **Document metadata passed through orchestrator state**: The orchestrator already holds `ApplicationMetadata.documents` from Phase 1. The `download_all_documents` response includes `document_id` for each download. These are joined to produce the ingested document list with URLs. No new MCP tools needed.

### Quality Attributes

- **Backward compatibility**: `key_documents` defaults to `None` on `ReviewContent`. Existing clients unaffected.
- **Performance**: Summaries generated within the existing Phase 5 Claude call by extending the prompt. No additional LLM calls.

---

## API Design

The `ReviewContent` object gains a new optional field:

**`key_documents`** — array of `KeyDocument` objects, or `null` for reviews generated before this feature.

Each `KeyDocument` contains:
- `title` (string) — document title from the Cherwell portal
- `category` (string) — one of "Transport & Access", "Design & Layout", "Application Core"
- `summary` (string) — 1-2 sentence LLM-generated summary of the document's content and cycling relevance
- `url` (string | null) — direct PDF download URL from the Cherwell portal

Example response fragment:
```json
{
  "review": {
    "overall_rating": "amber",
    "key_documents": [
      {
        "title": "Transport Assessment (Part 1 of 7)",
        "category": "Transport & Access",
        "summary": "Analyses traffic impacts of the proposed logistics development including junction capacity modelling for the A41 access and cycle route provision.",
        "url": "https://planningregister.cherwell.gov.uk/Document/Download?..."
      }
    ],
    "full_markdown": "...",
    ...
  }
}
```

Error handling: If the LLM fails to produce valid `key_documents` JSON, the field is set to `null` and the review completes normally. This is a graceful degradation — the review itself remains valid.

---

## Modified Components

### AgentOrchestrator._phase_download_documents
**Change Description** Currently stores only `file_path` and `document_id` from download results. Must also capture `description`, `document_type`, and `url` for each successfully downloaded document, making this metadata available for Phase 5.

**Dependants** `_phase_generate_review` (reads the new metadata)

**Kind** Method

**Requirements References**
- [key-documents:FR-005]: Document selection depends on knowing which documents were successfully downloaded and ingested, with their original metadata preserved through the pipeline.

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Download metadata preserved | Download results contain document_id, description, document_type, url | Phase 2 completes | DocumentIngestionResult.document_metadata contains a dict mapping file_path to {description, document_type, url} |
| TS-02 | Failed downloads excluded from metadata | A document fails to download | Phase 2 completes | The failed document has no entry in document_metadata |

### DocumentIngestionResult
**Change Description** Currently tracks `documents_fetched`, `documents_ingested`, `failed_documents`, and `document_paths`. Must add a `document_metadata` field — a dict mapping `file_path` to `{description, document_type, url}` — so Phase 5 can look up source metadata for ingested documents.

**Dependants** `AgentOrchestrator._phase_download_documents`, `AgentOrchestrator._phase_generate_review`

**Kind** Dataclass

**Requirements References**
- [key-documents:FR-001]: The API response requires title and url per document, which must flow from scraper metadata through the pipeline.
- [key-documents:FR-005]: Only ingested documents should appear; the metadata dict allows filtering against `document_paths`.

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Metadata dict populated | 3 documents downloaded with metadata | DocumentIngestionResult created | document_metadata has 3 entries keyed by file_path |
| TS-02 | Metadata dict empty when no downloads | No documents downloaded | DocumentIngestionResult created | document_metadata is empty dict |

### AgentOrchestrator._phase_generate_review
**Change Description** Currently builds a Claude prompt from application summary and evidence chunks, then parses the markdown response to extract `overall_rating`. Must be extended to: (a) include the ingested document list in the prompt, instructing Claude to produce a `key_documents` JSON array with title, category, summary, and url; (b) parse the `key_documents` JSON from the response; (c) include it in `ReviewResult.review`; (d) instruct Claude to include a "Key Documents" section in the markdown.

**Dependants** `ReviewContent` schema (consumes the new field)

**Kind** Method

**Requirements References**
- [key-documents:FR-001]: The key_documents array is populated from the LLM response.
- [key-documents:FR-002]: The prompt specifies the three categories for the LLM to assign.
- [key-documents:FR-003]: The LLM generates summaries based on document content (evidence chunks).
- [key-documents:FR-004]: The prompt instructs Claude to include a "Key Documents" section in the markdown.
- [key-documents:FR-006]: The prompt instructs ordering by cycling relevance within categories.
- [key-documents:NFR-001]: Summaries are generated in the same Claude call, no extra latency.

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Key documents in review dict | Claude returns valid key_documents JSON | Phase 5 completes | ReviewResult.review contains "key_documents" list |
| TS-02 | Key documents markdown section | Claude returns markdown with Key Documents section | Phase 5 completes | full_markdown contains "## Key Documents" section after "## Application Summary" |
| TS-03 | Graceful fallback on parse failure | Claude response does not contain valid key_documents JSON | Phase 5 completes | ReviewResult.review["key_documents"] is None, review still succeeds |
| TS-04 | Document URLs passed through | Ingested document metadata includes urls | Phase 5 builds prompt | Prompt includes document urls for Claude to reproduce in output |

### ReviewContent (Pydantic schema)
**Change Description** Currently has fields: `overall_rating`, `summary`, `aspects`, `policy_compliance`, `recommendations`, `suggested_conditions`, `full_markdown`. Must add `key_documents: list[KeyDocument] | None = None`.

**Dependants** `ReviewResponse` (parent schema), API consumers

**Kind** Class (Pydantic model)

**Requirements References**
- [key-documents:FR-001]: The API response must include the key_documents array.
- [key-documents:NFR-002]: The field defaults to None, preserving backward compatibility.

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Key documents in API response | A completed review with key_documents data | GET /api/v1/reviews/{id} | Response includes review.key_documents array |
| TS-02 | Null for old reviews | A review completed before this feature | GET /api/v1/reviews/{id} | review.key_documents is null |
| TS-03 | Schema serialization | KeyDocument with all fields populated | Serialize to JSON | All fields present: title, category, summary, url |

---

## Added Components

### KeyDocument
**Description** Pydantic model representing a single key document in the API response. Contains `title` (str), `category` (str), `summary` (str), and `url` (str | None).

**Users** `ReviewContent` schema, API consumers

**Kind** Class (Pydantic model)

**Location** `src/api/schemas.py`

**Requirements References**
- [key-documents:FR-001]: Defines the structure of each element in the key_documents array.
- [key-documents:FR-002]: Category field holds one of the three defined categories.
- [key-documents:FR-003]: Summary field holds the LLM-generated description.

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Valid KeyDocument | title="Transport Assessment", category="Transport & Access", summary="...", url="https://..." | Create KeyDocument | All fields accessible, serializes to JSON correctly |
| TS-02 | KeyDocument with null url | url is None | Create KeyDocument | Serializes with url: null |

---

## Used Components

### DocumentFilter
**Location** `src/mcp_servers/cherwell_scraper/filters.py`

**Provides** Category pattern lists (`ALLOWLIST_CORE_PATTERNS`, `ALLOWLIST_ASSESSMENT_PATTERNS`, `ALLOWLIST_OFFICER_PATTERNS`) that define which document types map to which categories. These same patterns inform the category guidance in the LLM prompt.

**Used By** `AgentOrchestrator._phase_generate_review` (prompt construction references the same categories conceptually)

### MCPClientManager
**Location** `src/agent/mcp_client.py`

**Provides** `call_tool()` for calling `download_all_documents`, `list_ingested_documents`, and other MCP tools.

**Used By** `AgentOrchestrator` (all phases)

### Anthropic Client
**Location** `anthropic` package

**Provides** Claude API access for generating the review including key document summaries.

**Used By** `AgentOrchestrator._phase_generate_review`

---

## Documentation Considerations

- API reference documentation should be updated to describe the new `key_documents` field in the review response.
- No new README changes required — this is an enhancement to existing output.

---

## Instrumentation (if needed)

| Requirement | Observability Criteria | Implementation | Component |
|-------------|------------------------|----------------|-----------|
| [key-documents:NFR-001] | Review processing time increase ≤ 30s | Log total Phase 5 duration. Compare with baseline via existing `processing_time_seconds` in ReviewMetadata. | `AgentOrchestrator._phase_generate_review` |

---

## Integration Test Scenarios (if needed)

| ID | Scenario | Given | When | Then | Components Involved |
|----|----------|-------|------|------|---------------------|
| ITS-01 | Key documents flow through pipeline | A review is submitted for an application with transport and design documents | Review completes | ReviewResult.review contains key_documents with correct titles, categories, summaries, and urls | AgentOrchestrator, ReviewContent, KeyDocument |
| ITS-02 | Key documents in stored result | A review completes with key_documents | GET /api/v1/reviews/{id} | Response JSON includes review.key_documents array with expected structure | review_jobs, RedisClient, ReviewResponse |

---

## E2E Test Scenarios (if needed)

| ID | Scenario | Given | When | Then | User Journey |
|----|----------|-------|------|------|--------------|
| E2E-01 | Full review with key documents | API is running, application 25/00284/F exists | POST review, wait for completion, GET result | Response contains key_documents array with transport and design documents listed, full_markdown contains "## Key Documents" section | Submit → Poll status → Retrieve completed review → Verify key_documents present |

---

## Test Data

- Use existing test fixture for 25/00284/F which has transport assessments, design & access statement, site plans, and ES chapters.
- Mock Claude responses in unit tests with sample key_documents JSON embedded in the response.

---

## Test Feasibility

- Unit tests for schema changes and prompt construction can run without external dependencies.
- Integration tests require mocked MCP tools and mocked Claude API responses.
- E2E test requires the full Docker stack and a live Anthropic API key — manual verification only.

---

## Risks and Dependencies

- **Risk: LLM output format variability** — Claude may not consistently produce valid JSON for `key_documents`. Mitigation: parse with error handling and fall back to `null`. The prompt includes explicit JSON schema instructions.
- **Risk: Token budget** — Adding the document list to the prompt increases input tokens. For large applications (200+ documents), this could be significant. Mitigation: limit the document list passed to the prompt to the first 50 ingested documents, ordered by type relevance.
- **Dependency: Anthropic API** — No new dependency; already used in Phase 5.
- **Assumption: Document URLs remain valid** — Cherwell portal download URLs are assumed to remain accessible for the lifetime of the planning application. If the portal changes URL scheme, existing reviews' links will break. This is acceptable as the URLs are provided as-is from the source system.

---

## Feasability Review

- No large missing features or infrastructure. All changes build on existing components.
- The `download_all_documents` response already contains `document_id` per download, and `ApplicationMetadata.documents` already contains `{document_id, description, document_type, url}`. Joining these two datasets by `document_id` provides the full metadata needed.

---

## Task Breakdown

> **CRITICAL: Tests are written WITH implementation, not after.**

### Phase 1: Schema and Data Plumbing

- Task 1: Add KeyDocument schema and key_documents field to ReviewContent
  - Status: Done
  - Add `KeyDocument` Pydantic model to `src/api/schemas.py` with fields: title, category, summary, url
  - Add `key_documents: list[KeyDocument] | None = None` to `ReviewContent`
  - Requirements: [key-documents:FR-001], [key-documents:NFR-002]
  - Test Scenarios: [key-documents:KeyDocument/TS-01], [key-documents:KeyDocument/TS-02], [key-documents:ReviewContent/TS-01], [key-documents:ReviewContent/TS-02], [key-documents:ReviewContent/TS-03]

- Task 2: Extend DocumentIngestionResult with document_metadata
  - Status: Done
  - Add `document_metadata: dict[str, dict[str, Any]]` field to `DocumentIngestionResult` dataclass
  - Update `_phase_download_documents` to populate metadata from `ApplicationMetadata.documents` joined with download results by `document_id`
  - Requirements: [key-documents:FR-005]
  - Test Scenarios: [key-documents:AgentOrchestrator._phase_download_documents/TS-01], [key-documents:AgentOrchestrator._phase_download_documents/TS-02], [key-documents:DocumentIngestionResult/TS-01], [key-documents:DocumentIngestionResult/TS-02]

### Phase 2: LLM Integration and Markdown

- Task 3: Extend Phase 5 prompt to generate key_documents
  - Status: Done
  - Build ingested document list from `DocumentIngestionResult.document_metadata` joined with successfully ingested `document_paths`
  - Add document list to the Claude user prompt with instructions to produce a `key_documents` JSON array
  - Add category assignment rules (Transport & Access, Design & Layout, Application Core) to the system prompt
  - Add instructions for the "Key Documents" markdown section after "Application Summary"
  - Parse `key_documents` JSON from Claude response and include in `ReviewResult.review`
  - Handle parse failures gracefully (set to None)
  - Requirements: [key-documents:FR-001], [key-documents:FR-002], [key-documents:FR-003], [key-documents:FR-004], [key-documents:FR-006], [key-documents:NFR-001]
  - Test Scenarios: [key-documents:AgentOrchestrator._phase_generate_review/TS-01], [key-documents:AgentOrchestrator._phase_generate_review/TS-02], [key-documents:AgentOrchestrator._phase_generate_review/TS-03], [key-documents:AgentOrchestrator._phase_generate_review/TS-04], [key-documents:ITS-01], [key-documents:ITS-02]

---

## Intermediate Dead Code Tracking

| Phase Introduced | Description | Used In Phase | Status |
|------------------|-------------|---------------|--------|
| — | No dead code expected | — | — |

---

## Intermediate Stub Tracking

| Phase Introduced | Test Name | Reason for Stub | Implemented In Phase | Status |
|------------------|-----------|-----------------|----------------------|--------|
| — | No stubs expected | — | — | — |

---

## Requirements Validation

- [key-documents:FR-001]
  - Phase 1 Task 1 (KeyDocument schema, ReviewContent field)
  - Phase 2 Task 3 (LLM generates key_documents, included in review dict)
- [key-documents:FR-002]
  - Phase 2 Task 3 (category assignment rules in prompt)
- [key-documents:FR-003]
  - Phase 2 Task 3 (LLM generates summaries from document content)
- [key-documents:FR-004]
  - Phase 2 Task 3 (markdown section in prompt instructions)
- [key-documents:FR-005]
  - Phase 1 Task 2 (document metadata plumbing, ingested document selection)
- [key-documents:FR-006]
  - Phase 2 Task 3 (ordering instructions in prompt)
- [key-documents:NFR-001]
  - Phase 2 Task 3 (single Claude call, instrumentation via existing logging)
- [key-documents:NFR-002]
  - Phase 1 Task 1 (field defaults to None)

---

## Test Scenario Validation

### Component Scenarios
- [key-documents:KeyDocument/TS-01]: Phase 1 Task 1
- [key-documents:KeyDocument/TS-02]: Phase 1 Task 1
- [key-documents:ReviewContent/TS-01]: Phase 1 Task 1
- [key-documents:ReviewContent/TS-02]: Phase 1 Task 1
- [key-documents:ReviewContent/TS-03]: Phase 1 Task 1
- [key-documents:DocumentIngestionResult/TS-01]: Phase 1 Task 2
- [key-documents:DocumentIngestionResult/TS-02]: Phase 1 Task 2
- [key-documents:AgentOrchestrator._phase_download_documents/TS-01]: Phase 1 Task 2
- [key-documents:AgentOrchestrator._phase_download_documents/TS-02]: Phase 1 Task 2
- [key-documents:AgentOrchestrator._phase_generate_review/TS-01]: Phase 2 Task 3
- [key-documents:AgentOrchestrator._phase_generate_review/TS-02]: Phase 2 Task 3
- [key-documents:AgentOrchestrator._phase_generate_review/TS-03]: Phase 2 Task 3
- [key-documents:AgentOrchestrator._phase_generate_review/TS-04]: Phase 2 Task 3

### Integration Scenarios
- [key-documents:ITS-01]: Phase 2 Task 3
- [key-documents:ITS-02]: Phase 2 Task 3

### E2E Scenarios
- [key-documents:E2E-01]: Manual verification

---

## Appendix

### Glossary
- **Key document**: An ingested application document selected for prominent listing in the report.
- **Document metadata**: The `{description, document_type, url}` tuple from the Cherwell scraper for each document.
- **Category**: One of "Transport & Access", "Design & Layout", or "Application Core".

### References
- [key-documents specification](.sdd/key-documents/specification.md)
- [document-filtering specification](.sdd/document-filtering/specification.md)
- [agent-integration design](.sdd/agent-integration/design.md)

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-07 | BBUG | Initial design |

---
