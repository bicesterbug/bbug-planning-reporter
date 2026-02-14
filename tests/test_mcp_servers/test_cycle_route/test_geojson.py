"""
Tests for ESRI→GeoJSON converter.

Verifies [cycle-route-assessment:FR-007] - ESRI polygon → GeoJSON conversion + centroid
Verifies [cycle-route-assessment:FR-010] - GeoJSON RFC 7946 compliant

Verifies test scenarios:
- [cycle-route-assessment:GeoJSONConverter/TS-01] Simple polygon converted
- [cycle-route-assessment:GeoJSONConverter/TS-02] Centroid calculated correctly
- [cycle-route-assessment:GeoJSONConverter/TS-03] Multi-ring polygon handled
- [cycle-route-assessment:GeoJSONConverter/TS-04] Properties preserved
"""

import json
from pathlib import Path

import pytest

from src.mcp_servers.cycle_route.geojson import (
    calculate_centroid,
    esri_to_geojson,
    parse_arcgis_response,
)

FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures" / "cycle_route"


# =============================================================================
# calculate_centroid
# =============================================================================


class TestCalculateCentroid:
    """Verifies [cycle-route-assessment:GeoJSONConverter/TS-02]."""

    def test_square_centroid(self):
        """Centroid of a unit square at origin."""
        ring = [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
        lon, lat = calculate_centroid(ring)
        assert lon == pytest.approx(0.5)
        assert lat == pytest.approx(0.5)

    def test_closed_ring_excludes_closing_point(self):
        """Closing point should not skew the centroid."""
        ring = [[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]
        lon, lat = calculate_centroid(ring)
        assert lon == pytest.approx(1.0)
        assert lat == pytest.approx(1.0)

    def test_open_ring(self):
        """Ring that isn't closed still calculates centroid."""
        ring = [[0, 0], [2, 0], [2, 2], [0, 2]]
        lon, lat = calculate_centroid(ring)
        assert lon == pytest.approx(1.0)
        assert lat == pytest.approx(1.0)

    def test_empty_ring(self):
        """Empty ring returns origin."""
        lon, lat = calculate_centroid([])
        assert lon == 0.0
        assert lat == 0.0

    def test_single_point_ring(self):
        """Single point ring returns that point."""
        ring = [[-1.15, 51.9]]
        lon, lat = calculate_centroid(ring)
        assert lon == pytest.approx(-1.15)
        assert lat == pytest.approx(51.9)


# =============================================================================
# esri_to_geojson
# =============================================================================


class TestEsriToGeojson:
    """Verifies [cycle-route-assessment:GeoJSONConverter/TS-01] through TS-04."""

    def test_simple_polygon_converted(self):
        """[GeoJSONConverter/TS-01] Simple polygon produces valid GeoJSON FeatureCollection."""
        rings = [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
        attributes = {
            "DLGSDST.dbo.Planning_ArcGIS_Link_Public.application_number": "21/03267/OUT",
            "DLGSDST.dbo.Planning_ArcGIS_Link_Public.location": "Test Location",
            "SHAPE.STArea()": 5000,
        }

        result = esri_to_geojson(rings, attributes)

        assert result["type"] == "FeatureCollection"
        assert len(result["features"]) == 2

        polygon = result["features"][0]
        assert polygon["type"] == "Feature"
        assert polygon["geometry"]["type"] == "Polygon"
        assert polygon["geometry"]["coordinates"] == rings

        centroid = result["features"][1]
        assert centroid["type"] == "Feature"
        assert centroid["geometry"]["type"] == "Point"

    def test_centroid_feature_present(self):
        """[GeoJSONConverter/TS-02] Centroid is the geometric centre of the polygon."""
        rings = [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]
        attributes = {"SHAPE.STArea()": 4}

        result = esri_to_geojson(rings, attributes)
        centroid_coords = result["features"][1]["geometry"]["coordinates"]

        assert centroid_coords[0] == pytest.approx(1.0)  # lon
        assert centroid_coords[1] == pytest.approx(1.0)  # lat

    def test_multi_ring_polygon(self):
        """[GeoJSONConverter/TS-03] Multi-ring polygon (exterior + hole) handled."""
        exterior = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
        hole = [[2, 2], [8, 2], [8, 8], [2, 8], [2, 2]]
        rings = [exterior, hole]
        attributes = {}

        result = esri_to_geojson(rings, attributes)

        polygon_geom = result["features"][0]["geometry"]
        assert polygon_geom["type"] == "Polygon"
        assert len(polygon_geom["coordinates"]) == 2

    def test_properties_preserved(self):
        """[GeoJSONConverter/TS-04] Properties extracted from ESRI attributes."""
        rings = [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
        attributes = {
            "DLGSDST.dbo.Planning_ArcGIS_Link_Public.application_number": "21/03267/OUT",
            "DLGSDST.dbo.Planning_ArcGIS_Link_Public.location": "Land at Test Road\r\nBicester",
            "SHAPE.STArea()": 5000,
        }

        result = esri_to_geojson(rings, attributes)
        props = result["features"][0]["properties"]

        assert props["application_ref"] == "21/03267/OUT"
        assert props["address"] == "Land at Test Road, Bicester"
        assert props["area_sqm"] == 5000
        assert props["feature_type"] == "site_boundary"

    def test_large_site_centroid_note(self):
        """[CycleRouteMCP/TS-05] Large site gets centroid note about access point."""
        rings = [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
        attributes = {"SHAPE.STArea()": 150000}  # > 100000 sqm

        result = esri_to_geojson(rings, attributes)
        centroid_props = result["features"][1]["properties"]

        assert "large site" in centroid_props["centroid_note"].lower()

    def test_normal_site_centroid_note(self):
        """Normal sites get standard centroid note."""
        rings = [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
        attributes = {"SHAPE.STArea()": 5000}

        result = esri_to_geojson(rings, attributes)
        centroid_props = result["features"][1]["properties"]

        assert "geometric centre" in centroid_props["centroid_note"].lower()

    def test_gis_prefix_attributes(self):
        """Attributes with CORPGIS prefix are also extracted."""
        rings = [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
        attributes = {
            "CORPGIS.MASTERGOV.DEF_Planning.APPLICATION_REF": "22/00001/F",
        }

        result = esri_to_geojson(rings, attributes)
        # _extract_attribute tries short_name "APPLICATION_REF" with GIS prefix
        props = result["features"][0]["properties"]
        assert props["application_ref"] == "22/00001/F"


# =============================================================================
# parse_arcgis_response
# =============================================================================


class TestParseArcgisResponse:
    """Test the full ArcGIS response parser."""

    def test_valid_response_parsed(self):
        """Real ArcGIS fixture produces valid GeoJSON."""
        fixture_path = FIXTURES_DIR / "arcgis_response_21_03267.json"
        with open(fixture_path) as f:
            response = json.load(f)

        result = parse_arcgis_response(response)

        assert result is not None
        assert result["type"] == "FeatureCollection"
        assert len(result["features"]) == 2

        polygon = result["features"][0]
        assert polygon["geometry"]["type"] == "Polygon"
        assert len(polygon["geometry"]["coordinates"]) >= 1

        centroid = result["features"][1]
        assert centroid["geometry"]["type"] == "Point"
        coords = centroid["geometry"]["coordinates"]
        # Bicester area coordinates
        assert -1.21 < coords[0] < -1.1  # longitude
        assert 51.85 < coords[1] < 51.96  # latitude

    def test_empty_features(self):
        """Empty features list returns None."""
        result = parse_arcgis_response({"features": []})
        assert result is None

    def test_missing_features_key(self):
        """Missing features key returns None."""
        result = parse_arcgis_response({})
        assert result is None

    def test_no_rings(self):
        """Feature with no geometry rings returns None."""
        response = {
            "features": [
                {"geometry": {}, "attributes": {}}
            ]
        }
        result = parse_arcgis_response(response)
        assert result is None
