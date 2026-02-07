# Specification: Key Documents Listing

**Version:** 1.0
**Date:** 2026-02-07
**Status:** Draft

---

## Problem Statement

Review reports and API responses contain no reference to the source documents that were analysed. A reader cannot quickly identify or access the transport assessment, travel plan, design & access statement, or other key documents that underpin the review's conclusions. They must manually search the Cherwell portal to find the relevant PDFs.

## Beneficiaries

**Primary:**
- Bicester BUG committee members reviewing reports before submitting consultation responses
- Residents reading a published review who want to verify claims against source material

**Secondary:**
- Case officers receiving BBUG's consultation response who need to cross-reference cited documents
- Other cycling advocacy groups using the tool who need quick access to supporting evidence

---

## Outcomes

**Must Haves**
- A "Key Documents" section at the top of the Markdown report listing the most relevant documents with direct download links
- A `key_documents` array in the JSON API response containing document title, LLM-generated summary, and download URL for each listed document
- Documents are categorised into meaningful groups (e.g. "Transport & Access", "Design & Layout", "Application Core")
- The listing includes all core application documents and transport/active-travel-related documents that were ingested

**Nice-to-haves**
- Each document summary briefly explains why it is relevant to cycling/active travel assessment
- The document listing is ordered by relevance to cycling advocacy (most relevant first within each category)

---

## Explicitly Out of Scope

- Linking to individual pages or sections within a PDF (deep linking)
- Generating a separate downloadable document index file (e.g. CSV, spreadsheet)
- Modifying the Cherwell scraper to fetch additional metadata beyond what it already collects
- Adding documents that were filtered out (denied) during ingestion — only ingested documents are listed
- Thumbnails or previews of documents

---

## Functional Requirements

### FR-001: Key Documents Array in API Response
**Description:** The `GET /api/v1/reviews/{review_id}` response must include a `key_documents` field within the `review` object. This field is an array of objects, each containing `title` (string), `category` (string), `summary` (string, 1-2 sentences), and `url` (string, direct PDF download URL).

**Examples:**
- Positive case: A completed review for 25/00284/F returns `key_documents` with entries for "Transport Assessment", "Travel Plan Framework", "Design and Access Statement", etc., each with a summary like "Sets out the traffic impact of the development and proposed mitigation measures including junction improvements and cycle infrastructure."
- Edge case: A document has no download URL in the scraper metadata (e.g. URL is null). The document is still listed with `url` set to `null`.

### FR-002: Document Category Assignment
**Description:** Each key document must be assigned to one of the following categories: "Transport & Access" (transport assessments, travel plans, highway reports, cycling/walking strategies), "Design & Layout" (design & access statements, site plans, floor plans, elevations, masterplans), or "Application Core" (planning statements, application forms, environmental statements, covering letters). The category is determined by matching the document's `document_type` from the scraper against known type patterns.

**Examples:**
- Positive case: A document with type "Transport Assessment" is categorised as "Transport & Access".
- Positive case: A document with type "Design and Access Statement" is categorised as "Design & Layout".
- Edge case: A document with an unrecognised type that was ingested is categorised as "Application Core" (fallback).

### FR-003: LLM-Generated Document Summary
**Description:** During the analysis phase, the LLM must produce a 1-2 sentence summary for each key document explaining its content and relevance to cycling/active travel assessment. The summary is generated from the document's ingested text chunks, not from the title alone.

**Examples:**
- Positive case: A Transport Assessment is summarised as "Analyses traffic impacts of the proposed 163,338 sqm logistics development, including junction capacity modelling for the A41 access. Proposes a cycle route connecting to the existing network at Vendee Drive."
- Negative case: The summary must not simply restate the document title (e.g. "This is a transport assessment" is insufficient).

### FR-004: Markdown Report Key Documents Section
**Description:** The Markdown report must include a "Key Documents" section positioned immediately after the "Application Summary" section and before "Overall Assessment". Documents are grouped by category with each entry showing the document title as a clickable Markdown link to the download URL, followed by the summary on a new line.

**Examples:**
- Positive case: The section renders as:
  ```
  ## Key Documents

  ### Transport & Access
  - [Transport Assessment (Part 1 of 7)](https://planningregister.cherwell.gov.uk/Document/Download?...)
    Sets out the traffic impact of the proposed development...
  - [Travel Plan Framework](https://planningregister.cherwell.gov.uk/Document/Download?...)
    Outlines sustainable travel targets and monitoring strategy...

  ### Design & Layout
  - [Design and Access Statement](https://planningregister.cherwell.gov.uk/Document/Download?...)
    Describes the site layout including cycle parking locations...
  ```
- Edge case: A document with `url: null` is rendered without a link: `- Transport Assessment (no download available)`

### FR-005: Document Selection Criteria
**Description:** The key documents listing must include all documents that were successfully ingested during the review pipeline AND whose `document_type` matches the core application documents or transport/active-travel-related categories. Documents that failed ingestion or were filtered out by the document filter are excluded.

**Examples:**
- Positive case: A "Noise Assessment" that was denied by the document filter does not appear in key documents.
- Positive case: An "ES Chapter on Transport" that was ingested is included under "Transport & Access".
- Edge case: An application with only 2 ingested documents (planning statement and site plan) lists both under their respective categories.

### FR-006: Ordering Within Categories
**Description:** Within each category group, documents must be ordered by relevance to cycling advocacy. "Transport & Access" documents appear first in the section, followed by "Design & Layout", then "Application Core". Within each category, the LLM determines the ordering during analysis based on how directly relevant the document is to cycling/active travel.

**Examples:**
- Positive case: In "Transport & Access", a "Cycle Infrastructure Plan" appears before a general "Highway Impact Assessment".

---

## Non-Functional Requirements

### NFR-001: Summary Generation Latency
**Category:** Performance
**Description:** Generating document summaries must not add more than 30 seconds to the overall review processing time, as the summaries are produced during the existing LLM analysis phase rather than as a separate step.
**Acceptance Threshold:** Total review processing time increases by no more than 30 seconds compared to a review without key document summaries.
**Verification:** Observability — compare review processing durations before and after the feature via worker job logs.

### NFR-002: Backward Compatibility
**Category:** Maintainability
**Description:** The `key_documents` field must be additive to the existing API response schema. Existing fields in the `review` object must not change. Clients that do not read `key_documents` must be unaffected.
**Acceptance Threshold:** All existing API tests pass without modification. The field defaults to `null` or an empty array for reviews completed before this feature.
**Verification:** Testing — existing API test suite passes; manual check of pre-existing review responses.

---

## Open Questions

None — all requirements clarified during discovery.

---

## Appendix

### Glossary
- **Key document:** An ingested application document whose type matches the core or transport/active-travel category patterns, selected for prominent listing in the report.
- **Document type:** The category string extracted from the Cherwell portal's document table (e.g. "Transport Assessment", "Planning Statement").
- **Ingested document:** A document that was successfully downloaded, parsed, chunked, embedded, and stored in ChromaDB during the review pipeline.

### References
- [document-filtering specification](.sdd/document-filtering/specification.md) — defines which documents are allowed/denied during ingestion
- [agent-integration specification](.sdd/agent-integration/specification.md) — defines the review generation pipeline
- [response-letter specification](.sdd/response-letter/specification.md) — reference for Markdown report structure

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-07 | BBUG | Initial specification |
