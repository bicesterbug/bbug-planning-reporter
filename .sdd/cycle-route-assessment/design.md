# Design: Cycle Route Assessment

**Version:** 1.0
**Date:** 2026-02-14
**Status:** Draft
**Linked Specification** `.sdd/cycle-route-assessment/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The system has a 7-phase review pipeline orchestrated by `AgentOrchestrator`:

1. FETCHING_METADATA → 2. FILTERING_DOCUMENTS → 3. DOWNLOADING_DOCUMENTS → 4. INGESTING_DOCUMENTS → 5. ANALYSING_APPLICATION → 6. GENERATING_REVIEW → 7. VERIFYING_REVIEW

Three MCP servers (cherwell-scraper, document-store, policy-kb) provide tools via SSE transport on ports 3001-3003. They have no authentication and are only accessible within the Docker `agent-net` network. The worker connects to them via the `MCPClientManager` which routes tool calls by name.

The structured review output uses a two-phase LLM generation in GENERATING_REVIEW: a structure call (JSON) followed by a report call (markdown). The JSON schema is defined in `review_schema.py` and the prompt in `structure_prompt.py`. Results flow through: orchestrator → worker → Redis → API. Route assessment data must be available as evidence context **before** this phase so cycling infrastructure issues and S106 funding suggestions are factored into the final report.

### Proposed Architecture

Four changes layered into phases:

1. **Shared MCP auth middleware** — A reusable Starlette middleware (`src/mcp_servers/shared/auth.py`) that checks `Authorization: Bearer <token>` against `MCP_API_KEY` env var. Added to all MCP server `create_app()` functions. Opt-in: no auth when env var is unset.

2. **Streamable HTTP transport** — All MCP servers add a `/mcp` endpoint using the SDK's `StreamableHTTPServerTransport` alongside existing `/sse` + `/messages`. The existing SSE endpoints remain for backward compatibility with the internal worker.

3. **External exposure via Traefik** — Production docker-compose adds MCP servers to the `traefik_default` network with path-prefix routing (`/mcp/<service>/`). The API service already demonstrates this pattern.

4. **New cycle-route MCP server** — A fourth MCP server (`src/mcp_servers/cycle_route/`) on port 3004 providing `get_site_boundary` and `assess_cycle_route` tools. Uses httpx to call ESRI ArcGIS, OSRM, and Overpass APIs.

5. **Pipeline integration** — New `ASSESSING_ROUTES` phase inserted between ANALYSING_APPLICATION and GENERATING_REVIEW (phase 6, bumping GENERATING_REVIEW to 7 and VERIFYING_REVIEW to 8). This ensures route assessment data (infrastructure issues, LTN 1/20 scores, S106 suggestions) is available in the evidence context when the LLM generates the review. New `route_assessments` and `site_boundary` fields in review output. New API endpoint `GET /api/v1/reviews/{id}/site-boundary`. Destination management via Redis with API endpoints.

### Technology Decisions

- **httpx** for external API calls (OSRM, Overpass, ArcGIS) — already a project dependency, async-native
- **No new geospatial libraries** — centroid calculation is simple arithmetic on polygon coordinates; ESRI→GeoJSON conversion is trivial (rings map directly to GeoJSON coordinates when requesting `outSR=4326`)
- **MCP SDK StreamableHTTPServerTransport** — already available in the installed SDK (`mcp>=1.0.0`), just not used yet
- **`hmac.compare_digest`** for constant-time token comparison — stdlib, no new dependency
- **Redis** for destination storage — follows existing pattern for configuration data (policies, webhooks)

### Quality Attributes

- **Reliability**: Route assessment is a new optional phase — failure is recoverable, the review completes without route data. Auth is opt-in via env var, zero impact on existing deployments.
- **Maintainability**: LTN 1/20 scoring factors are named constants in a dedicated module. Auth middleware is shared across all servers.
- **Security**: Bearer token auth with constant-time comparison, enforced on all MCP protocol endpoints when configured.

---

## API Design

### New Endpoints

**GET /api/v1/reviews/{id}/site-boundary**
- Returns GeoJSON FeatureCollection with site polygon and centroid point
- 200: GeoJSON response with `Content-Type: application/geo+json`
- 404: `site_boundary_not_found` if review has no boundary data

**GET /api/v1/destinations**
- Returns list of configured destinations with names, coordinates, categories
- 200: `{"destinations": [...], "total": N}`

**POST /api/v1/destinations**
- Adds a new destination
- Body: `{"name": "...", "lat": N, "lon": N, "category": "rail"|"bus"|"other"}`
- 201: Created destination with generated ID

**DELETE /api/v1/destinations/{id}**
- Removes a destination
- 200: Success

### Modified Request Schema

`ReviewOptionsRequest` gains optional `destination_ids: list[str] | None` field. When null/omitted, all destinations are assessed.

### Modified Response Schema

`ReviewContent` gains optional `route_assessments: list[RouteAssessment] | None` field.

`ReviewResponse` gains optional `site_boundary: dict | None` field at top level.

---

## Modified Components

### AgentOrchestrator.run (phases list)

**Change Description** Currently executes 7 phases in sequence. Must insert `ASSESSING_ROUTES` as phase 6, between ANALYSING_APPLICATION and GENERATING_REVIEW (bumping GENERATING_REVIEW to 7 and VERIFYING_REVIEW to 8). This positioning ensures route assessment data is available in `_build_evidence_context` when the LLM generates the review, so cycling infrastructure issues and S106 funding suggestions are included in the final report. The new phase calls the cycle-route MCP server to look up the site boundary, geocode the centroid, and assess routes to configured destinations.

**Dependants** `ProgressTracker` (new phase), `ReviewResult` (new fields), `_build_evidence_context` (route data for LLM)

**Kind** Method

**Requirements References**
- [cycle-route-assessment:FR-008]: Route assessment data must be included in the review output
- [cycle-route-assessment:NFR-002]: Phase must handle failures gracefully (recoverable error)
- [cycle-route-assessment:NFR-005]: Review must complete even if route assessment fails

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Route assessment phase executes before review generation | Orchestrator with cycle-route MCP available | `run()` completes | 8 phases executed in order: ...ANALYSING→ASSESSING_ROUTES→GENERATING_REVIEW→VERIFYING, route_assessments in evidence context for LLM |
| TS-02 | Route assessment skipped on MCP unavailable | Orchestrator without cycle-route MCP | `run()` completes | 7 phases execute (ASSESSING_ROUTES skipped), review succeeds, route_assessments is None |
| TS-03 | Route assessment recoverable on boundary lookup failure | ArcGIS returns no features for this reference | `_phase_assess_routes()` runs | Phase completes with warning, route_assessments empty, GENERATING_REVIEW still proceeds |

### ReviewPhase enum and PHASE_WEIGHTS

**Change Description** Currently has 7 phases summing to weight 100. Must insert `ASSESSING_ROUTES` between ANALYSING_APPLICATION and GENERATING_REVIEW in the enum definition. Weights rebalanced: reduce ANALYSING_APPLICATION from 25→20, INGESTING_DOCUMENTS from 25→22, add ASSESSING_ROUTES at 8. Phase number map: ASSESSING_ROUTES=6, GENERATING_REVIEW=7, VERIFYING_REVIEW=8.

**Dependants** `PHASE_NUMBER_MAP`, `_build_progress_dict` (total_phases 7→8)

**Kind** Enum + constants

**Requirements References**
- [cycle-route-assessment:FR-008]: Need a pipeline phase for route assessment

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Phase weights sum to 100 | Updated PHASE_WEIGHTS dict | Sum all values | Total equals 100 |
| TS-02 | ASSESSING_ROUTES is phase 6 | Updated PHASE_NUMBER_MAP | Look up ASSESSING_ROUTES | Returns 6, GENERATING_REVIEW=7, VERIFYING_REVIEW=8, total_phases=8 |

### ReviewContent (API schema)

**Change Description** Currently has optional fields for aspects, policy_compliance, recommendations, etc. Must add `route_assessments: list[RouteAssessment] | None` field.

**Dependants** None — optional field, backward compatible

**Kind** Pydantic model

**Requirements References**
- [cycle-route-assessment:FR-008]: Route assessments in structured output
- [cycle-route-assessment:NFR-005]: Optional field for backward compatibility

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | ReviewContent with route_assessments | Response JSON with route_assessments array | Parsed by ReviewContent | route_assessments populated |
| TS-02 | ReviewContent without route_assessments | Response JSON without route_assessments | Parsed by ReviewContent | route_assessments is None, no error |

### ReviewOptionsRequest (API schema)

**Change Description** Currently has focus_areas, output_format, toggles. Must add `destination_ids: list[str] | None` for per-review destination selection.

**Dependants** `AgentOrchestrator.__init__` (receives options)

**Kind** Pydantic model

**Requirements References**
- [cycle-route-assessment:FR-006]: Per-review destination selection

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Request with destination_ids | `{"destination_ids": ["dest_001"]}` | Validated by ReviewOptionsRequest | destination_ids = ["dest_001"] |
| TS-02 | Request without destination_ids | `{}` | Validated by ReviewOptionsRequest | destination_ids = None (all destinations) |
| TS-03 | Request with empty destination_ids | `{"destination_ids": []}` | Validated by ReviewOptionsRequest | destination_ids = [] (no assessment) |

### MCPClientManager and TOOL_ROUTING

**Change Description** Currently routes tools to 3 server types. Must add `CYCLE_ROUTE` server type and route `get_site_boundary` and `assess_cycle_route` tools. Must also pass optional auth headers when `MCP_API_KEY` is configured.

**Dependants** None

**Kind** Class + dict

**Requirements References**
- [cycle-route-assessment:FR-001]: New MCP server tools must be routable
- [cycle-route-assessment:FR-011]: Client must pass auth headers when configured

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Route assess_cycle_route to cycle-route server | TOOL_ROUTING updated | `call_tool("assess_cycle_route", ...)` | Calls cycle-route MCP server |
| TS-02 | Graceful degradation when cycle-route unavailable | Cycle-route MCP not running | `initialize()` | Other servers connect; cycle-route logged as warning |

### Existing MCP server create_app functions

**Change Description** The `create_app()` function in each of the 3 existing MCP servers (cherwell-scraper, document-store, policy-kb) creates a Starlette app with `/sse`, `/messages`, and optionally `/health` routes, with no middleware. Must add: (1) `MCPAuthMiddleware` when `MCP_API_KEY` is set, (2) Streamable HTTP `/mcp` endpoint, (3) `/health` endpoint on all servers (currently only cherwell-scraper has one).

**Dependants** None — middleware wraps existing routes transparently

**Kind** Functions (3 files)

**Requirements References**
- [cycle-route-assessment:FR-011]: Bearer token authentication on all MCP servers
- [cycle-route-assessment:FR-012]: Streamable HTTP transport on all MCP servers
- [cycle-route-assessment:NFR-005]: Backward compatible when MCP_API_KEY not set
- [cycle-route-assessment:NFR-006]: Constant-time comparison, logging

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Auth enforced when MCP_API_KEY set | `MCP_API_KEY=secret` | Request to `/sse` without auth | 401 Unauthorized |
| TS-02 | Auth passes with valid token | `MCP_API_KEY=secret`, `Authorization: Bearer secret` | Request to `/sse` | 200, SSE stream initiated |
| TS-03 | No auth when MCP_API_KEY unset | `MCP_API_KEY` not in env | Request to `/sse` without auth | 200, SSE stream initiated |
| TS-04 | Health endpoint always open | `MCP_API_KEY=secret` | Request to `/health` without auth | 200 OK |
| TS-05 | Streamable HTTP endpoint available | Server running | POST to `/mcp` with MCP headers | Valid MCP response |
| TS-06 | Invalid token rejected | `MCP_API_KEY=secret`, `Authorization: Bearer wrong` | Request to `/sse` | 401 Unauthorized |

### Production docker-compose.yml

**Change Description** Currently MCP servers are on `agent-net` only, not exposed via Traefik. Must add: (1) `traefik_default` network to all MCP services, (2) Traefik path-prefix routing labels for each (`/mcp/cherwell-scraper/`, etc.), (3) `MCP_API_KEY` env var, (4) new cycle-route-mcp service, (5) `CYCLE_ROUTE_URL` env var on worker.

**Dependants** deploy.sh (no changes needed), .env.example (new vars)

**Kind** Docker Compose configuration

**Requirements References**
- [cycle-route-assessment:FR-013]: External exposure via Traefik
- [cycle-route-assessment:FR-011]: MCP_API_KEY env var in production

**Test Scenarios**

N/A — Infrastructure configuration, verified by deployment testing.

### GitHub Actions release-build.yml

**Change Description** Must add `cycle-route-mcp` to the build matrix so it gets built and published to GHCR on release.

**Dependants** None

**Kind** CI/CD workflow

**Requirements References**
- [cycle-route-assessment:FR-001]: New server needs container image

**Test Scenarios**

N/A — CI/CD configuration, verified by release workflow.

---

## Added Components

### MCPAuthMiddleware

**Description** Starlette `BaseHTTPMiddleware` that checks `Authorization: Bearer <token>` header against the `MCP_API_KEY` environment variable. Uses `hmac.compare_digest` for constant-time comparison. Exempts `/health` endpoint. When `MCP_API_KEY` is not set, passes all requests through (no-op). Logs failed auth attempts at WARNING with client IP.

**Users** All 4 MCP server `create_app()` functions

**Kind** Class

**Location** `src/mcp_servers/shared/auth.py`

**Requirements References**
- [cycle-route-assessment:FR-011]: Bearer token authentication
- [cycle-route-assessment:NFR-005]: Opt-in, backward compatible when env var unset
- [cycle-route-assessment:NFR-006]: Constant-time comparison, logging, no bypass

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Valid bearer token accepted | Middleware with key="secret" | Request with `Authorization: Bearer secret` | Request passes through |
| TS-02 | Missing auth rejected | Middleware with key="secret" | Request with no Authorization header | 401 response |
| TS-03 | Invalid token rejected | Middleware with key="secret" | Request with `Authorization: Bearer wrong` | 401 response |
| TS-04 | Health exempt from auth | Middleware with key="secret" | GET /health without auth | Request passes through |
| TS-05 | No-op when key not configured | Middleware with key=None | Request with no auth | Request passes through |
| TS-06 | Basic auth scheme rejected | Middleware with key="secret" | `Authorization: Basic ...` | 401 response |

### create_mcp_app helper

**Description** Shared function that creates a Starlette app with SSE transport, Streamable HTTP transport, `/health` endpoint, and optional auth middleware. Replaces the duplicated `create_app()` logic in each server. Each server calls `create_mcp_app(mcp_server, health_handler=...)` instead of building routes manually.

**Users** All 4 MCP server modules

**Kind** Function

**Location** `src/mcp_servers/shared/transport.py`

**Requirements References**
- [cycle-route-assessment:FR-012]: Streamable HTTP transport
- [cycle-route-assessment:FR-011]: Auth middleware integration
- [cycle-route-assessment:NFR-005]: Backward compatible SSE support

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | App has SSE endpoint | `create_mcp_app(server)` | GET /sse | SSE stream initiated |
| TS-02 | App has Streamable HTTP endpoint | `create_mcp_app(server)` | POST /mcp with MCP headers | Valid response |
| TS-03 | App has health endpoint | `create_mcp_app(server)` | GET /health | 200 OK |
| TS-04 | Auth middleware attached when key set | `MCP_API_KEY=secret` | `create_mcp_app(server)` | Auth enforced on /sse, /messages, /mcp |

### CycleRouteMCP server

**Description** New MCP server providing two tools: `get_site_boundary` (queries ESRI ArcGIS for site polygon, converts to GeoJSON, calculates centroid) and `assess_cycle_route` (calculates cycling route via OSRM, queries infrastructure via Overpass, scores against LTN 1/20, identifies issues). Uses httpx.AsyncClient for all external API calls with configurable User-Agent and timeouts.

**Users** `MCPClientManager` (via tool routing), external clients (Claude Desktop, n8n)

**Kind** Module

**Location** `src/mcp_servers/cycle_route/server.py`

**Requirements References**
- [cycle-route-assessment:FR-001]: MCP server with route assessment tools
- [cycle-route-assessment:FR-002]: Route infrastructure analysis via Overpass
- [cycle-route-assessment:FR-003]: LTN 1/20 scoring
- [cycle-route-assessment:FR-004]: Key issues identification
- [cycle-route-assessment:FR-007]: Site boundary lookup from ArcGIS
- [cycle-route-assessment:NFR-001]: Complete within 30s for 3 destinations
- [cycle-route-assessment:NFR-003]: Rate limiting, User-Agent headers

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | get_site_boundary returns GeoJSON | Mock ArcGIS returns polygon for 21/03267/OUT | `get_site_boundary({"application_ref": "21/03267/OUT"})` | GeoJSON FeatureCollection with polygon + centroid features |
| TS-02 | get_site_boundary handles not found | Mock ArcGIS returns empty features | `get_site_boundary({"application_ref": "99/99999/X"})` | Error response: "Application not found in planning register" |
| TS-03 | assess_cycle_route returns full assessment | Mock OSRM returns route, mock Overpass returns way tags | `assess_cycle_route({"origin": {...}, "destination": {...}})` | Assessment with distance, provision breakdown, score, issues |
| TS-04 | assess_cycle_route handles no route | Mock OSRM returns no route | `assess_cycle_route(...)` | Error response: "No cycling route found" |
| TS-05 | Large site centroid noted | Mock ArcGIS returns polygon with STArea > 100000 | `get_site_boundary(...)` | GeoJSON properties include centroid_note about large site |

### ESRI to GeoJSON converter

**Description** Converts ESRI JSON polygon geometry (rings array in WGS84) to GeoJSON FeatureCollection. Calculates centroid as average of polygon exterior ring coordinates. Returns FeatureCollection with two features: the site polygon and the centroid point. Properties include application_ref, address, area_sqm, and centroid_note.

**Users** `CycleRouteMCP.get_site_boundary` tool handler

**Kind** Function

**Location** `src/mcp_servers/cycle_route/geojson.py`

**Requirements References**
- [cycle-route-assessment:FR-007]: ESRI polygon → GeoJSON conversion + centroid
- [cycle-route-assessment:FR-010]: GeoJSON RFC 7946 compliant

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Simple polygon converted | ESRI rings with 4 coordinate pairs | `esri_to_geojson(rings, properties)` | Valid GeoJSON FeatureCollection |
| TS-02 | Centroid calculated correctly | Square polygon at known coordinates | `esri_to_geojson(rings, ...)` | Centroid feature at geometric centre |
| TS-03 | Multi-ring polygon handled | ESRI rings with exterior + hole | `esri_to_geojson(rings, ...)` | GeoJSON Polygon with coordinates array for each ring |
| TS-04 | Properties preserved | Properties dict with application_ref, address | `esri_to_geojson(rings, properties)` | Features have matching properties |

### Route infrastructure analyser

**Description** Given an OSRM route geometry (list of coordinates), queries the Overpass API for OSM way data along the route. For each way, extracts: highway classification, cycleway tags (determining provision type), maxspeed, surface, and lit status. Aggregates into route segments with consistent provision. Returns a structured breakdown of the route by provision type with distances.

**Users** `CycleRouteMCP.assess_cycle_route` tool handler

**Kind** Module

**Location** `src/mcp_servers/cycle_route/infrastructure.py`

**Requirements References**
- [cycle-route-assessment:FR-002]: Query Overpass for way tags, determine provision type
- [cycle-route-assessment:NFR-003]: Rate limiting for Overpass calls

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Segregated cycleway detected | Overpass returns way with `highway=cycleway` | `analyse_route(coordinates)` | Segment with provision="segregated" |
| TS-02 | Shared-use path detected | Way with `highway=path, bicycle=designated, foot=designated` | `analyse_route(coordinates)` | Segment with provision="shared_use" |
| TS-03 | On-road lane detected | Way with `cycleway=lane` | `analyse_route(coordinates)` | Segment with provision="on_road_lane" |
| TS-04 | No provision detected | Way with `highway=primary`, no cycleway tags | `analyse_route(coordinates)` | Segment with provision="none" |
| TS-05 | Speed limit inferred | Way with `highway=residential`, no maxspeed | `analyse_route(coordinates)` | speed_limit="30" (UK default for residential) |
| TS-06 | Unknown surface handled | Way with no surface tag | `analyse_route(coordinates)` | surface="unknown" |

### LTN 1/20 route scorer

**Description** Calculates a 0-100 cycling quality score for a route based on LTN 1/20 principles. Scoring factors (each as named constants): proportion on segregated infrastructure (0-40 points), maximum speed on unsegregated sections (0-25 points, penalises >30mph), surface quality (0-15 points), route directness ratio vs driving distance (0-10 points), hostile junction count penalty (0-10 points). Returns score, RAG rating (Green ≥70, Amber 40-69, Red <40), and per-factor breakdown.

**Users** `CycleRouteMCP.assess_cycle_route` tool handler

**Kind** Module

**Location** `src/mcp_servers/cycle_route/scoring.py`

**Requirements References**
- [cycle-route-assessment:FR-003]: LTN 1/20 scoring with RAG rating
- [cycle-route-assessment:NFR-004]: Transparent scoring with named constants and factor breakdown

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Fully segregated route scores green | 100% segregated, good surface, direct | `score_route(segments)` | Score ≥ 85, rating="green" |
| TS-02 | Mixed route scores amber | 50% segregated, 50% 30mph road | `score_route(segments)` | Score 40-69, rating="amber" |
| TS-03 | High-speed unsegregated scores red | 80% on 50mph road, no provision | `score_route(segments)` | Score < 30, rating="red" |
| TS-04 | Score breakdown returned | Any route | `score_route(segments)` | breakdown dict with all 5 factor scores |
| TS-05 | Short route flagged | Route under 200m | `score_route(segments)` | short_route_note in result |

### Route issues identifier

**Description** Analyses infrastructure segments to identify specific issues requiring improvement. Each issue has: location description, problem description, severity (high/medium/low based on speed and provision gap), and a suggested improvement. Also generates S106 funding suggestions by pairing issues with improvement cost justifications citing LTN 1/20.

**Users** `CycleRouteMCP.assess_cycle_route` tool handler

**Kind** Module

**Location** `src/mcp_servers/cycle_route/issues.py`

**Requirements References**
- [cycle-route-assessment:FR-004]: Key issues with location, problem, improvement
- [cycle-route-assessment:FR-009]: S106 funding suggestions from issues

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | High-speed no-provision issue identified | Segment: 40mph, provision="none", 500m | `identify_issues(segments)` | Issue: severity="high", suggested improvement references LTN 1/20 Table 4-1 |
| TS-02 | Poor surface issue identified | Segment: provision="shared_use", surface="gravel" | `identify_issues(segments)` | Issue about resurfacing |
| TS-03 | No issues on good route | All segments: segregated, good surface | `identify_issues(segments)` | Empty issues list |
| TS-04 | S106 suggestion generated | Issue with high severity | `generate_s106_suggestions(issues)` | S106 suggestion referencing the issue and LTN 1/20 |

### Destination management

**Description** CRUD operations for cycle route destinations stored in Redis. Default destinations (Bicester North, Bicester Village, Manorsfield Road bus station) are seeded on first access if none exist. Each destination has: id (prefixed `dest_`), name, lat, lon, category. API routes at `/api/v1/destinations`.

**Users** API routes, orchestrator (to fetch destinations for assessment)

**Kind** Module + API routes

**Location** `src/shared/destinations.py` (storage), `src/api/routes/destinations.py` (API)

**Requirements References**
- [cycle-route-assessment:FR-005]: Configurable destinations with defaults
- [cycle-route-assessment:FR-006]: Per-review selection references destinations by ID

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Default destinations seeded | Empty Redis | `list_destinations()` | Returns 3 defaults: Bicester North, Bicester Village, Manorsfield Road |
| TS-02 | Add destination | Redis with defaults | `add_destination(name="Town Centre", lat=51.9, lon=-1.15, category="other")` | 4 destinations total, new one has generated ID |
| TS-03 | Delete destination | Redis with 4 destinations | `delete_destination("dest_004")` | 3 destinations remain |
| TS-04 | List destinations via API | Destinations in Redis | GET /api/v1/destinations | JSON array with all destinations |

### Site boundary API endpoint

**Description** New API route `GET /api/v1/reviews/{id}/site-boundary` that returns the stored GeoJSON for a review's site boundary. The GeoJSON is stored in the review result dict under `site_boundary` key by the orchestrator's route assessment phase.

**Users** Website frontend, external mapping tools

**Kind** API route

**Location** `src/api/routes/reviews.py`

**Requirements References**
- [cycle-route-assessment:FR-010]: GeoJSON served via API endpoint

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Boundary returned for completed review | Review with site_boundary in result | GET /api/v1/reviews/{id}/site-boundary | 200 with GeoJSON FeatureCollection |
| TS-02 | 404 when no boundary | Review completed but boundary lookup failed | GET /api/v1/reviews/{id}/site-boundary | 404 with `site_boundary_not_found` |
| TS-03 | 404 for unknown review | No review with this ID | GET /api/v1/reviews/{id}/site-boundary | 404 with `review_not_found` |

### Dockerfile.cycle-route

**Description** Dockerfile for the cycle-route MCP server container. Based on `cherwell-base:latest`, exposes port 3004, health check via `/health` endpoint.

**Users** Docker Compose, GitHub Actions

**Kind** Dockerfile

**Location** `docker/Dockerfile.cycle-route`

**Requirements References**
- [cycle-route-assessment:FR-001]: Containerised MCP server

**Test Scenarios**

N/A — Infrastructure, verified by build and deployment.

---

## Used Components

### MCPClientManager

**Location** `src/agent/mcp_client.py`

**Provides** Tool routing and SSE client connection management. The `call_tool()` method establishes a fresh SSE session per call. The `TOOL_ROUTING` dict maps tool names to server types.

**Used By** AgentOrchestrator (modified — new phase calls cycle-route tools), MCPServerType enum (modified — new CYCLE_ROUTE entry)

### ProgressTracker

**Location** `src/agent/progress.py`

**Provides** Phase-based progress tracking with Redis pub/sub. Tracks phase transitions, sub-progress, timing, and errors. Reports percent complete based on phase weights.

**Used By** AgentOrchestrator (modified — new ASSESSING_ROUTES phase)

### RedisClient

**Location** `src/shared/redis_client.py`

**Provides** Redis wrapper for storing/retrieving review results and job state. `store_result()` persists the review dict, `get_result()` retrieves it.

**Used By** Destination management (added), site boundary endpoint (added)

### AuthMiddleware (API reference)

**Location** `src/api/middleware/auth.py`

**Provides** Pattern reference for bearer token authentication. The MCPAuthMiddleware follows the same approach but simplified for a single shared token.

**Used By** MCPAuthMiddleware (added — inspired by, not directly reused due to different validation logic)

### SseServerTransport

**Location** `mcp.server.sse` (SDK)

**Provides** SSE transport for MCP servers. Already used by all 3 existing servers.

**Used By** `create_mcp_app` (added — wraps SSE setup alongside Streamable HTTP)

### StreamableHTTPServerTransport

**Location** `mcp.server.streamable_http` (SDK)

**Provides** Streamable HTTP transport for MCP servers. Available in the installed SDK but not currently used.

**Used By** `create_mcp_app` (added — adds `/mcp` endpoint)

---

## Documentation Considerations

- Update `deploy/PRODUCTION.md` with new MCP_API_KEY env var and cycle-route-mcp service
- Update `.env.example` in both root and deploy dirs with new env vars
- Update `deploy/docker-compose.yml` reference in PRODUCTION.md resource limits table

---

## Instrumentation

| Requirement | Observability Criteria | Implementation | Component |
|-------------|------------------------|----------------|-----------|
| [cycle-route-assessment:NFR-001] | Route assessment duration per destination | `logger.info("Route assessed", destination=name, duration_seconds=N)` | CycleRouteMCP |
| [cycle-route-assessment:NFR-001] | Total phase duration | ProgressTracker phase timing (existing) | AgentOrchestrator |
| [cycle-route-assessment:NFR-006] | Failed auth attempts | `logger.warning("Auth failed", client_ip=ip, endpoint=path)` | MCPAuthMiddleware |

---

## Integration Test Scenarios

| ID | Scenario | Given | When | Then | Components Involved |
|----|----------|-------|------|------|---------------------|
| ITS-01 | Full pipeline with route assessment | Mock MCP servers including cycle-route, mock external APIs | Orchestrator `run()` | Review completes with 8 phases in order (...ANALYSING→ASSESSING_ROUTES→GENERATING_REVIEW→VERIFYING), route_assessments populated and included in LLM evidence context, site_boundary stored | AgentOrchestrator, CycleRouteMCP, ProgressTracker |
| ITS-02 | Pipeline without cycle-route MCP | Only 3 original MCP servers available | Orchestrator `run()` | Review completes with 7 phases (ASSESSING_ROUTES skipped), route_assessments is None | AgentOrchestrator, MCPClientManager |
| ITS-03 | Auth + transport integration | MCP server with auth enabled, client with token | Client connects via SSE and Streamable HTTP | Both transports work with valid auth, reject without | MCPAuthMiddleware, create_mcp_app |
| ITS-04 | Site boundary API with review data | Completed review with site_boundary in Redis | GET /api/v1/reviews/{id}/site-boundary | GeoJSON returned, Content-Type correct | Site boundary endpoint, RedisClient |
| ITS-05 | Destinations API round-trip | Empty destinations | POST destination, GET destinations, use in review | Destination stored, listed, used as route target | Destination management, API routes |

---

## E2E Test Scenarios

N/A — E2E testing requires live external APIs (OSRM, Overpass, ArcGIS) which are not feasible in CI. Integration tests with mocked APIs provide sufficient coverage. Manual E2E verification against live APIs is documented in the test data section.

---

## Test Data

- **ESRI ArcGIS fixture**: JSON response for application 21/03267/OUT in WGS84 (polygon with 19 coordinate pairs). Captured from live API and stored as `tests/fixtures/cycle_route/arcgis_response_21_03267.json`.
- **OSRM route fixture**: JSON response for a cycling route from site centroid to Bicester North station. `tests/fixtures/cycle_route/osrm_route_to_bicester_north.json`.
- **Overpass infrastructure fixture**: JSON response for way tags along the route. `tests/fixtures/cycle_route/overpass_ways.json`.
- **Known-score routes**: Hand-calculated test cases for LTN 1/20 scoring validation (fully segregated, mixed, high-speed unsegregated).

---

## Test Feasibility

- All external API calls (ArcGIS, OSRM, Overpass) are mocked in tests using httpx mock transport or `unittest.mock.patch`
- MCP server tests use the existing in-process testing pattern
- Auth middleware tests use Starlette TestClient
- No missing infrastructure

---

## Risks and Dependencies

- **Risk: Overpass API rate limiting or downtime.** Public Overpass instances may throttle heavy use. **Mitigation:** Rate limiting in client, small buffer between queries, route geometry simplified before query to reduce bbox size. Could self-host Overpass in future.
- **Risk: OSRM public instance availability.** The public OSRM demo server has no SLA. **Mitigation:** Graceful failure — route assessment skipped if OSRM unavailable. Could self-host OSRM with UK extract in future.
- **Risk: ArcGIS endpoint URL changes.** The ESRI service URL contains a GUID that could change. **Mitigation:** URL is configurable via environment variable `ARCGIS_PLANNING_URL`.
- **Risk: MCP SDK StreamableHTTP API changes.** The SDK is >= 1.0.0 but Streamable HTTP is relatively new. **Mitigation:** Pin SDK version, test transport during CI.
- **Dependency: MCP Python SDK >= 1.0.0** — already installed, includes StreamableHTTPServerTransport.
- **Dependency: httpx** — already a project dependency.
- **Assumption:** OSRM cycling profile returns reasonable routes for the Bicester area. The public instance uses OSM data which has good coverage in the UK.

---

## Feasibility Review

No missing features or infrastructure. All external APIs are publicly available. The MCP SDK already includes Streamable HTTP support. httpx is already a dependency. No new large infrastructure needed.

---

## Task Breakdown

> **CRITICAL: Tests are written WITH implementation, not after.**

### Phase 1: MCP server auth and transport

- Task 1: Create shared auth middleware
  - Status: Done
  - Create `src/mcp_servers/shared/__init__.py` and `src/mcp_servers/shared/auth.py` with `MCPAuthMiddleware`. Implement bearer token validation with constant-time comparison, health endpoint exemption, opt-in via `MCP_API_KEY`. Add unit tests.
  - Requirements: [cycle-route-assessment:FR-011], [cycle-route-assessment:NFR-005], [cycle-route-assessment:NFR-006]
  - Test Scenarios: [cycle-route-assessment:MCPAuthMiddleware/TS-01], [cycle-route-assessment:MCPAuthMiddleware/TS-02], [cycle-route-assessment:MCPAuthMiddleware/TS-03], [cycle-route-assessment:MCPAuthMiddleware/TS-04], [cycle-route-assessment:MCPAuthMiddleware/TS-05], [cycle-route-assessment:MCPAuthMiddleware/TS-06]

- Task 2: Create shared transport helper and refactor existing servers
  - Status: Done
  - Create `src/mcp_servers/shared/transport.py` with `create_mcp_app()` that sets up SSE + Streamable HTTP + health + auth. Refactor the 3 existing servers to use it. Add `/health` to document-store and policy-kb. Add unit tests for transport helper.
  - Requirements: [cycle-route-assessment:FR-012], [cycle-route-assessment:FR-011], [cycle-route-assessment:NFR-005]
  - Test Scenarios: [cycle-route-assessment:create_mcp_app/TS-01], [cycle-route-assessment:create_mcp_app/TS-02], [cycle-route-assessment:create_mcp_app/TS-03], [cycle-route-assessment:create_mcp_app/TS-04], [cycle-route-assessment:ExistingMCPServers/TS-01], [cycle-route-assessment:ExistingMCPServers/TS-02], [cycle-route-assessment:ExistingMCPServers/TS-03], [cycle-route-assessment:ExistingMCPServers/TS-04], [cycle-route-assessment:ExistingMCPServers/TS-05], [cycle-route-assessment:ExistingMCPServers/TS-06], [cycle-route-assessment:ITS-03]

- Task 3: Update production docker-compose for external MCP exposure
  - Status: Done
  - Add Traefik labels and `traefik_default` network to all 3 existing MCP services with path-prefix routing. Add `MCP_API_KEY` env var. Update `.env.example`.
  - Requirements: [cycle-route-assessment:FR-013]
  - Test Scenarios: N/A (infrastructure)

### Phase 2: Cycle route MCP server

- Task 4: Create ESRI→GeoJSON converter and site boundary tool
  - Status: Done
  - Create `src/mcp_servers/cycle_route/__init__.py`, `geojson.py` with ESRI→GeoJSON converter + centroid calculation, and `server.py` skeleton with `get_site_boundary` tool. Create test fixtures from live ArcGIS response. Add unit and tool tests.
  - Requirements: [cycle-route-assessment:FR-001], [cycle-route-assessment:FR-007], [cycle-route-assessment:FR-010]
  - Test Scenarios: [cycle-route-assessment:GeoJSONConverter/TS-01], [cycle-route-assessment:GeoJSONConverter/TS-02], [cycle-route-assessment:GeoJSONConverter/TS-03], [cycle-route-assessment:GeoJSONConverter/TS-04], [cycle-route-assessment:CycleRouteMCP/TS-01], [cycle-route-assessment:CycleRouteMCP/TS-02], [cycle-route-assessment:CycleRouteMCP/TS-05]

- Task 5: Create route infrastructure analyser
  - Status: Done
  - Create `infrastructure.py` with Overpass query builder and way tag parser. Map OSM tags to provision types, speed limits, surfaces. Create Overpass response fixture. Add unit tests.
  - Requirements: [cycle-route-assessment:FR-002], [cycle-route-assessment:NFR-003]
  - Test Scenarios: [cycle-route-assessment:InfrastructureAnalyser/TS-01], [cycle-route-assessment:InfrastructureAnalyser/TS-02], [cycle-route-assessment:InfrastructureAnalyser/TS-03], [cycle-route-assessment:InfrastructureAnalyser/TS-04], [cycle-route-assessment:InfrastructureAnalyser/TS-05], [cycle-route-assessment:InfrastructureAnalyser/TS-06]

- Task 6: Create LTN 1/20 scorer and issues identifier
  - Status: Done
  - Create `scoring.py` with weighted scoring algorithm and `issues.py` with issue detection + S106 suggestion generation. Add unit tests with known-score route scenarios.
  - Requirements: [cycle-route-assessment:FR-003], [cycle-route-assessment:FR-004], [cycle-route-assessment:FR-009], [cycle-route-assessment:NFR-004]
  - Test Scenarios: [cycle-route-assessment:RouteScorer/TS-01], [cycle-route-assessment:RouteScorer/TS-02], [cycle-route-assessment:RouteScorer/TS-03], [cycle-route-assessment:RouteScorer/TS-04], [cycle-route-assessment:RouteScorer/TS-05], [cycle-route-assessment:IssuesIdentifier/TS-01], [cycle-route-assessment:IssuesIdentifier/TS-02], [cycle-route-assessment:IssuesIdentifier/TS-03], [cycle-route-assessment:IssuesIdentifier/TS-04]

- Task 7: Complete assess_cycle_route tool with OSRM integration
  - Status: Done
  - Wire OSRM route fetching, infrastructure analysis, scoring, and issue identification into the `assess_cycle_route` tool in `server.py`. Create OSRM fixture. Add tool-level tests.
  - Requirements: [cycle-route-assessment:FR-001], [cycle-route-assessment:FR-002], [cycle-route-assessment:NFR-001], [cycle-route-assessment:NFR-002]
  - Test Scenarios: [cycle-route-assessment:CycleRouteMCP/TS-03], [cycle-route-assessment:CycleRouteMCP/TS-04]

- Task 8: Add Dockerfile and CI build for cycle-route MCP
  - Status: Done
  - Create `docker/Dockerfile.cycle-route`, add to GitHub Actions build matrix, add to dev `docker-compose.yml`.
  - Requirements: [cycle-route-assessment:FR-001]
  - Test Scenarios: N/A (infrastructure)

### Phase 3: Pipeline integration

- Task 9: Add destination management
  - Status: Backlog
  - Create `src/shared/destinations.py` with Redis CRUD + default seeding. Create `src/api/routes/destinations.py` with GET/POST/DELETE endpoints. Add `destination_ids` to `ReviewOptionsRequest`. Add tests.
  - Requirements: [cycle-route-assessment:FR-005], [cycle-route-assessment:FR-006]
  - Test Scenarios: [cycle-route-assessment:DestinationManagement/TS-01], [cycle-route-assessment:DestinationManagement/TS-02], [cycle-route-assessment:DestinationManagement/TS-03], [cycle-route-assessment:DestinationManagement/TS-04], [cycle-route-assessment:ReviewOptionsRequest/TS-01], [cycle-route-assessment:ReviewOptionsRequest/TS-02], [cycle-route-assessment:ReviewOptionsRequest/TS-03]

- Task 10: Add ASSESSING_ROUTES phase to orchestrator
  - Status: Backlog
  - Insert `ASSESSING_ROUTES` into `ReviewPhase` enum between ANALYSING_APPLICATION and GENERATING_REVIEW, update weights (rebalance to sum 100) and phase map (ASSESSING_ROUTES=6, GENERATING_REVIEW=7, VERIFYING_REVIEW=8). Add `_phase_assess_routes()` to orchestrator that: fetches destinations, calls `get_site_boundary`, calls `assess_cycle_route` for each destination, stores results. Add `route_assessments` and `site_boundary` to review result dict. Add `CYCLE_ROUTE` to `MCPServerType` and `TOOL_ROUTING`. Update `_build_evidence_context` to include route data (scores, issues, S106 suggestions) so the LLM incorporates them during GENERATING_REVIEW. Add tests.
  - Requirements: [cycle-route-assessment:FR-008], [cycle-route-assessment:NFR-002], [cycle-route-assessment:NFR-005]
  - Test Scenarios: [cycle-route-assessment:AgentOrchestrator/TS-01], [cycle-route-assessment:AgentOrchestrator/TS-02], [cycle-route-assessment:AgentOrchestrator/TS-03], [cycle-route-assessment:ReviewPhase/TS-01], [cycle-route-assessment:ReviewPhase/TS-02], [cycle-route-assessment:ITS-01], [cycle-route-assessment:ITS-02]

- Task 11: Add route_assessments to API schemas and site-boundary endpoint
  - Status: Backlog
  - Add `RouteAssessment` and `route_assessments` to `ReviewContent`. Add `site_boundary` to `ReviewResponse`. Add `GET /reviews/{id}/site-boundary` endpoint. Add tests.
  - Requirements: [cycle-route-assessment:FR-008], [cycle-route-assessment:FR-010], [cycle-route-assessment:NFR-005]
  - Test Scenarios: [cycle-route-assessment:ReviewContent/TS-01], [cycle-route-assessment:ReviewContent/TS-02], [cycle-route-assessment:SiteBoundaryEndpoint/TS-01], [cycle-route-assessment:SiteBoundaryEndpoint/TS-02], [cycle-route-assessment:SiteBoundaryEndpoint/TS-03], [cycle-route-assessment:ITS-04], [cycle-route-assessment:ITS-05]

- Task 12: Update production docker-compose with cycle-route-mcp
  - Status: Backlog
  - Add cycle-route-mcp service to deploy/docker-compose.yml with Traefik labels, `CYCLE_ROUTE_URL` on worker, update PRODUCTION.md.
  - Requirements: [cycle-route-assessment:FR-001], [cycle-route-assessment:FR-013]
  - Test Scenarios: N/A (infrastructure)

---

## Intermediate Dead Code Tracking

| Phase Introduced | Description | Used In Phase | Status |
|------------------|-------------|---------------|--------|
| Phase 1 | `create_mcp_app` helper with Streamable HTTP support | Phase 2 (new server uses it) | Pending |
| Phase 2 | CycleRouteMCP server (standalone, not wired to pipeline) | Phase 3 (orchestrator calls it) | Pending |

---

## Intermediate Stub Tracking

N/A — No stubs planned. Each task implements complete functionality with tests.

---

## Requirements Validation

- [cycle-route-assessment:FR-001] — Phase 2 Tasks 4, 7, 8
- [cycle-route-assessment:FR-002] — Phase 2 Tasks 5, 7
- [cycle-route-assessment:FR-003] — Phase 2 Task 6
- [cycle-route-assessment:FR-004] — Phase 2 Task 6
- [cycle-route-assessment:FR-005] — Phase 3 Task 9
- [cycle-route-assessment:FR-006] — Phase 3 Task 9
- [cycle-route-assessment:FR-007] — Phase 2 Task 4
- [cycle-route-assessment:FR-008] — Phase 3 Tasks 10, 11
- [cycle-route-assessment:FR-009] — Phase 2 Task 6
- [cycle-route-assessment:FR-010] — Phase 2 Task 4, Phase 3 Task 11
- [cycle-route-assessment:FR-011] — Phase 1 Tasks 1, 2
- [cycle-route-assessment:FR-012] — Phase 1 Task 2
- [cycle-route-assessment:FR-013] — Phase 1 Task 3, Phase 3 Task 12
- [cycle-route-assessment:NFR-001] — Phase 2 Task 7
- [cycle-route-assessment:NFR-002] — Phase 2 Task 7, Phase 3 Task 10
- [cycle-route-assessment:NFR-003] — Phase 2 Task 5
- [cycle-route-assessment:NFR-004] — Phase 2 Task 6
- [cycle-route-assessment:NFR-005] — Phase 1 Tasks 1, 2, Phase 3 Tasks 10, 11
- [cycle-route-assessment:NFR-006] — Phase 1 Task 1

---

## Test Scenario Validation

### Component Scenarios
- [cycle-route-assessment:MCPAuthMiddleware/TS-01]: Phase 1 Task 1
- [cycle-route-assessment:MCPAuthMiddleware/TS-02]: Phase 1 Task 1
- [cycle-route-assessment:MCPAuthMiddleware/TS-03]: Phase 1 Task 1
- [cycle-route-assessment:MCPAuthMiddleware/TS-04]: Phase 1 Task 1
- [cycle-route-assessment:MCPAuthMiddleware/TS-05]: Phase 1 Task 1
- [cycle-route-assessment:MCPAuthMiddleware/TS-06]: Phase 1 Task 1
- [cycle-route-assessment:create_mcp_app/TS-01]: Phase 1 Task 2
- [cycle-route-assessment:create_mcp_app/TS-02]: Phase 1 Task 2
- [cycle-route-assessment:create_mcp_app/TS-03]: Phase 1 Task 2
- [cycle-route-assessment:create_mcp_app/TS-04]: Phase 1 Task 2
- [cycle-route-assessment:ExistingMCPServers/TS-01]: Phase 1 Task 2
- [cycle-route-assessment:ExistingMCPServers/TS-02]: Phase 1 Task 2
- [cycle-route-assessment:ExistingMCPServers/TS-03]: Phase 1 Task 2
- [cycle-route-assessment:ExistingMCPServers/TS-04]: Phase 1 Task 2
- [cycle-route-assessment:ExistingMCPServers/TS-05]: Phase 1 Task 2
- [cycle-route-assessment:ExistingMCPServers/TS-06]: Phase 1 Task 2
- [cycle-route-assessment:GeoJSONConverter/TS-01]: Phase 2 Task 4
- [cycle-route-assessment:GeoJSONConverter/TS-02]: Phase 2 Task 4
- [cycle-route-assessment:GeoJSONConverter/TS-03]: Phase 2 Task 4
- [cycle-route-assessment:GeoJSONConverter/TS-04]: Phase 2 Task 4
- [cycle-route-assessment:CycleRouteMCP/TS-01]: Phase 2 Task 4
- [cycle-route-assessment:CycleRouteMCP/TS-02]: Phase 2 Task 4
- [cycle-route-assessment:CycleRouteMCP/TS-03]: Phase 2 Task 7
- [cycle-route-assessment:CycleRouteMCP/TS-04]: Phase 2 Task 7
- [cycle-route-assessment:CycleRouteMCP/TS-05]: Phase 2 Task 4
- [cycle-route-assessment:InfrastructureAnalyser/TS-01]: Phase 2 Task 5
- [cycle-route-assessment:InfrastructureAnalyser/TS-02]: Phase 2 Task 5
- [cycle-route-assessment:InfrastructureAnalyser/TS-03]: Phase 2 Task 5
- [cycle-route-assessment:InfrastructureAnalyser/TS-04]: Phase 2 Task 5
- [cycle-route-assessment:InfrastructureAnalyser/TS-05]: Phase 2 Task 5
- [cycle-route-assessment:InfrastructureAnalyser/TS-06]: Phase 2 Task 5
- [cycle-route-assessment:RouteScorer/TS-01]: Phase 2 Task 6
- [cycle-route-assessment:RouteScorer/TS-02]: Phase 2 Task 6
- [cycle-route-assessment:RouteScorer/TS-03]: Phase 2 Task 6
- [cycle-route-assessment:RouteScorer/TS-04]: Phase 2 Task 6
- [cycle-route-assessment:RouteScorer/TS-05]: Phase 2 Task 6
- [cycle-route-assessment:IssuesIdentifier/TS-01]: Phase 2 Task 6
- [cycle-route-assessment:IssuesIdentifier/TS-02]: Phase 2 Task 6
- [cycle-route-assessment:IssuesIdentifier/TS-03]: Phase 2 Task 6
- [cycle-route-assessment:IssuesIdentifier/TS-04]: Phase 2 Task 6
- [cycle-route-assessment:DestinationManagement/TS-01]: Phase 3 Task 9
- [cycle-route-assessment:DestinationManagement/TS-02]: Phase 3 Task 9
- [cycle-route-assessment:DestinationManagement/TS-03]: Phase 3 Task 9
- [cycle-route-assessment:DestinationManagement/TS-04]: Phase 3 Task 9
- [cycle-route-assessment:ReviewOptionsRequest/TS-01]: Phase 3 Task 9
- [cycle-route-assessment:ReviewOptionsRequest/TS-02]: Phase 3 Task 9
- [cycle-route-assessment:ReviewOptionsRequest/TS-03]: Phase 3 Task 9
- [cycle-route-assessment:AgentOrchestrator/TS-01]: Phase 3 Task 10
- [cycle-route-assessment:AgentOrchestrator/TS-02]: Phase 3 Task 10
- [cycle-route-assessment:AgentOrchestrator/TS-03]: Phase 3 Task 10
- [cycle-route-assessment:ReviewPhase/TS-01]: Phase 3 Task 10
- [cycle-route-assessment:ReviewPhase/TS-02]: Phase 3 Task 10
- [cycle-route-assessment:ReviewContent/TS-01]: Phase 3 Task 11
- [cycle-route-assessment:ReviewContent/TS-02]: Phase 3 Task 11
- [cycle-route-assessment:SiteBoundaryEndpoint/TS-01]: Phase 3 Task 11
- [cycle-route-assessment:SiteBoundaryEndpoint/TS-02]: Phase 3 Task 11
- [cycle-route-assessment:SiteBoundaryEndpoint/TS-03]: Phase 3 Task 11

### Integration Scenarios
- [cycle-route-assessment:ITS-01]: Phase 3 Task 10
- [cycle-route-assessment:ITS-02]: Phase 3 Task 10
- [cycle-route-assessment:ITS-03]: Phase 1 Task 2
- [cycle-route-assessment:ITS-04]: Phase 3 Task 11
- [cycle-route-assessment:ITS-05]: Phase 3 Task 11

### E2E Scenarios
N/A — External API dependencies not feasible in CI.

---

## Appendix

### Glossary
- **create_mcp_app**: Shared factory function that builds a Starlette app with dual transport (SSE + Streamable HTTP) and optional auth
- **MCPAuthMiddleware**: Starlette middleware for bearer token validation on MCP protocol endpoints
- **Phase weight**: Percentage contribution of each pipeline phase to overall progress (must sum to 100)

### References
- [cycle-route-assessment specification](specification.md)
- [MCP Python SDK StreamableHTTPServerTransport](https://github.com/modelcontextprotocol/python-sdk) — Available in installed SDK
- [Starlette BaseHTTPMiddleware](https://www.starlette.io/middleware/) — Pattern for auth middleware
- [existing API auth middleware](../../src/api/middleware/auth.py) — Reference implementation

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-14 | Claude Opus 4.6 | Initial design |
| 1.1 | 2026-02-14 | Claude Opus 4.6 | Move ASSESSING_ROUTES before GENERATING_REVIEW (phase 6) so route issues and S106 suggestions feed into the LLM review |

---
