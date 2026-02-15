# Specification: Concise Output

**Version:** 1.0
**Date:** 2026-02-15
**Status:** Draft

---

## Problem Statement

The system's review output is too verbose in three areas: suggested conditions read as discursive paragraphs rather than standard planning conditions, the markdown report is longer than necessary for clear communication, and reviews are not branded with the advocacy group name. This reduces readability and makes outputs feel unprofessional compared to real planning consultation responses.

## Beneficiaries

**Primary:**
- Planning case officers who read the review/letter — concise conditions and reports are easier to act on

**Secondary:**
- Advocacy group members who review outputs before submission — quicker to review and approve
- The website visitors who read published reviews

---

## Outcomes

**Must Haves**
- Suggested conditions use standard LPA condition language, not verbose prose
- Markdown report prose is tighter — no padding, no repetition, no unnecessary sub-headings
- Review title includes the advocacy group stylised name (from `ADVOCACY_GROUP_STYLISED` env var)

**Nice-to-haves**
- Key document summaries reduced to a single short sentence each

---

## Explicitly Out of Scope

- Changing the report section structure (the 8-section layout stays)
- Changing the letter prompt or letter output format
- Changing the structured JSON schema (field names, types)
- Changing the aspect analysis field in the structure call (it feeds the report writer)
- Setting a hard line-count limit on reports

---

## Functional Requirements

**FR-001: Standard LPA condition format**
- Description: The structure call prompt must instruct the LLM to write suggested conditions in standard Local Planning Authority condition format: a trigger ("Prior to commencement / Prior to occupation / Within X months of commencement"), a requirement ("a scheme for X shall be submitted to and approved in writing by the local planning authority"), and a reason citing the relevant policy. Each condition is one concise sentence plus a "Reason:" line.
- Acceptance criteria: Every suggested condition in the structure call output follows the pattern `[Trigger], [requirement shall be submitted/approved/implemented]. Reason: [policy reference].` No condition exceeds ~3 sentences.
- Failure/edge cases: If no conditions are warranted the array remains empty (existing behaviour, unchanged).

**FR-002: Concise report prose**
- Description: The report call system prompt must instruct the LLM to write concise prose: no filler phrases, no restating what was already said in the summary table, no repeating policy references already in the compliance matrix. Each detailed assessment sub-section should be 1-3 focused paragraphs (reduced from 2-5). Recommendations should be single sentences, not paragraphs.
- Acceptance criteria: The report prompt contains explicit instructions to be concise. Detailed assessment guidance says 1-3 paragraphs per aspect (not 2-5). Recommendations section says one sentence per item.
- Failure/edge cases: For complex applications with many aspects the report will naturally be longer, but each individual section should still be tight.

**FR-003: Branded review title**
- Description: The review title must include the advocacy group's stylised name, read from the `ADVOCACY_GROUP_STYLISED` environment variable (defaulting to "Bicester BUG"). The format changes from `# Cycle Advocacy Review: [ref]` to `# [Group Stylised] Review: [ref]`.
- Acceptance criteria: The report prompt template uses the group stylised name in the title. The `build_report_prompt` function accepts a `group_stylised` parameter. The orchestrator passes this value through from environment config.
- Failure/edge cases: If `ADVOCACY_GROUP_STYLISED` is not set, falls back to the default "Bicester BUG". The fallback prompt in the orchestrator must also use the group name.

**FR-004: Concise key document summaries**
- Description: The structure call prompt must instruct the LLM to write key document summaries as a single short sentence (max ~15 words) describing the document's cycling relevance, not a paragraph.
- Acceptance criteria: The structure prompt field guidance for key_documents.summary says "1 short sentence on cycling relevance (max ~15 words)".
- Failure/edge cases: None — this is purely prompt guidance.

**FR-005: Tighter aspect analysis notes**
- Description: The structure call prompt must instruct the LLM to write the aspect `analysis` field as concise notes for the report writer — key findings, evidence references, and policy citations in bullet-point or short-paragraph form — not as draft prose. This prevents the report call from expanding already-verbose analysis into even more verbose prose.
- Acceptance criteria: The structure prompt field guidance for aspects.analysis says analysis notes should be concise bullet points or short paragraphs covering findings, evidence refs, and policy citations. Not draft prose.
- Failure/edge cases: None — purely prompt guidance.

---

## QA Plan

**QA-01: Conditions use standard format**
- Goal: Verify suggested conditions read like real planning conditions
- Steps:
  1. Submit a review request for a planning application that warrants conditions
  2. Retrieve the completed review from the API
  3. Read the `suggested_conditions` array in the review result
- Expected: Each condition follows the LPA pattern: trigger + "shall be submitted/approved" + Reason line. No condition is longer than ~3 sentences.

**QA-02: Report is noticeably shorter**
- Goal: Verify the markdown report is more concise than before
- Steps:
  1. Submit a review request for the same application used previously (25/00284/F or similar)
  2. Retrieve the full markdown from the review result
  3. Compare length and readability with the previous version
- Expected: Report is noticeably shorter. Detailed assessment sections are 1-3 paragraphs each. Recommendations are single sentences. Key document summaries are one short sentence each.

**QA-03: Review title includes group name**
- Goal: Verify the review title uses the stylised group name
- Steps:
  1. Set `ADVOCACY_GROUP_STYLISED=Bicester BUG` in the environment
  2. Submit a review request
  3. Read the full markdown output
- Expected: Title reads `# Bicester BUG Review: [ref]`, not `# Cycle Advocacy Review: [ref]`.

---

## Open Questions

None.

---

## Appendix

### Glossary
- **LPA:** Local Planning Authority — the council body that determines planning applications
- **LPA condition format:** The standard phrasing used in planning conditions attached to approvals, e.g. "Prior to occupation, a scheme for cycle parking shall be submitted to and approved in writing by the local planning authority."

### References
- Current structure prompt: `src/agent/prompts/structure_prompt.py`
- Current report prompt: `src/agent/prompts/report_prompt.py`
- Current review schema: `src/agent/review_schema.py`
- Orchestrator (passes config): `src/agent/orchestrator.py`
- Letter jobs (existing group name env var pattern): `src/worker/letter_jobs.py`
- Example verbose output: `output/25_00284_F_review.md`

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-15 | Claude | Initial specification |
