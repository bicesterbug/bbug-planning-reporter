"""
Route issues identifier and S106 suggestion generator.

Implements [cycle-route-assessment:FR-004] - Key issues with location, problem, improvement
Implements [cycle-route-assessment:FR-009] - S106 funding suggestions from issues

Implements test scenarios:
- [cycle-route-assessment:IssuesIdentifier/TS-01] High-speed no-provision issue
- [cycle-route-assessment:IssuesIdentifier/TS-02] Poor surface issue
- [cycle-route-assessment:IssuesIdentifier/TS-03] No issues on good route
- [cycle-route-assessment:IssuesIdentifier/TS-04] S106 suggestion generated
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.mcp_servers.cycle_route.infrastructure import RouteSegment


def identify_issues(segments: list[RouteSegment]) -> list[dict[str, Any]]:
    """
    Identify cycling infrastructure issues from route segments.

    Issues are aggregated by road name and issue type, producing one issue
    per road per issue type with total affected distance.

    Args:
        segments: Route segments from infrastructure analysis.

    Returns:
        List of issue dicts.
    """
    # Group segments by road name
    roads: dict[str, list[RouteSegment]] = {}
    for seg in segments:
        roads.setdefault(seg.name, []).append(seg)

    issues = []

    for road_name, road_segments in roads.items():
        total_distance = sum(s.distance_m for s in road_segments)

        # High-speed road with no cycle provision
        high_speed_segs = [
            s for s in road_segments
            if s.provision == "none" and s.speed_limit >= 40
        ]
        if high_speed_segs:
            affected_distance = sum(s.distance_m for s in high_speed_segs)
            max_speed = max(s.speed_limit for s in high_speed_segs)
            representative = high_speed_segs[0]
            issues.append({
                "location": f"{road_name} ({round(affected_distance)}m)",
                "problem": (
                    f"{max_speed}mph speed limit with no cycle provision "
                    f"on {representative.highway} road"
                ),
                "severity": "high",
                "suggested_improvement": (
                    "Segregated cycleway required — LTN 1/20 Table 4-1 states "
                    "that speeds above 30mph require physical separation from "
                    "motor traffic"
                ),
            })

        # Moderate-speed road with no provision (only if not already caught by high-speed)
        moderate_segs = [
            s for s in road_segments
            if s.provision == "none"
            and s.speed_limit >= 30
            and s.speed_limit < 40
            and s.highway in ("primary", "secondary", "tertiary", "trunk")
        ]
        if moderate_segs:
            affected_distance = sum(s.distance_m for s in moderate_segs)
            max_speed = max(s.speed_limit for s in moderate_segs)
            representative = moderate_segs[0]
            issues.append({
                "location": f"{road_name} ({round(affected_distance)}m)",
                "problem": (
                    f"{max_speed}mph {representative.highway} road with no cycle provision"
                ),
                "severity": "medium",
                "suggested_improvement": (
                    f"On-road cycle lane or segregated cycleway — "
                    f"LTN 1/20 recommends protection on roads classified "
                    f"{representative.highway} with speeds at 30mph"
                ),
            })

        # Poor surface quality
        poor_surface_segs = [
            s for s in road_segments
            if s.surface.lower() in ("gravel", "dirt", "grass", "mud", "sand", "ground")
        ]
        if poor_surface_segs:
            affected_distance = sum(s.distance_m for s in poor_surface_segs)
            representative = poor_surface_segs[0]
            issues.append({
                "location": f"{road_name} ({round(affected_distance)}m)",
                "problem": (
                    f"Poor surface ({representative.surface}) on "
                    f"{'shared-use path' if representative.provision == 'shared_use' else representative.highway}"
                ),
                "severity": "medium",
                "suggested_improvement": (
                    "Resurface to sealed tarmac/asphalt — LTN 1/20 para 5.5 "
                    "requires smooth, sealed surfaces for cycling infrastructure"
                ),
            })

        # Unlit shared-use or segregated path
        unlit_segs = [
            s for s in road_segments
            if s.lit is False and s.provision in ("segregated", "shared_use")
        ]
        if unlit_segs:
            affected_distance = sum(s.distance_m for s in unlit_segs)
            representative = unlit_segs[0]
            issues.append({
                "location": f"{road_name} ({round(affected_distance)}m)",
                "problem": f"Unlit {representative.provision.replace('_', ' ')} path",
                "severity": "low",
                "suggested_improvement": (
                    "Install lighting — LTN 1/20 para 10.5 states cycle "
                    "routes should be lit where they form part of the highway "
                    "or run through areas of public open space"
                ),
            })

    return issues


def generate_s106_suggestions(
    issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Generate S106 funding suggestions from identified route issues.

    Each suggestion references a specific issue and provides a brief
    justification citing LTN 1/20 or local policy.

    Args:
        issues: List of issue dicts from identify_issues().

    Returns:
        List of S106 suggestion dicts.
    """
    suggestions = []

    for issue in issues:
        if issue["severity"] in ("high", "medium"):
            suggestions.append({
                "issue_location": issue["location"],
                "improvement": issue["suggested_improvement"],
                "justification": (
                    f"Addresses identified deficiency: {issue['problem']}. "
                    f"S106 contribution towards off-site cycling infrastructure "
                    f"improvements is justified under Cherwell Local Plan "
                    f"Policy INF1 and NPPF paragraph 116."
                ),
                "severity": issue["severity"],
            })

    return suggestions
