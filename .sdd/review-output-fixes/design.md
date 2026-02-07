# Design: Review Output Fixes

**Version:** 1.0
**Date:** 2026-02-07
**Status:** Implementation Complete
**Linked Specification** `.sdd/review-output-fixes/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The system has two independent data paths affected by these bugs:

1. **Scraper → Filter → Download pipeline**: The Cherwell scraper (`parsers.py`) extracts document metadata from the portal HTML. The filter (`filters.py`) decides which documents to download based on `document_type` and `description` fields. Currently, `document_type` is set to per-row link text (usually the filename) rather than the portal's section header category, so filtering falls back to brittle title pattern matching.

2. **Orchestrator → Review output pipeline**: The orchestrator (`orchestrator.py`) calls Claude to generate a markdown review, then extracts `overall_rating`, `key_documents`, `full_markdown`, and `summary`. The `aspects`, `policy_compliance`, `recommendations`, and `suggested_conditions` fields defined in `ReviewContent` are never populated, despite the markdown containing all the structured data needed.

### Proposed Architecture

**Bug 1 — Category-based filtering**: Modify the parser's `_parse_cherwell_register_documents()` method to walk the table structure and track the current section header. Each document row inherits its section header as `document_type`. The filter then adds a new first step: check `document_type` against a category allowlist/denylist before falling through to existing title-based pattern matching.

**Bug 2 — Structured field parsing**: Add a new `ReviewMarkdownParser` module that extracts structured data from review markdown. The orchestrator calls this parser after receiving Claude's response, and includes the parsed fields in the `review` dict alongside the existing fields. The parser is a pure function with no side effects — it takes markdown text and returns parsed fields (or None for each field that can't be found).

### Technology Decisions

- **No new dependencies**: Both fixes use existing libraries (BeautifulSoup for HTML parsing, `re` for markdown parsing)
- **Pure function approach for markdown parser**: The `ReviewMarkdownParser` is stateless and testable in isolation with string inputs/outputs
- **Additive changes to filter**: Category-based filtering is added as a new first step in `_should_download()`, preserving existing title-based logic as fallback

### Quality Attributes

- **Robustness**: All parsing is wrapped in try/except with graceful fallback to None. A parsing failure never causes a review to fail.
- **Backward compatibility**: Existing `full_markdown` output is untouched. Structured fields are additive. Category extraction falls back to current behaviour if section headers aren't found.

---

## API Design

N/A — No public API changes. The existing `ReviewContent` schema already defines the fields that will now be populated. The `DocumentInfo.document_type` field already exists and will now contain more useful values.

---

## Modified Components

### CherwellParser._parse_cherwell_register_documents
**Change Description** Currently iterates over `<a class="singledownloadlink">` elements and sets `document_type` to the link text (typically the filename). Must be changed to: (1) find the enclosing table, (2) iterate rows in order, (3) identify section header rows (rows without a `singledownloadlink` that contain a category label), (4) track the current section header, and (5) set `document_type` to the current section header for each document row.

**Dependants** `DocumentFilter._should_download()` (consumes `document_type`)

**Kind** Method

**Requirements References**
- [review-output-fixes:FR-001]: Parser must extract section headers and propagate them as `document_type`
- [review-output-fixes:NFR-002]: Must correctly associate each document with its section header

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| CherwellParser/TS-01 | Section headers propagated | HTML table with "Application Forms" header row followed by 2 document rows, then "Supporting Documents" header followed by 3 document rows | `_parse_cherwell_register_documents()` is called | First 2 docs have `document_type="Application Forms"`, next 3 have `document_type="Supporting Documents"` |
| CherwellParser/TS-02 | Consultation category extracted | HTML table with a "Consultation Responses" (or similar) section header followed by document rows | Parser is called | Documents under that header get `document_type` containing "consultation" (case-insensitive) |
| CherwellParser/TS-03 | Flat table fallback | HTML table with `singledownloadlink` elements but no section header rows | Parser is called | Falls back to current behaviour: `document_type` is link text if different from description, else None |
| CherwellParser/TS-04 | Description preserved | HTML table with section headers, where each row has a description in the 4th cell | Parser is called | `description` is the 4th cell text (not the section header); `document_type` is the section header |

### DocumentFilter._should_download
**Change Description** Currently checks denylist patterns, then allowlist patterns, then non-transport denylist, all against both `document_type` and `description` as flattened text. Must be changed to add a new first step: if `document_type` matches a known portal category, make the decision based on the category alone (allowlist categories → ALLOW, denylist categories → DENY). Only fall through to existing title-based logic if `document_type` is None or not a recognised category.

**Dependants** None (public interface unchanged)

**Kind** Method

**Requirements References**
- [review-output-fixes:FR-002]: Filter must use `document_type` (portal category) as primary criterion
- [review-output-fixes:NFR-002]: Category-based filtering must not create false positives

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| DocumentFilter/TS-01 | Category allowlist hit | Document with `document_type="Supporting Documents"` and `description="Transport Response to Consultees"` | `_should_download()` is called | Returns `(True, ...)` — category takes precedence over title |
| DocumentFilter/TS-02 | Category denylist hit | Document with `document_type="Consultation Responses"` and `description="Transport Assessment"` | `_should_download()` is called with `include_consultation_responses=False` | Returns `(False, ...)` — category denylist denies regardless of title |
| DocumentFilter/TS-03 | Category denylist override | Document with `document_type="Consultation Responses"` | `_should_download()` is called with `include_consultation_responses=True` | Returns `(True, ...)` — override bypasses category denylist |
| DocumentFilter/TS-04 | No category fallback | Document with `document_type=None` and `description="Consultation Response"` | `_should_download()` is called | Returns `(False, ...)` — falls through to existing title-based denylist |
| DocumentFilter/TS-05 | Unknown category fallback | Document with `document_type="Other Stuff"` and `description="Planning Statement"` | `_should_download()` is called | Falls through to title-based logic, returns `(True, "Core application document")` |
| DocumentFilter/TS-06 | Comment category denied | Document with `document_type="Public Comments"` | `_should_download()` is called with `include_public_comments=False` | Returns `(False, ...)` |

### AgentOrchestrator._phase_generate_review
**Change Description** Currently extracts `overall_rating`, `key_documents`, `full_markdown`, and `summary` from Claude's markdown response. Must be changed to also call `ReviewMarkdownParser` to extract `aspects`, `policy_compliance`, `recommendations`, and `suggested_conditions`, and include them in the `review` dict.

**Dependants** `review_jobs._handle_success()` (passes through `result.review` dict unchanged)

**Kind** Method

**Requirements References**
- [review-output-fixes:FR-003]: Parse aspects from markdown
- [review-output-fixes:FR-004]: Parse policy compliance from markdown
- [review-output-fixes:FR-005]: Parse recommendations from markdown
- [review-output-fixes:FR-006]: Parse suggested conditions from markdown
- [review-output-fixes:NFR-001]: Parsing failures must not break review generation
- [review-output-fixes:NFR-003]: Existing `full_markdown` and `key_documents` unchanged

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| AgentOrchestrator/TS-01 | Structured fields populated | Claude returns markdown containing Assessment Summary table, Policy Compliance Matrix, and Recommendations section | `_phase_generate_review()` completes | `result.review` dict includes non-null `aspects`, `policy_compliance`, and `recommendations` |
| AgentOrchestrator/TS-02 | Parser failure graceful | Claude returns markdown that doesn't contain recognisable tables | `_phase_generate_review()` completes | `result.review` dict has null for `aspects`, `policy_compliance`, `recommendations`, `suggested_conditions`; no exception raised |
| AgentOrchestrator/TS-03 | Existing fields preserved | Claude returns valid markdown | `_phase_generate_review()` completes | `full_markdown`, `overall_rating`, `key_documents`, `summary` are unchanged from current behaviour |

---

## Added Components

### ReviewMarkdownParser
**Description** A stateless utility class that parses structured data from review markdown text. Provides methods to extract the Assessment Summary table (aspects), Policy Compliance Matrix, Recommendations list, and Suggested Conditions list. Each method returns the parsed data or None if the section is not found. All parsing is wrapped in try/except for robustness.

**Users** `AgentOrchestrator._phase_generate_review()`

**Kind** Class

**Location** `src/agent/review_parser.py`

**Requirements References**
- [review-output-fixes:FR-003]: `parse_aspects()` extracts aspect name, rating, and key issue from Assessment Summary table
- [review-output-fixes:FR-004]: `parse_policy_compliance()` extracts requirement, policy source, compliance status, and notes from Policy Compliance Matrix
- [review-output-fixes:FR-005]: `parse_recommendations()` extracts numbered recommendation titles from Recommendations section
- [review-output-fixes:FR-006]: `parse_suggested_conditions()` extracts condition strings from Suggested Conditions section
- [review-output-fixes:NFR-001]: Each method returns None on parse failure rather than raising

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| ReviewMarkdownParser/TS-01 | Parse aspects table | Markdown containing `## Assessment Summary` with `\| Aspect \| Rating \| Key Issue \|` table | `parse_aspects()` called | Returns list of dicts with `name`, `rating` (lowercased), `key_issue` |
| ReviewMarkdownParser/TS-02 | Parse aspects with varied ratings | Table rows with RED, AMBER, GREEN ratings | `parse_aspects()` called | Each rating is lowercased: "red", "amber", "green" |
| ReviewMarkdownParser/TS-03 | Parse aspects missing table | Markdown without Assessment Summary section | `parse_aspects()` called | Returns None |
| ReviewMarkdownParser/TS-04 | Parse policy compliance | Markdown containing `## Policy Compliance Matrix` with 4-column table using emoji compliance indicators | `parse_policy_compliance()` called | Returns list of dicts with `requirement`, `policy_source`, `compliant` (bool), `notes` |
| ReviewMarkdownParser/TS-05 | Parse compliance emoji indicators | Rows with "❌ NO", "⚠️ PARTIAL", "✅ YES", "⚠️ UNCLEAR" | `parse_policy_compliance()` called | "❌ NO" → `compliant=False`; "⚠️ PARTIAL" → `compliant=False, notes` includes "partial"; "✅ YES" → `compliant=True`; "⚠️ UNCLEAR" → `compliant=False, notes` includes "unclear" |
| ReviewMarkdownParser/TS-06 | Parse compliance missing table | Markdown without Policy Compliance Matrix | `parse_policy_compliance()` called | Returns None |
| ReviewMarkdownParser/TS-07 | Parse recommendations | Markdown with `## Recommendations` section containing numbered bold items like `1. **A41 Cycle Route to Bicester**` | `parse_recommendations()` called | Returns list of strings: `["A41 Cycle Route to Bicester", "Green Lane Connection to Chesterton", ...]` |
| ReviewMarkdownParser/TS-08 | Parse recommendations missing section | Markdown without Recommendations section | `parse_recommendations()` called | Returns None |
| ReviewMarkdownParser/TS-09 | Parse suggested conditions standalone | Markdown with `## Suggested Conditions` section containing numbered items | `parse_suggested_conditions()` called | Returns list of condition strings |
| ReviewMarkdownParser/TS-10 | Parse suggested conditions absent | Markdown without Suggested Conditions section | `parse_suggested_conditions()` called | Returns None |
| ReviewMarkdownParser/TS-11 | Parse real review output | The actual `25_00284_F_review.md` content | All parse methods called | `aspects` has 5 items, `policy_compliance` has 26 items, `recommendations` has 12+ items, `suggested_conditions` is None (no standalone section in this review) |
| ReviewMarkdownParser/TS-12 | Whitespace tolerance | Markdown table with extra spaces around pipe separators | `parse_aspects()` called | Parses correctly, trimming whitespace |

---

## Used Components

### ReviewContent (API schema)
**Location** `src/api/schemas.py:221`

**Provides** Pydantic model defining `aspects: list[ReviewAspect] | None`, `policy_compliance: list[PolicyCompliance] | None`, `recommendations: list[str] | None`, `suggested_conditions: list[str] | None` fields that are already part of the API contract but currently always null.

**Used By** `AgentOrchestrator._phase_generate_review` (provides data to populate these fields), `review_jobs._handle_success` (serialises them into Redis/JSON)

### ReviewAspect / PolicyCompliance (API schema)
**Location** `src/api/schemas.py:178-194`

**Provides** Pydantic models defining the shape of parsed aspect and compliance data. `ReviewMarkdownParser` output must match these shapes.

**Used By** `ReviewMarkdownParser` (produces data matching these schemas)

### DocumentInfo (scraper model)
**Location** `src/mcp_servers/cherwell_scraper/models.py:87`

**Provides** `document_type: str | None` field that will now contain the portal section header instead of the filename.

**Used By** `CherwellParser._parse_cherwell_register_documents` (sets the field), `DocumentFilter._should_download` (reads the field)

---

## Documentation Considerations

- `.env.example` — no changes needed (no new config)
- No new API endpoints or schema changes (fields already exist)
- No README changes needed

---

## Instrumentation (if needed)

N/A — No NFRs require observability-based verification. NFR-001 (parsing robustness) and NFR-002 (filter precision) are verified by testing. NFR-003 (backward compatibility) is verified by test suite.

---

## Integration Test Scenarios (if needed)

| ID | Scenario | Given | When | Then | Components Involved |
|----|----------|-------|------|------|---------------------|
| ITS-01 | Category-based filter with real document set | HTML fixture with section headers containing "Supporting Documents" and "Consultation Responses" groups, including documents that previously bypassed the filter ("Transport Response to Consultees", "Applicant's response to ATE comments") | `CherwellParser.parse_document_list()` then `DocumentFilter.filter_documents()` | "Transport Response to Consultees" is filtered (under consultation category); "Transport Assessment" under "Supporting Documents" is allowed | CherwellParser, DocumentFilter |
| ITS-02 | End-to-end review parsing | Orchestrator mock returns Claude response containing Assessment Summary, Policy Compliance Matrix, and Recommendations sections | `_phase_generate_review()` completes and result is passed to `_handle_success()` | The stored review_data dict contains non-null `aspects`, `policy_compliance`, `recommendations` alongside existing `full_markdown` and `overall_rating` | AgentOrchestrator, ReviewMarkdownParser, review_jobs |

---

## E2E Test Scenarios (if needed)

N/A — These are internal pipeline fixes. The existing E2E test (`test_review_lifecycle.py`) will implicitly validate the changes when run against a real Cherwell application. No new E2E tests needed.

---

## Test Data

- **HTML fixture**: A representative HTML fragment of the Cherwell portal document table with section header rows and document rows, including "Supporting Documents" and "Consultation Responses" sections. Store in `tests/fixtures/cherwell/document_table_with_categories.html`.
- **Markdown fixture**: The existing `output/25_00284_F_review.md` serves as the real-world test case for markdown parsing. A minimal synthetic markdown fixture should also be created for unit tests.

---

## Test Feasibility

- All tests are unit or integration level, requiring no external services
- HTML fixture can be hand-crafted based on the known Cherwell portal structure (section header rows followed by document rows)
- Markdown fixture already exists from the 25/00284/F review run

---

## Risks and Dependencies

1. **Portal HTML structure unknown for section headers**: The Cherwell portal renders documents via JavaScript, so the exact HTML structure of section header rows is not fully confirmed. The parser must be flexible enough to detect header rows by heuristic (e.g. rows without `singledownloadlink`, rows with fewer cells, rows with bold/header text).
   - **Mitigation**: Design the parser to detect headers via multiple heuristics. If no headers found, fall back to current behaviour. Test against a real portal fetch during integration testing.

2. **Markdown format varies between reviews**: Claude may produce slightly different formatting across reviews (e.g. different heading levels, table alignment, emoji usage).
   - **Mitigation**: Use regex patterns tolerant of whitespace, optional emoji, and case variations. Return None on parse failure rather than crashing.

3. **Truncated markdown**: The 25/00284/F review markdown is truncated mid-sentence at the end. The parser must handle markdown that ends abruptly.
   - **Mitigation**: Each section parser operates independently. A truncated Recommendations section still returns the items it found before truncation.

---

## Feasability Review

No blocking prerequisites. All changes are to existing modules with established test patterns.

---

## Task Breakdown

> **CRITICAL: Tests are written WITH implementation, not after.**

### Phase 1: Category-based document filtering

- Task 1: Update `CherwellParser._parse_cherwell_register_documents()` to extract section headers
  - Status: Done
  - Modify the parser to find the enclosing table of `singledownloadlink` elements, iterate rows in document order, identify section header rows (rows without download links), track the current section header, and set `document_type` to the current header for each document row. Create HTML fixture in `tests/fixtures/cherwell/document_table_with_categories.html`. Write tests for header propagation, consultation category extraction, flat table fallback, and description preservation.
  - Requirements: [review-output-fixes:FR-001], [review-output-fixes:NFR-002]
  - Test Scenarios: [review-output-fixes:CherwellParser/TS-01], [review-output-fixes:CherwellParser/TS-02], [review-output-fixes:CherwellParser/TS-03], [review-output-fixes:CherwellParser/TS-04]

- Task 2: Update `DocumentFilter._should_download()` to use category-based filtering
  - Status: Done
  - Add category allowlist and denylist constants. Insert a new first step in `_should_download()`: if `document_type` matches a known allowed category, return ALLOW immediately; if it matches a denied category (and not overridden), return DENY. Only fall through to existing title-based logic if `document_type` is None or unrecognised. Write tests for category allowlist, denylist, override, and fallback scenarios.
  - Requirements: [review-output-fixes:FR-002], [review-output-fixes:NFR-002]
  - Test Scenarios: [review-output-fixes:DocumentFilter/TS-01], [review-output-fixes:DocumentFilter/TS-02], [review-output-fixes:DocumentFilter/TS-03], [review-output-fixes:DocumentFilter/TS-04], [review-output-fixes:DocumentFilter/TS-05], [review-output-fixes:DocumentFilter/TS-06]

- Task 3: Integration test for parser + filter pipeline
  - Status: Done
  - Write integration test that parses HTML fixture through `CherwellParser` then filters through `DocumentFilter`, verifying that documents under "Consultation Responses" are filtered and documents under "Supporting Documents" are allowed regardless of title.
  - Requirements: [review-output-fixes:FR-001], [review-output-fixes:FR-002], [review-output-fixes:NFR-002]
  - Test Scenarios: [review-output-fixes:ITS-01]

### Phase 2: Structured review field parsing

- Task 4: Implement `ReviewMarkdownParser`
  - Status: Done
  - Create `src/agent/review_parser.py` with `ReviewMarkdownParser` class. Implement `parse_aspects()`, `parse_policy_compliance()`, `parse_recommendations()`, `parse_suggested_conditions()` methods. Each uses regex to find the relevant markdown section and parse table rows or numbered list items. Write comprehensive unit tests including the real 25/00284/F markdown as a test case.
  - Requirements: [review-output-fixes:FR-003], [review-output-fixes:FR-004], [review-output-fixes:FR-005], [review-output-fixes:FR-006], [review-output-fixes:NFR-001]
  - Test Scenarios: [review-output-fixes:ReviewMarkdownParser/TS-01], [review-output-fixes:ReviewMarkdownParser/TS-02], [review-output-fixes:ReviewMarkdownParser/TS-03], [review-output-fixes:ReviewMarkdownParser/TS-04], [review-output-fixes:ReviewMarkdownParser/TS-05], [review-output-fixes:ReviewMarkdownParser/TS-06], [review-output-fixes:ReviewMarkdownParser/TS-07], [review-output-fixes:ReviewMarkdownParser/TS-08], [review-output-fixes:ReviewMarkdownParser/TS-09], [review-output-fixes:ReviewMarkdownParser/TS-10], [review-output-fixes:ReviewMarkdownParser/TS-11], [review-output-fixes:ReviewMarkdownParser/TS-12]

- Task 5: Integrate `ReviewMarkdownParser` into orchestrator
  - Status: Done
  - In `_phase_generate_review()`, after extracting `review_markdown`, call `ReviewMarkdownParser` methods to get `aspects`, `policy_compliance`, `recommendations`, `suggested_conditions`. Add these to the `review` dict. Wrap in try/except so parsing failure doesn't affect existing fields. Update orchestrator tests.
  - Requirements: [review-output-fixes:FR-003], [review-output-fixes:FR-004], [review-output-fixes:FR-005], [review-output-fixes:FR-006], [review-output-fixes:NFR-001], [review-output-fixes:NFR-003]
  - Test Scenarios: [review-output-fixes:AgentOrchestrator/TS-01], [review-output-fixes:AgentOrchestrator/TS-02], [review-output-fixes:AgentOrchestrator/TS-03], [review-output-fixes:ITS-02]

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

- [review-output-fixes:FR-001]
  - Phase 1 Task 1
- [review-output-fixes:FR-002]
  - Phase 1 Task 2
  - Phase 1 Task 3
- [review-output-fixes:FR-003]
  - Phase 2 Task 4
  - Phase 2 Task 5
- [review-output-fixes:FR-004]
  - Phase 2 Task 4
  - Phase 2 Task 5
- [review-output-fixes:FR-005]
  - Phase 2 Task 4
  - Phase 2 Task 5
- [review-output-fixes:FR-006]
  - Phase 2 Task 4
  - Phase 2 Task 5

- [review-output-fixes:NFR-001]
  - Phase 2 Task 4
  - Phase 2 Task 5
- [review-output-fixes:NFR-002]
  - Phase 1 Task 1
  - Phase 1 Task 2
  - Phase 1 Task 3
- [review-output-fixes:NFR-003]
  - Phase 2 Task 5

---

## Test Scenario Validation

### Component Scenarios
- [review-output-fixes:CherwellParser/TS-01]: Phase 1 Task 1
- [review-output-fixes:CherwellParser/TS-02]: Phase 1 Task 1
- [review-output-fixes:CherwellParser/TS-03]: Phase 1 Task 1
- [review-output-fixes:CherwellParser/TS-04]: Phase 1 Task 1
- [review-output-fixes:DocumentFilter/TS-01]: Phase 1 Task 2
- [review-output-fixes:DocumentFilter/TS-02]: Phase 1 Task 2
- [review-output-fixes:DocumentFilter/TS-03]: Phase 1 Task 2
- [review-output-fixes:DocumentFilter/TS-04]: Phase 1 Task 2
- [review-output-fixes:DocumentFilter/TS-05]: Phase 1 Task 2
- [review-output-fixes:DocumentFilter/TS-06]: Phase 1 Task 2
- [review-output-fixes:ReviewMarkdownParser/TS-01]: Phase 2 Task 4
- [review-output-fixes:ReviewMarkdownParser/TS-02]: Phase 2 Task 4
- [review-output-fixes:ReviewMarkdownParser/TS-03]: Phase 2 Task 4
- [review-output-fixes:ReviewMarkdownParser/TS-04]: Phase 2 Task 4
- [review-output-fixes:ReviewMarkdownParser/TS-05]: Phase 2 Task 4
- [review-output-fixes:ReviewMarkdownParser/TS-06]: Phase 2 Task 4
- [review-output-fixes:ReviewMarkdownParser/TS-07]: Phase 2 Task 4
- [review-output-fixes:ReviewMarkdownParser/TS-08]: Phase 2 Task 4
- [review-output-fixes:ReviewMarkdownParser/TS-09]: Phase 2 Task 4
- [review-output-fixes:ReviewMarkdownParser/TS-10]: Phase 2 Task 4
- [review-output-fixes:ReviewMarkdownParser/TS-11]: Phase 2 Task 4
- [review-output-fixes:ReviewMarkdownParser/TS-12]: Phase 2 Task 4
- [review-output-fixes:AgentOrchestrator/TS-01]: Phase 2 Task 5
- [review-output-fixes:AgentOrchestrator/TS-02]: Phase 2 Task 5
- [review-output-fixes:AgentOrchestrator/TS-03]: Phase 2 Task 5

### Integration Scenarios
- [review-output-fixes:ITS-01]: Phase 1 Task 3
- [review-output-fixes:ITS-02]: Phase 2 Task 5

### E2E Scenarios
N/A

---

## Appendix

### Glossary
- **Section header row**: A `<tr>` in the Cherwell portal document table that contains a category label but no download link. Acts as a group divider for document rows below it.
- **Category-based filtering**: Making filter decisions based on the portal's section header categories rather than document title substring matching.
- **ReviewMarkdownParser**: New stateless utility that extracts structured data from Claude's markdown review output.

### References
- [review-output-fixes specification](specification.md)
- [document-filtering design](../document-filtering/design.md)
- [key-documents design](../key-documents/design.md)
- [agent-integration design](../agent-integration/design.md)

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-07 | Claude Opus 4.6 | Initial design |

---
