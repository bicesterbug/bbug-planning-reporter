# Cycle Route MCP Server -- API Reference

## Overview

The Cycle Route MCP server (port 3004) provides two tools for assessing cycling infrastructure quality between development sites and key destinations. It looks up planning application site boundaries from Cherwell's ArcGIS register, calculates cycling routes via OSRM, classifies infrastructure from OpenStreetMap data, scores routes against LTN 1/20 design standards, detects deficiencies, and generates S106 developer contribution suggestions. The server exposes tools over both SSE and Streamable HTTP MCP transports, with optional bearer token authentication.

---

## Transport & Authentication

### Endpoints

| Path | Method | Auth Required | Description |
|------|--------|---------------|-------------|
| `/health` | GET | No | Health check. Returns `{"status": "ok"}`. |
| `/sse` | GET | Yes | SSE transport (legacy). Initiates server-sent events connection. |
| `/messages/` | POST | Yes | SSE message posting endpoint (used with `/sse`). |
| `/mcp` | GET, POST, DELETE | Yes | Streamable HTTP transport (current MCP standard). |

### Authentication

Authentication is enforced by `MCPAuthMiddleware` when `MCP_API_KEY` is set.

- All requests to `/sse`, `/messages/`, and `/mcp` must include `Authorization: Bearer <token>`.
- `/health` is always exempt.
- When `MCP_API_KEY` is unset or empty, authentication is disabled entirely.
- Token comparison uses `hmac.compare_digest` for constant-time evaluation (timing attack prevention).
- Failed auth attempts are logged at WARNING level with client IP, endpoint, and method.

**Error responses (401):**

| Condition | Error message |
|-----------|---------------|
| No `Authorization` header | `"Missing Authorization header"` |
| Wrong scheme (not `Bearer`) | `"Invalid Authorization header format. Expected: Bearer <token>"` |
| Token mismatch | `"Invalid bearer token"` |

All 401 responses return:
```json
{"error": {"code": "unauthorized", "message": "..."}}
```

---

## Tools

### `get_site_boundary`

Queries Cherwell's ArcGIS MapServer for a planning application's site boundary polygon, converts the ESRI JSON response to RFC 7946 GeoJSON, and computes a centroid point from the exterior ring vertices.

#### Input

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `application_ref` | string | Yes | Planning application reference (e.g., `"25/01178/REM"`, `"21/03267/OUT"`) |

#### ArcGIS Query

The tool queries the Cherwell Public Planning Register MapServer:

```
https://utility.arcgis.com/usrsvcs/servers/3b969cb8886849d993863e4c913c82fc/rest/services/Public_Map_Services/Cherwell_Public_Planning_Register/MapServer/0/query
```

| Parameter | Value |
|-----------|-------|
| `f` | `json` |
| `returnGeometry` | `true` |
| `outSR` | `4326` |
| `outFields` | `*` |
| `where` | `DLGSDST.dbo.Planning_ArcGIS_Link_Public.application_number='<ref>'` |

The endpoint URL is configurable via the `ARCGIS_PLANNING_URL` environment variable.

#### Output -- Success

```json
{
  "status": "success",
  "geojson": {
    "type": "FeatureCollection",
    "features": [
      {
        "type": "Feature",
        "geometry": { "type": "Polygon", "coordinates": [[[lon, lat], ...]] },
        "properties": {
          "application_ref": "25/01178/REM",
          "address": "Land To The North West Of, Graven Hill, Ambrosden",
          "area_sqm": 12345.6,
          "feature_type": "site_boundary"
        }
      },
      {
        "type": "Feature",
        "geometry": { "type": "Point", "coordinates": [lon, lat] },
        "properties": {
          "application_ref": "25/01178/REM",
          "address": "Land To The North West Of, Graven Hill, Ambrosden",
          "area_sqm": 12345.6,
          "feature_type": "centroid",
          "centroid_note": "Geometric centre of site boundary; actual entrance may differ"
        }
      }
    ]
  }
}
```

For large sites (>100,000 sqm), `centroid_note` reads: `"Approximate origin; actual access point may vary significantly for this large site"`.

#### Output -- Not Found

```json
{
  "status": "error",
  "error_type": "not_found",
  "message": "Application 25/99999/OUT not found in planning register"
}
```

#### Output -- Internal Error

```json
{
  "status": "error",
  "error_type": "internal_error",
  "message": "..."
}
```

#### Behaviour

- Requests WGS84 output (`outSR=4326`) so coordinates are in `[longitude, latitude]` order per RFC 7946.
- ESRI polygon rings are passed through directly to GeoJSON Polygon coordinates (exterior ring + any holes).
- Centroid is the arithmetic mean of exterior ring vertices (closing duplicate vertex excluded for closed rings).
- Attribute lookup tries three key prefixes in order: `DLGSDST.dbo.Planning_ArcGIS_Link_Public.<field>`, then `CORPGIS.MASTERGOV.DEF_Planning.<field>`, then `<field>` directly.
- Newlines in the address field are replaced with commas.
- If ArcGIS returns features but the geometry has no rings, the result is treated as not found.
- `area_sqm` is extracted from the ESRI `SHAPE.STArea()` attribute; `null` if absent.

---

### `assess_cycle_route`

Assesses cycling route quality between two geographic points. Calculates a route via OSRM, queries Overpass API for OSM infrastructure data along the route corridor, classifies each segment, scores the route against LTN 1/20, detects infrastructure issues, and generates S106 suggestions.

#### Input

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `origin_lon` | float | Yes | -- | Origin longitude (WGS84) |
| `origin_lat` | float | Yes | -- | Origin latitude (WGS84) |
| `destination_lon` | float | Yes | -- | Destination longitude (WGS84) |
| `destination_lat` | float | Yes | -- | Destination latitude (WGS84) |
| `destination_name` | string | No | `"Destination"` | Human-readable destination name |

#### Output -- Success

```json
{
  "status": "success",
  "destination": "Bicester North Station",
  "distance_m": 2450,
  "duration_minutes": 10.3,
  "provision_breakdown": {
    "segregated": 850.2,
    "shared_use": 400.0,
    "none": 1199.8
  },
  "segments": [
    {
      "way_id": 123456789,
      "provision": "segregated",
      "highway": "cycleway",
      "speed_limit": 0,
      "surface": "asphalt",
      "lit": true,
      "distance_m": 320.5,
      "name": "Example Cycle Path"
    }
  ],
  "score": {
    "score": 62,
    "rating": "amber",
    "breakdown": {
      "segregation": 22,
      "speed_safety": 15,
      "surface_quality": 12,
      "directness": 7,
      "junction_safety": 6
    },
    "max_points": {
      "segregation": 40,
      "speed_safety": 25,
      "surface_quality": 15,
      "directness": 10,
      "junction_safety": 10
    }
  },
  "issues": [
    {
      "location": "Buckingham Road (300m section)",
      "problem": "High-speed road with no cycle provision",
      "severity": "high",
      "suggested_improvement": "Segregated cycleway required (LTN 1/20 Table 4-1)"
    }
  ],
  "s106_suggestions": [
    {
      "issue_location": "Buckingham Road (300m section)",
      "improvement": "Segregated cycleway required (LTN 1/20 Table 4-1)",
      "justification": "Addresses identified deficiency: High-speed road with no cycle provision. S106 contribution towards off-site cycling infrastructure improvements is justified under Cherwell Local Plan Policy INF1 and NPPF paragraph 116.",
      "severity": "high"
    }
  ],
  "route_geometry": [[lon, lat], [lon, lat], ...]
}
```

When a route is under 200m, the `score` object includes an additional field:
```json
"short_route_note": "Short distance; walking may be preferable"
```

#### Output -- No Route

```json
{
  "status": "error",
  "error_type": "no_route",
  "message": "No cycling route found to Bicester North Station"
}
```

Returned when OSRM returns a status code other than `"Ok"` or an empty routes array.

#### Output -- No Infrastructure Data

```json
{
  "status": "success",
  "destination": "Bicester North Station",
  "distance_m": 1234,
  "duration_minutes": 5.2,
  "provision_breakdown": {},
  "score": {"score": 0, "rating": "red", "breakdown": {}},
  "issues": [],
  "s106_suggestions": [],
  "note": "No infrastructure data available along route"
}
```

Returned when OSRM finds a route but Overpass returns no way data for the corridor.

#### Behaviour

- Routes are calculated via OSRM using the bike profile with full GeoJSON geometry.
- OSRM URL format: `{OSRM_URL}/{origin_lon},{origin_lat};{dest_lon},{dest_lat}?overview=full&geometries=geojson&steps=false`.
- Route geometry coordinates are sampled to ~50 points (every Nth point, always including the last) for the Overpass query.
- Overpass queries use a 20m buffer around sampled points. Non-routable highway types (`proposed`, `construction`, `abandoned`, `razed`, `platform`) are filtered out.
- All outbound HTTP requests use a 20-second timeout and include `User-Agent: BBUGCycleRouteAssessment/1.0 (cycling-advocacy-tool)`.
- A 0.5-second delay is inserted between consecutive external API calls to avoid overloading public services.
- `provision_breakdown` maps each provision type to total distance in metres, rounded to 1 decimal place.
- `distance_m` is rounded to the nearest integer. `duration_minutes` is rounded to 1 decimal place.

---

## LTN 1/20 Scoring

The route scoring algorithm produces a 0-100 cycling quality score based on LTN 1/20 (Cycle Infrastructure Design) principles. The score is composed of five weighted factors, clamped to 0-100, and mapped to a RAG rating.

### Scoring Factors

| Factor | Max Points | Weight | Description |
|--------|------------|--------|-------------|
| Segregation | 40 | 40% | Proportion of route on segregated infrastructure |
| Speed Safety | 25 | 25% | Traffic speed on unsegregated sections |
| Surface Quality | 15 | 15% | Surface type across route |
| Directness | 10 | 10% | Ratio of cycling distance to driving distance |
| Junction Safety | 10 | 10% | Hostile junction penalty |

### Segregation (40 points)

Credit per provision type based on distance proportion:

| Provision | Credit |
|-----------|--------|
| `segregated` | 100% |
| `shared_use` | 70% |
| `on_road_lane` | 40% |
| `advisory_lane` | 0% (implicit) |
| `none` | 0% |

### Speed Safety (25 points)

Applied to unsegregated sections only. If there are no unsegregated sections, full points are awarded.

| Speed Limit | Credit |
|-------------|--------|
| <= 20 mph | 100% |
| 21-30 mph | 60% |
| 31-40 mph | 20% |
| > 40 mph | 0% |

### Surface Quality (15 points)

| Surface Category | Surfaces | Credit |
|------------------|----------|--------|
| Good | asphalt, paved, concrete, concrete:plates, paving_stones | 100% |
| Fair | compacted, fine_gravel | 60% |
| Unknown | (no tag / unknown) | 50% |
| Poor | all others (gravel, dirt, grass, mud, sand, ground, etc.) | 20% |

### Directness (10 points)

Ratio of cycling distance to driving distance:

| Detour Ratio | Credit |
|--------------|--------|
| <= 1.1x | 100% |
| <= 1.3x | 70% |
| <= 1.5x | 40% |
| > 1.5x | 10% |

When no driving distance is available for comparison, half points (5/10) are awarded.

### Junction Safety (10 points)

Counts "hostile junctions": segments on primary, secondary, trunk, or tertiary roads with no cycling provision and speed >= 30 mph.

| Hostile Junctions | Credit |
|-------------------|--------|
| 0 | 100% |
| 1-2 | 60% |
| 3-5 | 30% |
| > 5 | 0% |

### RAG Rating Thresholds

| Score Range | Rating |
|-------------|--------|
| >= 70 | `green` |
| >= 40 | `amber` |
| < 40 | `red` |

---

## Infrastructure Classification

Each route segment is classified by cycling provision type based on OSM way tags returned from the Overpass API.

### Provision Types

| Provision | Tag Conditions |
|-----------|---------------|
| `segregated` | `highway=cycleway`; OR path/footway/bridleway with `bicycle=designated` and NOT `foot=designated`/`yes`; OR any road with `cycleway`/`cycleway:left`/`cycleway:right`/`cycleway:both` = `track` or `separate` |
| `shared_use` | Path/footway/bridleway with both `bicycle` and `foot` = `designated`/`yes`; OR path with `bicycle=yes`; OR designation containing `"shared"` |
| `on_road_lane` | Any road with `cycleway`/`cycleway:left`/`cycleway:right`/`cycleway:both` = `lane` |
| `advisory_lane` | Any road with `cycleway`/`cycleway:left`/`cycleway:right`/`cycleway:both` = `shared_lane` or `share_busway` |
| `none` | No cycle-specific tagging detected |

### Speed Limit Defaults

When the `maxspeed` tag is absent, UK defaults apply by highway classification:

| Highway Type | Default Speed (mph) |
|-------------|---------------------|
| motorway | 70 |
| trunk, primary, secondary | 60 |
| tertiary, unclassified, residential | 30 |
| living_street, service | 20 |
| cycleway, path, footway, bridleway, track, pedestrian | 0 |

Speed limits are parsed from the `maxspeed` tag value (e.g., `"30 mph"` becomes `30`).

### Surface

Taken directly from the `surface` OSM tag. Defaults to `"unknown"` when absent.

### Lighting

| Tag Value | Result |
|-----------|--------|
| `lit=yes` | `true` |
| `lit=no` | `false` |
| absent / other | `null` |

---

## Issue Detection

The issue detector examines each route segment and identifies cycling infrastructure deficiencies. Each issue includes a location, problem description, severity, and suggested improvement citing LTN 1/20 guidance.

### Issue Types

| Condition | Severity | Problem | Suggested Improvement |
|-----------|----------|---------|----------------------|
| `provision=none` AND `speed_limit >= 40` | `high` | High-speed road with no cycle provision | Segregated cycleway required (LTN 1/20 Table 4-1) |
| `provision=none` AND `speed_limit >= 30` AND `highway` in (primary, secondary, tertiary, trunk) | `medium` | Classified road at 30mph with no cycle provision | On-road cycle lane or segregated cycleway (LTN 1/20) |
| `surface` in (gravel, dirt, grass, mud, sand, ground) | `medium` | Poor surface quality | Resurface to sealed tarmac/asphalt (LTN 1/20 para 5.5) |
| `lit=false` AND `provision` in (segregated, shared_use) | `low` | Unlit segregated/shared-use path | Install lighting (LTN 1/20 para 10.5) |

### Issue Object

| Field | Type | Description |
|-------|------|-------------|
| `location` | string | Road/path name with distance (e.g., `"Buckingham Road (300m section)"`) |
| `problem` | string | Human-readable description of the deficiency |
| `severity` | string | `"high"`, `"medium"`, or `"low"` |
| `suggested_improvement` | string | Recommended fix with LTN 1/20 reference |

When no issues are detected, an empty array is returned.

---

## S106 Suggestions

S106 developer contribution suggestions are generated from identified route issues. Only `high` and `medium` severity issues produce suggestions. Low severity issues are excluded.

### Generation Rules

1. Iterate over all detected issues.
2. Skip any issue with severity `"low"`.
3. For each remaining issue, create a suggestion using the issue's location, improvement, and severity.
4. Generate the justification from the template below.

### Justification Template

```
Addresses identified deficiency: <problem>. S106 contribution towards off-site cycling
infrastructure improvements is justified under Cherwell Local Plan Policy INF1 and NPPF
paragraph 116.
```

Where `<problem>` is the `problem` field from the source issue.

### S106 Suggestion Object

| Field | Type | Description |
|-------|------|-------------|
| `issue_location` | string | Location from the source issue |
| `improvement` | string | Suggested improvement from the source issue |
| `justification` | string | Policy justification (Cherwell LP INF1 + NPPF para 116) |
| `severity` | string | `"high"` or `"medium"` |

When no issues exist or all issues are low severity, an empty array is returned.

---

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CYCLE_ROUTE_PORT` | No | `3004` | Server listen port |
| `MCP_API_KEY` | No | (unset) | Bearer token for authentication. When unset, auth is disabled. |
| `ARCGIS_PLANNING_URL` | No | Cherwell MapServer URL | ArcGIS REST API query endpoint for site boundary lookup |
| `OSRM_URL` | No | `https://router.project-osrm.org/route/v1/bike` | OSRM cycling route endpoint |

### Server Defaults

| Setting | Value |
|---------|-------|
| Host | `0.0.0.0` |
| HTTP timeout | 20 seconds (all outbound API calls) |
| User-Agent | `BBUGCycleRouteAssessment/1.0 (cycling-advocacy-tool)` |
| Rate limit delay | 0.5 seconds between consecutive external API calls |
| Memory limit | 512 MB (Docker container) |
| Overpass sample size | ~50 coordinate points (route geometry downsampled) |
| Overpass buffer | 20 metres around sampled points |
