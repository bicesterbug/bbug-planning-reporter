# Specification: Structured Review Output

**Version:** 1.0
**Date:** 2026-02-08
**Status:** Draft

---

## Problem Statement

The review generation pipeline asks Claude to produce a freeform markdown report, then uses fragile regex-based parsing (ReviewMarkdownParser) to extract structured fields (aspects, policy compliance, recommendations, conditions) into the JSON API response. This parsing breaks whenever Claude varies its formatting — inline recommendations vs. dedicated sections, emoji differences, table layout variations — and makes changing the report format extremely difficult because every format change requires updating the parser. The structured JSON fields and the markdown report are not guaranteed to match, and frequently one is populated while the other is not.

## Beneficiaries

**Primary:**
- API consumers (dashboards, letter generator, downstream tools) who need reliable structured data that is always populated and always consistent with the report
- Developers maintaining the system, who currently must update fragile regex parsers whenever Claude varies its output format

**Secondary:**
- End users (cycling advocacy groups) who receive more consistent, well-structured reports
- The letter generator, which can use authoritative structured data (recommendations, conditions) rather than relying on the markdown

---

## Outcomes

**Must Haves**
- The structured JSON fields (aspects, policy_compliance, recommendations, suggested_conditions, overall_rating, key_documents) are always populated from an authoritative source — not regex-parsed from markdown
- The markdown report matches the structured data exactly because it is constructed from the same data
- Changing the markdown report format does not require updating a parser
- The existing report structure (Assessment Summary table, Detailed Assessment sections, Policy Compliance Matrix, Recommendations, Suggested Conditions) is preserved in the rendered markdown
- The full detailed prose analysis per assessment aspect is present in the markdown report
- The ReviewMarkdownParser is removed along with its tests

**Nice-to-haves**
- The structured JSON includes brief notes/findings per aspect beyond just the rating and key issue
- Report rendering is configurable (e.g. could produce HTML or plain text in future)

---

## Explicitly Out of Scope

- Adding MCP tool access (search_application_docs, search_policy) during review generation — the evidence gathered in Phase 4 continues to be used
- Changing the Phase 4 evidence-gathering queries or strategy
- Changing the document download, ingestion, or filtering pipelines
- Retroactively fixing existing completed reviews
- Changing the letter generation pipeline (it continues to consume review data from Redis)
- Adding new assessment aspects beyond the current five (Cycle Parking, Cycle Routes, Junctions, Permeability, Policy Compliance)

---

## Functional Requirements

### FR-001: Two-Phase Review Generation
**Description:** The review generation phase (currently Phase 5 in the orchestrator) must be split into two sequential Claude API calls:
1. **Structure call**: Claude receives the application metadata and evidence chunks, and returns a JSON object containing the full structured assessment (overall rating, aspects with ratings and analysis, policy compliance items, recommendations, suggested conditions, and key documents).
2. **Report call**: Claude receives the structured JSON from phase 1 plus the original evidence, and writes a detailed prose markdown report following the established report format, using the JSON as an authoritative outline.

**Examples:**
- Positive case: Structure call returns JSON with 5 aspects, each with a rating, key_issue, and analysis notes. Report call produces a markdown report with the same 5 aspects, same ratings, and prose that expands on the analysis notes.
- Edge case: If the structure call returns an aspect with rating "red" and key_issue "No cycle parking", the markdown report must reflect that same rating and issue in the Assessment Summary table and Detailed Assessment section.
- Edge case: If the structure call fails or returns invalid JSON, the system falls back to a single-call approach producing markdown only (graceful degradation).

### FR-002: Structured JSON Schema
**Description:** The structure call must return a JSON object conforming to a defined schema. The schema must include all fields needed by the API response (`ReviewContent` in schemas.py):

The JSON object must contain:
- `overall_rating`: string, one of "red", "amber", "green"
- `aspects`: array of objects, each with `name` (string), `rating` (string: red/amber/green), `key_issue` (string), and `analysis` (string — markdown-formatted analysis notes for the report writer)
- `policy_compliance`: array of objects, each with `requirement` (string), `policy_source` (string), `compliant` (boolean), and `notes` (string or null)
- `recommendations`: array of strings, each a specific actionable recommendation
- `suggested_conditions`: array of strings, each a suggested planning condition (may be empty array if none warranted)
- `key_documents`: array of objects, each with `title` (string), `category` (string: "Transport & Access" | "Design & Layout" | "Application Core"), `summary` (string), and `url` (string or null)

**Examples:**
- Positive case: All fields are populated with valid data matching the schema
- Edge case: `suggested_conditions` is an empty array `[]` when the review finds none — the field is present but empty, not null
- Negative case: Invalid JSON from Claude — system must detect and fall back gracefully (see FR-001)

### FR-003: Markdown Report Rendering from JSON
**Description:** The report call must produce a markdown report that uses the structured JSON as its authoritative source for all quantitative/structured data (ratings, compliance verdicts, recommendation lists) while adding detailed prose analysis. The system prompt for the report call must instruct Claude to:
- Use the exact ratings and key issues from the JSON in the Assessment Summary table
- Use the exact compliance verdicts from the JSON in the Policy Compliance Matrix
- Use the JSON recommendations as the basis for the Recommendations section
- Use the JSON conditions as the basis for the Suggested Conditions section
- Expand the `analysis` field of each aspect into a full detailed prose section
- List key documents in the Key Documents section matching the JSON

**Examples:**
- Positive case: The Assessment Summary table in the markdown has the same aspect names, ratings, and key issues as the JSON `aspects` array
- Positive case: The Policy Compliance Matrix rows match the JSON `policy_compliance` array exactly (same requirement text, policy source, compliance verdict)
- Negative case: The markdown report must NOT contain aspects, compliance items, or recommendations that are not in the JSON

### FR-004: Structured Data as API Source of Truth
**Description:** The orchestrator must use the structured JSON from the structure call (FR-001 phase 1) as the source for all fields in the `ReviewResult.review` dict. The JSON fields (aspects, policy_compliance, recommendations, suggested_conditions, overall_rating, key_documents) must come directly from the parsed structure call response — not from regex parsing of the markdown.

**Examples:**
- Positive case: `review["aspects"]` in Redis contains the exact data from the structure call JSON
- Positive case: `review["full_markdown"]` contains the report from the report call
- Positive case: `review["key_documents"]` comes from the structure call JSON, not from a `key_documents_json` code block in the markdown

### FR-005: Remove ReviewMarkdownParser
**Description:** The `ReviewMarkdownParser` class in `src/agent/review_parser.py` and its tests in `tests/test_agent/test_review_parser.py` must be removed. All imports and usage of `ReviewMarkdownParser` in the orchestrator must be removed. The `key_documents_json` code block parsing in the orchestrator must also be removed (key_documents now comes from the structure call).

**Examples:**
- Positive case: `src/agent/review_parser.py` is deleted
- Positive case: `tests/test_agent/test_review_parser.py` is deleted
- Positive case: The orchestrator no longer imports or uses `ReviewMarkdownParser`
- Positive case: The system prompt no longer asks Claude to produce a `key_documents_json` code block

### FR-006: Report Format Preservation
**Description:** The rendered markdown report must preserve the established report structure that users and the letter generator depend on:

1. `# Cycle Advocacy Review: [Reference]`
2. `## Application Summary` — metadata bullet list
3. `## Key Documents` — categorised document links with summaries
4. `## Assessment Summary` — overall rating + aspect summary table
5. `## Detailed Assessment` — subsections for each aspect with full prose
6. `## Policy Compliance Matrix` — table with requirement, source, compliant, notes
7. `## Recommendations` — numbered list of recommendations
8. `## Suggested Conditions` — numbered list of conditions (if any)

**Examples:**
- Positive case: The letter generator (`letter_prompt.py`) continues to work unchanged — it reads `review["full_markdown"]` and passes it to Claude for letter writing
- Edge case: If there are no suggested conditions, the section may be omitted or contain "No suggested conditions" rather than an empty section

### FR-007: Graceful Fallback on Structure Call Failure
**Description:** If the structure call (phase 1) fails — due to invalid JSON, API error, or timeout — the system must fall back to the current single-call approach: ask Claude for a markdown report and attempt best-effort extraction of structured fields. This ensures reviews still complete even if the new two-phase approach encounters issues.

**Examples:**
- Positive case: Structure call returns truncated/invalid JSON → system logs a warning, falls back to single markdown call, structured fields may be null
- Positive case: Structure call API timeout → system retries once, then falls back
- Negative case: Both the structure call AND the fallback markdown call fail → review fails with an error (same as today)

---

## Non-Functional Requirements

### NFR-001: Token Budget
**Category:** Performance
**Description:** The combined token usage of both Claude API calls (structure + report) must not exceed 16,000 output tokens total. The structure call should target 2,000-4,000 output tokens for the JSON. The report call should use up to 12,000 output tokens for the detailed markdown.
**Acceptance Threshold:** Combined output tokens ≤ 16,000; total cost per review ≤ 2x current cost
**Verification:** Observability — log token usage for both calls

### NFR-002: Latency
**Category:** Performance
**Description:** The two-phase approach will increase review generation time due to the additional API call. The total generation time (both calls) should remain reasonable.
**Acceptance Threshold:** Total review generation phase ≤ 8 minutes (current single call takes ~3 minutes)
**Verification:** Observability — log phase duration

### NFR-003: Data Consistency
**Category:** Reliability
**Description:** The structured JSON fields in the API response must be consistent with the markdown report. Every aspect rating, compliance verdict, recommendation, and condition in the JSON must appear in the markdown.
**Acceptance Threshold:** Zero inconsistencies between JSON fields and markdown content for the same review
**Verification:** Testing — integration test comparing JSON fields against markdown content

### NFR-004: Backward Compatibility
**Category:** Compatibility
**Description:** The API response schema (`ReviewContent`, `ReviewResponse`) must not change. Existing API consumers must continue to work. The letter generator must continue to work unchanged.
**Acceptance Threshold:** No changes to Pydantic response models; letter generator tests pass unchanged
**Verification:** Existing test suite passes

### NFR-005: Structured Field Completeness
**Category:** Reliability
**Description:** When the two-phase approach succeeds, all structured fields (aspects, policy_compliance, recommendations, suggested_conditions, overall_rating, key_documents) must be populated — not null. Empty arrays are acceptable where no items exist (e.g. no suggested conditions), but null fields indicate a parsing failure.
**Acceptance Threshold:** All six structured fields are non-null on successful two-phase completion
**Verification:** Testing — assert non-null on all fields after successful generation

---

## Open Questions

None.

---

## Appendix

### Current Architecture (to be replaced)

```
Evidence (Phase 4) → Single Claude call → Markdown output
                                              ↓
                                    ReviewMarkdownParser (regex)
                                              ↓
                                    Structured fields (fragile, often null)
```

### Target Architecture

```
Evidence (Phase 4) → Structure call → JSON (authoritative)
                          ↓                    ↓
                     Report call         API response fields
                          ↓
                     Markdown report → review["full_markdown"]
```

### Glossary
- **Structure call**: The first Claude API call that returns a structured JSON assessment
- **Report call**: The second Claude API call that produces a detailed markdown report from the JSON outline
- **Two-phase approach**: The combined structure call + report call strategy
- **Fallback**: Reverting to the current single-call markdown approach when the structure call fails

### References
- [review-output-fixes specification](../review-output-fixes/specification.md) — Previous approach using regex parsing
- [key-documents specification](../key-documents/specification.md) — Key documents JSON extraction
- [agent-integration specification](../agent-integration/specification.md) — Review generation orchestrator
- [response-letter specification](../response-letter/specification.md) — Letter generator (downstream consumer)

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-08 | Claude Opus 4.6 | Initial specification |
