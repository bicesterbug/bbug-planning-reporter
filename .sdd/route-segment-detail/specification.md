# Specification: Route Segment Detail

**Version:** 1.1
**Date:** 2026-02-22
**Status:** Draft

---

## Problem Statement

The routes JSON output provides only aggregate data (total provision breakdown, overall LTN 1/20 score). Clients such as the bbug-website cannot break down a route by LTN compliance factors (provision type, speed environment, surface quality, lighting) because the per-segment data from Overpass is consumed internally by the scorer and then discarded. This prevents rendering colour-coded route maps or provision tables that show where along the route the problems are.

## Beneficiaries

**Primary:**
- bbug-website (Vercel) — needs per-segment data to render colour-coded route maps and provision breakdown tables

**Secondary:**
- Planning officers and councillors — visual route breakdown makes LTN 1/20 compliance gaps immediately obvious
- BBUG committee — can reference specific road segments when drafting consultation responses

---

## Outcomes

**Must Haves**
- Routes JSON includes a GeoJSON FeatureCollection of aggregated route segments with provision, speed, surface, lighting, and distance properties
- Consecutive segments with identical provision, speed limit, surface, and lighting are merged into a single feature with concatenated geometry
- Each aggregated feature includes the list of road names and OSM way IDs it covers
- Each aggregated feature includes per-segment LTN 1/20 quality factors so clients can colour-code by compliance
- Existing routes JSON fields are unchanged (backward compatible)

**Nice-to-haves**
- None

---

## Explicitly Out of Scope

- Segment-level issue attribution (issues remain a flat list with location strings)
- Rendering logic on the website (that is a bbug-website concern)
- Changes to the API response schema (segments are in the routes JSON file only)

---

## Functional Requirements

**FR-001: Segments GeoJSON in Routes Output**
- Description: The route assessment output must include a `segments_geojson` field containing a GeoJSON FeatureCollection. Each Feature represents one aggregated segment of the route with consistent infrastructure characteristics.
- Acceptance criteria: The `segments_geojson` field is present in each route assessment object in the `_routes.json` output file. It is a valid GeoJSON FeatureCollection with one or more LineString features.
- Failure/edge cases: If Overpass returns no way geometry for a segment, that segment is excluded from the FeatureCollection (the route still has `route_geojson` for the full line). If all segments lack geometry, `segments_geojson` is an empty FeatureCollection.

**FR-002: Consecutive Segment Aggregation**
- Description: Adjacent segments with identical provision, speed limit, surface, and lit values must be merged into a single GeoJSON Feature. Their geometries are concatenated into one LineString, their distances are summed, their road names are collected into an array, and their way IDs are collected into an array.
- Acceptance criteria: Given three consecutive segments [A(segregated, 30, asphalt, true), B(segregated, 30, asphalt, true), C(none, 40, asphalt, true)], the output contains two Features: one for A+B (merged) and one for C. The merged feature's `distance_m` equals A.distance_m + B.distance_m, its `way_ids` contains both way IDs, and its `names` contains the deduplicated road names.
- Failure/edge cases: Two segments with the same properties that are not adjacent (separated by a different segment) must remain as separate Features. A single segment that matches no neighbours is emitted as its own Feature.

**FR-003: Feature Properties**
- Description: Each GeoJSON Feature in `segments_geojson` must include the following properties: `provision` (string), `speed_limit` (integer, mph), `surface` (string), `lit` (boolean or null), `distance_m` (float, rounded to 1 decimal), `names` (array of unique road name strings), `way_ids` (array of integer OSM way IDs).
- Acceptance criteria: Every Feature in the FeatureCollection has all seven properties with the correct types. `names` contains no duplicates. `way_ids` preserves the order of the original segments.
- Failure/edge cases: When a segment has an empty or unnamed road (`name` is empty string), it is excluded from the `names` array. If all merged segments are unnamed, `names` is an empty array.

**FR-004: Backward Compatibility**
- Description: Adding `segments_geojson` must not change any existing fields in the route assessment output. The `provision_breakdown`, `score`, `issues`, `route_geojson`, `crossings_geojson`, `barriers_geojson`, and `transitions` fields must remain unchanged.
- Acceptance criteria: Existing test assertions for route assessment output continue to pass without modification. The new field is additive only.
- Failure/edge cases: None.

**FR-005: Original Provision Annotation**
- Description: When a segment's provision was upgraded by parallel detection (i.e. `original_provision` is not null), the Feature properties must include an `original_provision` field showing what the provision was before the upgrade.
- Acceptance criteria: A Feature whose underlying segment had `original_provision = "none"` and `provision = "segregated"` (due to parallel cycleway detection) includes `"original_provision": "none"` in its properties. Features with no parallel upgrade omit the `original_provision` property.
- Failure/edge cases: If two consecutive segments both have the same provision, speed, surface, lit, but different `original_provision` values, they must NOT be merged (the aggregation key includes `original_provision`).

**FR-006: Per-Segment LTN 1/20 Quality Factors**
- Description: Each GeoJSON Feature in `segments_geojson` must include a `score_factors` property containing the LTN 1/20 quality factors for that segment. These are the same 0.0--1.0 factors that the route-level scorer uses, allowing clients to colour-code segments by any compliance dimension. The factors are:
  - `segregation` (float 0.0--1.0): Provision quality. segregated=1.0, shared_use=0.7, on_road_lane=0.4, advisory_lane=0.0, none=0.0.
  - `speed_safety` (float 0.0--1.0): Speed environment safety. Only meaningful for unsegregated segments (provision is `none` or `advisory_lane`). For segregated/shared_use/on_road_lane segments, this is `null` (speed is not a factor). For unsegregated: speed_limit ≤20mph=1.0, ≤30mph=0.6, ≤40mph=0.2, >40mph=0.0.
  - `surface_quality` (float 0.0--1.0): Surface condition. Good surfaces (asphalt, paved, concrete, paving_stones)=1.0, fair (compacted, fine_gravel)=0.6, unknown=0.5, poor (everything else)=0.2.
  - `hostile_junction` (boolean): Whether this segment represents a hostile junction — `true` when provision is `none`, highway is primary/secondary/trunk/tertiary, and speed_limit ≥ 30mph.
- Acceptance criteria: Every Feature in `segments_geojson` has a `score_factors` property with all four keys. A segregated cycleway segment has `{"segregation": 1.0, "speed_safety": null, "surface_quality": 1.0, "hostile_junction": false}`. A 40mph A-road with no provision and asphalt surface has `{"segregation": 0.0, "speed_safety": 0.2, "surface_quality": 1.0, "hostile_junction": true}`.
- Failure/edge cases: When consecutive segments are aggregated, all underlying segments share the same provision, speed, surface, and lit — therefore they have identical score factors. The merged Feature uses those same factor values (no averaging needed).

---

## QA Plan

**QA-01: Visual inspection of routes JSON**
- Goal: Verify segments_geojson is present, correctly structured, and includes score factors
- Steps:
  1. Submit a review for application 21/03267/OUT (Baynards Green)
  2. Wait for completion
  3. Download the routes JSON from the `urls.routes_json` URL
  4. Inspect the first route object for `segments_geojson`
  5. Verify it is a FeatureCollection with LineString features
  6. Check properties include provision, speed_limit, surface, lit, distance_m, names, way_ids, score_factors
  7. Verify a segment with provision "none" on a primary/secondary road has `hostile_junction: true` and `speed_safety` is a float
  8. Verify a segment with provision "segregated" has `segregation: 1.0` and `speed_safety: null`
- Expected: Multiple features with varying provision types and score factors. Features along the same road with the same characteristics are merged (fewer features than raw Overpass ways). Total distance_m across all features approximately equals the route distance_m.

**QA-02: Map rendering verification**
- Goal: Verify segments can be rendered on a map colour-coded by compliance
- Steps:
  1. Copy the `segments_geojson` from QA-01 into geojson.io
  2. Visually confirm the segments cover the route from origin to destination
  3. Check that features with different provision types appear as separate line segments
  4. Spot-check that a segment with `segregation: 0.0` corresponds to a road with no cycling provision
- Expected: The segments form a continuous or near-continuous line along the route. No large gaps. Score factors match the visible infrastructure properties.

---

## Open Questions

None — all questions resolved during discovery.

---

## Appendix

### Glossary
- **Provision:** The type of cycling infrastructure on a road segment (segregated, shared_use, on_road_lane, advisory_lane, none)
- **Parallel detection:** When a road segment with no cycling provision has an adjacent cycleway detected by Overpass, its provision is upgraded and the original is recorded
- **LTN 1/20:** Local Transport Note 1/20 — UK government guidance on cycle infrastructure design quality

### References
- [Route Assessment Refinement spec](.sdd/route-assessment-refinement/specification.md)
- [Cycle Route Assessment spec](.sdd/cycle-route-assessment/specification.md)
- [Routes JSON format](docs/API.md#routes-json-_routesjson)

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.1 | 2026-02-22 | Claude | Add FR-006 per-segment LTN 1/20 quality factors |
| 1.0 | 2026-02-22 | Claude | Initial specification |
