# Design: Route Transition Analysis

**Version:** 1.0
**Date:** 2026-02-17
**Status:** Draft
**Linked Specification** `.sdd/route-transition-analysis/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The cycle route assessment pipeline in `src/mcp_servers/cycle_route/server.py` evaluates routes through a sequential process within `_assess_single_route()`:

1. Build Overpass query via `build_overpass_query()` — queries ways with `["highway"]` tag within a 20m buffer of sampled route coordinates, using `out geom`
2. POST to Overpass API
3. `parse_overpass_ways()` — parses way elements into `RouteSegment` objects
4. `detect_parallel_provision()` — upgrades road segments where adjacent cycleways exist
5. `summarise_provision()` — provision breakdown by distance
6. `score_route()` — LTN 1/20 score with 5 factors totalling 100 points (segregation: 40, speed: 25, surface: 15, directness: 10, junctions: 10)
7. `identify_issues()` — segment-level issue detection
8. `generate_s106_suggestions()` — S106 suggestions from issues
9. Return assessment dict with distance, provision, score, issues, s106, segments, geometry

The Overpass query currently fetches **only ways** (no nodes). The `RouteSegment` dataclass tracks per-segment provision, highway type, speed, surface, and lit status, but has no concept of transitions between segments. The scoring system in `scoring.py` evaluates each factor independently with no consideration of how provision changes along the route.

The orchestrator's `_build_evidence_context()` (line 1458) and `_build_route_evidence_summary()` (line 1576) iterate `_route_assessments` to produce evidence text for the LLM, showing provision, scores, issues, and parallel detection — but no transition statistics.

### Proposed Architecture

1. **Overpass query extension (NFR-001)**: Modify `build_overpass_query()` to include node queries for barriers and crossings inside the existing union block, alongside the way query. This adds barrier nodes (cycle_barrier, bollard, gate, stile, lift_gate) within 15m and crossing nodes within 20m. A single Overpass call returns both ways and nodes — no extra API round-trip.

2. **Transition analysis function**: Add `analyse_transitions()` to `infrastructure.py`. Takes the parsed segments and raw Overpass data (now containing nodes). Detects barriers (from barrier-tagged nodes), non-priority crossings (from segment transitions cross-referenced with crossing nodes), side changes (off-road to road to off-road patterns), and directness differential (from parallel detection data). Returns a structured `transitions` dict.

3. **Transition scoring**: Add `_score_transitions()` to `scoring.py` and a new `MAX_TRANSITION_POINTS = 10` constant. Redistribute existing weights: segregation 40→36, speed 25→23, surface 15→13, directness 10→9, junctions 10→9. Total remains 100. Transition scoring penalises barriers, non-priority crossings on fast roads, and side changes.

4. **Pipeline integration**: In `_assess_single_route()`, call `analyse_transitions()` after parallel detection. Pass the transitions object to `score_route()`. Include `transitions` in the assessment output dict.

5. **Orchestrator integration**: Add transition statistics (barrier count, non-priority crossing count, side change count, directness differential) to both `_build_evidence_context()` and `_build_route_evidence_summary()`.

6. **Graceful failure**: If transition analysis raises an exception, return a fallback transitions object with `unavailable: true`, empty arrays, and a neutral transition score of 5/10.

### Technology Decisions

- Node queries are added to the existing Overpass union block — no new external API dependencies
- Barrier and crossing detection uses OSM node tags, which are well-standardised
- Side change detection uses a simplified heuristic (off-road→road→off-road pattern) rather than full geometric side determination, to avoid complexity
- Directness differential reuses existing parallel detection data where available

### Quality Attributes

- **Rate limiting (NFR-001)**: No additional Overpass API calls — node queries are batched into the existing query
- **Graceful degradation**: Transition analysis failure does not block route assessment; a neutral score is assigned
- **Backward compatibility**: The `transitions` object is a new additive field; existing consumers that do not read it are unaffected

---

## Modified Components

### src/mcp_servers/cycle_route/infrastructure.py — build_overpass_query

**Change Description** Currently queries only ways with `["highway"]` tag. Must add node queries for barriers (within 15m) and crossings (within 20m) to the same union block, so both are returned in a single Overpass response.

**Dependants** `_assess_single_route()` in server.py (gains access to node data), `analyse_transitions()` (new, consumes nodes)

**Kind** Function

**Details**

The current query structure is:
```
[out:json][timeout:25];
(
  way(around:{buffer_m},{coords_str})["highway"];
);
out geom;
```

Add two node queries inside the union block:
```
[out:json][timeout:25];
(
  way(around:{buffer_m},{coords_str})["highway"];
  node(around:15,{coords_str})["barrier"~"cycle_barrier|bollard|gate|stile|lift_gate"];
  node(around:20,{coords_str})["crossing"];
);
out geom;
```

The `coords_str` is the same sampled coordinate string already computed. The node queries use fixed buffer distances (15m for barriers, 20m for crossings) rather than the configurable `buffer_m` parameter, since these are specification requirements. The `out geom` directive returns coordinates for both ways and nodes.

**Requirements References**
- [route-transition-analysis:FR-001]: Barrier nodes within 15m of route
- [route-transition-analysis:FR-002]: Crossing nodes near route
- [route-transition-analysis:NFR-001]: Batched into existing Overpass query

**Test Scenarios**

**TS-01: Overpass query includes barrier node query**
- Given: A set of route coordinates
- When: `build_overpass_query()` is called
- Then: The query string contains `node(around:15,` and `["barrier"~"cycle_barrier|bollard|gate|stile|lift_gate"]`

**TS-02: Overpass query includes crossing node query**
- Given: A set of route coordinates
- When: `build_overpass_query()` is called
- Then: The query string contains `node(around:20,` and `["crossing"]`

**TS-03: Overpass query retains existing way query**
- Given: A set of route coordinates
- When: `build_overpass_query()` is called
- Then: The query string still contains `way(around:` and `["highway"]`

---

### src/mcp_servers/cycle_route/scoring.py — score_route

**Change Description** Currently calculates 5 scoring factors with constants summing to 100. Must: (a) reduce existing factor weights proportionally to make room for 10 transition points, (b) accept an optional `transitions` parameter, (c) call `_score_transitions()` when transitions data is available, (d) include `transition_quality` in breakdown and `max_points`.

**Dependants** `_assess_single_route()` in server.py (passes transitions), test assertions on max points and breakdown keys

**Kind** Module (constants + function)

**Details**

Update constants:
```python
MAX_SEGREGATION_POINTS = 36   # was 40
MAX_SPEED_POINTS = 23         # was 25
MAX_SURFACE_POINTS = 13       # was 15
MAX_DIRECTNESS_POINTS = 9     # was 10
MAX_JUNCTION_POINTS = 9       # was 10
MAX_TRANSITION_POINTS = 10    # new
```

Add `transitions: dict | None = None` parameter to `score_route()`. When provided and not `unavailable`, call `_score_transitions(transitions)`. When `transitions` is None (caller has not adopted transition analysis yet) or has `unavailable: true`, award `MAX_TRANSITION_POINTS / 2` (5 points) as a neutral default.

Add `transition_quality` to the `breakdown` and `max_points` dicts in the return value.

**Requirements References**
- [route-transition-analysis:FR-005]: New transition_quality scoring factor (10 points), redistribute weights, total stays 100
- [route-transition-analysis:FR-005]: Neutral 5/10 when transition analysis unavailable

**Test Scenarios**

**TS-04: Updated max points sum to 100**
- Given: All MAX_*_POINTS constants
- When: Summed
- Then: Total equals 100

**TS-05: Score breakdown includes transition_quality**
- Given: A set of segments and transitions data
- When: `score_route()` is called with transitions
- Then: `breakdown` dict contains `transition_quality` key and `max_points` contains `transition_quality: 10`

**TS-06: Full transition score with no barriers or crossings**
- Given: Transitions with 0 barriers, 0 non-priority crossings, 0 side changes
- When: `score_route()` is called
- Then: `breakdown["transition_quality"]` equals `MAX_TRANSITION_POINTS` (10)

**TS-07: Barriers reduce transition score**
- Given: Transitions with 3 barriers, 0 crossings, 0 side changes
- When: `score_route()` is called
- Then: `breakdown["transition_quality"]` is less than `MAX_TRANSITION_POINTS`

**TS-08: Neutral transition score when unavailable**
- Given: Transitions with `unavailable: true`
- When: `score_route()` is called
- Then: `breakdown["transition_quality"]` equals 5.0 (half of MAX_TRANSITION_POINTS)

**TS-09: Neutral transition score when transitions is None**
- Given: `score_route()` called without transitions parameter
- When: Score is calculated
- Then: `breakdown["transition_quality"]` equals 5.0

**TS-10: Existing scoring factor weights reduced proportionally**
- Given: A fully segregated route with good surface
- When: `score_route()` is called
- Then: `max_points["segregation"]` is 36, `max_points["speed_safety"]` is 23, `max_points["surface_quality"]` is 13, `max_points["directness"]` is 9, `max_points["junction_safety"]` is 9

---

### src/mcp_servers/cycle_route/server.py — _assess_single_route

**Change Description** Currently calls `build_overpass_query`, parses ways, runs parallel detection, scores, identifies issues, and returns assessment. Must: (a) call `analyse_transitions()` after parallel detection, (b) pass transitions to `score_route()`, (c) include `transitions` in the returned assessment dict, (d) handle transition analysis failure gracefully.

**Dependants** `_assess_cycle_route()` (returns transitions in assessment), orchestrator evidence builders

**Kind** Method

**Details**

After the `detect_parallel_provision(segments, overpass_data)` call, add:

```
try:
    transitions = analyse_transitions(segments, overpass_data)
except Exception:
    logger.warning("Transition analysis failed", destination=dest_name)
    transitions = {
        "unavailable": True,
        "barriers": [],
        "non_priority_crossings": [],
        "side_changes": [],
        "directness_differential": None,
    }
```

Pass transitions to `score_route`:
```
route_score = score_route(segments, cycling_distance_m, transitions=transitions)
```

Add `"transitions": transitions` to the returned assessment dict.

**Requirements References**
- [route-transition-analysis:FR-006]: Return transitions object in assessment output for both routes
- [route-transition-analysis:FR-005]: Graceful failure with neutral score

**Test Scenarios**

**TS-11: Assessment includes transitions object**
- Given: Valid OSRM route and Overpass response with way and node elements
- When: `_assess_single_route()` completes
- Then: The returned dict contains a `transitions` key with `barriers`, `non_priority_crossings`, `side_changes`, and `directness_differential` fields

**TS-12: Assessment handles transition analysis failure**
- Given: Valid route but Overpass response that causes analyse_transitions to raise
- When: `_assess_single_route()` completes
- Then: The returned dict contains `transitions` with `unavailable: true` and score has `transition_quality` at 5

**TS-13: Transitions present in both shortest and safest routes**
- Given: OSRM returns alternatives with different routes
- When: `_assess_cycle_route()` completes
- Then: Both `shortest_route` and `safest_route` contain `transitions` objects

---

### src/agent/orchestrator.py — _build_evidence_context

**Change Description** Currently iterates route assessments and builds evidence text showing provision, scores, issues, S106, and parallel detection. Must add transition statistics (barrier count, non-priority crossing count, side change count, directness differential) to the route evidence text.

**Dependants** Structure and report prompts (via evidence text)

**Kind** Method

**Details**

Within the `for label, route_data in routes_to_show:` loop, after the parallel detection upgrade count block, add:

```
transitions = route_data.get("transitions", {})
if transitions and not transitions.get("unavailable"):
    barrier_count = len(transitions.get("barriers", []))
    crossing_count = len(transitions.get("non_priority_crossings", []))
    side_change_count = len(transitions.get("side_changes", []))
    directness = transitions.get("directness_differential")

    route_lines.append(
        f"- Transitions: {barrier_count} barriers, "
        f"{crossing_count} non-priority crossings, "
        f"{side_change_count} side changes"
    )
    if directness is not None:
        route_lines.append(f"- Directness differential: {directness:.2f}")
```

**Requirements References**
- [route-transition-analysis:FR-006]: Transition statistics in evidence text

**Test Scenarios**

**TS-14: Evidence text includes transition statistics**
- Given: A route assessment with transitions containing 2 barriers, 1 non-priority crossing, 0 side changes
- When: `_build_evidence_context()` is called
- Then: The evidence text contains "2 barriers, 1 non-priority crossings, 0 side changes"

**TS-15: Evidence text includes directness differential**
- Given: A route assessment with transitions containing directness_differential of 1.15
- When: `_build_evidence_context()` is called
- Then: The evidence text contains "Directness differential: 1.15"

**TS-16: Evidence text omits transitions when unavailable**
- Given: A route assessment with transitions `{unavailable: true}`
- When: `_build_evidence_context()` is called
- Then: The evidence text does not contain "Transitions:" line

---

### src/agent/orchestrator.py — _build_route_evidence_summary

**Change Description** Currently shows distance, score, provision percentages, parallel detection, and issue counts. Must add transition summary (barrier count, crossing count, side changes) to the condensed summary text.

**Dependants** Structure prompt (receives condensed route summary)

**Kind** Method

**Details**

Within the `for label, route_data in routes_to_show:` loop, after the parallel detection block, add:

```
transitions = route_data.get("transitions", {})
if transitions and not transitions.get("unavailable"):
    barrier_count = len(transitions.get("barriers", []))
    crossing_count = len(transitions.get("non_priority_crossings", []))
    side_change_count = len(transitions.get("side_changes", []))
    summary_lines.append(
        f"- Transitions: {barrier_count} barriers, "
        f"{crossing_count} non-priority crossings, "
        f"{side_change_count} side changes"
    )
```

**Requirements References**
- [route-transition-analysis:FR-006]: Transition statistics in evidence summary

**Test Scenarios**

**TS-17: Summary includes transition counts**
- Given: A route assessment with transitions containing barriers, crossings, and side changes
- When: `_build_route_evidence_summary()` is called
- Then: The summary contains "Transitions:" with barrier, crossing, and side change counts

**TS-18: Summary omits transitions when unavailable**
- Given: A route assessment with transitions `{unavailable: true}`
- When: `_build_route_evidence_summary()` is called
- Then: The summary does not contain "Transitions:" line

---

## Added Components

### src/mcp_servers/cycle_route/infrastructure.py — analyse_transitions

**Description** Analyses the transitions between route segments to detect barriers, non-priority road crossings, cycleway side changes, and directness differential. Uses both the parsed segments (for provision transitions) and the raw Overpass data (for barrier and crossing nodes).

**Users** `_assess_single_route()` in server.py

**Kind** Function

**Location** `src/mcp_servers/cycle_route/infrastructure.py`

**Details**

```
def analyse_transitions(
    segments: list[RouteSegment],
    overpass_data: dict[str, Any],
) -> dict[str, Any]:
```

The function performs four analyses:

**1. Barrier detection (FR-001)**

Extract all node elements from `overpass_data` where `tags.barrier` is one of `{cycle_barrier, bollard, gate, stile, lift_gate}`. For each barrier node:
- Record `type` (barrier tag value), `node_id` (OSM ID), `lat`, `lon`
- Deduplicate barriers within 5m of each other (keep first encountered)

Returns: `barriers` array of `{type, node_id, lat, lon}`

**2. Non-priority crossing detection (FR-002)**

Examine consecutive segment pairs. When an off-road segment (`provision in {segregated, shared_use}`) is followed by a road segment (`highway in ROAD_HIGHWAY_TYPES`) or vice versa, this is a potential crossing point.

For each transition point, search the Overpass node data for crossing nodes (`tags.crossing`) near the transition. If a node has `crossing=traffic_signals` or `crossing=marked`, the crossing is priority-controlled and not counted. Otherwise, it is a non-priority crossing.

Returns: `non_priority_crossings` array of `{road_name, road_speed_limit}`

**3. Side change detection (FR-003)**

Scan for the pattern: off-road segment (A) followed by road segment (B) followed by off-road segment (C), where A and C are both off-road provision types. This pattern indicates a cyclist must cross the road, potentially to reach provision on the opposite side.

This is a simplified heuristic. Full geometric side determination (comparing cycleway offset from carriageway centre line) is complex and deferred to the nice-to-have scope. The count provides a useful proxy.

Returns: `side_changes` array of `{road_name}`

**4. Directness differential (FR-004)**

For segments where `original_provision` is set (indicating parallel detection upgraded the provision), the road segments that were upgraded represent a detour compared to the parallel cycleway. The ratio is approximated as 1.0 (same distance model) unless the parallel cycleway has different geometry. Since `parse_overpass_ways` assigns equal distance per way, the differential is 1.0 when both exist. Return `None` when no parallel sections exist.

Returns: `directness_differential` as a float ratio or `None`

**Complete return structure:**
```python
{
    "barriers": [...],
    "non_priority_crossings": [...],
    "side_changes": [...],
    "directness_differential": float | None,
    "barrier_count": int,
    "non_priority_crossing_count": int,
    "side_change_count": int,
}
```

**Requirements References**
- [route-transition-analysis:FR-001]: Detect barriers along route
- [route-transition-analysis:FR-002]: Count non-priority road crossings
- [route-transition-analysis:FR-003]: Detect cycleway side changes
- [route-transition-analysis:FR-004]: Calculate directness differential

**Test Scenarios**

**TS-19: Barrier nodes detected from Overpass data**
- Given: Overpass data containing 2 node elements with `barrier=bollard` and `barrier=cycle_barrier`
- When: `analyse_transitions()` is called
- Then: `barriers` array has 2 entries with correct types and node IDs

**TS-20: Duplicate barriers within 5m deduplicated**
- Given: Overpass data with 2 bollard nodes at nearly identical coordinates (3m apart)
- When: `analyse_transitions()` is called
- Then: `barriers` array has 1 entry (deduplicated)

**TS-21: Non-priority crossing detected at off-road to road transition**
- Given: Segments [segregated cycleway, residential road (30mph)] with no crossing=traffic_signals node nearby
- When: `analyse_transitions()` is called
- Then: `non_priority_crossings` has 1 entry with road_speed_limit=30

**TS-22: Signalised crossing not counted as non-priority**
- Given: Segments [segregated cycleway, primary road] with a node having `crossing=traffic_signals` in Overpass data
- When: `analyse_transitions()` is called
- Then: `non_priority_crossings` is empty

**TS-23: Marked crossing not counted as non-priority**
- Given: Segments [shared_use path, secondary road] with a node having `crossing=marked` in Overpass data
- When: `analyse_transitions()` is called
- Then: `non_priority_crossings` is empty

**TS-24: Side change detected in off-road, road, off-road pattern**
- Given: Segments [segregated cycleway, residential road, shared_use path]
- When: `analyse_transitions()` is called
- Then: `side_changes` has 1 entry with the road name

**TS-25: No side change for off-road to off-road (no road in between)**
- Given: Segments [segregated cycleway, shared_use path]
- When: `analyse_transitions()` is called
- Then: `side_changes` is empty

**TS-26: Directness differential calculated from parallel detection**
- Given: Segments where 2 road segments have `original_provision` set (upgraded by parallel detection)
- When: `analyse_transitions()` is called
- Then: `directness_differential` is a float >= 1.0

**TS-27: Directness differential is None when no parallel sections**
- Given: Segments with no `original_provision` set
- When: `analyse_transitions()` is called
- Then: `directness_differential` is None

**TS-28: Empty barriers when no barrier nodes in Overpass data**
- Given: Overpass data with only way elements (no barrier nodes)
- When: `analyse_transitions()` is called
- Then: `barriers` is empty, `barrier_count` is 0

**TS-29: Non-priority crossing includes road name and speed limit**
- Given: Segments transitioning from cycleway to road named "Buckingham Road" at 30mph
- When: `analyse_transitions()` is called
- Then: The crossing entry has `road_name="Buckingham Road"` and `road_speed_limit=30`

---

### src/mcp_servers/cycle_route/scoring.py — _score_transitions

**Description** Calculates the transition quality score (0-10 points) based on barriers, non-priority crossings on fast roads, and side changes along the route. A route with no transition issues scores full points.

**Users** `score_route()` in scoring.py

**Kind** Function (private)

**Location** `src/mcp_servers/cycle_route/scoring.py`

**Details**

```
def _score_transitions(transitions: dict[str, Any]) -> float:
```

Penalty scheme:
- Each barrier: **2 points** penalty (barriers force dismounting, significantly impact usability)
- Each non-priority crossing on a road with `road_speed_limit >= 30`: **1.5 points** penalty
- Each non-priority crossing on a road with `road_speed_limit < 30`: **0.5 points** penalty
- Each side change: **1 point** penalty

Calculation:
```
penalty = 0
for barrier in transitions["barriers"]:
    penalty += 2.0

for crossing in transitions["non_priority_crossings"]:
    if crossing.get("road_speed_limit", 30) >= 30:
        penalty += 1.5
    else:
        penalty += 0.5

for side_change in transitions["side_changes"]:
    penalty += 1.0

score = max(0, MAX_TRANSITION_POINTS - penalty)
return round(score, 1)
```

The score is clamped to `[0, MAX_TRANSITION_POINTS]`. A route with 5 barriers would score 0/10. A route with 2 non-priority crossings at 30mph and 1 side change would score 10 - 3 - 1 = 6/10.

**Requirements References**
- [route-transition-analysis:FR-005]: Transition quality scoring factor

**Test Scenarios**

**TS-30: Clean route scores full transition points**
- Given: Transitions with empty barriers, crossings, and side_changes
- When: `_score_transitions()` is called
- Then: Returns `MAX_TRANSITION_POINTS` (10.0)

**TS-31: Barriers penalised at 2 points each**
- Given: Transitions with 3 barriers
- When: `_score_transitions()` is called
- Then: Returns `MAX_TRANSITION_POINTS - 6` = 4.0

**TS-32: Non-priority crossings on fast roads penalised at 1.5 points**
- Given: Transitions with 2 non-priority crossings at road_speed_limit=30
- When: `_score_transitions()` is called
- Then: Returns `MAX_TRANSITION_POINTS - 3` = 7.0

**TS-33: Non-priority crossings on slow roads penalised at 0.5 points**
- Given: Transitions with 2 non-priority crossings at road_speed_limit=20
- When: `_score_transitions()` is called
- Then: Returns `MAX_TRANSITION_POINTS - 1` = 9.0

**TS-34: Side changes penalised at 1 point each**
- Given: Transitions with 2 side changes
- When: `_score_transitions()` is called
- Then: Returns `MAX_TRANSITION_POINTS - 2` = 8.0

**TS-35: Score clamped to zero when penalties exceed max**
- Given: Transitions with 6 barriers (penalty = 12)
- When: `_score_transitions()` is called
- Then: Returns 0 (not negative)

**TS-36: Mixed penalties combine correctly**
- Given: Transitions with 1 barrier, 2 crossings at 30mph, 1 side change (penalty = 2 + 3 + 1 = 6)
- When: `_score_transitions()` is called
- Then: Returns 4.0

---

## Used Components

### src/mcp_servers/cycle_route/infrastructure.py — detect_parallel_provision

**Location** `src/mcp_servers/cycle_route/infrastructure.py`

**Provides** Parallel detection function that upgrades road segment provisions when adjacent cycleways exist. Sets `original_provision` on upgraded segments, which `analyse_transitions()` uses to calculate directness differential.

**Used By** `_assess_single_route()` in server.py (called before `analyse_transitions()`)

### src/mcp_servers/cycle_route/infrastructure.py — RouteSegment

**Location** `src/mcp_servers/cycle_route/infrastructure.py`

**Provides** Dataclass representing a route segment with provision, highway type, speed limit, surface, and `original_provision` for parallel detection upgrades. Used as input to `analyse_transitions()`.

**Used By** `analyse_transitions()`, `_score_transitions()` (indirectly via transitions dict)

### src/mcp_servers/cycle_route/issues.py — identify_issues, generate_s106_suggestions

**Location** `src/mcp_servers/cycle_route/issues.py`

**Provides** Issue detection and S106 suggestion generation. Used as-is — transition analysis adds complementary information but does not modify issue detection.

**Used By** `_assess_single_route()` in server.py

### src/mcp_servers/cycle_route/infrastructure.py — ROAD_HIGHWAY_TYPES

**Location** `src/mcp_servers/cycle_route/infrastructure.py`

**Provides** Set of highway types that are classified as roads. Used by `analyse_transitions()` to determine whether a segment is a road (for crossing detection).

**Used By** `analyse_transitions()`, `detect_parallel_provision()` (existing)

---

## Documentation Considerations

- The as-built spec `.sdd/cycle-route-api/specification.md` should be updated after implementation to reflect the new `transitions` field in the assessment response and the new node queries in the Overpass request

---

## Risks and Dependencies

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Added node queries increase Overpass response size | Medium | Low | Barrier and crossing nodes are sparse (typically <50 per route). Response size increase is marginal. |
| Added node queries increase Overpass query time | Low | Low | Node queries are simple tag filters on the same geographic area. Timeout remains 25s. |
| Barrier deduplication too aggressive (5m threshold) | Low | Low | 5m is smaller than any real barrier spacing. Losing one count is acceptable. |
| Side change heuristic overcounts | Medium | Low | Simplified pattern is a reasonable proxy. False positives result in modest 1-point penalties. |
| Directness differential inaccurate due to equal-distance-per-way approximation | Medium | Low | Inherent limitation of current segment distance model. Ratio still provides useful comparison. |
| Existing test assertions on MAX_*_POINTS values break when weights change | High | Low | Tests must be updated in Phase 1 alongside constant changes. |

---

## Feasibility Review

No blockers. The Overpass API supports mixed way+node queries in a single union block. All required data (barrier tags, crossing tags) is well-standardised in OSM.

---

## QA Feasibility

**QA-01 (Verify barrier detection):** Feasible — requires cycle-route MCP server and Overpass running. Bicester has mapped bollards on several cycle paths. Inspect `transitions.barriers` array.

**QA-02 (Verify non-priority crossing count):** Feasible — select a destination requiring crossing a road. Check `transitions.non_priority_crossings` count.

**QA-03 (Verify transition scoring impact):** Feasible — compare two destination assessments: one clean, one with barriers/crossings. Verify `transition_quality` differs.

---

## Task Breakdown

### Phase 1: Infrastructure and Scoring

**Task 1: Extend Overpass query with barrier and crossing node queries**
- Status: Complete
- Requirements: [route-transition-analysis:FR-001], [route-transition-analysis:FR-002], [route-transition-analysis:NFR-001]
- Test Scenarios: [route-transition-analysis:build_overpass_query/TS-01], [route-transition-analysis:build_overpass_query/TS-02], [route-transition-analysis:build_overpass_query/TS-03]
- Details:
  - Modify `build_overpass_query()` in `src/mcp_servers/cycle_route/infrastructure.py` to add `node(around:15,...)["barrier"~"..."]` and `node(around:20,...)["crossing"]` inside the existing union block
  - Update existing tests and add new tests in `tests/test_mcp_servers/test_cycle_route/test_infrastructure.py`

**Task 2: Implement analyse_transitions function**
- Status: Complete
- Requirements: [route-transition-analysis:FR-001], [route-transition-analysis:FR-002], [route-transition-analysis:FR-003], [route-transition-analysis:FR-004]
- Test Scenarios: [route-transition-analysis:analyse_transitions/TS-19] through [route-transition-analysis:analyse_transitions/TS-29]
- Details:
  - Add `analyse_transitions(segments, overpass_data)` function to `src/mcp_servers/cycle_route/infrastructure.py`
  - Implement barrier detection from overpass node elements
  - Implement non-priority crossing detection from segment transitions + crossing nodes
  - Implement side change detection from off-road/road/off-road patterns
  - Implement directness differential from parallel detection data
  - Add `_haversine_distance()` helper for barrier deduplication
  - Add test class `TestAnalyseTransitions` with helpers `_make_barrier_node()`, `_make_crossing_node()`

**Task 3: Update scoring with transition quality factor**
- Status: Complete
- Requirements: [route-transition-analysis:FR-005]
- Test Scenarios: [route-transition-analysis:score_route/TS-04] through [route-transition-analysis:score_route/TS-10], [route-transition-analysis:_score_transitions/TS-30] through [route-transition-analysis:_score_transitions/TS-36]
- Details:
  - Update MAX_*_POINTS constants in `src/mcp_servers/cycle_route/scoring.py`
  - Add `_score_transitions(transitions)` private function
  - Add `transitions: dict | None = None` parameter to `score_route()`
  - Include `transition_quality` in breakdown and max_points dicts
  - Update existing tests to reflect new constant values
  - Add `TestScoreTransitions` test class

### Phase 2: Server Integration and Orchestrator

**Task 4: Wire transition analysis into server pipeline**
- Status: Complete
- Requirements: [route-transition-analysis:FR-006], [route-transition-analysis:FR-005]
- Test Scenarios: [route-transition-analysis:_assess_single_route/TS-11], [route-transition-analysis:_assess_single_route/TS-12], [route-transition-analysis:_assess_single_route/TS-13]
- Details:
  - Import `analyse_transitions` in `src/mcp_servers/cycle_route/server.py`
  - Call `analyse_transitions(segments, overpass_data)` after `detect_parallel_provision()`
  - Pass `transitions=transitions` to `score_route()`
  - Add `"transitions": transitions` to assessment return dict
  - Wrap in try/except for graceful failure with fallback transitions object
  - Update `_make_overpass_response()` test helper to include node elements
  - Add server tests

**Task 5: Update orchestrator evidence builders with transition statistics**
- Status: Complete
- Requirements: [route-transition-analysis:FR-006]
- Test Scenarios: [route-transition-analysis:_build_evidence_context/TS-14] through [route-transition-analysis:_build_evidence_context/TS-16], [route-transition-analysis:_build_route_evidence_summary/TS-17], [route-transition-analysis:_build_route_evidence_summary/TS-18]
- Details:
  - Update `_build_evidence_context()` in `src/agent/orchestrator.py` to include transition statistics
  - Update `_build_route_evidence_summary()` to include transition counts
  - Update `_make_route_data()` helper to accept optional `transitions` parameter
  - Add orchestrator tests for transition data

---

## Intermediate Dead Code Tracking

None expected.

---

## Intermediate Stub Tracking

None expected. Each task implements complete functionality with tests.

---

## Requirements Validation

| Requirement | Tasks |
|-------------|-------|
| [route-transition-analysis:FR-001] | Phase 1 Tasks 1, 2 |
| [route-transition-analysis:FR-002] | Phase 1 Tasks 1, 2 |
| [route-transition-analysis:FR-003] | Phase 1 Task 2 |
| [route-transition-analysis:FR-004] | Phase 1 Task 2 |
| [route-transition-analysis:FR-005] | Phase 1 Task 3, Phase 2 Task 4 |
| [route-transition-analysis:FR-006] | Phase 2 Tasks 4, 5 |
| [route-transition-analysis:NFR-001] | Phase 1 Task 1 |

---

## Appendix

### Glossary
- **Barrier**: A physical obstruction on a cycleway (bollard, gate, cycle_barrier, stile, lift_gate) tagged as an OSM node
- **Non-priority crossing**: A point where a cyclist must cross a road without signal control or marked crossing infrastructure
- **Side change**: Where off-road cycle provision switches sides, detected by the off-road/road/off-road segment pattern
- **Directness differential**: Ratio of cycle provision distance to carriageway distance for parallel sections
- **Transition quality**: LTN 1/20 scoring factor (0-10 points) penalising barriers, non-priority crossings, and side changes

### References
- LTN 1/20 Cycle Infrastructure Design (DfT, 2020) — Chapter 7 on transitions and crossings
- OSM wiki: `barrier=*` tag values
- OSM wiki: `crossing=*` tag values
- Overpass API: node queries can be combined with way queries in a single union block

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-17 | Claude | Initial design |
