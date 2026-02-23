# Design: Deterministic Route Narrative

**Version:** 1.1
**Date:** 2026-02-23
**Status:** Implemented
**Linked Specification** `.sdd/deterministic-route-narrative/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The review pipeline has two sources of route data:

1. **`self._route_assessments`** — A list of dicts populated during the route assessment phase by calling the cycle-route MCP tool. Each dict has a flat structure: `destination` (str), `destination_id` (str), `distance_m` (safest route distance), `shortest_route_distance_m` (shortest route distance), `score` (dict with `score` and `rating` for the safest route), `same_route` (bool), plus geometry and issue fields.

The orchestrator builds `route_narrative` deterministically from this MCP data. The `route_assessment` field and its Pydantic models (`RouteAssessmentSection`, `RouteDestinationItem`, `RouteDestinationSummary`) have been removed from `ReviewStructure`. The structure prompt no longer references `route_assessment`.

### Proposed Architecture

The deterministic build extracts from the flat MCP data:

```
Route MCP → self._route_assessments (flat dicts) → deterministic build → route_narrative dict
```

The orchestrator builds `route_narrative` directly from `self._route_assessments` after the route assessment phase completes. The `route_assessment` field, its Pydantic models, and the structure prompt guidance have all been removed.

### Technology Decisions

- No new dependencies — the build is a simple dict comprehension over existing data
- The MCP tool returns a flat structure: `score.rating` → narrative `rating`, `score.score` → narrative `ltn_score`
- When `same_route=False`, the shortest route has only `distance_m` (no separate score); `ltn_score` and `rating` are set to None
- When `same_route=True`, both summaries share the safest route's score values

---

## Modified Components

### Modified: Orchestrator review generation (`src/agent/orchestrator.py`)

**Change Description:** Builds `route_narrative` deterministically from `self._route_assessments` (flat MCP output). The `narrative` string field is omitted.

**Dependants:** None — the `route_narrative` dict is serialised into the review JSON as-is.

**Kind:** Method

**Details**

Implementation at lines 1848-1871 builds from the flat MCP data structure:

```
route_narrative = None
if self._route_assessments:
    route_narrative = {
        "destinations": [
            {
                "destination_name": ra.get("destination", "Unknown"),
                "shortest_route_summary": {
                    "distance_m": ra.get("shortest_route_distance_m", ra.get("distance_m", 0)),
                    "ltn_score": ra.get("score", {}).get("score", 0) if ra.get("same_route", True) else None,
                    "rating": ra.get("score", {}).get("rating") if ra.get("same_route", True) else None,
                },
                "safest_route_summary": {
                    "distance_m": ra.get("distance_m", 0),
                    "ltn_score": ra.get("score", {}).get("score", 0),
                    "rating": ra.get("score", {}).get("rating"),
                },
                "same_route": ra.get("same_route", True),
            }
            for ra in self._route_assessments
        ]
    }
```

The MCP tool returns a flat dict where top-level `distance_m` and `score` are for the safest route, and `shortest_route_distance_m` is the shortest route distance. When `same_route=False`, the shortest route has no separate score — `ltn_score` and `rating` are set to None.

**Requirements References**
- [deterministic-route-narrative:FR-001]: Build route_narrative deterministically from self._route_assessments
- [deterministic-route-narrative:FR-004]: Remove narrative field from data shape

**Test Scenarios**

**TS-01: route_narrative populated from route assessments**
- Given: Orchestrator has `_route_assessments` with one destination (shortest_route.score.score=35, rating="red", distance_m=2200; safest_route.score.score=72, rating="amber", distance_m=2800; same_route=False)
- When: The deterministic build runs
- Then: `route_narrative` is a dict with `destinations[0]` having `destination_name`, `shortest_route_summary` (distance_m=2200, ltn_score=35, rating="red"), `safest_route_summary` (distance_m=2800, ltn_score=72, rating="amber"), and `same_route=False`. No `narrative` field exists on any destination.

**TS-02: route_narrative is None when no assessments**
- Given: Orchestrator has empty `_route_assessments` list
- When: The deterministic build runs
- Then: `route_narrative` is None

**TS-03: route_narrative handles same_route=True**
- Given: Orchestrator has `_route_assessments` with one destination where `same_route=True` and both routes have identical values
- When: The deterministic build runs
- Then: `route_narrative.destinations[0].same_route` is True and both summaries have the same values

---

### Modified: Review schema (`src/agent/review_schema.py`)

**Change Description:** Removed the `RouteAssessmentSection`, `RouteDestinationItem`, and `RouteDestinationSummary` Pydantic models. Removed the `route_assessment` field from `ReviewStructure`.

**Dependants:** `structure_prompt.py` (guidance removal), `orchestrator.py` (extraction removal), test files (model references)

**Kind:** Module (model classes)

**Details**

Removed:
- `RouteDestinationSummary` class
- `RouteDestinationItem` class
- `RouteAssessmentSection` class
- `route_assessment: RouteAssessmentSection | None = None` from `ReviewStructure`

**Requirements References**
- [deterministic-route-narrative:FR-002]: Remove route_assessment from ReviewStructure schema

**Test Scenarios**

**TS-04: ReviewStructure has no route_assessment field**
- Given: A valid structure JSON dict (without route_assessment)
- When: `ReviewStructure.model_validate()` called
- Then: The model instance has no `route_assessment` attribute

**TS-05: ReviewStructure ignores extra route_assessment in input**
- Given: A valid structure JSON dict with `route_assessment` key present
- When: `ReviewStructure.model_validate()` called
- Then: No error raised (Pydantic ignores extra fields by default). The model instance has no `route_assessment` attribute.

**TS-06: model_json_schema excludes route_assessment**
- Given: `ReviewStructure` class
- When: `model_json_schema()` called
- Then: `route_assessment` is not in the schema's `properties`

---

### Modified: Structure prompt (`src/agent/prompts/structure_prompt.py`)

**Change Description:** Removed the `route_assessment` field guidance block. Route evidence is now provided via the `route_evidence_text` parameter instead, which gives the LLM route context for analysis without asking it to populate a schema field.

**Dependants:** None

**Kind:** Function

**Details**

Removed the `**route_assessment** (REQUIRED when ...)` block and the `You MUST populate ...` instruction.

**Requirements References**
- [deterministic-route-narrative:FR-003]: Remove route_assessment guidance from structure prompt

**Test Scenarios**

**TS-07: Structure prompt does not contain route_assessment**
- Given: Structure prompt built with default arguments
- When: System prompt text inspected
- Then: The string "route_assessment" does not appear anywhere in the system prompt

---

## Used Components

### `self._route_assessments` (orchestrator instance attribute)

**Location:** `src/agent/orchestrator.py:179`

**Provides:** List of flat route assessment dicts from the cycle-route MCP tool. Each dict has `destination` (str), `destination_id` (str), `distance_m` (safest route distance), `shortest_route_distance_m` (shortest route distance), `score` (dict with `score` int and `rating` str for the safest route), `same_route` (bool), plus `route_geojson`, `segments_geojson`, `issues`, `s106_suggestions`, and `transitions`.

**Used By:** Modified orchestrator component — source data for the deterministic route_narrative build

---

## Documentation Considerations

- None — the `route_narrative` data shape in `docs/API.md` already shows the correct fields (the `narrative` string was never included in the API docs example because it was always null in practice)

---

## Risks and Dependencies

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Old reviews in Redis have `route_assessment` in structure JSON | Low | None | Pydantic ignores extra fields; old structures still parse |
| Website expects `narrative` string field in route_narrative | Low | Low | Field was always null in production; website never consumed it. Verify with website code if concerned. |

---

## Task Breakdown

### Phase 1: Remove schema models and prompt guidance

**Task 1: Remove route_assessment models from review_schema.py**
- Status: Complete
- Requirements: [deterministic-route-narrative:FR-002]
- Test Scenarios: [deterministic-route-narrative:ReviewSchema/TS-04], [deterministic-route-narrative:ReviewSchema/TS-05], [deterministic-route-narrative:ReviewSchema/TS-06]
- Details:
  - Delete `RouteDestinationSummary`, `RouteDestinationItem`, `RouteAssessmentSection` classes
  - Delete `route_assessment` field from `ReviewStructure`
  - Replace `TestRouteAssessmentSection` tests with TS-04, TS-05, TS-06
  - Remove `RouteAssessmentSection`, `RouteDestinationSummary`, `RouteDestinationItem` imports from test file

**Task 2: Remove route_assessment guidance from structure prompt**
- Status: Complete
- Requirements: [deterministic-route-narrative:FR-003]
- Test Scenarios: [deterministic-route-narrative:StructurePrompt/TS-07]
- Details:
  - Delete the route_assessment guidance block (lines 93-100)
  - Replace `TestStructurePromptRouteAssessment` class with a single test asserting `route_assessment` is absent

**Task 3: Build route_narrative deterministically in orchestrator**
- Status: Complete
- Requirements: [deterministic-route-narrative:FR-001], [deterministic-route-narrative:FR-004]
- Test Scenarios: [deterministic-route-narrative:Orchestrator/TS-01], [deterministic-route-narrative:Orchestrator/TS-02], [deterministic-route-narrative:Orchestrator/TS-03]
- Details:
  - Replace the `structure.route_assessment` extraction (lines 1857-1871) with deterministic build from `self._route_assessments`
  - Remove the `from src.agent.review_schema import ReviewStructure` usage in `TestRouteNarrativeExtraction` — tests should exercise the deterministic build logic directly
  - Replace existing `TestRouteNarrativeExtraction` tests with TS-01, TS-02, TS-03

---

## QA Feasibility

**QA-01 (Verify route_narrative populated):** Fully testable. Submit a review for an application with route destinations. The route_narrative field is now built deterministically from MCP data, so it will always be populated when route assessments exist.

**QA-02 (Verify route_narrative absent when no routes):** Fully testable. Submit a review without route destinations. The route_narrative will be None.

**QA-03 (Verify structure call no longer includes route_assessment):** Fully testable via worker logs. The tool schema will not contain `route_assessment` since the Pydantic model field is removed.

---

## Appendix

### Glossary
- **route_narrative**: Structured summary of route assessments in the review JSON (per-destination distance, score, rating)
- **route_assessment**: The removed optional field from the structure call schema
- **structure call**: First LLM call producing structured JSON via tool_use
- **MCP**: Model Context Protocol — used for the cycle-route assessment tool

### References
- [deterministic-route-narrative spec](.sdd/deterministic-route-narrative/specification.md)
- [route-narrative-report spec](.sdd/route-narrative-report/specification.md)
- [cycle-route-assessment spec](.sdd/cycle-route-assessment/specification.md)

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-19 | Claude | Initial design |
| 1.1 | 2026-02-23 | Claude | Updated to reflect implementation: flat MCP data shape, shortest route score handling when same_route=False, all tasks marked Complete |
