# Design: Valhalla Routing Engine

**Version:** 1.0
**Date:** 2026-02-20
**Status:** Draft
**Linked Specification** `.sdd/valhalla-routing-engine/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The cycle-route-mcp service (`src/mcp_servers/cycle_route/server.py`) makes HTTP calls to two external public APIs:

1. **OSRM** (`router.project-osrm.org`) — bicycle route geometry, distance, duration
2. **Overpass API** (`overpass-api.de`) — OSM way tags within 20m buffer of route geometry

The `CycleRouteMCP` class takes an `osrm_url` constructor parameter (defaulting to `DEFAULT_OSRM_URL` constant or `OSRM_URL` env var). For each destination, it makes one OSRM request with `alternatives=true`, then assesses each returned route via Overpass + scoring. The "shortest" and "safest" routes are selected post-hoc from the assessed alternatives by min distance and max score respectively.

The `score_route()` function accepts an optional `driving_distance_m` parameter which is always `None` today, causing `_score_directness()` to return half-points (4.5/9).

### Proposed Architecture

```
cycle-route-mcp ──► Valhalla (self-hosted, Docker network)
                       ├── bicycle route (shortest)     costing:"bicycle", shortest:true
                       ├── bicycle route (safest)        costing:"bicycle", use_roads:0.1
                       └── driving route (directness)    costing:"auto"
                ──► Overpass API (external, unchanged)
```

Replace the single OSRM call with three Valhalla calls per destination:
1. **Shortest bicycle route** — `costing: "bicycle"` with `shortest: true`
2. **Safest bicycle route** — `costing: "bicycle"` with `use_roads: 0.1, avoid_bad_surfaces: 0.6, use_hills: 0.3`
3. **Driving route** — `costing: "auto"` (distance only, for directness score)

Each bicycle route is then independently assessed via Overpass + scoring (as today). The driving distance feeds into `score_route()` for directness scoring.

A new Valhalla sidecar container runs in both dev and production Docker Compose stacks, built from the Oxfordshire OSM extract with SRTM elevation data.

### Technology Decisions

- **Valhalla Docker image**: `ghcr.io/valhalla/valhalla-scripted:latest` — official upstream image with built-in tile building
- **OSM extract**: Oxfordshire from Geofabrik (~25 MB PBF). Covers all Bicester-area destinations. Routes that cross the extract boundary will fail gracefully (no route found).
- **Elevation**: SRTM tiles enabled via `build_elevation=True`. Modest disk overhead (~200 MB for Oxfordshire tiles).
- **Polyline decoding**: Valhalla returns encoded polylines with 6-digit precision. A new `decode_polyline` utility function converts these to `[lon, lat]` pairs for compatibility with the existing Overpass query builder.
- **No OSRM compatibility mode**: Valhalla can output OSRM-format JSON but this requires the `osrm` output format which doesn't support all bicycle costing options. We use native Valhalla JSON format.

### Quality Attributes

- **Reliability**: Self-hosted container eliminates dependency on external OSRM public demo server
- **Latency**: Local Docker network requests vs internet round-trips. Valhalla responds in ~5-20ms for regional routes
- **Maintainability**: Valhalla URL is configurable; the polyline decoder is a pure function with no dependencies

---

## Modified Components

### CycleRouteMCP (`src/mcp_servers/cycle_route/server.py`)

**Change Description** Currently constructs OSRM GET requests with URL path parameters and query strings, parses OSRM JSON response format (`code: "Ok"`, `routes[].geometry.coordinates`). Must change to Valhalla POST requests with JSON body, parse Valhalla response format (`trip.legs[].shape`, `trip.summary`), and make three requests per destination instead of one.

**Dependants** Test file `tests/test_mcp_servers/test_cycle_route/test_server.py` — all mock helpers and assertions change.

**Kind** Class

**Details**

Constructor changes:
- Remove `osrm_url` parameter, replace with `valhalla_url` (default: env `VALHALLA_URL` or `http://valhalla:8002`)
- Remove `DEFAULT_OSRM_URL` constant
- Add `DEFAULT_VALHALLA_URL = "http://valhalla:8002"`

`_assess_cycle_route` changes:
- Replace the single OSRM GET call with three Valhalla POST calls:
  1. POST `/route` with `costing: "bicycle", costing_options: {bicycle: {shortest: true}}`
  2. POST `/route` with `costing: "bicycle", costing_options: {bicycle: {use_roads: 0.1, avoid_bad_surfaces: 0.6, use_hills: 0.3}}`
  3. POST `/route` with `costing: "auto"` — extract `trip.summary.length` (km) and convert to metres for driving distance
- Parse each bicycle response: decode `trip.legs[0].shape` to coordinates, extract `trip.summary.length` (km→m) and `trip.summary.time` (seconds)
- Pass decoded coordinates and distances to `_assess_single_route()` (same interface as today)
- Pass driving distance to `score_route()` via a new `driving_distance_m` parameter on `_assess_single_route()`
- Determine `same_route` by comparing decoded coordinate lists (or by checking if distance difference is <1%)
- If the safest route request fails, fall back to the shortest route for both
- If the shortest route request fails, fall back to the safest route for both
- If both fail, return `error_type: "no_route"`
- If the driving route request fails, pass `driving_distance_m=None` (existing half-points fallback)

Valhalla error handling:
- Valhalla errors return HTTP 400 with `{"error_code": N, "error": "message", "status_code": 400, "status": "Bad Request"}`
- Error code 171 = "No path could be found for input" (equivalent to OSRM's "NoRoute")
- Any non-200 response or missing `trip` key is treated as route failure

**Requirements References**
- [valhalla-routing-engine:FR-002]: Bicycle route via Valhalla replaces OSRM
- [valhalla-routing-engine:FR-003]: Two separate requests with different costing options
- [valhalla-routing-engine:FR-004]: Third request for driving distance
- [valhalla-routing-engine:FR-006]: use_hills parameter in safest route costing
- [valhalla-routing-engine:FR-007]: VALHALLA_URL env var configuration

**Test Scenarios**

**TS-01: Bicycle route via Valhalla returns assessment**
- Given: Mock Valhalla returning valid bicycle route responses and mock Overpass returning infrastructure data
- When: `_assess_cycle_route()` called
- Then: Result contains `shortest_route` and `safest_route` with distance, duration, score, and geometry

**TS-02: Valhalla called with correct costing for shortest route**
- Given: Mock transport capturing request bodies
- When: `_assess_cycle_route()` called
- Then: One request has `costing: "bicycle"` with `costing_options.bicycle.shortest: true`

**TS-03: Valhalla called with correct costing for safest route**
- Given: Mock transport capturing request bodies
- When: `_assess_cycle_route()` called
- Then: One request has `costing: "bicycle"` with `costing_options.bicycle.use_roads: 0.1`

**TS-04: Driving distance request uses auto costing**
- Given: Mock transport capturing request bodies
- When: `_assess_cycle_route()` called
- Then: One request has `costing: "auto"`

**TS-05: No route from Valhalla returns error**
- Given: Valhalla returns HTTP 400 with error_code 171
- When: `_assess_cycle_route()` called
- Then: Result has `status: "error"` and `error_type: "no_route"`

**TS-06: Safest route failure falls back to shortest**
- Given: Shortest route request succeeds, safest route request fails (HTTP 400)
- When: `_assess_cycle_route()` called
- Then: `shortest_route` and `safest_route` contain the same data, `same_route` is true

**TS-07: Driving route failure falls back to half-points**
- Given: Both bicycle routes succeed, driving route request fails
- When: `_assess_cycle_route()` called
- Then: Score breakdown `directness` equals 4.5 (half of MAX_DIRECTNESS_POINTS)

**TS-08: Driving distance passed to scorer**
- Given: Driving route returns 2000m distance, shortest bicycle route is 2200m (1.1 ratio)
- When: `_assess_cycle_route()` called
- Then: Score breakdown `directness` equals 9.0 (full points for <=1.1 ratio)

---

### _assess_single_route (`src/mcp_servers/cycle_route/server.py`)

**Change Description** Currently accepts a dict with `distance`, `duration`, `geometry.coordinates`. Must also accept a `driving_distance_m` parameter to pass through to `score_route()`.

**Dependants** None — internal method called only by `_assess_cycle_route`.

**Kind** Method

**Details**

Add `driving_distance_m: float | None = None` parameter. Pass it through to `score_route()`:

```
score_route(segments, cycling_distance_m, driving_distance_m=driving_distance_m, transitions=transitions)
```

The route dict input shape changes from OSRM format (`route["geometry"]["coordinates"]`) to pre-decoded coordinates (a list of `[lon, lat]` pairs) and explicit distance/duration values, since decoding happens in `_assess_cycle_route` before calling this method.

**Requirements References**
- [valhalla-routing-engine:FR-004]: Driving distance passed through to scorer

**Test Scenarios**

Covered by TS-08 (driving distance integration tested end-to-end through `_assess_cycle_route`).

---

### Test Helpers (`tests/test_mcp_servers/test_cycle_route/test_server.py`)

**Change Description** The `_make_osrm_route()`, `_make_osrm_response()` helpers and all URL pattern matching (`router.project-osrm.org`) must be replaced with Valhalla equivalents.

**Dependants** All `TestAssessCycleRoute` tests.

**Kind** Module

**Details**

Replace helpers:
- `_make_osrm_route()` → remove
- `_make_osrm_response()` → remove
- Add `_make_valhalla_response(distance_km, duration_s, shape)` — returns `{"trip": {"legs": [{"shape": "..."}], "summary": {"length": distance_km, "time": duration_s}}}`
- Add `_make_valhalla_error(error_code, message)` — returns error response dict
- Add `_encode_polyline(coords)` — test helper that encodes `[lon, lat]` pairs to 6-digit precision polyline string

Mock transport URL patterns change from `"router.project-osrm.org"` to `"valhalla"` (matching the default `http://valhalla:8002` URL).

The mock transport handler must now inspect the POST body to differentiate between shortest bicycle, safest bicycle, and auto requests (all go to the same `/route` endpoint). Differentiate by checking `costing` and `costing_options.bicycle.shortest` in the JSON body.

**Requirements References**
- [valhalla-routing-engine:FR-002]: Tests must use Valhalla response format
- [valhalla-routing-engine:FR-003]: Tests must verify different costing options
- [valhalla-routing-engine:FR-005]: Tests must use encoded polyline format

**Test Scenarios**

Test scenario coverage is defined on the CycleRouteMCP component (TS-01 through TS-08). The test helpers exist to support those scenarios.

---

## Added Components

### decode_polyline (`src/mcp_servers/cycle_route/polyline.py`)

**Description** Pure function that decodes a Valhalla encoded polyline string (6-digit precision) into a list of `[lon, lat]` coordinate pairs. Valhalla uses the Google encoded polyline algorithm but with 10^6 precision instead of 10^5.

**Users** `CycleRouteMCP._assess_cycle_route()` — decodes route shapes from Valhalla responses.

**Kind** Module with single function

**Location** `src/mcp_servers/cycle_route/polyline.py`

**Details**

The function accepts an encoded string and a precision parameter (default 6). It returns a list of `[lon, lat]` pairs (GeoJSON order, matching the existing coordinate convention used by the Overpass query builder).

The algorithm:
1. Iterate through the encoded string
2. For each coordinate component, consume 5-bit chunks until the continuation bit is clear
3. Apply precision factor (10^6 for Valhalla, 10^5 for Google standard)
4. Accumulate deltas to produce absolute lat/lon values
5. Return as `[lon, lat]` pairs (swap from the encoded lat-first order)

Empty or None input returns an empty list.

**Requirements References**
- [valhalla-routing-engine:FR-005]: Decode Valhalla encoded polylines to coordinate pairs

**Test Scenarios**

**TS-09: Known encoded string decodes correctly**
- Given: An encoded polyline string with known coordinates
- When: `decode_polyline()` called
- Then: Returns expected `[lon, lat]` pairs within 0.000001 tolerance

**TS-10: Empty string returns empty list**
- Given: Empty string input
- When: `decode_polyline()` called
- Then: Returns `[]`

**TS-11: Round-trip encode/decode preserves coordinates**
- Given: A list of `[lon, lat]` pairs
- When: Encoded then decoded (using test helper encoder)
- Then: Decoded coordinates match originals within precision tolerance

---

### Valhalla Service (Docker Compose)

**Description** A Valhalla routing container added to both dev and production Docker Compose files. Uses the official `ghcr.io/valhalla/valhalla-scripted` image with the Oxfordshire PBF extract and SRTM elevation data.

**Users** `cycle-route-mcp` service connects to it at `http://valhalla:8002` over the Docker network.

**Kind** Docker Compose service

**Location** `docker-compose.yml` (dev) and `deploy/docker-compose.yml` (production)

**Details**

Dev compose service:
- Image: `ghcr.io/valhalla/valhalla-scripted:latest`
- Volume: `./data/valhalla:/custom_files` — stores downloaded PBF, built tiles, elevation data
- Environment: `tile_urls=https://download.geofabrik.de/europe/united-kingdom/england/oxfordshire-latest.osm.pbf`, `build_elevation=True`, `server_threads=2`
- Port: internal only (no host port mapping needed, accessed via Docker network)
- Network: `agent-net`
- Memory limit: 1GB
- Health check: `curl -f http://localhost:8002/status` (Valhalla's built-in status endpoint)

Production compose service:
- Same image, same env/volume pattern
- Volume on external drive: `${DATA_DIR:-./data}/valhalla:/custom_files`
- Memory limit: 1GB
- No Traefik labels (internal service only, not exposed to internet)

The cycle-route-mcp service adds `VALHALLA_URL=http://valhalla:8002` to its environment and optionally `depends_on: valhalla`.

**Requirements References**
- [valhalla-routing-engine:FR-001]: Self-hosted Valhalla container
- [valhalla-routing-engine:FR-006]: Elevation data via `build_elevation=True`
- [valhalla-routing-engine:NFR-002]: Memory limit 1GB

**Test Scenarios**

No unit test scenarios — this is infrastructure configuration. Validated by QA-01 (end-to-end review with Valhalla).

---

## Used Components

### Overpass Infrastructure Analyser (`src/mcp_servers/cycle_route/infrastructure.py`)

**Location** `src/mcp_servers/cycle_route/infrastructure.py`

**Provides** `build_overpass_query(coordinates)`, `parse_overpass_ways()`, `detect_parallel_provision()`, `analyse_transitions()`, `summarise_provision()`, `segments_to_feature_collection()` — all infrastructure analysis from OSM way tags along a route corridor.

**Used By** `CycleRouteMCP._assess_single_route()` — unchanged. The decoded Valhalla coordinates feed into `build_overpass_query()` in exactly the same format as OSRM coordinates did.

### LTN 1/20 Scorer (`src/mcp_servers/cycle_route/scoring.py`)

**Location** `src/mcp_servers/cycle_route/scoring.py`

**Provides** `score_route(segments, cycling_distance_m, driving_distance_m, transitions)` — calculates 0-100 LTN 1/20 score. The `driving_distance_m` parameter already exists but has always been `None`. Now receives actual values from the Valhalla auto route.

**Used By** `CycleRouteMCP._assess_single_route()` — the only change is that `driving_distance_m` may now be a real value instead of `None`.

### Issue Identification (`src/mcp_servers/cycle_route/issues.py`)

**Location** `src/mcp_servers/cycle_route/issues.py`

**Provides** `identify_issues(segments)`, `generate_s106_suggestions(issues)` — unchanged.

**Used By** `CycleRouteMCP._assess_single_route()` — no changes needed.

---

## Documentation Considerations

- Update `docs/mcp-cycle-route.md` if it references OSRM (describe the Valhalla routing engine)
- Update `LOCAL_DEV.md` to note the Valhalla container and first-run tile build time

---

## Integration Test Scenarios

**ITS-01: Full route assessment with Valhalla mock**
- Given: Mock Valhalla returning two different bicycle routes (shortest 2200m, safest 2800m with different geometry) and a driving route (2000m), mock Overpass returning cycleway data for safest and road data for shortest
- When: `_assess_cycle_route()` called
- Then: `shortest_route.distance_m` is 2200, `safest_route.distance_m` is 2800, `same_route` is false, `safest_route.score.score` > `shortest_route.score.score`, directness score uses driving distance
- Components Involved: CycleRouteMCP, decode_polyline, scoring, infrastructure

---

## Test Data

- Encoded polyline test vectors: manually encode known Bicester-area coordinates using the 6-digit precision algorithm
- Valhalla response fixtures: JSON structures matching the Valhalla `/route` response format with `trip.legs[].shape` and `trip.summary`
- Reuse existing Overpass fixtures (`_make_overpass_response`) — unchanged

---

## Test Feasibility

- All tests use httpx MockTransport as today — no external API calls needed
- The polyline encode/decode tests are pure functions with no dependencies
- The Valhalla container itself is validated only in QA (integration testing with real container is manual)

---

## QA Feasibility

**QA-01 (Route assessment completes with Valhalla)**: Requires full Docker Compose stack running including the Valhalla container with built tiles. First-run tile build takes ~5 minutes. All steps are performable.

**QA-02 (Shortest vs safest routes differ)**: Read cycle-route-mcp logs. Valhalla's different costing options should produce different routes for destinations where both road and cycleway paths exist. Some flat, simple destinations may return the same route — `same_route: true` is acceptable.

**QA-03 (Directness score uses actual driving distance)**: Check score breakdown in the route assessment response. The `directness` field should no longer be 4.5 for all routes.

**QA-04 (Elevation affects route selection)**: Check Valhalla container startup logs for SRTM data loading. Duration estimates implicitly include gradient — no separate elevation output is exposed in the current response format.

---

## Risks and Dependencies

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Oxfordshire extract boundary truncates routes to distant destinations | Medium | Low | All configured destinations (Bicester North, town centre, bus stops) are well within the extract. Edge case for very distant destinations results in "no route" error, which is already handled. |
| Valhalla container tile build fails on production server | Low | Medium | Tile build happens once on first container start. If it fails, check disk space and PBF URL. Manual rebuild: `docker compose restart valhalla`. |
| Encoded polyline decode produces subtly wrong coordinates | Low | High | Decode function is well-specified (Google polyline algorithm with precision 6). Verified with round-trip tests and known coordinate pairs. |
| Three API calls per destination increases assessment time | Medium | Low | Valhalla responds in ~5-20ms per call (local Docker network). Three calls add ~60ms total vs one OSRM call that took ~200-500ms over the internet. Net faster. |
| Geofabrik Oxfordshire PBF download unavailable at first build | Low | Low | PBF can be pre-downloaded and placed in the volume directory. Container checks for existing tiles before downloading. |

---

## Feasibility Review

No blocking dependencies. All components are available:
- Valhalla Docker image is public and actively maintained
- Oxfordshire PBF is freely available from Geofabrik
- SRTM elevation data is automatically downloaded by the Valhalla build process
- The polyline decoding algorithm is well-documented and simple to implement

---

## Task Breakdown

### Phase 1: Polyline decoder and Valhalla response handling

**Task 1: Add polyline decoder module**
- Status: Backlog
- Requirements: [valhalla-routing-engine:FR-005]
- Test Scenarios: [valhalla-routing-engine:decode_polyline/TS-09], [valhalla-routing-engine:decode_polyline/TS-10], [valhalla-routing-engine:decode_polyline/TS-11]
- Details: Create `src/mcp_servers/cycle_route/polyline.py` with `decode_polyline(encoded, precision=6)` function. Create `tests/test_mcp_servers/test_cycle_route/test_polyline.py` with decode tests. TDD: write tests first using known encoded strings.

**Task 2: Replace OSRM with Valhalla in CycleRouteMCP**
- Status: Backlog
- Requirements: [valhalla-routing-engine:FR-002], [valhalla-routing-engine:FR-003], [valhalla-routing-engine:FR-004], [valhalla-routing-engine:FR-005], [valhalla-routing-engine:FR-006], [valhalla-routing-engine:FR-007]
- Test Scenarios: [valhalla-routing-engine:CycleRouteMCP/TS-01], [valhalla-routing-engine:CycleRouteMCP/TS-02], [valhalla-routing-engine:CycleRouteMCP/TS-03], [valhalla-routing-engine:CycleRouteMCP/TS-04], [valhalla-routing-engine:CycleRouteMCP/TS-05], [valhalla-routing-engine:CycleRouteMCP/TS-06], [valhalla-routing-engine:CycleRouteMCP/TS-07], [valhalla-routing-engine:CycleRouteMCP/TS-08], [valhalla-routing-engine:ITS-01]
- Details:
  - Replace `osrm_url` with `valhalla_url` in constructor and constants
  - Rewrite `_assess_cycle_route()` to make 3 Valhalla POST calls (shortest bicycle, safest bicycle, auto)
  - Update `_assess_single_route()` signature to accept `driving_distance_m` and pass to `score_route()`
  - Handle Valhalla error format (HTTP 400 with error_code)
  - Decode polyline shape to `[lon, lat]` coordinates using the new decoder
  - Implement fallback logic: safest fails → use shortest for both; driving fails → None
  - Update all test helpers: replace `_make_osrm_*` with `_make_valhalla_*`, add `_encode_polyline()` test helper
  - Update all test assertions for Valhalla URL patterns and POST body inspection

### Phase 2: Docker Compose and configuration

**Task 3: Add Valhalla container to Docker Compose files**
- Status: Backlog
- Requirements: [valhalla-routing-engine:FR-001], [valhalla-routing-engine:FR-006], [valhalla-routing-engine:NFR-002]
- Test Scenarios: Validated by QA-01
- Details:
  - Add `valhalla` service to `docker-compose.yml` (dev) with `ghcr.io/valhalla/valhalla-scripted:latest`, volume mount, environment variables, health check, agent-net network, 1GB memory limit
  - Add `valhalla` service to `deploy/docker-compose.yml` (production) with same config, external drive volume
  - Add `VALHALLA_URL=http://valhalla:8002` to cycle-route-mcp environment in both compose files
  - Remove `OSRM_URL` from any environment configuration
  - Create `data/valhalla/` directory in `.gitignore` if not already excluded

---

## Intermediate Dead Code Tracking

None — no intermediate dead code expected. OSRM code is fully replaced in Task 2.

---

## Intermediate Stub Tracking

None — no stubs expected.

---

## Appendix

### Glossary
- **Encoded polyline**: Compact string encoding of a sequence of lat/lon coordinates. Valhalla uses Google's algorithm with 10^6 precision (6 decimal places).
- **Costing options**: JSON object sent with Valhalla route requests that controls the routing behaviour (e.g., `use_roads`, `use_hills`, `shortest`).
- **SRTM**: Shuttle Radar Topography Mission — global elevation dataset at ~30m resolution.

### Valhalla Response Reference

Successful route response:
```json
{
  "trip": {
    "legs": [{
      "shape": "_p~iF~ps|U_ulLnnqC...",
      "summary": {"length": 2.5, "time": 600.0}
    }],
    "summary": {"length": 2.5, "time": 600.0},
    "units": "kilometers",
    "status": 0
  }
}
```

Error response (HTTP 400):
```json
{
  "error_code": 171,
  "error": "No path could be found for input",
  "status_code": 400,
  "status": "Bad Request"
}
```

### References
- [Valhalla API reference](https://valhalla.github.io/valhalla/api/turn-by-turn/api-reference/)
- [Valhalla Docker setup](https://github.com/valhalla/valhalla/blob/master/docker/README.md)
- [Google polyline encoding](https://developers.google.com/maps/documentation/utilities/polylinealgorithm)
- [Routing engine spike report](../routing-engine-spike/spike-report.md)

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-20 | Claude | Initial design |
