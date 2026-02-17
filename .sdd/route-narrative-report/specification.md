# Specification: Route Narrative Report

**Version:** 1.1
**Date:** 2026-02-17
**Status:** Implemented

---

## Problem Statement

The route assessment currently produces structured data (scores, segments, issues) that is passed as evidence context to the LLM during review generation. The LLM incorporates this into the Cycle Routes aspect of the review, but the route-specific analysis is buried within the general review text. There is no dedicated route assessment section that tells the story of each destination route — comparing shortest vs safest, explaining transition quality, and synthesising the LTN 1/20 compliance picture for each connection. Reviewers and planning officers need a clear per-destination narrative.

## Beneficiaries

**Primary:**
- BBUG reviewers who need a readable per-destination route assessment that explains what the numbers mean in planning terms

**Secondary:**
- Planning officers receiving reviews, who benefit from a self-contained route narrative they can reference in decision reports

---

## Outcomes

**Must Haves**
- The review output includes a dedicated "Route Assessment" section with per-destination narratives
- Each narrative is LLM-generated from the assessment statistics (dual routes, transition analysis, scoring)
- The narrative explains the shortest vs safest route trade-off, transition quality issues, and overall LTN 1/20 compliance for each destination
- The route assessment section is an optional part of the review, controlled by the existing destination_ids mechanism (omitted when no route assessment is performed)
- Statistics (distances, scores, provision percentages, barrier/crossing counts) are included alongside the narrative for verifiability

**Nice-to-haves**
- A summary paragraph comparing all destinations before the per-destination detail

---

## Explicitly Out of Scope

- Deterministic template-based report generation (the narrative is LLM-generated)
- Map or visual rendering of routes
- Changes to the destinations API
- Modifications to the route scoring algorithm (handled by route-dual-routing and route-transition-analysis specs)

---

## Functional Requirements

**FR-001: Route Assessment Section in Review Structure**
- Description: The structure prompt must define a `route_assessment` section in the review JSON structure, separate from the existing `detailed_assessment` aspects. This section contains an array of per-destination route narratives.
- Acceptance criteria: The structure JSON includes a `route_assessment` object with a `destinations` array. Each entry has: `destination_name`, `shortest_route_summary` (distance, score, rating), `safest_route_summary` (distance, score, rating), `narrative` (LLM-generated text), and `same_route` flag.
- Failure/edge cases: If no route assessment was performed (no destinations, MCP unavailable), the `route_assessment` section is omitted from the structure entirely.

**FR-002: Per-Destination Narrative Generation**
- Description: The LLM must generate a narrative paragraph for each assessed destination that explains: (1) how the shortest route compares to the safest route in distance and score, (2) key transition quality issues (barriers, crossings, side changes) if any, (3) the overall LTN 1/20 compliance picture, and (4) any S106-relevant deficiencies.
- Acceptance criteria: Each destination narrative is 3-8 sentences, references specific numbers from the assessment data, and draws a conclusion about route adequacy. The narrative must not invent data — it must be grounded in the assessment statistics provided.
- Failure/edge cases: If the shortest and safest routes are the same, the narrative notes this and focuses on the single route's quality. If transition data is unavailable, the narrative omits transition commentary.

**FR-003: Route Assessment in Report Markdown**
- Description: The report prompt must render the route assessment section as a dedicated heading (e.g. `## Route Assessment`) in the review markdown, after the detailed assessment and before recommendations. Each destination appears as a sub-section with its narrative and a statistics summary table.
- Acceptance criteria: The rendered markdown includes a `## Route Assessment` heading, with per-destination `### [Destination Name]` sub-sections containing the narrative text and a summary table showing: route type, distance, LTN 1/20 score, rating, key provision percentages.
- Failure/edge cases: If route assessment section is absent from the structure (no assessment performed), the heading is not rendered.

**FR-004: Evidence Context Enhancement**
- Description: The route evidence text passed to the LLM must include transition statistics (barrier count, crossing count, side change count, directness differential) alongside the existing distance and provision data, so the LLM has sufficient context to write meaningful narratives.
- Acceptance criteria: The evidence text for each destination includes: both routes' distances and scores, provision percentages, transition counts, and top issues. The evidence is structured clearly enough that the LLM can distinguish shortest from safest route data.
- Failure/edge cases: If transition data is unavailable, the evidence text omits transition fields (the LLM adapts based on what is provided).

**FR-005: Backward Compatibility**
- Description: Reviews that do not include route assessment (destination_ids=[] or MCP unavailable) must produce output identical to the current format. The route assessment section simply does not appear.
- Acceptance criteria: A review with no route assessment produces the same structure and report as before this feature. No empty `route_assessment: {}` stub.
- Failure/edge cases: If route assessment was attempted but all destinations failed, the section is omitted rather than showing empty results.

**FR-006: Reliable Structure Call Population**
- Description: The structure prompt must strongly direct the LLM to populate the `route_assessment` tool field when route evidence is present. The current OPTIONAL guidance is too weak — the LLM skips the field and relies on the report call to render route data from evidence context alone, bypassing structured validation.
- Acceptance criteria: When Cycling Route Assessments data is present in the user prompt, the LLM populates the `route_assessment` field in the structure call response. The prompt must use binding language (MUST/REQUIRED) conditional on route data presence, not merely OPTIONAL.
- Failure/edge cases: If the route evidence text is the default "No cycling route assessments were performed", the field must not be populated.

**FR-007: Dual-Route API Schema**
- Description: The API `RouteAssessment` model must reflect the dual-route data structure (shortest_route, safest_route, same_route) introduced by route-dual-routing. The current flat model (distance_m, score, etc. at top level) drops all dual-route fields during Pydantic serialisation, resulting in null values in the API response.
- Acceptance criteria: The `RouteAssessment` Pydantic model includes `shortest_route` and `safest_route` sub-objects (each with distance_m, duration_minutes, provision_breakdown, score, issues, s106_suggestions) and a `same_route` boolean. The API response correctly reflects the full route data stored by the orchestrator.
- Failure/edge cases: Old reviews with flat route data (if any exist) should still deserialise without error — all new fields should be optional.

---

## QA Plan

**QA-01: Verify route narrative in review output**
- Goal: Confirm the review includes a dedicated route assessment section with per-destination narratives
- Steps:
  1. Submit a review with route assessment enabled (default destinations)
  2. Wait for completion
  3. Read the review output markdown
- Expected: A `## Route Assessment` section appears with destination sub-sections, each containing a narrative paragraph and statistics table. The narrative references actual distances and scores from the assessment.

**QA-02: Verify no route section when assessment skipped**
- Goal: Confirm backward compatibility when route assessment is not performed
- Steps:
  1. Submit a review with `destination_ids: []`
  2. Wait for completion
  3. Read the review output markdown
- Expected: No `## Route Assessment` heading appears. Review is otherwise normal.

**QA-03: Verify narrative references real data**
- Goal: Confirm the LLM narrative is grounded in assessment statistics, not fabricated
- Steps:
  1. Submit a review and compare the narrative text against the raw route_assessments data in the review result
  2. Check that distances, scores, and ratings mentioned in the narrative match the structured data
- Expected: All numerical claims in the narrative match the assessment data within rounding tolerance.

---

## Open Questions

None — all questions resolved during discovery.

---

## Appendix

### Glossary
- **Route narrative**: An LLM-generated paragraph explaining the route assessment findings for a specific destination in plain English
- **Statistics table**: A structured summary of key route metrics (distance, score, provision breakdown) rendered in markdown

### References
- Depends on: `.sdd/route-dual-routing/specification.md` (dual route data structure)
- Depends on: `.sdd/route-transition-analysis/specification.md` (transition statistics)
- Existing review structure: `src/agent/prompts/structure_prompt.py`
- Existing report rendering: `src/agent/prompts/report_prompt.py`

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.1 | 2026-02-17 | Claude | Add FR-006 (reliable structure call population) and FR-007 (dual-route API schema) based on production testing |
| 1.0 | 2026-02-17 | Claude | Initial specification |
