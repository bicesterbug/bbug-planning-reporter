# Design: Concise Output

**Version:** 1.0
**Date:** 2026-02-15
**Status:** Draft
**Linked Specification** `.sdd/concise-output/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

Review output is generated in a two-phase LLM pipeline:

1. **Structure call** (`build_structure_prompt`) — returns validated JSON via Anthropic tool_use, including `suggested_conditions`, `aspects[].analysis`, and `key_documents[].summary`
2. **Report call** (`build_report_prompt`) — expands the JSON into prose markdown, with a hardcoded title `# Cycle Advocacy Review: [ref]`

The prompts contain no conciseness constraints and no condition-format guidance. The report prompt instructs "2-5 paragraphs per aspect" and "1-2 sentences" for document summaries, which drives verbosity.

A **fallback path** in the orchestrator generates markdown in a single call if the structure call fails, with a hardcoded system prompt that also uses the generic title.

Letters already read `ADVOCACY_GROUP_STYLISED` from the environment via `_get_group_config()` in `letter_jobs.py`, but reviews do not.

### Proposed Architecture

No structural changes. All changes are prompt text edits plus wiring `group_stylised` into the report prompt builder and orchestrator call sites.

- `build_structure_prompt` — add conciseness guidance for conditions, document summaries, and aspect analysis
- `build_report_prompt` — accept `group_stylised` parameter, use it in title, add conciseness instructions
- `orchestrator.py` — read `ADVOCACY_GROUP_STYLISED` from env, pass to `build_report_prompt`, update fallback prompt

### Technology Decisions

None — prompt text changes only, plus one new function parameter.

### Quality Attributes

- **Maintainability:** Group name config follows the same `os.getenv` pattern already used in `letter_jobs.py`

---

## Modified Components

### build_structure_prompt

**Change Description:** Currently provides no format guidance for `suggested_conditions`, allows verbose `analysis` notes, and allows paragraph-length `key_documents.summary`. Add conciseness instructions to three field guidance sections.

**Dependants:** None (orchestrator call site unchanged — no signature change)

**Kind:** Function

**Details**

Changes to the system prompt string in `build_structure_prompt()`:

1. Replace the `suggested_conditions` guidance:
   ```
   Current:  "Planning conditions to attach if approval is granted. May be an empty array if no conditions are warranted."

   New:      "Planning conditions in standard LPA format. Each condition: a trigger
              (e.g. 'Prior to commencement', 'Prior to first occupation'), a requirement
              ('a scheme for X shall be submitted to and approved in writing by the local
              planning authority'), and 'Reason: [policy ref]'. Keep each condition to 1-2
              sentences plus the Reason line. May be an empty array if no conditions are warranted."
   ```

2. Replace the `analysis` guidance:
   ```
   Current:  "A detailed markdown-formatted analysis (2-4 paragraphs) covering findings, evidence references, and policy citations. This will be used by a report writer to produce detailed prose."

   New:      "Concise analysis notes for the report writer: key findings, evidence references, and policy citations. Use bullet points or short paragraphs. Do not write draft prose — the report writer will expand these notes."
   ```

3. Replace the `key_documents.summary` guidance:
   ```
   Current:  "1-2 sentences on content and cycling relevance"

   New:      "One short sentence on cycling relevance (max ~15 words)"
   ```

**Requirements References**
- [concise-output:FR-001]: Standard LPA condition format guidance
- [concise-output:FR-004]: Concise key document summaries
- [concise-output:FR-005]: Tighter aspect analysis notes

**Test Scenarios**

**TS-01: Condition format guidance present**
- Given: Default arguments
- When: `build_structure_prompt()` called
- Then: System prompt contains "standard LPA format" and "Reason:" in the suggested_conditions guidance

**TS-02: Concise analysis guidance present**
- Given: Default arguments
- When: `build_structure_prompt()` called
- Then: System prompt contains "Concise analysis notes" and does NOT contain "2-4 paragraphs"

**TS-03: Short document summary guidance present**
- Given: Default arguments
- When: `build_structure_prompt()` called
- Then: System prompt contains "max ~15 words" in the key_documents guidance

---

### build_report_prompt

**Change Description:** Currently accepts no group identity and hardcodes "Cycle Advocacy Review" as the title. Also instructs "2-5 paragraphs" per aspect and has no conciseness constraints. Changes: accept `group_stylised` parameter, use it in the title template, reduce paragraphs-per-aspect to "1-3", add conciseness instructions.

**Dependants:** `orchestrator.py` (must pass new parameter at both call sites)

**Kind:** Function

**Details**

1. Add `group_stylised: str = "Bicester BUG"` parameter to `build_report_prompt()`.

2. Replace the title template:
   ```
   Current:  # Cycle Advocacy Review: [Application Reference]
   New:      # {group_stylised} Review: [Application Reference]
   ```

3. Replace paragraphs-per-aspect guidance:
   ```
   Current:  "expand it into detailed prose (2-5 paragraphs per aspect)"
   New:      "expand into focused prose (1-3 paragraphs per aspect). Be concise: no filler, no restating the summary table, no repeating policy refs already in the compliance matrix."
   ```

4. Add conciseness rule to recommendations section:
   ```
   Current:  "Each recommendation from the JSON as a numbered item with policy justification."
   New:      "Each recommendation as a single sentence with a policy reference in parentheses."
   ```

5. Add a conciseness rule to the CRITICAL RULES section:
   ```
   Add: "- Be CONCISE throughout. Avoid filler phrases, unnecessary sub-headings, and restating information already present in tables. Each section should add insight, not repeat data."
   ```

**Requirements References**
- [concise-output:FR-002]: Concise report prose
- [concise-output:FR-003]: Branded review title

**Test Scenarios**

**TS-04: Group name in title template**
- Given: `group_stylised="Test Cyclists"`
- When: `build_report_prompt()` called
- Then: System prompt contains `# Test Cyclists Review:` and does NOT contain "Cycle Advocacy Review"

**TS-05: Default group name used when not specified**
- Given: `group_stylised` not passed (uses default)
- When: `build_report_prompt()` called
- Then: System prompt contains `# Bicester BUG Review:`

**TS-06: Concise paragraphs guidance**
- Given: Any input
- When: `build_report_prompt()` called
- Then: System prompt contains "1-3 paragraphs" and does NOT contain "2-5 paragraphs"

**TS-07: Conciseness rule present**
- Given: Any input
- When: `build_report_prompt()` called
- Then: System prompt contains "Be CONCISE"

**TS-08: Single-sentence recommendation guidance**
- Given: Any input
- When: `build_report_prompt()` called
- Then: System prompt contains "single sentence" in the recommendations section guidance

---

### Orchestrator report call sites

**Change Description:** The orchestrator calls `build_report_prompt()` at two places (line ~1532 for two-phase, line ~1590 for fallback). Both must pass `group_stylised`. The fallback system prompt override (line ~1597) must also use the group name in its title. The group name is read from `ADVOCACY_GROUP_STYLISED` env var with default "Bicester BUG".

**Dependants:** None

**Kind:** Module (`src/agent/orchestrator.py`)

**Details**

1. Near the top of the `_generate_review` method (or wherever the two-phase block starts), read the env var:
   ```
   group_stylised = os.getenv("ADVOCACY_GROUP_STYLISED", "Bicester BUG")
   ```

2. Pass to both `build_report_prompt` call sites:
   ```
   build_report_prompt(..., group_stylised=group_stylised)
   ```

3. Update the fallback system prompt string:
   ```
   Current:  "1. # Cycle Advocacy Review: [Reference]"
   New:      f"1. # {group_stylised} Review: [Reference]"
   ```

**Requirements References**
- [concise-output:FR-003]: Branded review title (wiring env var to prompt)

**Test Scenarios**

**TS-09: Fallback prompt uses group name**
- Given: Structure call fails, triggering fallback
- When: Fallback system prompt is constructed
- Then: The fallback prompt contains the group stylised name in the title template (not "Cycle Advocacy Review")

---

## Used Components

### _get_group_config (letter_jobs.py)
**Location:** `src/worker/letter_jobs.py:36-42`

**Provides:** Pattern for reading `ADVOCACY_GROUP_STYLISED` from env with default. The orchestrator will use the same `os.getenv` call directly (not importing this function, since it returns a 3-tuple and we only need one value).

**Used By:** Reference pattern only — no direct dependency.

---

## Documentation Considerations

- None. Prompt changes are internal implementation detail.

---

## Test Data

- Existing test fixtures in `test_structure_prompt.py` and `test_report_prompt.py` are sufficient
- No new test data needed — tests verify prompt string content

---

## Test Feasibility

- All tests are unit tests that call prompt builder functions and assert on string content
- No external dependencies or infrastructure needed

---

## Risks and Dependencies

- **LLM compliance risk (low):** Prompt changes guide but don't guarantee output format. The LLM may still produce verbose conditions or long prose. Mitigation: the instructions are clear and specific. Can iterate on prompt wording if needed.
- **No breaking changes:** The `group_stylised` parameter has a default value, so existing callers work without modification.

---

## Feasibility Review

- No missing features or infrastructure. All changes are to existing prompt text and one new parameter.

---

## Task Breakdown

### Phase 1: All changes

**Task 1: Add conciseness guidance to structure prompt**
- Status: Backlog
- Requirements: [concise-output:FR-001], [concise-output:FR-004], [concise-output:FR-005]
- Test Scenarios: [concise-output:build_structure_prompt/TS-01], [concise-output:build_structure_prompt/TS-02], [concise-output:build_structure_prompt/TS-03]
- Details:
  Edit `src/agent/prompts/structure_prompt.py` system prompt:
  - Replace `suggested_conditions` guidance with LPA format instructions
  - Replace `analysis` guidance with concise bullet-point instructions
  - Replace `key_documents.summary` guidance with "max ~15 words"
  Update tests in `tests/test_agent/test_prompts/test_structure_prompt.py`:
  - Add TS-01: assert "standard LPA format" and "Reason:" in system prompt
  - Add TS-02: assert "Concise analysis notes" present, "2-4 paragraphs" absent
  - Add TS-03: assert "max ~15 words" present
  - Update any existing tests that assert on the old wording

**Task 2: Add group name and conciseness to report prompt**
- Status: Backlog
- Requirements: [concise-output:FR-002], [concise-output:FR-003]
- Test Scenarios: [concise-output:build_report_prompt/TS-04], [concise-output:build_report_prompt/TS-05], [concise-output:build_report_prompt/TS-06], [concise-output:build_report_prompt/TS-07], [concise-output:build_report_prompt/TS-08]
- Details:
  Edit `src/agent/prompts/report_prompt.py`:
  - Add `group_stylised: str = "Bicester BUG"` parameter
  - Replace `# Cycle Advocacy Review:` with f-string using group_stylised
  - Change "2-5 paragraphs" to "1-3 paragraphs" with conciseness note
  - Add "Be CONCISE" rule to CRITICAL RULES
  - Change recommendation guidance to "single sentence"
  Update tests in `tests/test_agent/test_prompts/test_report_prompt.py`:
  - Add TS-04: custom group name in title
  - Add TS-05: default group name
  - Add TS-06: "1-3 paragraphs" present, "2-5 paragraphs" absent
  - Add TS-07: "Be CONCISE" present
  - Add TS-08: "single sentence" in recommendations guidance
  - Update existing test that asserts "Cycle Advocacy Review" in sections list

**Task 3: Wire group name through orchestrator**
- Status: Backlog
- Requirements: [concise-output:FR-003]
- Test Scenarios: [concise-output:Orchestrator/TS-09]
- Details:
  Edit `src/agent/orchestrator.py`:
  - Read `group_stylised = os.getenv("ADVOCACY_GROUP_STYLISED", "Bicester BUG")` before the structure/report calls
  - Pass `group_stylised=group_stylised` to both `build_report_prompt()` call sites
  - Update fallback system prompt to use f-string with group_stylised instead of hardcoded "Cycle Advocacy Review"
  Update `tests/test_agent/test_prompts/test_report_prompt.py` or `tests/test_agent/test_orchestrator.py`:
  - Add TS-09 if orchestrator has testable fallback logic, otherwise verify via integration test in QA

---

## Intermediate Dead Code Tracking

None — no dead code introduced.

---

## Intermediate Stub Tracking

None — no stubs.

---

## Appendix

### Glossary
- **LPA condition format:** Standard phrasing for planning conditions: trigger + "shall be submitted to and approved in writing by the local planning authority" + Reason line
- **Structure call:** First LLM call that produces validated JSON assessment
- **Report call:** Second LLM call that expands JSON into prose markdown

### References
- `src/agent/prompts/structure_prompt.py` — structure call prompt builder
- `src/agent/prompts/report_prompt.py` — report call prompt builder
- `src/agent/orchestrator.py` — calls both prompt builders
- `src/worker/letter_jobs.py:36-42` — existing env var pattern for group name
- `tests/test_agent/test_prompts/test_structure_prompt.py` — existing structure prompt tests
- `tests/test_agent/test_prompts/test_report_prompt.py` — existing report prompt tests

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-15 | Claude | Initial design |
