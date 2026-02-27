# Design: Route Barrier Filtering

**Version:** 1.0
**Date:** 2026-02-27
**Status:** Implemented
**Linked Specification** `.sdd/route-barrier-filtering/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The route assessment flow in `_assess_single_route()` is:

1. Build Overpass query from route coordinates (20m buffer for ways, 15m for barriers, 20m for crossings)
2. Query Overpass — returns ways, barrier nodes, and crossing nodes in one response
3. Parse ways into `RouteSegment` objects
4. `analyse_transitions()` reads barrier nodes from the Overpass response by iterating all node elements with barrier tags

Barriers are matched purely by proximity — any OSM node with a qualifying barrier tag within 15m of a sampled route point is included, regardless of whether the node is on a way the cyclist actually follows. This produces false positives from parallel footpaths, park entrances, and adjacent paths.

### Proposed Architecture

Insert a Valhalla `/trace_attributes` call between routing and Overpass to identify on-route OSM way IDs:

```
Valhalla /route → decode shape
                ↓
Valhalla /trace_attributes (shape) → set of on-route way IDs
                ↓
Overpass query (ways + crossings + barrier nodes from on-route ways only)
                ↓
parse_overpass_ways → segments
                ↓
analyse_transitions → barriers (on-route only), crossings, side changes
```

The Overpass query is restructured: instead of fetching barrier nodes by proximity (`node(around:15,...)`), it fetches barrier-tagged nodes that are members of the on-route ways (`node(w.onroute)["barrier"~"..."]`). The `analyse_transitions()` function is unchanged — it already reads barrier nodes from the Overpass response elements, so filtering at the query level is sufficient.

### Technology Decisions

- Valhalla `/trace_attributes` with `shape_match: "edge_walk"` — the route shape is already from Valhalla's `/route`, so `edge_walk` (exact edge matching) is appropriate and most precise
- On-route way IDs are passed into the Overpass query using `way(id:...)->.onroute;` then `node(w.onroute)["barrier"~"..."]` — this keeps barrier detection in a single Overpass round-trip
- Fallback: if `/trace_attributes` fails, the existing proximity-based barrier query is used (graceful degradation)

---

## Modified Components

### Modified: Overpass query builder (`src/mcp_servers/cycle_route/infrastructure.py` — `build_overpass_query`)

**Change Description:** Currently builds a single union query fetching ways, barrier nodes (by proximity), and crossing nodes. Must accept an optional set of on-route OSM way IDs. When provided, replace the standalone barrier node proximity query with a targeted query for barrier nodes that are members of the on-route ways. When not provided (fallback), keep the existing proximity-based barrier query unchanged.

**Dependants:** `_assess_single_route` (passes way IDs)

**Kind:** Function

**Details**

Add parameter: `on_route_way_ids: set[int] | None = None`

When `on_route_way_ids` is provided and non-empty, the query becomes:

```
[out:json][timeout:25];
way(id:{comma_separated_ids})->.onroute;
(
  way(around:{buffer_m},{coords_str})["highway"];
  node(around:20,{coords_str})["crossing"];
  node(w.onroute)["barrier"~"cycle_barrier|bollard|gate|stile|lift_gate"];
);
out geom;
```

When `on_route_way_ids` is None or empty, the query is unchanged (existing proximity-based barrier line preserved).

**Requirements References**
- [route-barrier-filtering:FR-003]: Fetch way node IDs and tags from Overpass — on-route way membership query provides barrier-tagged nodes with coordinates
- [route-barrier-filtering:FR-004]: Remove standalone barrier node query — replaced by `node(w.onroute)` when way IDs available

**Test Scenarios**

**TS-01: Query uses on-route way membership when way IDs provided**
- Given: Route coordinates and `on_route_way_ids={123, 456}`
- When: `build_overpass_query(coords, on_route_way_ids={123, 456})` called
- Then: Query contains `way(id:123,456)->.onroute;` and `node(w.onroute)["barrier"~"..."]`. Query does NOT contain `node(around:15,...)["barrier"...]`.

**TS-02: Query falls back to proximity when no way IDs**
- Given: Route coordinates and `on_route_way_ids=None`
- When: `build_overpass_query(coords)` called
- Then: Query contains `node(around:15,{coords_str})["barrier"~"..."]` (existing behaviour). Query does NOT contain `way(id:...)->.onroute`.

**TS-03: Query falls back to proximity when empty way IDs**
- Given: Route coordinates and `on_route_way_ids=set()`
- When: `build_overpass_query(coords, on_route_way_ids=set())` called
- Then: Query is identical to the no-way-IDs case (proximity-based barriers).

---

### Modified: Route assessment orchestration (`src/mcp_servers/cycle_route/server.py` — `_assess_single_route`)

**Change Description:** Currently calls `build_overpass_query(route_coords)` directly. Must first call the new `_request_trace_attributes()` method to obtain on-route way IDs, then pass them to `build_overpass_query()`.

**Dependants:** None

**Kind:** Method

**Details**

Before the Overpass query, add:

```
on_route_way_ids = await self._request_trace_attributes(route_coords)
overpass_query = build_overpass_query(route_coords, on_route_way_ids=on_route_way_ids)
```

The `_request_trace_attributes` call is placed before the Overpass rate-limit sleep, so both external calls (Valhalla + Overpass) are sequential with the existing delay between them.

**Requirements References**
- [route-barrier-filtering:FR-001]: The trace_attributes call is initiated from here
- [route-barrier-filtering:FR-002]: On-route way IDs are passed to the Overpass query, ensuring only on-route barriers are fetched

**Test Scenarios**

**TS-04: Assessment passes trace_attributes way IDs to Overpass query**
- Given: `_request_trace_attributes` returns `{100, 200, 300}`
- When: `_assess_single_route` runs
- Then: `build_overpass_query` is called with `on_route_way_ids={100, 200, 300}`

**TS-05: Assessment uses fallback when trace_attributes fails**
- Given: `_request_trace_attributes` returns `None`
- When: `_assess_single_route` runs
- Then: `build_overpass_query` is called with `on_route_way_ids=None` (proximity-based fallback)

---

## Added Components

### Added: Valhalla trace_attributes request (`src/mcp_servers/cycle_route/server.py` — `_request_trace_attributes`)

**Description:** Makes a POST request to Valhalla's `/trace_attributes` endpoint, passing the decoded route shape. Extracts unique OSM way IDs from the response's `edges` array. Returns None on any failure (timeout, HTTP error, parse error) to enable graceful fallback.

**Users:** `_assess_single_route` — called before the Overpass query

**Kind:** Method (on `CycleRouteMCP` class)

**Location:** `src/mcp_servers/cycle_route/server.py`

**Details**

The method accepts route coordinates as `list[list[float]]` (in `[lon, lat]` GeoJSON order) and converts them to the Valhalla shape format `[{"lat": ..., "lon": ...}, ...]`.

Request body:

```
{
  "shape": [{"lat": coord[1], "lon": coord[0]} for coord in route_coords],
  "costing": "bicycle",
  "shape_match": "edge_walk",
  "filters": {
    "attributes": ["edge.way_id"],
    "action": "include"
  }
}
```

Response parsing:
- Extract `edges` array from response JSON
- Collect unique non-zero `way_id` values into a `set[int]`
- Return the set, or `None` on any failure

Failure handling: wrap in try/except, log warning with destination name, return `None`. This triggers the fallback in `build_overpass_query`.

Timeout: 10 seconds (trace_attributes against a local Valhalla instance should be fast).

**Requirements References**
- [route-barrier-filtering:FR-001]: Identify on-route OSM way IDs via Valhalla trace_attributes

**Test Scenarios**

**TS-06: Extracts way IDs from trace_attributes response**
- Given: Route coordinates for a known path
- When: `_request_trace_attributes(coords)` called with a mocked Valhalla response containing `edges: [{"way_id": 100}, {"way_id": 200}, {"way_id": 100}]`
- Then: Returns `{100, 200}` (deduplicated)

**TS-07: Returns None on Valhalla error**
- Given: Valhalla returns HTTP 500
- When: `_request_trace_attributes(coords)` called
- Then: Returns `None`; warning logged

**TS-08: Returns None on timeout**
- Given: Valhalla does not respond within 10 seconds
- When: `_request_trace_attributes(coords)` called
- Then: Returns `None`; warning logged

**TS-09: Returns None on empty edges**
- Given: Valhalla returns `{"edges": []}`
- When: `_request_trace_attributes(coords)` called
- Then: Returns `None` (empty set treated as failure to avoid accidentally removing all barriers)

**TS-10: Filters out zero way IDs**
- Given: Valhalla response with `edges: [{"way_id": 0}, {"way_id": 300}]`
- When: `_request_trace_attributes(coords)` called
- Then: Returns `{300}` (zero filtered out)

---

## Used Components

### `analyse_transitions` (`src/mcp_servers/cycle_route/infrastructure.py:595`)

**Location:** `src/mcp_servers/cycle_route/infrastructure.py`

**Provides:** Barrier detection by iterating Overpass response node elements with barrier tags. The existing code reads whatever barrier nodes are in the Overpass response — if the query only returns on-route barrier nodes, the function naturally returns only on-route barriers without modification.

**Used By:** `_assess_single_route` — unchanged call site, but now receives filtered Overpass data

### `barriers_to_geojson` (`src/mcp_servers/cycle_route/infrastructure.py:781`)

**Location:** `src/mcp_servers/cycle_route/infrastructure.py`

**Provides:** Converts barrier dicts to GeoJSON FeatureCollection. Unchanged.

**Used By:** `_assess_single_route` — unchanged

### `_score_transitions` (`src/mcp_servers/cycle_route/scoring.py:225`)

**Location:** `src/mcp_servers/cycle_route/scoring.py`

**Provides:** Scores transition quality, deducting 2.0 points per barrier. Unchanged — will naturally produce more accurate scores with fewer false-positive barriers.

**Used By:** `score_route` — unchanged

---

## Documentation Considerations

- None — the route assessment output shape is unchanged. The `barriers` array, `barriers_geojson`, and scoring all use the same format with fewer false positives.

---

## Integration Test Scenarios

**ITS-01: End-to-end barrier filtering with on-route way IDs**
- Given: A mock Valhalla that returns route shape and trace_attributes with way IDs `{100, 200}`. A mock Overpass response with two barrier nodes: one on way 100 (on-route bollard) and one on way 999 (off-route gate on parallel path).
- When: `_assess_single_route` runs
- Then: `transitions["barriers"]` contains only the bollard on way 100. The gate on way 999 is excluded. `barriers_geojson` has 1 feature.
- Components Involved: `_request_trace_attributes`, `build_overpass_query`, `analyse_transitions`, `barriers_to_geojson`

**ITS-02: End-to-end fallback when trace_attributes unavailable**
- Given: Valhalla `/trace_attributes` returns HTTP 500. Overpass returns barrier nodes via proximity.
- When: `_assess_single_route` runs
- Then: Assessment completes with proximity-based barriers (existing behaviour). Warning logged for trace_attributes failure.
- Components Involved: `_request_trace_attributes`, `build_overpass_query`, `analyse_transitions`

---

## Risks and Dependencies

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Valhalla `/trace_attributes` not available in deployed Valhalla build | Low | High | Valhalla's Docker image includes trace_attributes by default. Verify on deployed instance before merging. |
| `edge_walk` fails for complex route shapes (e.g., u-turns) | Low | Low | Fallback to proximity-based detection ensures assessment still completes. Log warning for investigation. |
| Large route shapes cause slow `/trace_attributes` response | Low | Low | Local Valhalla instance; 10s timeout prevents blocking. Could sample coordinates if needed but likely unnecessary. |
| Some on-route barriers missed if Valhalla's way IDs don't match Overpass data (stale OSM data in Valhalla) | Low | Low | Valhalla and Overpass both use OSM data; if Valhalla's extract is older, some way IDs may not match. Periodic Valhalla data updates mitigate this. |

---

## Task Breakdown

### Phase 1: Valhalla trace_attributes integration and Overpass query modification

**Task 1: Add `_request_trace_attributes` method**
- Status: Complete
- Requirements: [route-barrier-filtering:FR-001]
- Test Scenarios: [route-barrier-filtering:TraceAttributes/TS-06], [route-barrier-filtering:TraceAttributes/TS-07], [route-barrier-filtering:TraceAttributes/TS-08], [route-barrier-filtering:TraceAttributes/TS-09], [route-barrier-filtering:TraceAttributes/TS-10]
- Details:
  - Add `_request_trace_attributes(route_coords)` method to `CycleRouteMCP` class
  - POST to `{valhalla_url}/trace_attributes` with shape, costing, filters
  - Extract unique non-zero `way_id` values from `edges` array
  - Return `set[int]` or `None` on failure
  - Write tests in `tests/test_mcp_servers/test_cycle_route/test_infrastructure.py` (or new test file for server methods)

**Task 2: Modify `build_overpass_query` to accept on-route way IDs**
- Status: Complete
- Requirements: [route-barrier-filtering:FR-003], [route-barrier-filtering:FR-004]
- Test Scenarios: [route-barrier-filtering:OverpassQuery/TS-01], [route-barrier-filtering:OverpassQuery/TS-02], [route-barrier-filtering:OverpassQuery/TS-03]
- Details:
  - Add `on_route_way_ids: set[int] | None = None` parameter
  - When provided and non-empty: prepend `way(id:...)->.onroute;`, replace barrier line with `node(w.onroute)["barrier"~"..."]`
  - When None or empty: keep existing `node(around:15,...)["barrier"~"..."]` line
  - Update existing tests, add new test cases

**Task 3: Wire trace_attributes into `_assess_single_route`**
- Status: Complete
- Requirements: [route-barrier-filtering:FR-001], [route-barrier-filtering:FR-002]
- Test Scenarios: [route-barrier-filtering:Assessment/TS-04], [route-barrier-filtering:Assessment/TS-05], [route-barrier-filtering:ITS-01], [route-barrier-filtering:ITS-02]
- Details:
  - Call `_request_trace_attributes(route_coords)` before Overpass query
  - Pass result to `build_overpass_query(route_coords, on_route_way_ids=...)`
  - Ensure fallback path works when trace_attributes returns None

---

## QA Feasibility

**QA-01 (Off-route barriers excluded):** Fully testable. Submit a review for an application where previous assessments showed off-route barriers. Compare before/after `_routes.json` output — off-route barriers should be absent. Cross-reference remaining barrier locations on a map against the route line.

**QA-02 (On-route barriers still detected):** Fully testable. Use an application whose route passes through a known physical barrier (e.g., bollard on a shared-use path). Verify it still appears in the barriers list.

**QA-03 (Graceful fallback):** Testable with white-box setup — temporarily stop the Valhalla container and submit a review. The assessment should complete with barriers detected via the proximity fallback. Restart Valhalla afterward. **White-box setup required:** stopping Valhalla container.

---

## Appendix

### Glossary
- **On-route way**: An OSM way whose ID is returned by Valhalla's `/trace_attributes` for the assessed route shape
- **Barrier node**: An OSM node tagged with a `barrier` value from `BARRIER_TYPES`
- **trace_attributes**: Valhalla endpoint that map-matches a shape to the road network and returns edge attributes including OSM way IDs
- **edge_walk**: Valhalla shape matching mode that assumes the input shape follows the road network exactly (appropriate for shapes from Valhalla's own `/route` endpoint)

### References
- [route-barrier-filtering spec](.sdd/route-barrier-filtering/specification.md)
- [route-transition-analysis spec](.sdd/route-transition-analysis/specification.md) — original barrier detection
- [Valhalla trace_attributes API](https://valhalla.github.io/valhalla/api/map-matching/api-reference/)
- Current barrier detection: `src/mcp_servers/cycle_route/infrastructure.py:613-637`

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-27 | Claude | Initial design |
