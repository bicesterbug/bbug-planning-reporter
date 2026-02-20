# Specification: Valhalla Routing Engine

**Version:** 1.0
**Date:** 2026-02-20
**Status:** Draft

---

## Problem Statement

The cycle route assessment MCP server depends on the OSRM public demo server (`router.project-osrm.org`) for bicycle routing, which has no SLA and causes production failures (504 timeouts observed). OSRM's single bike profile cannot differentiate between "shortest" and "safest" routes — both alternatives optimise the same cost function. The directness component of the LTN 1/20 score always defaults to half-points (4.5/9) because no driving distance is available for comparison.

## Beneficiaries

**Primary:**
- Review consumers (planning officers, cycling advocates) — more accurate LTN 1/20 scores and meaningfully different shortest vs safest route comparisons

**Secondary:**
- System operators — elimination of an unreliable external dependency reduces on-call burden

---

## Outcomes

**Must Haves**
- Self-hosted Valhalla instance replaces the OSRM public demo server for all bicycle routing
- Shortest and safest routes are computed with genuinely different routing strategies (not just geometric alternatives)
- Driving distance is available for every route assessment, enabling accurate directness scoring
- Elevation data is available, enabling hill-aware bicycle routing
- Production reliability is under our control (no external routing API dependency)

**Nice-to-haves**
- Costing parameters (use_roads, use_hills, avoid_bad_surfaces) are configurable via environment variables rather than hardcoded

---

## Explicitly Out of Scope

- Replacing the Overpass API for infrastructure analysis — Overpass remains for way tag queries. Valhalla's `trace_attributes` endpoint may replace Overpass in a future specification.
- BRouter integration — evaluated in the routing engine spike, deferred to a separate specification if needed.
- Custom Valhalla tile builds in CI/CD — tiles are built once at deployment time from a downloaded PBF file. Automated OSM data updates are a future concern.
- Changes to the LTN 1/20 scoring weights or thresholds — only the directness score input changes (from None to actual driving distance).
- Changes to the Overpass query, infrastructure analyser, or issue identification logic.

---

## Functional Requirements

**FR-001: Valhalla Container in Docker Compose**
- Description: A self-hosted Valhalla routing container runs alongside the existing services, built from the Oxfordshire OSM extract with elevation data enabled. The cycle-route-mcp service connects to it via the Docker network.
- Acceptance criteria: `docker compose up valhalla` starts a Valhalla instance that responds to `/route` requests on its internal port. The cycle-route-mcp service can reach it at `http://valhalla:8002`.
- Failure/edge cases: If the Valhalla container fails to start (e.g., missing PBF file), the cycle-route-mcp service should log the connection failure and return an error for route assessments rather than hanging.

**FR-002: Bicycle Route via Valhalla**
- Description: The `_assess_cycle_route` method calls Valhalla's `/route` endpoint with `costing: "bicycle"` instead of OSRM. The route response provides distance (metres), duration (seconds), and route geometry as coordinate pairs.
- Acceptance criteria: A route request to Valhalla returns distance, duration, and geometry that can be passed to the existing `_assess_single_route` method and Overpass infrastructure analysis without changes to downstream logic.
- Failure/edge cases: Valhalla returns an error object with `error_code` and `status_code` fields (not OSRM's `"code": "Ok"` format). A "no route" response from Valhalla must be handled and return the existing `error_type: "no_route"` response.

**FR-003: Dual-Strategy Routing (Shortest vs Safest)**
- Description: Two separate Valhalla requests are made per destination — one with `shortest: true` for the shortest distance route, and one with `use_roads: 0.1, avoid_bad_surfaces: 0.6` for the safest/most comfortable route. Each is independently assessed for infrastructure, scored, and the results populate `shortest_route` and `safest_route` in the response.
- Acceptance criteria: When the two requests return different geometries, `same_route` is false and the shortest route has a shorter distance while the safest route has a higher LTN 1/20 score (or equal, if the shortest route happens to also be the safest). When both return the same geometry, `same_route` is true.
- Failure/edge cases: If the safest route request fails but shortest succeeds (or vice versa), the successful route is used for both `shortest_route` and `safest_route` with `same_route: true` and a note.

**FR-004: Driving Distance for Directness Score**
- Description: A third Valhalla request per destination uses `costing: "auto"` to compute the driving distance. This distance is passed to `score_route()` as the `driving_distance_m` parameter, replacing the current `None` default.
- Acceptance criteria: `_score_directness()` receives an actual driving distance value, producing scores that vary based on how direct the cycling route is compared to driving. A cycling route 10% longer than driving scores 9/9; one 50%+ longer scores 0.9/9.
- Failure/edge cases: If the driving route request fails (timeout, Valhalla error), fall back to `driving_distance_m=None` (half-points, as today). The driving route failure must not block the bicycle route assessment.

**FR-005: Valhalla Response Format Adaptation**
- Description: Valhalla returns route geometry as an encoded polyline with 6-digit precision (not GeoJSON). The encoded shape must be decoded to a list of `[lon, lat]` coordinate pairs for compatibility with the existing Overpass query builder and route geometry output.
- Acceptance criteria: Decoded coordinates from Valhalla produce the same downstream behaviour as OSRM's GeoJSON coordinates — Overpass queries return infrastructure data and route geometry is included in the assessment response as `[lon, lat]` pairs.
- Failure/edge cases: Empty or malformed shape strings must be handled gracefully (treat as no route).

**FR-006: Elevation-Aware Bicycle Routing**
- Description: The Valhalla container is built with SRTM elevation data. The bicycle route requests include the `use_hills` costing parameter, causing Valhalla to prefer flatter routes when alternatives exist and to adjust duration estimates for gradients.
- Acceptance criteria: A route request with `use_hills: 0.3` between two points with significant elevation difference returns a different (flatter) route than `use_hills: 1.0`, or the same route with adjusted duration.
- Failure/edge cases: If elevation data is unavailable for a tile, Valhalla routes without elevation penalties (graceful degradation). No code changes needed — this is Valhalla's built-in behaviour.

**FR-007: Configuration via Environment Variables**
- Description: The Valhalla endpoint URL and bicycle costing parameters are configurable via environment variables, with sensible defaults.
- Acceptance criteria: Setting `VALHALLA_URL=http://valhalla:8002` in the cycle-route-mcp environment configures the routing endpoint. The existing `OSRM_URL` variable is removed. Costing parameters (`VALHALLA_USE_ROADS`, `VALHALLA_USE_HILLS`, `VALHALLA_AVOID_BAD_SURFACES`) have defaults matching the safest route strategy.
- Failure/edge cases: If `VALHALLA_URL` is not set, it defaults to `http://valhalla:8002` (Docker network address).

---

## Non-Functional Requirements

**NFR-001: Route Request Latency**
- A single Valhalla bicycle route request for an Oxfordshire origin/destination pair must complete in under 500ms (p95). The full dual-strategy assessment (2 bicycle + 1 auto + Overpass) must complete within the existing 30-second timeout for 3 destinations.

**NFR-002: Container Resource Limits**
- The Valhalla container must run within 1GB RAM and 500MB disk for tiles + elevation data (Oxfordshire extract). This fits within the production server's available resources alongside the existing 512MB cycle-route-mcp container.

---

## QA Plan

**QA-01: Route assessment completes with Valhalla**
- Goal: Verify end-to-end route assessment works with the self-hosted Valhalla instance
- Steps:
  1. Start the full Docker Compose stack including the Valhalla container
  2. Submit a review for application 25/01178/REM (South East Bicester)
  3. Wait for review to complete
  4. Fetch the review JSON and inspect `route_narrative`
- Expected: Review completes successfully. `route_narrative` contains destinations with non-zero distances and LTN scores. No OSRM-related errors in logs.

**QA-02: Shortest vs safest routes differ**
- Goal: Verify the two routing strategies produce meaningfully different results
- Steps:
  1. Check the cycle-route-mcp logs for a completed review
  2. Compare `shortest_route.distance_m` and `safest_route.distance_m` for a destination where alternative routes exist
- Expected: For at least one destination, the shortest route has a shorter distance and the safest route has a higher (or equal) LTN 1/20 score. If both routes are the same, `same_route` is true.

**QA-03: Directness score uses actual driving distance**
- Goal: Verify the directness score is no longer defaulting to half-points
- Steps:
  1. Check the score breakdown in a completed route assessment
  2. Look at the `directness` component in the score breakdown
- Expected: The directness score varies from the previous constant 4.5 — it should be higher for direct routes (closer to 9.0) or lower for indirect routes (closer to 0.9).

**QA-04: Elevation affects route selection**
- Goal: Verify elevation data is being used
- Steps:
  1. Inspect Valhalla container logs during startup to confirm elevation tiles are loaded
  2. Compare routes for a destination with hills vs a flat destination
- Expected: Valhalla logs confirm SRTM elevation data loaded. Duration estimates account for gradient (visible in response `summary.time`).

---

## Open Questions

None — all questions resolved during the spike and discovery interview.

---

## Appendix

### Glossary
- **OSRM:** Open Source Routing Machine — the current routing engine (public demo server)
- **Valhalla:** Open-source C++ routing engine with dynamic costing
- **Encoded polyline:** Compact string representation of route geometry (Valhalla uses 6-digit precision)
- **Costing options:** Per-request parameters that tune Valhalla's route selection (use_roads, use_hills, etc.)
- **SRTM:** Shuttle Radar Topography Mission — elevation dataset used by Valhalla

### References
- [Routing engine spike report](.sdd/routing-engine-spike/spike-report.md)
- [Valhalla API reference](https://valhalla.github.io/valhalla/api/turn-by-turn/api-reference/)
- [Valhalla bicycle costing](https://valhalla.github.io/valhalla/sif/elevation_costing/)
- [Valhalla Docker setup](https://github.com/valhalla/valhalla/blob/master/docker/README.md)
- [Geofabrik Oxfordshire extract](https://download.geofabrik.de/europe/united-kingdom/england/oxfordshire.html)

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-20 | Claude | Initial specification |
