# Specification: Response Letter

**Version:** 1.0
**Date:** 2026-02-07
**Status:** Draft

---

## Problem Statement

After the AI agent generates a planning application review, the advocacy group must manually rewrite the analysis into a formal consultee letter before submitting it to the planning authority. This is time-consuming, error-prone, and produces inconsistent letter quality. The system should automatically convert a completed review into a submission-ready consultee letter addressed to the case officer.

## Beneficiaries

**Primary:**
- Bicester Bike Users' Group (BBUG) volunteers who respond to planning consultations — they get a submission-ready letter instead of a raw review

**Secondary:**
- Planning officers who receive more consistently structured and policy-referenced representations
- Other cycling advocacy groups who may use the system in future (group name is configurable)

---

## Outcomes

**Must Haves**
- A completed review can be converted into a formal consultee letter via a single API call
- The letter is written in professional prose by the LLM (not a mechanical template reformat)
- The caller specifies the group's stance: object, support, support-with-conditions, or neutral comment
- The letter is addressed to the case officer (from scraper data when available, generic fallback otherwise)
- Policy documents are cited inline and collected in a references/bibliography section
- The advocacy group name, stylised name, and abbreviation are configurable via environment variables
- The letter is available as Markdown for editing/preview before submission

**Nice-to-haves**
- The caller can set a custom letter date (defaults to generation date)
- The letter tone is adjustable between formal-technical and accessible-persuasive
- The caller can override the case officer name

---

## Explicitly Out of Scope

- PDF rendering of the letter (can be added later using existing PDFGenerator infrastructure)
- Automatic submission of the letter to the planning portal
- Email delivery of the letter
- Storing/versioning multiple letter drafts per review
- Multi-language support
- DOCX output format
- Editing the letter content via the API after generation

---

## Functional Requirements

### FR-001: Generate Response Letter
**Description:** The system must accept a request to generate a consultee response letter for a completed review. The request specifies the review ID and the group's stance. The system uses the LLM to rewrite the review findings into formal letter prose and returns a letter ID for retrieval.

**Examples:**
- Positive case: `POST /api/v1/reviews/{review_id}/letter` with `{"stance": "object"}` returns 202 Accepted with a letter ID
- Edge case: Requesting a letter for a review that is still processing returns 400 with `review_incomplete` error

### FR-002: Stance Selection
**Description:** The caller must specify one of four stances that frames the letter's position: `object` (the group opposes the application), `support` (the group supports it), `conditional` (the group supports subject to conditions being met), or `neutral` (the group provides factual comments without taking a position). The stance determines the letter's opening, framing, and closing language.

**Examples:**
- Positive case: `"stance": "object"` produces a letter opening with "Bicester BUG wishes to object to this application..."
- Positive case: `"stance": "conditional"` produces "Bicester BUG supports this application subject to the following conditions..."
- Negative case: `"stance": "invalid"` returns 422 validation error

### FR-003: Advocacy Group Configuration
**Description:** The advocacy group identity must be configurable via environment variables: `ADVOCACY_GROUP_NAME` (full legal name, default: "Bicester Bike Users' Group"), `ADVOCACY_GROUP_STYLISED` (display name, default: "Bicester BUG"), and `ADVOCACY_GROUP_SHORT` (abbreviation, default: "BBUG"). The letter uses the stylised name in the body and the full name in the sign-off.

**Examples:**
- Positive case: Letter body uses "Bicester BUG" and sign-off uses "On behalf of Bicester Bike Users' Group (BBUG)"
- Edge case: If env vars are not set, defaults are used

### FR-004: Case Officer Addressing
**Description:** The letter must be addressed to the case officer. The system first uses the case officer name from the scraper metadata (if available in the application data). The caller may override this via an optional `case_officer` field in the request. If neither is available, the letter falls back to "Dear Sir/Madam" with the address "The Case Officer, Cherwell District Council".

**Examples:**
- Positive case: Scraper provides "Ms J. Smith" — letter opens "Dear Ms J. Smith"
- Positive case: Caller overrides with `"case_officer": "Mr A. Jones"` — letter opens "Dear Mr A. Jones"
- Edge case: No scraper data and no override — letter opens "Dear Sir/Madam"

### FR-005: Inline Policy Citations with Bibliography
**Description:** The letter must reference policy documents inline in the text (e.g. "as required by LTN 1/20, Section 11.1") and include a full bibliography/references section at the end listing all policy sources cited, their full titles, revision dates, and the sections referenced.

**Examples:**
- Positive case: Body text includes "contrary to paragraph 112 of the NPPF (December 2024)" and the references section lists "National Planning Policy Framework, December 2024, Ministry of Housing, Communities and Local Government"
- Edge case: Review has no policy references — bibliography section is omitted

### FR-006: Letter Date
**Description:** The letter must include a date. By default this is the date of generation. The caller may optionally provide a `letter_date` field (YYYY-MM-DD format) to override it.

**Examples:**
- Positive case: No date provided — letter dated with today's date
- Positive case: `"letter_date": "2026-02-10"` — letter dated 10 February 2026
- Negative case: `"letter_date": "not-a-date"` — returns 422 validation error

### FR-007: Tone Selection
**Description:** The caller may optionally specify a `tone` parameter: `formal` (professional planning language with precise technical terminology) or `accessible` (clear, jargon-light language suitable for councillors and the public). Default is `formal`.

**Examples:**
- Positive case: `"tone": "formal"` produces "The proposed development fails to provide cycle infrastructure in accordance with..."
- Positive case: `"tone": "accessible"` produces "The plans do not include safe cycling routes as required by..."
- Edge case: No tone specified — defaults to `formal`

### FR-008: Retrieve Generated Letter
**Description:** The system must provide an endpoint to retrieve a generated letter by its letter ID. The letter is returned as Markdown text. If the letter is still being generated (LLM call in progress), the endpoint returns the current status.

**Examples:**
- Positive case: `GET /api/v1/letters/{letter_id}` returns the letter content as Markdown when generation is complete
- Positive case: `GET /api/v1/letters/{letter_id}` returns `{"status": "generating", "progress": {...}}` while in progress
- Negative case: Non-existent letter ID returns 404

### FR-009: Letter Content Structure
**Description:** The generated letter must include: (1) sender header with group name and date, (2) recipient addressing with case officer name and planning authority, (3) subject line with application reference and site address, (4) opening paragraph stating the group's stance, (5) body paragraphs covering the key findings from the review with inline policy citations, (6) recommendations paragraph, (7) suggested conditions paragraph (when stance is `conditional` or the review includes conditions), (8) closing paragraph, (9) sign-off with full group name, (10) references/bibliography section.

**Examples:**
- Positive case: A letter for an objection includes all 10 sections in order
- Edge case: A `neutral` stance letter omits "suggested conditions" if none are relevant

### FR-010: LLM-Based Letter Generation
**Description:** The letter must be generated by calling the LLM (Claude) with the review content, application metadata, stance, tone, and group identity as context. The LLM produces the letter in a single pass as Markdown. The system prompt must instruct the LLM to produce a consultee letter, not a review — the voice should be that of the advocacy group writing to the planning authority.

**Examples:**
- Positive case: The LLM receives the full review markdown and produces a letter that restructures the content into persuasive prose
- Negative case: The LLM output must not reproduce the review verbatim — it must be rewritten in letter form

---

## Non-Functional Requirements

### NFR-001: Generation Latency
**Category:** Performance
**Description:** Letter generation must complete within a reasonable time given it involves an LLM call
**Acceptance Threshold:** Letter generation completes within 60 seconds for 95th percentile
**Verification:** Observability — log generation duration, expose in letter status response as `processing_time_seconds`

### NFR-002: Token Efficiency
**Category:** Performance
**Description:** The LLM call for letter generation should be efficient with token usage
**Acceptance Threshold:** Letter generation uses fewer than 6,000 output tokens on average
**Verification:** Observability — log input and output token counts in letter metadata

### NFR-003: Consistent Letter Quality
**Category:** Reliability
**Description:** Generated letters must consistently include all required sections and properly cite policies
**Acceptance Threshold:** 100% of generated letters include sender header, recipient, subject line, body, and sign-off
**Verification:** Testing — validate letter structure in integration tests by checking for required sections

### NFR-004: Authentication
**Category:** Security
**Description:** Letter generation and retrieval endpoints must require the same Bearer token authentication as all other API endpoints
**Acceptance Threshold:** Unauthenticated requests return 401
**Verification:** Testing — verify auth is enforced in endpoint tests

___



## Open Questions

None — all questions resolved during discovery.

---

## Appendix

### Glossary
- **Consultee letter:** A formal written representation submitted to a planning authority in response to a planning application consultation
- **Stance:** The advocacy group's position on the application: object, support, conditional (support with conditions), or neutral
- **Case officer:** The planning officer at the local authority responsible for determining the application
- **BBUG:** Bicester Bike Users' Group — the initial/default advocacy group using this system
- **Bibliography:** A references section at the end of the letter listing all policy documents cited

### References
- [docs/API.md](../../docs/API.md) — Current API reference
- [docs/DESIGN.md](../../docs/DESIGN.md) — System architecture
- [.sdd/project-guidelines.md](../project-guidelines.md) — Project conventions
- [.sdd/agent-integration/specification.md](../agent-integration/specification.md) — Review generation specification

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-07 | SDD | Initial specification |
