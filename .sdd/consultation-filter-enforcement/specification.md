# Specification: Consultation Filter Enforcement

**Version:** 1.0
**Date:** 2026-02-15
**Status:** Draft

---

## Problem Statement

Consultation responses and public comments are appearing in completed reviews despite the `include_consultation_responses` and `include_public_comments` API toggles defaulting to `false`. The LLM-based document filter (introduced in `review-workflow-redesign`) selects documents by asking Claude which are relevant, but the LLM ignores the prompt instruction to exclude consultation responses when they appear transport-relevant (e.g. "Oxfordshire County Council's Consultation Response" matches the LLM's understanding of transport relevance). There is no programmatic enforcement after the LLM selection, so these documents are downloaded, ingested, and used as evidence in the review.

## Beneficiaries

**Primary:**
- Bicester BUG committee members who expect the default review to analyse only applicant-submitted documents, not consultee opinions that could bias the independent assessment

**Secondary:**
- Users who explicitly enable the consultation toggle and expect the review to clearly distinguish between applicant evidence and consultee evidence

---

## Outcomes

**Must Haves**
- A programmatic post-filter after the LLM document selection that removes consultation responses and public comments unless the corresponding API toggle is enabled
- The post-filter uses the same classification logic (portal category headers and title patterns) as the existing `DocumentFilter` in `filters.py`
- When a document is removed by the post-filter, it is logged with the reason for removal

**Nice-to-haves**
- N/A

---

## Explicitly Out of Scope

- Changing the LLM filter prompt (the prompt already says to exclude these; the issue is enforcement, not instruction)
- Modifying the `DocumentFilter` class in `filters.py` (it already has the correct logic; the orchestrator just doesn't use it after LLM selection)
- Changing the default values of `include_consultation_responses` or `include_public_comments`
- Adding new document type patterns (the existing patterns in `DocumentFilter` are sufficient)
- Changing how the toggles are passed through the API or worker layers (they already reach the orchestrator via `self._options`)

---

## Functional Requirements

### FR-001: Programmatic Post-Filter After LLM Selection
**Description:** After the LLM document filter returns its list of selected document IDs, the orchestrator must apply a programmatic filter that removes any document whose `document_type` (portal section header) or `description` (title) matches consultation response or public comment patterns, unless the corresponding API toggle is enabled for that review.

**Examples:**
- Positive case: LLM selects "Oxfordshire County Council's Consultation Response" (document_type="Consultation Responses"). The post-filter removes it because `include_consultation_responses` defaults to `false`.
- Positive case: LLM selects "Active Travel England Standing Advice Response" (document_type="Consultation Responses"). The post-filter removes it.
- Negative case: LLM selects "Transport Assessment" (document_type="Supporting Documents"). The post-filter does not remove it.
- Edge case: Review submitted with `include_consultation_responses: true`. LLM selects "OCC Highways Consultation Response". The post-filter allows it through.

### FR-002: Classification Using Existing Patterns
**Description:** The post-filter must classify documents using the same two-tier approach as `DocumentFilter`: first check `document_type` against the category denylist (`CATEGORY_DENYLIST_CONSULTATION`, `CATEGORY_DENYLIST_PUBLIC`), then fall back to checking `description` against the title-based denylist patterns (`DENYLIST_CONSULTATION_RESPONSE_PATTERNS`, `DENYLIST_PUBLIC_COMMENT_PATTERNS`). This ensures consistency between the scraper's filter and the orchestrator's post-filter.

**Examples:**
- Positive case: Document with `document_type="Consultee Responses"` is caught by category denylist.
- Positive case: Document with `document_type=None` and `description="Statutory Consultee Response - OCC"` is caught by title pattern denylist.
- Negative case: Document with `document_type="Supporting Documents"` and `description="Highway Impact Assessment"` is not caught by any denylist.

### FR-003: Logging of Removed Documents
**Description:** When the post-filter removes a document from the LLM's selection, it must log a warning with the document description, document type, and the reason for removal (category match or title pattern match).

**Examples:**
- Log entry: `"Document removed by post-filter" document="OCC Highways Consultation Response" document_type="Consultation Responses" reason="category_denylist_consultation"`

---

## Non-Functional Requirements

### NFR-001: No Impact on Toggle-Enabled Reviews
**Category:** Maintainability
**Description:** When `include_consultation_responses` or `include_public_comments` is `true`, the post-filter must not remove matching documents. The existing behaviour for toggle-enabled reviews must be preserved exactly.
**Acceptance Threshold:** All existing tests pass. A review with `include_consultation_responses: true` includes consultation response documents in the review.
**Verification:** Testing — unit tests verify toggle-enabled path is unaffected.

### NFR-002: Filter Performance
**Category:** Performance
**Description:** The post-filter runs in-memory against the LLM's selected document list (typically 5-30 documents). It must add negligible overhead.
**Acceptance Threshold:** < 1ms for filtering 50 documents.
**Verification:** Testing — unit test with 50-document list completes within time bound.

---

## Open Questions

None.

---

## Appendix

### Glossary
- **LLM filter:** The Haiku-based document relevance classifier in `_phase_filter_documents()` that asks Claude to select relevant documents from the full list.
- **Post-filter:** The programmatic check applied after the LLM filter to enforce hard exclusion rules that the LLM cannot be relied upon to follow.
- **Portal category:** The section header from the Cherwell planning portal (e.g. "Consultation Responses", "Supporting Documents") stored in the `document_type` field.

### References
- [review-scope-control specification](.sdd/review-scope-control/specification.md) — defines the toggle contract
- [review-workflow-redesign specification](.sdd/review-workflow-redesign/specification.md) — introduced LLM-based filtering
- [document-filtering specification](.sdd/document-filtering/specification.md) — defines the allowlist/denylist mechanism

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-15 | BBUG | Initial specification |
