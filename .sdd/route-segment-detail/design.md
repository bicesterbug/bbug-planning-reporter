# Design: Route Segment Detail

**Version:** 1.1
**Date:** 2026-02-22
**Status:** Implemented
**Linked Specification:** `.sdd/route-segment-detail/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The cycle route MCP server (`src/mcp_servers/cycle_route/`) assesses routes in `_assess_single_route()`:

1. Query Overpass for way data along the route
2. `parse_overpass_ways()` → `list[RouteSegment]` (one per OSM way, with geometry)
3. `detect_parallel_provision()` mutates segments (sets `original_provision`)
4. `summarise_provision(segments)` → aggregate provision breakdown dict
5. `score_route(segments, ...)` → aggregate LTN 1/20 score dict
6. `identify_issues(segments)` → issues list
7. Return flat dict with aggregate data; segments are discarded

A `segments_to_feature_collection()` function exists in `infrastructure.py` but is unused — it converts raw segments to GeoJSON without aggregation or score factors.

### Proposed Architecture

Add two new functions and wire them into the existing pipeline:

1. `compute_segment_score_factors()` in `scoring.py` — pure function that computes LTN 1/20 quality factors for a single segment's properties
2. `aggregate_segments_to_geojson()` in `infrastructure.py` — merges consecutive segments with matching properties into GeoJSON Features, calls `compute_segment_score_factors()` for each

The server's `_assess_single_route()` calls `aggregate_segments_to_geojson(segments)` after scoring and adds the result as `segments_geojson` to the return dict. All existing fields remain unchanged.

```
segments = parse_overpass_ways(overpass_data)
detect_parallel_provision(segments, overpass_data)
provision = summarise_provision(segments)
route_score = score_route(segments, ...)
route_issues = identify_issues(segments)
segments_geojson = aggregate_segments_to_geojson(segments)  # NEW
return { ..., "segments_geojson": segments_geojson }
```

### Technology Decisions

- **Highway in aggregation key:** The spec defines the aggregation key as `(provision, speed_limit, surface, lit, original_provision)`. The design adds `highway` to the key to guarantee that `hostile_junction` in `score_factors` is consistent within a merged segment. Without this, a merged "none, 30mph, asphalt, lit" segment spanning both a primary road and a residential road would have ambiguous hostile_junction status. This is a strictly finer granularity — it never merges segments the spec would keep separate.

- **Reuse existing constants:** Score factor thresholds reuse `GOOD_SURFACES`, `FAIR_SURFACES`, `SPEED_*` constants from `scoring.py` rather than duplicating them.

---

## Added Components

### `compute_segment_score_factors()`

**Description:** Pure function that computes LTN 1/20 quality factors for a segment based on its infrastructure properties. Returns the same factors the route-level scorer uses internally, enabling clients to colour-code segments by any compliance dimension.

**Users:** `aggregate_segments_to_geojson()` in infrastructure.py

**Kind:** Function

**Location:** `src/mcp_servers/cycle_route/scoring.py`

**Details:**

Accepts provision, speed_limit, surface, and highway. Returns a dict with four keys:

- `segregation`: float 0.0–1.0. segregated→1.0, shared_use→0.7, on_road_lane→0.4, else→0.0
- `speed_safety`: float 0.0–1.0 or None. None when provision is not `none`/`advisory_lane`. Otherwise: ≤20mph→1.0, ≤30mph→0.6, ≤40mph→0.2, >40mph→0.0
- `surface_quality`: float 0.0–1.0. Good surfaces→1.0, fair→0.6, unknown→0.5, else→0.2
- `hostile_junction`: bool. True when provision is `none` AND highway in (primary, secondary, trunk, tertiary) AND speed_limit ≥ 30

**Requirements References:**
- [route-segment-detail:FR-006]: Per-segment LTN 1/20 quality factors

**Test Scenarios**

**TS-01: Segregated cycleway score factors**
- Given: provision="segregated", speed_limit=0, surface="asphalt", highway="cycleway"
- When: compute_segment_score_factors is called
- Then: Returns `{segregation: 1.0, speed_safety: None, surface_quality: 1.0, hostile_junction: False}`

**TS-02: Shared-use path score factors**
- Given: provision="shared_use", speed_limit=0, surface="compacted", highway="path"
- When: compute_segment_score_factors is called
- Then: Returns `{segregation: 0.7, speed_safety: None, surface_quality: 0.6, hostile_junction: False}`

**TS-03: On-road lane score factors**
- Given: provision="on_road_lane", speed_limit=30, surface="asphalt", highway="secondary"
- When: compute_segment_score_factors is called
- Then: Returns `{segregation: 0.4, speed_safety: None, surface_quality: 1.0, hostile_junction: False}`

**TS-04: No provision on 20mph residential**
- Given: provision="none", speed_limit=20, surface="asphalt", highway="residential"
- When: compute_segment_score_factors is called
- Then: Returns `{segregation: 0.0, speed_safety: 1.0, surface_quality: 1.0, hostile_junction: False}`

**TS-05: No provision on 30mph primary (hostile)**
- Given: provision="none", speed_limit=30, surface="asphalt", highway="primary"
- When: compute_segment_score_factors is called
- Then: Returns `{segregation: 0.0, speed_safety: 0.6, surface_quality: 1.0, hostile_junction: True}`

**TS-06: No provision on 40mph trunk**
- Given: provision="none", speed_limit=40, surface="unknown", highway="trunk"
- When: compute_segment_score_factors is called
- Then: Returns `{segregation: 0.0, speed_safety: 0.2, surface_quality: 0.5, hostile_junction: True}`

**TS-07: No provision above 40mph**
- Given: provision="none", speed_limit=60, surface="asphalt", highway="primary"
- When: compute_segment_score_factors is called
- Then: Returns `{segregation: 0.0, speed_safety: 0.0, surface_quality: 1.0, hostile_junction: True}`

**TS-08: Advisory lane score factors**
- Given: provision="advisory_lane", speed_limit=30, surface="asphalt", highway="tertiary"
- When: compute_segment_score_factors is called
- Then: Returns `{segregation: 0.0, speed_safety: 0.6, surface_quality: 1.0, hostile_junction: False}`

**TS-09: Poor surface**
- Given: provision="segregated", speed_limit=0, surface="gravel", highway="cycleway"
- When: compute_segment_score_factors is called
- Then: Returns `{segregation: 1.0, speed_safety: None, surface_quality: 0.2, hostile_junction: False}`

---

### `aggregate_segments_to_geojson()`

**Description:** Merges consecutive `RouteSegment` instances with matching infrastructure properties into aggregated GeoJSON Features. Concatenates geometries, sums distances, collects road names and way IDs, and attaches LTN 1/20 score factors to each Feature.

**Users:** `CycleRouteMCP._assess_single_route()` in server.py

**Kind:** Function

**Location:** `src/mcp_servers/cycle_route/infrastructure.py`

**Details:**

Accepts a `list[RouteSegment]`. Returns a GeoJSON FeatureCollection dict.

Aggregation key: `(provision, speed_limit, surface, lit, original_provision, highway)`. Only consecutive segments with identical keys are merged.

For each aggregated group:
- Geometry: Concatenate all segment geometries into one LineString coordinate array. Skip segments with `geometry=None`.
- `distance_m`: Sum of all segment distances, rounded to 1 decimal.
- `names`: Unique non-empty road names from the group, preserving first-seen order.
- `way_ids`: All way IDs from the group in order.
- `provision`, `speed_limit`, `surface`, `lit`, `highway`: Taken from the group (all identical by definition).
- `original_provision`: Included only when not None.
- `score_factors`: Result of `compute_segment_score_factors()` for the group's properties.

If no segments have geometry, returns an empty FeatureCollection.

**Requirements References:**
- [route-segment-detail:FR-001]: Segments GeoJSON in routes output
- [route-segment-detail:FR-002]: Consecutive segment aggregation
- [route-segment-detail:FR-003]: Feature properties
- [route-segment-detail:FR-005]: Original provision annotation
- [route-segment-detail:FR-006]: Per-segment score factors (via compute_segment_score_factors)

**Test Scenarios**

**TS-10: Single segment produces single Feature**
- Given: One RouteSegment with geometry
- When: aggregate_segments_to_geojson is called
- Then: FeatureCollection has 1 Feature with correct properties

**TS-11: Consecutive identical segments merged**
- Given: Two consecutive segments with same (provision, speed_limit, surface, lit, original_provision, highway) and each with geometry
- When: aggregate_segments_to_geojson is called
- Then: FeatureCollection has 1 Feature. distance_m is the sum. way_ids contains both IDs. Geometry coordinates are concatenated.

**TS-12: Different segments stay separate**
- Given: Segments [A(segregated), B(segregated), C(none)] in order
- When: aggregate_segments_to_geojson is called
- Then: FeatureCollection has 2 Features: A+B merged, C separate

**TS-13: Non-adjacent identical segments not merged**
- Given: Segments [A(segregated), B(none), C(segregated)]
- When: aggregate_segments_to_geojson is called
- Then: FeatureCollection has 3 Features (no merge across gap)

**TS-14: Names deduplicated and empty excluded**
- Given: Two segments both named "High Street" merged with a third named ""
- When: aggregate_segments_to_geojson is called
- Then: Merged feature names = ["High Street"]. Feature for unnamed segment has names = [].

**TS-15: Segment without geometry excluded**
- Given: Two segments, one with geometry=None and one with geometry
- When: aggregate_segments_to_geojson is called
- Then: FeatureCollection has 1 Feature (the one with geometry). The segment without geometry contributes to distance_m and names but not to the geometry coordinates.

**TS-16: original_provision included when set**
- Given: One segment with original_provision="none" and provision="segregated"
- When: aggregate_segments_to_geojson is called
- Then: Feature properties include "original_provision": "none"

**TS-17: Different original_provision prevents merge**
- Given: Two consecutive segments with same provision/speed/surface/lit/highway but one has original_provision="none" and the other has original_provision=None
- When: aggregate_segments_to_geojson is called
- Then: FeatureCollection has 2 Features (not merged)

**TS-18: Different highway prevents merge**
- Given: Two consecutive segments with same provision/speed/surface/lit but different highway values
- When: aggregate_segments_to_geojson is called
- Then: FeatureCollection has 2 Features

**TS-19: Empty segments list**
- Given: Empty list of segments
- When: aggregate_segments_to_geojson is called
- Then: Returns FeatureCollection with empty features array

**TS-20: Score factors attached to each Feature**
- Given: Two segments — one segregated cycleway, one unprotected primary road
- When: aggregate_segments_to_geojson is called
- Then: First Feature has score_factors.segregation=1.0, second has score_factors.hostile_junction=True

**TS-21: All segments lack geometry**
- Given: Two segments both with geometry=None
- When: aggregate_segments_to_geojson is called
- Then: Returns empty FeatureCollection (no features)

---

## Modified Components

### `CycleRouteMCP._assess_single_route()`

**Change Description:** Currently builds the return dict from provision, score, issues, GeoJSON route/crossings/barriers, and transitions. Add a call to `aggregate_segments_to_geojson(segments)` and include the result as `segments_geojson` in the return dict. No existing fields are changed.

**Dependants:** None — additive field only. The orchestrator passes route data through without field filtering.

**Kind:** Method

**Location:** `src/mcp_servers/cycle_route/server.py`

**Details:**

After the existing `generate_s106_suggestions()` call and before the return dict, call:
```
segments_geojson = aggregate_segments_to_geojson(segments)
```

Add `"segments_geojson": segments_geojson` to the return dict.

Also add `segments_geojson` to the `_empty_assessment()` fallback dict as an empty FeatureCollection.

**Requirements References:**
- [route-segment-detail:FR-001]: segments_geojson present in output
- [route-segment-detail:FR-004]: Existing fields unchanged

**Test Scenarios**

**TS-22: segments_geojson present in full assessment**
- Given: A successful route assessment with Overpass data
- When: assess_cycle_route tool is called
- Then: Result contains "segments_geojson" key with a FeatureCollection

**TS-23: Empty assessment includes segments_geojson**
- Given: Overpass returns no data (empty assessment fallback)
- When: assess_cycle_route tool is called
- Then: Result contains "segments_geojson" with empty FeatureCollection

**TS-24: Existing fields unchanged**
- Given: A successful route assessment
- When: assess_cycle_route tool is called
- Then: All existing fields (distance_m, score, provision_breakdown, issues, s106_suggestions, route_geojson, crossings_geojson, barriers_geojson, transitions, parallel_upgrades) are present and unchanged

---

## Used Components

### `RouteSegment` dataclass
**Location:** `src/mcp_servers/cycle_route/infrastructure.py`

**Provides:** Segment data including `way_id`, `provision`, `highway`, `speed_limit`, `surface`, `lit`, `distance_m`, `name`, `original_provision`, `geometry`. The `geometry` field already contains `[lon, lat]` coordinate arrays from Overpass way nodes.

**Used By:** `aggregate_segments_to_geojson()`, `compute_segment_score_factors()`

### Scoring constants
**Location:** `src/mcp_servers/cycle_route/scoring.py`

**Provides:** `GOOD_SURFACES`, `FAIR_SURFACES`, `SPEED_NO_PENALTY`, `SPEED_LOW_PENALTY`, `SPEED_HIGH_PENALTY` constants. Reusing these ensures score factors stay consistent with the route-level scorer.

**Used By:** `compute_segment_score_factors()`

### `_make_segment()` test factory
**Location:** `tests/test_mcp_servers/test_cycle_route/test_scoring.py`

**Provides:** Convenient factory for constructing `RouteSegment` instances with sensible defaults.

**Used By:** New tests for `aggregate_segments_to_geojson()` and `compute_segment_score_factors()`

---

## Documentation Considerations

- Update `docs/API.md` Routes JSON section to document the `segments_geojson` field, its properties, and `score_factors`

---

## Test Data

- Reuse `_make_segment()` factory from `test_scoring.py` with added `geometry` and `original_provision` parameters
- No external fixtures needed — all test data is constructed in-test

---

## Test Feasibility

No missing infrastructure. All tests are unit tests using constructed segments.

---

## QA Feasibility

**QA-01 (Visual inspection of routes JSON):** Fully feasible with existing review submission. Submit review for 21/03267/OUT, download routes JSON, inspect `segments_geojson` field.

**QA-02 (Map rendering):** Fully feasible by pasting `segments_geojson` into geojson.io. No white-box setup needed.

---

## Risks and Dependencies

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Overpass way geometry missing for some ways | Medium | Low | Segments without geometry are excluded from the FeatureCollection; the route still has `route_geojson` as fallback. TS-15 and TS-21 test this. |
| Aggregated segment count still large for long routes | Low | Low | Aggregation reduces segments significantly (many consecutive ways share properties). Payload size is bounded by the route length. |
| Score factor constants drift from route-level scorer | Low | Medium | Both functions import from the same constants in `scoring.py`. No duplication. |

---

## Task Breakdown

### Phase 1: Add segment detail to route output

**Task 1: Add compute_segment_score_factors to scoring module**
- Status: Complete
- Requirements: [route-segment-detail:FR-006]
- Test Scenarios: [route-segment-detail:compute_segment_score_factors/TS-01], [route-segment-detail:compute_segment_score_factors/TS-02], [route-segment-detail:compute_segment_score_factors/TS-03], [route-segment-detail:compute_segment_score_factors/TS-04], [route-segment-detail:compute_segment_score_factors/TS-05], [route-segment-detail:compute_segment_score_factors/TS-06], [route-segment-detail:compute_segment_score_factors/TS-07], [route-segment-detail:compute_segment_score_factors/TS-08], [route-segment-detail:compute_segment_score_factors/TS-09]
- Details: Add function to `scoring.py`. Uses existing constants. Write tests in `test_scoring.py`.

**Task 2: Add aggregate_segments_to_geojson to infrastructure module**
- Status: Complete
- Requirements: [route-segment-detail:FR-001], [route-segment-detail:FR-002], [route-segment-detail:FR-003], [route-segment-detail:FR-005], [route-segment-detail:FR-006]
- Test Scenarios: [route-segment-detail:aggregate_segments_to_geojson/TS-10], [route-segment-detail:aggregate_segments_to_geojson/TS-11], [route-segment-detail:aggregate_segments_to_geojson/TS-12], [route-segment-detail:aggregate_segments_to_geojson/TS-13], [route-segment-detail:aggregate_segments_to_geojson/TS-14], [route-segment-detail:aggregate_segments_to_geojson/TS-15], [route-segment-detail:aggregate_segments_to_geojson/TS-16], [route-segment-detail:aggregate_segments_to_geojson/TS-17], [route-segment-detail:aggregate_segments_to_geojson/TS-18], [route-segment-detail:aggregate_segments_to_geojson/TS-19], [route-segment-detail:aggregate_segments_to_geojson/TS-20], [route-segment-detail:aggregate_segments_to_geojson/TS-21]
- Details: Add function to `infrastructure.py`. Imports `compute_segment_score_factors` from `scoring.py`. Write tests in `test_infrastructure.py`.

**Task 3: Wire segments_geojson into server and update docs**
- Status: Complete
- Requirements: [route-segment-detail:FR-001], [route-segment-detail:FR-004]
- Test Scenarios: [route-segment-detail:CycleRouteMCP/TS-22], [route-segment-detail:CycleRouteMCP/TS-23], [route-segment-detail:CycleRouteMCP/TS-24]
- Details: Call `aggregate_segments_to_geojson(segments)` in `_assess_single_route()`, add to return dict. Add empty FeatureCollection to `_empty_assessment()` fallback. Write tests in `test_server.py`. Update `docs/API.md` routes JSON section with `segments_geojson` field documentation.

---

## Appendix

### Key Design Decision: Highway in Aggregation Key

The specification defines the aggregation key as `(provision, speed_limit, surface, lit, original_provision)`. This design adds `highway` to the key. Reason: FR-006 requires a `hostile_junction` boolean that depends on highway classification. Without `highway` in the key, a merged segment spanning both a primary road and a residential road would have ambiguous hostile_junction status. Including `highway` guarantees score factor consistency within each aggregated Feature, at the cost of slightly finer granularity. This never merges segments the spec would keep separate — it only prevents some merges the spec would allow.

### References
- [Route Segment Detail specification](.sdd/route-segment-detail/specification.md)
- [Scoring module](src/mcp_servers/cycle_route/scoring.py)
- [Infrastructure module](src/mcp_servers/cycle_route/infrastructure.py)

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.1 | 2026-02-22 | Claude | Mark all tasks complete |
| 1.0 | 2026-02-22 | Claude | Initial design |
