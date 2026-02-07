# Specification: Document Filtering

**Version:** 1.0
**Date:** 2026-02-07
**Status:** Draft

---

## Problem Statement

The Cherwell scraper currently downloads all documents associated with a planning application, including public comments and objection letters from residents. These documents are not relevant for policy-based technical reviews and add unnecessary processing overhead, storage costs, and noise to the vector database. The system needs intelligent filtering to download only technically relevant documents.

## Beneficiaries

**Primary:**
- System operators who want to reduce storage costs and processing time
- Agent orchestrator which gets cleaner, more relevant context for policy reviews
- End users who receive faster review turnaround times

**Secondary:**
- Developers maintaining the system (fewer edge cases from irrelevant documents)
- Future features that rely on clean, policy-relevant document collections

---

## Outcomes
[Intended outcomes of this work, written from the user/consumer's perspective]

**Must Haves**
- Public comments and resident objection letters are automatically filtered out and not downloaded
- Core application documents (planning statements, design & access statements, drawings) are always downloaded
- Technical assessments (transport, environmental, heritage, flood risk) are always downloaded
- Officer reports, committee reports, decision notices, and conditions documents are always downloaded
- Users can override filtering to download all documents when needed for special cases
- Filtering decisions are transparent and auditable

**Nice-to-haves**
- Configurable filter rules without code changes
- Statistics on filtered vs downloaded documents
- Filter effectiveness metrics (e.g., storage saved, processing time saved)

---

## Explicitly Out of Scope

- Analysis of document content to determine relevance (filtering is based on metadata only)
- Filtering based on file size or format
- Retroactive filtering of already-downloaded documents
- User-specific filtering preferences per review request
- Machine learning or AI-based document classification
- Filtering of policy documents (only application documents are filtered)
- Support for planning portals other than Cherwell (filtering logic is Cherwell-specific)

---

## Functional Requirements

### FR-001: Filter Public Comments by Document Type
**Description:** The system must filter out documents whose document type/category field from the Cherwell portal indicates they are public comments, objections, or representations. The filtering must occur before documents are downloaded.

**Examples:**
- Positive case: A document with type "Representation from resident" is skipped
- Positive case: A document with type "Public Comment" is not downloaded
- Positive case: A document with type "Objection Letter" is filtered out
- Edge case: A document with no type/category field defaults to being downloaded (fail-safe behavior)

### FR-002: Download Core Application Documents
**Description:** The system must always download documents categorized as core application materials, including planning statements, design and access statements, application forms, and drawings/plans.

**Examples:**
- Positive case: Document type "Planning Statement" is always downloaded
- Positive case: Document type "Design and Access Statement" is always downloaded
- Positive case: Document type "Proposed Plans" is always downloaded
- Positive case: Document type "Application Form" is always downloaded

### FR-003: Download Technical Assessment Documents
**Description:** The system must always download technical assessment documents including transport assessments, environmental impact assessments, heritage statements, flood risk assessments, ecology reports, and similar specialist reports.

**Examples:**
- Positive case: Document type "Transport Assessment" is always downloaded
- Positive case: Document type "Environmental Impact Assessment" is always downloaded
- Positive case: Document type "Heritage Statement" is always downloaded
- Positive case: Document type "Flood Risk Assessment" is always downloaded
- Positive case: Document type "Ecology Report" is always downloaded

### FR-004: Download Officer and Decision Documents
**Description:** The system must always download documents produced by the council including officer reports, committee reports, delegated decision notices, and planning conditions.

**Examples:**
- Positive case: Document type "Officer Report" is always downloaded
- Positive case: Document type "Committee Report" is always downloaded
- Positive case: Document type "Decision Notice" is always downloaded
- Positive case: Document type "Planning Conditions" is always downloaded

### FR-005: Override Filter with Download All Flag
**Description:** The system must provide a flag or parameter that bypasses all filtering and downloads all documents for a given application reference. This override must be available via the MCP tool interface.

**Examples:**
- Positive case: When `download_all=true`, documents normally filtered (public comments) are downloaded
- Positive case: When `download_all=false` (default), filtering rules are applied
- Edge case: Override flag works even when filter configuration is present

### FR-006: Report Filtered Documents
**Description:** When documents are filtered, the system must return structured information about which documents were skipped, including document ID, description, and filter reason. The response must distinguish between downloaded and filtered documents.

**Examples:**
- Positive case: Response includes a `filtered_documents` array with `document_id`, `description`, `document_type`, and `filter_reason`
- Positive case: Response includes counts: `total_documents`, `downloaded_count`, `filtered_count`
- Negative case: Filtered documents are NOT included in the main `documents` array returned by `list_application_documents`

### FR-007: Backward Compatible Filtering
**Description:** The filtering behavior must be opt-in or backward compatible with existing code. Existing calls to `download_all_documents` must continue to work without modification.

**Examples:**
- Positive case: Default behavior filters public comments (new behavior)
- Positive case: Existing orchestrator code continues to work without changes
- Edge case: If filter rules cannot be loaded or are invalid, system defaults to downloading all documents

---

## Non-Functional Requirements

### NFR-001: Filter Performance
**Category:** Performance
**Description:** Document filtering must not add significant latency to the document listing operation. Classification decisions should be made in-memory based on document metadata already fetched.
**Acceptance Threshold:** Filtering adds < 10ms per document to list_application_documents execution time
**Verification:** Observability (add metrics for filter decision time)

### NFR-002: Filter Reliability
**Category:** Reliability
**Description:** The filtering logic must be robust to variations in Cherwell portal document type naming. If a document type is unknown or ambiguous, the system must default to downloading (fail-safe).
**Acceptance Threshold:** Zero false negatives (no relevant documents incorrectly filtered)
**Verification:** Testing (integration tests with real Cherwell application data covering diverse document types)

### NFR-003: Filter Auditability
**Category:** Maintainability
**Description:** Every filtering decision must be logged with the document ID, document type, filter decision (download/skip), and filter reason. Logs must include application reference for traceability.
**Acceptance Threshold:** 100% of filter decisions logged at INFO level with structured fields
**Verification:** Code review (verify logging calls are present for all filter paths)

### NFR-004: Filter Rule Clarity
**Category:** Maintainability
**Description:** Filter rules must be defined in a single, clearly documented location in the codebase. Rules must use explicit allowlists and denylists based on document type patterns.
**Acceptance Threshold:** All filter rules are defined in one module/file with inline comments explaining each rule
**Verification:** Code review (verify filter rules are centralized and documented)

---

## Open Questions

None. Requirements are clear based on user interview.

---

## Appendix

### Glossary
- **Document Type/Category:** Structured metadata field in the Cherwell portal's document listing that classifies the document (e.g., "Planning Statement", "Public Comment", "Transport Assessment")
- **Public Comment:** Any document submitted by a member of the public in response to a planning application, including objections, support letters, and representations
- **Core Application Document:** Essential documents submitted by the applicant as part of the planning application
- **Technical Assessment:** Specialist reports analyzing specific aspects of the proposed development (transport, environment, heritage, etc.)
- **Officer/Decision Document:** Documents produced by the council as part of the decision-making process

### References
- [Cherwell Planning Register](https://planningregister.cherwell.gov.uk/) - Target system for scraping
- [Document downloads - Planning process | Cherwell District Council](https://www.cherwell.gov.uk/downloads/115/planning) - Document categories reference
- [foundation-api specification](../foundation-api/specification.md) - Cherwell scraper requirements
- [foundation-api design](../foundation-api/design.md) - Cherwell scraper implementation

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-07 | Claude Opus 4.6 | Initial specification |
