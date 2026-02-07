# Design: Review Scope Control

**Version:** 1.0
**Date:** 2026-02-07
**Status:** Implemented
**Linked Specification** `.sdd/review-scope-control/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The review pipeline flows as: API request → Redis queue → worker → AgentOrchestrator → MCP tool calls → Claude LLM.

Document filtering happens inside the `cherwell-scraper-mcp` server. When `download_all_documents` is called, a `DocumentFilter` applies allowlist/denylist pattern matching to decide which documents to download. Currently:

1. The filter checks an **allowlist** first (core docs, transport assessments, officer reports)
2. Then checks a **denylist** (public comments, non-transport technical docs)
3. Defaults to **allow** for unknown types

**Bug identified during testing:** Consultation responses from statutory consultees (e.g. "Consultation Response - OCC Highways") match the allowlist pattern "highway" in `ALLOWLIST_ASSESSMENT_PATTERNS` before the denylist check for "consultation response" fires. This means consultation responses are being downloaded and reviewed despite being on the denylist.

Additionally, there is an **options flow gap**: `ReviewOptionsRequest` fields are stored in the `ReviewJob` in Redis, but `process_review` in the worker does not read or pass these options to the `AgentOrchestrator`, and the orchestrator does not pass any options to the `download_all_documents` MCP tool call.

### Proposed Architecture

Two changes are required:

1. **Fix filter priority** — Consultation response and public comment patterns are checked BEFORE the allowlist, giving them higher priority. This prevents allowlist patterns (like "highway") from accidentally matching consultation response descriptions.

2. **Add toggle parameters** — Two new boolean parameters (`include_consultation_responses`, `include_public_comments`) flow from the API through the entire pipeline:

```
API (ReviewOptionsRequest)
  → Redis (ReviewOptions on ReviewJob)
    → Worker (process_review reads options)
      → Orchestrator (accepts options, passes to tool call)
        → MCP Tool (DownloadAllDocumentsInput)
          → DocumentFilter (filter_documents with toggle params)
```

When a toggle is `true`, the corresponding denylist category is skipped entirely for that filter invocation, allowing those documents through.

### Technology Decisions

- No new dependencies required — all changes are to existing Pydantic models, dataclasses, and function signatures
- The `DENYLIST_PUBLIC_COMMENT_PATTERNS` list is split into two separate class attributes to enable independent toggling
- Filter priority change is a bug fix that applies regardless of the toggles

### Quality Attributes

- **Backward compatibility**: Both new fields default to `false`, so existing clients see no behaviour change
- **Maintainability**: Pattern lists are separated by category with clear naming, making it easy to add/remove patterns per category

---

## API Design

### ReviewOptionsRequest (API Schema)

Two new optional boolean fields are added to the `ReviewOptionsRequest` model:

- `include_consultation_responses` (boolean, default `false`) — When true, documents matching consultation response patterns are downloaded and included as LLM evidence
- `include_public_comments` (boolean, default `false`) — When true, documents matching public comment patterns are downloaded and included as LLM evidence

### DownloadAllDocumentsInput (MCP Tool Schema)

Two new optional boolean fields are added to the `DownloadAllDocumentsInput` model with the same names and defaults. These are passed as arguments in the `download_all_documents` MCP tool call.

### DocumentFilter.filter_documents

Two new optional boolean parameters are added to the `filter_documents` method signature:

- `include_consultation_responses` (bool, default `False`)
- `include_public_comments` (bool, default `False`)

### Error Handling

No new error conditions. Invalid combinations (e.g. `skip_filter=true` with toggles) are handled by `skip_filter` taking precedence — toggles are simply ignored.

---

## Modified Components

### DocumentFilter
**Change Description** Currently has a single `DENYLIST_PUBLIC_COMMENT_PATTERNS` list containing both consultation response and public comment patterns. The `_should_download` method checks the allowlist before the denylist, causing consultation responses with allowlist-matching words (e.g. "highway") to bypass the denylist.

Changes:
1. Split `DENYLIST_PUBLIC_COMMENT_PATTERNS` into `DENYLIST_CONSULTATION_RESPONSE_PATTERNS` and `DENYLIST_PUBLIC_COMMENT_PATTERNS`
2. Change `_should_download` priority: check consultation response and public comment denylists BEFORE the allowlist
3. Add `include_consultation_responses` and `include_public_comments` parameters to `filter_documents` which are forwarded to `_should_download`
4. When a toggle is `true`, skip the corresponding denylist check

**Dependants** `_download_all_documents` in `server.py` (passes new params)

**Kind** Class

**Requirements References**
- [review-scope-control:FR-001]: The `include_consultation_responses` parameter controls whether consultation response patterns are skipped in the denylist check
- [review-scope-control:FR-002]: The `include_public_comments` parameter controls whether public comment patterns are skipped in the denylist check
- [review-scope-control:FR-003]: The filter accepts per-review flags and selectively bypasses denylist categories; `skip_filter` still takes full precedence
- [review-scope-control:NFR-001]: Both parameters default to `false`, preserving existing behaviour
- [review-scope-control:NFR-002]: Checking two extra booleans adds negligible overhead

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| DocumentFilter/TS-01 | Consultation response blocked by default despite allowlist match | A document with description "Consultation Response - OCC Highways" and both toggles at default (false) | `filter_documents` is called | Document is filtered out with reason indicating consultation response, NOT allowed through via "highway" allowlist match |
| DocumentFilter/TS-02 | Consultation response allowed when toggle enabled | A document with description "Consultation Response - OCC Highways" and `include_consultation_responses=True` | `filter_documents` is called | Document is allowed through (consultation response denylist is skipped) |
| DocumentFilter/TS-03 | Public comment blocked by default | A document with type "letter from resident" and both toggles at default (false) | `filter_documents` is called | Document is filtered out with reason indicating public comment |
| DocumentFilter/TS-04 | Public comment allowed when toggle enabled | A document with type "letter from resident" and `include_public_comments=True` | `filter_documents` is called | Document is allowed through |
| DocumentFilter/TS-05 | Both toggles enabled simultaneously | Documents including both consultation responses and public comments, with both toggles true | `filter_documents` is called | Both document types are allowed through |
| DocumentFilter/TS-06 | skip_filter overrides toggles | Documents with `skip_filter=True` and both toggles false | `filter_documents` is called | All documents are allowed through (skip_filter takes precedence) |
| DocumentFilter/TS-07 | Core documents unaffected by toggles | A Transport Assessment document with toggles at default | `filter_documents` is called | Document is allowed through via allowlist as before |
| DocumentFilter/TS-08 | Non-transport document unaffected by toggles | An "ecology" document with toggles at default | `filter_documents` is called | Document is filtered out via non-transport denylist as before |
| DocumentFilter/TS-09 | Consultation response pattern matching is case insensitive | A document with description "CONSULTATION RESPONSE - Environment Agency" | `filter_documents` is called with defaults | Document is filtered out |
| DocumentFilter/TS-10 | Public comment only toggle does not affect consultation responses | A consultation response document with `include_public_comments=True` but `include_consultation_responses=False` | `filter_documents` is called | Consultation response is still filtered out |

### DownloadAllDocumentsInput
**Change Description** Currently accepts `application_ref`, `output_dir`, and `skip_filter`. Needs two additional boolean fields for the toggle parameters.

**Dependants** None (consumed by `_download_all_documents`)

**Kind** Class (Pydantic BaseModel)

**Requirements References**
- [review-scope-control:FR-004]: The toggle values must flow through the MCP tool input to the filter

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| DownloadAllDocumentsInput/TS-01 | Defaults both toggles to false | No toggle values provided in input | Model is instantiated with only required fields | `include_consultation_responses` and `include_public_comments` are both `False` |
| DownloadAllDocumentsInput/TS-02 | Accepts both toggles as true | Input includes both toggles set to true | Model is instantiated | Both fields are `True` |

### _download_all_documents (server.py)
**Change Description** Currently creates a `DocumentFilter` and calls `filter_documents(docs, skip_filter=input.skip_filter, application_ref=input.application_ref)`. Needs to also pass the two toggle parameters from the input to `filter_documents`.

**Dependants** None

**Kind** Method (on `CherwellScraperMCP`)

**Requirements References**
- [review-scope-control:FR-004]: Passes toggle values from MCP tool input to DocumentFilter

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| _download_all_documents/TS-01 | Passes toggle params to filter | Tool called with `include_consultation_responses=True` | `_download_all_documents` executes | `filter_documents` is called with `include_consultation_responses=True` |

### ReviewOptionsRequest (schemas.py)
**Change Description** Currently has `focus_areas`, `output_format`, `include_policy_matrix`, `include_suggested_conditions`. Needs two new boolean fields for the toggles.

**Dependants** `submit_review` in `routes/reviews.py` (reads new fields), `ReviewOptions` in `models.py` (internal model mirrors fields)

**Kind** Class (Pydantic BaseModel)

**Requirements References**
- [review-scope-control:FR-001]: API parameter for consultation responses
- [review-scope-control:FR-002]: API parameter for public comments
- [review-scope-control:NFR-001]: Both default to false for backward compatibility

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| ReviewOptionsRequest/TS-01 | Defaults both toggles to false | No toggle fields in request JSON | `ReviewOptionsRequest` is parsed | Both fields are `False` |
| ReviewOptionsRequest/TS-02 | Accepts explicit true values | Request JSON includes both toggles as true | `ReviewOptionsRequest` is parsed | Both fields are `True` |
| ReviewOptionsRequest/TS-03 | Existing fields unaffected | Request with only existing fields (focus_areas, etc.) | `ReviewOptionsRequest` is parsed | Existing fields parsed correctly, new fields default to false |

### ReviewOptions (models.py)
**Change Description** Internal model that mirrors `ReviewOptionsRequest`. Currently has `focus_areas`, `output_format`, `include_policy_matrix`, `include_suggested_conditions`. Needs the same two new boolean fields.

**Dependants** `AgentOrchestrator` (reads options), `process_review` (passes options)

**Kind** Class (Pydantic BaseModel)

**Requirements References**
- [review-scope-control:FR-004]: Options must flow from API to worker to orchestrator

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| ReviewOptions/TS-01 | Defaults both toggles to false | `ReviewOptions` created with no toggle fields | Model is instantiated | Both fields are `False` |

### submit_review (routes/reviews.py)
**Change Description** Currently converts `ReviewOptionsRequest` to `ReviewOptions` but only maps the four existing fields. Must also map the two new toggle fields.

**Dependants** None

**Kind** Function

**Requirements References**
- [review-scope-control:FR-004]: Options flow from API request to internal model

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| submit_review/TS-01 | New toggle fields mapped to internal model | API request with `include_consultation_responses=True` | `submit_review` is called | The `ReviewOptions` stored in Redis has `include_consultation_responses=True` |

### AgentOrchestrator (orchestrator.py)
**Change Description** Currently `__init__` does not accept options. `_phase_download_documents` hardcodes `download_all_documents` without toggle parameters. Changes:
1. Add `options: ReviewOptions | None = None` parameter to `__init__`
2. In `_phase_download_documents`, read toggle values from `self._options` and include them in the `download_all_documents` tool arguments

**Dependants** None

**Kind** Class

**Requirements References**
- [review-scope-control:FR-004]: Orchestrator passes toggle flags to MCP tool call
- [review-scope-control:FR-005]: Consultation response documents are not listed in key_documents (they flow through as evidence only — no changes needed to existing key_documents logic since the LLM is only instructed to list ingested application documents, and the filter categories don't affect key document selection)

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| AgentOrchestrator/TS-01 | Passes toggle flags in download tool call | Orchestrator created with options having `include_consultation_responses=True` | `_phase_download_documents` executes | `download_all_documents` MCP tool is called with `include_consultation_responses: true` in arguments |
| AgentOrchestrator/TS-02 | Default options omit toggle flags | Orchestrator created with no options | `_phase_download_documents` executes | `download_all_documents` MCP tool is called without toggle flags (or with both as false) |

### process_review (review_jobs.py)
**Change Description** Currently creates `AgentOrchestrator(review_id, application_ref, redis_client=redis_client)` without reading options from the Redis job. Must read the `ReviewJob` from Redis, extract options, and pass them to the orchestrator.

**Dependants** None

**Kind** Function

**Requirements References**
- [review-scope-control:FR-004]: Options flow from Redis job through worker to orchestrator

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| process_review/TS-01 | Reads options from Redis job and passes to orchestrator | A ReviewJob in Redis with `options.include_consultation_responses=True` | `process_review` is called | `AgentOrchestrator` is created with the options from the job |
| process_review/TS-02 | Works without options | A ReviewJob in Redis with no options | `process_review` is called | `AgentOrchestrator` is created with `options=None` (default behaviour) |

---

## Added Components

None — all changes modify existing components.

---

## Used Components

### RedisClient
**Location** `src/shared/redis_client.py`

**Provides** `get_job(review_id)` returns a `ReviewJob` which includes `options: ReviewOptions | None`. Already stores and retrieves the full `ReviewJob` including options.

**Used By** `process_review` (to read the ReviewJob and extract options)

### MCPClientManager
**Location** `src/agent/mcp_client.py`

**Provides** `call_tool(name, arguments, timeout)` for invoking MCP server tools. Already supports arbitrary dict arguments.

**Used By** `AgentOrchestrator._phase_download_documents` (to call `download_all_documents` with new toggle arguments)

---

## Documentation Considerations

- The API documentation (if/when generated from OpenAPI) will automatically include the new fields from the Pydantic schema changes
- No separate documentation updates required — the field descriptions on the Pydantic models serve as documentation

---

## Instrumentation (if needed)

N/A — No NFRs require observability-based verification. The existing filter logging (structlog in `filter_documents`) already logs every filter decision including the reason, which will naturally include the new toggle-related decisions.

---

## Integration Test Scenarios (if needed)

| ID | Scenario | Given | When | Then | Components Involved |
|----|----------|-------|------|------|---------------------|
| ITS-01 | Toggle flows from API to filter | An API request with `options.include_consultation_responses: true` | The request is processed through submit_review, stored in Redis, read by worker, passed to orchestrator, and forwarded to MCP tool | The `filter_documents` call receives `include_consultation_responses=True` | ReviewOptionsRequest, ReviewOptions, submit_review, process_review, AgentOrchestrator, DownloadAllDocumentsInput, DocumentFilter |
| ITS-02 | Default request does not pass toggles | An API request with no options | The request flows through the full pipeline | The `filter_documents` call receives default values (both toggles false), and consultation responses are blocked | ReviewOptionsRequest, ReviewOptions, submit_review, process_review, AgentOrchestrator, DownloadAllDocumentsInput, DocumentFilter |

---

## E2E Test Scenarios (if needed)

N/A — Full E2E testing requires a running Cherwell portal and Docker stack. The integration test scenarios above verify the complete options flow through mocked components. Manual verification is described in Test Feasibility.

---

## Test Data

- Existing `DocumentInfo` fixture data can be extended with consultation response and public comment documents
- Sample document descriptions: "Consultation Response - OCC Highways", "Consultation Response - Environment Agency", "Consultee Response - Parish Council", "Letter from Resident - J Smith", "Public Comment - A Jones", "Letter of Objection"
- Existing test fixtures for orchestrator (`sample_download_response`) can be reused with modifications

---

## Test Feasibility

- All component and integration tests can be implemented with the existing test infrastructure (pytest, unittest.mock, fakeredis)
- No missing infrastructure
- No external dependencies needed for testing — the MCP tool calls and Redis interactions can be mocked

---

## Risks and Dependencies

- **Risk: Pattern split accuracy** — Splitting the denylist into two categories requires careful categorisation. Mitigation: The spec explicitly defines which patterns belong to each category.
- **Risk: Breaking existing filter behaviour** — Changing the filter priority (denylist before allowlist for consultation/public comment patterns) could theoretically affect non-consultation documents. Mitigation: Only consultation response and public comment patterns are elevated in priority; the allowlist still applies for all other document types.
- **Dependency: Redis job already stores options** — The `RedisClient.store_job` and `get_job` methods already serialize/deserialize the full `ReviewJob` including `ReviewOptions`. Adding new fields to `ReviewOptions` is backward-compatible (Pydantic defaults handle missing fields in existing Redis data).

---

## Feasability Review

No blockers — all required infrastructure exists and changes are additive.

---

## Task Breakdown

> **CRITICAL: Tests are written WITH implementation, not after.**

### Phase 1: Fix filter priority and add toggle parameters

- Task 1: Split denylist and fix filter priority in DocumentFilter
  - Status: Done
  - Split `DENYLIST_PUBLIC_COMMENT_PATTERNS` into `DENYLIST_CONSULTATION_RESPONSE_PATTERNS` (patterns: "consultation response", "consultee response", "statutory consultee") and `DENYLIST_PUBLIC_COMMENT_PATTERNS` (patterns: "public comment", "comment from", "objection", "representation from", "letter from resident", "letter from neighbour", "letter of objection", "letter of support", "petition")
  - Add `include_consultation_responses: bool = False` and `include_public_comments: bool = False` parameters to `filter_documents` and `_should_download`
  - Change `_should_download` priority: check consultation response patterns, then public comment patterns, THEN allowlist
  - When a toggle is `True`, skip the corresponding denylist check
  - Write tests for all DocumentFilter scenarios
  - Requirements: [review-scope-control:FR-001], [review-scope-control:FR-002], [review-scope-control:FR-003], [review-scope-control:NFR-001], [review-scope-control:NFR-002]
  - Test Scenarios: [review-scope-control:DocumentFilter/TS-01], [review-scope-control:DocumentFilter/TS-02], [review-scope-control:DocumentFilter/TS-03], [review-scope-control:DocumentFilter/TS-04], [review-scope-control:DocumentFilter/TS-05], [review-scope-control:DocumentFilter/TS-06], [review-scope-control:DocumentFilter/TS-07], [review-scope-control:DocumentFilter/TS-08], [review-scope-control:DocumentFilter/TS-09], [review-scope-control:DocumentFilter/TS-10]

- Task 2: Add toggle fields to MCP tool input and server
  - Status: Done
  - Add `include_consultation_responses` and `include_public_comments` boolean fields (default `False`) to `DownloadAllDocumentsInput`
  - Update `_download_all_documents` to pass the new fields to `document_filter.filter_documents()`
  - Write tests for tool input defaults and parameter passthrough
  - Requirements: [review-scope-control:FR-004]
  - Test Scenarios: [review-scope-control:DownloadAllDocumentsInput/TS-01], [review-scope-control:DownloadAllDocumentsInput/TS-02], [review-scope-control:_download_all_documents/TS-01]

### Phase 2: Wire options through the full pipeline

- Task 3: Add toggle fields to API schema and internal model
  - Status: Done
  - Add `include_consultation_responses: bool = False` and `include_public_comments: bool = False` to `ReviewOptionsRequest` in `schemas.py`
  - Add the same fields to `ReviewOptions` in `models.py`
  - Update `submit_review` in `routes/reviews.py` to map the new fields from request to internal model
  - Write tests for schema defaults and field mapping
  - Requirements: [review-scope-control:FR-001], [review-scope-control:FR-002], [review-scope-control:NFR-001]
  - Test Scenarios: [review-scope-control:ReviewOptionsRequest/TS-01], [review-scope-control:ReviewOptionsRequest/TS-02], [review-scope-control:ReviewOptionsRequest/TS-03], [review-scope-control:ReviewOptions/TS-01], [review-scope-control:submit_review/TS-01]

- Task 4: Wire options from worker through orchestrator to MCP tool call
  - Status: Done
  - Update `AgentOrchestrator.__init__` to accept `options: ReviewOptions | None = None` and store as `self._options`
  - Update `_phase_download_documents` to read toggle values from `self._options` and include them in the `download_all_documents` tool arguments dict
  - Update `process_review` in `review_jobs.py` to read the `ReviewJob` from Redis (via `redis_wrapper.get_job(review_id)`), extract options, and pass them to `AgentOrchestrator`
  - Write tests for orchestrator tool call arguments and worker options passthrough
  - Requirements: [review-scope-control:FR-004], [review-scope-control:FR-005]
  - Test Scenarios: [review-scope-control:AgentOrchestrator/TS-01], [review-scope-control:AgentOrchestrator/TS-02], [review-scope-control:process_review/TS-01], [review-scope-control:process_review/TS-02], [review-scope-control:ITS-01], [review-scope-control:ITS-02]

---

## Intermediate Dead Code Tracking

| Phase Introduced | Description | Used In Phase | Status |
|------------------|-------------|---------------|--------|
| N/A | No dead code expected | N/A | N/A |

---

## Intermediate Stub Tracking

| Phase Introduced | Test Name | Reason for Stub | Implemented In Phase | Status |
|------------------|-----------|-----------------|----------------------|--------|
| N/A | No stubs expected | N/A | N/A | N/A |

---

## Requirements Validation

- [review-scope-control:FR-001]
  - Phase 1 Task 1 (filter accepts and honours `include_consultation_responses`)
  - Phase 2 Task 3 (API schema field)
- [review-scope-control:FR-002]
  - Phase 1 Task 1 (filter accepts and honours `include_public_comments`)
  - Phase 2 Task 3 (API schema field)
- [review-scope-control:FR-003]
  - Phase 1 Task 1 (filter override mechanism with per-review flags and skip_filter precedence)
- [review-scope-control:FR-004]
  - Phase 1 Task 2 (MCP tool input fields)
  - Phase 2 Task 3 (API schema and internal model fields)
  - Phase 2 Task 4 (worker and orchestrator passthrough)
- [review-scope-control:FR-005]
  - Phase 2 Task 4 (consultation/public comment documents flow as evidence only — existing key_documents logic is unaffected because it is driven by the LLM prompt which instructs it to list only ingested application documents)

- [review-scope-control:NFR-001]
  - Phase 1 Task 1 (filter defaults)
  - Phase 2 Task 3 (API schema defaults)
- [review-scope-control:NFR-002]
  - Phase 1 Task 1 (negligible overhead from boolean checks)

---

## Test Scenario Validation

### Component Scenarios
- [review-scope-control:DocumentFilter/TS-01]: Phase 1 Task 1
- [review-scope-control:DocumentFilter/TS-02]: Phase 1 Task 1
- [review-scope-control:DocumentFilter/TS-03]: Phase 1 Task 1
- [review-scope-control:DocumentFilter/TS-04]: Phase 1 Task 1
- [review-scope-control:DocumentFilter/TS-05]: Phase 1 Task 1
- [review-scope-control:DocumentFilter/TS-06]: Phase 1 Task 1
- [review-scope-control:DocumentFilter/TS-07]: Phase 1 Task 1
- [review-scope-control:DocumentFilter/TS-08]: Phase 1 Task 1
- [review-scope-control:DocumentFilter/TS-09]: Phase 1 Task 1
- [review-scope-control:DocumentFilter/TS-10]: Phase 1 Task 1
- [review-scope-control:DownloadAllDocumentsInput/TS-01]: Phase 1 Task 2
- [review-scope-control:DownloadAllDocumentsInput/TS-02]: Phase 1 Task 2
- [review-scope-control:_download_all_documents/TS-01]: Phase 1 Task 2
- [review-scope-control:ReviewOptionsRequest/TS-01]: Phase 2 Task 3
- [review-scope-control:ReviewOptionsRequest/TS-02]: Phase 2 Task 3
- [review-scope-control:ReviewOptionsRequest/TS-03]: Phase 2 Task 3
- [review-scope-control:ReviewOptions/TS-01]: Phase 2 Task 3
- [review-scope-control:submit_review/TS-01]: Phase 2 Task 3
- [review-scope-control:AgentOrchestrator/TS-01]: Phase 2 Task 4
- [review-scope-control:AgentOrchestrator/TS-02]: Phase 2 Task 4
- [review-scope-control:process_review/TS-01]: Phase 2 Task 4
- [review-scope-control:process_review/TS-02]: Phase 2 Task 4

### Integration Scenarios
- [review-scope-control:ITS-01]: Phase 2 Task 4
- [review-scope-control:ITS-02]: Phase 2 Task 4

### E2E Scenarios
N/A — manual verification with running stack.

---

## Appendix

### Glossary
- **Consultation response**: A document submitted by a statutory consultee (Highway Authority, Environment Agency, parish council) in response to the planning application
- **Public comment**: A document submitted by a member of the public expressing support, objection, or comment
- **Denylist**: Pattern list in DocumentFilter that causes matching documents to be excluded from download
- **Allowlist**: Pattern list in DocumentFilter that causes matching documents to be explicitly included

### References
- [review-scope-control specification](.sdd/review-scope-control/specification.md)
- [document-filtering specification](.sdd/document-filtering/specification.md)
- [document-filtering design](.sdd/document-filtering/design.md)
- [agent-integration specification](.sdd/agent-integration/specification.md)

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-07 | BBUG | Initial design |
