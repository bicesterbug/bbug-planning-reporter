# Specification: Document Type Detection

**Version:** 1.0
**Date:** 2026-02-14
**Status:** Implemented

---

## Problem Statement

The system downloads and attempts to ingest all selected documents into the vector store, including architectural plans, elevations, and site drawings that are image-only PDFs. These documents produce garbage text when OCR'd (dimension labels, arrows, annotations), which pollutes vector search results and wastes processing time. There is currently no mechanism to detect that a document is image-based and skip vector ingestion while still retaining the document for reference.

## Beneficiaries

**Primary:**
- The review agent — receives cleaner vector search results without garbage OCR text from drawings, leading to more accurate reviews

**Secondary:**
- Operators — fewer misleading ingestion "failures" in logs and fewer wasted OCR cycles
- End users — faster review completion (no time spent on futile OCR of plans)

---

## Outcomes

**Must Haves**
- Image-heavy PDFs (plans, elevations, sections, drawings) are detected after download and excluded from vector ingestion
- Detected image documents are still downloaded and uploaded to S3 (when configured) for archival
- The review output includes metadata listing which plans/drawings were submitted but not text-searched
- Text-based PDFs (transport assessments, planning statements, officer reports) continue to be ingested as before

**Nice-to-haves**
- The detection threshold is configurable via environment variable so operators can tune sensitivity

---

## Explicitly Out of Scope

- Pre-download LLM-based detection of image documents (unreliable since "Site Plan" could be either a text document or a drawing)
- Running OCR on image-heavy documents to attempt text extraction (user decision: skip OCR entirely on suspected plans to save time)
- Multimodal/vision-model analysis of plan content (future enhancement)
- Changes to the pre-download LLM document filter (it already excludes some visual types; this feature handles what slips through)
- Changes to how non-PDF image files (.png, .jpg) are handled (these are already gated by OCR availability)

---

## Functional Requirements

### FR-001: Image Ratio Detection
**Description:** After a PDF is downloaded and before text extraction, the system must compute the image-to-page-area ratio for each page. If the average image ratio across all pages exceeds a threshold (default 0.7), the document is classified as "image-based".

**Examples:**
- Positive case: A site plan PDF with 3 pages of architectural drawings (95% image area) is classified as image-based
- Edge case: A transport assessment PDF with one page of diagrams (80% images) but 40 pages of text (5% images each) has an average ratio of ~0.07 and is classified as text-based
- Edge case: A single-page site plan with an image occupying 65% of the page (below 0.7 threshold) is classified as text-based and ingested normally

### FR-002: Skip Vector Ingestion for Image Documents
**Description:** Documents classified as image-based must not be chunked, embedded, or stored in the vector database. The ingestion tool must return a distinct status indicating the document was skipped due to being image-based, not because of an error.

**Examples:**
- Positive case: `ingest_document` returns `{"status": "skipped", "reason": "image_based", "image_ratio": 0.92}` for a site plan
- Negative case: A text PDF still returns `{"status": "success", "chunks_created": 47}` as before

### FR-003: Retain Image Documents for Download/Archival
**Description:** Image-based documents must still be downloaded to local storage and uploaded to S3 (when configured). They are not deleted after the skip decision. The orchestrator must track these separately from ingested documents and from failed documents.

**Examples:**
- Positive case: Site plan is downloaded, uploaded to S3, but not ingested. Download record is preserved.
- Negative case: Site plan is NOT deleted from local disk when S3 upload succeeds (same as current text documents)

### FR-004: Plans Submitted Metadata in Review Output
**Description:** The review result must include a list of documents that were detected as image-based and skipped from vector ingestion. Each entry must include the document description, document type (if known), and URL. This list is available to the agent as context during review generation.

**Examples:**
- Positive case: Review metadata includes `"plans_submitted": [{"description": "Site Plan", "url": "https://...", "document_type": "Site Plans", "image_ratio": 0.94}]`
- Edge case: If no documents are image-based, the list is empty

### FR-005: Agent Context for Skipped Plans
**Description:** When generating the review, the agent must be informed which plans/drawings were submitted so it can reference their existence (e.g., "A site plan was submitted") even though they were not searchable via the vector store.

**Examples:**
- Positive case: The review prompt includes a section listing skipped plan documents by name
- Edge case: No plans were skipped — the section is omitted or empty

---

## Non-Functional Requirements

### NFR-001: Detection Performance
**Category:** Performance
**Description:** Image ratio computation must not significantly increase ingestion time for text-based documents.
**Acceptance Threshold:** Image ratio detection adds < 500ms per document (most PDFs are already opened by PyMuPDF for text extraction)
**Verification:** Testing — benchmark test comparing ingestion time with and without detection

### NFR-002: Configurable Threshold
**Category:** Maintainability
**Description:** The image ratio threshold for classifying a document as image-based must be configurable via environment variable.
**Acceptance Threshold:** `IMAGE_RATIO_THRESHOLD` environment variable with default 0.7, accepted range 0.0-1.0
**Verification:** Testing — unit test confirming env var override

### NFR-003: Logging
**Category:** Maintainability
**Description:** Each document classification decision (text-based or image-based) must be logged with the computed image ratio, page count, and decision outcome.
**Acceptance Threshold:** INFO-level log entry for every document processed, including image ratio and classification result
**Verification:** Observability — log inspection during review runs

---

## Open Questions

None

---

## Appendix

### Glossary
- **Image ratio:** The proportion of page area occupied by embedded images in a PDF, computed per-page and averaged across all pages. A ratio of 1.0 means the entire page is images; 0.0 means no images.
- **Image-based document:** A PDF where the average image ratio exceeds the threshold (default 0.7), indicating it consists primarily of drawings, plans, or photographs rather than extractable text.
- **Plans submitted:** A metadata field in the review output listing image-based documents that were downloaded but not ingested into the vector store.

### References
- PyMuPDF `page.get_image_info()` for computing image coverage
- Current image ratio computation in `src/mcp_servers/document_store/processor.py`

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-14 | Claude | Initial specification |
