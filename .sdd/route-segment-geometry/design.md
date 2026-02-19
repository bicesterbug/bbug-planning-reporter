# Design: Route Segment Geometry

**Version:** 1.0
**Date:** 2026-02-18
**Status:** Implemented
**Linked Specification** `.sdd/route-segment-geometry/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The cycle-route MCP server queries OSRM for route paths and Overpass for per-way infrastructure data. `parse_overpass_ways()` creates `RouteSegment` dataclass instances from each Overpass way element but discards the way's geometry (coordinate array). Segments are serialised via `to_dict()` as plain dicts in a `segments` array on each route variant.

The Overpass query uses `out geom` which returns geometry for every way element as `[{"lat": float, "lon": float}, ...]`. This geometry is currently used only for bearing calculation in parallel detection, then discarded.

### Proposed Architecture

1. **Store geometry**: `parse_overpass_ways()` extracts the `geometry` array from each Overpass way element and stores it on the `RouteSegment`, converting from Overpass `{lat, lon}` dicts to GeoJSON `[lon, lat]` coordinate pairs.
2. **GeoJSON output**: A new `segments_to_feature_collection()` function builds a GeoJSON FeatureCollection from a list of segments. Each Feature has a LineString geometry and properties from the segment fields.
3. **Replace segments array**: In `_assess_single_route()`, the `segments` field in the result dict becomes the FeatureCollection instead of the plain dict array.

### Technology Decisions

- GeoJSON coordinates in `[longitude, latitude]` order per RFC 7946.
- Overpass geometry `[{"lat": 51.9, "lon": -1.15}]` converted to `[[-1.15, 51.9]]` during parsing.
- Segments with fewer than 2 coordinate points get `geometry: null` (valid GeoJSON Feature).

---

## Modified Components

### Modified: `RouteSegment` (`src/mcp_servers/cycle_route/infrastructure.py`)

**Change Description:** Add a `geometry` field to the dataclass to hold the way's coordinate array in GeoJSON `[lon, lat]` format. The existing `to_dict()` method continues to work for internal use but now includes the geometry.

**Dependants:** `parse_overpass_ways()` must populate the field.

**Kind:** Dataclass

**Details**

Add field `geometry: list[list[float]] | None = None` to the dataclass. Each entry is a `[lon, lat]` pair. Update `to_dict()` to include `geometry` when present.

**Requirements References**
- [route-segment-geometry:FR-001]: Segment must have geometry from Overpass

**Test Scenarios**

**TS-01: RouteSegment stores geometry**
- Given: A RouteSegment created with geometry coordinates
- When: Accessing the geometry field
- Then: The geometry is a list of `[lon, lat]` pairs

**TS-02: RouteSegment geometry defaults to None**
- Given: A RouteSegment created without geometry
- When: Accessing the geometry field
- Then: geometry is None

---

### Modified: `parse_overpass_ways()` (`src/mcp_servers/cycle_route/infrastructure.py`)

**Change Description:** Currently creates RouteSegments without geometry. Must extract the `geometry` array from each Overpass way element, convert from `{lat, lon}` dicts to `[lon, lat]` pairs, and pass to RouteSegment.

**Dependants:** None — consumers of segments get geometry automatically.

**Kind:** Function

**Details**

For each way in the Overpass response, extract `way.get("geometry", [])`. Convert each `{"lat": lat, "lon": lon}` dict to `[lon, lat]`. If the result has fewer than 2 points, store `None`. Otherwise store the coordinate list on the segment.

**Requirements References**
- [route-segment-geometry:FR-001]: Geometry from Overpass stored on segment
- [route-segment-geometry:FR-004]: Coordinates in lon/lat GeoJSON order

**Test Scenarios**

**TS-03: Geometry extracted from Overpass way**
- Given: An Overpass response with a way element containing `geometry: [{"lat": 51.9, "lon": -1.15}, {"lat": 51.91, "lon": -1.14}]`
- When: `parse_overpass_ways()` is called
- Then: The resulting segment has `geometry: [[-1.15, 51.9], [-1.14, 51.91]]`

**TS-04: Way without geometry gets null**
- Given: An Overpass response with a way element that has no `geometry` key
- When: `parse_overpass_ways()` is called
- Then: The resulting segment has `geometry: None`

**TS-05: Single-node way gets null geometry**
- Given: An Overpass response with a way whose geometry has only 1 point
- When: `parse_overpass_ways()` is called
- Then: The resulting segment has `geometry: None`

---

## Added Components

### Added: `segments_to_feature_collection()` (`src/mcp_servers/cycle_route/infrastructure.py`)

**Description:** Converts a list of RouteSegments into a GeoJSON FeatureCollection. Each segment becomes a Feature with LineString geometry (or null) and properties containing all segment fields.

**Users:** `_assess_single_route()` in server.py.

**Kind:** Function

**Location:** `src/mcp_servers/cycle_route/infrastructure.py` (alongside existing segment functions)

**Details**

Accepts `list[RouteSegment]`. Returns a dict representing a GeoJSON FeatureCollection:
- `type`: "FeatureCollection"
- `features`: list of Feature dicts, one per segment

Each Feature:
- `type`: "Feature"
- `geometry`: `{"type": "LineString", "coordinates": segment.geometry}` if geometry exists, else `null`
- `properties`: dict with `way_id`, `provision`, `highway`, `speed_limit`, `surface`, `lit`, `distance_m`, `name`, and `original_provision` (when not None)

**Requirements References**
- [route-segment-geometry:FR-002]: Segments field is a FeatureCollection
- [route-segment-geometry:FR-003]: Properties include all segment fields
- [route-segment-geometry:FR-004]: Geometry is a LineString in [lon, lat] order

**Test Scenarios**

**TS-06: FeatureCollection structure**
- Given: A list of 2 RouteSegments with geometry
- When: `segments_to_feature_collection()` is called
- Then: Result has `type: "FeatureCollection"` with 2 Features. Each Feature has `type: "Feature"`, `geometry.type: "LineString"`, and `properties` with all segment fields.

**TS-07: Feature with null geometry**
- Given: A RouteSegment with `geometry: None`
- When: `segments_to_feature_collection()` is called
- Then: The Feature has `geometry: null`

**TS-08: Properties include original_provision when present**
- Given: A RouteSegment with `original_provision: "none"`
- When: `segments_to_feature_collection()` is called
- Then: The Feature's properties include `original_provision: "none"`

**TS-09: Properties exclude original_provision when absent**
- Given: A RouteSegment with `original_provision: None`
- When: `segments_to_feature_collection()` is called
- Then: The Feature's properties do not contain `original_provision`

**TS-10: Empty segments list**
- Given: An empty list of segments
- When: `segments_to_feature_collection()` is called
- Then: Result has `type: "FeatureCollection"` with `features: []`

---

### Modified: `_assess_single_route()` (`src/mcp_servers/cycle_route/server.py`)

**Change Description:** Currently sets `"segments": [s.to_dict() for s in segments]`. Change to use `segments_to_feature_collection(segments)` so the output is a GeoJSON FeatureCollection.

**Dependants:** Orchestrator and routes JSON consumers see the new format.

**Kind:** Method

**Details**

Replace `"segments": [s.to_dict() for s in segments]` with `"segments": segments_to_feature_collection(segments)`.

**Requirements References**
- [route-segment-geometry:FR-002]: segments field is a FeatureCollection

**Test Scenarios**

**TS-11: Full route assessment produces FeatureCollection segments**
- Given: A mock OSRM route and Overpass response with way geometry
- When: `assess_cycle_route` is called
- Then: The shortest_route.segments is a GeoJSON FeatureCollection with Feature entries matching the Overpass ways

---

## Used Components

### `summarise_provision()` (`src/mcp_servers/cycle_route/infrastructure.py`)

**Location:** `src/mcp_servers/cycle_route/infrastructure.py`

**Provides:** Aggregates provision distances from segments. Uses `seg.provision` and `seg.distance_m` — unaffected by geometry addition.

**Used By:** `_assess_single_route()` — called before the output dict is built.

### `detect_parallel_provision()` (`src/mcp_servers/cycle_route/infrastructure.py`)

**Location:** `src/mcp_servers/cycle_route/infrastructure.py`

**Provides:** Upgrades segment provisions based on parallel cycleways. Already reads Overpass geometry from the raw response for bearing calculation — unaffected by storing geometry on segments.

**Used By:** `_assess_single_route()` — called after parsing, before output.

### `score_route()` / `identify_issues()` (`src/mcp_servers/cycle_route/scoring.py`, `issues.py`)

**Location:** `src/mcp_servers/cycle_route/scoring.py`, `src/mcp_servers/cycle_route/issues.py`

**Provides:** Route scoring and issue identification from segment lists. Uses segment fields like `provision`, `speed_limit`, `highway`, `distance_m` — unaffected by geometry addition.

**Used By:** `_assess_single_route()`

---

## Documentation Considerations

- No API docs change needed — route_assessments are in the separate routes JSON file (stripped from the review JSON as of v0.3.3)

---

## QA Feasibility

**QA-01 (Verify segments FeatureCollection in routes JSON):** Fully testable. Submit review, download routes JSON, inspect segments field. No white-box setup needed.

**QA-02 (Verify map rendering):** Fully testable. Copy FeatureCollection to geojson.io. No white-box setup needed.

---

## Risks and Dependencies

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Overpass returns ways without geometry (e.g., node-only response) | Low | Low | Segments get `geometry: null`; map renders them as invisible — acceptable degradation |
| Routes JSON file size increases | Low | None | User confirmed size is not a concern; routes file is separate from review JSON |
| Existing tests expect plain segments array | Certain | Low | Update test assertions to expect FeatureCollection format |

---

## Task Breakdown

### Phase 1: Add geometry to segments and produce FeatureCollection

**Task 1: Add geometry field to RouteSegment and extract in parse_overpass_ways**
- Status: Done
- Requirements: [route-segment-geometry:FR-001], [route-segment-geometry:FR-004]
- Test Scenarios: [route-segment-geometry:RouteSegment/TS-01], [route-segment-geometry:RouteSegment/TS-02], [route-segment-geometry:parse_overpass_ways/TS-03], [route-segment-geometry:parse_overpass_ways/TS-04], [route-segment-geometry:parse_overpass_ways/TS-05]
- Details: Add `geometry: list[list[float]] | None = field(default=None)` to RouteSegment. In `parse_overpass_ways()`, extract `way.get("geometry", [])`, convert `{lat, lon}` → `[lon, lat]`, store on segment (None if < 2 points).

**Task 2: Add segments_to_feature_collection function**
- Status: Done
- Requirements: [route-segment-geometry:FR-002], [route-segment-geometry:FR-003], [route-segment-geometry:FR-004]
- Test Scenarios: [route-segment-geometry:segments_to_feature_collection/TS-06], [route-segment-geometry:segments_to_feature_collection/TS-07], [route-segment-geometry:segments_to_feature_collection/TS-08], [route-segment-geometry:segments_to_feature_collection/TS-09], [route-segment-geometry:segments_to_feature_collection/TS-10]
- Details: New function in infrastructure.py. Converts list of RouteSegments to GeoJSON FeatureCollection dict.

**Task 3: Wire FeatureCollection into route output and update server tests**
- Status: Done
- Requirements: [route-segment-geometry:FR-002]
- Test Scenarios: [route-segment-geometry:_assess_single_route/TS-11]
- Details: In `_assess_single_route()`, replace `[s.to_dict() for s in segments]` with `segments_to_feature_collection(segments)`. Update existing server tests that assert on segments format.

---

## Appendix

### Glossary
- **GeoJSON FeatureCollection**: `{"type": "FeatureCollection", "features": [...]}`
- **GeoJSON Feature**: `{"type": "Feature", "geometry": {...}, "properties": {...}}`
- **LineString**: A GeoJSON geometry type representing an ordered sequence of points connected by straight lines

### References
- [GeoJSON Specification (RFC 7946)](https://datatracker.ietf.org/doc/html/rfc7946)

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-18 | Claude | Initial design |
