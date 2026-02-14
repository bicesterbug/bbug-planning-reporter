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
    OVERPASS_API_URL,
    build_overpass_query,
    parse_overpass_ways,
    summarise_provision,
)
from src.mcp_servers.cycle_route.issues import (
    generate_s106_suggestions,
    identify_issues,
)
from src.mcp_servers.cycle_route.scoring import score_route

logger = structlog.get_logger(__name__)

# Default ArcGIS endpoint for Cherwell planning register
DEFAULT_ARCGIS_URL = (
    "https://utility.arcgis.com/usrsvcs/servers/"
    "3b969cb8886849d993863e4c913c82fc/rest/services/"
    "Public_Map_Services/Cherwell_Public_Planning_Register/"
    "MapServer/0/query"
)

# OSRM cycling profile endpoint
DEFAULT_OSRM_URL = "https://router.project-osrm.org/route/v1/bike"

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
        osrm_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.arcgis_url = arcgis_url or os.getenv("ARCGIS_PLANNING_URL", DEFAULT_ARCGIS_URL)
        self.osrm_url = osrm_url or os.getenv("OSRM_URL", DEFAULT_OSRM_URL)
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
                        "Calculates route via OSRM, analyses infrastructure via "
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

    async def _assess_cycle_route(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Assess cycling route between two points."""
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

        # Step 1: Get cycling route from OSRM
        osrm_url = (
            f"{self.osrm_url}/{origin_lon},{origin_lat};"
            f"{dest_lon},{dest_lat}"
        )
        osrm_params = {
            "overview": "full",
            "geometries": "geojson",
            "steps": "false",
        }

        response = await self.http.get(osrm_url, params=osrm_params)
        response.raise_for_status()
        osrm_data = response.json()

        if osrm_data.get("code") != "Ok" or not osrm_data.get("routes"):
            return {
                "status": "error",
                "error_type": "no_route",
                "message": f"No cycling route found to {dest_name}",
            }

        route = osrm_data["routes"][0]
        cycling_distance_m = route["distance"]
        cycling_duration_s = route["duration"]
        route_coords = route["geometry"]["coordinates"]

        # Rate limit before next API call
        await asyncio.sleep(EXTERNAL_API_DELAY)

        # Step 2: Query Overpass for infrastructure along route
        overpass_query = build_overpass_query(route_coords)

        overpass_response = await self.http.post(
            OVERPASS_API_URL,
            data={"data": overpass_query},
        )
        overpass_response.raise_for_status()
        overpass_data = overpass_response.json()

        # Step 3: Parse infrastructure segments
        segments = parse_overpass_ways(overpass_data, cycling_distance_m)

        if not segments:
            logger.warning(
                "No infrastructure data found",
                destination=dest_name,
            )
            return {
                "status": "success",
                "destination": dest_name,
                "distance_m": round(cycling_distance_m),
                "duration_minutes": round(cycling_duration_s / 60, 1),
                "provision_breakdown": {},
                "score": {"score": 0, "rating": "red", "breakdown": {}},
                "issues": [],
                "s106_suggestions": [],
                "note": "No infrastructure data available along route",
            }

        # Step 4: Score and identify issues
        provision = summarise_provision(segments)
        route_score = score_route(segments, cycling_distance_m)
        route_issues = identify_issues(segments)
        s106 = generate_s106_suggestions(route_issues)

        logger.info(
            "Route assessed",
            destination=dest_name,
            distance_m=round(cycling_distance_m),
            score=route_score["score"],
            rating=route_score["rating"],
            issues_count=len(route_issues),
        )

        return {
            "status": "success",
            "destination": dest_name,
            "distance_m": round(cycling_distance_m),
            "duration_minutes": round(cycling_duration_s / 60, 1),
            "provision_breakdown": provision,
            "segments": [s.to_dict() for s in segments],
            "score": route_score,
            "issues": route_issues,
            "s106_suggestions": s106,
            "route_geometry": route_coords,
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
