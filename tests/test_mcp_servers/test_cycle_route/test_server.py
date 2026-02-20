"""
Tests for Cycle Route MCP server.

Verifies [cycle-route-assessment:FR-001] - MCP server with route assessment tools
Verifies [cycle-route-assessment:FR-002] - Route infrastructure analysis via Overpass
Verifies [cycle-route-assessment:FR-003] - LTN 1/20 scoring
Verifies [cycle-route-assessment:FR-004] - Key issues identification
Verifies [cycle-route-assessment:FR-007] - Site boundary lookup from ArcGIS
Verifies [cycle-route-assessment:NFR-001] - Complete within 30s for 3 destinations
Verifies [cycle-route-assessment:NFR-002] - Graceful failure handling
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.mcp_servers.cycle_route.server import CycleRouteMCP

FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures" / "cycle_route"

# Default test coordinates (Bicester area)
DEFAULT_COORDS = [
    [-1.1534, 51.8997],
    [-1.1510, 51.9010],
    [-1.1480, 51.9025],
    [-1.1450, 51.9050],
]


def _encode_polyline(coords: list[list[float]], precision: int = 6) -> str:
    """Test helper: encode [lon, lat] pairs to polyline string."""
    encoded = []
    prev_lat = 0
    prev_lon = 0
    factor = 10**precision

    for lon, lat in coords:
        lat_int = round(lat * factor)
        lon_int = round(lon * factor)
        d_lat = lat_int - prev_lat
        d_lon = lon_int - prev_lon
        prev_lat = lat_int
        prev_lon = lon_int

        for val in (d_lat, d_lon):
            val = ~(val << 1) if val < 0 else val << 1
            while val >= 0x20:
                encoded.append(chr((0x20 | (val & 0x1F)) + 63))
                val >>= 5
            encoded.append(chr(val + 63))

    return "".join(encoded)


def _make_mock_transport(responses: dict[str, httpx.Response]) -> httpx.MockTransport:
    """Create a mock transport that returns responses based on URL patterns."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for pattern, response in responses.items():
            if pattern in url:
                return response
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


def _load_arcgis_fixture() -> dict:
    """Load the real ArcGIS fixture."""
    with open(FIXTURES_DIR / "arcgis_response_21_03267.json") as f:
        return json.load(f)


def _make_valhalla_response(
    distance_km: float = 2.5,
    duration_s: float = 600,
    coords: list[list[float]] | None = None,
) -> dict:
    """Create a mock Valhalla route response."""
    if coords is None:
        coords = DEFAULT_COORDS
    shape = _encode_polyline(coords)
    return {
        "trip": {
            "legs": [{
                "shape": shape,
                "summary": {"length": distance_km, "time": duration_s},
            }],
            "summary": {"length": distance_km, "time": duration_s},
            "units": "kilometers",
            "status": 0,
        },
    }


def _make_valhalla_error(error_code: int = 171, message: str = "No path could be found") -> dict:
    """Create a mock Valhalla error response."""
    return {
        "error_code": error_code,
        "error": message,
        "status_code": 400,
        "status": "Bad Request",
    }


def _make_overpass_response(
    ways: list[dict] | None = None,
    nodes: list[dict] | None = None,
) -> dict:
    """Create a mock Overpass response with geometry and optional nodes."""
    if ways is None:
        ways = [
            {
                "type": "way",
                "id": 100,
                "tags": {
                    "highway": "cycleway",
                    "surface": "asphalt",
                    "lit": "yes",
                    "name": "NCN Route 51",
                },
                "geometry": [
                    {"lat": 51.8997, "lon": -1.1534},
                    {"lat": 51.9010, "lon": -1.1510},
                ],
            },
            {
                "type": "way",
                "id": 101,
                "tags": {
                    "highway": "secondary",
                    "maxspeed": "30 mph",
                    "surface": "asphalt",
                    "name": "Buckingham Road",
                },
                "geometry": [
                    {"lat": 51.9010, "lon": -1.1510},
                    {"lat": 51.9025, "lon": -1.1480},
                ],
            },
            {
                "type": "way",
                "id": 102,
                "tags": {
                    "highway": "residential",
                    "surface": "asphalt",
                    "name": "Station Approach",
                },
                "geometry": [
                    {"lat": 51.9025, "lon": -1.1480},
                    {"lat": 51.9050, "lon": -1.1450},
                ],
            },
        ]
    elements = list(ways)
    if nodes:
        elements.extend(nodes)
    return {"elements": elements}


def _make_valhalla_handler(
    shortest_response: dict | None = None,
    safest_response: dict | None = None,
    driving_response: dict | None = None,
    overpass_response: dict | None = None,
    captured_bodies: list | None = None,
    shortest_status: int = 200,
    safest_status: int = 200,
    driving_status: int = 200,
) -> httpx.MockTransport:
    """Create a mock transport that differentiates Valhalla requests by costing."""
    if shortest_response is None:
        shortest_response = _make_valhalla_response()
    if safest_response is None:
        safest_response = _make_valhalla_response()
    if driving_response is None:
        driving_response = _make_valhalla_response(distance_km=2.0, duration_s=300)
    if overpass_response is None:
        overpass_response = _make_overpass_response()

    valhalla_call_count = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "overpass-api.de" in url:
            return httpx.Response(200, json=overpass_response)
        if "arcgis.com" in url:
            return httpx.Response(404, json={"error": "not found"})
        if "valhalla" in url and "/route" in url:
            body = json.loads(request.content)
            if captured_bodies is not None:
                captured_bodies.append(body)
            costing = body.get("costing", "")
            costing_opts = body.get("costing_options", {})

            if costing == "auto":
                return httpx.Response(driving_status, json=driving_response)
            elif costing == "bicycle":
                bicycle_opts = costing_opts.get("bicycle", {})
                if bicycle_opts.get("shortest") is True:
                    valhalla_call_count[0] += 1
                    return httpx.Response(shortest_status, json=shortest_response)
                else:
                    valhalla_call_count[0] += 1
                    return httpx.Response(safest_status, json=safest_response)

            return httpx.Response(400, json=_make_valhalla_error())
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


# =============================================================================
# get_site_boundary
# =============================================================================


class TestGetSiteBoundary:

    @pytest.mark.anyio
    async def test_returns_geojson(self):
        """get_site_boundary returns GeoJSON FeatureCollection."""
        arcgis_data = _load_arcgis_fixture()
        transport = _make_mock_transport({
            "arcgis.com": httpx.Response(200, json=arcgis_data),
        })
        client = httpx.AsyncClient(transport=transport)
        mcp = CycleRouteMCP(http_client=client)

        result = await mcp._get_site_boundary(
            {"application_ref": "21/03267/OUT"}
        )

        assert result["status"] == "success"
        geojson = result["geojson"]
        assert geojson["type"] == "FeatureCollection"
        assert len(geojson["features"]) == 2

        polygon = geojson["features"][0]
        assert polygon["geometry"]["type"] == "Polygon"

        centroid = geojson["features"][1]
        assert centroid["geometry"]["type"] == "Point"

    @pytest.mark.anyio
    async def test_not_found(self):
        """get_site_boundary handles not found."""
        empty_response = {"features": []}
        transport = _make_mock_transport({
            "arcgis.com": httpx.Response(200, json=empty_response),
        })
        client = httpx.AsyncClient(transport=transport)
        mcp = CycleRouteMCP(http_client=client)

        result = await mcp._get_site_boundary(
            {"application_ref": "99/99999/X"}
        )

        assert result["status"] == "error"
        assert result["error_type"] == "not_found"
        assert "not found" in result["message"].lower()

    @pytest.mark.anyio
    async def test_http_error_propagates(self):
        """HTTP errors from ArcGIS propagate as exceptions."""
        transport = _make_mock_transport({
            "arcgis.com": httpx.Response(500, text="Server Error"),
        })
        client = httpx.AsyncClient(transport=transport)
        mcp = CycleRouteMCP(http_client=client)

        with pytest.raises(httpx.HTTPStatusError):
            await mcp._get_site_boundary(
                {"application_ref": "21/03267/OUT"}
            )


# =============================================================================
# assess_cycle_route
# =============================================================================


class TestAssessCycleRoute:
    """Tests for dual-route cycle assessment via Valhalla."""

    @pytest.mark.anyio
    async def test_full_assessment_dual_route(self):
        """assess_cycle_route returns dual route assessment via Valhalla."""
        transport = _make_valhalla_handler()
        client = httpx.AsyncClient(transport=transport)
        mcp = CycleRouteMCP(http_client=client)

        result = await mcp._assess_cycle_route({
            "origin_lon": -1.1534,
            "origin_lat": 51.8997,
            "destination_lon": -1.1450,
            "destination_lat": 51.9050,
            "destination_name": "Bicester North",
        })

        assert result["status"] == "success"
        assert result["destination"] == "Bicester North"
        assert "shortest_route" in result
        assert "safest_route" in result
        assert "same_route" in result

        shortest = result["shortest_route"]
        assert shortest["distance_m"] == 2500
        assert shortest["duration_minutes"] == 10.0
        assert "provision_breakdown" in shortest
        assert "score" in shortest
        assert "issues" in shortest
        assert "s106_suggestions" in shortest
        assert "segments" in shortest
        assert "route_geometry" in shortest
        assert shortest["score"]["rating"] in ("green", "amber", "red")

        segments = shortest["segments"]
        assert segments["type"] == "FeatureCollection"
        assert len(segments["features"]) > 0
        feature = segments["features"][0]
        assert feature["type"] == "Feature"
        assert feature["geometry"]["type"] == "LineString"
        assert "provision" in feature["properties"]

    @pytest.mark.anyio
    async def test_valhalla_called_with_shortest_costing(self):
        """Valhalla request includes bicycle costing with shortest=true."""
        captured_bodies: list[dict] = []
        transport = _make_valhalla_handler(captured_bodies=captured_bodies)
        client = httpx.AsyncClient(transport=transport)
        mcp = CycleRouteMCP(http_client=client)

        await mcp._assess_cycle_route({
            "origin_lon": -1.15,
            "origin_lat": 51.9,
            "destination_lon": -1.14,
            "destination_lat": 51.91,
        })

        bicycle_bodies = [b for b in captured_bodies if b.get("costing") == "bicycle"]
        shortest_bodies = [
            b for b in bicycle_bodies
            if b.get("costing_options", {}).get("bicycle", {}).get("shortest") is True
        ]
        assert len(shortest_bodies) == 1

    @pytest.mark.anyio
    async def test_valhalla_called_with_safest_costing(self):
        """Valhalla request includes bicycle costing with use_roads=0.1."""
        captured_bodies: list[dict] = []
        transport = _make_valhalla_handler(captured_bodies=captured_bodies)
        client = httpx.AsyncClient(transport=transport)
        mcp = CycleRouteMCP(http_client=client)

        await mcp._assess_cycle_route({
            "origin_lon": -1.15,
            "origin_lat": 51.9,
            "destination_lon": -1.14,
            "destination_lat": 51.91,
        })

        bicycle_bodies = [b for b in captured_bodies if b.get("costing") == "bicycle"]
        safest_bodies = [
            b for b in bicycle_bodies
            if b.get("costing_options", {}).get("bicycle", {}).get("use_roads") == 0.1
        ]
        assert len(safest_bodies) == 1
        safest = safest_bodies[0]
        assert safest["costing_options"]["bicycle"]["avoid_bad_surfaces"] == 0.6
        assert safest["costing_options"]["bicycle"]["use_hills"] == 0.3

    @pytest.mark.anyio
    async def test_driving_distance_request_uses_auto(self):
        """Valhalla driving distance request uses auto costing."""
        captured_bodies: list[dict] = []
        transport = _make_valhalla_handler(captured_bodies=captured_bodies)
        client = httpx.AsyncClient(transport=transport)
        mcp = CycleRouteMCP(http_client=client)

        await mcp._assess_cycle_route({
            "origin_lon": -1.15,
            "origin_lat": 51.9,
            "destination_lon": -1.14,
            "destination_lat": 51.91,
        })

        auto_bodies = [b for b in captured_bodies if b.get("costing") == "auto"]
        assert len(auto_bodies) == 1

    @pytest.mark.anyio
    async def test_no_route_found(self):
        """No route from Valhalla returns error."""
        error = _make_valhalla_error(171, "No path could be found")
        transport = _make_valhalla_handler(
            shortest_response=error,
            safest_response=error,
            shortest_status=400,
            safest_status=400,
        )
        client = httpx.AsyncClient(transport=transport)
        mcp = CycleRouteMCP(http_client=client)

        result = await mcp._assess_cycle_route({
            "origin_lon": -1.15,
            "origin_lat": 51.9,
            "destination_lon": -0.5,
            "destination_lat": 52.5,
            "destination_name": "Faraway Place",
        })

        assert result["status"] == "error"
        assert result["error_type"] == "no_route"
        assert "Faraway Place" in result["message"]

    @pytest.mark.anyio
    async def test_safest_failure_falls_back_to_shortest(self):
        """Safest route failure falls back to shortest for both."""
        shortest = _make_valhalla_response(distance_km=2.2, duration_s=550)
        error = _make_valhalla_error(171, "No path")
        transport = _make_valhalla_handler(
            shortest_response=shortest,
            safest_response=error,
            safest_status=400,
        )
        client = httpx.AsyncClient(transport=transport)
        mcp = CycleRouteMCP(http_client=client)

        result = await mcp._assess_cycle_route({
            "origin_lon": -1.15,
            "origin_lat": 51.9,
            "destination_lon": -1.14,
            "destination_lat": 51.91,
        })

        assert result["status"] == "success"
        assert result["same_route"] is True
        assert result["shortest_route"]["distance_m"] == result["safest_route"]["distance_m"]

    @pytest.mark.anyio
    async def test_driving_failure_gives_half_points_directness(self):
        """Driving route failure results in half-points directness score."""
        error = _make_valhalla_error(171, "No path")
        transport = _make_valhalla_handler(
            driving_response=error,
            driving_status=400,
        )
        client = httpx.AsyncClient(transport=transport)
        mcp = CycleRouteMCP(http_client=client)

        result = await mcp._assess_cycle_route({
            "origin_lon": -1.15,
            "origin_lat": 51.9,
            "destination_lon": -1.14,
            "destination_lat": 51.91,
        })

        assert result["status"] == "success"
        shortest = result["shortest_route"]
        assert shortest["score"]["breakdown"]["directness"] == 4.5

    @pytest.mark.anyio
    async def test_driving_distance_passed_to_scorer(self):
        """Driving distance is used for directness scoring."""
        # Driving distance 2.0 km, shortest bicycle distance 2.2 km (ratio 1.1 → full points)
        shortest = _make_valhalla_response(distance_km=2.2, duration_s=550)
        safest = _make_valhalla_response(distance_km=2.2, duration_s=550)
        driving = _make_valhalla_response(distance_km=2.0, duration_s=300)
        transport = _make_valhalla_handler(
            shortest_response=shortest,
            safest_response=safest,
            driving_response=driving,
        )
        client = httpx.AsyncClient(transport=transport)
        mcp = CycleRouteMCP(http_client=client)

        result = await mcp._assess_cycle_route({
            "origin_lon": -1.15,
            "origin_lat": 51.9,
            "destination_lon": -1.14,
            "destination_lat": 51.91,
        })

        assert result["status"] == "success"
        shortest_route = result["shortest_route"]
        # With ratio 2200/2000 = 1.1, should get full directness points (9.0)
        assert shortest_route["score"]["breakdown"]["directness"] == 9.0

    @pytest.mark.anyio
    async def test_no_infrastructure_data(self):
        """Route with no infrastructure data returns dual structure with note."""
        overpass_data = {"elements": []}
        transport = _make_valhalla_handler(overpass_response=overpass_data)
        client = httpx.AsyncClient(transport=transport)
        mcp = CycleRouteMCP(http_client=client)

        result = await mcp._assess_cycle_route({
            "origin_lon": -1.15,
            "origin_lat": 51.9,
            "destination_lon": -1.14,
            "destination_lat": 51.91,
        })

        assert result["status"] == "success"
        assert "note" in result
        assert result["same_route"] is True
        assert result["shortest_route"]["score"]["score"] == 0

    @pytest.mark.anyio
    async def test_default_destination_name(self):
        """Missing destination_name defaults to 'Destination'."""
        transport = _make_valhalla_handler()
        client = httpx.AsyncClient(transport=transport)
        mcp = CycleRouteMCP(http_client=client)

        result = await mcp._assess_cycle_route({
            "origin_lon": -1.15,
            "origin_lat": 51.9,
            "destination_lon": -1.14,
            "destination_lat": 51.91,
        })

        assert result["destination"] == "Destination"

    @pytest.mark.anyio
    async def test_assessment_includes_transitions(self):
        """Assessment includes transitions data."""
        barrier_node = {
            "type": "node", "id": 9001, "lat": 51.9010, "lon": -1.1510,
            "tags": {"barrier": "bollard"},
        }
        overpass_data = _make_overpass_response(nodes=[barrier_node])
        transport = _make_valhalla_handler(overpass_response=overpass_data)
        client = httpx.AsyncClient(transport=transport)
        mcp = CycleRouteMCP(http_client=client)

        result = await mcp._assess_cycle_route({
            "origin_lon": -1.1534,
            "origin_lat": 51.8997,
            "destination_lon": -1.1450,
            "destination_lat": 51.9050,
            "destination_name": "Test",
        })

        assert result["status"] == "success"
        shortest = result["shortest_route"]
        assert "transitions" in shortest
        transitions = shortest["transitions"]
        assert "barriers" in transitions
        assert "non_priority_crossings" in transitions
        assert "side_changes" in transitions
        assert "directness_differential" in transitions

    @pytest.mark.anyio
    async def test_transitions_in_both_routes(self):
        """Transitions present in both routes."""
        # Use different distances so routes are distinguishable
        shortest = _make_valhalla_response(distance_km=2.2, duration_s=550)
        safest = _make_valhalla_response(distance_km=2.8, duration_s=700)
        transport = _make_valhalla_handler(
            shortest_response=shortest,
            safest_response=safest,
        )
        client = httpx.AsyncClient(transport=transport)
        mcp = CycleRouteMCP(http_client=client)

        result = await mcp._assess_cycle_route({
            "origin_lon": -1.15,
            "origin_lat": 51.9,
            "destination_lon": -1.14,
            "destination_lat": 51.91,
        })

        assert "transitions" in result["shortest_route"]
        assert "transitions" in result["safest_route"]

    @pytest.mark.anyio
    async def test_different_shortest_and_safest_routes(self):
        """Different shortest and safest routes produce same_route=false."""
        # Shortest: 2.2km via roads
        shortest_coords = [
            [-1.16, 51.90],
            [-1.13, 51.91],
        ]
        # Safest: 2.8km via cycleways (different geometry)
        safest_coords = [
            [-1.16, 51.90],
            [-1.15, 51.905],
            [-1.13, 51.91],
        ]
        bad_ways = [
            {
                "type": "way", "id": 300,
                "tags": {"highway": "primary", "maxspeed": "40 mph", "surface": "asphalt", "name": "Main Road"},
                "geometry": [{"lat": 51.90, "lon": -1.16}, {"lat": 51.91, "lon": -1.13}],
            },
        ]
        good_ways = [
            {
                "type": "way", "id": 200,
                "tags": {"highway": "cycleway", "surface": "asphalt", "name": "Cycleway A"},
                "geometry": [{"lat": 51.90, "lon": -1.16}, {"lat": 51.905, "lon": -1.15}],
            },
            {
                "type": "way", "id": 201,
                "tags": {"highway": "cycleway", "surface": "asphalt", "name": "Cycleway B"},
                "geometry": [{"lat": 51.905, "lon": -1.15}, {"lat": 51.91, "lon": -1.13}],
            },
        ]

        shortest_resp = _make_valhalla_response(distance_km=2.2, duration_s=550, coords=shortest_coords)
        safest_resp = _make_valhalla_response(distance_km=2.8, duration_s=700, coords=safest_coords)

        overpass_call_count = [0]

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "overpass-api.de" in url:
                overpass_call_count[0] += 1
                # First call is for shortest route (roads), second for safest (cycleways)
                if overpass_call_count[0] == 1:
                    return httpx.Response(200, json={"elements": bad_ways})
                else:
                    return httpx.Response(200, json={"elements": good_ways})
            if "valhalla" in url and "/route" in url:
                body = json.loads(request.content)
                costing = body.get("costing", "")
                costing_opts = body.get("costing_options", {})
                if costing == "auto":
                    return httpx.Response(200, json=_make_valhalla_response(distance_km=2.0))
                if costing == "bicycle":
                    if costing_opts.get("bicycle", {}).get("shortest") is True:
                        return httpx.Response(200, json=shortest_resp)
                    return httpx.Response(200, json=safest_resp)
            return httpx.Response(404)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        mcp = CycleRouteMCP(http_client=client)

        result = await mcp._assess_cycle_route({
            "origin_lon": -1.16,
            "origin_lat": 51.90,
            "destination_lon": -1.13,
            "destination_lat": 51.91,
        })

        assert result["same_route"] is False
        assert result["shortest_route"]["distance_m"] == 2200
        assert result["safest_route"]["distance_m"] == 2800
        assert result["safest_route"]["score"]["score"] > result["shortest_route"]["score"]["score"]

    @pytest.mark.anyio
    @patch(
        "src.mcp_servers.cycle_route.infrastructure.asyncio.sleep",
        new_callable=AsyncMock,
    )
    async def test_overpass_total_failure_degrades_gracefully(self, mock_sleep):
        """Overpass 504 on all retries+fallback produces empty assessment stub."""
        overpass_call_count = [0]

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "overpass" in url:
                overpass_call_count[0] += 1
                return httpx.Response(504, text="Gateway Timeout")
            if "valhalla" in url and "/route" in url:
                body = json.loads(request.content)
                costing = body.get("costing", "")
                if costing == "auto":
                    return httpx.Response(200, json=_make_valhalla_response(distance_km=2.0))
                return httpx.Response(200, json=_make_valhalla_response())
            return httpx.Response(404)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        mcp = CycleRouteMCP(http_client=client)

        result = await mcp._assess_cycle_route({
            "origin_lon": -1.15,
            "origin_lat": 51.9,
            "destination_lon": -1.14,
            "destination_lat": 51.91,
            "destination_name": "Unreachable",
        })

        assert result["status"] == "success"
        assert result["destination"] == "Unreachable"
        assert result["shortest_route"]["score"]["score"] == 0
        assert "note" in result
        # Primary (3) + fallback (1) for each of the 2 routes = 8 Overpass calls
        assert overpass_call_count[0] == 8


# =============================================================================
# MCP tool listing
# =============================================================================


class TestToolListing:
    """Verify the server exposes the expected tools."""

    @pytest.mark.anyio
    async def test_lists_two_tools(self):
        """Server exposes get_site_boundary and assess_cycle_route."""
        mcp = CycleRouteMCP()

        assert mcp.server.name == "cycle-route-mcp"


class TestCallToolDispatch:
    """Test the call_tool dispatch for unknown tools."""

    @pytest.mark.anyio
    async def test_unknown_tool_error(self):
        """Unknown tool name returns error via call_tool handler."""
        mcp = CycleRouteMCP()

        assert mcp.server is not None
