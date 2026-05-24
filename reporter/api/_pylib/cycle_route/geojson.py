"""
ESRI JSON to GeoJSON converter.

Implements [cycle-route-assessment:FR-007] - ESRI polygon → GeoJSON conversion + centroid
Implements [cycle-route-assessment:FR-010] - GeoJSON RFC 7946 compliant

Implements test scenarios:
- [cycle-route-assessment:GeoJSONConverter/TS-01] Simple polygon converted
- [cycle-route-assessment:GeoJSONConverter/TS-02] Centroid calculated correctly
- [cycle-route-assessment:GeoJSONConverter/TS-03] Multi-ring polygon handled
- [cycle-route-assessment:GeoJSONConverter/TS-04] Properties preserved
"""

from typing import Any

# Attribute key prefixes from the ESRI response (fully qualified field names)
_ATTR_PREFIX_PLANNING = "DLGSDST.dbo.Planning_ArcGIS_Link_Public."
_ATTR_PREFIX_GIS = "CORPGIS.MASTERGOV.DEF_Planning."


def _extract_attribute(attributes: dict[str, Any], short_name: str) -> Any:
    """Extract attribute value trying both prefixed and short key names."""
    # Try planning link prefix
    key = f"{_ATTR_PREFIX_PLANNING}{short_name}"
    if key in attributes:
        return attributes[key]
    # Try GIS prefix
    key = f"{_ATTR_PREFIX_GIS}{short_name}"
    if key in attributes:
        return attributes[key]
    # Try direct
    return attributes.get(short_name)


def calculate_centroid(ring: list[list[float]]) -> tuple[float, float]:
    """
    Calculate the centroid of a polygon ring as the average of coordinates.

    This is the geometric centre (mean of vertices), not the true centroid
    of the polygon area, but sufficient for route origin approximation.

    Args:
        ring: List of [lon, lat] coordinate pairs. The last point should
              repeat the first (closed ring) but is excluded from the average.

    Returns:
        Tuple of (longitude, latitude).
    """
    # Exclude closing point if ring is closed
    coords = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    if not coords:
        return (0.0, 0.0)

    lon = sum(c[0] for c in coords) / len(coords)
    lat = sum(c[1] for c in coords) / len(coords)
    return (lon, lat)


def esri_to_geojson(
    rings: list[list[list[float]]],
    attributes: dict[str, Any],
) -> dict[str, Any]:
    """
    Convert ESRI polygon geometry to a GeoJSON FeatureCollection.

    Returns a FeatureCollection with two features:
    1. The site polygon
    2. The centroid point (used as route origin)

    Args:
        rings: ESRI polygon rings (list of coordinate arrays in WGS84).
               First ring is exterior, subsequent rings are holes.
        attributes: ESRI feature attributes dict.

    Returns:
        GeoJSON FeatureCollection (RFC 7946).
    """
    application_ref = (
        _extract_attribute(attributes, "application_number")
        or _extract_attribute(attributes, "APPLICATION_REF")
        or "unknown"
    )
    location = _extract_attribute(attributes, "location") or ""
    area_sqm = attributes.get("SHAPE.STArea()", 0)

    # Calculate centroid from exterior ring
    exterior_ring = rings[0] if rings else []
    centroid_lon, centroid_lat = calculate_centroid(exterior_ring)

    # Determine centroid accuracy note
    centroid_note = "Geometric centre of site boundary; actual entrance may differ"
    if area_sqm and area_sqm > 100000:
        centroid_note = (
            "Approximate origin; actual access point may vary significantly "
            "for this large site"
        )

    # Build shared properties
    base_properties = {
        "application_ref": application_ref,
        "address": location.replace("\r\n", ", ").strip(),
        "area_sqm": round(area_sqm, 1) if area_sqm else None,
    }

    # Polygon feature
    polygon_feature = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": rings,
        },
        "properties": {
            **base_properties,
            "feature_type": "site_boundary",
        },
    }

    # Centroid point feature
    centroid_feature = {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [centroid_lon, centroid_lat],
        },
        "properties": {
            **base_properties,
            "feature_type": "centroid",
            "centroid_note": centroid_note,
        },
    }

    return {
        "type": "FeatureCollection",
        "features": [polygon_feature, centroid_feature],
    }


def parse_arcgis_response(response_json: dict[str, Any]) -> dict[str, Any] | None:
    """
    Parse an ArcGIS query response and convert the first feature to GeoJSON.

    Args:
        response_json: Raw JSON response from ArcGIS REST API query.

    Returns:
        GeoJSON FeatureCollection, or None if no features found.
    """
    features = response_json.get("features", [])
    if not features:
        return None

    feature = features[0]
    geometry = feature.get("geometry", {})
    rings = geometry.get("rings", [])
    attributes = feature.get("attributes", {})

    if not rings:
        return None

    return esri_to_geojson(rings, attributes)
