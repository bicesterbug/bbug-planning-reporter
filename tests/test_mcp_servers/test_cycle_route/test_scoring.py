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
    MAX_TRANSITION_POINTS,
    _score_transitions,
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
        result = score_route(segments, 2000, shortest_distance_m=2100)

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
        result = score_route(segments, 2000, shortest_distance_m=2100)

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
        """Score breakdown includes all 6 factors."""
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
        assert "transition_quality" in breakdown

    def test_max_points_documented(self):
        """Max points per factor are in the result."""
        segments = [_make_segment(distance_m=1000)]
        result = score_route(segments, 1000)

        assert result["max_points"]["segregation"] == MAX_SEGREGATION_POINTS
        assert result["max_points"]["speed_safety"] == MAX_SPEED_POINTS
        assert result["max_points"]["surface_quality"] == MAX_SURFACE_POINTS
        assert result["max_points"]["directness"] == MAX_DIRECTNESS_POINTS
        assert result["max_points"]["junction_safety"] == MAX_JUNCTION_POINTS
        assert result["max_points"]["transition_quality"] == MAX_TRANSITION_POINTS

    def test_total_max_is_100(self):
        """All max points sum to 100."""
        total = (
            MAX_SEGREGATION_POINTS + MAX_SPEED_POINTS + MAX_SURFACE_POINTS
            + MAX_DIRECTNESS_POINTS + MAX_JUNCTION_POINTS + MAX_TRANSITION_POINTS
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
        result = score_route(segments, 1000, shortest_distance_m=1050)
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


# =============================================================================
# Transition scoring
# =============================================================================


def _make_transitions(barriers=None, crossings=None, side_changes=None,
                      unavailable=False):
    """Create a transitions dict for testing."""
    result = {
        "barriers": barriers or [],
        "non_priority_crossings": crossings or [],
        "side_changes": side_changes or [],
        "directness_differential": None,
    }
    if unavailable:
        result["unavailable"] = True
    return result


class TestScoreTransitions:
    """Verifies [route-transition-analysis:_score_transitions/TS-30] through TS-36."""

    def test_clean_route_full_points(self):
        """[TS-30] Clean route scores full transition points."""
        transitions = _make_transitions()
        assert _score_transitions(transitions) == MAX_TRANSITION_POINTS

    def test_barriers_penalised(self):
        """[TS-31] Barriers penalised at 2 points each."""
        barriers = [{"type": "bollard"}, {"type": "gate"}, {"type": "cycle_barrier"}]
        transitions = _make_transitions(barriers=barriers)
        assert _score_transitions(transitions) == MAX_TRANSITION_POINTS - 6  # 4.0

    def test_fast_road_crossings_penalised(self):
        """[TS-32] Non-priority crossings on fast roads penalised at 1.5 points."""
        crossings = [
            {"road_speed_limit": 30},
            {"road_speed_limit": 40},
        ]
        transitions = _make_transitions(crossings=crossings)
        assert _score_transitions(transitions) == MAX_TRANSITION_POINTS - 3  # 7.0

    def test_slow_road_crossings_penalised(self):
        """[TS-33] Non-priority crossings on slow roads penalised at 0.5 points."""
        crossings = [
            {"road_speed_limit": 20},
            {"road_speed_limit": 20},
        ]
        transitions = _make_transitions(crossings=crossings)
        assert _score_transitions(transitions) == MAX_TRANSITION_POINTS - 1  # 9.0

    def test_side_changes_penalised(self):
        """[TS-34] Side changes penalised at 1 point each."""
        side_changes = [{"road_name": "A"}, {"road_name": "B"}]
        transitions = _make_transitions(side_changes=side_changes)
        assert _score_transitions(transitions) == MAX_TRANSITION_POINTS - 2  # 8.0

    def test_score_clamped_to_zero(self):
        """[TS-35] Score clamped to zero when penalties exceed max."""
        barriers = [{"type": "bollard"}] * 6  # penalty = 12
        transitions = _make_transitions(barriers=barriers)
        assert _score_transitions(transitions) == 0

    def test_mixed_penalties_combine(self):
        """[TS-36] Mixed penalties combine correctly."""
        barriers = [{"type": "bollard"}]  # 2
        crossings = [{"road_speed_limit": 30}, {"road_speed_limit": 30}]  # 3
        side_changes = [{"road_name": "X"}]  # 1
        transitions = _make_transitions(
            barriers=barriers, crossings=crossings, side_changes=side_changes,
        )
        assert _score_transitions(transitions) == 4.0  # 10 - 6


class TestScoreRouteWithTransitions:
    """Verifies [route-transition-analysis:score_route/TS-04] through TS-10."""

    def test_updated_max_points_sum_to_100(self):
        """[TS-04] Updated max points sum to 100."""
        total = (
            MAX_SEGREGATION_POINTS + MAX_SPEED_POINTS + MAX_SURFACE_POINTS
            + MAX_DIRECTNESS_POINTS + MAX_JUNCTION_POINTS + MAX_TRANSITION_POINTS
        )
        assert total == 100

    def test_breakdown_includes_transition_quality(self):
        """[TS-05] Score breakdown includes transition_quality."""
        segments = [_make_segment(distance_m=1000)]
        transitions = _make_transitions()
        result = score_route(segments, 1000, transitions=transitions)
        assert "transition_quality" in result["breakdown"]
        assert result["max_points"]["transition_quality"] == 10

    def test_full_transition_score_no_issues(self):
        """[TS-06] Full transition score with no barriers or crossings."""
        segments = [_make_segment(distance_m=1000)]
        transitions = _make_transitions()
        result = score_route(segments, 1000, transitions=transitions)
        assert result["breakdown"]["transition_quality"] == MAX_TRANSITION_POINTS

    def test_barriers_reduce_transition_score(self):
        """[TS-07] Barriers reduce transition score."""
        segments = [_make_segment(distance_m=1000)]
        barriers = [{"type": "bollard"}] * 3
        transitions = _make_transitions(barriers=barriers)
        result = score_route(segments, 1000, transitions=transitions)
        assert result["breakdown"]["transition_quality"] < MAX_TRANSITION_POINTS

    def test_neutral_when_unavailable(self):
        """[TS-08] Neutral transition score when unavailable."""
        segments = [_make_segment(distance_m=1000)]
        transitions = _make_transitions(unavailable=True)
        result = score_route(segments, 1000, transitions=transitions)
        assert result["breakdown"]["transition_quality"] == 5.0

    def test_neutral_when_none(self):
        """[TS-09] Neutral transition score when transitions is None."""
        segments = [_make_segment(distance_m=1000)]
        result = score_route(segments, 1000)
        assert result["breakdown"]["transition_quality"] == 5.0

    def test_existing_factor_weights_reduced(self):
        """[TS-10] Existing scoring factor weights reduced proportionally."""
        segments = [_make_segment(distance_m=1000)]
        result = score_route(segments, 1000)
        assert result["max_points"]["segregation"] == 36
        assert result["max_points"]["speed_safety"] == 23
        assert result["max_points"]["surface_quality"] == 13
        assert result["max_points"]["directness"] == 9
        assert result["max_points"]["junction_safety"] == 9
