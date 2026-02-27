# Specification: Route Barrier Filtering

**Version:** 1.0
**Date:** 2026-02-27
**Status:** Draft

---

## Problem Statement

The route assessment includes barriers (bollards, gates, cycle barriers, etc.) that are near the route but not actually on the way the cyclist is following. These off-route barriers — on parallel footpaths, park entrances, or adjacent paths — are incorrectly penalising the route score (2 points each) and appearing in the report. Only barriers that are physically across a way the route traverses (i.e., that would require the cyclist to stop or dismount) should be included.

## Beneficiaries

**Primary:**
- Review consumers — route scores and barrier counts accurately reflect obstacles the cyclist would actually encounter

**Secondary:**
- Report credibility — removing false-positive barriers makes the assessment more trustworthy when referenced in planning responses

---

## Outcomes

**Must Haves**
- Barriers are only included when they are a node on an OSM way that the route actually follows
- On-route way identification uses Valhalla's `/trace_attributes` endpoint to get exact OSM way IDs for the route
- The separate barrier node query is removed from the Overpass query (barriers are identified from on-route way node data instead)

**Nice-to-haves**
- None

---

## Explicitly Out of Scope

- Changing barrier types (cycle_barrier, bollard, gate, stile, lift_gate all remain)
- Changing the scoring penalty per barrier (remains 2.0 points)
- Changing the crossing detection logic (crossings use a different matching approach)
- Filtering crossings by on-route membership (separate concern)
- Changes to the route GeoJSON output format

---

## Functional Requirements

**FR-001: Identify on-route OSM way IDs via Valhalla trace_attributes**
- Description: After obtaining a route from Valhalla's `/route` endpoint, make a follow-up call to Valhalla's `/trace_attributes` endpoint, passing the decoded route shape with `"shape_match": "map_snap"`. Extract the list of unique OSM way IDs (`edge.way_id`) from the response. This identifies exactly which OSM ways the route traverses.
- Acceptance criteria: For a given route, the system obtains a set of OSM way IDs from Valhalla that correspond to the ways the route follows. The way IDs are available for use by the barrier filtering logic.
- Failure/edge cases: If the `/trace_attributes` call fails (timeout, error), fall back to the current behaviour (no barrier filtering) so that the route assessment still completes. Log the failure as a warning.

**FR-002: Identify barriers from on-route way nodes**
- Description: Replace the independent Overpass barrier node query (`node(around:15,...)["barrier"~"..."]`) with a method that checks whether any node on the on-route OSM ways has a barrier tag. After fetching ways from Overpass, for each way whose OSM ID is in the on-route set from FR-001, iterate its nodes and check for barrier tags matching the existing `BARRIER_TYPES` set. This ensures only barriers physically on a way the cyclist follows are included.
- Acceptance criteria: A barrier node that exists on a parallel footpath (not in the on-route way ID set) is excluded. A barrier node that is a member of an on-route way is included. The `barriers` list in the `transitions` dict contains only on-route barriers.
- Failure/edge cases: When the on-route way ID set is unavailable (FR-001 fallback), the existing proximity-based barrier query is used unchanged. Barrier deduplication (5m threshold) continues to apply.

**FR-003: Fetch way node IDs and tags from Overpass**
- Description: The Overpass query must return sufficient data to identify barrier-tagged nodes on ways. Currently `out geom` returns geometry coordinates but not individual node IDs with tags. The query must be adjusted so that for each way, the constituent node IDs are available, and barrier-tagged nodes can be identified — either by augmenting the existing query to also fetch node details for the returned ways, or by adding a secondary query for nodes that are members of the on-route ways.
- Acceptance criteria: For each on-route way returned by Overpass, the system can determine which of its constituent nodes have barrier tags (from the `BARRIER_TYPES` set) and obtain the node's coordinates.
- Failure/edge cases: If a way has no barrier-tagged nodes, it contributes nothing to the barriers list. The query must not significantly increase the Overpass response size (only node data for on-route ways is needed, not all ways).

**FR-004: Remove standalone barrier node query from Overpass**
- Description: The line `node(around:15,{coords_str})["barrier"~"cycle_barrier|bollard|gate|stile|lift_gate"];` must be removed from the batched Overpass query. Barriers are now sourced exclusively from on-route way node membership (FR-002/FR-003).
- Acceptance criteria: The Overpass query sent to the API does not contain a standalone barrier node fetch. The `barriers` list is populated only by the on-route way node checking logic.
- Failure/edge cases: In the fallback case (no on-route way IDs available), the standalone barrier query is temporarily restored for that request only.

---

## QA Plan

**QA-01: Verify off-route barriers are excluded**
- Goal: Confirm barriers on parallel paths are no longer included
- Steps:
  1. Submit a review for an application where the current assessment includes barriers that are visibly off-route (e.g., on a park entrance or parallel footpath)
  2. Wait for completion
  3. Inspect the `_routes.json` output — check `transitions.barriers` array
  4. Cross-reference barrier locations against the route line on a map
- Expected: Only barriers on the actual route way are listed. Barriers on adjacent paths are absent.

**QA-02: Verify on-route barriers are still detected**
- Goal: Confirm genuine on-route barriers (bollards/gates across the cycling way) are still included
- Steps:
  1. Submit a review where the route passes through a known bollard or cycle barrier on the actual carriageway/cycleway
  2. Inspect `transitions.barriers` in the output
- Expected: The on-route barrier appears with correct type, coordinates, and node_id.

**QA-03: Verify graceful fallback on trace_attributes failure**
- Goal: Confirm assessment still completes if Valhalla trace_attributes is unavailable
- Steps:
  1. Temporarily make the Valhalla service return errors for trace_attributes
  2. Submit a review
- Expected: Route assessment completes with barriers detected via the existing proximity method. A warning is logged.

---

## Open Questions

None.

---

## Appendix

### Glossary
- **On-route way**: An OSM way whose ID appears in the Valhalla trace_attributes response for the assessed route — the cyclist actually travels along this way
- **Barrier node**: An OSM node tagged with a `barrier` value from the `BARRIER_TYPES` set (cycle_barrier, bollard, gate, stile, lift_gate)
- **trace_attributes**: A Valhalla endpoint that takes a shape and returns detailed attributes for each edge the shape is matched to, including the OSM way ID

### References
- [route-transition-analysis spec](.sdd/route-transition-analysis/specification.md) — original feature introducing barrier detection
- [Valhalla trace_attributes API](https://valhalla.github.io/valhalla/api/map-matching/api-reference/) — map matching with attribute extraction
- Current barrier detection: `src/mcp_servers/cycle_route/infrastructure.py` lines 613-637

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-27 | Claude | Initial specification |
