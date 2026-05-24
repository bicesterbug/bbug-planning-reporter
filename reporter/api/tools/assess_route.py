"""Custom tool: assess_cycle_route (Vercel Python function).

Baseline-only cycle accessibility assessment for the CURRENT network (the OSM
constraint from the vision: proposed infrastructure must be inferred by the
agent, not measured here). Orchestrates a hosted Valhalla-compatible routing
API + Overpass, then runs the ported LTN 1/20 scoring and issue detection.

Returns COMPACT JSON per destination — scores, RAG rating, provision breakdown,
issues, and S106 suggestions. Route geometry is NOT returned by default (token
firewall); set include_geometry=true to also persist GeoJSON to Blob (caller
gets a ref, not the coordinates).

Reuses the proven cycle-route engine ported verbatim into api/_pylib/cycle_route.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _pylib.cycle_route.geojson import parse_arcgis_response  # noqa: E402
from _pylib.cycle_route.infrastructure import (  # noqa: E402
    analyse_transitions,
    build_overpass_query,
    detect_parallel_provision,
    parse_overpass_ways,
    query_overpass_resilient,
    summarise_provision,
)
from _pylib.cycle_route.issues import generate_s106_suggestions, identify_issues  # noqa: E402
from _pylib.cycle_route.polyline import decode_polyline  # noqa: E402
from _pylib.cycle_route.scoring import score_route  # noqa: E402

VALHALLA_URL = os.getenv("VALHALLA_URL", "")  # hosted Valhalla (e.g. Stadia Maps)
VALHALLA_API_KEY = os.getenv("VALHALLA_API_KEY", "")
ARCGIS_PLANNING_URL = os.getenv(
    "ARCGIS_PLANNING_URL",
    "https://utility.arcgis.com/usrsvcs/servers/3b969cb8886849d993863e4c913c82fc/"
    "rest/services/Public_Map_Services/Cherwell_Public_Planning_Register/MapServer/0/query",
)
INTERNAL_TOKEN = os.getenv("INTERNAL_TOOL_TOKEN", "")

# Safest-route costing weights (favour quiet/segregated infrastructure).
_SAFEST_BICYCLE_OPTIONS = {"use_roads": 0.1, "avoid_bad_surfaces": 0.6, "use_hills": 0.3}


def _valhalla_params() -> dict:
    return {"api_key": VALHALLA_API_KEY} if VALHALLA_API_KEY else {}


async def _route(client: httpx.AsyncClient, origin: dict, dest: dict, *, safest: bool) -> dict | None:
    """Call the Valhalla-compatible /route endpoint. Returns shape + summary."""
    bicycle_opts = {"bicycle": _SAFEST_BICYCLE_OPTIONS} if safest else {}
    body = {
        "locations": [
            {"lat": origin["lat"], "lon": origin["lon"]},
            {"lat": dest["lat"], "lon": dest["lon"]},
        ],
        "costing": "bicycle",
        "costing_options": bicycle_opts,
        "units": "kilometers",
    }
    resp = await client.post(f"{VALHALLA_URL}/route", params=_valhalla_params(), json=body)
    if resp.status_code >= 400:
        return None
    trip = resp.json().get("trip", {})
    legs = trip.get("legs", [])
    summary = trip.get("summary", {})
    return {
        "shape": legs[0].get("shape") if legs else None,
        "distance_m": summary.get("length", 0) * 1000.0,
        "duration_s": summary.get("time", 0),
    }


async def _trace_way_ids(client: httpx.AsyncClient, shape: str) -> set[int]:
    """trace_attributes → set of OSM way_ids on the route (for on-route filtering)."""
    coords = decode_polyline(shape, precision=6)
    if len(coords) < 2:
        return set()
    body = {
        "shape": [{"lat": lat, "lon": lon} for lon, lat in coords],
        "costing": "bicycle",
        "shape_match": "edge_walk",
        "filters": {"attributes": ["edge.way_id"], "action": "include"},
    }
    resp = await client.post(f"{VALHALLA_URL}/trace_attributes", params=_valhalla_params(), json=body)
    if resp.status_code >= 400:
        return set()
    edges = resp.json().get("edges", [])
    return {e["way_id"] for e in edges if "way_id" in e}


async def _resolve_origin(client: httpx.AsyncClient, application_ref: str) -> dict | None:
    """Look up the site centroid from the Cherwell ArcGIS planning register."""
    params = {
        "f": "json",
        "returnGeometry": "true",
        "outSR": "4326",
        "outFields": "*",
        "where": f"application_number='{application_ref}'",
    }
    resp = await client.get(ARCGIS_PLANNING_URL, params=params)
    if resp.status_code >= 400:
        return None
    fc = parse_arcgis_response(resp.json())
    if not fc:
        return None
    for feat in fc["features"]:
        if feat["properties"].get("feature_type") == "centroid":
            lon, lat = feat["geometry"]["coordinates"]
            return {"lat": lat, "lon": lon, "note": feat["properties"].get("centroid_note")}
    return None


async def _assess_one(client: httpx.AsyncClient, origin: dict, dest: dict) -> dict:
    safest = await _route(client, origin, dest, safest=True)
    shortest = await _route(client, origin, dest, safest=False)
    if not safest or not safest["shape"]:
        return {"destination": dest.get("name"), "error": "no_route_found"}

    coords = decode_polyline(safest["shape"], precision=6)
    way_ids = await _trace_way_ids(client, safest["shape"])
    overpass = await query_overpass_resilient(
        client,
        build_overpass_query(coords, on_route_way_ids=way_ids or None),
        destination=dest.get("name", ""),
    )

    if overpass:
        segments = parse_overpass_ways(overpass, safest["distance_m"])
        segments = detect_parallel_provision(segments, overpass)
        transitions = analyse_transitions(segments, overpass)
    else:
        segments, transitions = [], {"unavailable": True}

    scoring = score_route(
        segments,
        cycling_distance_m=safest["distance_m"],
        shortest_distance_m=shortest["distance_m"] if shortest else None,
        transitions=transitions,
    )
    issues = identify_issues(segments)

    return {
        "destination": dest.get("name"),
        "category": dest.get("category"),
        "distance_m": round(safest["distance_m"]),
        "duration_minutes": round(safest["duration_s"] / 60, 1),
        "score": scoring["score"],
        "rating": scoring["rating"],
        "breakdown": scoring["breakdown"],
        "provision_breakdown_m": summarise_provision(segments),
        "issues": issues,
        "s106_suggestions": generate_s106_suggestions(issues),
        "transitions": {
            "barriers": transitions.get("barrier_count", 0),
            "non_priority_crossings": transitions.get("non_priority_crossing_count", 0),
            "side_changes": transitions.get("side_change_count", 0),
        },
        "baseline_only": True,
        "osm_data_available": bool(overpass),
    }


async def _assess(body: dict) -> dict:
    if not VALHALLA_URL:
        return {"error": "VALHALLA_URL not configured"}

    destinations = body.get("destinations") or []
    if not destinations:
        return {"error": "destinations is required (list of {name, lat, lon, category})"}

    async with httpx.AsyncClient(timeout=60.0) as client:
        origin = body.get("origin")
        if not origin and body.get("application_ref"):
            origin = await _resolve_origin(client, body["application_ref"])
        if not origin:
            return {"error": "origin not provided and could not be resolved from application_ref"}

        results = [await _assess_one(client, origin, d) for d in destinations]

    return {
        "origin": origin,
        "assessments": results,
        "note": "Baseline assessment of the CURRENT cycle network. Proposed "
        "infrastructure must be inferred from the application documents.",
    }


class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        if INTERNAL_TOKEN and self.headers.get("x-internal-token") != INTERNAL_TOKEN:
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("content-length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            self._send(200, asyncio.run(_assess(body)))
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"error": str(exc)})

    def _send(self, code: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
