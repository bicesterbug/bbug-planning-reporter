# Design: Dual Route Assessment (Shortest + Safest)

**Version:** 1.0
**Date:** 2026-02-17
**Status:** Draft
**Linked Specification** `.sdd/route-dual-routing/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The `assess_cycle_route` MCP tool in `src/mcp_servers/cycle_route/server.py` requests a single route from OSRM (first route only), queries Overpass for infrastructure tags along the route, scores it against LTN 1/20, and returns a flat response with `distance_m`, `score`, `provision_breakdown`, `segments`, `issues`, `s106_suggestions`, and `route_geometry`.

The Overpass query (`build_overpass_query`) uses `out body` which returns tags only — no way geometry. All ways within 20m of sampled route points are treated as route segments with distance divided equally.

The orchestrator's `_phase_assess_routes()` stores each destination's assessment result as-is, and `_build_route_evidence_summary()` builds a flat per-destination text block for the LLM.

### Proposed Architecture

1. **OSRM alternatives**: Add `alternatives=true` to the OSRM request. Process all returned routes (typically 1-3).

2. **Parallel detection**: After getting segments for each route, run a second targeted Overpass query for `highway=cycleway` or `highway=path` with `bicycle=designated` within 30m of road segments that have poor provision. Use `out geom` to get way geometry for bearing comparison. Upgrade matching segment provisions.

3. **Route selection**: Score all alternatives (with parallel detection applied), then select shortest (min distance) and safest (max score).

4. **Response structure**: Return `shortest_route` and `safest_route` objects, each containing the same fields as the current single-route response, plus a `same_route` boolean.

5. **Orchestrator**: Parse the new response structure. Build evidence text showing both routes per destination.

### Technology Decisions

- Reuse existing OSRM and Overpass infrastructure — no new external dependencies
- Parallel detection uses a separate Overpass query rather than modifying the existing one, to keep concerns separate and to only fetch geometry when needed
- Bearing calculation uses simple first-to-last-node bearing of each way — sufficient for detecting parallel ways without complex geometry intersection

### Quality Attributes

- **Rate limiting**: Parallel detection batches all candidate segments into a single Overpass query per route, adding only one extra API call per route alternative
- **Graceful degradation**: If parallel detection fails, routes are scored with original provision only

---

## Modified Components

### src/mcp_servers/cycle_route/infrastructure.py — RouteSegment

**Change Description** The `RouteSegment` dataclass currently has a `provision` field. Add an `original_provision` field so that when parallel detection upgrades a segment's provision, the original value is preserved.

**Dependants** `to_dict()` method, all consumers of `RouteSegment`

**Kind** Dataclass

**Details**

Add `original_provision: str | None = None` field. When parallel detection upgrades provision, `original_provision` stores the pre-upgrade value. The `to_dict()` method includes `original_provision` only when it differs from `provision`.

**Requirements References**
- [route-dual-routing:FR-003]: Preserve original provision alongside upgraded provision

**Test Scenarios**

**TS-01: RouteSegment includes original_provision in dict when upgraded**
- Given: A RouteSegment with `provision="segregated"` and `original_provision="none"`
- When: `to_dict()` is called
- Then: The dict contains both `provision: "segregated"` and `original_provision: "none"`

**TS-02: RouteSegment omits original_provision when not upgraded**
- Given: A RouteSegment with `provision="none"` and `original_provision=None`
- When: `to_dict()` is called
- Then: The dict does not contain `original_provision`

---

### src/mcp_servers/cycle_route/infrastructure.py — build_overpass_query

**Change Description** Currently uses `out body` which returns tags only. Change to `out geom` so way geometry (node coordinates) is available for bearing calculation in parallel detection.

**Dependants** `parse_overpass_ways()` (unaffected — it reads `tags`, not geometry)

**Kind** Function

**Details**

Change the output directive from `out body` to `out geom`. This adds coordinate arrays to each way element in the response, which the existing `parse_overpass_ways` ignores but the new parallel detection function requires.

**Requirements References**
- [route-dual-routing:FR-003]: Way geometry needed for bearing-based matching

**Test Scenarios**

**TS-03: Overpass query requests geometry output**
- Given: A set of route coordinates
- When: `build_overpass_query()` is called
- Then: The query string contains `out geom`

---

### src/mcp_servers/cycle_route/server.py — _assess_cycle_route

**Change Description** Currently requests a single OSRM route and returns a flat assessment. Change to request alternatives, score each with parallel detection, select shortest and safest, and return a dual-route response.

**Dependants** Orchestrator `_phase_assess_routes()`, `_build_route_evidence_summary()`

**Kind** Method

**Details**

1. Add `alternatives=true` to OSRM params
2. Iterate all returned routes: for each, query Overpass, parse segments, run parallel detection, score
3. Select shortest (min distance) and safest (max score, tie-break by shorter distance)
4. Return `{status, destination, shortest_route: {...}, safest_route: {...}, same_route: bool}`

Each route object contains: `distance_m`, `duration_minutes`, `provision_breakdown`, `segments`, `score`, `issues`, `s106_suggestions`, `route_geometry`.

To avoid excessive Overpass calls (one per alternative), share a single Overpass query when alternatives overlap significantly. Practically: query Overpass once for the first (primary) route's coordinates, and reuse the same Overpass data for alternatives whose coordinates fall within similar coverage. If an alternative diverges significantly, issue a separate Overpass query.

**Requirements References**
- [route-dual-routing:FR-001]: Request OSRM alternatives
- [route-dual-routing:FR-002]: Select shortest route
- [route-dual-routing:FR-004]: Score all alternatives with parallel detection
- [route-dual-routing:FR-005]: Select safest route
- [route-dual-routing:FR-006]: Return dual route assessment
- [route-dual-routing:NFR-001]: Rate limiting maintained

**Test Scenarios**

**TS-04: OSRM called with alternatives=true**
- Given: Valid origin and destination coordinates
- When: `_assess_cycle_route()` is called
- Then: The OSRM request includes `alternatives=true` in params

**TS-05: Shortest route selected by minimum distance**
- Given: OSRM returns 3 alternatives with distances 2400m, 2100m, 2800m
- When: Routes are evaluated
- Then: The route with 2100m is selected as shortest

**TS-06: Safest route selected by maximum score**
- Given: Three alternatives scored 45, 72, 61
- When: Routes are evaluated
- Then: The route scoring 72 is selected as safest

**TS-07: Same route when only one alternative returned**
- Given: OSRM returns only 1 route
- When: Assessment completes
- Then: Response has `same_route: true` with both `shortest_route` and `safest_route` containing identical data

**TS-08: Same route when shortest is also safest**
- Given: OSRM returns 2 routes where the shortest also has the highest score
- When: Assessment completes
- Then: Response has `same_route: true`

**TS-09: Error handling unchanged for OSRM failure**
- Given: OSRM returns no routes
- When: Assessment is attempted
- Then: Returns error response with `error_type: "no_route"` (unchanged from current)

---

### src/agent/orchestrator.py — _phase_assess_routes

**Change Description** Currently stores the flat MCP response in `_route_assessments`. Must now handle the dual-route response structure, extracting `shortest_route` and `safest_route` data.

**Dependants** `_build_route_evidence_summary()`, review result output

**Kind** Method

**Details**

When storing route assessment results, preserve the full dual-route structure. The `destination_id` is added at the top level as before.

**Requirements References**
- [route-dual-routing:FR-006]: Consume dual route response
- [route-dual-routing:NFR-002]: Backward compatibility (graceful degradation unchanged)

**Test Scenarios**

**TS-10: Orchestrator stores dual-route assessment**
- Given: MCP returns a dual-route response with `shortest_route` and `safest_route`
- When: `_phase_assess_routes` processes the result
- Then: The stored assessment contains both route objects

---

### src/agent/orchestrator.py — _build_route_evidence_summary

**Change Description** Currently builds a single-route evidence block per destination. Must show both shortest and safest routes with their respective scores and provision breakdowns, noting parallel detection upgrades.

**Dependants** Structure and report prompts (via evidence text)

**Kind** Method

**Details**

For each destination:
- If `same_route` is true, show a single route block with a note "This route is both the shortest and safest option"
- If `same_route` is false, show two labelled sub-blocks: "Shortest route" and "Safest route", each with distance, score, rating, provision percentages
- Where segments have `original_provision` set, note the parallel detection upgrade count

**Requirements References**
- [route-dual-routing:FR-007]: Both routes in evidence text

**Test Scenarios**

**TS-11: Evidence text shows both routes when different**
- Given: A route assessment with `same_route: false`, shortest scoring 45, safest scoring 72
- When: `_build_route_evidence_summary()` is called
- Then: The evidence text contains "Shortest route" and "Safest route" sections with respective scores

**TS-12: Evidence text shows single route when same**
- Given: A route assessment with `same_route: true`
- When: `_build_route_evidence_summary()` is called
- Then: The evidence text shows a single route with "both the shortest and safest" note

**TS-13: Evidence text notes parallel detection upgrades**
- Given: A route assessment where segments have `original_provision` set
- When: `_build_route_evidence_summary()` is called
- Then: The evidence text mentions the number of segments with upgraded provision

---

## Added Components

### src/mcp_servers/cycle_route/infrastructure.py — detect_parallel_provision

**Description** Queries Overpass for cycleways and designated paths within 30m of road segments that have poor provision (none, advisory_lane, on_road_lane). Matches candidates by proximity and bearing similarity (within 30 degrees). Upgrades matching segment provisions in-place, preserving the original provision.

**Users** `_assess_cycle_route()` in server.py

**Kind** Function (async)

**Location** `src/mcp_servers/cycle_route/infrastructure.py`

**Details**

`async def detect_parallel_provision(segments, route_coords, overpass_data, http_client) -> list[RouteSegment]`

1. Identify road segments with poor provision and classified highway type
2. Build an Overpass query for `highway=cycleway` or `(highway=path AND bicycle=designated)` within 30m of sampled route coordinates, with `out geom`
3. For each candidate cycleway, calculate bearing from first to last node
4. For each poor-provision road segment, find the Overpass way for that way_id (or nearby way) and calculate its bearing
5. If a candidate cycleway is within 30m and bearing difference ≤30 degrees: upgrade segment's provision to match the cycleway's classification, set `original_provision` to the old value
6. If multiple candidates match, use the one with the best provision (segregated > shared_use)

The function operates on the Overpass data already fetched for the route (which now includes geometry from `out geom`), scanning for cycleway-type ways that weren't included in the route segments. This avoids an extra Overpass call.

**Requirements References**
- [route-dual-routing:FR-003]: Detect parallel cycle provision
- [route-dual-routing:NFR-001]: Batched into existing Overpass query (no extra API call)

**Test Scenarios**

**TS-14: Parallel cycleway detected and provision upgraded**
- Given: A road segment with `provision="none"` and a cycleway way within 30m with similar bearing in the Overpass data
- When: `detect_parallel_provision()` is called
- Then: The segment's provision is upgraded to "segregated" and `original_provision` is set to "none"

**TS-15: No upgrade when no parallel cycleway exists**
- Given: A road segment with `provision="none"` and no nearby cycleway in the Overpass data
- When: `detect_parallel_provision()` is called
- Then: The segment's provision remains "none" and `original_provision` is None

**TS-16: No upgrade when bearing differs too much**
- Given: A road segment and a nearby cycleway with bearing difference > 30 degrees (e.g. a crossing cycleway)
- When: `detect_parallel_provision()` is called
- Then: The segment's provision is not upgraded

**TS-17: Best provision selected when multiple candidates**
- Given: A road segment near both a shared_use path and a segregated cycleway
- When: `detect_parallel_provision()` is called
- Then: The segment is upgraded to "segregated" (the better classification)

---

### src/mcp_servers/cycle_route/infrastructure.py — calculate_way_bearing

**Description** Calculates the compass bearing of an OSM way from its first to last node coordinates.

**Users** `detect_parallel_provision()`

**Kind** Function

**Location** `src/mcp_servers/cycle_route/infrastructure.py`

**Details**

`def calculate_way_bearing(geometry: list[dict]) -> float | None`

Takes the `geometry` array from an Overpass way element (list of `{lat, lon}` dicts). Returns bearing in degrees (0-360) from first to last node, or None if fewer than 2 nodes.

**Requirements References**
- [route-dual-routing:FR-003]: Bearing comparison for parallel detection

**Test Scenarios**

**TS-18: Bearing calculated correctly for east-west way**
- Given: A way running from (-1.16, 51.90) to (-1.14, 51.90)
- When: `calculate_way_bearing()` is called
- Then: Returns approximately 90 degrees (east)

**TS-19: Bearing calculated correctly for north-south way**
- Given: A way running from (-1.15, 51.89) to (-1.15, 51.91)
- When: `calculate_way_bearing()` is called
- Then: Returns approximately 0 degrees (north)

**TS-20: Returns None for single-node way**
- Given: A way with only one node
- When: `calculate_way_bearing()` is called
- Then: Returns None

---

### src/mcp_servers/cycle_route/infrastructure.py — bearing_difference

**Description** Calculates the absolute angular difference between two bearings, accounting for wrap-around (e.g. 350° vs 10° = 20° difference).

**Users** `detect_parallel_provision()`

**Kind** Function

**Location** `src/mcp_servers/cycle_route/infrastructure.py`

**Details**

`def bearing_difference(bearing_a: float, bearing_b: float) -> float`

Returns the minimum angular difference in degrees (0-180). Parallel ways going in opposite directions (180° apart) should be treated as having 0° difference since the direction of travel doesn't matter for provision matching.

**Requirements References**
- [route-dual-routing:FR-003]: Bearing within 30 degrees for parallel detection

**Test Scenarios**

**TS-21: Same bearing returns 0**
- Given: Two bearings of 90°
- When: `bearing_difference()` is called
- Then: Returns 0

**TS-22: Opposite directions treated as parallel**
- Given: Bearings of 90° and 270° (east vs west)
- When: `bearing_difference()` is called
- Then: Returns 0 (opposite directions are parallel)

**TS-23: Wrap-around handled correctly**
- Given: Bearings of 350° and 10°
- When: `bearing_difference()` is called
- Then: Returns 20

---

## Used Components

### src/mcp_servers/cycle_route/scoring.py — score_route

**Location** `src/mcp_servers/cycle_route/scoring.py`

**Provides** LTN 1/20 scoring function that takes segments and returns score, rating, and breakdown. Used as-is — scoring operates on the `provision` field, so parallel-detection upgrades are automatically reflected.

**Used By** Modified `_assess_cycle_route()` in server.py

### src/mcp_servers/cycle_route/issues.py — identify_issues, generate_s106_suggestions

**Location** `src/mcp_servers/cycle_route/issues.py`

**Provides** Issue detection and S106 suggestion generation. Used as-is for each alternative route.

**Used By** Modified `_assess_cycle_route()` in server.py

---

## Documentation Considerations

- The as-built spec `.sdd/cycle-route-api/specification.md` should be updated after implementation to reflect the new dual-route response structure
- No external API documentation changes needed

---

## Risks and Dependencies

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| OSRM returns only 1 route for most destinations | Medium | Low | `same_route: true` deduplication handles this cleanly; value still comes from parallel detection |
| Parallel detection Overpass query timeout on large routes | Low | Low | Uses existing timeout (25s); graceful degradation to original provision if it fails |
| Bearing calculation inaccurate for curved ways | Medium | Low | First-to-last-node bearing is an approximation; for ways >500m this may miss curves but still correctly identifies generally parallel ways |
| Extra Overpass data from `out geom` increases response size | Low | Low | Geometry adds ~2x to Overpass response; within acceptable limits for the 20m-buffer query |

---

## Feasibility Review

No blockers. All external APIs (OSRM alternatives, Overpass geometry output) are available and free. The change is additive — existing functionality is preserved.

---

## QA Feasibility

**QA-01 (Dual routes for known destination):** Fully feasible — requires the cycle-route MCP server and OSRM to be running. The destination must exist in Redis.

**QA-02 (Parallel detection on Banbury Road):** Feasible — requires a planning application located north of Bicester town centre. The segregated cycleway on Banbury Road is well-mapped on OSM. May require manual inspection of segment data.

**QA-03 (Single-route deduplication):** Feasible — use a destination very close to the site where OSRM would only return one route.

---

## Task Breakdown

### Phase 1: Infrastructure and MCP Server

**Task 1: Add RouteSegment.original_provision and bearing utilities**
- Status: Done
- Requirements: [route-dual-routing:FR-003]
- Test Scenarios: [route-dual-routing:RouteSegment/TS-01], [route-dual-routing:RouteSegment/TS-02], [route-dual-routing:infrastructure/TS-03], [route-dual-routing:infrastructure/TS-18], [route-dual-routing:infrastructure/TS-19], [route-dual-routing:infrastructure/TS-20], [route-dual-routing:infrastructure/TS-21], [route-dual-routing:infrastructure/TS-22], [route-dual-routing:infrastructure/TS-23]
- Details:
  - Add `original_provision: str | None = None` to RouteSegment dataclass
  - Update `to_dict()` to include `original_provision` only when set and different from `provision`
  - Change `build_overpass_query` to use `out geom` instead of `out body`
  - Add `calculate_way_bearing(geometry)` function
  - Add `bearing_difference(bearing_a, bearing_b)` function
  - Write tests for all of the above

**Task 2: Implement parallel cycle provision detection**
- Status: Done
- Requirements: [route-dual-routing:FR-003], [route-dual-routing:NFR-001]
- Test Scenarios: [route-dual-routing:detect_parallel_provision/TS-14], [route-dual-routing:detect_parallel_provision/TS-15], [route-dual-routing:detect_parallel_provision/TS-16], [route-dual-routing:detect_parallel_provision/TS-17]
- Details:
  - Add `detect_parallel_provision(segments, overpass_data)` function
  - Scan Overpass data for cycleway/designated-path ways not already in route segments
  - Match by way_id proximity (shared nodes or nearby coordinates) and bearing similarity
  - Upgrade provision on matching road segments, preserving `original_provision`
  - Write tests with mock Overpass data containing parallel cycleways

**Task 3: Wire OSRM alternatives, dual scoring, and dual route response**
- Status: Done
- Requirements: [route-dual-routing:FR-001], [route-dual-routing:FR-002], [route-dual-routing:FR-004], [route-dual-routing:FR-005], [route-dual-routing:FR-006], [route-dual-routing:NFR-001]
- Test Scenarios: [route-dual-routing:_assess_cycle_route/TS-04], [route-dual-routing:_assess_cycle_route/TS-05], [route-dual-routing:_assess_cycle_route/TS-06], [route-dual-routing:_assess_cycle_route/TS-07], [route-dual-routing:_assess_cycle_route/TS-08], [route-dual-routing:_assess_cycle_route/TS-09]
- Details:
  - Add `alternatives=true` to OSRM params in `_assess_cycle_route`
  - Extract helper to assess a single route (Overpass + segments + parallel detection + scoring + issues)
  - Loop over all OSRM alternatives, assess each
  - Select shortest by min distance, safest by max score (tie-break: shorter)
  - Build dual route response with `same_route` flag
  - Write tests mocking OSRM with multiple alternatives

### Phase 2: Orchestrator

**Task 4: Update orchestrator for dual route response and evidence summary**
- Status: Done
- Requirements: [route-dual-routing:FR-007], [route-dual-routing:NFR-002]
- Test Scenarios: [route-dual-routing:orchestrator/TS-10], [route-dual-routing:orchestrator/TS-11], [route-dual-routing:orchestrator/TS-12], [route-dual-routing:orchestrator/TS-13]
- Details:
  - Update `_phase_assess_routes` to handle new response structure
  - Rewrite `_build_route_evidence_summary()` to show both routes per destination
  - Handle `same_route: true` case with single-route text
  - Note parallel detection upgrades in evidence text
  - Write tests for evidence text generation

---

## Intermediate Dead Code Tracking

None expected.

---

## Intermediate Stub Tracking

None expected.

---

## Appendix

### Glossary
- **OSRM alternatives**: Multiple candidate routes returned by OSRM when `alternatives=true` is specified
- **Parallel detection**: Scanning Overpass data for cycleway-type ways adjacent to road segments
- **Provision upgrade**: Changing a segment's provision classification based on detected parallel infrastructure

### References
- OSRM API: `alternatives=true` returns up to 3 alternative routes
- Overpass API: `out geom` returns way geometry with node coordinates
- Haversine formula for bearing calculation between two coordinates
- Existing infrastructure code: `src/mcp_servers/cycle_route/infrastructure.py`
- Existing server code: `src/mcp_servers/cycle_route/server.py`
- Existing orchestrator: `src/agent/orchestrator.py` lines 1277-1620

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-17 | Claude | Initial design |
