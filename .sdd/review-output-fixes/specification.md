# Specification: Review Output Fixes

**Version:** 1.0
**Date:** 2026-02-07
**Status:** Draft

---

## Problem Statement

Two bugs affect the quality and completeness of review output:

1. **Consultee response documents bypass the filter because the scraper doesn't extract document categories from the portal's table structure.** The Cherwell portal document table groups documents under section headers ("Application Forms", "Supporting Documents", "Consultation Responses", etc.) but the scraper ignores these section headers and instead uses the per-row link text as `document_type` — which is typically the filename, not a useful category. The filter then falls back to pattern-matching against document titles/descriptions, but patterns like "consultation response", "consultee response", "statutory consultee" don't match titles like "Transport Response to Consultees" or "Applicant's response to ATE comments". The root cause is that the scraper should extract the section header as the document category so the filter can make decisions based on the portal's own categorisation rather than brittle title substring matching.

2. **Structured JSON fields in the API response are never populated.** The `ReviewContent` schema defines `aspects`, `policy_compliance`, `recommendations`, and `suggested_conditions` fields, but the orchestrator only extracts `overall_rating`, `key_documents`, `full_markdown`, and `summary` from Claude's response. The structured data exists within the markdown output (the review contains an Assessment Summary table with aspect ratings, a Policy Compliance Matrix, numbered Recommendations, and Suggested Conditions) but is never parsed into the JSON fields.

## Beneficiaries

**Primary:**
- End users (cycling advocacy groups) who receive cleaner reviews not contaminated by applicant rebuttals to consultee objections
- API consumers who need structured data (aspects, policy compliance, recommendations) without parsing markdown

**Secondary:**
- Developers building downstream tools (dashboards, letter generators) that depend on structured fields
- The letter generator which could use structured recommendations/conditions instead of re-parsing markdown

---

## Outcomes

**Must Haves**
- The scraper extracts the section header from the Cherwell portal document table (e.g. "Application Forms", "Supporting Documents", "Consultation Responses") and stores it as `document_type` on each `DocumentInfo`
- The filter uses `document_type` (the portal category) as the primary filtering mechanism rather than title pattern matching
- Documents under consultation/correspondence categories are filtered out by default (unless `include_consultation_responses=True`)
- Documents under application-related categories ("Application Forms", "Supporting Documents", "Site Plans", "Proposed Plans") are allowed
- Existing filter behaviour is preserved as a fallback when `document_type` is not available (e.g. if the portal changes format)
- The `aspects` field in the API response is populated with aspect name, rating, and key issue parsed from Claude's output
- The `policy_compliance` field is populated with requirement, policy source, and compliance status
- The `recommendations` field is populated with the numbered recommendation strings
- The `suggested_conditions` field is populated with suggested planning conditions
- Existing `full_markdown` output is preserved unchanged
- Existing `key_documents` parsing continues to work

**Nice-to-haves**
- `key_documents` JSON block is populated from the parsed output (currently requires LLM to emit a special JSON block)

---

## Explicitly Out of Scope

- Changing the LLM prompt or review generation approach (the fix parses existing markdown output)
- Integrating the unused `ReviewGenerator` class from `generator.py` (that class depends on `AssessmentResult` and `PolicyComparisonResult` objects which the current orchestrator doesn't produce)
- Retroactively re-running or fixing existing reviews
- Adding new structured sections beyond what the LLM already produces in markdown
- Content-based document filtering (filtering is based on portal metadata only)
- Changing the Cherwell portal scraping to use a headless browser (the portal renders the document table via JavaScript, but the current approach works for the HTML returned by the server)

---

## Functional Requirements

### FR-001: Extract Document Category from Portal Section Headers
**Description:** The scraper must extract the section header (table group heading) from the Cherwell portal document table and store it as the `document_type` field on each `DocumentInfo`. The portal document table groups documents under headers like "Application Forms", "Supporting Documents", "Site Plans", "Proposed Plans", "Plans - Proposed", and potentially "Consultation Responses" or similar headings. Each document row should inherit the `document_type` of its enclosing section.

Currently, `_parse_cherwell_register_documents()` sets `document_type` to the link text (typically the filename), which is not a useful category. The parser must be updated to walk the table structure, identify section header rows, and propagate the current section header to all document rows beneath it.

**Examples:**
- A document under the "Supporting Documents" section header gets `document_type="Supporting Documents"`
- A document under the "Application Forms" section header gets `document_type="Application Forms"`
- A document under a "Consultation Responses" section header gets `document_type="Consultation Responses"`
- If no section header is found (flat table), `document_type` falls back to current behaviour (link text if different from description, else None)
- The `description` field continues to hold the human-readable document title from the description column

### FR-002: Filter Documents by Portal Category
**Description:** The filter must use the `document_type` (portal category) as the primary filtering criterion. Categories that represent consultation responses, third-party correspondence, or public comments should be denied. Categories that represent application submission materials should be allowed. Title-based pattern matching should remain as a fallback for documents with no category or an unrecognised category.

**Category Allowlist (always download):**
- "Application Forms"
- "Supporting Documents"
- "Site Plans"
- "Proposed Plans"
- "Plans - Proposed"

**Category Denylist (filter out by default):**
- Any category containing "consultation" (e.g. "Consultation Responses")
- Any category containing "comment" (e.g. "Public Comments")
- Any category containing "representation" or "objection"

**Examples:**
- Positive case: Document with `document_type="Consultation Responses"` is filtered — this catches "Transport Response to Consultees", "Applicant's response to ATE comments", "Consultation Response", etc. regardless of their individual titles
- Positive case: Document with `document_type="Supporting Documents"` is allowed, even if title contains "response" (e.g. an ES appendix)
- Positive case: Document with `document_type=None` falls back to existing title-based pattern matching
- Negative case: "Transport Assessment" under "Supporting Documents" is NOT filtered
- Edge case: `include_consultation_responses=True` still overrides the category-based denylist

### FR-003: Parse Aspects from Review Markdown
**Description:** The orchestrator must parse the Assessment Summary table from the markdown output into the `aspects` JSON field. The table has columns: Aspect, Rating, Key Issue.

**Examples:**
- Input markdown: `| Cycle Parking | AMBER | Quantity adequate but design quality unverified |`
- Output JSON: `{"name": "Cycle Parking", "rating": "amber", "key_issue": "Quantity adequate but design quality unverified"}`
- Edge case: If the table is not found, `aspects` remains null (graceful degradation)

### FR-004: Parse Policy Compliance from Review Markdown
**Description:** The orchestrator must parse the Policy Compliance Matrix table from the markdown output into the `policy_compliance` JSON field.

**Examples:**
- Input markdown: `| Prioritise sustainable transport modes | NPPF para 115(a) | ❌ NO | Car-based design with token cycle provision |`
- Output JSON: `{"requirement": "Prioritise sustainable transport modes", "policy_source": "NPPF para 115(a)", "compliant": false, "notes": "Car-based design with token cycle provision"}`
- Edge case: `⚠️ PARTIAL` should map to `compliant: false` with notes indicating partial compliance
- Edge case: If the table is not found, `policy_compliance` remains null

### FR-005: Parse Recommendations from Review Markdown
**Description:** The orchestrator must parse the numbered recommendations from the Recommendations section into a string array.

**Examples:**
- Input: Section headed `## Recommendations` containing numbered subsections with bold titles
- Output: Array of recommendation strings, one per numbered item (title only, not full detail)
- Edge case: If the section is not found, `recommendations` remains null

### FR-006: Parse Suggested Conditions from Review Markdown
**Description:** The orchestrator must parse suggested planning conditions from the review markdown into a string array.

**Examples:**
- Input: Section headed `## Suggested Conditions` or similar containing numbered conditions
- Output: Array of condition strings
- Edge case: Not all reviews will have suggested conditions — field remains null if absent
- Edge case: The current 25/00284/F review has conditions embedded in the recommendations section — parser should handle both standalone section and embedded conditions

---

## Non-Functional Requirements

### NFR-001: Parsing Robustness
**Category:** Reliability
**Description:** Markdown parsing must be tolerant of minor formatting variations from Claude (extra whitespace, inconsistent capitalisation, emoji vs text for compliance indicators). If parsing fails for any field, that field should remain null rather than causing the review to fail.
**Acceptance Threshold:** Zero review failures caused by parsing errors
**Verification:** Testing with multiple review outputs

### NFR-002: Filter Category Precision
**Category:** Reliability
**Description:** Category-based filtering must not create false positives. Documents under "Supporting Documents" must never be filtered out by the category check (title-based fallback only applies when no category is set). The scraper must correctly associate each document with its section header — a parsing error that assigns a consultee response header to a supporting document would be a critical bug.
**Acceptance Threshold:** Zero false positives on the 25/00284/F document set (955 documents)
**Verification:** Integration test with real or representative HTML from the Cherwell portal

### NFR-003: Backward Compatibility
**Category:** Compatibility
**Description:** Changes must not alter the `full_markdown` output or break existing `key_documents` parsing. The structured fields are additive — any consumer relying on `full_markdown` must continue to work unchanged.
**Acceptance Threshold:** Existing test suite passes without modification (except new tests)
**Verification:** Full test suite run

---

## Open Questions

None. Requirements are clear from analysis of the 25/00284/F review output.

---

## Appendix

### Evidence: Documents That Bypassed Filter (25/00284/F)

955 documents were downloaded for this application. The following documents are consultation/correspondence documents that bypassed the filter because the scraper doesn't extract their portal section category, and their titles don't match existing denylist patterns:

| File | Description | Portal Category (not extracted) | Why It Bypassed |
|------|-------------|--------------------------------|-----------------|
| `076_Transport Response to Consultees.pdf.pdf` | Applicant's response to OCC highways objections | Likely "Consultation Responses" or similar | Title doesn't match denylist substring patterns |
| `141_Transport Response to Consultees.pdf.pdf` | Duplicate | Same | Same |
| `164_Applicant_s response to ATE comments.pdf` | Applicant's response to Active Travel England | Same | Same |
| `169_Applicant_s response to ATE comments.pdf` | Duplicate | Same | Same |
| `236_Applicant_s response to ATE comments.pdf` | Duplicate | Same | Same |
| `161_Response to EA comments from applicant.pdf` | Applicant's response to Environment Agency | Same | Same |
| `166_Response to EA comments from applicant.pdf` | Duplicate | Same | Same |
| `233_Response to EA comments from applicant.pdf` | Duplicate | Same | Same |
| `165_Seimens letter re highway concerns.pdf` | Third-party letter about highways | Same | Same |
| `170_Seimens letter re highway concerns.pdf` | Duplicate | Same | Same |
| `237_Seimens letter re highway concerns.pdf` | Duplicate | Same | Same |
| `161_holding obj National Highways.pdf` | Holding objection from statutory consultee | Same | Same |
| `162_Holding objection from Nat Highways.pdf` | Same | Same | Same |

Note: 43 files named `Consultation Response.pdf` (471-513) **were** correctly filtered by the existing title-based "consultation response" pattern. With category-based filtering, ALL of the above documents would also be filtered if they share the same portal section header.

### Evidence: Portal Document Table Structure

The Cherwell portal at `planningregister.cherwell.gov.uk` renders the document table with JavaScript. Documents are grouped under section headers within the table:
- **Application Forms** — application form documents
- **Supporting Documents** — technical submissions (transport assessments, design statements, ES chapters, etc.)
- **Site Plans** / **Proposed Plans** / **Plans - Proposed** — drawings and plans
- (Consultation responses appear under their own section header)

The scraper (`_parse_cherwell_register_documents` in `parsers.py`) currently finds `<a class="singledownloadlink">` elements and extracts the link text as `category`. It does NOT look for section header rows in the table. The section headers are `<tr>` rows within the same `<table>` that act as group dividers.

### Evidence: Unpopulated Fields in Review JSON

From `output/25_00284_F_review.json`:
```json
{
  "review": {
    "overall_rating": "red",        // ✅ Populated
    "summary": "# Cycle Advocacy...", // ✅ Populated (first 500 chars of markdown)
    "key_documents": null,           // ❌ Not populated (parser found no JSON block)
    "aspects": null,                 // ❌ Not populated
    "policy_compliance": null,       // ❌ Not populated
    "recommendations": null,         // ❌ Not populated
    "suggested_conditions": null,    // ❌ Not populated
    "full_markdown": "# Cycle..."   // ✅ Populated
  }
}
```

The `full_markdown` contains all the data needed to populate these fields:
- **Assessment Summary** table at line ~46 of the markdown
- **Policy Compliance Matrix** table at line ~365
- **Recommendations** section at line ~399 with 13+ numbered subsections
- **Suggested Conditions** embedded within recommendations

### Glossary

- **Section header**: A row in the Cherwell portal document table that acts as a group divider, labelling the category of documents below it (e.g. "Application Forms", "Supporting Documents", "Consultation Responses")
- **Document category / document_type**: The section header under which a document appears on the Cherwell portal. Stored in `DocumentInfo.document_type`.
- **Denylist**: Pattern list used to filter out documents; if a document's category or description matches a denylist pattern, it is not downloaded
- **Allowlist**: Pattern list used to ensure documents are always downloaded
- **Fail-safe**: Default filter behaviour when no pattern matches — currently allows the document (prevents false negatives)
- **Category-based filtering**: Filtering documents based on the portal's own categorisation (section headers) rather than substring matching against document titles

### References

- [document-filtering specification](../document-filtering/specification.md) - Original filter design
- [review-scope-control specification](../review-scope-control/specification.md) - Consultation response toggle
- [key-documents specification](../key-documents/specification.md) - Key documents JSON extraction
- [agent-integration specification](../agent-integration/specification.md) - Review generation design

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-07 | Claude Opus 4.6 | Initial specification |
