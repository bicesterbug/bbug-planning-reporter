# Spike: Routing Engine Alternatives (Valhalla / BRouter)

**Date:** 2026-02-20
**Status:** Complete
**Author:** Claude

---

## Context

The cycle route assessment MCP server currently uses the **OSRM public demo server** (`https://router.project-osrm.org/route/v1/bike`) for bicycle routing. The server provides route geometry and distance/duration, but nothing else — all infrastructure analysis comes from a separate Overpass API query that fetches OSM way tags along the route corridor.

### Current Architecture

```
Origin/Destination coords
        │
        ▼
   OSRM public API  ──►  Route geometry + distance + duration
        │
        ▼
   Overpass API      ──►  OSM way tags within 20m buffer of route
        │
        ▼
   Infrastructure    ──►  Classify provision per way (segregated/shared_use/
   Analyser               on_road_lane/advisory_lane/none)
        │
        ▼
   LTN 1/20 Scorer  ──►  Score 0-100 with 6 component breakdown
```

### Problems With Current Setup

1. **No SLA** — OSRM's public demo server is unreliable (504 timeouts observed in production)
2. **Single bike profile** — cannot tune for comfort vs speed at request time
3. **No driving distance** — directness score defaults to half-points (4.5/9) because we can't compute a driving route for comparison
4. **No elevation awareness** — routing ignores hills entirely
5. **Coarse infrastructure matching** — Overpass buffer query returns all ways within 20m of the route, not the actual ways the route uses. Per-way distances are approximated by dividing total route distance equally among ways
6. **Two external API dependencies** — both OSRM and Overpass are uncontrolled public services

---

## Option A: Valhalla

### What It Is

Open-source C++ routing engine (originally Mapzen, now community-maintained). Uses **tiled graph data** with **dynamic costing** — routing profiles are evaluated at request time from the same tile set, unlike OSRM which bakes profiles at build time.

### Bicycle Costing Model

Valhalla's bicycle costing has runtime-tuneable parameters:

| Parameter | Range | What It Does |
|-----------|-------|-------------|
| `bicycle_type` | Road/Hybrid/Cross/Mountain | Determines default speed and surface tolerance |
| `use_roads` | 0–1 | 0 = strongly prefer cycleways, 1 = comfortable on roads |
| `use_hills` | 0–1 | 0 = strongly avoid hills, 1 = experienced climber |
| `avoid_bad_surfaces` | 0–1 | Avoidance of poor surfaces relative to bike type |
| `shortest` | bool | When true, optimise for shortest distance |
| `cycling_speed` | KPH | Override default speed |

This means we can request **two meaningfully different routes** from the same deployment:
- **Shortest**: `{"shortest": true}` — direct route, may use busy roads
- **Safest**: `{"use_roads": 0.1, "avoid_bad_surfaces": 0.8}` — prefers cycleways and good surfaces

Currently we rely on OSRM `alternatives=true` which returns geometrically different routes but optimised for the same cost function — the "alternatives" aren't comfort-optimised, just geographically distinct.

### Driving Distance (Directness Score Fix)

Valhalla serves multiple costing profiles from the same tile set. A single deployment handles:
- `costing: "bicycle"` — cycling route
- `costing: "auto"` — driving route

This directly fixes our directness score gap. Currently `_score_directness()` gives half-points (4.5/9) when no driving distance is available. With Valhalla we'd get an actual driving distance for every assessment.

### Edge-Level Attributes

The `/route` endpoint returns maneuver-level data (street names, distance, duration) but NOT per-edge attributes like surface type or cycle infrastructure.

However, the `/trace_attributes` endpoint returns rich per-edge data when you feed it a route shape:
- `edge.road_class`, `edge.surface`, `edge.cycle_lane`, `edge.bicycle_network`
- `edge.speed_limit`, `edge.length`, `edge.way_id`
- `edge.weighted_grade`, `edge.traffic_signal`

**Practical pattern**: Route with `/route`, then call `/trace_attributes` on the result shape to get per-edge infrastructure. This replaces our Overpass query entirely — the attributes come from Valhalla's own tile data which is derived from the same OSM source.

### Elevation

Valhalla integrates SRTM elevation data. It penalises hills based on the `use_hills` parameter, adjusting both route selection and duration estimates. Requires `build_elevation=True` when building tiles.

### Self-Hosting

| Aspect | Detail |
|--------|--------|
| Docker image | `ghcr.io/valhalla/valhalla-scripted:latest` |
| OSM data | Oxfordshire PBF: **25 MB**; England PBF: **1.5 GB** |
| Tile size | Oxfordshire: ~50–100 MB; England: ~2–3 GB |
| Build time | Oxfordshire: <5 min; England: ~30 min |
| Runtime RAM | Oxfordshire: <512 MB; England: ~1–2 GB |
| Build RAM | Oxfordshire: <1 GB; England: ~4 GB |
| Port | 8002 (default) |

```bash
# Minimal setup
mkdir valhalla_data
wget -P valhalla_data/ https://download.geofabrik.de/europe/united-kingdom/england/oxfordshire-latest.osm.pbf

docker run -dt \
  -v $PWD/valhalla_data:/custom_files \
  -p 8002:8002 \
  -e build_elevation=True \
  --name valhalla \
  ghcr.io/valhalla/valhalla-scripted:latest
```

### Public Demo Server

FOSSGIS runs a public instance at `https://valhalla1.openstreetmap.de`:
- Global coverage, rebuilt every ~2 days
- Rate limit: 1 req/user/sec, 100 req/sec total
- **Not for production** — fair-use demo only

### API Format

Request is JSON POST. Response uses **encoded polyline (6-digit precision)**, not GeoJSON. Supports `alternates: N` for alternative routes. Can also output OSRM-compatible format or GPX.

---

## Option B: BRouter

### What It Is

Open-source Java routing engine purpose-built for cycling. Uses a unique **scripted cost function** system rather than pre-computed contraction hierarchies. Profiles are full scripts, not just parameter configs — you can express conditional logic like "an ungraded track with asphalt surface is fine, but a grade-5 track with asphalt should still be penalised."

### Profile System

BRouter ships with ~15 profiles including:

| Profile | Purpose |
|---------|---------|
| `trekking` | Default balanced cycling (commuting/touring) |
| `fastbike` | Road cycling / speed |
| `fastbike-verylowtraffic` | Fast but strongly avoids traffic |
| `gravel` | Mixed-surface cycling |
| `mtb` | Mountain biking |
| `shortest` | Shortest distance |

Custom profiles are `.brf` scripts with per-way cost variables (`costfactor`, `turncost`, `initialcost`, `uphillcostfactor`). The scripting language supports conditionals, boolean logic, and multi-variable correlations. A community of third-party profiles exists.

### Per-Segment Infrastructure Data

This is BRouter's standout feature. The GeoJSON response includes a `messages` array with per-segment data including:
- **Full OSM way tags** for every segment (e.g., `highway=tertiary surface=asphalt cycleway=lane`)
- **Full OSM node tags** (barriers, crossings)
- Distance, elevation, cost components per segment

This means BRouter returns the exact infrastructure data we currently fetch from Overpass as a separate query. The `WayTags` field contains `highway=*`, `surface=*`, `cycleway=*`, `smoothness=*` etc. for each segment of the actual route.

### Elevation

BRouter uses SRTM v4.1 with sophisticated noise filtering. It reports "filtered ascend" that removes GPS/SRTM artifacts, giving realistic climbing figures. Configurable `uphillcost`/`downhillcost` per metre of elevation change.

### Driving Distance

BRouter has experimental `car-vario` profiles but **turn restrictions are not implemented** for car routing. Not suitable for production driving distance calculations. We'd still need a separate engine (OSRM or Valhalla) for the directness score.

### Self-Hosting

| Aspect | Detail |
|--------|--------|
| Docker image | Build from repo, or community images (`eoger/brouter`, `kotaicode/brouter-server`) |
| Data format | Pre-built `.rd5` segment files (NOT raw OSM PBF) |
| UK data | ~300 MB (3 segment tiles: W10_N50, E0_N50, E5_N50) |
| Runtime RAM | **128 MB** — dramatically lighter than Valhalla or OSRM |
| Port | 17777 (default) |
| Data updates | Weekly pre-built segments from `https://brouter.de/brouter/segments4/` |

```bash
# Download segment files for UK coverage
mkdir segments4 profiles2
wget -P segments4/ https://brouter.de/brouter/segments4/W10_N50.rd5
wget -P segments4/ https://brouter.de/brouter/segments4/E0_N50.rd5

# Copy profiles from repo
cp brouter/misc/profiles2/*.brf profiles2/

docker run -dt \
  -v $PWD/segments4:/segments4 \
  -v $PWD/profiles2:/profiles2 \
  -p 17777:17777 \
  --name brouter \
  eoger/brouter:latest
```

### Public Demo Server

`https://brouter.de/brouter?lonlats=...&profile=trekking&format=geojson`
- No documented rate limits but community-run, not for production
- Web UI at `https://brouter.de/brouter-web/` and `https://bikerouter.de`

### API Format

Simple GET request with query parameters. Returns native GeoJSON with 3D coordinates (lon, lat, elevation). Alternatives via `alternativeidx=0..3` (separate requests per alternative).

---

## Comparison Matrix

| Capability | OSRM (current) | Valhalla | BRouter |
|-----------|----------------|----------|---------|
| **Cycling profile tuning at runtime** | None | 6+ parameters per request | Full scripting language |
| **Shortest vs safest from same data** | No | Yes (costing_options) | Yes (different profiles) |
| **Alternative routes** | Yes (`alternatives=true`) | Yes (`alternates: N`) | Yes (`alternativeidx=0..3`) |
| **Driving distance (same deployment)** | No | **Yes** (`costing: "auto"`) | No (experimental, no turn restrictions) |
| **Per-edge infrastructure attributes** | No | Yes (via `/trace_attributes`) | **Yes (inline in response)** |
| **Elevation-aware routing** | No | Yes (SRTM, `use_hills`) | Yes (SRTM v4.1, filtered) |
| **Surface-aware routing** | No | Yes (`avoid_bad_surfaces`) | Yes (20+ surface types with configurable costs) |
| **Cycle network preference (NCN/RCN/LCN)** | No | Yes (parsed, used in costing) | Yes (configurable preference) |
| **Barrier/dismount handling** | No | Limited | Yes (configurable node costs) |
| **Self-host RAM (Oxfordshire)** | ~200 MB | ~512 MB | **128 MB** |
| **Self-host disk** | ~100 MB | ~100 MB (+ elevation) | ~300 MB (UK tiles) |
| **Response format** | GeoJSON | Encoded polyline (6-digit) | GeoJSON with elevation |
| **Eliminates Overpass dependency** | No | Partially (via trace_attributes) | **Yes (tags inline)** |
| **Docker image** | `osrm/osrm-backend` | `ghcr.io/valhalla/valhalla-scripted` | Community images |
| **Language** | C++ | C++ | Java |
| **Query speed** | Fastest (~1ms) | Fast (~5–20ms) | Moderate (~20–100ms) |

---

## Impact on Route Assessment Feature

### What Valhalla Would Enable

1. **Real directness scores** — Auto costing gives driving distance; our `_score_directness()` currently gives 4.5/9 by default. With actual ratios, a direct cycling route scores 9/9 and an indirect one drops to 0.9/9. This is **+4.5 or -3.6 points** of scoring accuracy.

2. **Comfort-aware routing** — Request `use_roads=0.1` for a genuinely safe route vs `shortest=true` for the direct route. Currently both OSRM alternatives optimise the same cost function, so "safest" is really just "a different geometry."

3. **Hill awareness** — Routes through Bicester are flat, but outlying destinations (Launton, Stratton Audley) have hills. `use_hills` would find flatter alternatives.

4. **Eliminate one external dependency** — Self-hosted Valhalla replaces the unreliable OSRM public server. Still need Overpass for infrastructure analysis unless we adopt the `/trace_attributes` two-call pattern.

5. **Per-edge attributes via trace_attributes** — Could partially replace Overpass by getting `surface`, `road_class`, `cycle_lane`, `speed_limit` from Valhalla's tile data. However, this requires a second API call per route and may not cover all the tags we currently extract from Overpass (e.g., `cycleway:left`, barrier nodes).

### What BRouter Would Enable

1. **Inline infrastructure data** — Every segment of the route response includes full OSM way tags. This **eliminates the Overpass API dependency entirely**. No more buffer-based approximation — we get the exact tags for the exact ways the route uses.

2. **Precise per-segment distances** — BRouter reports actual distance per segment, not our current approximation of dividing total distance equally among ways. This directly improves `_score_segregation()` accuracy (36/100 points of the total score).

3. **Barrier and crossing detection inline** — Node tags (barriers, crossings) are included in the response. Our `analyse_transitions()` currently queries Overpass for these; BRouter provides them with the route.

4. **Surface-aware routing** — BRouter's profile scripts have granular surface handling (20+ types). Routes would prefer good surfaces, and the response would confirm what surface each segment actually has.

5. **Custom cycling advocacy profile** — We could write a `.brf` profile tuned specifically for UK cycling infrastructure assessment, weighting cycle lanes, LTN 1/20 preferred infrastructure types, and penalising known hostile junction patterns.

### What Neither Engine Replaces

- **ArcGIS site boundary lookup** — still needed to find the planning application polygon
- **LTN 1/20 scoring logic** — still our custom scorer, just fed better data
- **Issue identification** — still our logic, but with more accurate segment data

---

## Trade-offs

### Valhalla Trade-offs

| Pro | Con |
|-----|-----|
| Driving distance fixes directness score | Response is encoded polyline, not GeoJSON — need decode step |
| Comfort-tuneable bicycle routing | Per-edge attributes require second call (`/trace_attributes`) |
| Elevation-aware | Larger footprint than BRouter (~512 MB vs 128 MB) |
| Well-maintained, active community | Still need Overpass for complete tag coverage (cycleway:left, etc.) |
| Multiple profiles from one deployment | Tile rebuild needed when OSM data updates |

### BRouter Trade-offs

| Pro | Con |
|-----|-----|
| Inline way tags eliminates Overpass | **No production-quality driving distance** — still need OSRM or Valhalla for directness score |
| Exact per-segment distances | Community Docker images, no official image |
| Lightest resource footprint (128 MB RAM) | Alternatives require separate requests (not in one call) |
| Full scripting for custom profiles | Java-based — different stack from our Python/C++ ecosystem |
| Weekly pre-built segment files | Slower query speed (~20-100ms vs ~1-5ms) |
| Inline barrier/crossing node data | Less well-known, smaller community than Valhalla |

### Hybrid: Valhalla + BRouter

A combination is worth considering:
- **Valhalla** for driving distance (directness score) and comfort-aware route generation
- **BRouter** for infrastructure-rich route assessment with inline OSM tags

Both are lightweight enough to self-host on the production server. Combined footprint: ~640 MB RAM, ~400 MB disk for Oxfordshire.

However, this adds two services to manage instead of one.

---

## Recommendation

### Short-term: Self-host Valhalla (biggest bang for effort)

**Why:** Fixes the three most impactful problems:
1. Eliminates unreliable public OSRM dependency (production 504s)
2. Provides driving distance for directness scoring (+/- 4.5 points accuracy)
3. Enables comfort-aware shortest vs safest routing

The Oxfordshire extract is 25 MB, builds in <5 minutes, runs in <512 MB. Fits within the production server's existing 512 MB cycle-route-mcp memory limit if we add Valhalla as a sidecar container.

**Migration path:** Replace `OSRM_URL` calls in `server.py` with Valhalla `/route` calls. Keep Overpass for infrastructure analysis initially. Add a separate `auto` route request for driving distance. Two-call pattern (route + trace_attributes) to replace Overpass can be a follow-up.

### Medium-term: Add BRouter for infrastructure data

**Why:** Eliminates Overpass dependency and gives exact per-segment infrastructure data. The inline OSM tags are more accurate than our 20m buffer Overpass query.

**Migration path:** Add BRouter as a second routing sidecar. Use it specifically for infrastructure analysis — feed it the same origin/destination, get back per-segment way tags, replace `parse_overpass_ways()` with BRouter tag parsing. Keep Valhalla for route geometry, driving distance, and comfort tuning.

### Long-term: BRouter-only with Valhalla driving distance

If BRouter proves reliable, it could replace both OSRM and Overpass for cycling route assessment. Valhalla would only be needed for driving distance calculations (or we accept half-point directness scores and drop Valhalla entirely).

---

## Next Steps

If proceeding with Valhalla self-hosting:

1. Write SDD specification for `valhalla-routing-engine` feature
2. Add Valhalla container to docker-compose (dev and deploy)
3. Modify `server.py` to call Valhalla `/route` instead of OSRM
4. Add driving distance request (`costing: "auto"`) for directness scoring
5. Update `_score_directness()` to use real driving distance
6. Test with Oxfordshire extract against known routes
