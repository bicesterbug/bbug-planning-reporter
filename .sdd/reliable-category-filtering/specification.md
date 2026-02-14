# Specification: Reliable Category Filtering

**Version:** 1.0
**Date:** 2026-02-14
**Status:** Implemented

---

## Problem Statement

The document filter for public comments and consultee responses is not working. Investigation of the live Cherwell portal (application 21/03267/OUT) reveals two critical bugs: (1) the HTML parser skips section header rows that use `<th>` elements, causing ALL 667 documents to receive `document_type = None` and bypassing category-based filtering entirely, and (2) the category denylist uses "consultation responses" but the portal actually uses "Consultee Responses". The result is that hundreds of irrelevant consultation responses and public comments are downloaded, ingested, and pollute the review with noise.

## Beneficiaries

**Primary:**
- BBUG reviewers who receive reviews polluted by irrelevant consultation responses and public comments
- System operators paying for unnecessary document downloads, storage, and LLM token usage

**Secondary:**
- The LLM review agent which gets cleaner, more focused evidence context
- System reliability — large applications (600+ docs) cause timeouts due to unnecessary downloads

---

## Outcomes

**Must Haves**
- Section headers using `<th>` elements in `<tr class="header active">` rows are correctly extracted as document categories
- Every document parsed from the portal has a non-None `document_type` when section headers are present in the HTML
- The category denylist matches the actual portal text: "Consultee Responses" (not just "Consultation Responses")
- All portal category variants are recognized: "Application Forms", "Supporting Documents", "Site Plans", "Proposed Plans", "Consultee Responses", "Public Comments", "Officer/Committee Consideration", "Decision and Legal Agreements", "Planning Application Documents"
- Filtering is validated against real Cherwell portal HTML (not just synthetic test fixtures)

**Nice-to-haves**
- None — this is a critical reliability fix

---

## Explicitly Out of Scope

- Changing the fail-safe default behavior (unknown types still default to allow)
- Adding new title-based denylist patterns (the category-based approach is the primary defense)
- Modifying the `include_consultation_responses` or `include_public_comments` toggle behavior
- Content-based or AI-based document classification
- Supporting portals other than Cherwell

---

## Functional Requirements

### FR-001: Extract Section Headers from `<th>` Rows
**Description:** The parser must recognize section header rows where the category name is in a `<th>` element within a `<tr>` row (typically with class "header active" or similar). These rows contain a single `<th>` with the category text (e.g., "Supporting Documents", "Public Comments"). The parser currently skips all rows containing `<th>` elements, which is incorrect — only the first row (the column header with "Document Type", "Date", "Description", etc.) should be skipped.

**Examples:**
- Positive case: `<tr class="header active"><th>Supporting Documents</th></tr>` sets `current_section = "Supporting Documents"` for subsequent document rows
- Positive case: `<tr class="header active"><th>Public Comments</th></tr>` sets `current_section = "Public Comments"`
- Edge case: The first row with `<th>` elements containing column headers ("Document Type", "Date", "Description", "File Size", "Drawing/Rev Number") is correctly skipped as a table header, not treated as a section header
- Edge case: Existing `<td colspan>` and `<strong>/<b>` section header detection continues to work for other portal layouts

### FR-002: Match All Portal Category Names in Filter
**Description:** The filter's category denylist and allowlist must match the actual category names used by the Cherwell planning register portal. The denylist must include both "Consultee Responses" and "Consultation Responses" (for robustness). The allowlist and denylist must cover all known portal categories.

**Examples:**
- Positive case: Documents under "Consultee Responses" category are filtered (denied by default)
- Positive case: Documents under "Consultation Responses" category are also filtered (backward compatibility)
- Positive case: Documents under "Public Comments" category are filtered
- Positive case: Documents under "Supporting Documents" are allowed
- Positive case: Documents under "Application Forms" are allowed
- Positive case: Documents under "Site Plans" are allowed
- Positive case: Documents under "Proposed Plans" are allowed
- Positive case: Documents under "Officer/Committee Consideration" are allowed
- Positive case: Documents under "Decision and Legal Agreements" are allowed
- Positive case: Documents under "Planning Application Documents" are allowed
- Edge case: Category matching remains case-insensitive

### FR-003: Validate with Real Portal HTML
**Description:** The test suite must include a fixture of real Cherwell portal HTML (from a representative application) that exercises the full parser-to-filter pipeline. This fixture must contain documents across multiple categories including consultation responses and public comments, demonstrating that the filter correctly separates them.

**Examples:**
- Positive case: A fixture HTML containing "Supporting Documents", "Consultee Responses", and "Public Comments" sections produces correct `document_type` values for each document
- Positive case: The filter correctly allows supporting documents and denies consultation responses and public comments from the fixture

---

## Non-Functional Requirements

### NFR-001: Zero False Negatives on Category Extraction
**Category:** Reliability
**Description:** When the portal HTML contains section header rows (in any supported format: `<th>`, `<td colspan>`, `<strong>`/`<b>`), every document row following a section header must have its `document_type` set to that section's category text. No document should have `document_type = None` when section headers are present.
**Acceptance Threshold:** 100% of documents have non-None `document_type` when section headers exist in the HTML
**Verification:** Testing (integration test with real portal HTML fixture asserting no None document_type values)

### NFR-002: Backward Compatibility
**Category:** Reliability
**Description:** The fix must not break existing section header extraction for HTML layouts that use `<td colspan>` or `<strong>`/`<b>` patterns (as in the existing test fixture `document_table_with_categories.html`).
**Acceptance Threshold:** All existing parser and filter tests continue to pass without modification
**Verification:** Testing (run full test suite)

### NFR-003: Filter Auditability
**Category:** Maintainability
**Description:** Filter decisions must continue to be logged at INFO level with category information. When a document is filtered by category, the log must include the category name.
**Acceptance Threshold:** 100% of filter decisions logged with category context
**Verification:** Code review

---

## Open Questions

None. The root causes have been identified through direct investigation of the live Cherwell portal.

---

## Appendix

### Glossary
- **Section header row**: A `<tr>` in the document table that contains the category name (e.g., "Supporting Documents") rather than a document download link. Groups subsequent document rows under that category.
- **Category**: The `document_type` field value extracted from section headers, used by the filter to determine download/skip decisions.
- **Portal categories (observed)**: Application Forms, Supporting Documents, Site Plans, Proposed Plans, Consultee Responses, Public Comments, Officer/Committee Consideration, Decision and Legal Agreements, Planning Application Documents.

### Root Cause Analysis

**Bug 1: Parser skips `<th>` section headers**
- Location: `parsers.py:_extract_section_header()` line 387
- Code: `if row.find("th"): return None`
- Effect: All rows containing `<th>` elements are skipped, including section header rows like `<tr class="header active"><th>Public Comments</th></tr>`
- Impact: ALL documents get `document_type = None`, bypassing category-based filtering entirely

**Bug 2: Category name mismatch**
- Location: `filters.py` line 106-108
- Code: `CATEGORY_DENYLIST_CONSULTATION = ["consultation responses"]`
- Portal uses: "Consultee Responses" (not "Consultation Responses")
- Impact: Even if category extraction worked, consultation responses would not be filtered because the denylist uses a different string

**Evidence**: Live portal test on application 21/03267/OUT:
- 667 documents parsed, ALL with `document_type = None`
- 462 documents downloaded (should be ~200 after filtering)
- Section headers found at rows: 1 (Application Forms), 45 (Supporting Documents), 241 (Site Plans), 246 (Proposed Plans), 263 (Consultee Responses), 385 (Public Comments), 664 (Officer/Committee Consideration), 670 (Decision and Legal Agreements), 672 (Planning Application Documents)

### References
- [document-filtering specification](../document-filtering/specification.md) - Original filter spec
- [review-scope-control specification](../review-scope-control/specification.md) - Toggle behavior
- [review-output-fixes specification](../review-output-fixes/specification.md) - Category-based filtering addition

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-14 | Claude Opus 4.6 | Initial specification |
