"""
Tests for LTN 1/20 route scorer.

Verifies [cycle-route-assessment:FR-003] - LTN 1/20 scoring with RAG rating
Verifies [cycle-route-assessment:NFR-004] - Transparent scoring with named constants

Verifies test scenarios:
- [cycle-route-assessment:RouteScorer/TS-01] Fully segregated route scores green
- [cycle-route-assessment:RouteScorer/TS-02] Mixed route scores amber
- [cycle-route-assessment:RouteScorer/TS-03] High-speed unsegregated scores red
- [cycle-route-assessment:RouteScorer/TS-04] Score breakdown returned
- [cycle-route-assessment:RouteScorer/TS-05] Short route flagged
"""

import pytest

from src.mcp_servers.cycle_route.infrastructure import RouteSegment
from src.mcp_servers.cycle_route.scoring import (
    AMBER_THRESHOLD,
    GREEN_THRESHOLD,
    MAX_DIRECTNESS_POINTS,
    MAX_JUNCTION_POINTS,
    MAX_SEGREGATION_POINTS,
    MAX_SPEED_POINTS,
    MAX_SURFACE_POINTS,
    score_route,
)


def _make_segment(
    provision: str = "none",
    highway: str = "residential",
    speed_limit: int = 30,
    surface: str = "asphalt",
    lit: bool | None = True,
    distance_m: float = 500,
    name: str = "Test Road",
) -> RouteSegment:
    """Create a RouteSegment with defaults for testing."""
    return RouteSegment(
        way_id=1,
        provision=provision,
        highway=highway,
        speed_limit=speed_limit,
        surface=surface,
        lit=lit,
        distance_m=distance_m,
        name=name,
    )


class TestScoreRouteGreen:
    """Verifies [cycle-route-assessment:RouteScorer/TS-01]."""

    def test_fully_segregated_good_surface_scores_green(self):
        """100% segregated, good surface, direct route → green."""
        segments = [
            _make_segment(provision="segregated", highway="cycleway",
                          speed_limit=0, surface="asphalt", distance_m=2000),
        ]
        result = score_route(segments, 2000, driving_distance_m=2100)

        assert result["score"] >= GREEN_THRESHOLD
        assert result["rating"] == "green"

    def test_mostly_segregated_scores_green(self):
        """90% segregated with 10% low-speed residential → green."""
        segments = [
            _make_segment(provision="segregated", highway="cycleway",
                          speed_limit=0, surface="asphalt", distance_m=1800),
            _make_segment(provision="none", highway="residential",
                          speed_limit=20, surface="asphalt", distance_m=200),
        ]
        result = score_route(segments, 2000, driving_distance_m=2100)

        assert result["score"] >= GREEN_THRESHOLD
        assert result["rating"] == "green"


class TestScoreRouteAmber:
    """Verifies [cycle-route-assessment:RouteScorer/TS-02]."""

    def test_mixed_route_scores_amber(self):
        """50% segregated, 50% on 30mph road → amber."""
        segments = [
            _make_segment(provision="segregated", highway="cycleway",
                          speed_limit=0, surface="asphalt", distance_m=1000),
            _make_segment(provision="none", highway="secondary",
                          speed_limit=30, surface="asphalt", distance_m=1000),
        ]
        result = score_route(segments, 2000)

        assert AMBER_THRESHOLD <= result["score"] < GREEN_THRESHOLD
        assert result["rating"] == "amber"


class TestScoreRouteRed:
    """Verifies [cycle-route-assessment:RouteScorer/TS-03]."""

    def test_high_speed_unsegregated_scores_red(self):
        """80% on 50mph road, no provision → red."""
        segments = [
            _make_segment(provision="none", highway="primary",
                          speed_limit=50, surface="asphalt", distance_m=1600),
            _make_segment(provision="none", highway="secondary",
                          speed_limit=40, surface="asphalt", distance_m=400),
        ]
        result = score_route(segments, 2000)

        assert result["score"] < AMBER_THRESHOLD
        assert result["rating"] == "red"


class TestScoreBreakdown:
    """Verifies [cycle-route-assessment:RouteScorer/TS-04]."""

    def test_breakdown_has_all_factors(self):
        """Score breakdown includes all 5 factors."""
        segments = [
            _make_segment(provision="segregated", distance_m=1000),
        ]
        result = score_route(segments, 1000)

        assert "breakdown" in result
        breakdown = result["breakdown"]
        assert "segregation" in breakdown
        assert "speed_safety" in breakdown
        assert "surface_quality" in breakdown
        assert "directness" in breakdown
        assert "junction_safety" in breakdown

    def test_max_points_documented(self):
        """Max points per factor are in the result."""
        segments = [_make_segment(distance_m=1000)]
        result = score_route(segments, 1000)

        assert result["max_points"]["segregation"] == MAX_SEGREGATION_POINTS
        assert result["max_points"]["speed_safety"] == MAX_SPEED_POINTS
        assert result["max_points"]["surface_quality"] == MAX_SURFACE_POINTS
        assert result["max_points"]["directness"] == MAX_DIRECTNESS_POINTS
        assert result["max_points"]["junction_safety"] == MAX_JUNCTION_POINTS

    def test_total_max_is_100(self):
        """All max points sum to 100."""
        total = (
            MAX_SEGREGATION_POINTS + MAX_SPEED_POINTS + MAX_SURFACE_POINTS
            + MAX_DIRECTNESS_POINTS + MAX_JUNCTION_POINTS
        )
        assert total == 100

    def test_score_clamped_to_0_100(self):
        """Score cannot exceed 100 or go below 0."""
        segments = [_make_segment(provision="segregated", distance_m=1000)]
        result = score_route(segments, 1000)
        assert 0 <= result["score"] <= 100


class TestScoreShortRoute:
    """Verifies [cycle-route-assessment:RouteScorer/TS-05]."""

    def test_short_route_flagged(self):
        """Route under 200m gets a short_route_note."""
        segments = [
            _make_segment(provision="segregated", distance_m=150),
        ]
        result = score_route(segments, 150)

        assert "short_route_note" in result
        assert "walking" in result["short_route_note"].lower()

    def test_normal_route_no_flag(self):
        """Route over threshold has no short_route_note."""
        segments = [_make_segment(distance_m=500)]
        result = score_route(segments, 500)

        assert "short_route_note" not in result


class TestScoreFactors:
    """Test individual scoring factor behaviours."""

    def test_shared_use_partial_credit(self):
        """Shared-use path gets 70% credit for segregation score."""
        seg_segregated = [_make_segment(provision="segregated", distance_m=1000)]
        seg_shared = [_make_segment(provision="shared_use", highway="path",
                                     speed_limit=0, distance_m=1000)]

        score_seg = score_route(seg_segregated, 1000)["breakdown"]["segregation"]
        score_shared = score_route(seg_shared, 1000)["breakdown"]["segregation"]

        assert score_shared == pytest.approx(score_seg * 0.7, abs=0.5)

    def test_low_speed_full_speed_points(self):
        """20mph or below on unsegregated section → full speed points."""
        segments = [_make_segment(provision="none", speed_limit=20, distance_m=1000)]
        result = score_route(segments, 1000)
        assert result["breakdown"]["speed_safety"] == MAX_SPEED_POINTS

    def test_no_unsegregated_full_speed_points(self):
        """All segregated → full speed points."""
        segments = [
            _make_segment(provision="segregated", speed_limit=0, distance_m=1000),
        ]
        result = score_route(segments, 1000)
        assert result["breakdown"]["speed_safety"] == MAX_SPEED_POINTS

    def test_poor_surface_low_score(self):
        """Gravel surface scores poorly."""
        segments = [_make_segment(surface="gravel", distance_m=1000)]
        result = score_route(segments, 1000)
        assert result["breakdown"]["surface_quality"] < MAX_SURFACE_POINTS * 0.5

    def test_direct_route_full_points(self):
        """Cycling distance close to driving distance → full directness points."""
        segments = [_make_segment(distance_m=1000)]
        result = score_route(segments, 1000, driving_distance_m=1050)
        assert result["breakdown"]["directness"] == MAX_DIRECTNESS_POINTS

    def test_no_driving_distance_half_points(self):
        """No driving distance available → half directness points."""
        segments = [_make_segment(distance_m=1000)]
        result = score_route(segments, 1000)
        assert result["breakdown"]["directness"] == MAX_DIRECTNESS_POINTS / 2

    def test_no_hostile_junctions_full_points(self):
        """No hostile junctions → full junction points."""
        segments = [
            _make_segment(provision="segregated", highway="cycleway",
                          speed_limit=0, distance_m=1000),
        ]
        result = score_route(segments, 1000)
        assert result["breakdown"]["junction_safety"] == MAX_JUNCTION_POINTS
