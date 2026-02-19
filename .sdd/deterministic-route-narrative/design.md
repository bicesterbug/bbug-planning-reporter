# Design: Deterministic Route Narrative

**Version:** 1.0
**Date:** 2026-02-19
**Status:** Draft
**Linked Specification** `.sdd/deterministic-route-narrative/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The review pipeline has two sources of route data:

1. **`self._route_assessments`** — A list of dicts populated during the route assessment phase by calling the cycle-route MCP tool. Each dict contains `destination`, `destination_id`, `shortest_route`, `safest_route`, and `same_route`. Each route variant has `distance_m`, `score` (with `score` and `rating` fields), and other fields.

2. **`structure.route_assessment`** — An optional field on the `ReviewStructure` Pydantic model that the LLM is asked to populate during the structure call. This field contains `RouteAssessmentSection` → `RouteDestinationItem` → `RouteDestinationSummary` models.

The orchestrator currently builds `route_narrative` by extracting data from source 2 (LLM output). However, the LLM consistently leaves this field null because the JSON tool schema marks it as `Optional[...] = None` — the schema's `default: null` overrides the prompt's instructions. The data needed already exists in source 1.

### Proposed Architecture

Replace the LLM-dependent extraction with a deterministic build:

```
Route MCP → self._route_assessments → deterministic build → route_narrative dict
```

The orchestrator builds `route_narrative` directly from `self._route_assessments` after the route assessment phase completes, before the structure call. The `route_assessment` field, its Pydantic models, and the structure prompt guidance are all removed since they are no longer needed.

### Technology Decisions

- No new dependencies — the build is a simple dict comprehension over existing data
- The `score.rating` field from the MCP result maps directly to the narrative's `rating` field
- The `score.score` field maps to `ltn_score`

---

## Modified Components

### Modified: Orchestrator review generation (`src/agent/orchestrator.py`)

**Change Description:** Currently builds `route_narrative` from `structure.route_assessment` (LLM output). Must instead build it deterministically from `self._route_assessments` (MCP output). Also remove the `narrative` string field from each destination entry.

**Dependants:** None — the `route_narrative` dict is serialised into the review JSON as-is.

**Kind:** Method

**Details**

Replace lines 1857-1871 (`route_narrative` extraction) with:

```
route_narrative = None
if self._route_assessments:
    route_narrative = {
        "destinations": [
            {
                "destination_name": ra.get("destination", "Unknown"),
                "shortest_route_summary": {
                    "distance_m": ra["shortest_route"]["distance_m"],
                    "ltn_score": ra["shortest_route"]["score"]["score"],
                    "rating": ra["shortest_route"]["score"]["rating"],
                },
                "safest_route_summary": {
                    "distance_m": ra["safest_route"]["distance_m"],
                    "ltn_score": ra["safest_route"]["score"]["score"],
                    "rating": ra["safest_route"]["score"]["rating"],
                },
                "same_route": ra.get("same_route", True),
            }
            for ra in self._route_assessments
        ]
    }
```

This extracts from `self._route_assessments` (source 1) instead of `structure.route_assessment` (source 2). The `narrative` string field is omitted per FR-004.

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

**Change Description:** Remove the `RouteAssessmentSection`, `RouteDestinationItem`, and `RouteDestinationSummary` Pydantic models. Remove the `route_assessment` field from `ReviewStructure`. These models were only used by the LLM structure call extraction path which is being replaced.

**Dependants:** `structure_prompt.py` (guidance removal), `orchestrator.py` (extraction removal), test files (model references)

**Kind:** Module (model classes)

**Details**

Remove:
- `RouteDestinationSummary` class (lines 100-112)
- `RouteDestinationItem` class (lines 115-125)
- `RouteAssessmentSection` class (lines 128-131)
- `route_assessment: RouteAssessmentSection | None = None` from `ReviewStructure` (line 153)

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

**Change Description:** Remove the entire `route_assessment` field guidance block. The LLM no longer needs to populate this field since route_narrative is built deterministically.

**Dependants:** None

**Kind:** Function

**Details**

Remove lines 93-100 (the `**route_assessment** (REQUIRED when ...)` block and the `You MUST populate ...` instruction).

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

**Provides:** List of route assessment dicts from the cycle-route MCP tool. Each dict has `destination` (str), `destination_id` (str), `shortest_route` (dict with `distance_m`, `score`, etc.), `safest_route` (same shape), and `same_route` (bool).

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
- Status: Backlog
- Requirements: [deterministic-route-narrative:FR-002]
- Test Scenarios: [deterministic-route-narrative:ReviewSchema/TS-04], [deterministic-route-narrative:ReviewSchema/TS-05], [deterministic-route-narrative:ReviewSchema/TS-06]
- Details:
  - Delete `RouteDestinationSummary`, `RouteDestinationItem`, `RouteAssessmentSection` classes
  - Delete `route_assessment` field from `ReviewStructure`
  - Replace `TestRouteAssessmentSection` tests with TS-04, TS-05, TS-06
  - Remove `RouteAssessmentSection`, `RouteDestinationSummary`, `RouteDestinationItem` imports from test file

**Task 2: Remove route_assessment guidance from structure prompt**
- Status: Backlog
- Requirements: [deterministic-route-narrative:FR-003]
- Test Scenarios: [deterministic-route-narrative:StructurePrompt/TS-07]
- Details:
  - Delete the route_assessment guidance block (lines 93-100)
  - Replace `TestStructurePromptRouteAssessment` class with a single test asserting `route_assessment` is absent

**Task 3: Build route_narrative deterministically in orchestrator**
- Status: Backlog
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
