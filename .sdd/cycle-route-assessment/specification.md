# Specification: Cycle Route Assessment

**Version:** 1.2
**Date:** 2026-02-14
**Status:** Draft

---

## Problem Statement

Planning application reviews currently assess cycling provision only within the development site boundary — cycle parking, on-site route design, and permeability. They do not evaluate the quality of cycling routes between the development and key destinations in Bicester (train stations, bus station, town centre). This means reviews miss critical off-site infrastructure deficiencies that could be addressed through S106 contributions, and advocacy responses lack the evidence base to argue for improvements to cycle routes connecting the site to the wider network.

Additionally, the existing MCP servers (cherwell-scraper, document-store, policy-kb) are only accessible internally within the Docker network with no authentication. This prevents them from being connected to external tools like Claude web, Claude Desktop, or n8n workflow automation — limiting their utility to the internal worker pipeline only.

## Beneficiaries

**Primary:**
- BBUG reviewers who need evidence-based arguments about off-site cycle infrastructure deficiencies to include in planning responses
- Cyclists who would benefit from S106-funded improvements to routes between new developments and key destinations

**Secondary:**
- Planning officers who receive more complete, data-backed consultation responses
- The review agent which gains a richer evidence base for scoring transport-related aspects and suggesting conditions
- Developers and operators who want to connect MCP servers to Claude web, Claude Desktop, or n8n for ad-hoc tool usage and workflow automation

---

## Outcomes

**Must Haves**
- The application site boundary polygon is fetched from Cherwell's ESRI ArcGIS planning register and stored as GeoJSON
- The site boundary GeoJSON is retrievable via an API endpoint
- The polygon centroid is used as the route origin, with an acknowledgement that the actual site entrance may differ
- Each review includes a route assessment for cycling from the application site to configurable destination locations
- Route assessments include distance, cycle provision type, road speed, surface quality, and an LTN 1/20-based score
- Key issues along each route are identified (e.g., missing crossings, high-speed roads with no segregation, poor surfaces)
- Route assessment findings feed into S106 funding suggestions for off-site cycle infrastructure improvements
- Destinations are configurable via API with sensible defaults (both Bicester train stations, Manorsfield Road bus station)
- Per-review destination selection is supported (default: all configured destinations)
- All MCP servers (new and existing) support authenticated remote connections from Claude web, Claude Desktop, and n8n

**Nice-to-haves**
- Route visualisation data (polyline coordinates) for potential future map display
- Intermediate waypoint breakdown showing provision changes along the route
- OAuth 2.1 support for full Claude web integration (beyond bearer token)

---

## Explicitly Out of Scope

- Map rendering or visual route display (GeoJSON data only — the website can render maps in future)
- Real-time traffic data or congestion analysis
- Pedestrian-only route assessment (cycling routes only, though shared-use paths are captured)
- Route assessment for destinations outside Bicester (e.g., Oxford, Kidlington)
- Historical route quality comparison or trend analysis
- Automatic detection of destinations — they are explicitly configured
- Precise site entrance/access point detection — centroid is used as approximation
- Editing or correcting site boundary polygons from ArcGIS
- OAuth 2.1 implementation for MCP servers (bearer token is sufficient for n8n, Claude Desktop, and Claude Code; OAuth is a nice-to-have for Claude web integration)
- Per-user or per-key MCP access control (single shared bearer token is sufficient)
- MCP server tool-level authorization (all authenticated clients can call all tools)

---

## Functional Requirements

### FR-001: Cycle Route MCP Server
**Description:** A new MCP server must provide tools for assessing cycling routes between two geographic points and for looking up application site boundaries. The server must use OSRM (Open Source Routing Machine) for cycling route calculation, the Overpass API for querying OpenStreetMap infrastructure data along the route, and Cherwell's ESRI ArcGIS MapServer for site boundary lookup. The server must support both Streamable HTTP and SSE transports with bearer token authentication (see FR-011).

**Examples:**
- Positive case: Tool `assess_cycle_route` accepts origin coordinates and destination coordinates, returns route distance, provision breakdown, speed limits, surface types, and issues
- Positive case: Tool `get_site_boundary` accepts an application reference, returns the site polygon as GeoJSON with centroid coordinates
- Edge case: OSRM returns no route (e.g., disconnected network) — tool returns an error with clear message

### FR-002: Route Infrastructure Analysis
**Description:** For each route segment returned by OSRM, the system must query Overpass API for OpenStreetMap way tags to determine: cycle provision type (segregated cycleway, shared-use path, on-road cycle lane, advisory lane, or no provision), road speed limit, surface type, and road classification. The analysis must cover each segment of the route, not just origin and destination.

**Examples:**
- Positive case: A route along a segregated cycle path returns provision "segregated", surface "asphalt", speed_limit "n/a" (off-road)
- Positive case: A route along a 40mph A-road with no cycle lane returns provision "none", surface "asphalt", speed_limit "40"
- Positive case: A shared-use pavement returns provision "shared_use", surface "asphalt", speed_limit "n/a"
- Edge case: An OSM way has no surface tag — defaults to "unknown"
- Edge case: A way has no speed limit tag but is classified as residential — infers 30mph default

### FR-003: LTN 1/20 Route Scoring
**Description:** Each route must receive a cycling quality score based on LTN 1/20 (Cycle Infrastructure Design) principles. The score considers: proportion of route on segregated infrastructure, maximum traffic speed on shared sections, surface quality, directness compared to driving route, and presence of hostile junctions. The score must be a numeric value 0-100 with a corresponding Red/Amber/Green rating.

**Examples:**
- Positive case: Route entirely on segregated cycle paths with good surfaces scores 85+ (Green)
- Positive case: Route mostly on 30mph residential roads with advisory lanes scores 40-60 (Amber)
- Positive case: Route requiring use of 50mph A-road with no provision scores below 30 (Red)
- Edge case: Very short route (under 200m) — scored but flagged as "short distance, walking may be preferable"

### FR-004: Key Issues Identification
**Description:** The assessment must identify and list specific issues along each route that would need addressing to provide safe, comfortable cycling. Each issue must include a location description, the nature of the problem, and a suggested improvement.

**Examples:**
- Positive case: "A41 roundabout at Pioneer Square — multi-lane roundabout with no cycle provision, requires grade-separated or signal-controlled crossing"
- Positive case: "Buckingham Road section (300m) — 40mph speed limit with no cycle lane, requires segregated cycleway"
- Positive case: "Path surface between Langford Park and railway bridge — unpaved/gravel surface, requires tarmac resurfacing"
- Edge case: Route has no significant issues — returns empty issues list with a note that the route is well-provided

### FR-005: Configurable Destinations
**Description:** Destination locations must be configurable via the API. The system must ship with default destinations for Bicester: Bicester North station, Bicester Village station, and Manorsfield Road bus station. Each destination has a name, coordinates, and an optional category (e.g., "rail", "bus"). New destinations can be added and existing ones can be listed via API endpoints.

**Examples:**
- Positive case: GET /api/v1/destinations returns the three default destinations with names, coordinates, and categories
- Positive case: POST /api/v1/destinations adds a new destination (e.g., "Bicester Village shopping centre")
- Edge case: Adding a destination with coordinates far outside Bicester — accepted but logged as warning

### FR-006: Per-Review Destination Selection
**Description:** When submitting a review request, the caller can optionally specify which destinations to assess routes to. If not specified, all configured destinations are assessed. Destinations are referenced by their ID or name.

**Examples:**
- Positive case: Review request with no destination selection — routes assessed to all three default destinations
- Positive case: Review request specifying only "Bicester North" — route assessed to that station only
- Positive case: Review request specifying destination IDs ["dest_001", "dest_002"] — routes assessed to those two destinations
- Edge case: Review request specifying a non-existent destination ID — returns 422 error

### FR-007: Application Site Boundary Lookup
**Description:** The system must fetch the application site boundary polygon from Cherwell's public ESRI ArcGIS planning register MapServer. The query uses the application reference number to look up the geometry via the ArcGIS REST API query endpoint, requesting output in WGS84 (EPSG:4326). The returned ESRI polygon geometry (rings array) must be converted to GeoJSON format (FeatureCollection with a Polygon feature). The polygon centroid must be calculated and used as the route origin point for cycling route assessments. The system must acknowledge in assessment output that the centroid is an approximation — the actual site entrance/access point may differ from the geometric centre, particularly for large or irregularly shaped sites.

**Examples:**
- Positive case: Application 21/03267/OUT returns a polygon with 19 coordinate pairs, centroid at approximately (-1.199, 51.954), stored as GeoJSON
- Positive case: Application 25/01178/REM returns a smaller polygon, centroid used as route origin for all destination assessments
- Edge case: Application reference not found in ArcGIS register (e.g., very old or pre-digital application) — site boundary lookup fails, route assessment skipped gracefully with warning, review continues without it
- Edge case: Large site (e.g., 200+ hectares) — centroid noted as "approximate origin; actual access point may vary significantly for this large site"

### FR-010: Site Boundary GeoJSON Storage and API
**Description:** The site boundary polygon fetched from ArcGIS must be converted to standard GeoJSON (RFC 7946) and stored alongside the review result. A new API endpoint must serve the GeoJSON for a given review. The GeoJSON feature properties must include the application reference, site address, area (from ESRI `STArea`), and a note about centroid accuracy.

**Examples:**
- Positive case: GET /api/v1/reviews/{id}/site-boundary returns a GeoJSON FeatureCollection with the site polygon, centroid point, and properties (application_ref, address, area_sqm)
- Positive case: The GeoJSON includes both the original polygon and a derived centroid point as separate features
- Edge case: Review has no site boundary (lookup failed) — endpoint returns 404 with error code `site_boundary_not_found`
- Edge case: GeoJSON is valid and can be loaded directly into mapping tools (Leaflet, Mapbox, QGIS)

### FR-011: MCP Server Authentication
**Description:** All MCP servers (the new cycle-route server and the three existing servers: cherwell-scraper, document-store, policy-kb) must support bearer token authentication. When an `MCP_API_KEY` environment variable is set, the server must require a valid `Authorization: Bearer <token>` header on all requests to `/sse`, `/messages`, and `/mcp` endpoints. The `/health` endpoint must remain unauthenticated. When `MCP_API_KEY` is not set, the server must operate without authentication (backward-compatible for internal Docker network use). This enables external clients (n8n, Claude Desktop, Claude Code) to connect securely.

**Examples:**
- Positive case: n8n MCP Client Tool connects to `https://bbug-planning.mmsio.com/mcp/cherwell-scraper/sse` with `Authorization: Bearer sk-mcp-...` header — connection succeeds, tools are listed
- Positive case: Claude Desktop connects to the cycle-route MCP server with bearer token — tools discoverable and callable
- Positive case: Internal worker connects without auth header when `MCP_API_KEY` is not set — backward compatible
- Negative case: External request with no auth header when `MCP_API_KEY` is set — returns 401 Unauthorized
- Negative case: External request with invalid bearer token — returns 401 Unauthorized
- Edge case: `/health` endpoint always returns 200 regardless of auth configuration

### FR-012: MCP Server Streamable HTTP Transport
**Description:** All MCP servers must support Streamable HTTP transport in addition to the existing SSE transport. Streamable HTTP is the current MCP standard (SSE is deprecated) and is required for compatibility with Claude web, Claude Desktop, and n8n. The server must expose a single `/mcp` endpoint that handles both GET (for SSE stream initiation) and POST (for message exchange) as per the MCP Streamable HTTP specification. The existing `/sse` and `/messages` endpoints must continue to work for backward compatibility with the internal worker.

**Examples:**
- Positive case: Claude Desktop connects via Streamable HTTP to `https://bbug-planning.mmsio.com/mcp/policy-kb/mcp` — tools listed and callable
- Positive case: n8n MCP Client Tool connects via SSE to `/sse` endpoint — continues to work
- Positive case: Internal worker connects via SSE to `/sse` endpoint — no change required
- Edge case: Client sends request to `/mcp` without proper MCP headers — returns appropriate error

### FR-013: MCP Server External Exposure
**Description:** All MCP servers must be accessible externally via the existing Traefik reverse proxy with HTTPS. Each server must be exposed under a path prefix on the existing `bbug-planning.mmsio.com` domain: `/mcp/cherwell-scraper/`, `/mcp/document-store/`, `/mcp/policy-kb/`, and `/mcp/cycle-route/`. Traefik routing rules must strip the prefix before forwarding to the container. The production docker-compose must connect MCP server containers to the `traefik_default` external network and add appropriate Traefik labels.

**Examples:**
- Positive case: `https://bbug-planning.mmsio.com/mcp/cycle-route/mcp` routes to cycle-route-mcp container port 3004
- Positive case: `https://bbug-planning.mmsio.com/mcp/cherwell-scraper/sse` routes to cherwell-scraper-mcp container port 3001
- Positive case: Internal worker continues to use `http://cherwell-scraper-mcp:3001/sse` via Docker network — no change
- Edge case: Traefik handles TLS termination — MCP servers only serve plain HTTP internally

### FR-008: Integration with Review Output
**Description:** Route assessment results must be included in the structured review output as a new `route_assessments` field alongside existing fields (aspects, policy_compliance, etc.). Each assessment includes the destination name, distance, overall score, provision breakdown, key issues, and S106 suggestion. The assessment data must also be available to the LLM review generation phase as evidence context.

**Examples:**
- Positive case: ReviewResponse includes `route_assessments` array with one entry per assessed destination
- Positive case: The full_markdown review includes a "Cycle Route Assessment" section with findings
- Edge case: Route assessment failed for all destinations — `route_assessments` is empty array, review still completes

### FR-009: S106 Funding Suggestions
**Description:** Based on identified route issues, the system must generate specific S106 contribution suggestions for off-site cycle infrastructure improvements. Each suggestion must reference the specific route issue it addresses and provide a brief justification citing LTN 1/20 or local policy.

**Examples:**
- Positive case: Issue "40mph road with no cycle provision (500m)" generates S106 suggestion "Segregated cycleway along Buckingham Road (500m) — required by LTN 1/20 Table 4-1 for speeds above 30mph"
- Positive case: Issue "unlit shared-use path" generates S106 suggestion "Lighting upgrade on shared-use path between site and Bicester North station"
- Edge case: No issues found — no S106 suggestions for off-site improvements (S106 section notes routes are adequate)

---

## Non-Functional Requirements

### NFR-001: Route Assessment Latency
**Category:** Performance
**Description:** The complete route assessment (site boundary lookup + routing + infrastructure analysis + scoring) for all default destinations must complete within a reasonable time, given it involves external API calls to ArcGIS, OSRM, and Overpass.
**Acceptance Threshold:** Route assessment for 3 destinations completes within 30 seconds
**Verification:** Testing (integration test measuring wall-clock time) and Observability (duration logged per assessment)

### NFR-002: External API Resilience
**Category:** Reliability
**Description:** The route assessment must handle external API failures (ArcGIS, OSRM, Overpass) gracefully. If any external API is unavailable, the assessment for that route must fail individually without blocking other routes or the wider review. If the ArcGIS boundary lookup fails, the entire route assessment phase is skipped.
**Acceptance Threshold:** Individual route failure does not prevent assessment of remaining destinations; review completes even if all route assessments fail
**Verification:** Testing (mock API failures and verify graceful degradation)

### NFR-003: Rate Limiting for External APIs
**Category:** Reliability
**Description:** OSRM and Overpass calls must be rate-limited to avoid overloading public instances. ArcGIS calls are limited (one per review for boundary lookup). All external API calls must include an appropriate User-Agent header.
**Acceptance Threshold:** Clear User-Agent header on all external requests; no burst of concurrent requests to any single external service
**Verification:** Code review

### NFR-004: Scoring Transparency
**Category:** Maintainability
**Description:** The LTN 1/20 scoring algorithm must be implemented with clear, auditable scoring factors. Each factor's contribution to the overall score must be traceable, so that scoring can be reviewed and adjusted.
**Acceptance Threshold:** Score breakdown shows individual factor contributions; scoring weights are defined as named constants
**Verification:** Code review and Testing (known route scenarios produce expected scores)

### NFR-005: Backward Compatibility
**Category:** Reliability
**Description:** Adding route assessment to the review pipeline must not break existing reviews. The new phase must be optional — if it fails entirely, the review proceeds without route data. Existing API responses must remain valid (route_assessments is an optional field). MCP server auth must be opt-in — when `MCP_API_KEY` is not set, servers behave exactly as before with no auth required.
**Acceptance Threshold:** All existing tests pass without modification; reviews complete when route assessment is unavailable; internal worker operates without auth configuration
**Verification:** Testing (full test suite passes; review without route MCP server succeeds; MCP servers work with and without auth configured)

### NFR-006: MCP Auth Security
**Category:** Security
**Description:** Bearer tokens for MCP server auth must be configured via environment variables, never hardcoded. Tokens must be compared using constant-time comparison to prevent timing attacks. Failed authentication attempts must be logged at WARNING level with the client IP. Auth must be enforced on all MCP protocol endpoints (`/sse`, `/messages`, `/mcp`) but not on `/health`.
**Acceptance Threshold:** No auth bypass on protected endpoints; no timing side-channels; failed attempts logged
**Verification:** Testing (auth middleware unit tests) and Code review

---

## Open Questions

None. Requirements have been clarified through discussion:
- Destinations: Both Bicester North and Bicester Village train stations, plus Manorsfield Road bus station
- Data source: External APIs (OSRM for routing, Overpass for infrastructure data) — not a local MCP-hosted OSM database
- Site location: Cherwell's ESRI ArcGIS planning register for site boundary polygon (replaces Nominatim geocoding)
- Route origin: Polygon centroid used as approximation, with caveat that entrance location may differ
- MCP server auth: Bearer token authentication (works with n8n and Claude Desktop/Code). OAuth 2.1 for full Claude web integration is a nice-to-have for later
- MCP transport: Streamable HTTP (current standard) alongside SSE (deprecated but still needed for internal worker)

---

## Appendix

### Glossary
- **OSRM**: Open Source Routing Machine — calculates cycling routes between coordinates
- **Overpass API**: Queries OpenStreetMap data for infrastructure attributes (tags) along ways
- **ESRI ArcGIS MapServer**: Cherwell's public geospatial service hosting planning application site boundary polygons
- **EPSG:27700**: British National Grid coordinate system used natively by the ArcGIS service
- **EPSG:4326 / WGS84**: Standard GPS coordinate system (latitude/longitude) used by GeoJSON and web mapping
- **GeoJSON**: RFC 7946 standard format for encoding geographic data structures (points, polygons, etc.)
- **Centroid**: The geometric centre of a polygon — used as route origin approximation
- **LTN 1/20**: Local Transport Note 1/20 "Cycle Infrastructure Design" — UK government guidance document defining design standards for cycling infrastructure
- **S106**: Section 106 of the Town and Country Planning Act 1990 — legal agreements between developers and local authorities, often funding off-site infrastructure improvements
- **Provision type**: The type of cycling infrastructure on a road segment — segregated cycleway, shared-use path, on-road cycle lane, advisory lane, or no provision
- **Cycle Quality Score**: A 0-100 numeric score derived from LTN 1/20 principles assessing route suitability for cycling
- **Streamable HTTP**: The current MCP transport standard (replacing SSE) — uses a single HTTP endpoint for bidirectional communication
- **SSE (Server-Sent Events)**: The legacy MCP transport — still supported but deprecated in the MCP specification
- **Bearer token**: An HTTP authentication scheme where the client sends `Authorization: Bearer <token>` header
- **MCP_API_KEY**: Environment variable holding the shared secret for MCP server authentication

### ArcGIS API Details

The Cherwell planning register exposes site boundaries via an ESRI ArcGIS REST API:

**Base endpoint:** `https://utility.arcgis.com/usrsvcs/servers/3b969cb8886849d993863e4c913c82fc/rest/services/Public_Map_Services/Cherwell_Public_Planning_Register/MapServer/0/query`

**Query by application reference:**
- Parameter: `where=DLGSDST.dbo.Planning_ArcGIS_Link_Public.application_number='21/03267/OUT'`
- Parameter: `f=json` (JSON response format)
- Parameter: `returnGeometry=true`
- Parameter: `outSR=4326` (output in WGS84 for direct GeoJSON conversion)
- Parameter: `outFields=*` (all attributes)

**Response format:** ESRI JSON with `features[].geometry.rings` containing coordinate arrays and `features[].attributes` containing application metadata (APPLICATION_REF, location, proposal, decision, area, perimeter).

**Coordinate conversion:** Requesting `outSR=4326` returns rings as `[longitude, latitude]` pairs in WGS84, avoiding the need for manual EPSG:27700 → WGS84 reprojection. The rings array maps directly to a GeoJSON Polygon's `coordinates` field.

### References
- [LTN 1/20 Cycle Infrastructure Design](https://www.gov.uk/government/publications/cycle-infrastructure-design-ltn-120) — Primary scoring reference
- [OSRM API documentation](https://project-osrm.org/docs/v5.24.0/api/)
- [Overpass API documentation](https://wiki.openstreetmap.org/wiki/Overpass_API)
- [Cherwell ArcGIS MapServer](https://utility.arcgis.com/usrsvcs/servers/3b969cb8886849d993863e4c913c82fc/rest/services/Public_Map_Services/Cherwell_Public_Planning_Register/MapServer/0) — Site boundary data source
- [GeoJSON RFC 7946](https://datatracker.ietf.org/doc/html/rfc7946) — GeoJSON standard
- [Bicester LCWIP](https://www.oxfordshire.gov.uk/) — Local Cycling and Walking Infrastructure Plan for Bicester
- [MCP Streamable HTTP Transport](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports#streamable-http) — Current MCP transport specification
- [MCP Authorization](https://modelcontextprotocol.io/docs/tutorials/security/authorization) — MCP auth specification
- [Claude remote MCP servers](https://support.claude.com/en/articles/11503834-building-custom-connectors-via-remote-mcp-servers) — Claude web MCP connector requirements
- [n8n MCP Client Tool](https://docs.n8n.io/integrations/builtin/cluster-nodes/sub-nodes/n8n-nodes-langchain.toolmcp/) — n8n MCP client configuration
- [existing MCP server pattern](../foundation-api/design.md) — SSE transport, tool definitions, Pydantic schemas
- [structured-review-output specification](../structured-review-output/specification.md) — Review output schema this extends

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.2 | 2026-02-14 | Claude Opus 4.6 | Add MCP server auth, Streamable HTTP transport, and external exposure (FR-011, FR-012, FR-013, NFR-006). Applies to all MCP servers (new + existing) |
| 1.1 | 2026-02-14 | Claude Opus 4.6 | Replace Nominatim geocoding with ESRI ArcGIS site boundary lookup; add GeoJSON storage and API endpoint (FR-007 rewritten, FR-010 added) |
| 1.0 | 2026-02-14 | Claude Opus 4.6 | Initial specification |
