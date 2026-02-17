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
    analyse_transitions,
    bearing_difference,
    build_overpass_query,
    calculate_way_bearing,
    classify_provision,
    detect_parallel_provision,
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

    def test_query_includes_barrier_node_query(self):
        """[route-transition-analysis:build_overpass_query/TS-01] Barrier node query."""
        coords = [[-1.15, 51.9], [-1.14, 51.91]]
        query = build_overpass_query(coords)
        assert "node(around:15," in query
        assert '["barrier"~"cycle_barrier|bollard|gate|stile|lift_gate"]' in query

    def test_query_includes_crossing_node_query(self):
        """[route-transition-analysis:build_overpass_query/TS-02] Crossing node query."""
        coords = [[-1.15, 51.9], [-1.14, 51.91]]
        query = build_overpass_query(coords)
        assert "node(around:20," in query
        assert '["crossing"]' in query

    def test_query_retains_way_query(self):
        """[route-transition-analysis:build_overpass_query/TS-03] Way query preserved."""
        coords = [[-1.15, 51.9], [-1.14, 51.91]]
        query = build_overpass_query(coords)
        assert "way(around:" in query
        assert '["highway"]' in query


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


# =============================================================================
# RouteSegment.original_provision
# =============================================================================


class TestRouteSegmentOriginalProvision:
    def test_includes_original_provision_when_upgraded(self):
        """to_dict() includes original_provision when set."""
        seg = RouteSegment(1, "segregated", "secondary", 30, "asphalt", True, 500, "Banbury Rd",
                          original_provision="none")
        d = seg.to_dict()
        assert d["provision"] == "segregated"
        assert d["original_provision"] == "none"

    def test_omits_original_provision_when_not_set(self):
        """to_dict() omits original_provision when None."""
        seg = RouteSegment(1, "none", "secondary", 30, "asphalt", True, 500, "Banbury Rd")
        d = seg.to_dict()
        assert "original_provision" not in d


# =============================================================================
# build_overpass_query — out geom
# =============================================================================


class TestBuildOverpassQueryGeom:
    def test_query_uses_out_geom(self):
        """Query requests geometry output."""
        coords = [[-1.15, 51.9], [-1.14, 51.91]]
        query = build_overpass_query(coords)
        assert "out geom" in query
        assert "out body" not in query


# =============================================================================
# calculate_way_bearing
# =============================================================================


class TestCalculateWayBearing:
    def test_east_west_bearing(self):
        """Way running east returns ~90 degrees."""
        geometry = [
            {"lat": 51.90, "lon": -1.16},
            {"lat": 51.90, "lon": -1.14},
        ]
        bearing = calculate_way_bearing(geometry)
        assert bearing is not None
        assert abs(bearing - 90) < 2

    def test_north_south_bearing(self):
        """Way running north returns ~0 degrees."""
        geometry = [
            {"lat": 51.89, "lon": -1.15},
            {"lat": 51.91, "lon": -1.15},
        ]
        bearing = calculate_way_bearing(geometry)
        assert bearing is not None
        assert bearing < 2 or bearing > 358

    def test_single_node_returns_none(self):
        """Way with only one node returns None."""
        geometry = [{"lat": 51.90, "lon": -1.15}]
        assert calculate_way_bearing(geometry) is None

    def test_empty_returns_none(self):
        """Empty geometry returns None."""
        assert calculate_way_bearing([]) is None


# =============================================================================
# bearing_difference
# =============================================================================


class TestBearingDifference:
    def test_same_bearing(self):
        """Same bearing returns 0."""
        assert bearing_difference(90, 90) == 0

    def test_opposite_directions_parallel(self):
        """Opposite directions (east vs west) treated as parallel."""
        assert bearing_difference(90, 270) == 0

    def test_wrap_around(self):
        """350 and 10 degrees differ by 20."""
        assert bearing_difference(350, 10) == 20

    def test_perpendicular(self):
        """North vs east is 90 degrees."""
        assert bearing_difference(0, 90) == 90

    def test_slight_angle(self):
        """Small difference correctly calculated."""
        assert bearing_difference(85, 95) == 10

    def test_opposite_wrap(self):
        """170 and 350 are opposite-ish (180 apart → 0)."""
        assert bearing_difference(170, 350) == 0


# =============================================================================
# detect_parallel_provision
# =============================================================================


def _make_overpass_data(ways):
    """Helper to build Overpass-style response with geometry."""
    return {"elements": ways}


def _make_road_way(way_id, lat_start, lon_start, lat_end, lon_end, tags=None):
    """Helper to build a road way element."""
    base_tags = {"highway": "secondary", "name": "Test Road"}
    if tags:
        base_tags.update(tags)
    return {
        "type": "way",
        "id": way_id,
        "tags": base_tags,
        "geometry": [
            {"lat": lat_start, "lon": lon_start},
            {"lat": lat_end, "lon": lon_end},
        ],
    }


def _make_cycleway_way(way_id, lat_start, lon_start, lat_end, lon_end, tags=None):
    """Helper to build a cycleway way element."""
    base_tags = {"highway": "cycleway", "surface": "asphalt"}
    if tags:
        base_tags.update(tags)
    return {
        "type": "way",
        "id": way_id,
        "tags": base_tags,
        "geometry": [
            {"lat": lat_start, "lon": lon_start},
            {"lat": lat_end, "lon": lon_end},
        ],
    }


class TestDetectParallelProvision:
    def test_parallel_cycleway_upgrades_provision(self):
        """Road segment with parallel cycleway gets provision upgraded."""
        # Road running east-west
        road = _make_road_way(100, 51.90, -1.16, 51.90, -1.14)
        # Parallel cycleway running east-west (slightly offset)
        cycleway = _make_cycleway_way(200, 51.9001, -1.16, 51.9001, -1.14)

        segments = [
            RouteSegment(100, "none", "secondary", 30, "asphalt", True, 500, "Test Road"),
        ]
        overpass = _make_overpass_data([road, cycleway])

        result = detect_parallel_provision(segments, overpass)
        assert result[0].provision == "segregated"
        assert result[0].original_provision == "none"

    def test_no_upgrade_without_parallel(self):
        """Road segment without parallel cycleway keeps original provision."""
        road = _make_road_way(100, 51.90, -1.16, 51.90, -1.14)

        segments = [
            RouteSegment(100, "none", "secondary", 30, "asphalt", True, 500, "Test Road"),
        ]
        overpass = _make_overpass_data([road])

        result = detect_parallel_provision(segments, overpass)
        assert result[0].provision == "none"
        assert result[0].original_provision is None

    def test_no_upgrade_when_bearing_differs(self):
        """Perpendicular cycleway does not upgrade provision."""
        # Road running east-west
        road = _make_road_way(100, 51.90, -1.16, 51.90, -1.14)
        # Cycleway running north-south (perpendicular)
        cycleway = _make_cycleway_way(200, 51.89, -1.15, 51.91, -1.15)

        segments = [
            RouteSegment(100, "none", "secondary", 30, "asphalt", True, 500, "Test Road"),
        ]
        overpass = _make_overpass_data([road, cycleway])

        result = detect_parallel_provision(segments, overpass)
        assert result[0].provision == "none"
        assert result[0].original_provision is None

    def test_best_provision_selected(self):
        """When multiple candidates match, the best provision is used."""
        # Road running east-west
        road = _make_road_way(100, 51.90, -1.16, 51.90, -1.14)
        # Shared-use path parallel
        shared = _make_cycleway_way(
            200, 51.9001, -1.16, 51.9001, -1.14,
            tags={"highway": "path", "bicycle": "designated", "foot": "designated"},
        )
        # Segregated cycleway parallel
        segregated = _make_cycleway_way(300, 51.9002, -1.16, 51.9002, -1.14)

        segments = [
            RouteSegment(100, "none", "secondary", 30, "asphalt", True, 500, "Test Road"),
        ]
        overpass = _make_overpass_data([road, shared, segregated])

        result = detect_parallel_provision(segments, overpass)
        assert result[0].provision == "segregated"
        assert result[0].original_provision == "none"

    def test_already_good_provision_not_upgraded(self):
        """Segment with segregated provision is not touched."""
        road = _make_road_way(100, 51.90, -1.16, 51.90, -1.14,
                             tags={"highway": "cycleway"})
        cycleway = _make_cycleway_way(200, 51.9001, -1.16, 51.9001, -1.14)

        segments = [
            RouteSegment(100, "segregated", "cycleway", 0, "asphalt", True, 500, "Route"),
        ]
        overpass = _make_overpass_data([road, cycleway])

        result = detect_parallel_provision(segments, overpass)
        assert result[0].provision == "segregated"
        assert result[0].original_provision is None

    def test_cycleway_already_in_segments_not_candidate(self):
        """A cycleway that's already a route segment is not used as a candidate."""
        road = _make_road_way(100, 51.90, -1.16, 51.90, -1.14)
        # This cycleway has the same way_id as a segment — should be excluded
        cycleway = _make_cycleway_way(200, 51.9001, -1.16, 51.9001, -1.14)

        segments = [
            RouteSegment(100, "none", "secondary", 30, "asphalt", True, 500, "Test Road"),
            RouteSegment(200, "segregated", "cycleway", 0, "asphalt", True, 200, "Cycle Path"),
        ]
        overpass = _make_overpass_data([road, cycleway])

        result = detect_parallel_provision(segments, overpass)
        # The road segment should NOT be upgraded because way 200 is already in segments
        assert result[0].provision == "none"
        assert result[0].original_provision is None


# =============================================================================
# analyse_transitions
# =============================================================================


def _make_barrier_node(node_id, lat, lon, barrier_type="bollard"):
    """Helper to build a barrier node element."""
    return {
        "type": "node",
        "id": node_id,
        "lat": lat,
        "lon": lon,
        "tags": {"barrier": barrier_type},
    }


def _make_crossing_node(node_id, lat, lon, crossing_type="uncontrolled"):
    """Helper to build a crossing node element."""
    return {
        "type": "node",
        "id": node_id,
        "lat": lat,
        "lon": lon,
        "tags": {"crossing": crossing_type},
    }


class TestAnalyseTransitions:
    """Verifies [route-transition-analysis:analyse_transitions/TS-19] through TS-29."""

    def test_barrier_nodes_detected(self):
        """[TS-19] Barrier nodes detected from Overpass data."""
        elements = [
            _make_barrier_node(1001, 51.90, -1.15, "bollard"),
            _make_barrier_node(1002, 51.91, -1.14, "cycle_barrier"),
        ]
        segments = [
            RouteSegment(100, "segregated", "cycleway", 0, "asphalt", True, 500, "Route"),
        ]
        result = analyse_transitions(segments, {"elements": elements})
        assert len(result["barriers"]) == 2
        assert result["barriers"][0]["type"] == "bollard"
        assert result["barriers"][0]["node_id"] == 1001
        assert result["barriers"][1]["type"] == "cycle_barrier"
        assert result["barrier_count"] == 2

    def test_duplicate_barriers_deduplicated(self):
        """[TS-20] Duplicate barriers within 5m deduplicated."""
        # Two bollards 3m apart (approx 0.00003 degrees lat)
        elements = [
            _make_barrier_node(1001, 51.900000, -1.15, "bollard"),
            _make_barrier_node(1002, 51.900027, -1.15, "bollard"),
        ]
        segments = [
            RouteSegment(100, "segregated", "cycleway", 0, "asphalt", True, 500, "Route"),
        ]
        result = analyse_transitions(segments, {"elements": elements})
        assert len(result["barriers"]) == 1

    def test_non_priority_crossing_detected(self):
        """[TS-21] Non-priority crossing at off-road to road transition."""
        segments = [
            RouteSegment(100, "segregated", "cycleway", 0, "asphalt", True, 500, "Cycle Path"),
            RouteSegment(101, "none", "residential", 30, "asphalt", True, 500, "Main St"),
        ]
        result = analyse_transitions(segments, {"elements": []})
        assert len(result["non_priority_crossings"]) == 1
        assert result["non_priority_crossings"][0]["road_speed_limit"] == 30
        assert result["non_priority_crossing_count"] == 1

    def test_signalised_crossing_not_counted(self):
        """[TS-22] Signalised crossing not counted as non-priority."""
        elements = [
            _make_crossing_node(2001, 51.90, -1.15, "traffic_signals"),
        ]
        segments = [
            RouteSegment(100, "segregated", "cycleway", 0, "asphalt", True, 500, "Cycle Path"),
            RouteSegment(101, "none", "primary", 40, "asphalt", True, 500, "A Road"),
        ]
        result = analyse_transitions(segments, {"elements": elements})
        assert len(result["non_priority_crossings"]) == 0

    def test_marked_crossing_not_counted(self):
        """[TS-23] Marked crossing not counted as non-priority."""
        elements = [
            _make_crossing_node(2001, 51.90, -1.15, "marked"),
        ]
        segments = [
            RouteSegment(100, "shared_use", "path", 0, "asphalt", None, 500, "Shared Path"),
            RouteSegment(101, "none", "secondary", 30, "asphalt", True, 500, "B Road"),
        ]
        result = analyse_transitions(segments, {"elements": elements})
        assert len(result["non_priority_crossings"]) == 0

    def test_side_change_detected(self):
        """[TS-24] Side change detected in off-road, road, off-road pattern."""
        segments = [
            RouteSegment(100, "segregated", "cycleway", 0, "asphalt", True, 300, "Cycle Path"),
            RouteSegment(101, "none", "residential", 30, "asphalt", True, 100, "Cross St"),
            RouteSegment(102, "shared_use", "path", 0, "asphalt", None, 400, "Shared Path"),
        ]
        result = analyse_transitions(segments, {"elements": []})
        assert len(result["side_changes"]) == 1
        assert result["side_changes"][0]["road_name"] == "Cross St"
        assert result["side_change_count"] == 1

    def test_no_side_change_without_road(self):
        """[TS-25] No side change for off-road to off-road (no road in between)."""
        segments = [
            RouteSegment(100, "segregated", "cycleway", 0, "asphalt", True, 500, "Path A"),
            RouteSegment(101, "shared_use", "path", 0, "asphalt", None, 500, "Path B"),
        ]
        result = analyse_transitions(segments, {"elements": []})
        assert len(result["side_changes"]) == 0

    def test_directness_differential_from_parallel(self):
        """[TS-26] Directness differential calculated from parallel detection."""
        segments = [
            RouteSegment(100, "segregated", "secondary", 30, "asphalt", True, 500, "Rd",
                         original_provision="none"),
            RouteSegment(101, "segregated", "secondary", 30, "asphalt", True, 500, "Rd",
                         original_provision="none"),
        ]
        result = analyse_transitions(segments, {"elements": []})
        assert result["directness_differential"] is not None
        assert result["directness_differential"] >= 1.0

    def test_directness_differential_none_without_parallel(self):
        """[TS-27] Directness differential is None when no parallel sections."""
        segments = [
            RouteSegment(100, "segregated", "cycleway", 0, "asphalt", True, 500, "Path"),
        ]
        result = analyse_transitions(segments, {"elements": []})
        assert result["directness_differential"] is None

    def test_empty_barriers_when_no_nodes(self):
        """[TS-28] Empty barriers when no barrier nodes."""
        segments = [
            RouteSegment(100, "segregated", "cycleway", 0, "asphalt", True, 500, "Path"),
        ]
        result = analyse_transitions(segments, {"elements": []})
        assert result["barriers"] == []
        assert result["barrier_count"] == 0

    def test_crossing_includes_road_name_and_speed(self):
        """[TS-29] Non-priority crossing includes road name and speed limit."""
        segments = [
            RouteSegment(100, "segregated", "cycleway", 0, "asphalt", True, 500, "Cycle Path"),
            RouteSegment(101, "none", "secondary", 30, "asphalt", True, 500, "Buckingham Road"),
        ]
        result = analyse_transitions(segments, {"elements": []})
        assert len(result["non_priority_crossings"]) == 1
        assert result["non_priority_crossings"][0]["road_name"] == "Buckingham Road"
        assert result["non_priority_crossings"][0]["road_speed_limit"] == 30
