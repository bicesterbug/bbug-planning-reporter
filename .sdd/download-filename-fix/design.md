# Design: Download Filename Fix

**Version:** 1.0
**Date:** 2026-02-14
**Status:** Implemented
**Linked Specification** `.sdd/download-filename-fix/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context
The `_download_document` method in `CherwellScraperMCP` (server.py) derives filenames from download URLs when no explicit filename is provided. It uses `urlparse(url).path.split("/")[-1]` which works for URLs like `https://example.com/files/report.pdf` but fails for Cherwell portal URLs like `/Document/Download?fileName=Report.pdf`, producing `"Download"` with no extension.

### Proposed Architecture
Modify the filename derivation logic to check for a `fileName` query parameter first (matching the Cherwell portal's URL pattern), then fall back to path-based extraction, then to a hash-based default with `.pdf` extension. Also add duplicate filename disambiguation to handle multiple documents with the same name in the same application directory.

### Technology Decisions
- Use `urllib.parse.parse_qs` (already imported) to extract query parameters
- Simple numeric suffix `_N` pattern for deduplication (no need for UUID or hash)

### Quality Attributes
- Minimal code change to existing filename derivation logic
- Backwards compatible with non-Cherwell URLs

---

## API Design

N/A — no public API changes. Internal behavior change only.

---

## Modified Components

### `_download_document` method in `CherwellScraperMCP`
**Change Description** Currently extracts filename from `urlparse(url).path.split("/")[-1]`. Change to: (1) check `fileName` query parameter first via `parse_qs`, (2) fall back to path extraction, (3) fall back to hash-based default. After determining filename, check if the file already exists in the output directory and append `_N` suffix if so.

**Dependants** None — internal implementation change

**Kind** Method

**Requirements References**
- [download-filename-fix:FR-001]: Extract filename from query parameter
- [download-filename-fix:FR-002]: Disambiguate duplicate filenames
- [download-filename-fix:NFR-001]: Backwards compatible with path-based URLs

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| _download_document/TS-01 | Filename from query parameter | URL has `fileName=Report.pdf` query param | `_download_document` is called | File is saved as `Report.pdf` |
| _download_document/TS-02 | URL-encoded filename | URL has `fileName=Transport%20Assessment.pdf` | `_download_document` is called | File is saved as `Transport Assessment.pdf` |
| _download_document/TS-03 | Path-based filename fallback | URL is `https://example.com/files/report.pdf` (no query params) | `_download_document` is called | File is saved as `report.pdf` |
| _download_document/TS-04 | Hash-based default fallback | URL path ends with empty segment and no query params | `_download_document` is called | File is saved as `document_XXXXXXXX.pdf` |
| _download_document/TS-05 | Explicit filename parameter | `filename` input parameter is provided | `_download_document` is called | Explicit filename is used (no extraction) |
| _download_document/TS-06 | Duplicate filename disambiguation | Two documents have the same filename in same directory | Both are downloaded | Second file gets `_1` suffix before extension |
| _download_document/TS-07 | Multiple duplicates | Three documents with same filename | All three downloaded | Files are `name.pdf`, `name_1.pdf`, `name_2.pdf` |

---

## Added Components

None

---

## Used Components

### `urllib.parse` module
**Location** Python standard library

**Provides** `parse_qs` for extracting query parameters from URLs

**Used By** Modified `_download_document` method

---

## Documentation Considerations
- None — internal bugfix

---

## Instrumentation (if needed)

N/A

---

## Integration Test Scenarios (if needed)

N/A — the fix is at the filename derivation level, tested via unit tests with mocked HTTP.

---

## E2E Test Scenarios (if needed)

| ID | Scenario | Given | When | Then | User Journey |
|----|----------|-------|------|------|--------------|
| E2E-01 | Review with Cherwell portal documents succeeds | Application 21/03266/F exists with Cherwell download URLs | Review is submitted | Documents are downloaded with correct extensions and ingested successfully | Submit review → download → ingest → analyse → generate |

Note: E2E-01 requires a live Cherwell portal — manual verification only.

---

## Test Data
- Mock Cherwell download URLs with `fileName` query parameter
- Mock standard URLs with filenames in path

---

## Test Feasibility
- All tests use mocked HTTP responses (respx) — no external dependencies
- Existing test fixtures already contain Cherwell URL patterns

---

## Risks and Dependencies
- **Low risk**: Additive change to filename extraction logic with fallback chain
- **No external dependencies**: Uses only standard library `urllib.parse`

---

## Feasability Review
- No blockers

---

## Task Breakdown

### Phase 1: Fix filename extraction and add deduplication

- Task 1: Fix `_download_document` filename extraction to use `fileName` query parameter and add deduplication
  - Status: Done
  - Modify `_download_document` in `server.py` to: (1) extract `fileName` from query params, (2) fall back to path, (3) fall back to hash default. Add loop to disambiguate duplicate filenames by appending `_N` suffix. Write tests for all scenarios.
  - Requirements: [download-filename-fix:FR-001], [download-filename-fix:FR-002], [download-filename-fix:NFR-001]
  - Test Scenarios: [download-filename-fix:_download_document/TS-01], [download-filename-fix:_download_document/TS-02], [download-filename-fix:_download_document/TS-03], [download-filename-fix:_download_document/TS-04], [download-filename-fix:_download_document/TS-05], [download-filename-fix:_download_document/TS-06], [download-filename-fix:_download_document/TS-07]

---

## Intermediate Dead Code Tracking

N/A — single phase.

---

## Intermediate Stub Tracking

N/A — single phase.

---

## Requirements Validation

- [download-filename-fix:FR-001]
  - Phase 1 Task 1
- [download-filename-fix:FR-002]
  - Phase 1 Task 1
- [download-filename-fix:NFR-001]
  - Phase 1 Task 1

---

## Test Scenario Validation

### Component Scenarios
- [download-filename-fix:_download_document/TS-01]: Phase 1 Task 1
- [download-filename-fix:_download_document/TS-02]: Phase 1 Task 1
- [download-filename-fix:_download_document/TS-03]: Phase 1 Task 1
- [download-filename-fix:_download_document/TS-04]: Phase 1 Task 1
- [download-filename-fix:_download_document/TS-05]: Phase 1 Task 1
- [download-filename-fix:_download_document/TS-06]: Phase 1 Task 1
- [download-filename-fix:_download_document/TS-07]: Phase 1 Task 1

### Integration Scenarios
N/A

### E2E Scenarios
- [download-filename-fix:E2E-01]: Manual verification after deployment

---

## Appendix

### Glossary
- **Cherwell portal URL pattern**: `/Document/Download?module=PLA&...&fileName=<encoded_filename>`

### References
- Cherwell portal HTML fixtures in `tests/fixtures/cherwell/`

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-14 | Claude | Initial design |
