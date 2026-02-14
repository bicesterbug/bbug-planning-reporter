# Design: Document Type Detection

**Version:** 1.0
**Date:** 2026-02-14
**Status:** Implemented
**Linked Specification** `.sdd/document-type-detection/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context
The document ingestion flow is: Download (scraper) -> Ingest (document-store MCP) -> Chunk -> Embed -> Store (ChromaDB). During ingestion, `DocumentProcessor.extract_text()` opens the PDF with PyMuPDF and computes `image_ratio` per page via `_calculate_image_ratio()`. Pages with image_ratio > `IMAGE_HEAVY_THRESHOLD` (0.7) are flagged `contains_drawings=True`, but this flag is informational only — the document is still fully extracted, chunked, embedded, and stored regardless.

Image-heavy documents (architectural plans, elevations, site drawings) produce garbage text when OCR'd (dimension labels, arrows, annotations), which pollutes vector search results and wastes processing time on futile extraction, chunking, and embedding.

### Proposed Architecture
Add a classification gate in the ingestion pipeline between file validation and text extraction. A new `classify_document()` method on `DocumentProcessor` opens the PDF, computes the per-page image ratio (reusing `_calculate_image_ratio()`), averages across all pages, and returns whether the document is image-based. If classified as image-based, `_ingest_document()` in the document store server returns `{"status": "skipped", "reason": "image_based"}` without proceeding to extraction, chunking, or embedding.

The orchestrator tracks skipped documents separately from ingested and failed documents, and passes a `plans_submitted` list to the review generation prompts so the agent can reference submitted plans even though they were not searchable.

### Technology Decisions
- Reuse existing `_calculate_image_ratio()` method on `DocumentProcessor` — no new dependencies
- Classification threshold configurable via `IMAGE_RATIO_THRESHOLD` environment variable (default 0.7, matching existing `IMAGE_HEAVY_THRESHOLD`)
- New `DocumentClassification` dataclass for clean return type from classification method

### Quality Attributes
- Minimal code change — adds a gate before existing extraction, does not change the extraction pipeline itself
- No performance regression for text documents — classification adds < 500ms (only opens the PDF and reads image metadata, no OCR or text extraction)
- Backwards compatible — text documents continue to flow through the existing pipeline unchanged

---

## API Design

N/A — no public API changes. The `ingest_document` MCP tool gains a new return status (`"skipped"`) but this is an internal tool interface, not a public API.

---

## Modified Components

### `DocumentProcessor` in `processor.py`
**Change Description** Currently defines `IMAGE_HEAVY_THRESHOLD = 0.7` as a class constant and computes image ratios during page extraction only. Change to: (1) read threshold from `IMAGE_RATIO_THRESHOLD` env var with fallback to 0.7, (2) add `classify_document()` method that opens a PDF, computes average image ratio across all pages, and returns a `DocumentClassification` result indicating whether the document is image-based.

**Dependants** `DocumentStoreMCP._ingest_document` will call the new method before `extract_text()`.

**Kind** Class

**Requirements References**
- [document-type-detection:FR-001]: Image ratio detection — requires computing average ratio across all pages and comparing to threshold
- [document-type-detection:NFR-001]: Detection must add < 500ms — classification reuses lightweight `_calculate_image_ratio()` without OCR
- [document-type-detection:NFR-002]: Configurable threshold — reads `IMAGE_RATIO_THRESHOLD` env var
- [document-type-detection:NFR-003]: Logging — classification decision logged at INFO level

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| DocumentProcessor/TS-01 | Image-based PDF detected | A PDF where all pages have image ratio > 0.7 | `classify_document()` is called | Returns `is_image_based=True` with correct average ratio |
| DocumentProcessor/TS-02 | Text-based PDF detected | A PDF where pages have image ratio < 0.7 | `classify_document()` is called | Returns `is_image_based=False` with correct average ratio |
| DocumentProcessor/TS-03 | Mixed PDF classified as text | A PDF with one image-heavy page (0.9) and many text pages (0.05 each), average < 0.7 | `classify_document()` is called | Returns `is_image_based=False` |
| DocumentProcessor/TS-04 | Threshold from env var | `IMAGE_RATIO_THRESHOLD=0.5` set in environment | `DocumentProcessor` is instantiated | Threshold is 0.5 instead of default 0.7 |
| DocumentProcessor/TS-05 | Non-PDF file skips classification | An image file (.png) is passed | `classify_document()` is called | Returns `is_image_based=False` (classification only applies to PDFs) |
| DocumentProcessor/TS-06 | Corrupt PDF handled gracefully | A corrupt PDF that cannot be opened | `classify_document()` is called | Returns `is_image_based=False` with ratio 0.0 (falls through to normal extraction error handling) |

### `DocumentStoreMCP._ingest_document` in `server.py`
**Change Description** Currently proceeds directly from file validation to text extraction. Change to: after file validation and duplicate check, call `processor.classify_document()`. If the document is classified as image-based, return `{"status": "skipped", "reason": "image_based", "image_ratio": <ratio>, "total_pages": <pages>}` immediately without extraction, chunking, or embedding.

**Dependants** `AgentOrchestrator._phase_ingest_documents` must handle the new `"skipped"` status.

**Kind** Method

**Requirements References**
- [document-type-detection:FR-001]: Uses classification result to gate ingestion
- [document-type-detection:FR-002]: Returns `"skipped"` status for image-based documents

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| DocumentStoreMCP/TS-01 | Image-based PDF skipped | A PDF classified as image-based (ratio > threshold) | `ingest_document` tool is called | Returns `{"status": "skipped", "reason": "image_based", "image_ratio": ...}` |
| DocumentStoreMCP/TS-02 | Text-based PDF ingested normally | A PDF classified as text-based (ratio < threshold) | `ingest_document` tool is called | Returns `{"status": "success", "chunks_created": ...}` as before |
| DocumentStoreMCP/TS-03 | Already-ingested check before classification | A PDF that was already ingested | `ingest_document` tool is called | Returns `"already_ingested"` (duplicate check happens before classification) |

### `DocumentIngestionResult` in `orchestrator.py`
**Change Description** Currently has `documents_fetched`, `documents_ingested`, `failed_documents`, `document_paths`, and `document_metadata` fields. Add `skipped_documents: list[dict[str, Any]]` field to track documents that were skipped due to being image-based, including their description, document_type, url, and image_ratio.

**Dependants** `_phase_ingest_documents`, `_build_evidence_context`, `_phase_generate_review`

**Kind** Dataclass

**Requirements References**
- [document-type-detection:FR-003]: Skipped documents tracked separately from ingested and failed
- [document-type-detection:FR-004]: Plans submitted metadata available for review output

**Test Scenarios**

N/A — dataclass change verified through integration scenarios.

### `AgentOrchestrator._phase_ingest_documents` in `orchestrator.py`
**Change Description** Currently handles `"success"` and `"already_ingested"` as successful statuses and everything else as failures. Change to: also handle `"skipped"` status by adding the document to `skipped_documents` with its metadata (description, document_type, url, image_ratio). Skipped documents do not count as failures and do not count toward the "no documents ingested" check.

**Dependants** None

**Kind** Method

**Requirements References**
- [document-type-detection:FR-002]: Skipped documents handled distinctly from errors
- [document-type-detection:FR-003]: Skipped documents still have their download and S3 records preserved

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| AgentOrchestrator/TS-01 | Skipped doc tracked separately | `ingest_document` returns `{"status": "skipped", "reason": "image_based"}` for one document | Ingestion phase completes | Document appears in `skipped_documents`, not in `failed_documents`, and does not increment `documents_ingested` |
| AgentOrchestrator/TS-02 | All docs skipped but some ingested | 3 documents: 1 ingested, 2 skipped | Ingestion phase completes | `documents_ingested=1`, `skipped_documents` has 2 entries, no error raised |
| AgentOrchestrator/TS-03 | All docs skipped, none ingested | All documents return `"skipped"` | Ingestion phase completes | Error raised: "No documents could be ingested" (same as current behaviour for zero ingested docs) |

### `AgentOrchestrator._build_evidence_context` in `orchestrator.py`
**Change Description** Currently returns a 4-tuple `(app_summary, ingested_docs_text, app_evidence_text, policy_evidence_text)`. Change to: return a 5-tuple adding `plans_submitted_text`. If `skipped_documents` is non-empty, format each as a bullet point with description, document type, and image ratio. If empty, return `"No plans or drawings were detected."`.

**Dependants** `_phase_generate_review` must unpack the new 5-tuple.

**Kind** Method

**Requirements References**
- [document-type-detection:FR-004]: Plans submitted list available for review
- [document-type-detection:FR-005]: Agent receives list of skipped plans

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| AgentOrchestrator/TS-04 | Plans submitted text generated | 2 documents in `skipped_documents` | `_build_evidence_context()` is called | Returns 5th element with formatted bullet list of skipped docs |
| AgentOrchestrator/TS-05 | No plans submitted | `skipped_documents` is empty | `_build_evidence_context()` is called | Returns 5th element as `"No plans or drawings were detected."` |

### `AgentOrchestrator._phase_generate_review` in `orchestrator.py`
**Change Description** Currently calls `_build_evidence_context()` for a 4-tuple and passes the context to `build_structure_prompt` and `build_report_prompt`. Change to: unpack the 5-tuple, pass `plans_submitted_text` to both prompt builders, and include `plans_submitted` list in the review result metadata.

**Dependants** None

**Kind** Method

**Requirements References**
- [document-type-detection:FR-004]: Plans submitted included in review output
- [document-type-detection:FR-005]: Plans context passed to agent during review generation

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| AgentOrchestrator/TS-06 | Plans metadata in review result | Skipped documents exist | Review generation completes | `result.metadata["plans_submitted"]` contains list of skipped doc metadata |
| AgentOrchestrator/TS-07 | Empty plans metadata | No skipped documents | Review generation completes | `result.metadata["plans_submitted"]` is an empty list |

### `build_structure_prompt` in `structure_prompt.py`
**Change Description** Currently accepts 4 parameters (app_summary, ingested_docs_text, app_evidence_text, policy_evidence_text). Add a 5th parameter `plans_submitted_text` and include it in the user prompt so the LLM knows which plans/drawings were submitted but not searchable.

**Dependants** None

**Kind** Function

**Requirements References**
- [document-type-detection:FR-005]: Agent informed about submitted plans

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| build_structure_prompt/TS-01 | Plans section included in prompt | Non-empty plans_submitted_text | `build_structure_prompt()` is called | User prompt contains a "Plans & Drawings Submitted" section with the text |
| build_structure_prompt/TS-02 | Plans section with no plans | plans_submitted_text is "No plans or drawings were detected." | `build_structure_prompt()` is called | User prompt still contains the section with the no-plans text |

### `build_report_prompt` in `report_prompt.py`
**Change Description** Currently accepts 5 parameters (structure_json, app_summary, ingested_docs_text, app_evidence_text, policy_evidence_text). Add a 6th parameter `plans_submitted_text` and include it in the user prompt.

**Dependants** None

**Kind** Function

**Requirements References**
- [document-type-detection:FR-005]: Agent informed about submitted plans during report writing

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| build_report_prompt/TS-01 | Plans section in report prompt | Non-empty plans_submitted_text | `build_report_prompt()` is called | User prompt contains a "Plans & Drawings Submitted" section |

---

## Added Components

### `DocumentClassification` dataclass in `processor.py`
**Description** Return type for `classify_document()`. Contains: `is_image_based` (bool), `average_image_ratio` (float), `page_count` (int), `page_ratios` (list[float]).

**Users** `DocumentStoreMCP._ingest_document`

**Kind** Dataclass

**Location** `src/mcp_servers/document_store/processor.py` (alongside `PageExtraction` and `DocumentExtraction`)

**Requirements References**
- [document-type-detection:FR-001]: Encapsulates the classification result with all required data (ratio, page count)
- [document-type-detection:NFR-003]: Provides data for the INFO-level log entry

**Test Scenarios**

N/A — simple dataclass, verified through `DocumentProcessor` tests.

---

## Used Components

### `_calculate_image_ratio` method on `DocumentProcessor`
**Location** `src/mcp_servers/document_store/processor.py:287-320`

**Provides** Per-page image-to-area ratio computation using `page.get_images()` and `page.rect`. Already handles errors gracefully (returns 0.0).

**Used By** New `classify_document()` method on `DocumentProcessor`

### `DocumentIngestionResult` dataclass
**Location** `src/agent/orchestrator.py:72-80`

**Provides** Tracks ingestion results including fetched, ingested, failed counts and document metadata.

**Used By** Modified `_phase_ingest_documents` and `_build_evidence_context` methods

### PyMuPDF (`fitz`)
**Location** External dependency (already installed)

**Provides** PDF opening and page-level image analysis. `fitz.open()` opens a PDF; `page.get_images()` lists embedded images.

**Used By** `classify_document()` method

---

## Documentation Considerations
- None — internal feature change. No public API modifications.

---

## Instrumentation (if needed)

| Requirement | Observability Criteria | Implementation | Component |
|-------------|------------------------|----------------|-----------|
| [document-type-detection:NFR-003] | Every document classification decision logged with image ratio, page count, and result | `logger.info("Document classified", image_ratio=..., page_count=..., is_image_based=..., file_path=...)` | `DocumentProcessor.classify_document()` |
| [document-type-detection:NFR-003] | Skipped document logged during ingestion | `logger.info("Document skipped (image-based)", ...)` | `DocumentStoreMCP._ingest_document()` |

---

## Integration Test Scenarios (if needed)

| ID | Scenario | Given | When | Then | Components Involved |
|----|----------|-------|------|------|---------------------|
| ITS-01 | Image-based PDF skipped end-to-end | A test PDF with high image ratio exists on disk | `ingest_document` MCP tool is called | Returns `{"status": "skipped", "reason": "image_based"}` with correct ratio | `DocumentProcessor`, `DocumentStoreMCP` |
| ITS-02 | Text PDF unaffected by classification gate | A normal text PDF exists on disk | `ingest_document` MCP tool is called | Returns `{"status": "success"}` with chunks as before | `DocumentProcessor`, `DocumentStoreMCP` |

---

## E2E Test Scenarios (if needed)

| ID | Scenario | Given | When | Then | User Journey |
|----|----------|-------|------|------|--------------|
| E2E-01 | Review with mixed document types | Application has text docs and image plans | Full review workflow runs | Text docs ingested, image docs skipped, review includes `plans_submitted` metadata | Submit review -> download -> classify -> ingest (skip plans) -> analyse -> generate (with plans context) -> verify |

Note: E2E-01 requires either real planning application documents or carefully crafted test PDFs — manual verification recommended.

---

## Test Data
- Create a synthetic image-heavy PDF (a page filled with a large image occupying >70% of area) using PyMuPDF or reportlab in test fixtures
- Create a synthetic text-heavy PDF (multiple pages of text content) for the negative case
- Create a mixed PDF (one image page + several text pages) for the edge case
- Existing test PDFs in `tests/fixtures/` can be used for text-based regression testing

---

## Test Feasibility
- All unit tests use mock PDFs or mock the `fitz.open()` call — no external dependencies
- Integration tests (ITS-01, ITS-02) require synthetic PDFs on disk — can be generated in test setup
- E2E-01 requires a full orchestrator setup with MCP mocks — existing test infrastructure in `tests/test_agent/` supports this pattern

---

## Risks and Dependencies
- **Low risk**: `_calculate_image_ratio()` may not perfectly detect all image-heavy documents (e.g., a plan with extensive text annotations could have a ratio below threshold). Mitigation: threshold is configurable, and the pre-download LLM filter already excludes obvious visual document types.
- **Low risk**: Changing the `_build_evidence_context` return type from 4-tuple to 5-tuple could break any direct unpacking. Mitigation: the method is only called in one place (`_phase_generate_review`), which is also being modified.
- **No external dependencies**: Uses only existing PyMuPDF and standard library.

---

## Feasability Review
- No blockers. All required infrastructure (PyMuPDF, image ratio computation, orchestrator tracking) already exists.

---

## Task Breakdown

### Phase 1: Image detection and ingestion skip

- Task 1: Add `DocumentClassification` dataclass and `classify_document()` method to `DocumentProcessor`, make threshold configurable via `IMAGE_RATIO_THRESHOLD` env var
  - Status: Done
  - Add `DocumentClassification` dataclass with `is_image_based`, `average_image_ratio`, `page_count`, `page_ratios` fields. Add `classify_document(file_path)` method that opens the PDF, iterates pages calling `_calculate_image_ratio()`, computes the average, and returns classification. Read threshold from env var in `__init__`. Log classification at INFO level. Write tests for all scenarios including edge cases.
  - Requirements: [document-type-detection:FR-001], [document-type-detection:NFR-001], [document-type-detection:NFR-002], [document-type-detection:NFR-003]
  - Test Scenarios: [document-type-detection:DocumentProcessor/TS-01], [document-type-detection:DocumentProcessor/TS-02], [document-type-detection:DocumentProcessor/TS-03], [document-type-detection:DocumentProcessor/TS-04], [document-type-detection:DocumentProcessor/TS-05], [document-type-detection:DocumentProcessor/TS-06]

- Task 2: Add `"skipped"` status handling to `_ingest_document` in document store server
  - Status: Done
  - After file validation and duplicate check, call `processor.classify_document()`. If `is_image_based` is True, return `{"status": "skipped", "reason": "image_based", "image_ratio": ..., "total_pages": ...}` without proceeding to extraction. Log the skip. Write tests for skip, normal ingestion, and duplicate check ordering.
  - Requirements: [document-type-detection:FR-001], [document-type-detection:FR-002]
  - Test Scenarios: [document-type-detection:DocumentStoreMCP/TS-01], [document-type-detection:DocumentStoreMCP/TS-02], [document-type-detection:DocumentStoreMCP/TS-03], [document-type-detection:ITS-01], [document-type-detection:ITS-02]

### Phase 2: Orchestrator and prompt integration

- Task 3: Track skipped documents in orchestrator and pass to review prompts
  - Status: Done
  - Add `skipped_documents` field to `DocumentIngestionResult`. In `_phase_ingest_documents`, handle `"skipped"` status by adding doc metadata to `skipped_documents`. Update `_build_evidence_context` to return 5-tuple with `plans_submitted_text`. Update `_phase_generate_review` to unpack 5-tuple, pass to both prompt builders, and include `plans_submitted` in review result metadata. Modify `build_structure_prompt` and `build_report_prompt` to accept and include `plans_submitted_text`. Write tests for all orchestrator and prompt scenarios.
  - Requirements: [document-type-detection:FR-002], [document-type-detection:FR-003], [document-type-detection:FR-004], [document-type-detection:FR-005]
  - Test Scenarios: [document-type-detection:AgentOrchestrator/TS-01], [document-type-detection:AgentOrchestrator/TS-02], [document-type-detection:AgentOrchestrator/TS-03], [document-type-detection:AgentOrchestrator/TS-04], [document-type-detection:AgentOrchestrator/TS-05], [document-type-detection:AgentOrchestrator/TS-06], [document-type-detection:AgentOrchestrator/TS-07], [document-type-detection:build_structure_prompt/TS-01], [document-type-detection:build_structure_prompt/TS-02], [document-type-detection:build_report_prompt/TS-01]

---

## Intermediate Dead Code Tracking

N/A — no dead code introduced in intermediate phases. Phase 1 output (`"skipped"` status) is consumed in Phase 2.

---

## Intermediate Stub Tracking

N/A — no stubs required.

---

## Requirements Validation

- [document-type-detection:FR-001]
  - Phase 1 Task 1 (classify_document method)
  - Phase 1 Task 2 (classification gate in _ingest_document)
- [document-type-detection:FR-002]
  - Phase 1 Task 2 (skipped status return)
  - Phase 2 Task 3 (orchestrator handles skipped status)
- [document-type-detection:FR-003]
  - Phase 2 Task 3 (skipped documents tracked separately, download/S3 unaffected)
- [document-type-detection:FR-004]
  - Phase 2 Task 3 (plans_submitted in review result metadata)
- [document-type-detection:FR-005]
  - Phase 2 Task 3 (plans_submitted_text passed to prompts)

- [document-type-detection:NFR-001]
  - Phase 1 Task 1 (lightweight classification using existing _calculate_image_ratio)
- [document-type-detection:NFR-002]
  - Phase 1 Task 1 (IMAGE_RATIO_THRESHOLD env var)
- [document-type-detection:NFR-003]
  - Phase 1 Task 1 (logging in classify_document)
  - Phase 1 Task 2 (logging in _ingest_document skip path)

---

## Test Scenario Validation

### Component Scenarios
- [document-type-detection:DocumentProcessor/TS-01]: Phase 1 Task 1
- [document-type-detection:DocumentProcessor/TS-02]: Phase 1 Task 1
- [document-type-detection:DocumentProcessor/TS-03]: Phase 1 Task 1
- [document-type-detection:DocumentProcessor/TS-04]: Phase 1 Task 1
- [document-type-detection:DocumentProcessor/TS-05]: Phase 1 Task 1
- [document-type-detection:DocumentProcessor/TS-06]: Phase 1 Task 1
- [document-type-detection:DocumentStoreMCP/TS-01]: Phase 1 Task 2
- [document-type-detection:DocumentStoreMCP/TS-02]: Phase 1 Task 2
- [document-type-detection:DocumentStoreMCP/TS-03]: Phase 1 Task 2
- [document-type-detection:AgentOrchestrator/TS-01]: Phase 2 Task 3
- [document-type-detection:AgentOrchestrator/TS-02]: Phase 2 Task 3
- [document-type-detection:AgentOrchestrator/TS-03]: Phase 2 Task 3
- [document-type-detection:AgentOrchestrator/TS-04]: Phase 2 Task 3
- [document-type-detection:AgentOrchestrator/TS-05]: Phase 2 Task 3
- [document-type-detection:AgentOrchestrator/TS-06]: Phase 2 Task 3
- [document-type-detection:AgentOrchestrator/TS-07]: Phase 2 Task 3
- [document-type-detection:build_structure_prompt/TS-01]: Phase 2 Task 3
- [document-type-detection:build_structure_prompt/TS-02]: Phase 2 Task 3
- [document-type-detection:build_report_prompt/TS-01]: Phase 2 Task 3

### Integration Scenarios
- [document-type-detection:ITS-01]: Phase 1 Task 2
- [document-type-detection:ITS-02]: Phase 1 Task 2

### E2E Scenarios
- [document-type-detection:E2E-01]: Manual verification after deployment

---

## Appendix

### Glossary
- **Image ratio**: The proportion of page area occupied by embedded images in a PDF, computed per-page via `_calculate_image_ratio()` and averaged across all pages.
- **Image-based document**: A PDF where the average image ratio exceeds the threshold (default 0.7).
- **Plans submitted**: Metadata field in review output listing image-based documents that were downloaded but not ingested.

### References
- `src/mcp_servers/document_store/processor.py` — existing `_calculate_image_ratio()` at lines 287-320
- `src/mcp_servers/document_store/server.py` — existing `_ingest_document()` at lines 194-367
- `src/agent/orchestrator.py` — existing `_phase_ingest_documents()` at lines 672-771
- PyMuPDF `page.get_images()` documentation

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-14 | Claude | Initial design |
