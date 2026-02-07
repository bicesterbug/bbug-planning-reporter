# Specification: Review Scope Control

**Version:** 1.0
**Date:** 2026-02-07
**Status:** Draft

---

## Problem Statement

Consultation responses and public comments from the Cherwell portal are currently blocked by the document denylist and never downloaded. There is no way for a user to opt in to including these document types when submitting a review. Some applications receive substantive consultation responses (e.g. from the Highway Authority, Environment Agency, or parish councils) that contain transport-relevant analysis, and users cannot access this evidence through the review pipeline.

## Beneficiaries

**Primary:**
- Bicester BUG committee members who want to see what the Highway Authority or other statutory consultees have said about transport impacts before drafting their own response
- Residents who want to understand whether public concerns about cycling/pedestrian safety have already been raised

**Secondary:**
- Case officers who may find it useful that the cycling review cross-references existing consultation responses
- Other cycling advocacy groups using the tool who want a fuller picture of the application's consultation history

---

## Outcomes

**Must Haves**
- Two new boolean API parameters on the review options that control whether consultation responses and public comments are included in the review
- When a toggle is enabled, the corresponding document types bypass the download denylist and are downloaded, ingested, and passed as evidence to the LLM for analysis
- When a toggle is disabled (the default), behaviour is identical to today — these document types are filtered out at download time
- The toggles do NOT cause these documents to appear in the Key Documents listing — they are evidence-only inputs to the LLM

**Nice-to-haves**
- The review markdown mentions when consultation responses or public comments were included in the analysis (e.g. a brief note in the Application Summary or a count)

---

## Explicitly Out of Scope

- Adding a separate "Public Sentiment" or "Consultation Summary" section to the review report
- Listing consultation responses or public comments in the Key Documents section
- Changing the default filtering behaviour for any other document types
- Modifying the existing `skip_filter` MCP tool parameter (it remains an all-or-nothing bypass at the scraper level)
- Providing per-document selection (users cannot pick individual documents to include/exclude)
- Downloading these documents but not analysing them (download and analysis are coupled via the toggle)

---

## Functional Requirements

### FR-001: include_consultation_responses API Parameter
**Description:** The `ReviewOptionsRequest` schema must accept a new optional boolean field `include_consultation_responses` that defaults to `false`. When `true`, document types matching consultation response patterns (e.g. "consultation response", "consultee response", "statutory consultee") are removed from the denylist for that review and are downloaded, ingested, and included as evidence in the LLM analysis.

**Examples:**
- Positive case: A review request with `options.include_consultation_responses: true` for application 25/00284/F causes the "Consultation Response - OCC Highways" document to be downloaded, ingested, and available as search evidence for the LLM.
- Negative case: A review request with `options.include_consultation_responses: false` (or omitted) filters out the same document at download time, exactly as today.

### FR-002: include_public_comments API Parameter
**Description:** The `ReviewOptionsRequest` schema must accept a new optional boolean field `include_public_comments` that defaults to `false`. When `true`, document types matching public comment patterns (e.g. "public comment", "comment from", "objection", "representation from", "letter from resident", "letter from neighbour", "letter of objection", "letter of support", "petition") are removed from the denylist for that review and are downloaded, ingested, and included as evidence in the LLM analysis.

**Examples:**
- Positive case: A review request with `options.include_public_comments: true` causes "Letter from Resident - J Smith" to be downloaded and ingested.
- Negative case: A review request with `options.include_public_comments: false` (or omitted) filters out the same document at download time.
- Edge case: A review with both toggles enabled downloads both consultation responses AND public comments.

### FR-003: Filter Override Mechanism
**Description:** The document filter must accept per-review flags that selectively remove consultation response patterns and/or public comment patterns from the denylist for that specific filter invocation. The core allowlist and remaining denylist entries are unaffected. The existing `skip_filter` parameter (which bypasses all filtering) remains unchanged and takes precedence — if `skip_filter` is true, the new toggles are irrelevant.

**Examples:**
- Positive case: `filter_documents(docs, include_consultation_responses=True, include_public_comments=False)` allows consultation responses through but continues to block public comments.
- Positive case: `filter_documents(docs, skip_filter=True)` bypasses all filtering regardless of the new toggles.
- Edge case: A document type that matches both the allowlist and the consultation response denylist (unlikely but defensive) — the allowlist takes precedence (document is downloaded).

### FR-004: Options Flow Through Pipeline
**Description:** The `include_consultation_responses` and `include_public_comments` values must flow from the API request through the worker and orchestrator to the scraper's `download_all_documents` tool call. The orchestrator must pass these flags to the MCP tool, and the scraper must pass them to the `DocumentFilter`.

**Examples:**
- Positive case: User submits `POST /api/v1/reviews` with `options.include_consultation_responses: true`. The worker reads this option, passes it to the orchestrator, which passes `include_consultation_responses: true` in the `download_all_documents` tool arguments.

### FR-005: Excluded from Key Documents
**Description:** Documents that were included via the `include_consultation_responses` or `include_public_comments` toggles must NOT appear in the `key_documents` array in the API response or the "Key Documents" section of the markdown report. They serve only as additional evidence for the LLM during analysis.

**Examples:**
- Positive case: A consultation response is downloaded and ingested when the toggle is on, but the review's `key_documents` array does not contain an entry for it.
- Positive case: The Key Documents markdown section lists only core application documents and transport assessments, not consultation responses.

---

## Non-Functional Requirements

### NFR-001: Backward Compatibility
**Category:** Maintainability
**Description:** The new fields must be additive to the existing `ReviewOptionsRequest` schema. Existing API clients that do not send the new fields must see no change in behaviour. Both fields default to `false`.
**Acceptance Threshold:** All existing API tests pass without modification. Reviews submitted without the new fields produce identical results to before.
**Verification:** Testing — existing API test suite passes; manual check of a review submitted without the new fields.

### NFR-002: Processing Time Impact
**Category:** Performance
**Description:** When toggles are enabled, additional documents are downloaded and ingested, which will increase total processing time proportionally to the number of extra documents. This is expected and acceptable. The overhead from the filter logic change itself (checking two extra booleans) must be negligible.
**Acceptance Threshold:** Filter logic change adds < 1ms to filter_documents execution. Total review time increase is proportional to the number of additional documents only.
**Verification:** Testing — unit test confirms filter logic completes within expected time bounds for a 500-document list.

---

## Open Questions

None — all requirements clarified during discovery.

---

## Appendix

### Glossary
- **Consultation response:** A document submitted by a statutory consultee (e.g. Highway Authority, Environment Agency, parish council) or other organisation in response to the planning application consultation.
- **Public comment:** A document submitted by a member of the public (resident, neighbour, interest group) expressing support, objection, or comment on the planning application.
- **Denylist:** The set of document type patterns in `DocumentFilter` that cause matching documents to be excluded from download during the review pipeline.
- **Evidence:** Document text chunks retrieved via vector search and passed to the LLM as context for generating the review.

### References
- [document-filtering specification](.sdd/document-filtering/specification.md) — defines the current allowlist/denylist mechanism
- [document-filtering design](.sdd/document-filtering/design.md) — design of the filter implementation
- [foundation-api specification](.sdd/foundation-api/specification.md) — defines the review request API
- [agent-integration specification](.sdd/agent-integration/specification.md) — defines the review generation pipeline

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-07 | BBUG | Initial specification |
