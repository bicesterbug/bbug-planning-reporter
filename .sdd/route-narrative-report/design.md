# Design: Route Narrative Report

**Specification:** [specification.md](specification.md)
**Status:** Implemented

---

## Architecture

The route narrative feature adds a dedicated `## Route Assessment` section to the review output. It uses the existing two-phase review generation pipeline:

1. **Structure call** — The LLM generates a `route_assessment` object with per-destination narratives grounded in the route assessment statistics (distances, LTN 1/20 scores, transition data).
2. **Report call** — The LLM renders the route assessment as a markdown section with narrative prose and statistics tables.

### Data Flow

```
Route assessments (MCP)
    → Evidence context (orchestrator)
        → Structure call prompt (LLM generates route_assessment JSON)
            → Pydantic validation (RouteAssessmentSection)
                → Report call prompt (LLM renders ## Route Assessment)
                    → Review output (route_narrative in API response)
```

## Implementation

### New Pydantic Models (`review_schema.py`)

- `RouteDestinationSummary` — distance_m, ltn_score, rating (with case normalisation)
- `RouteDestinationItem` — destination_name, shortest/safest summaries, narrative, same_route
- `RouteAssessmentSection` — destinations list (min_length=1)
- `ReviewStructure.route_assessment` — optional field (None when no assessment)

### Prompt Changes

**Structure prompt** — The `route_assessment` field guidance uses conditional binding language: when Cycling Route Assessments data is present in the user prompt, the LLM MUST populate this field. The guidance specifies:
- Destinations array extracted from the route evidence data
- Per-destination shortest/safest route summaries with exact distances, scores, ratings
- 3-8 sentence narrative grounded in the assessment statistics
- same_route flag
- Must not invent data
- Must NOT include the field when no route data is provided

**Report prompt** — Added section 6 (Route Assessment) between Detailed Assessment and Policy Compliance Matrix. Includes:
- Conditional rendering (omit when absent)
- Per-destination subsections with narrative and statistics table
- Two-column table (shortest/safest) or single-column (same_route)
- Binding rules for exact data usage

### Orchestrator Changes

- Extracts `route_narrative` dict from validated structure
- Adds to review result dict alongside existing `route_assessments`
- Fallback path sets `route_narrative = None`
- Fallback section list includes Route Assessment placeholder

### API Schema Changes

- `ReviewContent.route_narrative: dict[str, Any] | None = None` — plain dict, backward compatible
- `RouteAssessment` model updated to dual-route structure with `shortest_route` and `safest_route` sub-objects plus `same_route` boolean

## Components

### Modified: Structure Prompt (`src/agent/prompts/structure_prompt.py`)

**Requirements:** [FR-001], [FR-002], [FR-006]

The `route_assessment` field guidance must be changed from OPTIONAL to conditionally REQUIRED. When the Cycling Route Assessments section in the user prompt contains actual route data (not the default "No cycling route assessments were performed"), the LLM MUST populate the `route_assessment` field.

The guidance should:
- Use "REQUIRED when route data is present" instead of "OPTIONAL"
- Include explicit instruction: "You MUST populate route_assessment when Cycling Route Assessments data is provided above"
- Keep the "Do NOT include" instruction for when no route data is present

**Test scenarios:**

- `TS-01`: Given structure prompt built with default args, when system prompt inspected, then "MUST populate route_assessment" is present
- `TS-02`: Given structure prompt, when system prompt inspected, then "Do NOT include route_assessment" guidance for absent data is present

### Modified: API RouteAssessment Model (`src/api/schemas.py`)

**Requirements:** [FR-007]

The `RouteAssessment` model must be restructured to match the dual-route data shape produced by the orchestrator:

Current flat model fields (`distance_m`, `duration_minutes`, `provision_breakdown`, `score`, `issues`, `s106_suggestions`) become fields within `shortest_route` and `safest_route` sub-objects. A new `RouteData` model holds the per-route fields. Top-level fields are `destination`, `destination_id`, `shortest_route`, `safest_route`, `same_route`. All fields optional for backward compatibility.

**Test scenarios:**

- `TS-03`: Given a ReviewContent with dual-route route_assessments data, when serialized, then shortest_route and safest_route sub-objects are present with distance_m, score, etc.
- `TS-04`: Given a ReviewContent with no route_assessments, when serialized, then route_assessments is None (backward compatible)
- `TS-05`: Given a ReviewContent with route_assessments containing same_route=true, when serialized, then same_route field is true

### Modified: API Docs (`docs/API.md`)

**Requirements:** [FR-007]

Update the `route_assessments` example in the review response to show the dual-route structure.

## Task Breakdown

### Phase 1: Fix prompt reliability and API schema

**Task 1: Strengthen structure prompt route_assessment guidance**
- Change "OPTIONAL" to conditionally REQUIRED with binding language
- Test scenarios: [TS-01], [TS-02]

**Task 2: Update RouteAssessment API model to dual-route structure**
- Add `RouteData` model for per-route fields
- Restructure `RouteAssessment` with shortest_route, safest_route, same_route
- Keep all fields optional for backward compatibility
- Test scenarios: [TS-03], [TS-04], [TS-05]

**Task 3: Update API docs route_assessments example**
- Update docs/API.md route_assessments example to dual-route format

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| LLM still ignores route_assessment despite stronger prompt | Medium | Low | The report call renders route data from evidence context regardless; route_narrative is supplementary structured data |
| Old reviews with flat route data in Redis | Low | Low | All new RouteAssessment fields are optional; old data deserialises with None for new fields |

## Key Decisions

1. **LLM-generated narratives** — The narrative is written by the LLM during the structure call, constrained by the prompt to reference real data. This allows natural language that explains what the numbers mean in planning terms.
2. **Optional section** — The entire route_assessment is omitted (not empty) when no assessment was performed, maintaining backward compatibility.
3. **Plain dict in API for route_narrative** — The API schema uses `dict[str, Any]` rather than nested Pydantic models since the data is already validated by `review_schema.py` in the orchestrator.
4. **Conditional binding over OPTIONAL** — Production testing showed the LLM ignores OPTIONAL fields. Using "MUST when data is present" ensures reliable population.
5. **Nested RouteData model** — A shared `RouteData` model for both shortest_route and safest_route avoids field duplication and matches the MCP server output shape.
