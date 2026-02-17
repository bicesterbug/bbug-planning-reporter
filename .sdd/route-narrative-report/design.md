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

**Structure prompt** — Added `route_assessment` field guidance marked as OPTIONAL, with instructions for 3-8 sentence narratives grounded in statistics. Must not invent data.

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

### API Schema

- `ReviewContent.route_narrative: dict[str, Any] | None = None` — plain dict, backward compatible

## Key Decisions

1. **LLM-generated narratives** — The narrative is written by the LLM during the structure call, constrained by the prompt to reference real data. This allows natural language that explains what the numbers mean in planning terms.
2. **Optional section** — The entire route_assessment is omitted (not empty) when no assessment was performed, maintaining backward compatibility.
3. **Plain dict in API** — The API schema uses `dict[str, Any]` rather than nested Pydantic models since the data is already validated by `review_schema.py` in the orchestrator.
