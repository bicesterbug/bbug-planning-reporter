"""
Cycle Route Assessment MCP Server.

Implements [cycle-route-assessment:FR-001] - MCP server with route assessment tools
Implements [cycle-route-assessment:FR-002] - Route infrastructure analysis via Overpass
Implements [cycle-route-assessment:FR-003] - LTN 1/20 scoring
Implements [cycle-route-assessment:FR-004] - Key issues identification
Implements [cycle-route-assessment:FR-007] - Site boundary lookup from ArcGIS
Implements [cycle-route-assessment:NFR-001] - Complete within 30s for 3 destinations
Implements [cycle-route-assessment:NFR-002] - Graceful failure handling
Implements [cycle-route-assessment:NFR-003] - Rate limiting, User-Agent headers

Implements test scenarios:
- [cycle-route-assessment:CycleRouteMCP/TS-01] get_site_boundary returns GeoJSON
- [cycle-route-assessment:CycleRouteMCP/TS-02] get_site_boundary handles not found
- [cycle-route-assessment:CycleRouteMCP/TS-03] assess_cycle_route returns full assessment
- [cycle-route-assessment:CycleRouteMCP/TS-04] assess_cycle_route handles no route
- [cycle-route-assessment:CycleRouteMCP/TS-05] Large site centroid noted
"""

import asyncio
import json
import os
from typing import Any

import httpx
import structlog
from mcp.server import Server
from mcp.types import TextContent, Tool
from pydantic import BaseModel, Field
from starlette.applications import Starlette

from src.mcp_servers.cycle_route.geojson import parse_arcgis_response
from src.mcp_servers.cycle_route.infrastructure import (
    aggregate_segments_to_geojson,
    analyse_transitions,
    barriers_to_geojson,
    build_overpass_query,
    crossings_to_geojson,
    detect_parallel_provision,
    parse_overpass_ways,
    query_overpass_resilient,
    route_to_geojson,
    summarise_provision,
)
from src.mcp_servers.cycle_route.issues import (
    generate_s106_suggestions,
    identify_issues,
)
from src.mcp_servers.cycle_route.polyline import decode_polyline
from src.mcp_servers.cycle_route.scoring import score_route

logger = structlog.get_logger(__name__)

# Default ArcGIS endpoint for Cherwell planning register
DEFAULT_ARCGIS_URL = (
    "https://utility.arcgis.com/usrsvcs/servers/"
    "3b969cb8886849d993863e4c913c82fc/rest/services/"
    "Public_Map_Services/Cherwell_Public_Planning_Register/"
    "MapServer/0/query"
)

# Valhalla routing engine endpoint
DEFAULT_VALHALLA_URL = "http://valhalla:8002"

# User-Agent for external API calls
USER_AGENT = "BBUGCycleRouteAssessment/1.0 (cycling-advocacy-tool)"

# Rate limit delay between external calls (seconds)
EXTERNAL_API_DELAY = 0.5


# =============================================================================
# Tool Input Schemas
# =============================================================================

class GetSiteBoundaryInput(BaseModel):
    """Input schema for get_site_boundary tool."""
    application_ref: str = Field(
        description="Planning application reference (e.g., '21/03267/OUT')"
    )


class AssessCycleRouteInput(BaseModel):
    """Input schema for assess_cycle_route tool."""
    origin_lon: float = Field(description="Origin longitude (WGS84)")
    origin_lat: float = Field(description="Origin latitude (WGS84)")
    destination_lon: float = Field(description="Destination longitude (WGS84)")
    destination_lat: float = Field(description="Destination latitude (WGS84)")
    destination_name: str = Field(
        default="Destination",
        description="Human-readable destination name",
    )


# =============================================================================
# MCP Server
# =============================================================================

class CycleRouteMCP:
    """MCP server for cycling route assessment."""

    def __init__(
        self,
        arcgis_url: str | None = None,
        valhalla_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.arcgis_url = arcgis_url or os.getenv("ARCGIS_PLANNING_URL", DEFAULT_ARCGIS_URL)
        self.valhalla_url = valhalla_url or os.getenv("VALHALLA_URL", DEFAULT_VALHALLA_URL)
        self._http = http_client
        self.server = Server("cycle-route-mcp")
        self._setup_handlers()

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=20.0,
                headers={"User-Agent": USER_AGENT},
            )
        return self._http

    def _setup_handlers(self) -> None:
        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            return [
                Tool(
                    name="get_site_boundary",
                    description=(
                        "Look up the site boundary polygon for a planning application "
                        "from Cherwell's ArcGIS planning register. Returns GeoJSON "
                        "FeatureCollection with the site polygon and centroid point."
                    ),
                    inputSchema=GetSiteBoundaryInput.model_json_schema(),
                ),
                Tool(
                    name="assess_cycle_route",
                    description=(
                        "Assess the cycling route quality between two points. "
                        "Calculates route via Valhalla, analyses infrastructure via "
                        "Overpass, scores against LTN 1/20, and identifies issues. "
                        "Returns distance, provision breakdown, score, issues, and "
                        "S106 funding suggestions."
                    ),
                    inputSchema=AssessCycleRouteInput.model_json_schema(),
                ),
            ]

        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
            try:
                if name == "get_site_boundary":
                    result = await self._get_site_boundary(arguments)
                elif name == "assess_cycle_route":
                    result = await self._assess_cycle_route(arguments)
                else:
                    result = {
                        "status": "error",
                        "error_type": "unknown_tool",
                        "message": f"Unknown tool: {name}",
                    }
            except Exception as e:
                logger.exception("Tool error", tool=name, error=str(e))
                result = {
                    "status": "error",
                    "error_type": "internal_error",
                    "message": str(e),
                }
            return [TextContent(type="text", text=json.dumps(result))]

    async def _get_site_boundary(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Look up site boundary from ArcGIS and return as GeoJSON."""
        app_ref = arguments["application_ref"]
        logger.info("Looking up site boundary", application_ref=app_ref)

        # Query ArcGIS
        where_clause = (
            f"DLGSDST.dbo.Planning_ArcGIS_Link_Public.application_number="
            f"'{app_ref}'"
        )
        params = {
            "f": "json",
            "returnGeometry": "true",
            "outSR": "4326",
            "outFields": "*",
            "where": where_clause,
        }

        response = await self.http.get(self.arcgis_url, params=params)
        response.raise_for_status()
        data = response.json()

        geojson = parse_arcgis_response(data)
        if geojson is None:
            return {
                "status": "error",
                "error_type": "not_found",
                "message": (
                    f"Application {app_ref} not found in planning register"
                ),
            }

        logger.info(
            "Site boundary found",
            application_ref=app_ref,
            num_features=len(geojson.get("features", [])),
        )

        return {
            "status": "success",
            "geojson": geojson,
        }

    async def _assess_single_route(
        self,
        route_coords: list[list[float]],
        cycling_distance_m: float,
        cycling_duration_s: float,
        dest_name: str,
        shortest_distance_m: float | None = None,
    ) -> dict[str, Any] | None:
        """
        Assess a single route: query Overpass, parse segments,
        run parallel detection, score, and identify issues.

        Returns route assessment dict or None if no infrastructure data.
        """
        # Rate limit before Overpass call
        await asyncio.sleep(EXTERNAL_API_DELAY)

        overpass_query = build_overpass_query(route_coords)
        overpass_data = await query_overpass_resilient(
            self.http, overpass_query, destination=dest_name,
        )
        if overpass_data is None:
            return None

        segments = parse_overpass_ways(overpass_data, cycling_distance_m)
        if not segments:
            return None

        # Parallel detection: upgrade provisions for road segments with adjacent cycleways
        detect_parallel_provision(segments, overpass_data)

        # Transition analysis: barriers, crossings, side changes
        try:
            transitions = analyse_transitions(segments, overpass_data)
        except Exception:
            logger.warning("Transition analysis failed", destination=dest_name)
            transitions = {
                "unavailable": True,
                "barriers": [],
                "non_priority_crossings": [],
                "side_changes": [],
                "directness_differential": None,
            }

        parallel_upgrades = sum(1 for s in segments if s.original_provision is not None)
        provision = summarise_provision(segments)
        route_score = score_route(
            segments, cycling_distance_m,
            shortest_distance_m=shortest_distance_m,
            transitions=transitions,
        )
        route_issues = identify_issues(segments)
        s106 = generate_s106_suggestions(route_issues)
        segments_geojson = aggregate_segments_to_geojson(segments)

        return {
            "distance_m": round(cycling_distance_m),
            "duration_minutes": round(cycling_duration_s / 60, 1),
            "provision_breakdown": provision,
            "route_geojson": route_to_geojson(route_coords, cycling_distance_m, cycling_duration_s),
            "crossings_geojson": crossings_to_geojson(transitions.get("non_priority_crossings", [])),
            "barriers_geojson": barriers_to_geojson(transitions.get("barriers", [])),
            "segments_geojson": segments_geojson,
            "score": route_score,
            "issues": route_issues,
            "s106_suggestions": s106,
            "transitions": transitions,
            "parallel_upgrades": parallel_upgrades,
        }

    async def _request_valhalla_route(
        self,
        origin_lon: float,
        origin_lat: float,
        dest_lon: float,
        dest_lat: float,
        costing: str,
        costing_options: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Make a single Valhalla route request. Returns parsed response or None on failure."""
        body: dict[str, Any] = {
            "locations": [
                {"lon": origin_lon, "lat": origin_lat},
                {"lon": dest_lon, "lat": dest_lat},
            ],
            "costing": costing,
            "units": "kilometers",
        }
        if costing_options:
            body["costing_options"] = costing_options

        try:
            response = await self.http.post(
                f"{self.valhalla_url}/route",
                json=body,
            )
            if response.status_code != 200:
                return None
            data = response.json()
            if "trip" not in data:
                return None
            return data
        except Exception:
            return None

    async def _assess_cycle_route(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Assess cycling route between two points with dual Valhalla routing."""
        origin_lon = arguments["origin_lon"]
        origin_lat = arguments["origin_lat"]
        dest_lon = arguments["destination_lon"]
        dest_lat = arguments["destination_lat"]
        dest_name = arguments.get("destination_name", "Destination")

        logger.info(
            "Assessing cycle route",
            destination=dest_name,
            origin=f"{origin_lat:.4f},{origin_lon:.4f}",
            destination_coords=f"{dest_lat:.4f},{dest_lon:.4f}",
        )

        # Step 1: Request two bicycle routes from Valhalla (no driving route)
        shortest_data = await self._request_valhalla_route(
            origin_lon, origin_lat, dest_lon, dest_lat,
            costing="bicycle",
            costing_options={"bicycle": {"shortest": True}},
        )

        await asyncio.sleep(EXTERNAL_API_DELAY)

        safest_data = await self._request_valhalla_route(
            origin_lon, origin_lat, dest_lon, dest_lat,
            costing="bicycle",
            costing_options={"bicycle": {
                "use_roads": 0.1,
                "avoid_bad_surfaces": 0.6,
                "use_hills": 0.3,
            }},
        )

        # Fallback logic: if one bicycle route fails, use the other for both
        if shortest_data is None and safest_data is None:
            return {
                "status": "error",
                "error_type": "no_route",
                "message": f"No cycling route found to {dest_name}",
            }

        if shortest_data is None:
            shortest_data = safest_data
        elif safest_data is None:
            safest_data = shortest_data

        # Step 2: Decode routes
        def _extract_route(data: dict[str, Any]) -> tuple[list[list[float]], float, float]:
            leg = data["trip"]["legs"][0]
            coords = decode_polyline(leg["shape"])
            summary = data["trip"]["summary"]
            distance_m = summary["length"] * 1000  # km → m
            duration_s = summary["time"]
            return coords, distance_m, duration_s

        shortest_coords, shortest_dist, shortest_dur = _extract_route(shortest_data)
        safest_coords, safest_dist, safest_dur = _extract_route(safest_data)

        # Determine if same route (distance difference < 1%)
        same_route = (
            shortest_data is safest_data
            or abs(shortest_dist - safest_dist) / max(shortest_dist, 1) < 0.01
        )

        # Step 3: Assess ONLY the safest route via Overpass + scoring
        # Shortest route provides distance only for directness comparison
        assessment = await self._assess_single_route(
            safest_coords, safest_dist, safest_dur,
            dest_name, shortest_distance_m=shortest_dist,
        )

        # Build fallback stub for routes with no infrastructure data
        def _empty_assessment(coords: list, dist: float, dur: float) -> dict[str, Any]:
            return {
                "distance_m": round(dist),
                "duration_minutes": round(dur / 60, 1),
                "provision_breakdown": {},
                "route_geojson": route_to_geojson(coords, dist, dur),
                "crossings_geojson": {"type": "FeatureCollection", "features": []},
                "barriers_geojson": {"type": "FeatureCollection", "features": []},
                "segments_geojson": {"type": "FeatureCollection", "features": []},
                "score": {"score": 0, "rating": "red", "breakdown": {}},
                "issues": [],
                "s106_suggestions": [],
                "parallel_upgrades": 0,
            }

        if assessment is None:
            assessment = _empty_assessment(safest_coords, safest_dist, safest_dur)

        logger.info(
            "Route assessed",
            destination=dest_name,
            distance=assessment["distance_m"],
            score=assessment["score"]["score"],
            shortest_distance=round(shortest_dist),
            same_route=same_route,
        )

        return {
            "status": "success",
            "destination": dest_name,
            **assessment,
            "shortest_route_distance_m": round(shortest_dist),
            "shortest_route_geometry": shortest_coords,
            "same_route": same_route,
        }


def create_app() -> Starlette:
    """Create the Starlette application with SSE + Streamable HTTP transport."""
    from src.mcp_servers.shared.transport import create_mcp_app

    mcp_server = CycleRouteMCP()
    return create_mcp_app(mcp_server.server)


async def main() -> None:
    """Run the MCP server."""
    import uvicorn

    port = int(os.getenv("CYCLE_ROUTE_PORT", "3004"))

    logger.info(
        "Cycle Route MCP Server starting",
        component="cycle-route-mcp",
        port=port,
    )

    app = create_app()
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
