"""
Tests for route infrastructure analyser.

Verifies [cycle-route-assessment:FR-002] - Query Overpass for way tags, determine provision
Verifies [cycle-route-assessment:NFR-003] - Rate limiting for Overpass calls

Verifies test scenarios:
- [cycle-route-assessment:InfrastructureAnalyser/TS-01] Segregated cycleway detected
- [cycle-route-assessment:InfrastructureAnalyser/TS-02] Shared-use path detected
- [cycle-route-assessment:InfrastructureAnalyser/TS-03] On-road lane detected
- [cycle-route-assessment:InfrastructureAnalyser/TS-04] No provision detected
- [cycle-route-assessment:InfrastructureAnalyser/TS-05] Speed limit inferred
- [cycle-route-assessment:InfrastructureAnalyser/TS-06] Unknown surface handled
"""

import pytest

from src.mcp_servers.cycle_route.infrastructure import (
    RouteSegment,
    build_overpass_query,
    classify_provision,
    extract_lit,
    extract_speed_limit,
    extract_surface,
    parse_overpass_ways,
    summarise_provision,
)

# =============================================================================
# classify_provision
# =============================================================================


class TestClassifyProvision:
    """Verifies [cycle-route-assessment:InfrastructureAnalyser/TS-01] through TS-04."""

    def test_dedicated_cycleway(self):
        """[InfrastructureAnalyser/TS-01] highway=cycleway → segregated."""
        assert classify_provision({"highway": "cycleway"}) == "segregated"

    def test_shared_use_path(self):
        """[InfrastructureAnalyser/TS-02] Path with bicycle+foot designated → shared_use."""
        tags = {"highway": "path", "bicycle": "designated", "foot": "designated"}
        assert classify_provision(tags) == "shared_use"

    def test_footway_bicycle_yes_foot_yes(self):
        """Footway with bicycle=yes and foot=yes → shared_use."""
        tags = {"highway": "footway", "bicycle": "yes", "foot": "yes"}
        assert classify_provision(tags) == "shared_use"

    def test_path_bicycle_designated_only(self):
        """Path with bicycle=designated but no foot tag → segregated."""
        tags = {"highway": "path", "bicycle": "designated"}
        assert classify_provision(tags) == "segregated"

    def test_shared_designation(self):
        """Path with shared use designation → shared_use."""
        tags = {"highway": "path", "designation": "shared use path"}
        assert classify_provision(tags) == "shared_use"

    def test_path_bicycle_yes_no_foot(self):
        """Path with bicycle=yes but no foot → segregated (bicycle designated path)."""
        tags = {"highway": "path", "bicycle": "yes"}
        assert classify_provision(tags) == "segregated"

    def test_path_no_cycling(self):
        """Path without bicycle tags → none."""
        tags = {"highway": "path"}
        assert classify_provision(tags) == "none"

    def test_on_road_lane(self):
        """[InfrastructureAnalyser/TS-03] Road with cycleway=lane → on_road_lane."""
        tags = {"highway": "secondary", "cycleway": "lane"}
        assert classify_provision(tags) == "on_road_lane"

    def test_cycleway_track(self):
        """Road with cycleway=track → segregated."""
        tags = {"highway": "primary", "cycleway": "track"}
        assert classify_provision(tags) == "segregated"

    def test_cycleway_separate(self):
        """Road with cycleway=separate → segregated."""
        tags = {"highway": "primary", "cycleway": "separate"}
        assert classify_provision(tags) == "segregated"

    def test_cycleway_left_lane(self):
        """cycleway:left=lane → on_road_lane."""
        tags = {"highway": "tertiary", "cycleway:left": "lane"}
        assert classify_provision(tags) == "on_road_lane"

    def test_cycleway_both_track(self):
        """cycleway:both=track → segregated."""
        tags = {"highway": "primary", "cycleway:both": "track"}
        assert classify_provision(tags) == "segregated"

    def test_shared_lane(self):
        """cycleway=shared_lane → advisory_lane."""
        tags = {"highway": "residential", "cycleway": "shared_lane"}
        assert classify_provision(tags) == "advisory_lane"

    def test_share_busway(self):
        """cycleway=share_busway → advisory_lane."""
        tags = {"highway": "primary", "cycleway": "share_busway"}
        assert classify_provision(tags) == "advisory_lane"

    def test_no_provision(self):
        """[InfrastructureAnalyser/TS-04] Primary road with no cycleway tags → none."""
        tags = {"highway": "primary"}
        assert classify_provision(tags) == "none"

    def test_residential_no_provision(self):
        """Residential road with no cycleway tags → none."""
        tags = {"highway": "residential"}
        assert classify_provision(tags) == "none"


# =============================================================================
# extract_speed_limit
# =============================================================================


class TestExtractSpeedLimit:
    """Verifies [cycle-route-assessment:InfrastructureAnalyser/TS-05]."""

    def test_explicit_speed(self):
        """Explicit maxspeed tag is parsed."""
        assert extract_speed_limit({"maxspeed": "30 mph"}) == 30

    def test_explicit_speed_no_unit(self):
        """Maxspeed without unit."""
        assert extract_speed_limit({"maxspeed": "40"}) == 40

    def test_residential_default(self):
        """[InfrastructureAnalyser/TS-05] Residential without maxspeed → 30."""
        assert extract_speed_limit({"highway": "residential"}) == 30

    def test_trunk_default(self):
        """Trunk road without maxspeed → 60."""
        assert extract_speed_limit({"highway": "trunk"}) == 60

    def test_cycleway_default(self):
        """Cycleway → 0 (off-road)."""
        assert extract_speed_limit({"highway": "cycleway"}) == 0

    def test_living_street_default(self):
        """Living street → 20."""
        assert extract_speed_limit({"highway": "living_street"}) == 20

    def test_unknown_highway_default(self):
        """Unknown highway type → 30 fallback."""
        assert extract_speed_limit({"highway": "some_new_type"}) == 30

    def test_invalid_maxspeed(self):
        """Invalid maxspeed falls back to highway default."""
        assert extract_speed_limit({"maxspeed": "signals", "highway": "primary"}) == 60


# =============================================================================
# extract_surface and extract_lit
# =============================================================================


class TestExtractSurface:
    """Verifies [cycle-route-assessment:InfrastructureAnalyser/TS-06]."""

    def test_explicit_surface(self):
        """Explicit surface tag returned."""
        assert extract_surface({"surface": "asphalt"}) == "asphalt"

    def test_unknown_surface(self):
        """[InfrastructureAnalyser/TS-06] Missing surface → 'unknown'."""
        assert extract_surface({}) == "unknown"


class TestExtractLit:
    def test_lit_yes(self):
        assert extract_lit({"lit": "yes"}) is True

    def test_lit_no(self):
        assert extract_lit({"lit": "no"}) is False

    def test_lit_unknown(self):
        assert extract_lit({}) is None

    def test_lit_other_value(self):
        assert extract_lit({"lit": "limited"}) is None


# =============================================================================
# build_overpass_query
# =============================================================================


class TestBuildOverpassQuery:
    def test_query_contains_highway_filter(self):
        """Query filters for highway ways."""
        coords = [[-1.15, 51.9], [-1.14, 51.91]]
        query = build_overpass_query(coords)
        assert '["highway"]' in query

    def test_query_contains_coordinates(self):
        """Query includes coordinate points."""
        coords = [[-1.15, 51.9], [-1.14, 51.91]]
        query = build_overpass_query(coords)
        assert "51.9" in query
        assert "-1.15" in query

    def test_query_samples_long_routes(self):
        """Long routes are sampled to avoid huge queries."""
        coords = [[-1.15 + i * 0.001, 51.9] for i in range(200)]
        query = build_overpass_query(coords)
        # Should not include all 200 coordinates
        coord_count = query.count(",51.9")
        assert coord_count < 200

    def test_query_includes_endpoint(self):
        """Last coordinate is always included."""
        coords = [[-1.15 + i * 0.001, 51.9] for i in range(100)]
        last_lon = coords[-1][0]
        query = build_overpass_query(coords)
        assert f"{last_lon}" in query


# =============================================================================
# parse_overpass_ways
# =============================================================================


class TestParseOverpassWays:
    def test_basic_parsing(self):
        """Ways are parsed into RouteSegments."""
        response = {
            "elements": [
                {
                    "type": "way",
                    "id": 12345,
                    "tags": {
                        "highway": "cycleway",
                        "surface": "asphalt",
                        "lit": "yes",
                        "name": "NCN Route 51",
                    },
                },
                {
                    "type": "way",
                    "id": 67890,
                    "tags": {
                        "highway": "secondary",
                        "maxspeed": "30 mph",
                        "surface": "asphalt",
                        "name": "Buckingham Road",
                    },
                },
            ]
        }

        segments = parse_overpass_ways(response, 1000)

        assert len(segments) == 2
        assert segments[0].provision == "segregated"
        assert segments[0].name == "NCN Route 51"
        assert segments[0].distance_m == pytest.approx(500)
        assert segments[1].provision == "none"
        assert segments[1].speed_limit == 30

    def test_skips_proposed_highways(self):
        """Proposed/construction highways are filtered out."""
        response = {
            "elements": [
                {
                    "type": "way",
                    "id": 1,
                    "tags": {"highway": "proposed"},
                },
                {
                    "type": "way",
                    "id": 2,
                    "tags": {"highway": "residential", "name": "Main St"},
                },
            ]
        }

        segments = parse_overpass_ways(response, 500)
        assert len(segments) == 1
        assert segments[0].highway == "residential"

    def test_empty_response(self):
        """Empty elements list returns empty segments."""
        assert parse_overpass_ways({"elements": []}, 1000) == []

    def test_nodes_only_response(self):
        """Response with only nodes (no ways) returns empty."""
        response = {
            "elements": [
                {"type": "node", "id": 1, "lat": 51.9, "lon": -1.15},
            ]
        }
        assert parse_overpass_ways(response, 1000) == []

    def test_unnamed_way(self):
        """Way without name tag gets 'Unnamed'."""
        response = {
            "elements": [
                {"type": "way", "id": 1, "tags": {"highway": "path"}},
            ]
        }
        segments = parse_overpass_ways(response, 100)
        assert segments[0].name == "Unnamed"


# =============================================================================
# summarise_provision
# =============================================================================


class TestSummariseProvision:
    def test_single_type(self):
        """Single provision type sums correctly."""
        segments = [
            RouteSegment(1, "segregated", "cycleway", 0, "asphalt", True, 500, "Route"),
            RouteSegment(2, "segregated", "cycleway", 0, "asphalt", True, 300, "Route"),
        ]
        result = summarise_provision(segments)
        assert result == {"segregated": 800.0}

    def test_mixed_types(self):
        """Mixed provision types each summed."""
        segments = [
            RouteSegment(1, "segregated", "cycleway", 0, "asphalt", True, 500, "A"),
            RouteSegment(2, "none", "primary", 60, "asphalt", True, 300, "B"),
            RouteSegment(3, "shared_use", "path", 0, "asphalt", None, 200, "C"),
        ]
        result = summarise_provision(segments)
        assert result["segregated"] == 500.0
        assert result["none"] == 300.0
        assert result["shared_use"] == 200.0
