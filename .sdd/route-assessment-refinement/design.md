# Design: Route Assessment Refinement

**Version:** 1.0
**Date:** 2026-02-21
**Status:** Draft
**Linked Specification:** `.sdd/route-assessment-refinement/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The cycle route MCP server (`server.py`) requests three Valhalla routes per destination (shortest, safest, driving), then assesses both cycling routes via Overpass. Each assessment calls `parse_overpass_ways()` which divides the total route distance equally among all returned ways (~11m segments each). `identify_issues()` produces one issue per segment, creating dozens of duplicates for a single road. The GeoJSON output (`segments`) is a FeatureCollection of per-way LineStrings that includes side roads from the 20m Overpass buffer. `_score_directness()` compares cycling distance to driving distance.

```
Current flow per destination:
  Valhalla → shortest route → Overpass → segments → score + issues
  Valhalla → safest route   → Overpass → segments → score + issues
  Valhalla → driving route  → (directness baseline)
  Output: { shortest_route: {...}, safest_route: {...}, same_route }
```

### Proposed Architecture

Only the safest route is assessed via Overpass. The shortest route provides distance only (for directness scoring). The driving route is removed entirely. The output is flattened — the assessment IS the safest route, with `shortest_route_distance_m` for reference.

```
New flow per destination:
  Valhalla → shortest route → distance only (no Overpass)
  Valhalla → safest route   → Overpass → segments → score + issues
  Output: { destination, distance_m, score, issues, route_geojson,
            crossings_geojson, barriers_geojson,
            shortest_route_distance_m, same_route }
```

Key changes:
1. `parse_overpass_ways()` calculates per-way distance from geometry (haversine sum) instead of equal division
2. `identify_issues()` aggregates issues by road name + issue type
3. Output replaces `segments` with three GeoJSON collections: route (Valhalla polyline), crossings (Points), barriers (Points)
4. `_score_directness()` compares safest distance to shortest cycling distance
5. Output flattened from dual-route to single-assessment structure

### Technology Decisions

- **Haversine for way length** — `_haversine_distance()` already exists in `infrastructure.py` for barrier deduplication. Reuse it for summing node-to-node distances within each way. Accuracy is sufficient for the ~200m road segments involved.
- **Crossing location approximation** — Use the last geometry node of the preceding segment (or first node of the following) as the crossing point. This is an approximation but adequate for GeoJSON visualisation.
- **Flat output structure** — A single assessment (the safest route) eliminates redundancy. The shortest route contributes only distance for directness scoring. Consumers (orchestrator, API) process one assessment instead of two.

---

## Modified Components

### `parse_overpass_ways()` function

**Change Description:** Currently divides `route_distance_m` equally among all ways. Changes to calculate each segment's distance from its actual Overpass geometry by summing haversine distances between consecutive nodes.

**Dependants:** None — all callers consume `RouteSegment.distance_m` which now has actual per-way values.

**Kind:** Function

**Details:**

```
# Before:
per_way_distance = route_distance_m / len(ways)

# After:
For each way:
  distance = _way_length_m(way.get("geometry", []))
  # Falls back to 0 if geometry missing/single node
```

The `route_distance_m` parameter is retained for backward compatibility but no longer used for distance calculation.

**Requirements References:**
- [route-assessment-refinement:FR-006]: Geometry-based segment distances

**Test Scenarios**

**TS-01: Distance from geometry**
- Given: An Overpass response with a way whose geometry spans ~200m
- When: `parse_overpass_ways()` is called
- Then: The segment's `distance_m` is approximately 200m (within 5% of haversine calculation)

**TS-02: Way with no geometry**
- Given: An Overpass response with a way that has no geometry nodes
- When: `parse_overpass_ways()` is called
- Then: The segment's `distance_m` is 0

**TS-03: Multiple ways get independent distances**
- Given: Overpass response with two ways: one ~300m, one ~50m
- When: `parse_overpass_ways()` is called
- Then: First segment distance is ~300m, second is ~50m (not 175m each)

---

### `identify_issues()` function

**Change Description:** Currently generates one issue per segment. Changes to aggregate segments by `(road_name, issue_type)` and produce one aggregated issue per group with total affected distance and highest speed limit.

**Dependants:** `generate_s106_suggestions()` — consumes the issues list. No change needed since it reads the same fields (location, problem, severity, suggested_improvement).

**Kind:** Function

**Details:**

```
# Group segments by road name
# For each road group:
#   Check for each issue type (high-speed, moderate-speed, poor surface, unlit)
#   Aggregate: total distance, max speed_limit, combine location string
#   Produce one issue per (road_name, issue_type) combination
#
# Location format changes:
#   Before: "A41 (500m section)"
#   After:  "A41 (1200m)" — total distance across all segments of that road

Issue types are identified by their detection logic:
  - "no_provision_high_speed": provision=none, speed>=40
  - "no_provision_moderate": provision=none, speed>=30, classified road
  - "poor_surface": surface in poor list
  - "unlit_path": lit=False, off-road provision
```

**Requirements References:**
- [route-assessment-refinement:FR-001]: Aggregate issues by road

**Test Scenarios**

**TS-04: Multiple segments same road aggregated**
- Given: Three segments all named "A4095" with provision=none, speed=40, distances 100m, 150m, 250m
- When: `identify_issues()` is called
- Then: Exactly one "high" severity issue with distance 500m in location

**TS-05: Different roads produce separate issues**
- Given: Two segments: "A4095" (provision=none, 40mph, 500m) and "B4030" (provision=none, 40mph, 300m)
- When: `identify_issues()` is called
- Then: Two separate "high" severity issues

**TS-06: Same road different issue types**
- Given: Three segments named "River Path": two shared_use with gravel surface, one shared_use unlit
- When: `identify_issues()` is called
- Then: One surface issue and one lighting issue (separate issue types)

**TS-07: Speed limit uses highest from road group**
- Given: Two segments named "A4095": one at 40mph, one at 50mph
- When: `identify_issues()` is called
- Then: Issue reports 50mph (the highest speed limit)

**TS-08: Good route produces no issues (unchanged)**
- Given: Fully segregated route with good surface and lighting
- When: `identify_issues()` is called
- Then: Empty list

---

### `analyse_transitions()` function

**Change Description:** Currently non-priority crossings contain only `road_name` and `road_speed_limit`. Changes to also include `lat` and `lon` from segment geometry, enabling GeoJSON point generation.

**Dependants:** `crossings_to_geojson()` (new) — reads lat/lon from crossings.

**Kind:** Function

**Details:**

```
# For each non-priority crossing between segments:
# Approximate location from geometry of adjacent segments:
if seg_a has geometry:
    lat, lon = seg_a.geometry[-1][1], seg_a.geometry[-1][0]
elif seg_b has geometry:
    lat, lon = seg_b.geometry[0][1], seg_b.geometry[0][0]
else:
    lat, lon = None, None

non_priority_crossings.append({
    "road_name": road_seg.name,
    "road_speed_limit": road_seg.speed_limit,
    "lat": lat,      # NEW
    "lon": lon,       # NEW
})
```

**Requirements References:**
- [route-assessment-refinement:FR-003]: Non-priority crossing GeoJSON requires lat/lon

**Test Scenarios**

**TS-09: Crossing has lat/lon from segment geometry**
- Given: Segments with geometry, transition from off-road to road
- When: `analyse_transitions()` is called
- Then: Non-priority crossing entry has lat and lon fields matching the segment boundary

**TS-10: Crossing without geometry has None lat/lon**
- Given: Segments with no geometry (geometry=None), off-road to road transition
- When: `analyse_transitions()` is called
- Then: Non-priority crossing entry has lat=None, lon=None

---

### `_score_directness()` function

**Change Description:** Currently accepts `driving_distance_m` and compares cycling vs driving distance. Changes to accept `shortest_distance_m` and compare safest (assessed) cycling distance vs shortest cycling distance.

**Dependants:** `score_route()` — passes the parameter through.

**Kind:** Function

**Details:**

```
# Before:
def _score_directness(cycling_distance_m, driving_distance_m=None) -> float

# After:
def _score_directness(cycling_distance_m, shortest_distance_m=None) -> float
    # Same ratio logic: cycling_distance_m / shortest_distance_m
    # Same thresholds: <=1.1 full, <=1.3 70%, <=1.5 40%, >1.5 10%
    # Half points when shortest_distance_m is None
```

The function body logic is identical — only the parameter name and semantics change.

**Requirements References:**
- [route-assessment-refinement:FR-005]: Directness compares safest vs shortest cycling distance

**Test Scenarios**

**TS-11: Direct route scores full points**
- Given: cycling_distance=1000, shortest_distance=1000
- When: `_score_directness()` is called
- Then: Returns MAX_DIRECTNESS_POINTS (9)

**TS-12: Indirect route scores low**
- Given: cycling_distance=2000, shortest_distance=1000
- When: `_score_directness()` is called
- Then: Returns MAX_DIRECTNESS_POINTS * 0.1

**TS-13: No shortest distance gives half points**
- Given: cycling_distance=1000, shortest_distance=None
- When: `_score_directness()` is called
- Then: Returns MAX_DIRECTNESS_POINTS / 2

---

### `score_route()` function

**Change Description:** Rename `driving_distance_m` parameter to `shortest_distance_m`. Pass through to `_score_directness()`.

**Dependants:** `_assess_single_route()` in server.py — passes the parameter.

**Kind:** Function

**Details:**

```
# Before:
def score_route(segments, cycling_distance_m, driving_distance_m=None, transitions=None)

# After:
def score_route(segments, cycling_distance_m, shortest_distance_m=None, transitions=None)
```

**Requirements References:**
- [route-assessment-refinement:FR-005]: Directness compares safest vs shortest cycling distance

**Test Scenarios**

Existing test scenarios for `score_route()` are updated with the renamed parameter. No new test scenarios needed — the scoring logic is unchanged.

---

### `_assess_single_route()` method

**Change Description:** Currently returns `segments` (FeatureCollection of per-way LineStrings) and `route_geometry` (raw coords). Changes to return `route_geojson`, `crossings_geojson`, `barriers_geojson`, and `parallel_upgrades` count. Accepts route coords and shortest distance as new parameters. Passes `shortest_distance_m` to `score_route()` instead of `driving_distance_m`.

**Dependants:** `_assess_cycle_route()` — consumes the return dict.

**Kind:** Method

**Details:**

```
async def _assess_single_route(
    self,
    route_coords,
    cycling_distance_m,
    cycling_duration_s,
    dest_name,
    shortest_distance_m=None,  # replaces driving_distance_m
) -> dict | None:

    # ... existing Overpass + parse + parallel detection + transitions ...

    parallel_upgrades = sum(1 for s in segments if s.original_provision is not None)

    return {
        "distance_m": round(cycling_distance_m),
        "duration_minutes": round(cycling_duration_s / 60, 1),
        "provision_breakdown": provision,
        "route_geojson": route_to_geojson(route_coords, cycling_distance_m, cycling_duration_s),
        "crossings_geojson": crossings_to_geojson(transitions.get("non_priority_crossings", [])),
        "barriers_geojson": barriers_to_geojson(transitions.get("barriers", [])),
        "score": route_score,
        "issues": route_issues,
        "s106_suggestions": s106,
        "transitions": transitions,
        "parallel_upgrades": parallel_upgrades,
    }
```

**Requirements References:**
- [route-assessment-refinement:FR-002]: route_geojson from Valhalla polyline
- [route-assessment-refinement:FR-003]: crossings_geojson
- [route-assessment-refinement:FR-004]: barriers_geojson
- [route-assessment-refinement:FR-005]: shortest_distance_m parameter

**Test Scenarios**

**TS-14: Assessment output has route_geojson instead of segments**
- Given: Valid route with Overpass infrastructure data
- When: `_assess_single_route()` is called
- Then: Result contains `route_geojson` (FeatureCollection with LineString). No `segments` key.

**TS-15: Assessment includes crossings and barriers GeoJSON**
- Given: Route with barriers and off-road transitions in Overpass data
- When: `_assess_single_route()` is called
- Then: Result contains `crossings_geojson` and `barriers_geojson` FeatureCollections

**TS-16: Parallel upgrades counted**
- Given: Route where parallel detection upgrades one segment
- When: `_assess_single_route()` is called
- Then: Result contains `parallel_upgrades: 1`

---

### `_assess_cycle_route()` method

**Change Description:** Currently requests three Valhalla routes (shortest, safest, driving), assesses both cycling routes via Overpass, and returns `{shortest_route, safest_route, same_route}`. Changes to request only two Valhalla routes (shortest, safest — no driving), assess only the safest route via Overpass, and return a flat assessment with `shortest_route_distance_m` and `shortest_route_geometry` for reference.

**Dependants:** Orchestrator `_phase_assess_routes()` — stores the result in `self._route_assessments`. Evidence builders and route_narrative reference the result shape.

**Kind:** Method

**Details:**

```
async def _assess_cycle_route(self, arguments):
    # Step 1: Request TWO routes (no driving)
    shortest_data = await self._request_valhalla_route(..., costing="bicycle",
        costing_options={"bicycle": {"shortest": True}})
    safest_data = await self._request_valhalla_route(..., costing="bicycle",
        costing_options={"bicycle": {"use_roads": 0.1, ...}})
    # No driving route request

    # Fallback: if one fails, use the other
    # ...existing logic...

    # Step 2: Decode routes
    shortest_coords, shortest_dist, shortest_dur = _extract_route(shortest_data)
    safest_coords, safest_dist, safest_dur = _extract_route(safest_data)

    same_route = ...  # existing same-route detection

    # Step 3: Assess ONLY safest route
    assessment = await self._assess_single_route(
        safest_coords, safest_dist, safest_dur,
        dest_name, shortest_distance_m=shortest_dist,
    )

    # Step 4: Build flat result
    if assessment is None:
        assessment = _empty_assessment(safest_coords, safest_dist, safest_dur)

    return {
        "status": "success",
        "destination": dest_name,
        **assessment,  # flat: distance_m, score, issues, route_geojson, etc.
        "shortest_route_distance_m": round(shortest_dist),
        "shortest_route_geometry": shortest_coords,
        "same_route": same_route,
    }
```

**Requirements References:**
- [route-assessment-refinement:FR-005]: Only one Overpass call per destination; no driving route; flat output structure

**Test Scenarios**

**TS-17: Only one Overpass call per destination**
- Given: Mock Valhalla and Overpass handlers
- When: `_assess_cycle_route()` is called
- Then: Only one set of Overpass requests made (not two). Valhalla gets two bicycle requests (not three — no driving).

**TS-18: Flat output structure**
- Given: Successful route assessment
- When: `_assess_cycle_route()` is called
- Then: Result has top-level `distance_m`, `score`, `issues`, `route_geojson`, `crossings_geojson`, `barriers_geojson`, `shortest_route_distance_m`, `same_route`. No `shortest_route` or `safest_route` sub-objects.

**TS-19: Same route directness is 1.0**
- Given: Shortest and safest routes produce the same distance (same_route=true)
- When: `_assess_cycle_route()` is called
- Then: Directness score is MAX_DIRECTNESS_POINTS. `same_route` is true.

**TS-20: Safest-only failure returns empty stub**
- Given: Safest route Overpass returns no data, shortest route succeeds
- When: `_assess_cycle_route()` is called
- Then: Returns flat empty assessment with `shortest_route_distance_m` populated.

**TS-21: Both routes fail returns error**
- Given: Both Valhalla shortest and safest fail
- When: `_assess_cycle_route()` is called
- Then: Returns error result with `status: "error"`.

---

### `_build_evidence_context()` method (orchestrator)

**Change Description:** Currently iterates `ra.get("shortest_route")` and `ra.get("safest_route")` sub-objects. Changes to read flat assessment fields directly from `ra`. Counts parallel upgrades from `ra.get("parallel_upgrades")` instead of scanning segment features.

**Dependants:** None — output is a string for the LLM prompt.

**Kind:** Method

**Details:**

```
# Before:
routes_to_show = []
if same_route:
    route_data = ra.get("shortest_route", ra)
    routes_to_show.append(("Shortest & safest route (same)", route_data))
else:
    routes_to_show.append(("Shortest route", ra.get("shortest_route", {})))
    routes_to_show.append(("Safest route", ra.get("safest_route", {})))

# After:
# ra IS the assessment (safest route). Show it as a single entry.
route_lines.append(f"### Route to {dest}")
distance = ra.get("distance_m", 0)
score = ra.get("score", {})
# ... read flat fields directly ...

# Shortest route reference:
shortest_dist = ra.get("shortest_route_distance_m")
if shortest_dist and not ra.get("same_route", True):
    route_lines.append(f"- Shortest route distance: {shortest_dist}m")

# Parallel upgrades from explicit count:
upgraded = ra.get("parallel_upgrades", 0)
if upgraded:
    route_lines.append(f"- Parallel detection: {upgraded} segment(s) upgraded")
```

**Requirements References:**
- [route-assessment-refinement:FR-005]: Flat assessment structure consumed by orchestrator

**Test Scenarios**

**TS-22: Evidence context uses flat assessment fields**
- Given: Route assessment with flat structure (distance_m, score at top level)
- When: `_build_evidence_context()` is called
- Then: Evidence text includes distance, score, issues from flat fields. No "Shortest route" / "Safest route" labels.

**TS-23: Shortest route distance shown when different**
- Given: Route assessment with same_route=false, shortest_route_distance_m=2000
- When: `_build_evidence_context()` is called
- Then: Evidence text includes "Shortest route distance: 2000m"

---

### `_build_route_evidence_summary()` method (orchestrator)

**Change Description:** Same changes as `_build_evidence_context()` — read flat fields instead of dual-route sub-objects.

**Dependants:** None — output is a string for the structure prompt.

**Kind:** Method

**Requirements References:**
- [route-assessment-refinement:FR-005]: Flat assessment structure

**Test Scenarios**

Covered by TS-22 and TS-23 (same data flow pattern).

---

### Route narrative construction (orchestrator)

**Change Description:** Currently builds `shortest_route_summary` and `safest_route_summary` sub-objects. Changes to build a single `assessment_summary` from flat fields, plus `shortest_route_distance_m`.

**Dependants:** `ReviewContent.route_narrative` — `dict[str, Any]`, flexible.

**Kind:** Code block in `_phase_generate_review()`

**Details:**

```
# Before:
route_narrative = {
    "destinations": [{
        "destination_name": ra.get("destination"),
        "shortest_route_summary": {
            "distance_m": ra["shortest_route"]["distance_m"],
            "ltn_score": ra["shortest_route"]["score"]["score"],
            "rating": ra["shortest_route"]["score"]["rating"],
        },
        "safest_route_summary": { ... },
        "same_route": ra.get("same_route"),
    }]
}

# After:
route_narrative = {
    "destinations": [{
        "destination_name": ra.get("destination"),
        "assessment_summary": {
            "distance_m": ra["distance_m"],
            "ltn_score": ra["score"]["score"],
            "rating": ra["score"]["rating"],
        },
        "shortest_route_distance_m": ra.get("shortest_route_distance_m"),
        "same_route": ra.get("same_route"),
    }]
}
```

**Requirements References:**
- [route-assessment-refinement:FR-005]: Flat assessment structure

**Test Scenarios**

**TS-24: Route narrative uses flat structure**
- Given: Route assessments with flat structure
- When: `_phase_generate_review()` builds route_narrative
- Then: Each destination has `assessment_summary` (not `shortest_route_summary`/`safest_route_summary`), plus `shortest_route_distance_m`.

---

### `RouteAssessment` API model

**Change Description:** Currently has `shortest_route: RouteData | None` and `safest_route: RouteData | None` sub-objects. Changes to flat fields matching the new MCP output. The recently-added `RouteData` model is removed.

**Dependants:** `ReviewContent.route_assessments` — validates each assessment. `src/api/schemas/__init__.py` — exports.

**Kind:** Pydantic model

**Details:**

```
class RouteAssessment(BaseModel):
    destination: str | None = None
    destination_id: str | None = None
    distance_m: float | None = None
    duration_minutes: float | None = None
    provision_breakdown: dict[str, float] | None = None
    route_geojson: dict[str, Any] | None = None
    crossings_geojson: dict[str, Any] | None = None
    barriers_geojson: dict[str, Any] | None = None
    score: dict[str, Any] | None = None
    issues: list[dict[str, Any]] | None = None
    s106_suggestions: list[dict[str, Any]] | None = None
    shortest_route_distance_m: float | None = None
    same_route: bool | None = None
```

**Requirements References:**
- [route-assessment-refinement:FR-005]: Flat output structure consumed by API

**Test Scenarios**

**TS-25: Flat RouteAssessment parses correctly**
- Given: Dict with flat assessment fields (distance_m, score, route_geojson, etc.)
- When: `RouteAssessment(**data)` is constructed
- Then: All fields populated correctly

**TS-26: Empty RouteAssessment backward compatible**
- Given: Dict with only destination and destination_id
- When: `RouteAssessment(**data)` is constructed
- Then: All optional fields are None

---

## Added Components

### `_way_length_m()` function

**Description:** Calculate the total length of an OSM way in metres by summing haversine distances between consecutive geometry nodes.

**Users:** `parse_overpass_ways()`

**Kind:** Function

**Location:** `src/mcp_servers/cycle_route/infrastructure.py`

**Details:**

```
def _way_length_m(geometry: list[dict]) -> float:
    """Sum haversine distances between consecutive nodes.
    Returns 0 if fewer than 2 nodes."""
    total = 0.0
    for i in range(len(geometry) - 1):
        total += _haversine_distance(
            geometry[i]["lat"], geometry[i]["lon"],
            geometry[i+1]["lat"], geometry[i+1]["lon"],
        )
    return total
```

**Requirements References:**
- [route-assessment-refinement:FR-006]: Geometry-based segment distances

**Test Scenarios**

**TS-27: Known distance between two points**
- Given: Two nodes approximately 100m apart (known coordinates)
- When: `_way_length_m()` is called
- Then: Returns approximately 100m (within 1%)

**TS-28: Empty geometry returns zero**
- Given: Empty geometry list or single node
- When: `_way_length_m()` is called
- Then: Returns 0.0

---

### `route_to_geojson()` function

**Description:** Convert Valhalla route coordinates to a GeoJSON FeatureCollection with a single LineString Feature. Properties include distance and duration.

**Users:** `_assess_single_route()`

**Kind:** Function

**Location:** `src/mcp_servers/cycle_route/infrastructure.py`

**Details:**

```
def route_to_geojson(
    coords: list[list[float]],
    distance_m: float,
    duration_s: float,
) -> dict[str, Any]:
    """Build a FeatureCollection with one LineString from route coordinates."""
    if len(coords) < 2:
        geometry = None
    else:
        geometry = {"type": "LineString", "coordinates": coords}

    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "distance_m": round(distance_m),
                "duration_minutes": round(duration_s / 60, 1),
            },
        }],
    }
```

**Requirements References:**
- [route-assessment-refinement:FR-002]: Route GeoJSON from Valhalla geometry

**Test Scenarios**

**TS-29: Valid route produces LineString**
- Given: Coordinates list with 5+ points
- When: `route_to_geojson()` is called
- Then: Returns FeatureCollection with one Feature. Geometry is LineString with same coordinates.

**TS-30: Short route produces null geometry**
- Given: Coordinates list with fewer than 2 points
- When: `route_to_geojson()` is called
- Then: Feature has null geometry.

**TS-31: Properties include distance and duration**
- Given: Valid coordinates, distance_m=2500, duration_s=600
- When: `route_to_geojson()` is called
- Then: Feature properties have `distance_m: 2500` and `duration_minutes: 10.0`.

---

### `crossings_to_geojson()` function

**Description:** Convert non-priority crossings to a GeoJSON FeatureCollection of Point features. Crossings without lat/lon are skipped.

**Users:** `_assess_single_route()`

**Kind:** Function

**Location:** `src/mcp_servers/cycle_route/infrastructure.py`

**Details:**

```
def crossings_to_geojson(
    crossings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build FeatureCollection of Point features from crossing data."""
    features = []
    for c in crossings:
        lat, lon = c.get("lat"), c.get("lon")
        if lat is None or lon is None:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "road_name": c.get("road_name", "Unknown"),
                "road_speed_limit": c.get("road_speed_limit", 30),
            },
        })
    return {"type": "FeatureCollection", "features": features}
```

**Requirements References:**
- [route-assessment-refinement:FR-003]: Non-priority crossing GeoJSON

**Test Scenarios**

**TS-32: Crossings with lat/lon become Point features**
- Given: List with one crossing at lat=51.9, lon=-1.15
- When: `crossings_to_geojson()` is called
- Then: FeatureCollection with one Point feature at [-1.15, 51.9]

**TS-33: Crossings without lat/lon skipped**
- Given: List with one crossing where lat=None
- When: `crossings_to_geojson()` is called
- Then: FeatureCollection has empty features array

**TS-34: No crossings produces empty collection**
- Given: Empty crossings list
- When: `crossings_to_geojson()` is called
- Then: FeatureCollection with empty features array

---

### `barriers_to_geojson()` function

**Description:** Convert barrier data to a GeoJSON FeatureCollection of Point features.

**Users:** `_assess_single_route()`

**Kind:** Function

**Location:** `src/mcp_servers/cycle_route/infrastructure.py`

**Details:**

```
def barriers_to_geojson(
    barriers: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build FeatureCollection of Point features from barrier data."""
    features = []
    for b in barriers:
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [b["lon"], b["lat"]],
            },
            "properties": {
                "barrier_type": b.get("type", "unknown"),
                "node_id": b.get("node_id", 0),
            },
        })
    return {"type": "FeatureCollection", "features": features}
```

**Requirements References:**
- [route-assessment-refinement:FR-004]: Barrier GeoJSON

**Test Scenarios**

**TS-35: Barriers become Point features**
- Given: List with one barrier at lat=51.9, lon=-1.15, type="cycle_barrier"
- When: `barriers_to_geojson()` is called
- Then: FeatureCollection with one Point feature, properties include barrier_type and node_id

**TS-36: No barriers produces empty collection**
- Given: Empty barriers list
- When: `barriers_to_geojson()` is called
- Then: FeatureCollection with empty features array

---

## Used Components

### `_haversine_distance()`
**Location:** `src/mcp_servers/cycle_route/infrastructure.py:461`

**Provides:** Calculates distance between two lat/lon points in metres. Already used for barrier deduplication.

**Used By:** `_way_length_m()` (new) for summing node-to-node distances within ways.

### `RouteSegment` dataclass
**Location:** `src/mcp_servers/cycle_route/infrastructure.py:56`

**Provides:** Data container for parsed Overpass way data. `distance_m` field will now contain geometry-derived values.

**Used By:** All scoring and issues functions (unchanged interface).

### `decode_polyline()`
**Location:** `src/mcp_servers/cycle_route/polyline.py`

**Provides:** Decodes Valhalla's encoded polyline format to `[[lon, lat], ...]` coordinate arrays.

**Used By:** `_assess_cycle_route()` — provides coordinates for `route_to_geojson()`.

### `query_overpass_resilient()`
**Location:** `src/mcp_servers/cycle_route/infrastructure.py:614`

**Provides:** Resilient Overpass querying with retry and fallback. Called once per destination (not twice as before).

**Used By:** `_assess_single_route()` (unchanged).

---

## Documentation Considerations

- `docs/API.md` — Update the `route_assessments` example to show the flat structure with `route_geojson`, `crossings_geojson`, `barriers_geojson`, `shortest_route_distance_m`.

---

## Test Data

- Existing mock helpers (`_make_segment`, `_make_overpass_data`, `_make_road_way`, `_make_valhalla_handler`) are reused
- Geometry data for `_way_length_m` tests uses real-ish Bicester coordinates
- Overpass way geometry nodes can be constructed with known haversine distances for verification

---

## Test Feasibility

- All test scenarios use existing mock infrastructure (mock transports, fakeredis)
- No external dependencies required
- `asyncio.sleep` patches already exist for Overpass retry tests

---

## Risks and Dependencies

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Orchestrator evidence builders miss a reference to old dual-route structure | Medium | Low | Grep for "shortest_route" and "safest_route" in orchestrator tests; update all references |
| `segments_to_feature_collection()` becomes dead code | Certain | None | Remove it as part of Task 5; it has no other callers |
| Issue aggregation by "Unnamed" groups unrelated roads | Low | Low | Acceptable per spec — Unnamed roads are typically short service roads |
| Crossing lat/lon approximation is inaccurate | Low | Low | The geometry endpoint of adjacent segments is the best available approximation; exact intersection is out of scope |

---

## Feasability Review

- No blockers. All infrastructure exists. Changes are internal to MCP server and orchestrator.

---

## Task Breakdown

### Phase 1: Infrastructure and scoring changes

**Task 1: Geometry-based segment distances**
- Status: Backlog
- Requirements: [route-assessment-refinement:FR-006]
- Test Scenarios: [route-assessment-refinement:_way_length_m/TS-27], [route-assessment-refinement:_way_length_m/TS-28], [route-assessment-refinement:parse_overpass_ways/TS-01], [route-assessment-refinement:parse_overpass_ways/TS-02], [route-assessment-refinement:parse_overpass_ways/TS-03]
- Details:
  - Add `_way_length_m()` function to `infrastructure.py`
  - Modify `parse_overpass_ways()` to use `_way_length_m()` for segment distances
  - Update existing `test_basic_parsing` test (currently expects equal division)
  - Add test methods for the new scenarios

**Task 2: Issue aggregation by road**
- Status: Backlog
- Requirements: [route-assessment-refinement:FR-001]
- Test Scenarios: [route-assessment-refinement:identify_issues/TS-04], [route-assessment-refinement:identify_issues/TS-05], [route-assessment-refinement:identify_issues/TS-06], [route-assessment-refinement:identify_issues/TS-07], [route-assessment-refinement:identify_issues/TS-08]
- Details:
  - Rewrite `identify_issues()` to group segments by road name, then detect issues per group
  - Update existing tests that check per-segment issue counts
  - Add new tests for aggregation scenarios

**Task 3: Crossing lat/lon and GeoJSON converters**
- Status: Backlog
- Requirements: [route-assessment-refinement:FR-002], [route-assessment-refinement:FR-003], [route-assessment-refinement:FR-004]
- Test Scenarios: [route-assessment-refinement:analyse_transitions/TS-09], [route-assessment-refinement:analyse_transitions/TS-10], [route-assessment-refinement:route_to_geojson/TS-29], [route-assessment-refinement:route_to_geojson/TS-30], [route-assessment-refinement:route_to_geojson/TS-31], [route-assessment-refinement:crossings_to_geojson/TS-32], [route-assessment-refinement:crossings_to_geojson/TS-33], [route-assessment-refinement:crossings_to_geojson/TS-34], [route-assessment-refinement:barriers_to_geojson/TS-35], [route-assessment-refinement:barriers_to_geojson/TS-36]
- Details:
  - Add lat/lon to `analyse_transitions()` non-priority crossings
  - Add `route_to_geojson()`, `crossings_to_geojson()`, `barriers_to_geojson()` to `infrastructure.py`
  - Update existing transition tests to verify lat/lon presence
  - Add test class for each GeoJSON converter

**Task 4: Directness scoring parameter rename**
- Status: Backlog
- Requirements: [route-assessment-refinement:FR-005]
- Test Scenarios: [route-assessment-refinement:_score_directness/TS-11], [route-assessment-refinement:_score_directness/TS-12], [route-assessment-refinement:_score_directness/TS-13]
- Details:
  - Rename `driving_distance_m` → `shortest_distance_m` in `_score_directness()` and `score_route()`
  - Update all test references from `driving_distance_m` to `shortest_distance_m`

### Phase 2: Server, orchestrator, and API changes

**Task 5: Server output restructuring**
- Status: Backlog
- Requirements: [route-assessment-refinement:FR-002], [route-assessment-refinement:FR-003], [route-assessment-refinement:FR-004], [route-assessment-refinement:FR-005]
- Test Scenarios: [route-assessment-refinement:_assess_single_route/TS-14], [route-assessment-refinement:_assess_single_route/TS-15], [route-assessment-refinement:_assess_single_route/TS-16], [route-assessment-refinement:_assess_cycle_route/TS-17], [route-assessment-refinement:_assess_cycle_route/TS-18], [route-assessment-refinement:_assess_cycle_route/TS-19], [route-assessment-refinement:_assess_cycle_route/TS-20], [route-assessment-refinement:_assess_cycle_route/TS-21]
- Details:
  - Modify `_assess_single_route()`: replace segments with GeoJSON outputs, accept shortest_distance_m, add parallel_upgrades count
  - Modify `_assess_cycle_route()`: remove driving route request, assess only safest route, flatten output
  - Remove `segments_to_feature_collection()` (dead code)
  - Remove `OVERPASS_API_URL` import removal if not already done
  - Update existing server tests for new output structure
  - Add new test scenarios

**Task 6: Orchestrator and API update**
- Status: Backlog
- Requirements: [route-assessment-refinement:FR-005]
- Test Scenarios: [route-assessment-refinement:orchestrator/TS-22], [route-assessment-refinement:orchestrator/TS-23], [route-assessment-refinement:orchestrator/TS-24], [route-assessment-refinement:RouteAssessment/TS-25], [route-assessment-refinement:RouteAssessment/TS-26]
- Details:
  - Update `_build_evidence_context()` for flat assessment fields
  - Update `_build_route_evidence_summary()` for flat assessment fields
  - Update route_narrative construction for flat structure
  - Update `RouteAssessment` model: remove `RouteData` and dual-route fields, add flat fields
  - Update `src/api/schemas/__init__.py` exports
  - Update `docs/API.md` example
  - Update orchestrator tests (`test_route_assessment.py`) for flat structure
  - Update API tests (`test_schemas.py`, `test_reviews.py`) for flat RouteAssessment

---

## Intermediate Dead Code Tracking

**DC-01: `segments_to_feature_collection()`**
- Reason: Replaced by `route_to_geojson()`. No longer called after Task 5.
- Status: Pending — removed in Task 5.

---

## Intermediate Stub Tracking

None — no stubs.

---

## Appendix

### QA Feasibility Analysis

**QA-01 (Verify aggregated issues):** Fully testable in production. Submit a review for any application near a major road. Inspect `route_assessments[0].issues` in the review result — each road should appear once per issue type.

**QA-02 (Verify route GeoJSON is clean):** Fully testable. Extract `route_geojson` from the assessment result and paste into geojson.io. The LineString should follow the road without side-road stubs.

**QA-03 (Verify crossing and barrier collections):** Fully testable. Extract `crossings_geojson` and `barriers_geojson` from the result. Points should appear at appropriate locations.

**QA-04 (Verify single Overpass call):** Testable via worker logs. Grep for "Overpass" entries. Should see one set of retry logs per destination, not two.

### Glossary
- **Way**: An OSM element representing a linear feature (road, path). Overpass returns ways within the buffer zone.
- **Segment**: A `RouteSegment` parsed from an Overpass way, with infrastructure classification.
- **Flat assessment**: The new output structure where the safest route assessment fields are at the top level, not nested under `safest_route`.

### References
- [Overpass API](https://wiki.openstreetmap.org/wiki/Overpass_API)
- [Valhalla routing](https://valhalla.github.io/valhalla/)
- LTN 1/20: Cycle Infrastructure Design (DfT)

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-21 | Claude | Initial design |
