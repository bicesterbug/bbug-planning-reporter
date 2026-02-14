# Specification: Cycle Route MCP Server API

**Version:** 1.0
**Date:** 2026-02-14
**Status:** As-Built

---

## Problem Statement

The Cycle Route MCP server exists but its tool contracts, scoring methodology, and external API integration details are not documented as-built. The original design specification (`.sdd/cycle-route-assessment/specification.md`) describes the planned system across the full review pipeline. This document captures the as-built MCP server tool contracts specifically -- the two MCP tools (`get_site_boundary`, `assess_cycle_route`), the LTN 1/20 scoring algorithm, the ESRI-to-GeoJSON conversion, infrastructure classification from Overpass, issue detection, and S106 suggestion generation -- as they exist in the implemented code on port 3004.

## Beneficiaries

**Primary:**
- Developers integrating with the Cycle Route MCP server via Claude Desktop, Claude Code, n8n, or the internal worker
- Maintainers who need to understand the exact tool input/output contracts and scoring weights

**Secondary:**
- BBUG reviewers who need to interpret the route assessment scores, issue severities, and S106 suggestions in review outputs
- External MCP clients that need to construct valid tool calls and parse responses

---

## Outcomes

**Must Haves**
- The `get_site_boundary` tool accepts a planning application reference and returns a GeoJSON FeatureCollection with the site polygon and centroid point
- The `assess_cycle_route` tool accepts origin/destination coordinates and returns distance, duration, provision breakdown, segments, LTN 1/20 score, issues, S106 suggestions, and route geometry
- LTN 1/20 scoring produces a 0-100 score with green/amber/red rating and a breakdown of five weighted factors
- ESRI JSON polygon responses from ArcGIS are converted to RFC 7946 GeoJSON
- Route infrastructure is classified from Overpass API OSM way tags into five provision types
- Issues are detected based on speed limits, provision gaps, surface quality, and lighting
- S106 suggestions are generated from high and medium severity issues with policy justification
- The server runs on port 3004 with dual SSE + Streamable HTTP transport and bearer token authentication via MCP_API_KEY

**Nice-to-haves**
- None (this documents what is built)

---

## Explicitly Out of Scope

- REST API endpoints for destinations, reviews, or site boundaries (those are in the API service, not this MCP server)
- Destination management (adding, listing, or selecting destinations -- handled by the REST API layer)
- Document analysis or policy lookup (handled by document-store-mcp and policy-kb-mcp respectively)
- Integration with the review pipeline (handled by the worker service)
- Route visualisation or map rendering (the server returns GeoJSON coordinates only)
- OAuth 2.1 authentication (bearer token only)

---

## Functional Requirements

### [cycle-route-api:FR-001] get_site_boundary Tool

**Description:** The `get_site_boundary` MCP tool looks up the site boundary polygon for a planning application from Cherwell's ArcGIS planning register. It queries the ESRI ArcGIS MapServer REST API using the application reference number, requests output in WGS84 (outSR=4326), and converts the ESRI JSON polygon response to a GeoJSON FeatureCollection containing both the site polygon and a centroid point.

**Input Schema:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `application_ref` | string | Yes | Planning application reference (e.g., `"25/01178/REM"`, `"21/03267/OUT"`) |

**Output Schema:**
| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"success"` or `"error"` |
| `error_type` | string | Present on error: `"not_found"` or `"internal_error"` |
| `message` | string | Present on error: human-readable error description |
| `geojson` | object | Present on success: GeoJSON FeatureCollection (see [cycle-route-api:FR-005]) |

**ArcGIS Query Parameters:**
| Parameter | Value |
|-----------|-------|
| `f` | `json` |
| `returnGeometry` | `true` |
| `outSR` | `4326` |
| `outFields` | `*` |
| `where` | `DLGSDST.dbo.Planning_ArcGIS_Link_Public.application_number='<ref>'` |

**ArcGIS Endpoint:** `https://utility.arcgis.com/usrsvcs/servers/3b969cb8886849d993863e4c913c82fc/rest/services/Public_Map_Services/Cherwell_Public_Planning_Register/MapServer/0/query`

**Examples:**
- Positive case: Application `25/01178/REM` found in register -- returns `{"status": "success", "geojson": { ... FeatureCollection ... }}`
- Negative case: Application not found in ArcGIS register -- returns `{"status": "error", "error_type": "not_found", "message": "Application 25/99999/OUT not found in planning register"}`
- Edge case: ArcGIS returns features but no geometry rings -- returns `None` (treated as not found)

### [cycle-route-api:FR-002] assess_cycle_route Tool

**Description:** The `assess_cycle_route` MCP tool assesses the cycling route quality between two geographic points. It calculates the route via OSRM, queries Overpass API for OSM infrastructure data along the route, classifies each segment's cycling provision, scores the route against LTN 1/20, identifies infrastructure issues, and generates S106 funding suggestions.

**Input Schema:**
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `origin_lon` | float | Yes | -- | Origin longitude (WGS84) |
| `origin_lat` | float | Yes | -- | Origin latitude (WGS84) |
| `destination_lon` | float | Yes | -- | Destination longitude (WGS84) |
| `destination_lat` | float | Yes | -- | Destination latitude (WGS84) |
| `destination_name` | string | No | `"Destination"` | Human-readable destination name |

**Output Schema (success):**
| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"success"` |
| `destination` | string | The destination name |
| `distance_m` | int | Total cycling route distance in metres (rounded) |
| `duration_minutes` | float | Estimated cycling duration in minutes (1 decimal place) |
| `provision_breakdown` | object | Map of provision type to total distance in metres (see [cycle-route-api:FR-006]) |
| `segments` | array | Array of route segment objects (see below) |
| `score` | object | LTN 1/20 score object (see [cycle-route-api:FR-003]) |
| `issues` | array | Array of issue objects (see [cycle-route-api:FR-007]) |
| `s106_suggestions` | array | Array of S106 suggestion objects (see [cycle-route-api:FR-008]) |
| `route_geometry` | array | Array of `[lon, lat]` coordinate pairs from the OSRM route |

**Segment Object:**
| Field | Type | Description |
|-------|------|-------------|
| `way_id` | int | OSM way ID |
| `provision` | string | One of: `"segregated"`, `"shared_use"`, `"on_road_lane"`, `"advisory_lane"`, `"none"` |
| `highway` | string | OSM highway classification (e.g., `"cycleway"`, `"residential"`, `"primary"`) |
| `speed_limit` | int | Speed limit in mph (0 for off-road paths) |
| `surface` | string | Surface type (e.g., `"asphalt"`, `"gravel"`, `"unknown"`) |
| `lit` | bool or null | Whether the segment is lit (`true`, `false`, or `null` if unknown) |
| `distance_m` | float | Approximate segment distance in metres (1 decimal place) |
| `name` | string | Road/path name or `"Unnamed"` |

**Output Schema (no route):**
```json
{
  "status": "error",
  "error_type": "no_route",
  "message": "No cycling route found to <destination_name>"
}
```

**Output Schema (no infrastructure data):**
When OSRM returns a route but Overpass returns no way data, the tool returns a success response with empty provision, zero score (red), and a note:
```json
{
  "status": "success",
  "destination": "...",
  "distance_m": 1234,
  "duration_minutes": 5.2,
  "provision_breakdown": {},
  "score": {"score": 0, "rating": "red", "breakdown": {}},
  "issues": [],
  "s106_suggestions": [],
  "note": "No infrastructure data available along route"
}
```

**Examples:**
- Positive case: Route from site centroid to Bicester North station -- returns full assessment with segments, score, issues, and S106 suggestions
- Negative case: OSRM returns code != "Ok" or empty routes array -- returns `{"status": "error", "error_type": "no_route", ...}`
- Edge case: Overpass returns no ways along the route corridor -- returns success with zero score and note

### [cycle-route-api:FR-003] LTN 1/20 Scoring

**Description:** The route scoring algorithm produces a 0-100 cycling quality score based on LTN 1/20 (Cycle Infrastructure Design) principles. The score is composed of five weighted factors. The total score is clamped to the range 0-100 and mapped to a Red/Amber/Green rating.

**Scoring Factors:**

| Factor | Max Points | Weight | Description |
|--------|-----------|--------|-------------|
| Segregation | 40 | 40% | Proportion of route on segregated infrastructure. Segregated = 100% credit, shared-use = 70% credit, on-road lane = 40% credit. |
| Speed Safety | 25 | 25% | Traffic speed on unsegregated sections. <=20mph: full points; 21-30mph: 60%; 31-40mph: 20%; >40mph: 0%. No unsegregated sections = full points. |
| Surface Quality | 15 | 15% | Surface type across route. Good (asphalt, paved, concrete, concrete:plates, paving_stones): 100%; Fair (compacted, fine_gravel): 60%; Unknown: 50%; Poor (all others): 20%. |
| Directness | 10 | 10% | Ratio of cycling distance to driving distance. <=1.1x: 100%; <=1.3x: 70%; <=1.5x: 40%; >1.5x: 10%. Half points when no driving distance available. |
| Junction Safety | 10 | 10% | Hostile junction penalty. Counts segments on primary/secondary/trunk/tertiary roads with no provision and speed >=30mph. 0 hostile: 100%; 1-2: 60%; 3-5: 30%; >5: 0%. |

**RAG Rating Thresholds:**
| Score Range | Rating |
|------------|--------|
| >= 70 | `"green"` |
| >= 40 | `"amber"` |
| < 40 | `"red"` |

**Score Output Schema:**
| Field | Type | Description |
|-------|------|-------------|
| `score` | int | Total score 0-100 |
| `rating` | string | `"green"`, `"amber"`, or `"red"` |
| `breakdown` | object | Per-factor scores: `segregation`, `speed_safety`, `surface_quality`, `directness`, `junction_safety` |
| `max_points` | object | Maximum possible points per factor (for context) |
| `short_route_note` | string | Present when route < 200m: `"Short distance; walking may be preferable"` |

**Examples:**
- Positive case: Fully segregated cycle path route with good surfaces scores 85+ (green)
- Positive case: Mixed route on 30mph residential roads with advisory lanes scores 40-60 (amber)
- Positive case: Route on 50mph A-road with no provision scores below 30 (red)
- Edge case: Route under 200m -- scored normally but includes `short_route_note`
- Edge case: No driving distance comparison available -- directness score defaults to half points (5/10)

### [cycle-route-api:FR-004] OSRM Route Planning

**Description:** The server uses OSRM (Open Source Routing Machine) public API for cycling route calculation. It requests the cycling route between origin and destination coordinates with full geometry in GeoJSON format. The default OSRM endpoint is `https://router.project-osrm.org/route/v1/bike` and is configurable via the `OSRM_URL` environment variable.

**OSRM Request Parameters:**
| Parameter | Value | Description |
|-----------|-------|-------------|
| `overview` | `full` | Return complete route geometry |
| `geometries` | `geojson` | Return geometry as GeoJSON |
| `steps` | `false` | Do not return turn-by-turn steps |

**OSRM URL Format:** `{OSRM_URL}/{origin_lon},{origin_lat};{dest_lon},{dest_lat}`

**Extracted Fields:**
| Field | Source | Description |
|-------|--------|-------------|
| `distance` | `routes[0].distance` | Route distance in metres |
| `duration` | `routes[0].duration` | Route duration in seconds |
| `coordinates` | `routes[0].geometry.coordinates` | Array of `[lon, lat]` pairs |

**Examples:**
- Positive case: OSRM returns `code: "Ok"` with one or more routes -- first route is used
- Negative case: OSRM returns `code != "Ok"` or empty routes array -- error returned to caller

### [cycle-route-api:FR-005] ESRI to GeoJSON Conversion

**Description:** ESRI JSON polygon geometry from the ArcGIS response is converted to an RFC 7946 GeoJSON FeatureCollection. The FeatureCollection contains two features: the site boundary polygon and a centroid point derived from the exterior ring. The centroid is calculated as the arithmetic mean of the exterior ring vertices (excluding the closing point for closed rings).

**GeoJSON FeatureCollection Structure:**

Feature 1 -- Site Boundary Polygon:
| Property | Type | Description |
|----------|------|-------------|
| `application_ref` | string | Planning application reference |
| `address` | string | Site address (newlines replaced with commas) |
| `area_sqm` | float or null | Site area from ESRI `SHAPE.STArea()` |
| `feature_type` | string | `"site_boundary"` |

Feature 2 -- Centroid Point:
| Property | Type | Description |
|----------|------|-------------|
| `application_ref` | string | Planning application reference |
| `address` | string | Site address |
| `area_sqm` | float or null | Site area |
| `feature_type` | string | `"centroid"` |
| `centroid_note` | string | Accuracy caveat (varies by site size) |

**Centroid Note Values:**
- Standard sites: `"Geometric centre of site boundary; actual entrance may differ"`
- Large sites (>100,000 sqm): `"Approximate origin; actual access point may vary significantly for this large site"`

**Attribute Extraction:** The converter tries three key prefixes when looking up attributes from the ESRI response:
1. `DLGSDST.dbo.Planning_ArcGIS_Link_Public.<field>`
2. `CORPGIS.MASTERGOV.DEF_Planning.<field>`
3. `<field>` (direct/short name)

**Examples:**
- Positive case: Single polygon with 19-point exterior ring -- converts to FeatureCollection with polygon and centroid
- Positive case: Multi-ring polygon (exterior + holes) -- all rings passed through to GeoJSON Polygon coordinates
- Edge case: Empty rings array -- returns `None` (no features to convert)

### [cycle-route-api:FR-006] Infrastructure Classification

**Description:** Route segments are classified by cycling provision type based on OSM way tags returned from the Overpass API. The classification determines the `provision` field for each `RouteSegment` and feeds into the scoring and issue detection.

**Provision Types:**

| Provision | Tag Conditions |
|-----------|---------------|
| `segregated` | `highway=cycleway`; OR path/footway/bridleway with `bicycle=designated` and NOT `foot=designated`/`yes`; OR any road with `cycleway`/`cycleway:left`/`cycleway:right`/`cycleway:both` = `track` or `separate` |
| `shared_use` | Path/footway/bridleway with both `bicycle` and `foot` = `designated`/`yes`; OR path with `bicycle=yes`; OR designation containing "shared" |
| `on_road_lane` | Any road with `cycleway`/`cycleway:left`/`cycleway:right`/`cycleway:both` = `lane` |
| `advisory_lane` | Any road with `cycleway`/`cycleway:left`/`cycleway:right`/`cycleway:both` = `shared_lane` or `share_busway` |
| `none` | No cycle-specific tagging detected |

**Speed Limit Extraction:** Parsed from `maxspeed` tag (e.g., `"30 mph"` -> 30). Falls back to UK defaults by highway classification:

| Highway Type | Default Speed (mph) |
|-------------|---------------------|
| motorway | 70 |
| trunk, primary, secondary | 60 |
| tertiary, unclassified, residential | 30 |
| living_street, service | 20 |
| cycleway, path, footway, bridleway, track, pedestrian | 0 |

**Surface Extraction:** Taken directly from the `surface` OSM tag, defaulting to `"unknown"`.

**Lighting Extraction:** `lit=yes` -> `true`, `lit=no` -> `false`, otherwise `null`.

**Overpass Query Construction:** The route geometry coordinates are sampled (every Nth point to produce ~50 samples, always including the last point) and used to build an `around` query with a 20m buffer. Non-routable highway types (`proposed`, `construction`, `abandoned`, `razed`, `platform`) are filtered out.

**Provision Breakdown:** The `summarise_provision` function aggregates segment distances by provision type, returning a dict mapping provision type to total distance in metres (rounded to 1 decimal place).

**Examples:**
- Positive case: Way tagged `highway=cycleway` -- classified as `segregated`
- Positive case: Way tagged `highway=footway`, `bicycle=designated`, `foot=designated` -- classified as `shared_use`
- Positive case: Way tagged `highway=primary`, `cycleway:right=lane` -- classified as `on_road_lane`
- Positive case: Way tagged `highway=residential` with no cycleway tags -- classified as `none`
- Edge case: Way has no `surface` tag -- defaults to `"unknown"`, scored at 50% in surface quality factor

### [cycle-route-api:FR-007] Issue Detection

**Description:** The issue detector examines each route segment and identifies cycling infrastructure deficiencies. Each issue includes a location description, the specific problem, a severity level, and a suggested improvement citing LTN 1/20 guidance.

**Issue Types:**

| Condition | Severity | Problem | Suggested Improvement |
|-----------|----------|---------|----------------------|
| `provision=none` AND `speed_limit >= 40` | `high` | High-speed road with no cycle provision | Segregated cycleway required (LTN 1/20 Table 4-1) |
| `provision=none` AND `speed_limit >= 30` AND `highway` in (primary, secondary, tertiary, trunk) | `medium` | Classified road at 30mph with no cycle provision | On-road cycle lane or segregated cycleway (LTN 1/20) |
| `surface` in (gravel, dirt, grass, mud, sand, ground) | `medium` | Poor surface quality | Resurface to sealed tarmac/asphalt (LTN 1/20 para 5.5) |
| `lit=false` AND `provision` in (segregated, shared_use) | `low` | Unlit segregated/shared-use path | Install lighting (LTN 1/20 para 10.5) |

**Issue Object Schema:**
| Field | Type | Description |
|-------|------|-------------|
| `location` | string | Road/path name with distance (e.g., `"Buckingham Road (300m section)"`) |
| `problem` | string | Human-readable description of the deficiency |
| `severity` | string | `"high"`, `"medium"`, or `"low"` |
| `suggested_improvement` | string | Recommended fix with LTN 1/20 reference |

**Examples:**
- Positive case: 40mph A-road with no cycle provision -- high severity issue with segregated cycleway suggestion
- Positive case: Gravel surface on shared-use path -- medium severity issue with resurfacing suggestion
- Positive case: Unlit segregated cycle path -- low severity issue with lighting suggestion
- Edge case: Route has no issues -- returns empty issues array

### [cycle-route-api:FR-008] S106 Suggestions

**Description:** S106 developer contribution suggestions are generated from identified route issues. Only high and medium severity issues generate suggestions. Each suggestion references the specific issue location, the recommended improvement, and a policy justification citing Cherwell Local Plan Policy INF1 and NPPF paragraph 116.

**S106 Suggestion Object Schema:**
| Field | Type | Description |
|-------|------|-------------|
| `issue_location` | string | Location from the source issue |
| `improvement` | string | Suggested improvement from the source issue |
| `justification` | string | Policy justification citing the deficiency, Cherwell LP INF1, and NPPF para 116 |
| `severity` | string | Severity from the source issue (`"high"` or `"medium"`) |

**Justification Template:** `"Addresses identified deficiency: <problem>. S106 contribution towards off-site cycling infrastructure improvements is justified under Cherwell Local Plan Policy INF1 and NPPF paragraph 116."`

**Examples:**
- Positive case: High-severity issue on 40mph road -- generates S106 suggestion with INF1/NPPF justification
- Positive case: Medium-severity poor surface issue -- generates S106 suggestion
- Edge case: Low-severity unlit path issue -- no S106 suggestion generated (low severity excluded)
- Edge case: No issues identified -- empty S106 suggestions array

### [cycle-route-api:FR-009] Transport Protocol

**Description:** The MCP server exposes four HTTP endpoints via a Starlette application with dual transport support. The SSE transport provides backward compatibility with the internal worker; Streamable HTTP is the current MCP standard for external clients.

**Endpoints:**
| Path | Method | Description |
|------|--------|-------------|
| `/health` | GET | Health check, returns `{"status": "ok"}`. Always unauthenticated. |
| `/sse` | GET | SSE transport endpoint (legacy). Initiates SSE connection. |
| `/messages/` | POST | SSE message posting endpoint (used with `/sse`). |
| `/mcp` | GET, POST, DELETE | Streamable HTTP transport endpoint (current MCP standard). |

**Server Configuration:**
| Setting | Value | Source |
|---------|-------|--------|
| Port | 3004 | `CYCLE_ROUTE_PORT` env var, default `3004` |
| Host | `0.0.0.0` | Hardcoded |
| HTTP timeout | 20 seconds | `httpx.AsyncClient` timeout for outbound API calls |
| User-Agent | `BBUGCycleRouteAssessment/1.0 (cycling-advocacy-tool)` | Sent on all external API calls |
| Rate limit delay | 0.5 seconds | `asyncio.sleep` between consecutive external API calls |

### [cycle-route-api:FR-010] Authentication

**Description:** Bearer token authentication is enforced via the `MCPAuthMiddleware` Starlette middleware. When `MCP_API_KEY` is set, all requests to `/sse`, `/messages`, and `/mcp` must include a valid `Authorization: Bearer <token>` header. The `/health` endpoint is always exempt. When `MCP_API_KEY` is not set or empty, authentication is disabled (no-op middleware for backward compatibility).

**Authentication Flow:**
1. If `MCP_API_KEY` is not set or empty -- all requests pass through (no auth)
2. If request path is `/health` -- pass through (exempt)
3. If no `Authorization` header -- return 401 with `{"error": {"code": "unauthorized", "message": "Missing Authorization header"}}`
4. If header is not `Bearer <token>` format -- return 401 with `{"error": {"code": "unauthorized", "message": "Invalid Authorization header format. Expected: Bearer <token>"}}`
5. If token does not match `MCP_API_KEY` (constant-time comparison via `hmac.compare_digest`) -- return 401 with `{"error": {"code": "unauthorized", "message": "Invalid bearer token"}}`
6. If token matches -- request passes through

**Security Properties:**
- Constant-time token comparison prevents timing attacks (`hmac.compare_digest`)
- Failed auth attempts logged at WARNING level with client IP, endpoint, and method
- API key sourced from environment variable, never hardcoded

**Examples:**
- Positive case: Request with valid `Authorization: Bearer sk-mcp-...` header -- passes through to tool handler
- Negative case: Request with no auth header when `MCP_API_KEY` is set -- returns 401
- Negative case: Request with `Authorization: Basic ...` header -- returns 401 (only Bearer scheme accepted)
- Positive case: Request to `/health` with no auth header -- returns 200 regardless of `MCP_API_KEY` setting
- Positive case: No `MCP_API_KEY` configured -- all requests pass through without auth

---

## Non-Functional Requirements

### [cycle-route-api:NFR-001] External API Dependency
**Category:** Reliability
**Description:** The server depends on three external services: OSRM (route planning), Overpass API (infrastructure data), and Cherwell ArcGIS MapServer (site boundaries). All outbound HTTP requests use a 20-second timeout via `httpx.AsyncClient`. A 0.5-second delay is inserted between consecutive external API calls to avoid overloading public services. All external requests include the `BBUGCycleRouteAssessment/1.0` User-Agent header. If OSRM is unavailable or returns no route, the tool returns an error. If Overpass returns no data, the tool returns a success response with zero score and a note.
**Acceptance Threshold:** Individual external API failure returns a clear error to the caller without crashing the server; timeout does not exceed 20 seconds per call
**Verification:** Testing (mock external API failures and verify graceful degradation)

### [cycle-route-api:NFR-002] Memory
**Category:** Performance
**Description:** The MCP server container is limited to 512MB memory. The server holds no persistent state beyond the HTTP client. Overpass queries are sampled to ~50 coordinate points to avoid excessive response sizes.
**Acceptance Threshold:** Container operates within 512MB under normal load
**Verification:** Observability (Docker container memory metrics)

### [cycle-route-api:NFR-003] GeoJSON Compliance
**Category:** Standards
**Description:** All GeoJSON output from the `get_site_boundary` tool conforms to RFC 7946. Coordinates are in WGS84 (longitude, latitude order). FeatureCollections contain typed Features with geometry and properties. The output is loadable by standard GeoJSON consumers (Leaflet, Mapbox, QGIS).
**Acceptance Threshold:** GeoJSON output validates against RFC 7946; coordinates in [longitude, latitude] order
**Verification:** Testing (validate output against GeoJSON schema)

---

## Open Questions

None. This documents the system as-built.

---

## Appendix

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CYCLE_ROUTE_PORT` | No | `3004` | Server listen port |
| `MCP_API_KEY` | No | (unset) | Bearer token for authentication. When unset, auth is disabled. |
| `ARCGIS_PLANNING_URL` | No | Cherwell MapServer URL | ArcGIS REST API query endpoint for site boundary lookup |
| `OSRM_URL` | No | `https://router.project-osrm.org/route/v1/bike` | OSRM cycling route endpoint |

### Source Files

| File | Purpose |
|------|---------|
| `src/mcp_servers/cycle_route/server.py` | MCP server, tool registration, tool handlers |
| `src/mcp_servers/cycle_route/infrastructure.py` | Overpass query, OSM tag classification, provision types |
| `src/mcp_servers/cycle_route/scoring.py` | LTN 1/20 scoring algorithm with five weighted factors |
| `src/mcp_servers/cycle_route/geojson.py` | ESRI JSON to GeoJSON conversion, centroid calculation |
| `src/mcp_servers/cycle_route/issues.py` | Issue detection and S106 suggestion generation |
| `src/mcp_servers/shared/transport.py` | Dual SSE + Streamable HTTP transport setup |
| `src/mcp_servers/shared/auth.py` | Bearer token authentication middleware |

### Glossary

- **OSRM**: Open Source Routing Machine -- calculates cycling routes between coordinates using the bike profile
- **Overpass API**: Queries OpenStreetMap data for infrastructure attributes (way tags) along a route corridor
- **ESRI ArcGIS MapServer**: Cherwell's public geospatial service hosting planning application site boundary polygons
- **GeoJSON**: RFC 7946 standard format for encoding geographic data structures (points, polygons, feature collections)
- **Centroid**: The arithmetic mean of polygon vertex coordinates -- used as route origin approximation
- **LTN 1/20**: Local Transport Note 1/20 "Cycle Infrastructure Design" -- UK government guidance defining design standards for cycling infrastructure
- **S106**: Section 106 of the Town and Country Planning Act 1990 -- legal agreements between developers and local authorities for infrastructure contributions
- **Provision type**: The type of cycling infrastructure on a road segment -- segregated, shared_use, on_road_lane, advisory_lane, or none
- **RAG rating**: Red/Amber/Green classification derived from the 0-100 LTN 1/20 score
- **Streamable HTTP**: The current MCP transport standard -- uses a single `/mcp` HTTP endpoint for bidirectional communication
- **SSE (Server-Sent Events)**: The legacy MCP transport -- supported for backward compatibility with the internal worker
- **Bearer token**: HTTP authentication scheme where the client sends `Authorization: Bearer <token>` header
- **MCP_API_KEY**: Environment variable holding the shared secret for MCP server authentication
- **WGS84 / EPSG:4326**: Standard GPS coordinate system (longitude, latitude) used by GeoJSON and web mapping

### References

- [LTN 1/20 Cycle Infrastructure Design](https://www.gov.uk/government/publications/cycle-infrastructure-design-ltn-120) -- Primary scoring reference
- [OSRM API documentation](https://project-osrm.org/docs/v5.24.0/api/)
- [Overpass API documentation](https://wiki.openstreetmap.org/wiki/Overpass_API)
- [Cherwell ArcGIS MapServer](https://utility.arcgis.com/usrsvcs/servers/3b969cb8886849d993863e4c913c82fc/rest/services/Public_Map_Services/Cherwell_Public_Planning_Register/MapServer/0) -- Site boundary data source
- [GeoJSON RFC 7946](https://datatracker.ietf.org/doc/html/rfc7946) -- GeoJSON standard
- [MCP Streamable HTTP Transport](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports#streamable-http) -- Current MCP transport specification
- [Original cycle-route-assessment specification](../cycle-route-assessment/specification.md) -- Design-time specification this documents as-built

### Change History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-14 | Claude Opus 4.6 | Initial as-built specification documenting MCP server tool contracts |
