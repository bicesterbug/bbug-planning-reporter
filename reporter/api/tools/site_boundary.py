"""Custom tool: get_site_boundary (Vercel Python function).

Looks up the application's site boundary + centroid from the Cherwell ArcGIS
planning register and returns GeoJSON (RFC 7946). The centroid is the route
origin used by assess_cycle_route. Reuses the ported ESRI→GeoJSON converter.
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _pylib.cycle_route.geojson import parse_arcgis_response  # noqa: E402

INTERNAL_TOKEN = os.getenv("INTERNAL_TOOL_TOKEN", "")
ARCGIS_PLANNING_URL = os.getenv(
    "ARCGIS_PLANNING_URL",
    "https://utility.arcgis.com/usrsvcs/servers/3b969cb8886849d993863e4c913c82fc/"
    "rest/services/Public_Map_Services/Cherwell_Public_Planning_Register/MapServer/0/query",
)


def _lookup(application_ref: str) -> dict:
    params = {
        "f": "json",
        "returnGeometry": "true",
        "outSR": "4326",
        "outFields": "*",
        "where": f"application_number='{application_ref}'",
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(ARCGIS_PLANNING_URL, params=params)
        resp.raise_for_status()
        fc = parse_arcgis_response(resp.json())

    if not fc:
        return {"error": "site boundary not found", "application_ref": application_ref}

    centroid = next(
        (f for f in fc["features"] if f["properties"].get("feature_type") == "centroid"),
        None,
    )
    return {
        "application_ref": application_ref,
        "geojson": fc,
        "centroid": centroid["geometry"]["coordinates"] if centroid else None,
        "centroid_note": centroid["properties"].get("centroid_note") if centroid else None,
    }


class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        if INTERNAL_TOKEN and self.headers.get("x-internal-token") != INTERNAL_TOKEN:
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("content-length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            ref = (body.get("application_ref") or "").strip()
            if not ref:
                self._send(400, {"error": "application_ref is required"})
                return
            self._send(200, _lookup(ref))
        except httpx.HTTPStatusError as exc:
            self._send(502, {"error": f"arcgis returned {exc.response.status_code}"})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"error": str(exc)})

    def _send(self, code: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
