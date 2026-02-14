"""
LTN 1/20 route scorer.

Implements [cycle-route-assessment:FR-003] - LTN 1/20 scoring with RAG rating
Implements [cycle-route-assessment:NFR-004] - Transparent scoring with named constants

Implements test scenarios:
- [cycle-route-assessment:RouteScorer/TS-01] Fully segregated route scores green
- [cycle-route-assessment:RouteScorer/TS-02] Mixed route scores amber
- [cycle-route-assessment:RouteScorer/TS-03] High-speed unsegregated scores red
- [cycle-route-assessment:RouteScorer/TS-04] Score breakdown returned
- [cycle-route-assessment:RouteScorer/TS-05] Short route flagged
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.mcp_servers.cycle_route.infrastructure import RouteSegment

# =============================================================================
# Scoring Constants — LTN 1/20 inspired
# =============================================================================

# Maximum points per scoring factor
MAX_SEGREGATION_POINTS = 40  # Proportion of route on segregated infrastructure
MAX_SPEED_POINTS = 25  # Speed safety on unsegregated sections
MAX_SURFACE_POINTS = 15  # Surface quality across route
MAX_DIRECTNESS_POINTS = 10  # Route directness vs driving distance
MAX_JUNCTION_POINTS = 10  # Hostile junction penalty

# RAG rating thresholds
GREEN_THRESHOLD = 70
AMBER_THRESHOLD = 40

# Short route threshold (metres)
SHORT_ROUTE_THRESHOLD = 200

# Speed penalty thresholds (mph) for unsegregated sections
SPEED_NO_PENALTY = 20  # 20mph or below: full points
SPEED_LOW_PENALTY = 30  # 21-30mph: moderate penalty
SPEED_HIGH_PENALTY = 40  # 31-40mph: high penalty
# Above 40mph: maximum penalty

# Good surface types
GOOD_SURFACES = {"asphalt", "paved", "concrete", "concrete:plates", "paving_stones"}
FAIR_SURFACES = {"compacted", "fine_gravel"}
# Everything else (gravel, unpaved, dirt, grass, unknown) is poor


def _score_segregation(segments: list[RouteSegment]) -> float:
    """Score based on proportion of route on segregated or shared-use infrastructure."""
    total_distance = sum(s.distance_m for s in segments)
    if total_distance == 0:
        return 0

    segregated_distance = sum(
        s.distance_m for s in segments if s.provision == "segregated"
    )
    shared_distance = sum(
        s.distance_m for s in segments if s.provision == "shared_use"
    )
    lane_distance = sum(
        s.distance_m for s in segments if s.provision == "on_road_lane"
    )

    # Segregated = full credit, shared_use = 70% credit, lane = 40% credit
    weighted = segregated_distance + (shared_distance * 0.7) + (lane_distance * 0.4)
    proportion = weighted / total_distance

    return round(proportion * MAX_SEGREGATION_POINTS, 1)


def _score_speed(segments: list[RouteSegment]) -> float:
    """Score based on traffic speed on unsegregated sections (lower speed = better)."""
    unsegregated = [
        s for s in segments if s.provision in ("none", "advisory_lane")
    ]
    if not unsegregated:
        # No unsegregated sections — full points
        return MAX_SPEED_POINTS

    total_distance = sum(s.distance_m for s in unsegregated)
    if total_distance == 0:
        return MAX_SPEED_POINTS

    weighted_score = 0
    for seg in unsegregated:
        if seg.speed_limit <= SPEED_NO_PENALTY:
            factor = 1.0
        elif seg.speed_limit <= SPEED_LOW_PENALTY:
            factor = 0.6
        elif seg.speed_limit <= SPEED_HIGH_PENALTY:
            factor = 0.2
        else:
            factor = 0.0
        weighted_score += seg.distance_m * factor

    proportion = weighted_score / total_distance
    return round(proportion * MAX_SPEED_POINTS, 1)


def _score_surface(segments: list[RouteSegment]) -> float:
    """Score based on surface quality across the route."""
    total_distance = sum(s.distance_m for s in segments)
    if total_distance == 0:
        return 0

    weighted_score = 0
    for seg in segments:
        surface = seg.surface.lower()
        if surface in GOOD_SURFACES:
            factor = 1.0
        elif surface in FAIR_SURFACES:
            factor = 0.6
        elif surface == "unknown":
            factor = 0.5  # Assume average for unknown
        else:
            factor = 0.2  # Poor surfaces
        weighted_score += seg.distance_m * factor

    proportion = weighted_score / total_distance
    return round(proportion * MAX_SURFACE_POINTS, 1)


def _score_directness(
    cycling_distance_m: float,
    driving_distance_m: float | None,
) -> float:
    """Score based on route directness compared to driving distance."""
    if not driving_distance_m or driving_distance_m == 0:
        # No driving comparison available — give half points
        return MAX_DIRECTNESS_POINTS / 2

    ratio = cycling_distance_m / driving_distance_m
    if ratio <= 1.1:
        return MAX_DIRECTNESS_POINTS  # Very direct
    if ratio <= 1.3:
        return MAX_DIRECTNESS_POINTS * 0.7
    if ratio <= 1.5:
        return MAX_DIRECTNESS_POINTS * 0.4
    return MAX_DIRECTNESS_POINTS * 0.1  # Very indirect


def _score_junctions(segments: list[RouteSegment]) -> float:
    """
    Score based on hostile junction exposure.

    Counts segments on high-classification roads without provision as
    potential hostile junctions.
    """
    hostile_count = sum(
        1 for s in segments
        if s.provision == "none"
        and s.highway in ("primary", "secondary", "trunk", "tertiary")
        and s.speed_limit >= 30
    )

    if hostile_count == 0:
        return MAX_JUNCTION_POINTS
    if hostile_count <= 2:
        return MAX_JUNCTION_POINTS * 0.6
    if hostile_count <= 5:
        return MAX_JUNCTION_POINTS * 0.3
    return 0


def score_route(
    segments: list[RouteSegment],
    cycling_distance_m: float,
    driving_distance_m: float | None = None,
) -> dict[str, Any]:
    """
    Calculate a 0-100 LTN 1/20 cycling quality score for a route.

    Args:
        segments: Route segments from infrastructure analysis.
        cycling_distance_m: Total cycling route distance in metres.
        driving_distance_m: Optional driving distance for directness comparison.

    Returns:
        Dict with score, rating, breakdown, and optional notes.
    """
    segregation = _score_segregation(segments)
    speed = _score_speed(segments)
    surface = _score_surface(segments)
    directness = _score_directness(cycling_distance_m, driving_distance_m)
    junctions = _score_junctions(segments)

    total = round(segregation + speed + surface + directness + junctions)
    total = max(0, min(100, total))

    if total >= GREEN_THRESHOLD:
        rating = "green"
    elif total >= AMBER_THRESHOLD:
        rating = "amber"
    else:
        rating = "red"

    result: dict[str, Any] = {
        "score": total,
        "rating": rating,
        "breakdown": {
            "segregation": round(segregation, 1),
            "speed_safety": round(speed, 1),
            "surface_quality": round(surface, 1),
            "directness": round(directness, 1),
            "junction_safety": round(junctions, 1),
        },
        "max_points": {
            "segregation": MAX_SEGREGATION_POINTS,
            "speed_safety": MAX_SPEED_POINTS,
            "surface_quality": MAX_SURFACE_POINTS,
            "directness": MAX_DIRECTNESS_POINTS,
            "junction_safety": MAX_JUNCTION_POINTS,
        },
    }

    if cycling_distance_m < SHORT_ROUTE_THRESHOLD:
        result["short_route_note"] = (
            "Short distance; walking may be preferable"
        )

    return result
