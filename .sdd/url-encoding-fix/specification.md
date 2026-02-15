# Specification: URL Encoding Fix

**Version:** 1.0
**Date:** 2026-02-15
**Status:** Draft

---

## Problem Statement

URLs containing spaces break markdown link rendering throughout the system. Cherwell document URLs and S3 public URLs both contain unencoded spaces (e.g. `003_Delegated Officer Report.pdf`), which produce malformed markdown links like `[Title](https://example.com/003_Delegated Officer Report.pdf)`. Markdown renderers treat the space as the end of the URL, truncating the link and leaving trailing text as visible prose.

## Beneficiaries

**Primary:**
- Frontend (bbug-website) — markdown reviews render with working, clickable document links

**Secondary:**
- Webhook consumers — receive well-formed markdown
- Operators — review artefact URLs are valid HTTP URLs

---

## Outcomes

**Must Haves**
- All URLs embedded in markdown output are percent-encoded so markdown links render correctly
- All public URLs returned by the API (in the `urls` object and `output_url` fields) are percent-encoded

**Nice-to-haves**
- Downloaded filenames no longer contain spaces (preventing the issue at the source)

---

## Explicitly Out of Scope

- Changing the Cherwell portal URLs themselves (those are external)
- Retroactively fixing URLs in already-stored Redis results
- URL encoding in non-markdown contexts (JSON field values are fine with spaces)

---

## Functional Requirements

**FR-001: Percent-encode document URLs before LLM prompt injection**
- Description: When building the evidence context for the LLM, all document URLs from Cherwell must be percent-encoded before being included in the prompt text. This ensures the LLM receives valid URLs and reproduces them correctly in markdown links.
- Acceptance criteria: Document URLs containing spaces (e.g. `https://example.com/003_Delegated Officer Report.pdf`) are encoded to `https://example.com/003_Delegated%20Officer%20Report.pdf` before being passed to the LLM prompt.
- Failure/edge cases: URLs that are already encoded must not be double-encoded (e.g. `%20` must not become `%2520`). URLs that are `None` or empty are left as-is.

**FR-002: Percent-encode storage public URLs**
- Description: The `public_url()` method on all storage backends must return percent-encoded URLs. This applies to `S3StorageBackend`, `LocalStorageBackend`, and `InMemoryStorageBackend`.
- Acceptance criteria: A key containing spaces like `25_01178_REM/003_Delegated Officer Report.pdf` produces a URL with `003_Delegated%20Officer%20Report.pdf`. Path separators (`/`) must NOT be encoded.
- Failure/edge cases: Keys without special characters are returned unchanged. Keys with other special characters (e.g. parentheses) are also encoded.

**FR-003: Eliminate spaces from download filenames**
- Description: The Cherwell scraper must replace spaces with underscores in generated download filenames. This prevents spaces from entering S3 keys and local file paths at the source.
- Acceptance criteria: A document with description "Delegated Officer Report" produces filename `003_Delegated_Officer_Report.pdf` instead of `003_Delegated Officer Report.pdf`.
- Failure/edge cases: Existing files already stored with spaces are unaffected (idempotent per review_id). Only new downloads use underscore filenames.

---

## QA Plan

**QA-01: Markdown links render correctly in a new review**
- Goal: Verify that document links in the review markdown are clickable
- Steps:
  1. Submit a review for an application with multi-word document titles (e.g. `25/00413/F`)
  2. Wait for completion
  3. Fetch the review markdown via the `urls.review_md` URL
  4. Open the markdown in a renderer (e.g. the website)
  5. Click a document link (e.g. "Delegated Officer Report")
- Expected: Link renders as a single clickable element. Clicking opens the document PDF. No trailing text after the link.

**QA-02: API urls object contains valid URLs**
- Goal: Verify the `urls` fields are valid, encoded URLs
- Steps:
  1. After a review completes, call `GET /api/v1/reviews/{id}?urls_only=true`
  2. Copy a URL from the `urls` object (e.g. `review_json`)
  3. Paste into browser
- Expected: URL works directly — no "file not found" due to space encoding issues.

---

## Appendix

### Glossary
- **Percent-encoding:** Replacing unsafe characters in URLs with `%XX` hex codes per RFC 3986 (e.g. space becomes `%20`)
- **Evidence context:** The text block assembled by the orchestrator containing document metadata, passed to the LLM for review generation

### References
- [RFC 3986 - URI Syntax](https://www.rfc-editor.org/rfc/rfc3986) — Defines percent-encoding rules
- `src/agent/orchestrator.py` lines 1289-1298 — Evidence context assembly
- `src/shared/storage.py` — Storage backend `public_url()` methods
- `src/mcp_servers/cherwell_scraper/server.py` line 464-467 — Download filename generation

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-15 | SDD | Initial specification |
