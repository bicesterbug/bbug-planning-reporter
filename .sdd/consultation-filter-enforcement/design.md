# Design: Consultation Filter Enforcement

**Version:** 1.0
**Date:** 2026-02-15
**Status:** Draft
**Linked Specification** `.sdd/consultation-filter-enforcement/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The review pipeline has two filtering paths that were designed independently:

1. **Programmatic filter** (`DocumentFilter` in `filters.py`): Used by `download_all_documents` in the Cherwell scraper MCP. Has comprehensive category and title-pattern denylists for consultation responses and public comments. Respects `include_consultation_responses` / `include_public_comments` toggles.

2. **LLM filter** (`_phase_filter_documents` in `orchestrator.py`): Introduced by `review-workflow-redesign`. Asks Haiku to classify documents. The prompt instructs it to exclude "Consultation responses and public comments" but the LLM ignores this for transport-relevant consultee responses (e.g. OCC Highways). There is no programmatic enforcement after the LLM returns its selection.

The `review-scope-control` feature added API toggles (`include_consultation_responses`, `include_public_comments`) that flow through to the orchestrator's `self._options`, but they are only read when calling `download_all_documents` — which the current pipeline no longer uses (it uses LLM filter + individual downloads instead).

### Proposed Architecture

Add a programmatic post-filter step in `_phase_filter_documents()` that runs immediately after the LLM selection, before setting `self._selected_documents`. The post-filter:

1. Iterates the LLM-selected documents
2. For each document, checks if it matches consultation response or public comment patterns
3. Removes matching documents unless the corresponding toggle is enabled
4. Logs each removal

```
list_application_documents (MCP)
        ↓
LLM filter (Haiku selects relevant docs)
        ↓
  ┌─── POST-FILTER (new) ───┐
  │ For each selected doc:   │
  │  1. Category denylist    │
  │  2. Title pattern match  │
  │  3. Check toggle flags   │
  │  4. Remove or keep       │
  └──────────────────────────┘
        ↓
self._selected_documents (filtered list)
        ↓
download_document (individual, per doc)
```

### Technology Decisions

- Reuse the existing pattern constants from `DocumentFilter` by importing them, rather than duplicating. This keeps the deny patterns in a single source of truth.
- Implement as a private method `_post_filter_consultation_documents()` on the orchestrator, keeping the logic co-located with `_phase_filter_documents()`.

### Quality Attributes

- **Maintainability:** Single source of truth for deny patterns (imported from `filters.py`).
- **Reliability:** Programmatic enforcement cannot be bypassed by LLM reasoning, unlike prompt-based instructions.

---

## API Design

N/A — No public interface changes. The existing API toggles (`include_consultation_responses`, `include_public_comments`) now take effect in the LLM filter path as well.

---

## Modified Components

### `_phase_filter_documents` method
**Change Description:** Currently sets `self._selected_documents` directly from the LLM-selected IDs matched against the full document list. Must be modified to pass the matched list through `_post_filter_consultation_documents()` before assignment.

**Dependants:** None — `self._selected_documents` is consumed downstream by `_phase_download_documents` unchanged.

**Kind:** Method (on `AgentOrchestrator` class)

**Requirements References**
- [consultation-filter-enforcement:FR-001]: The post-filter call must be inserted after LLM selection and before `self._selected_documents` assignment.

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Consultation response removed by default | LLM selects a doc with `document_type="Consultation Responses"` and toggle is `false` (default) | `_phase_filter_documents` completes | Document is not in `self._selected_documents` |
| TS-02 | Consultation response kept when toggle enabled | LLM selects a doc with `document_type="Consultation Responses"` and `include_consultation_responses=true` | `_phase_filter_documents` completes | Document is in `self._selected_documents` |
| TS-03 | Public comment removed by default | LLM selects a doc with `document_type="Public Comments"` and toggle is `false` (default) | `_phase_filter_documents` completes | Document is not in `self._selected_documents` |
| TS-04 | Public comment kept when toggle enabled | LLM selects a doc with `document_type="Public Comments"` and `include_public_comments=true` | `_phase_filter_documents` completes | Document is in `self._selected_documents` |
| TS-05 | Non-consultation docs unaffected | LLM selects docs with `document_type="Supporting Documents"` | `_phase_filter_documents` completes | All documents remain in `self._selected_documents` |

---

## Added Components

### `_post_filter_consultation_documents` method
**Description:** Takes the list of LLM-selected documents and removes any that match consultation response or public comment patterns, unless the corresponding toggle is enabled. Returns the filtered list.

**Users:** Called by `_phase_filter_documents()` in the orchestrator.

**Kind:** Private method on `AgentOrchestrator` class

**Location:** `src/agent/orchestrator.py`

**Requirements References**
- [consultation-filter-enforcement:FR-001]: Core post-filter logic
- [consultation-filter-enforcement:FR-002]: Uses existing `DocumentFilter` patterns for classification
- [consultation-filter-enforcement:FR-003]: Logs each removed document
- [consultation-filter-enforcement:NFR-001]: Respects toggle flags
- [consultation-filter-enforcement:NFR-002]: In-memory filtering, negligible overhead

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Category denylist match (consultation) | Document with `document_type="Consultation Responses"`, toggle `false` | `_post_filter_consultation_documents` called | Document removed, log emitted |
| TS-02 | Category denylist match (consultee variant) | Document with `document_type="Consultee Responses"`, toggle `false` | `_post_filter_consultation_documents` called | Document removed |
| TS-03 | Category denylist match (public comments) | Document with `document_type="Public Comments"`, toggle `false` | `_post_filter_consultation_documents` called | Document removed |
| TS-04 | Title pattern match (consultation) | Document with `document_type=None`, `description="Statutory Consultee Response - OCC"`, toggle `false` | `_post_filter_consultation_documents` called | Document removed |
| TS-05 | Title pattern match (public comment) | Document with `document_type=None`, `description="Letter from Resident - J Smith"`, toggle `false` | `_post_filter_consultation_documents` called | Document removed |
| TS-06 | Toggle overrides category denylist | Document with `document_type="Consultation Responses"`, `include_consultation_responses=true` | `_post_filter_consultation_documents` called | Document kept |
| TS-07 | Toggle overrides title pattern | Document with `description="Consultation Response - OCC"`, `include_consultation_responses=true` | `_post_filter_consultation_documents` called | Document kept |
| TS-08 | Non-matching documents pass through | Document with `document_type="Supporting Documents"`, `description="Transport Assessment"` | `_post_filter_consultation_documents` called | Document kept |
| TS-09 | Case-insensitive matching | Document with `document_type="CONSULTATION RESPONSES"` | `_post_filter_consultation_documents` called | Document removed |
| TS-10 | Mixed list filtering | List with 3 transport docs + 1 consultation response, toggle `false` | `_post_filter_consultation_documents` called | Returns 3 docs, logs 1 removal |

---

## Used Components

### `DocumentFilter` class
**Location:** `src/mcp_servers/cherwell_scraper/filters.py`

**Provides:** Category denylist constants (`CATEGORY_DENYLIST_CONSULTATION`, `CATEGORY_DENYLIST_PUBLIC`) and title pattern constants (`DENYLIST_CONSULTATION_RESPONSE_PATTERNS`, `DENYLIST_PUBLIC_COMMENT_PATTERNS`).

**Used By:** `_post_filter_consultation_documents` imports these constants for pattern matching.

---

## Documentation Considerations

- None required. The fix is internal to the orchestrator.

---

## Instrumentation (if needed)

| Requirement | Observability Criteria | Implementation | Component |
|-------------|------------------------|----------------|-----------|
| [consultation-filter-enforcement:FR-003] | Each removed document must be logged | `logger.warning("Document removed by post-filter", ...)` with doc description, type, and reason | `_post_filter_consultation_documents` |

---

## Integration Test Scenarios (if needed)

N/A — The post-filter is a pure in-memory function operating on dict data. It does not interact with external services. The component test scenarios above provide sufficient coverage.

---

## E2E Test Scenarios (if needed)

N/A — E2E verification will be done by rerunning a production review after deployment and confirming consultation responses are no longer in the output.

---

## Test Data

- Use document dicts matching the format returned by `list_application_documents`: `{"document_id": "...", "description": "...", "document_type": "...", "date_published": "..."}`.
- Test documents should include examples from the real production failures: "Oxfordshire County Council's Consultation Response" with `document_type="Consultation Responses"`, and "Active Travel England Standing Advice Response".

---

## Test Feasibility

- No missing infrastructure. All tests are unit tests using dict fixtures.

---

## Risks and Dependencies

- **Risk:** Future Cherwell portal section header variants not in the denylist. **Mitigation:** The title-pattern fallback (tier 2) catches documents even without a recognised category header.
- **Dependency:** Importing constants from `DocumentFilter` creates a coupling between orchestrator and scraper. **Mitigation:** The constants are stable class-level attributes that have not changed since initial implementation.

---

## Feasability Review

- No blockers. All required components exist.

---

## Task Breakdown

### Phase 1: Implement post-filter and update filter phase

- Task 1: Add `_post_filter_consultation_documents` method and call it from `_phase_filter_documents`
  - Status: Backlog
  - Add a private method that iterates documents, checks category denylists and title patterns, removes matches unless toggle enabled, logs removals. Insert call in `_phase_filter_documents` between LLM selection matching and `self._selected_documents` assignment. Write tests for all component scenarios.
  - Requirements: [consultation-filter-enforcement:FR-001], [consultation-filter-enforcement:FR-002], [consultation-filter-enforcement:FR-003], [consultation-filter-enforcement:NFR-001], [consultation-filter-enforcement:NFR-002]
  - Test Scenarios: [consultation-filter-enforcement:_phase_filter_documents/TS-01], [consultation-filter-enforcement:_phase_filter_documents/TS-02], [consultation-filter-enforcement:_phase_filter_documents/TS-03], [consultation-filter-enforcement:_phase_filter_documents/TS-04], [consultation-filter-enforcement:_phase_filter_documents/TS-05], [consultation-filter-enforcement:_post_filter_consultation_documents/TS-01], [consultation-filter-enforcement:_post_filter_consultation_documents/TS-02], [consultation-filter-enforcement:_post_filter_consultation_documents/TS-03], [consultation-filter-enforcement:_post_filter_consultation_documents/TS-04], [consultation-filter-enforcement:_post_filter_consultation_documents/TS-05], [consultation-filter-enforcement:_post_filter_consultation_documents/TS-06], [consultation-filter-enforcement:_post_filter_consultation_documents/TS-07], [consultation-filter-enforcement:_post_filter_consultation_documents/TS-08], [consultation-filter-enforcement:_post_filter_consultation_documents/TS-09], [consultation-filter-enforcement:_post_filter_consultation_documents/TS-10]

---

## Intermediate Dead Code Tracking

N/A — Single phase, no intermediate dead code.

| Phase Introduced | Description | Used In Phase | Status |
|------------------|-------------|---------------|--------|

---

## Intermediate Stub Tracking

N/A — No stubs required.

| Phase Introduced | Test Name | Reason for Stub | Implemented In Phase | Status |
|------------------|-----------|-----------------|----------------------|--------|

---

## Requirements Validation

- [consultation-filter-enforcement:FR-001]
  - Phase 1 Task 1
- [consultation-filter-enforcement:FR-002]
  - Phase 1 Task 1
- [consultation-filter-enforcement:FR-003]
  - Phase 1 Task 1
- [consultation-filter-enforcement:NFR-001]
  - Phase 1 Task 1
- [consultation-filter-enforcement:NFR-002]
  - Phase 1 Task 1

---

## Test Scenario Validation

### Component Scenarios
- [consultation-filter-enforcement:_phase_filter_documents/TS-01]: Phase 1 Task 1
- [consultation-filter-enforcement:_phase_filter_documents/TS-02]: Phase 1 Task 1
- [consultation-filter-enforcement:_phase_filter_documents/TS-03]: Phase 1 Task 1
- [consultation-filter-enforcement:_phase_filter_documents/TS-04]: Phase 1 Task 1
- [consultation-filter-enforcement:_phase_filter_documents/TS-05]: Phase 1 Task 1
- [consultation-filter-enforcement:_post_filter_consultation_documents/TS-01]: Phase 1 Task 1
- [consultation-filter-enforcement:_post_filter_consultation_documents/TS-02]: Phase 1 Task 1
- [consultation-filter-enforcement:_post_filter_consultation_documents/TS-03]: Phase 1 Task 1
- [consultation-filter-enforcement:_post_filter_consultation_documents/TS-04]: Phase 1 Task 1
- [consultation-filter-enforcement:_post_filter_consultation_documents/TS-05]: Phase 1 Task 1
- [consultation-filter-enforcement:_post_filter_consultation_documents/TS-06]: Phase 1 Task 1
- [consultation-filter-enforcement:_post_filter_consultation_documents/TS-07]: Phase 1 Task 1
- [consultation-filter-enforcement:_post_filter_consultation_documents/TS-08]: Phase 1 Task 1
- [consultation-filter-enforcement:_post_filter_consultation_documents/TS-09]: Phase 1 Task 1
- [consultation-filter-enforcement:_post_filter_consultation_documents/TS-10]: Phase 1 Task 1

### Integration Scenarios
N/A

### E2E Scenarios
N/A

---

## Appendix

### Glossary
- **Post-filter:** Programmatic filter applied after LLM document selection to enforce hard exclusion rules.
- **Portal category:** Section header from Cherwell portal stored in `document_type` field.

### References
- [review-scope-control specification](.sdd/review-scope-control/specification.md)
- [document-filtering design](.sdd/document-filtering/design.md)
- [review-workflow-redesign design](.sdd/review-workflow-redesign/design.md)

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-15 | BBUG | Initial design |

---
