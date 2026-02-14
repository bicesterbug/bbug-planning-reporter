# Design: Reliable Category Filtering

**Version:** 1.0
**Date:** 2026-02-14
**Status:** Implemented
**Linked Specification** `.sdd/reliable-category-filtering/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The document filtering pipeline has two stages:

1. **Parser** (`parsers.py:CherwellParser._parse_cherwell_register_documents`) walks the HTML table rows, extracting section header rows to set `document_type` for subsequent document rows. The `_extract_section_header` method identifies headers by looking for `<td colspan>` or `<strong>`/`<b>` patterns, but explicitly skips any row containing `<th>` elements.

2. **Filter** (`filters.py:DocumentFilter._should_download`) checks `document_type` against category allowlists/denylists first (exact match), then falls through to title-based pattern matching. When `document_type` is None, category matching is entirely skipped.

The Cherwell portal uses `<tr class="header active"><th>Category Name</th></tr>` for section headers. Since the parser skips all `<th>` rows, every document gets `document_type = None`, and the category-based filtering is completely bypassed.

### Proposed Architecture

No architectural change. Two targeted fixes:

1. **Parser fix**: Modify `_extract_section_header` to recognize `<th>`-based section headers while still correctly skipping the column header row (the first `<tr>` with multiple `<th>` cells like "Document Type", "Date", "Description").

2. **Filter fix**: Add "consultee responses" to the consultation denylist and add all newly discovered portal categories to the allowlist.

Both fixes are isolated to their respective modules with no ripple effects on the rest of the pipeline.

### Technology Decisions

- No new dependencies or technologies. Pure logic fixes in existing Python modules.
- New HTML test fixture based on real portal structure replaces reliance on synthetic fixtures that don't match production HTML.

### Quality Attributes

- **Reliability**: The primary quality attribute. Both the `<td colspan>/<strong>` and `<th>` section header formats will be supported, so the parser handles both the existing test fixture and real portal HTML.
- **Maintainability**: Category lists are kept centralized in `DocumentFilter` class attributes, easy to update when new portal categories are discovered.

---

## API Design

N/A — No public interface changes. Internal behavior fix only.

---

## Modified Components

### CherwellParser._extract_section_header

**Change Description:** Currently skips ALL rows with `<th>` elements (line 387). Must be changed to distinguish between the column header row (first `<tr>` with multiple `<th>` cells containing "Document", "Date", "Description", "File Size", "Drawing/Rev Number") and section header rows (a `<tr>` with a single `<th>` containing a category name like "Public Comments").

The fix: instead of `if row.find("th"): return None`, check whether the row has a single `<th>` element with non-column-header text. A row with multiple `<th>` cells is the column header and should be skipped. A row with a single `<th>` containing a short category name is a section header and should be extracted.

**Dependants:** `_parse_cherwell_register_documents` — calls this method. No change needed there since the return type and semantics are unchanged.

**Kind:** Method

**Requirements References**
- [reliable-category-filtering:FR-001]: This is the core parser fix — `<th>`-based section headers must be recognized
- [reliable-category-filtering:NFR-001]: Ensures every document gets a non-None `document_type` when headers are present
- [reliable-category-filtering:NFR-002]: Must continue to work with existing `<td colspan>` and `<strong>` patterns

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Single `<th>` section header extracted | HTML row `<tr class="header active"><th>Supporting Documents</th></tr>` | `_extract_section_header(row)` called | Returns "Supporting Documents" |
| TS-02 | Multi-`<th>` column header skipped | HTML row with 6 `<th>` cells: "All", "Document Type", "Date", "Description", "File Size", "Drawing/Rev Number" | `_extract_section_header(row)` called | Returns None |
| TS-03 | All portal categories extracted | HTML table with `<th>` section headers for all 9 known categories | `parse_document_list()` called | Each document has the correct `document_type` matching its section header |
| TS-04 | Existing `<td colspan>` headers still work | HTML table from existing fixture `document_table_with_categories.html` | `parse_document_list()` called | Section headers extracted correctly (existing tests pass) |
| TS-05 | No documents have None `document_type` | HTML table with `<th>` section headers and document rows | `parse_document_list()` called | Every document's `document_type` is non-None |

### DocumentFilter category lists

**Change Description:** Currently `CATEGORY_DENYLIST_CONSULTATION` only contains `"consultation responses"`. The real portal uses "Consultee Responses". Must add "consultee responses" to the consultation denylist. Must also add newly discovered portal categories to the allowlist: "proposed plans", "officer/committee consideration", "decision and legal agreements", "planning application documents".

**Dependants:** None — category matching logic in `_should_download` uses iteration over these lists, so adding entries requires no logic changes.

**Kind:** Class attributes

**Requirements References**
- [reliable-category-filtering:FR-002]: Category lists must match actual portal text

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | "Consultee Responses" category denied | Document with `document_type="Consultee Responses"` | `_should_download()` called with defaults | Returns `(False, ...)` |
| TS-02 | "Consultation Responses" still denied | Document with `document_type="Consultation Responses"` | `_should_download()` called with defaults | Returns `(False, ...)` (backward compat) |
| TS-03 | "Consultee Responses" allowed with toggle | Document with `document_type="Consultee Responses"` | `_should_download()` called with `include_consultation_responses=True` | Returns `(True, ...)` |
| TS-04 | "Proposed Plans" category allowed | Document with `document_type="Proposed Plans"` | `_should_download()` called | Returns `(True, ...)` |
| TS-05 | "Officer/Committee Consideration" allowed | Document with `document_type="Officer/Committee Consideration"` | `_should_download()` called | Returns `(True, ...)` |
| TS-06 | "Decision and Legal Agreements" allowed | Document with `document_type="Decision and Legal Agreements"` | `_should_download()` called | Returns `(True, ...)` |
| TS-07 | "Planning Application Documents" allowed | Document with `document_type="Planning Application Documents"` | `_should_download()` called | Returns `(True, ...)` |

---

## Added Components

### Real portal HTML test fixture

**Description:** A minimal HTML fixture that mirrors the actual Cherwell planning register structure with `<th>`-based section headers inside `<tr class="header active">` rows. Contains documents across multiple categories: Application Forms, Supporting Documents, Consultee Responses, Public Comments, and Site Plans. Used by both parser tests and integration tests.

**Users:** Parser tests (`test_parsers.py`), filter integration tests (`test_filters.py`)

**Kind:** Test fixture (HTML file)

**Location:** `tests/fixtures/cherwell/document_table_th_headers.html`

**Requirements References**
- [reliable-category-filtering:FR-001]: Exercises the `<th>` section header extraction
- [reliable-category-filtering:FR-003]: Provides real-format HTML for validation
- [reliable-category-filtering:NFR-001]: Verifies 100% category assignment

**Test Scenarios**

N/A — This is test data, not executable code. It is consumed by the test scenarios in other components.

---

## Used Components

### CherwellParser._parse_cherwell_register_documents

**Location:** `src/mcp_servers/cherwell_scraper/parsers.py:303`

**Provides:** The table-walking loop that calls `_extract_section_header` for non-document rows and sets `current_section` / `document_type`. The fix to `_extract_section_header` flows through this method automatically.

**Used By:** CherwellParser._extract_section_header (modified), test scenarios

### DocumentFilter._should_download

**Location:** `src/mcp_servers/cherwell_scraper/filters.py:299`

**Provides:** The filter decision logic that checks `document_type` against category allowlists/denylists. The updated category lists flow through this method automatically.

**Used By:** DocumentFilter category lists (modified), integration test scenarios

---

## Documentation Considerations

- No documentation changes needed. This is an internal bug fix.

---

## Instrumentation (if needed)

N/A — Existing INFO-level logging in `DocumentFilter.filter_documents` already logs every decision with `document_type` and `filter_reason`. The fix ensures `document_type` is populated, which makes the existing logs more useful.

---

## Integration Test Scenarios

| ID | Scenario | Given | When | Then | Components Involved |
|----|----------|-------|------|------|---------------------|
| ITS-01 | Parser+filter pipeline with `<th>` headers | HTML fixture with `<th>` section headers containing Application Forms (2 docs), Supporting Documents (2 docs), Consultee Responses (2 docs), Public Comments (2 docs), Site Plans (1 doc) | `parse_document_list()` then `filter_documents()` | Supporting Documents and Application Forms and Site Plans allowed (5 docs); Consultee Responses and Public Comments denied (4 docs) | CherwellParser, DocumentFilter |
| ITS-02 | Existing fixture still works | Existing `document_table_with_categories.html` fixture with `<td colspan>/<strong>` headers | `parse_document_list()` then `filter_documents()` | Same results as before: 6 allowed, 3 filtered (all existing test assertions pass) | CherwellParser, DocumentFilter |

---

## E2E Test Scenarios

N/A — This is an internal parser/filter fix. E2E testing would require a live Cherwell portal interaction which is not feasible in CI. The integration tests with real-format HTML fixtures provide sufficient coverage.

---

## Test Data

- **New fixture:** `tests/fixtures/cherwell/document_table_th_headers.html` — minimal HTML with `<th>`-based section headers matching real Cherwell portal structure. Contains 9 documents across 5 categories.
- **Existing fixture:** `tests/fixtures/cherwell/document_table_with_categories.html` — existing fixture with `<td colspan>/<strong>` headers. Must continue to work unchanged.

---

## Test Feasibility

No missing infrastructure. All tests run in-process with HTML fixtures — no external dependencies needed.

---

## Risks and Dependencies

- **Risk: Other portal HTML variants.** The Cherwell portal may have additional section header formats not yet encountered. **Mitigation:** The fix adds `<th>` support alongside existing `<td colspan>` and `<strong>` patterns, making the parser more resilient. If new formats are discovered, the same pattern can be extended.
- **Risk: Category names may change.** The portal could rename categories in future updates. **Mitigation:** Category matching is case-insensitive and the lists are centralized in one class, easy to update.
- **Assumption:** The column header row always has multiple `<th>` cells (6 observed: checkbox, Document Type, Date, Description, File Size, Drawing/Rev Number), while section header rows have a single `<th>`. This holds for all observed portal HTML.

---

## Feasibility Review

No missing features or infrastructure. This is a targeted two-file fix.

---

## Task Breakdown

> **CRITICAL: Tests are written WITH implementation, not after.**

### Phase 1: Fix parser and filter

- Task 1: Fix `_extract_section_header` to handle `<th>` section headers
  - Status: Done
  - Modify `_extract_section_header` in `parsers.py` to recognize single-`<th>` section header rows while skipping multi-`<th>` column header rows. Create the new HTML fixture `document_table_th_headers.html`. Add parser tests.
  - Requirements: [reliable-category-filtering:FR-001], [reliable-category-filtering:FR-003], [reliable-category-filtering:NFR-001], [reliable-category-filtering:NFR-002]
  - Test Scenarios: [reliable-category-filtering:CherwellParser/TS-01], [reliable-category-filtering:CherwellParser/TS-02], [reliable-category-filtering:CherwellParser/TS-03], [reliable-category-filtering:CherwellParser/TS-04], [reliable-category-filtering:CherwellParser/TS-05], [reliable-category-filtering:ITS-02]

- Task 2: Update filter category lists to match real portal
  - Status: Done
  - Add "consultee responses" to `CATEGORY_DENYLIST_CONSULTATION`. Add "proposed plans", "officer/committee consideration", "decision and legal agreements", "planning application documents" to `CATEGORY_ALLOWLIST`. Add filter tests for all new categories and integration test with new fixture.
  - Requirements: [reliable-category-filtering:FR-002], [reliable-category-filtering:FR-003]
  - Test Scenarios: [reliable-category-filtering:DocumentFilter/TS-01], [reliable-category-filtering:DocumentFilter/TS-02], [reliable-category-filtering:DocumentFilter/TS-03], [reliable-category-filtering:DocumentFilter/TS-04], [reliable-category-filtering:DocumentFilter/TS-05], [reliable-category-filtering:DocumentFilter/TS-06], [reliable-category-filtering:DocumentFilter/TS-07], [reliable-category-filtering:ITS-01]

---

## Intermediate Dead Code Tracking

N/A — Single phase, no intermediate dead code.

---

## Intermediate Stub Tracking

N/A — Single phase, no stubs.

---

## Requirements Validation

- [reliable-category-filtering:FR-001] — Extract `<th>` section headers
  - Phase 1 Task 1
- [reliable-category-filtering:FR-002] — Match all portal category names
  - Phase 1 Task 2
- [reliable-category-filtering:FR-003] — Validate with real portal HTML
  - Phase 1 Task 1 (fixture creation), Phase 1 Task 2 (integration test)
- [reliable-category-filtering:NFR-001] — Zero false negatives on category extraction
  - Phase 1 Task 1
- [reliable-category-filtering:NFR-002] — Backward compatibility
  - Phase 1 Task 1
- [reliable-category-filtering:NFR-003] — Filter auditability
  - N/A — existing logging already satisfies this; the fix ensures `document_type` is populated so logs are more informative

---

## Test Scenario Validation

### Component Scenarios
- [reliable-category-filtering:CherwellParser/TS-01]: Phase 1 Task 1
- [reliable-category-filtering:CherwellParser/TS-02]: Phase 1 Task 1
- [reliable-category-filtering:CherwellParser/TS-03]: Phase 1 Task 1
- [reliable-category-filtering:CherwellParser/TS-04]: Phase 1 Task 1
- [reliable-category-filtering:CherwellParser/TS-05]: Phase 1 Task 1
- [reliable-category-filtering:DocumentFilter/TS-01]: Phase 1 Task 2
- [reliable-category-filtering:DocumentFilter/TS-02]: Phase 1 Task 2
- [reliable-category-filtering:DocumentFilter/TS-03]: Phase 1 Task 2
- [reliable-category-filtering:DocumentFilter/TS-04]: Phase 1 Task 2
- [reliable-category-filtering:DocumentFilter/TS-05]: Phase 1 Task 2
- [reliable-category-filtering:DocumentFilter/TS-06]: Phase 1 Task 2
- [reliable-category-filtering:DocumentFilter/TS-07]: Phase 1 Task 2

### Integration Scenarios
- [reliable-category-filtering:ITS-01]: Phase 1 Task 2
- [reliable-category-filtering:ITS-02]: Phase 1 Task 1

### E2E Scenarios
N/A

---

## Appendix

### Glossary
- **Column header row**: The first `<tr>` in the document table containing multiple `<th>` cells with column labels ("Document Type", "Date", "Description", etc.). Must be skipped by section header extraction.
- **Section header row**: A `<tr>` within the document table containing a single `<th>` with a category name (e.g., "Public Comments"). Groups subsequent document rows under that category.

### References
- [reliable-category-filtering specification](specification.md)
- [document-filtering specification](../document-filtering/specification.md)
- [review-output-fixes design](../review-output-fixes/design.md) — original category extraction design
- Live Cherwell portal analysis: application 21/03267/OUT with 667 documents

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-14 | Claude Opus 4.6 | Initial design |

---
