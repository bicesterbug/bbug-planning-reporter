# Specification: Route Segment Geometry

**Version:** 1.0
**Date:** 2026-02-18
**Status:** Implemented

---

## Problem Statement

The route assessment data includes per-segment metadata (provision type, highway class, speed limit, surface) but no geometry. The website cannot render individual segments on a map with colour coding because there are no coordinates to draw. The Overpass API already returns per-way geometry during infrastructure analysis, but it is discarded after parallel detection. This means the data needed for map rendering is fetched but thrown away.

## Beneficiaries

**Primary:**
- Website users viewing route assessment maps — can see each segment colour-coded by cycling provision quality

**Secondary:**
- Planning officers — visual evidence of route quality issues at specific locations

---

## Outcomes

**Must Haves**
- Each route segment has its own geometry (coordinates) from OpenStreetMap
- Segments are returned as a GeoJSON FeatureCollection, directly renderable by map libraries (Mapbox GL, Leaflet)
- Each Feature carries the existing segment properties (provision, highway, speed_limit, surface, lit, distance_m, name) so the website can map them to colours

**Nice-to-haves**
- None

---

## Explicitly Out of Scope

- Colour mapping logic — the website determines how to colour each segment based on its properties
- Per-segment scoring — segments carry existing fields only, no new per-segment score
- Changes to the overall route scoring algorithm
- Changes to the route_narrative (LLM-generated prose) or route_geometry (OSRM full-route LineString)

---

## Functional Requirements

**FR-001: Store Overpass way geometry on each segment**
- Description: During infrastructure classification, the geometry returned by the Overpass API for each way must be preserved on the corresponding RouteSegment. The Overpass query already uses `out body geom` which returns coordinate arrays for ways.
- Acceptance criteria: Each RouteSegment has a `geometry` field containing an ordered list of `[lon, lat]` coordinate pairs from the Overpass response. Segments whose way was not found in the Overpass response (edge case) have `geometry: null`.
- Failure/edge cases: If the Overpass response does not include geometry for a way (e.g., the way was not returned), the segment's geometry is null rather than failing the entire assessment.

**FR-002: Replace segments array with GeoJSON FeatureCollection**
- Description: The `segments` field on each route variant (shortest_route, safest_route) must be a GeoJSON FeatureCollection instead of a plain array of dicts. Each Feature represents one segment.
- Acceptance criteria: The `segments` field is a valid GeoJSON FeatureCollection with `type: "FeatureCollection"` and a `features` array. Each Feature has `type: "Feature"`, a `geometry` object (LineString or null), and a `properties` object.
- Failure/edge cases: If a segment has no geometry, its Feature has `geometry: null` (valid GeoJSON for a Feature with unknown location).

**FR-003: Feature properties include existing segment fields**
- Description: Each Feature's `properties` object must include all current segment fields: `way_id`, `provision`, `highway`, `speed_limit`, `surface`, `lit`, `distance_m`, `name`, and `original_provision` (when present).
- Acceptance criteria: A Feature's properties object contains all the listed fields. The website can read `properties.provision` to determine segment colour.
- Failure/edge cases: None — all fields are always present on a RouteSegment.

**FR-004: Feature geometry is a GeoJSON LineString**
- Description: Each Feature's geometry must be a GeoJSON LineString with coordinates in `[longitude, latitude]` order (GeoJSON standard, EPSG:4326).
- Acceptance criteria: The geometry object has `type: "LineString"` and `coordinates: [[lon, lat], [lon, lat], ...]`. Coordinates are in longitude-first order per the GeoJSON specification (RFC 7946).
- Failure/edge cases: Very short segments (single-node ways) may have only one coordinate pair. These should still produce a valid LineString with at least 2 points, or geometry should be null if fewer than 2 points are available.

---

## QA Plan

**QA-01: Verify segments FeatureCollection in routes JSON**
- Goal: Confirm the routes JSON contains GeoJSON FeatureCollection for segments
- Steps:
  1. Submit a review for an application with route destinations configured
  2. Wait for completion
  3. Download the `_routes.json` from the output URL
  4. Inspect `shortest_route.segments` (or `safest_route.segments`)
- Expected: `segments` is a GeoJSON FeatureCollection. Each Feature has `type: "Feature"`, a `geometry` with `type: "LineString"` and `coordinates` array, and `properties` containing `provision`, `highway`, `speed_limit`, `surface`, `lit`, `distance_m`, `name`.

**QA-02: Verify map rendering**
- Goal: Confirm the FeatureCollection can be loaded directly into a map library
- Steps:
  1. Copy the `segments` FeatureCollection from the routes JSON
  2. Load it into a GeoJSON viewer (e.g., geojson.io)
  3. Verify segments appear as distinct lines along the route
- Expected: Segments render as individual polylines. Each has properties visible in the feature inspector. Segments follow the expected route path.

---

## Open Questions

None.

---

## Appendix

### Glossary
- **Provision**: The type of cycling infrastructure on a road segment (segregated cycle track, shared-use path, on-road lane, advisory lane, or none)
- **GeoJSON FeatureCollection**: A standard geospatial format (RFC 7946) containing an array of Feature objects, each with geometry and properties
- **OSRM**: Open Source Routing Machine — provides the route path between origin and destination
- **Overpass**: OpenStreetMap query API — provides way-level infrastructure data along the route

### References
- [GeoJSON Specification (RFC 7946)](https://datatracker.ietf.org/doc/html/rfc7946)
- [route-dual-routing spec](.sdd/route-dual-routing/specification.md)
- [cycle-route-assessment spec](.sdd/cycle-route-assessment/specification.md)

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-18 | Claude | Initial specification |
