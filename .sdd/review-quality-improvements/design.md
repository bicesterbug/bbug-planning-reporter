# Design: Review Quality Improvements

**Version:** 1.0
**Date:** 2026-02-16
**Status:** Draft
**Linked Specification** `.sdd/review-quality-improvements/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The review generation pipeline has three prompt files that control the LLM's behaviour:

1. **`search_query_prompt.py`** — Generates search queries for application docs and policy docs. Currently lists BICESTER_LCWIP as a valid source but provides no guidance on what to search for within it.
2. **`structure_prompt.py`** — Instructs the LLM to produce structured JSON assessment. Currently gives generic aspect guidance without nuance on proportionality, crossing types, or evidence-based compliance.
3. **`report_prompt.py`** — Instructs the LLM to expand the JSON into prose markdown. Currently follows the JSON strictly (which is correct) but inherits any gaps from the structure call.

All three prompts are pure text — modifying them requires no API changes, no schema changes, and no orchestration logic changes.

### Proposed Architecture

Modify the three prompt files to address the feedback:

1. **`search_query_prompt.py`** — Add guidance to generate LCWIP-specific queries targeting measurable local standards (path widths, crossing types, network connectivity).
2. **`structure_prompt.py`** — Add three new guidance sections: (a) proportionate parking assessment, (b) crossing type evaluation, (c) evidence-aware compliance notes. These are additions to the existing field guidance, not replacements.
3. **`report_prompt.py`** — No changes needed. The report call already follows the structure JSON faithfully; improving the structure call improves the report.

### Technology Decisions

- Prompt text changes only — no code logic changes
- Test changes are to prompt content assertions, not behaviour tests

### Quality Attributes

- **Correctness**: Prompts must not break existing schema compliance (structure call still returns valid JSON via tool use)
- **Backward compatibility**: No schema changes — the JSON structure is identical, only the content quality improves

---

## Modified Components

### search_query_prompt.py
**Change Description** Currently lists BICESTER_LCWIP as a valid source but provides no guidance on what specific local requirements to search for. Add guidance to the prompt instructing the LLM to include queries targeting LCWIP-specific measurable standards.

**Dependants** None (consumed by orchestrator, which passes queries to policy-kb MCP).

**Kind** Module (prompt text)

**Details**

Add to the `policy_queries` section of the system prompt, after the existing source list:

> When generating policy queries, ensure at least one query targets the Bicester LCWIP for local-specific standards such as:
> - Required shared path widths (may differ from LTN 1/20 minimums)
> - Crossing type requirements at junctions and access points
> - Network connectivity requirements for the site's location
> - Specific route improvements identified in the LCWIP for the area

**Requirements References**
- [review-quality-improvements:FR-002]: LCWIP-targeted policy search queries

**Test Scenarios**

**TS-01: LCWIP guidance in prompt**
- Given: The search query prompt is built
- When: The system prompt text is inspected
- Then: It contains guidance about querying BICESTER_LCWIP for path widths and crossing standards

**TS-02: LCWIP source guidance mentions specific standards**
- Given: The search query prompt is built
- When: The system prompt text is inspected
- Then: It mentions "shared path widths", "crossing type", and "network connectivity"

---

### structure_prompt.py
**Change Description** Currently provides generic aspect guidance. Add three new guidance areas: (1) proportionate parking assessment that considers applicant justification, (2) crossing type evaluation from TA text, (3) evidence-aware compliance notes distinguishing justified departures from unjustified non-compliance.

**Dependants** report_prompt.py (consumes the JSON output — but no changes needed there since the report follows the JSON).

**Kind** Module (prompt text)

**Details**

Add the following to the system prompt, integrated into the existing field guidance:

**Under the "Cycle Parking" aspect guidance** (after the existing bullet about quantity, type, location, security, accessibility):

> When assessing cycle parking quantity, consider proportionality:
> - If the applicant provides evidence for reduced provision (industry data, low staffing levels, shift patterns, phased delivery with monitoring), acknowledge the justification and assess whether the evidence is convincing
> - Note the shortfall against standards but distinguish between unjustified non-compliance and an evidence-backed departure
> - Assess whether delivery mechanisms for future provision are adequately secured (e.g. conditions, triggers, monitoring)
> - If no justification is provided for a shortfall, flag it firmly as non-compliance

**New crossing assessment guidance** (add to the aspects list, after "Junctions"):

> - "Crossing Design" or within "Cycle Routes" — assess specific crossing types described in the transport assessment (parallel, toucan, signal-controlled, uncontrolled, raised table). Evaluate whether crossing types give adequate priority to cyclists and pedestrians, considering traffic speed, visibility, and driver awareness. Flag inadequate types (e.g. uncontrolled crossings on busy internal roads, lack of cyclist priority). If the TA doesn't describe crossing designs, note the absence.

**Under the `policy_compliance` guidance**, add:

> In the "notes" field, distinguish between:
> - Unjustified non-compliance: "No justification provided for departure from [standard]"
> - Justified departure: "Applicant provides [type of evidence] to justify reduced provision. [Assessment of evidence strength]"
> - The "compliant" field remains boolean — use notes for nuance

**Requirements References**
- [review-quality-improvements:FR-001]: Proportionate parking assessment
- [review-quality-improvements:FR-003]: Crossing type assessment
- [review-quality-improvements:FR-004]: Evidence-aware compliance assessment

**Test Scenarios**

**TS-03: Proportionate parking guidance in prompt**
- Given: The structure prompt is built
- When: The system prompt text is inspected
- Then: It contains guidance about considering applicant evidence for reduced parking provision

**TS-04: Crossing type guidance in prompt**
- Given: The structure prompt is built
- When: The system prompt text is inspected
- Then: It contains guidance about assessing specific crossing types (parallel, toucan, uncontrolled)

**TS-05: Evidence-aware compliance guidance in prompt**
- Given: The structure prompt is built
- When: The system prompt text is inspected
- Then: It contains guidance about distinguishing unjustified non-compliance from justified departures in the notes field

---

## Used Components

### report_prompt.py
**Location** `src/agent/prompts/report_prompt.py`

**Provides** Report generation prompt that follows the structure JSON. No changes needed — it already faithfully expands whatever the structure call produces.

**Used By** Orchestrator (generates final markdown from improved structure JSON)

### orchestrator.py
**Location** `src/agent/orchestrator.py`

**Provides** Calls search_query_prompt, structure_prompt, and report_prompt in sequence. No changes needed — the orchestrator passes queries and evidence as-is.

**Used By** Worker (runs the full review pipeline)

---

## Documentation Considerations

- No API or schema documentation changes needed (JSON structure unchanged)
- The spec and design serve as the record of what changed and why

---

## Risks and Dependencies

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Prompt changes cause structure call to produce invalid JSON | Low | High | Tool-use schema enforcement prevents invalid JSON. Test the prompt with the live review. |
| LLM ignores new guidance and produces same output | Medium | Medium | QA-01 validates against the specific application. If guidance is ignored, strengthen wording. |
| Proportionality language makes reviews too lenient | Low | Medium | QA-02 validates that unjustified non-compliance is still firmly flagged. |
| LCWIP chunks don't contain the 3.5m requirement | Medium | Medium | If the retrieved chunks miss the requirement, this is a seeding/chunking issue to address separately. The query improvement increases the chance of retrieval. |

---

## Feasibility Review

No blockers. All changes are prompt text modifications with existing test patterns.

---

## QA Feasibility

**QA-01 (Rerun Graven Hill review):** Fully feasible — submit via API after deploying prompt changes. Compare output manually against Paul's feedback points.

**QA-02 (Standard residential application):** Fully feasible — use any previously reviewed residential application. Verify parking non-compliance is still firmly stated.

---

## Task Breakdown

### Phase 1: Prompt improvements

**Task 1: Improve search query prompt with LCWIP guidance**
- Status: Done
- Requirements: [review-quality-improvements:FR-002]
- Test Scenarios: [review-quality-improvements:search_query_prompt/TS-01], [review-quality-improvements:search_query_prompt/TS-02]
- Details:
  - Add LCWIP-specific query guidance to the system prompt in `search_query_prompt.py`
  - Update tests in `tests/test_agent/test_prompts/test_search_query_prompt.py`

**Task 2: Improve structure prompt with proportionality, crossings, and compliance nuance**
- Status: Done
- Requirements: [review-quality-improvements:FR-001], [review-quality-improvements:FR-003], [review-quality-improvements:FR-004]
- Test Scenarios: [review-quality-improvements:structure_prompt/TS-03], [review-quality-improvements:structure_prompt/TS-04], [review-quality-improvements:structure_prompt/TS-05]
- Details:
  - Add proportionate parking assessment guidance to the aspects section
  - Add crossing type assessment guidance
  - Add evidence-aware compliance guidance to the policy_compliance section
  - Update tests in `tests/test_agent/test_prompts/test_structure_prompt.py`

---

## Intermediate Dead Code Tracking

None expected.

---

## Intermediate Stub Tracking

None expected.

---

## Appendix

### References
- Live review feedback: Paul Troop (Bicester BUG chair), 2026-02-16
- Review ID: `rev_01KHHJ4WXM7AY6P18CMF1Y37BY` (application 25/03310/REM)

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-16 | Claude | Initial design |
