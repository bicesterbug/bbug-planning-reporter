# Specification: Route Assessment Refinement

**Version:** 1.0
**Date:** 2026-02-21
**Status:** Draft

---

## Problem Statement

Route assessment output contains three quality issues: (1) the same infrastructure finding is repeated for every ~11m Overpass way segment on a road, producing dozens of duplicate issues for a single street; (2) side roads within the 20m Overpass buffer are included as route segments and in the GeoJSON, inflating segment counts and distorting provision breakdowns; (3) both shortest and safest routes are fully assessed via Overpass when only the safest route needs infrastructure analysis — the shortest route's sole purpose is to establish how direct the safe route is.

## Beneficiaries

**Primary:**
- Review quality — route findings become actionable per-road summaries instead of noisy per-segment lists

**Secondary:**
- Performance — halving Overpass calls (1 per destination instead of 2) plus dropping the driving route request reduces latency and Overpass load

---

## Outcomes

**Must Haves**
- Issues aggregated by road, not repeated per 11m segment
- Route GeoJSON is the Valhalla polyline (no side roads), with separate collections for crossings and barriers
- Shortest route used only for directness scoring — no Overpass query, no scoring, no issues
- Segment distances calculated from actual way geometry instead of equal division

**Nice-to-haves**
- Filter out perpendicular side roads from Overpass way data before scoring

---

## Explicitly Out of Scope

- Matching Overpass ways to Valhalla route via geometry intersection (complex; geometry-based distances are sufficient)
- Changes to the LTN 1/20 scoring weights or thresholds
- Changes to the report prompt or structure prompt (they consume whatever the MCP produces)
- Changes to the API response schema (route_assessments is already `dict[str, Any]`)

---

## Functional Requirements

**FR-001: Aggregate Issues by Road**
- Description: Instead of generating one issue per segment, `identify_issues()` groups segments by road name and issue type, producing one aggregated issue per road with the total affected distance.
- Acceptance criteria: A 500m road with no cycle provision at 40mph produces exactly one "high" severity issue with `distance_m: 500`, not ~45 separate issues. Different roads still produce separate issues. Different issue types on the same road (e.g., no provision + poor surface) produce separate issues.
- Failure/edge cases: Multiple segments with the same name "Unnamed" are aggregated together (acceptable — Unnamed roads are typically short service roads). Segments with different speed limits on the same named road are grouped by the highest speed limit.

**FR-002: Route GeoJSON from Valhalla Geometry**
- Description: The `segments` field in the route assessment output is replaced by `route_geojson` — a GeoJSON FeatureCollection containing a single LineString Feature built from the Valhalla route polyline coordinates. This eliminates side roads from the route visualisation.
- Acceptance criteria: The route assessment output contains a `route_geojson` field with a FeatureCollection containing one LineString Feature. The coordinates come from the Valhalla decoded polyline. Properties include route distance and duration. The old `segments` field (FeatureCollection of per-way LineStrings) is no longer in the output.
- Failure/edge cases: If the route polyline has fewer than 2 points, the feature has null geometry (same pattern as current segments).

**FR-003: Non-Priority Crossing GeoJSON**
- Description: Non-priority crossings are returned as a separate GeoJSON FeatureCollection of Point features in the assessment output under `crossings_geojson`. Only crossings where the route is off-carriageway (provision is segregated or shared_use) are included.
- Acceptance criteria: The assessment output contains a `crossings_geojson` field with a FeatureCollection. Each Feature is a Point with properties: road_name, road_speed_limit. Only crossings at off-road-to-road or road-to-off-road transitions are included. Crossings at road-to-road transitions are excluded.
- Failure/edge cases: If there are no non-priority crossings, the FeatureCollection has an empty features array. Crossings where both adjacent segments are on-road are excluded.

**FR-004: Barrier GeoJSON**
- Description: Barriers are returned as a separate GeoJSON FeatureCollection of Point features in the assessment output under `barriers_geojson`.
- Acceptance criteria: The assessment output contains a `barriers_geojson` field with a FeatureCollection. Each Feature is a Point with properties: barrier_type, node_id. Duplicate barriers within 5m are still deduplicated (existing behaviour).
- Failure/edge cases: If there are no barriers, the FeatureCollection has an empty features array.

**FR-005: Shortest Route for Directness Only**
- Description: The shortest route is not assessed via Overpass. It provides only distance and geometry for directness scoring of the safest route. The driving route request is also removed — directness is calculated as safest_distance / shortest_distance. The output structure changes: the top-level result contains a single `assessment` (the safest route) plus `shortest_route_distance_m` and `shortest_route_geometry` for reference.
- Acceptance criteria: Only one Overpass call is made per destination (for the safest route). The directness score compares safest cycling distance to shortest cycling distance. The output no longer contains `shortest_route` and `safest_route` sub-objects — it has a flat assessment with a `shortest_route_distance_m` field. No driving route Valhalla request is made.
- Failure/edge cases: If shortest and safest routes are the same (same_route=true), directness ratio is 1.0 (maximum directness score). If the shortest route fails but safest succeeds, directness uses half-points (existing fallback). If safest fails but shortest succeeds, an empty assessment stub is returned.

**FR-006: Geometry-Based Segment Distances**
- Description: `parse_overpass_ways()` calculates each segment's distance from its actual Overpass geometry (haversine sum between consecutive nodes) instead of dividing route distance equally among all ways. This gives side roads their true short length and main road segments their true longer length, improving provision breakdown accuracy.
- Acceptance criteria: A 200m road returned by Overpass has a segment distance of approximately 200m (within 5% of haversine calculation), regardless of how many other ways are in the response. The total segment distance may differ from the Valhalla route distance (acceptable — Overpass ways don't perfectly match the route).
- Failure/edge cases: Ways with no geometry or only one node get distance 0. The route_distance_m parameter to `parse_overpass_ways()` is no longer needed for distance calculation but may be retained for backward compatibility.

---

## QA Plan

**QA-01: Verify aggregated issues**
- Goal: Confirm a long road with repeated issues produces one aggregated finding
- Steps:
  1. Submit a review for a planning application with a destination along a major road (e.g., A4095)
  2. Check the route_assessments in the review result
  3. Inspect the issues array
- Expected: Each road appears once per issue type with total distance, not repeated per segment

**QA-02: Verify route GeoJSON is clean**
- Goal: Confirm route GeoJSON is a single Valhalla LineString without side roads
- Steps:
  1. Submit a review with route assessment
  2. Extract route_geojson from the assessment result
  3. Paste into geojson.io to visualise
- Expected: A single continuous line following the cycling route. No stub side roads visible.

**QA-03: Verify separate crossing and barrier collections**
- Goal: Confirm crossings and barriers are separate point collections
- Steps:
  1. Submit a review with route assessment
  2. Check crossings_geojson and barriers_geojson in result
- Expected: Point features with appropriate properties. Crossings only at off-carriageway transitions.

**QA-04: Verify single Overpass call per destination**
- Goal: Confirm only one Overpass request is made per destination
- Steps:
  1. Submit a review and monitor worker logs
  2. Count Overpass-related log entries per destination
- Expected: One set of Overpass retry logs per destination (not two as before)

---

## Open Questions

None.

---

## Appendix

### Glossary
- **Provision**: Type of cycling infrastructure (segregated cycleway, shared-use path, on-road lane, advisory lane, none)
- **Off-carriageway**: Cycling on a path separate from the road (segregated or shared_use provision)
- **Non-priority crossing**: Where an off-road path meets a road without traffic signals or marked crossing — cyclists must yield
- **Directness ratio**: safest_route_distance / shortest_route_distance — how much further the safe route goes compared to the most direct cycling route

### References
- LTN 1/20: Cycle Infrastructure Design guidance (Department for Transport)
- Current implementation: `src/mcp_servers/cycle_route/infrastructure.py`, `server.py`, `issues.py`, `scoring.py`

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-21 | Claude | Initial specification |
