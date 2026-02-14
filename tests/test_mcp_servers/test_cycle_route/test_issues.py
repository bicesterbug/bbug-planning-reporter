"""
Tests for route issues identifier and S106 suggestion generator.

Verifies [cycle-route-assessment:FR-004] - Key issues with location, problem, improvement
Verifies [cycle-route-assessment:FR-009] - S106 funding suggestions from issues

Verifies test scenarios:
- [cycle-route-assessment:IssuesIdentifier/TS-01] High-speed no-provision issue
- [cycle-route-assessment:IssuesIdentifier/TS-02] Poor surface issue
- [cycle-route-assessment:IssuesIdentifier/TS-03] No issues on good route
- [cycle-route-assessment:IssuesIdentifier/TS-04] S106 suggestion generated
"""

from src.mcp_servers.cycle_route.infrastructure import RouteSegment
from src.mcp_servers.cycle_route.issues import (
    generate_s106_suggestions,
    identify_issues,
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


# =============================================================================
# identify_issues
# =============================================================================


class TestIdentifyIssues:
    """Verifies [cycle-route-assessment:IssuesIdentifier/TS-01] through TS-03."""

    def test_high_speed_no_provision(self):
        """[IssuesIdentifier/TS-01] 40mph with no provision → high severity issue."""
        segments = [
            _make_segment(provision="none", highway="primary",
                          speed_limit=40, distance_m=500, name="A41"),
        ]
        issues = identify_issues(segments)

        assert len(issues) >= 1
        high_issues = [i for i in issues if i["severity"] == "high"]
        assert len(high_issues) == 1
        assert "40mph" in high_issues[0]["problem"]
        assert "no cycle provision" in high_issues[0]["problem"]
        assert "LTN 1/20" in high_issues[0]["suggested_improvement"]
        assert "Table 4-1" in high_issues[0]["suggested_improvement"]
        assert "A41" in high_issues[0]["location"]

    def test_50mph_no_provision(self):
        """50mph roads also flagged as high severity."""
        segments = [
            _make_segment(provision="none", highway="trunk",
                          speed_limit=50, distance_m=800),
        ]
        issues = identify_issues(segments)
        high_issues = [i for i in issues if i["severity"] == "high"]
        assert len(high_issues) == 1

    def test_moderate_speed_classified_road(self):
        """30mph secondary road with no provision → medium severity."""
        segments = [
            _make_segment(provision="none", highway="secondary",
                          speed_limit=30, distance_m=500),
        ]
        issues = identify_issues(segments)
        medium_issues = [i for i in issues if i["severity"] == "medium"]
        assert len(medium_issues) == 1
        assert "30mph" in medium_issues[0]["problem"]

    def test_30mph_residential_no_issue(self):
        """30mph residential road is NOT classified as medium (not primary/secondary/etc)."""
        segments = [
            _make_segment(provision="none", highway="residential",
                          speed_limit=30, distance_m=500),
        ]
        issues = identify_issues(segments)
        speed_issues = [i for i in issues if "mph" in i.get("problem", "")]
        assert len(speed_issues) == 0

    def test_poor_surface(self):
        """[IssuesIdentifier/TS-02] Gravel surface → medium severity issue."""
        segments = [
            _make_segment(provision="shared_use", highway="path",
                          surface="gravel", distance_m=300, name="River Path"),
        ]
        issues = identify_issues(segments)

        surface_issues = [i for i in issues if "surface" in i["problem"].lower()]
        assert len(surface_issues) == 1
        assert surface_issues[0]["severity"] == "medium"
        assert "gravel" in surface_issues[0]["problem"].lower()
        assert "LTN 1/20" in surface_issues[0]["suggested_improvement"]

    def test_dirt_surface(self):
        """Dirt surface also flagged."""
        segments = [_make_segment(surface="dirt", distance_m=200)]
        issues = identify_issues(segments)
        assert any("dirt" in i["problem"].lower() for i in issues)

    def test_grass_surface(self):
        """Grass surface also flagged."""
        segments = [_make_segment(surface="grass", distance_m=200)]
        issues = identify_issues(segments)
        assert any("grass" in i["problem"].lower() for i in issues)

    def test_no_issues_good_route(self):
        """[IssuesIdentifier/TS-03] Fully segregated, good surface → no issues."""
        segments = [
            _make_segment(provision="segregated", highway="cycleway",
                          speed_limit=0, surface="asphalt", lit=True,
                          distance_m=2000, name="NCN 51"),
        ]
        issues = identify_issues(segments)
        assert issues == []

    def test_unlit_shared_use_path(self):
        """Unlit shared-use path → low severity issue."""
        segments = [
            _make_segment(provision="shared_use", highway="path",
                          lit=False, surface="asphalt", distance_m=500),
        ]
        issues = identify_issues(segments)
        low_issues = [i for i in issues if i["severity"] == "low"]
        assert len(low_issues) == 1
        assert "unlit" in low_issues[0]["problem"].lower()

    def test_unlit_segregated_path(self):
        """Unlit segregated path → low severity issue."""
        segments = [
            _make_segment(provision="segregated", highway="cycleway",
                          lit=False, surface="asphalt", distance_m=500),
        ]
        issues = identify_issues(segments)
        low_issues = [i for i in issues if i["severity"] == "low"]
        assert len(low_issues) == 1

    def test_lit_unknown_no_issue(self):
        """Lit=None (unknown) does not trigger unlit issue."""
        segments = [
            _make_segment(provision="shared_use", highway="path",
                          lit=None, surface="asphalt", distance_m=500),
        ]
        issues = identify_issues(segments)
        lit_issues = [i for i in issues if "unlit" in i.get("problem", "").lower()]
        assert len(lit_issues) == 0

    def test_multiple_issues_same_segment(self):
        """Segment can trigger multiple issues (speed + surface)."""
        segments = [
            _make_segment(provision="none", highway="primary",
                          speed_limit=40, surface="gravel", distance_m=500),
        ]
        issues = identify_issues(segments)
        assert len(issues) >= 2  # Speed issue + surface issue


# =============================================================================
# generate_s106_suggestions
# =============================================================================


class TestGenerateS106Suggestions:
    """Verifies [cycle-route-assessment:IssuesIdentifier/TS-04]."""

    def test_s106_from_high_severity(self):
        """[IssuesIdentifier/TS-04] High severity issue → S106 suggestion."""
        issues = [{
            "location": "A41 (500m section)",
            "problem": "40mph speed limit with no cycle provision",
            "severity": "high",
            "suggested_improvement": "Segregated cycleway required",
        }]

        suggestions = generate_s106_suggestions(issues)

        assert len(suggestions) == 1
        assert suggestions[0]["issue_location"] == "A41 (500m section)"
        assert "S106" in suggestions[0]["justification"]
        assert "Cherwell Local Plan" in suggestions[0]["justification"]
        assert "NPPF" in suggestions[0]["justification"]
        assert suggestions[0]["severity"] == "high"

    def test_s106_from_medium_severity(self):
        """Medium severity issues also get S106 suggestions."""
        issues = [{
            "location": "River Path (300m section)",
            "problem": "Poor surface (gravel)",
            "severity": "medium",
            "suggested_improvement": "Resurface to sealed tarmac",
        }]

        suggestions = generate_s106_suggestions(issues)
        assert len(suggestions) == 1

    def test_no_s106_from_low_severity(self):
        """Low severity issues do NOT get S106 suggestions."""
        issues = [{
            "location": "Towpath (500m section)",
            "problem": "Unlit shared use path",
            "severity": "low",
            "suggested_improvement": "Install lighting",
        }]

        suggestions = generate_s106_suggestions(issues)
        assert len(suggestions) == 0

    def test_empty_issues(self):
        """No issues → no suggestions."""
        assert generate_s106_suggestions([]) == []

    def test_mixed_severities(self):
        """Only high and medium issues generate suggestions."""
        issues = [
            {"location": "A", "problem": "P1", "severity": "high",
             "suggested_improvement": "I1"},
            {"location": "B", "problem": "P2", "severity": "low",
             "suggested_improvement": "I2"},
            {"location": "C", "problem": "P3", "severity": "medium",
             "suggested_improvement": "I3"},
        ]

        suggestions = generate_s106_suggestions(issues)
        assert len(suggestions) == 2
        assert suggestions[0]["issue_location"] == "A"
        assert suggestions[1]["issue_location"] == "C"
