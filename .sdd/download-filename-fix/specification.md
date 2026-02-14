# Specification: Download Filename Fix

**Version:** 1.0
**Date:** 2026-02-14
**Status:** Implemented

---

## Problem Statement

The Cherwell planning portal uses document download URLs where the filename is in a query parameter (`/Document/Download?...&fileName=Report.pdf`) rather than in the URL path. The scraper extracts the filename from the URL path only, yielding `"Download"` with no extension. This causes all downloaded documents to be saved as extensionless files (e.g., `/data/raw/21_03266_F/Download`), which the document store rejects with "Unsupported file type: " because it requires a known extension (.pdf, .png, etc.) to process files.

This results in every document failing ingestion, making the entire review fail with "No documents could be ingested".

## Beneficiaries

**Primary:**
- End users submitting reviews — currently all reviews with Cherwell downloads fail

**Secondary:**
- Operators debugging failed reviews

---

## Outcomes

**Must Haves**
- Documents downloaded from Cherwell portal URLs are saved with correct filenames and extensions
- Duplicate filenames within a single application are disambiguated (multiple documents may share the same name)
- Existing non-Cherwell URLs (with filenames in the path) continue to work

**Nice-to-haves**
- None

---

## Explicitly Out of Scope

- Content-Type header-based extension detection (add-on complexity, not needed since `fileName` param is always present)
- Changes to the document store ingestion (it correctly rejects extensionless files)
- Changes to the orchestrator download logic

---

## Functional Requirements

### FR-001: Extract Filename from Query Parameter
**Description:** When deriving a filename from a download URL, the scraper must check for a `fileName` query parameter first. If present and non-empty, use it as the filename. Otherwise fall back to the URL path extraction.

**Examples:**
- Positive case: URL `/Document/Download?module=PLA&fileName=Transport%20Assessment.pdf` → filename `Transport Assessment.pdf`
- Edge case: URL `/Document/Download?module=PLA` (no fileName param) → falls back to path-based extraction, then hash-based default
- Positive case: URL `https://example.com/files/report.pdf` → filename `report.pdf` (path extraction still works)

### FR-002: Disambiguate Duplicate Filenames
**Description:** When multiple documents in the same output directory would have the same filename, the scraper must append a numeric suffix to avoid overwriting. Files that already exist on disk with the same name get a `_N` suffix before the extension.

**Examples:**
- Positive case: Two documents named `Report.pdf` → saved as `Report.pdf` and `Report_1.pdf`
- Edge case: Three documents with the same name → `Report.pdf`, `Report_1.pdf`, `Report_2.pdf`

---

## Non-Functional Requirements

### NFR-001: Backwards Compatibility
**Category:** Maintainability
**Description:** URLs that contain filenames in the path (not query params) must continue to work as before.
**Acceptance Threshold:** All existing scraper tests pass without modification
**Verification:** Testing — existing test suite

---

## Open Questions

None

---

## Appendix

### Glossary
- **Cherwell portal:** The planning register website at planningregister.cherwell.gov.uk

### References
- Cherwell portal download URL format: `/Document/Download?module=PLA&recordNumber=...&fileName=...`

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-14 | Claude | Initial specification |
