# Design: Document Filtering

**Version:** 1.0
**Date:** 2026-02-07
**Status:** Draft
**Linked Specification** `.sdd/document-filtering/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The Cherwell Scraper MCP server exists and provides tools to fetch application metadata and download documents. The current implementation downloads ALL documents associated with a planning application without any filtering logic. The `download_all_documents` tool iterates through the document list returned by the parser and downloads every document regardless of type.

Current flow:
1. `list_application_documents` fetches and parses HTML, extracting all documents with their metadata (including `document_type`)
2. `download_all_documents` calls `list_application_documents`, then downloads each document in the returned list
3. No filtering occurs - all documents are downloaded

The `DocumentInfo` model already includes a `document_type` field that is populated by the parser from the Cherwell portal's document category field.

### Proposed Architecture

We will introduce a **DocumentFilter** component that acts as a gatekeeper between document listing and document downloading. The filter examines document metadata (specifically `document_type`) and makes allow/deny decisions based on configurable rules.

```
┌─────────────────────────────────────────────────────────────────┐
│  CherwellScraperMCP                                              │
│                                                                  │
│  ┌────────────────────────────────────────────────────┐         │
│  │  _download_all_documents()                          │         │
│  │                                                     │         │
│  │  1. Get document list from parser                  │         │
│  │     ↓                                               │         │
│  │  2. Apply DocumentFilter.filter_documents()  ◄─────┼─────┐   │
│  │     │                                               │     │   │
│  │     ├─→ documents_to_download[]                    │     │   │
│  │     └─→ filtered_documents[]                       │     │   │
│  │                                                     │     │   │
│  │  3. Download allowed documents only                │     │   │
│  │                                                     │     │   │
│  │  4. Return both lists in response                  │     │   │
│  └────────────────────────────────────────────────────┘     │   │
│                                                              │   │
│  ┌──────────────────────────────────────────────────────────┴─┐ │
│  │  DocumentFilter (NEW)                                      │ │
│  │                                                            │ │
│  │  - filter_documents(docs, skip_filter=False)              │ │
│  │  - _should_download(doc_type) → (decision, reason)        │ │
│  │  - FILTER_RULES (allowlist + denylist patterns)           │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

**Key Changes:**
1. New `DocumentFilter` class in `src/mcp_servers/cherwell_scraper/filters.py`
2. Modified `download_all_documents` tool to apply filtering before downloads
3. Extended `DownloadAllDocumentsInput` schema with `skip_filter` parameter
4. Enhanced response format to include filtered document metadata

### Technology Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Filter Implementation | In-memory rule-based matching | Fast (<10ms per doc), no external dependencies, easy to test |
| Filter Rules Storage | Python module constants | Simple, version-controlled, easy to review and modify |
| Pattern Matching | Case-insensitive substring matching | Robust to Cherwell portal naming variations |
| Default Behavior | Filter enabled by default | Aligns with spec requirement FR-007 (new safe default) |

### Quality Attributes

**Performance:**
- In-memory filtering with simple string matching ensures <10ms per document (NFR-001)
- No network calls or database queries during filtering

**Reliability:**
- Fail-safe defaults: unknown document types are allowed (downloaded)
- Explicit allowlist ensures critical documents are never filtered
- Denylist for public comments is secondary safety check

**Maintainability:**
- All filter rules centralized in one file with inline documentation
- Clear separation between filter logic and download logic
- Structured logging for every filter decision

**Auditability:**
- Every filter decision logged with document ID, type, decision, reason
- Filtered documents included in API response with filter reasons
- Application reference included in all log entries

---

## API Design

The MCP tool interface is modified to support filtering control and reporting.

### Modified Tool: download_all_documents

**Input Schema (Modified):**
```python
{
  "application_ref": "25/01178/REM",  # existing
  "output_dir": "/data/raw",          # existing
  "skip_filter": false                # NEW - optional, default false
}
```

**Output Schema (Enhanced):**
```python
{
  "status": "success",
  "application_ref": "25/01178/REM",
  "output_dir": "/data/raw/25_01178_REM",
  "total_documents": 45,               # NEW - total before filtering
  "downloaded_count": 38,              # NEW - documents downloaded
  "filtered_count": 7,                 # NEW - documents filtered
  "successful_downloads": 38,
  "failed_downloads": 0,
  "downloads": [                       # existing - only downloaded docs
    {
      "document_id": "abc123",
      "file_path": "/data/raw/25_01178_REM/001_Planning_Statement.pdf",
      "file_size": 1024000,
      "success": true
    }
  ],
  "filtered_documents": [              # NEW - skipped documents
    {
      "document_id": "def456",
      "description": "Objection from resident at 10 Main St",
      "document_type": "Public Comment",
      "filter_reason": "Public comment - not relevant for policy review"
    }
  ]
}
```

### Error Handling Strategy

No new error conditions are introduced. Filtering failures default to downloading documents (fail-safe behavior). Invalid `skip_filter` values are rejected with 400 error.

---

## Modified Components

### CherwellScraperMCP._download_all_documents

**Change Description:** Currently downloads all documents without filtering. Needs to apply DocumentFilter before downloading, collect filtered document metadata, and return enhanced response with both downloaded and filtered document lists.

**Dependants:** None - this is a leaf method in the MCP server

**Kind:** Method

**Requirements References**
- [document-filtering:FR-001]: Implements filtering of public comments by document type
- [document-filtering:FR-002]: Ensures core application documents are downloaded via filter allowlist
- [document-filtering:FR-003]: Ensures technical assessments are downloaded via filter allowlist
- [document-filtering:FR-004]: Ensures officer/decision documents are downloaded via filter allowlist
- [document-filtering:FR-005]: Implements skip_filter parameter to bypass filtering
- [document-filtering:FR-006]: Returns structured filtered_documents array with filter reasons
- [document-filtering:NFR-003]: Logs all filter decisions with structured metadata

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| CherwellScraperMCP/TS-01 | Filter public comments | Application has 5 core docs + 3 public comments | download_all_documents called with default settings | Only 5 core docs downloaded, 3 filtered with reasons in response |
| CherwellScraperMCP/TS-02 | Override filter | Application has 10 docs including public comments | download_all_documents called with skip_filter=true | All 10 documents downloaded, filtered_documents array is empty |
| CherwellScraperMCP/TS-03 | Unknown document type defaults to download | Application has doc with type "Unknown Category" | download_all_documents called | Unknown doc is downloaded (fail-safe behavior) |
| CherwellScraperMCP/TS-04 | Response format includes counts | Application has 20 docs, 5 filtered | download_all_documents completes | Response has total_documents=20, downloaded_count=15, filtered_count=5 |

### DownloadAllDocumentsInput

**Change Description:** Currently only has application_ref and output_dir. Needs to add optional skip_filter boolean parameter with default value false.

**Dependants:** CherwellScraperMCP._download_all_documents (reads this field)

**Kind:** Pydantic Model (Class)

**Requirements References**
- [document-filtering:FR-005]: Provides skip_filter parameter for override functionality
- [document-filtering:FR-007]: Default value (false) enables filtering by default for backward compatibility

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| DownloadAllDocumentsInput/TS-01 | Default skip_filter is false | Input JSON without skip_filter field | Model is instantiated | skip_filter field defaults to false |
| DownloadAllDocumentsInput/TS-02 | Explicit skip_filter=true | Input JSON with skip_filter: true | Model is instantiated | skip_filter field is true |
| DownloadAllDocumentsInput/TS-03 | Invalid skip_filter value | Input JSON with skip_filter: "yes" | Model is instantiated | Pydantic validation error raised |

---

## Added Components

### DocumentFilter

**Description:** Centralizes all document filtering logic. Examines document type metadata and returns filtering decisions based on explicit allowlist and denylist rules. Provides structured output including filter reasons for auditability.

**Users:** CherwellScraperMCP._download_all_documents

**Kind:** Class

**Location:** `src/mcp_servers/cherwell_scraper/filters.py` (new file)

**Requirements References**
- [document-filtering:FR-001]: Implements denylist for public comment patterns
- [document-filtering:FR-002]: Implements allowlist for core application document patterns
- [document-filtering:FR-003]: Implements allowlist for technical assessment patterns
- [document-filtering:FR-004]: Implements allowlist for officer/decision document patterns
- [document-filtering:NFR-001]: In-memory pattern matching for <10ms performance
- [document-filtering:NFR-002]: Fail-safe default when document type is unknown or ambiguous
- [document-filtering:NFR-004]: All filter rules defined in one module with inline documentation

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| DocumentFilter/TS-01 | Core documents allowed | Doc with type "Planning Statement" | filter_documents called | Document in allowed list, not in filtered list |
| DocumentFilter/TS-02 | Technical assessments allowed | Doc with type "Transport Assessment" | filter_documents called | Document in allowed list, not in filtered list |
| DocumentFilter/TS-03 | Officer reports allowed | Doc with type "Officer Report" | filter_documents called | Document in allowed list, not in filtered list |
| DocumentFilter/TS-04 | Public comments filtered | Doc with type "Public Comment" | filter_documents called | Document in filtered list with reason "Public comment" |
| DocumentFilter/TS-05 | Objection letters filtered | Doc with type "Objection Letter" | filter_documents called | Document in filtered list with reason "Public comment" |
| DocumentFilter/TS-06 | Representations filtered | Doc with type "Representation from resident" | filter_documents called | Document in filtered list with reason "Public comment" |
| DocumentFilter/TS-07 | Unknown type defaults to allow | Doc with type None or "Misc Document" | filter_documents called | Document in allowed list (fail-safe) |
| DocumentFilter/TS-08 | Case insensitive matching | Doc with type "PLANNING STATEMENT" | filter_documents called | Document in allowed list (case normalized) |
| DocumentFilter/TS-09 | Partial pattern matching | Doc with type "Supporting Planning Statement" | filter_documents called | Document in allowed list (contains "planning statement") |
| DocumentFilter/TS-10 | Skip filter override | Any document type | filter_documents called with skip_filter=true | All documents in allowed list, filtered list is empty |

### FilteredDocumentInfo

**Description:** Data model representing a document that was filtered out, including the reason why it was filtered. Used in the filtered_documents response array.

**Users:** DocumentFilter.filter_documents, CherwellScraperMCP._download_all_documents

**Kind:** Dataclass

**Location:** `src/mcp_servers/cherwell_scraper/filters.py`

**Requirements References**
- [document-filtering:FR-006]: Provides structure for filtered_documents array elements

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| FilteredDocumentInfo/TS-01 | Convert to dict | FilteredDocumentInfo instance | to_dict() called | Returns dict with document_id, description, document_type, filter_reason |
| FilteredDocumentInfo/TS-02 | None values handled | FilteredDocumentInfo with document_type=None | to_dict() called | Dict includes None value (not omitted) |

---

## Used Components

### DocumentInfo

**Location:** `src/mcp_servers/cherwell_scraper/models.py`

**Provides:** Data model for document metadata including document_type field populated by parser. Has to_dict() method for serialization.

**Used By:** DocumentFilter (reads document_type field), CherwellScraperMCP (passes to filter, includes in response)

### CherwellParser

**Location:** `src/mcp_servers/cherwell_scraper/parsers.py`

**Provides:** Parses Cherwell portal HTML and extracts document metadata including document type/category field. Returns list of DocumentInfo objects.

**Used By:** CherwellScraperMCP._list_application_documents (which is called by _download_all_documents)

### structlog.get_logger

**Location:** `structlog` package

**Provides:** Structured logging with context fields for audit trail. Already used throughout the scraper.

**Used By:** DocumentFilter (logs all filter decisions), CherwellScraperMCP (logs filtering summary)

---

## Documentation Considerations

- Update MCP server docstring to mention filtering behavior
- Add inline comments to DocumentFilter explaining each rule category
- Update README.md section on Cherwell scraper to document skip_filter parameter
- No API docs changes needed (internal MCP tool)

---

## Instrumentation

| Requirement | Observability Criteria | Implementation | Component |
|-------------|------------------------|----------------|-----------|
| [document-filtering:NFR-001] | Filter decision time < 10ms per document | Add timer around filter_documents(), log total_filter_time_ms at INFO level | DocumentFilter |
| [document-filtering:NFR-003] | 100% of filter decisions logged at INFO level with structured fields | Log each document with: document_id, document_type, decision (allow/skip), filter_reason, application_ref | DocumentFilter._should_download |

---

## Integration Test Scenarios

| ID | Scenario | Given | When | Then | Components Involved |
|----|----------|-------|------|------|---------------------|
| ITS-01 | End-to-end filtering with real Cherwell data | Test application with known document types | MCP tool download_all_documents called via MCP client | Correct documents downloaded, public comments filtered, response has accurate counts | CherwellParser, DocumentFilter, CherwellScraperMCP, CherwellClient |
| ITS-02 | Filter override works end-to-end | Same test application | MCP tool called with skip_filter=true | All documents downloaded including public comments | CherwellParser, DocumentFilter, CherwellScraperMCP, CherwellClient |

---

## E2E Test Scenarios

| ID | Scenario | Given | When | Then | User Journey |
|----|----------|-------|------|------|--------------|
| E2E-01 | Orchestrator receives only relevant documents | Orchestrator initiates review for application with mixed document types | Orchestrator calls download_all_documents → documents ingested to ChromaDB | Only policy-relevant documents are in vector DB, public comments excluded | 1. Orchestrator calls MCP tool<br/>2. Scraper filters docs<br/>3. Relevant docs downloaded<br/>4. Ingestion proceeds with clean dataset |

---

## Test Data

**Requirements:**
- Real Cherwell application HTML with diverse document types
- Fixture files representing different document categories:
  - Core: Planning Statement, Design & Access Statement, Proposed Plans
  - Technical: Transport Assessment, Heritage Statement, Flood Risk Assessment
  - Officer: Officer Report, Committee Report, Decision Notice
  - Public: Public Comment, Objection Letter, Representation from resident
  - Edge cases: documents with no type, unknown types, mixed case types

**Sources:**
- Scrape real applications from Cherwell portal for test fixtures
- Store in `tests/fixtures/cherwell/applications/` with different document mixes
- Document the source application references in fixture README

---

## Test Feasibility

All tests are feasible with current infrastructure:
- Unit tests: pytest with standard mocking
- Integration tests: Use real Cherwell HTML fixtures (already have fixture structure)
- E2E tests: Can be simulated with mocked MCP client and file system

No missing infrastructure or blocked dependencies.

---

## Risks and Dependencies

**Technical Risks:**

1. **Document type naming variations**
   - Risk: Cherwell portal may use inconsistent or unexpected document type names
   - Mitigation: Use case-insensitive substring matching, fail-safe default to allow
   - Mitigation: Log unknown document types at WARNING level for monitoring

2. **Breaking change risk**
   - Risk: Enabling filtering by default could break existing workflows expecting all documents
   - Mitigation: Provide skip_filter override, document the change clearly
   - Mitigation: Default behavior is still safe (downloads unknown types)

**External Dependencies:**
- None - filtering is purely in-memory based on existing metadata

**Assumptions:**
- Cherwell portal document type field is consistently populated (if not, defaults to download)
- Document type categorization is stable across different Cherwell portal versions

**Constraints:**
- Filter rules are code-based (not database/config) for simplicity
- Filtering only uses document_type metadata (not description or filename)

---

## Feasibility Review

No large missing features or infrastructure. This is a self-contained enhancement to the existing scraper.

---

## Task Breakdown

> **CRITICAL: Tests are written WITH implementation, not after.**
> Each task includes writing tests as part of implementation.

### Phase 1: Core Filtering Implementation

- Task 1: Create DocumentFilter class with filter rules
  - Status: Backlog
  - Create `src/mcp_servers/cherwell_scraper/filters.py`
  - Implement DocumentFilter class with:
    - ALLOWLIST_PATTERNS (core docs, assessments, officer docs)
    - DENYLIST_PATTERNS (public comments)
    - filter_documents() method
    - _should_download() helper
  - Create FilteredDocumentInfo dataclass
  - Add structured logging for each filter decision
  - Write unit tests covering all test scenarios for DocumentFilter (TS-01 through TS-10)
  - Write unit tests for FilteredDocumentInfo (TS-01, TS-02)
  - Requirements: [document-filtering:FR-001], [document-filtering:FR-002], [document-filtering:FR-003], [document-filtering:FR-004], [document-filtering:NFR-001], [document-filtering:NFR-002], [document-filtering:NFR-004]
  - Test Scenarios: [document-filtering:DocumentFilter/TS-01], [document-filtering:DocumentFilter/TS-02], [document-filtering:DocumentFilter/TS-03], [document-filtering:DocumentFilter/TS-04], [document-filtering:DocumentFilter/TS-05], [document-filtering:DocumentFilter/TS-06], [document-filtering:DocumentFilter/TS-07], [document-filtering:DocumentFilter/TS-08], [document-filtering:DocumentFilter/TS-09], [document-filtering:DocumentFilter/TS-10], [document-filtering:FilteredDocumentInfo/TS-01], [document-filtering:FilteredDocumentInfo/TS-02]

- Task 2: Extend DownloadAllDocumentsInput schema
  - Status: Backlog
  - Add `skip_filter: bool = Field(default=False)` to DownloadAllDocumentsInput
  - Update tool description to document the parameter
  - Write unit tests for schema validation (TS-01, TS-02, TS-03)
  - Requirements: [document-filtering:FR-005], [document-filtering:FR-007]
  - Test Scenarios: [document-filtering:DownloadAllDocumentsInput/TS-01], [document-filtering:DownloadAllDocumentsInput/TS-02], [document-filtering:DownloadAllDocumentsInput/TS-03]

- Task 3: Modify _download_all_documents to use filter
  - Status: Backlog
  - Import and instantiate DocumentFilter
  - After getting document list, call filter.filter_documents()
  - Iterate only over filtered allowed_documents for downloading
  - Build filtered_documents response array from filter results
  - Add total_documents, downloaded_count, filtered_count to response
  - Add logging for filtering summary with counts
  - Write unit tests for modified method (TS-01, TS-02, TS-03, TS-04)
  - Requirements: [document-filtering:FR-001], [document-filtering:FR-005], [document-filtering:FR-006], [document-filtering:NFR-003]
  - Test Scenarios: [document-filtering:CherwellScraperMCP/TS-01], [document-filtering:CherwellScraperMCP/TS-02], [document-filtering:CherwellScraperMCP/TS-03], [document-filtering:CherwellScraperMCP/TS-04]

### Phase 2: Integration Testing and Documentation

- Task 4: Add integration tests with real Cherwell fixtures
  - Status: Backlog
  - Create test fixtures in `tests/fixtures/cherwell/` with diverse document types
  - Write integration test for ITS-01 (end-to-end filtering)
  - Write integration test for ITS-02 (filter override)
  - Document fixture sources in fixture README
  - Requirements: [document-filtering:NFR-002]
  - Test Scenarios: [document-filtering:ITS-01], [document-filtering:ITS-02]

- Task 5: Add instrumentation and E2E test
  - Status: Backlog
  - Add timer around filter_documents() call, log total_filter_time_ms
  - Write E2E test simulating orchestrator workflow (E2E-01)
  - Verify logs contain all required structured fields
  - Requirements: [document-filtering:NFR-001], [document-filtering:NFR-003]
  - Test Scenarios: [document-filtering:E2E-01]

- Task 6: Update documentation
  - Status: Backlog
  - Add inline comments to filter rules explaining each category
  - Update MCP server docstring to mention filtering
  - Update README section on Cherwell scraper
  - No test scenarios (documentation only)

---

## Intermediate Dead Code Tracking

No dead code introduced. All code is used immediately within the same phase.

---

## Intermediate Stub Tracking

No stubs permitted. All tests are fully implemented with their tasks.

---

## Requirements Validation

- [document-filtering:FR-001]: Filter Public Comments by Document Type
  - Phase 1 Task 1: DocumentFilter class with denylist
  - Phase 1 Task 3: _download_all_documents applies filter

- [document-filtering:FR-002]: Download Core Application Documents
  - Phase 1 Task 1: DocumentFilter allowlist for core docs

- [document-filtering:FR-003]: Download Technical Assessment Documents
  - Phase 1 Task 1: DocumentFilter allowlist for assessments

- [document-filtering:FR-004]: Download Officer and Decision Documents
  - Phase 1 Task 1: DocumentFilter allowlist for officer/decision docs

- [document-filtering:FR-005]: Override Filter with Download All Flag
  - Phase 1 Task 2: Add skip_filter parameter
  - Phase 1 Task 3: Honor skip_filter in _download_all_documents

- [document-filtering:FR-006]: Report Filtered Documents
  - Phase 1 Task 3: Build filtered_documents array with reasons and counts

- [document-filtering:FR-007]: Backward Compatible Filtering
  - Phase 1 Task 2: Default skip_filter=false enables filtering by default

- [document-filtering:NFR-001]: Filter Performance
  - Phase 1 Task 1: In-memory pattern matching
  - Phase 2 Task 5: Add timing instrumentation

- [document-filtering:NFR-002]: Filter Reliability
  - Phase 1 Task 1: Fail-safe default for unknown types
  - Phase 2 Task 4: Integration tests with diverse document types

- [document-filtering:NFR-003]: Filter Auditability
  - Phase 1 Task 1: Structured logging in DocumentFilter
  - Phase 1 Task 3: Logging in _download_all_documents
  - Phase 2 Task 5: Verify logs in E2E test

- [document-filtering:NFR-004]: Filter Rule Clarity
  - Phase 1 Task 1: All rules in one module with comments
  - Phase 2 Task 6: Inline comments explaining each rule

---

## Test Scenario Validation

### Component Scenarios
- [document-filtering:DocumentFilter/TS-01]: Phase 1 Task 1
- [document-filtering:DocumentFilter/TS-02]: Phase 1 Task 1
- [document-filtering:DocumentFilter/TS-03]: Phase 1 Task 1
- [document-filtering:DocumentFilter/TS-04]: Phase 1 Task 1
- [document-filtering:DocumentFilter/TS-05]: Phase 1 Task 1
- [document-filtering:DocumentFilter/TS-06]: Phase 1 Task 1
- [document-filtering:DocumentFilter/TS-07]: Phase 1 Task 1
- [document-filtering:DocumentFilter/TS-08]: Phase 1 Task 1
- [document-filtering:DocumentFilter/TS-09]: Phase 1 Task 1
- [document-filtering:DocumentFilter/TS-10]: Phase 1 Task 1
- [document-filtering:FilteredDocumentInfo/TS-01]: Phase 1 Task 1
- [document-filtering:FilteredDocumentInfo/TS-02]: Phase 1 Task 1
- [document-filtering:DownloadAllDocumentsInput/TS-01]: Phase 1 Task 2
- [document-filtering:DownloadAllDocumentsInput/TS-02]: Phase 1 Task 2
- [document-filtering:DownloadAllDocumentsInput/TS-03]: Phase 1 Task 2
- [document-filtering:CherwellScraperMCP/TS-01]: Phase 1 Task 3
- [document-filtering:CherwellScraperMCP/TS-02]: Phase 1 Task 3
- [document-filtering:CherwellScraperMCP/TS-03]: Phase 1 Task 3
- [document-filtering:CherwellScraperMCP/TS-04]: Phase 1 Task 3

### Integration Scenarios
- [document-filtering:ITS-01]: Phase 2 Task 4
- [document-filtering:ITS-02]: Phase 2 Task 4

### E2E Scenarios
- [document-filtering:E2E-01]: Phase 2 Task 5

---

## Appendix

### Glossary
- **Allowlist:** Set of document type patterns that should always be downloaded
- **Denylist:** Set of document type patterns that should be filtered out
- **Fail-safe default:** Behavior when document type is unknown - defaults to downloading the document
- **Filter reason:** Human-readable explanation of why a document was filtered, included in response for auditability

### References
- [document-filtering specification](specification.md) - Requirements and acceptance criteria
- [foundation-api design](../foundation-api/design.md) - Cherwell scraper architecture
- [Cherwell Planning Register](https://planningregister.cherwell.gov.uk/) - Source system

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-07 | Claude Opus 4.6 | Initial design |

---
