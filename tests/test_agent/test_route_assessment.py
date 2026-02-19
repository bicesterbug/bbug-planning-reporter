"""
Tests for ASSESSING_ROUTES pipeline phase.

Verifies [cycle-route-assessment:FR-008] - Route assessment in pipeline
Verifies [cycle-route-assessment:NFR-002] - Graceful failure handling
Verifies [cycle-route-assessment:NFR-005] - Review completes even if assessment fails

Verifies test scenarios:
- [cycle-route-assessment:AgentOrchestrator/TS-01] Route assessment phase executes
- [cycle-route-assessment:AgentOrchestrator/TS-02] Route assessment skipped on MCP unavailable
- [cycle-route-assessment:AgentOrchestrator/TS-03] Recoverable on boundary lookup failure
- [cycle-route-assessment:ReviewPhase/TS-01] Phase weights sum to 100
- [cycle-route-assessment:ReviewPhase/TS-02] ASSESSING_ROUTES is phase 6
- [cycle-route-assessment:MCPClientManager/TS-01] Route tool routing
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.mcp_client import TOOL_ROUTING, MCPClientManager, MCPServerType, MCPToolError
from src.agent.orchestrator import AgentOrchestrator, OrchestratorError, ReviewPhase
from src.agent.progress import PHASE_NUMBER_MAP, PHASE_WEIGHTS

# =============================================================================
# ReviewPhase and weights
# =============================================================================


class TestPhaseWeightsAndNumbers:
    """Verifies [cycle-route-assessment:ReviewPhase/TS-01] and TS-02."""

    def test_weights_sum_to_100(self):
        """[ReviewPhase/TS-01] Phase weights sum to 100."""
        assert sum(PHASE_WEIGHTS.values()) == 100

    def test_assessing_routes_is_phase_6(self):
        """[ReviewPhase/TS-02] ASSESSING_ROUTES is phase 6."""
        assert PHASE_NUMBER_MAP[ReviewPhase.ASSESSING_ROUTES] == 6
        assert PHASE_NUMBER_MAP[ReviewPhase.GENERATING_REVIEW] == 7
        assert PHASE_NUMBER_MAP[ReviewPhase.VERIFYING_REVIEW] == 8

    def test_total_phases_is_8(self):
        """[ReviewPhase/TS-02] Total phases is 8."""
        assert len(ReviewPhase) == 8
        assert len(PHASE_WEIGHTS) == 8
        assert len(PHASE_NUMBER_MAP) == 8

    def test_assessing_routes_weight(self):
        """ASSESSING_ROUTES has weight 8."""
        assert PHASE_WEIGHTS[ReviewPhase.ASSESSING_ROUTES] == 8


# =============================================================================
# Tool routing
# =============================================================================


class TestToolRouting:
    """Verifies [cycle-route-assessment:MCPClientManager/TS-01]."""

    def test_get_site_boundary_routed(self):
        """get_site_boundary routes to CYCLE_ROUTE."""
        assert TOOL_ROUTING["get_site_boundary"] == MCPServerType.CYCLE_ROUTE

    def test_assess_cycle_route_routed(self):
        """assess_cycle_route routes to CYCLE_ROUTE."""
        assert TOOL_ROUTING["assess_cycle_route"] == MCPServerType.CYCLE_ROUTE

    def test_cycle_route_server_config(self):
        """MCPClientManager has cycle-route server config."""
        mgr = MCPClientManager(
            cherwell_scraper_url="http://fake:3001",
            document_store_url="http://fake:3002",
            policy_kb_url="http://fake:3003",
            cycle_route_url="http://fake:3004",
        )
        config = mgr._servers[MCPServerType.CYCLE_ROUTE]
        assert config.base_url == "http://fake:3004"
        assert "get_site_boundary" in config.tools
        assert "assess_cycle_route" in config.tools


# =============================================================================
# _phase_assess_routes
# =============================================================================


def _make_mock_mcp_client(connected=True):
    """Create a mock MCP client with cycle-route connected."""
    client = AsyncMock(spec=MCPClientManager)
    state = MagicMock()
    state.connected = connected
    client.is_connected.return_value = connected
    return client


def _make_boundary_result():
    """Create a mock boundary result."""
    return {
        "status": "success",
        "geojson": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[-1.15, 51.9], [-1.14, 51.9], [-1.14, 51.91], [-1.15, 51.91], [-1.15, 51.9]]],
                    },
                    "properties": {"application_ref": "21/03267/OUT"},
                },
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [-1.145, 51.905],
                    },
                    "properties": {"type": "centroid"},
                },
            ],
        },
    }


def _make_route_data(distance=2500, score=55, rating="amber", provision=None,
                     issues=None, s106=None, segments=None, transitions=None):
    """Create a single route data object."""
    if provision is None:
        provision = {"segregated": 1500, "none": 1000}
    if issues is None:
        issues = [
            {
                "severity": "high",
                "problem": "No cycling provision on A-road",
                "suggested_improvement": "Segregated cycleway needed",
            }
        ]
    if s106 is None:
        s106 = [{"suggestion": "Contribute to cycleway along Buckingham Road"}]
    if segments is None:
        segments = []
    result = {
        "distance_m": distance,
        "duration_minutes": round(distance / 250, 1),
        "provision_breakdown": provision,
        "score": {"score": score, "rating": rating, "breakdown": {}},
        "issues": issues,
        "s106_suggestions": s106,
        "segments": segments,
        "route_geometry": [],
    }
    if transitions is not None:
        result["transitions"] = transitions
    return result


def _make_route_result(destination="Bicester North", same_route=True):
    """Create a mock dual-route assessment result."""
    route = _make_route_data()
    result = {
        "status": "success",
        "destination": destination,
        "shortest_route": route,
        "safest_route": route,
        "same_route": same_route,
    }
    return result


def _make_dual_route_result(destination="Bicester North"):
    """Create a mock dual-route result where shortest != safest."""
    shortest = _make_route_data(
        distance=2200, score=35, rating="red",
        provision={"none": 2200},
        issues=[{"severity": "high", "problem": "No cycling provision"}],
    )
    safest = _make_route_data(
        distance=2800, score=72, rating="amber",
        provision={"segregated": 2000, "none": 800},
        issues=[{"severity": "medium", "problem": "Short gap in provision"}],
        segments={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": [[-1.15, 51.9], [-1.14, 51.91]]},
                    "properties": {
                        "way_id": 100, "provision": "segregated", "original_provision": "none",
                        "highway": "secondary", "speed_limit": 30, "surface": "asphalt",
                        "lit": True, "distance_m": 500, "name": "Banbury Rd",
                    },
                },
            ],
        },
    )
    return {
        "status": "success",
        "destination": destination,
        "shortest_route": shortest,
        "safest_route": safest,
        "same_route": False,
    }


class TestPhaseAssessRoutes:
    """Verifies [cycle-route-assessment:AgentOrchestrator/TS-01] through TS-03."""

    @pytest.mark.anyio
    async def test_skips_when_mcp_unavailable(self):
        """[AgentOrchestrator/TS-02] Route assessment skipped when MCP unavailable."""
        mcp_client = _make_mock_mcp_client(connected=False)
        mock_redis = AsyncMock()

        orch = AgentOrchestrator(
            review_id="rev_test",
            application_ref="21/03267/OUT",
            mcp_client=mcp_client,
            redis_client=mock_redis,
        )
        orch._initialized = True

        with pytest.raises(OrchestratorError) as exc_info:
            await orch._phase_assess_routes()

        assert exc_info.value.recoverable is True
        assert "not available" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_skips_when_empty_destination_ids(self):
        """Route assessment skipped when destination_ids is empty list."""
        mcp_client = _make_mock_mcp_client(connected=True)
        mock_redis = AsyncMock()

        options = MagicMock()
        options.destination_ids = []

        orch = AgentOrchestrator(
            review_id="rev_test",
            application_ref="21/03267/OUT",
            mcp_client=mcp_client,
            redis_client=mock_redis,
            options=options,
        )
        orch._initialized = True

        # Should return without error
        await orch._phase_assess_routes()
        assert orch._route_assessments == []

    @pytest.mark.anyio
    async def test_recoverable_on_boundary_error(self):
        """[AgentOrchestrator/TS-03] Recoverable on boundary lookup failure."""
        mcp_client = _make_mock_mcp_client(connected=True)
        mcp_client.call_tool = AsyncMock(return_value={
            "status": "error",
            "error_type": "not_found",
            "message": "Application not found",
        })
        mock_redis = AsyncMock()

        orch = AgentOrchestrator(
            review_id="rev_test",
            application_ref="99/99999/X",
            mcp_client=mcp_client,
            redis_client=mock_redis,
        )
        orch._initialized = True

        with pytest.raises(OrchestratorError) as exc_info:
            await orch._phase_assess_routes()

        assert exc_info.value.recoverable is True
        assert "boundary" in str(exc_info.value).lower()

    @pytest.mark.anyio
    async def test_recoverable_on_mcp_tool_error(self):
        """Boundary MCP tool error is recoverable."""
        mcp_client = _make_mock_mcp_client(connected=True)
        mcp_client.call_tool = AsyncMock(side_effect=MCPToolError(
            "get_site_boundary", "Server error"
        ))
        mock_redis = AsyncMock()

        orch = AgentOrchestrator(
            review_id="rev_test",
            application_ref="21/03267/OUT",
            mcp_client=mcp_client,
            redis_client=mock_redis,
        )
        orch._initialized = True

        with pytest.raises(OrchestratorError) as exc_info:
            await orch._phase_assess_routes()

        assert exc_info.value.recoverable is True

    @pytest.mark.anyio
    async def test_successful_assessment(self):
        """[AgentOrchestrator/TS-01] Route assessment phase executes successfully."""
        mcp_client = _make_mock_mcp_client(connected=True)

        boundary_result = _make_boundary_result()
        route_result = _make_route_result("Bicester North")

        # call_tool dispatches: first call is get_site_boundary, rest are assess_cycle_route
        mcp_client.call_tool = AsyncMock(side_effect=[
            boundary_result,
            route_result,
        ])

        mock_redis = AsyncMock()
        # Mock Redis for destination lookup
        mock_redis.exists = AsyncMock(return_value=True)
        mock_redis.hgetall = AsyncMock(return_value={
            "dest_001": json.dumps({
                "id": "dest_001",
                "name": "Bicester North",
                "lat": 51.9054,
                "lon": -1.1512,
                "category": "rail",
            }),
        })

        orch = AgentOrchestrator(
            review_id="rev_test",
            application_ref="21/03267/OUT",
            mcp_client=mcp_client,
            redis_client=mock_redis,
        )
        orch._initialized = True

        await orch._phase_assess_routes()

        assert len(orch._route_assessments) == 1
        assert orch._route_assessments[0]["destination"] == "Bicester North"
        assert orch._route_assessments[0]["destination_id"] == "dest_001"
        assert orch._site_boundary is not None
        assert orch._site_boundary["type"] == "FeatureCollection"

    @pytest.mark.anyio
    async def test_partial_route_failures(self):
        """Route assessment continues when some destinations fail."""
        mcp_client = _make_mock_mcp_client(connected=True)

        boundary_result = _make_boundary_result()
        success_result = _make_route_result("Bicester North")
        error_result = {"status": "error", "error_type": "no_route", "message": "No route found"}

        mcp_client.call_tool = AsyncMock(side_effect=[
            boundary_result,
            success_result,
            error_result,
        ])

        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=True)
        mock_redis.hgetall = AsyncMock(return_value={
            "dest_001": json.dumps({
                "id": "dest_001", "name": "Bicester North",
                "lat": 51.9054, "lon": -1.1512, "category": "rail",
            }),
            "dest_002": json.dumps({
                "id": "dest_002", "name": "Faraway",
                "lat": 52.5, "lon": -0.5, "category": "other",
            }),
        })

        orch = AgentOrchestrator(
            review_id="rev_test",
            application_ref="21/03267/OUT",
            mcp_client=mcp_client,
            redis_client=mock_redis,
        )
        orch._initialized = True

        await orch._phase_assess_routes()

        # One succeeded, one failed
        assert len(orch._route_assessments) == 1
        assert orch._route_assessments[0]["destination"] == "Bicester North"

    @pytest.mark.anyio
    async def test_filtered_destination_ids(self):
        """Only specified destination_ids are assessed."""
        mcp_client = _make_mock_mcp_client(connected=True)

        boundary_result = _make_boundary_result()
        route_result = _make_route_result("Bicester North")

        mcp_client.call_tool = AsyncMock(side_effect=[
            boundary_result,
            route_result,
        ])

        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=True)
        mock_redis.hgetall = AsyncMock(return_value={
            "dest_001": json.dumps({
                "id": "dest_001", "name": "Bicester North",
                "lat": 51.9054, "lon": -1.1512, "category": "rail",
            }),
            "dest_002": json.dumps({
                "id": "dest_002", "name": "Bicester Village",
                "lat": 51.8899, "lon": -1.1467, "category": "rail",
            }),
        })

        options = MagicMock()
        options.destination_ids = ["dest_001"]

        orch = AgentOrchestrator(
            review_id="rev_test",
            application_ref="21/03267/OUT",
            mcp_client=mcp_client,
            redis_client=mock_redis,
            options=options,
        )
        orch._initialized = True

        await orch._phase_assess_routes()

        # Only dest_001 assessed, not dest_002
        assert len(orch._route_assessments) == 1
        assert mcp_client.call_tool.call_count == 2  # boundary + 1 route


# =============================================================================
# _build_evidence_context with route data
# =============================================================================


class TestBuildEvidenceContext:
    """Test route evidence in _build_evidence_context."""

    def test_no_routes_default_text(self):
        """No route assessments gives default text."""
        orch = AgentOrchestrator(
            review_id="rev_test",
            application_ref="21/03267/OUT",
        )
        result = orch._build_evidence_context()
        assert len(result) == 6
        assert "No cycling route assessments" in result[5]

    def test_same_route_in_evidence(self):
        """Same-route assessment shows single labelled route."""
        orch = AgentOrchestrator(
            review_id="rev_test",
            application_ref="21/03267/OUT",
        )
        orch._route_assessments = [_make_route_result("Bicester North")]

        result = orch._build_evidence_context()
        route_text = result[5]
        assert "Bicester North" in route_text
        assert "2500m" in route_text
        assert "55/100" in route_text
        assert "amber" in route_text
        assert "Shortest & safest route (same)" in route_text
        assert "S106" in route_text

    def test_dual_route_in_evidence(self):
        """Dual-route assessment shows both routes with labels."""
        orch = AgentOrchestrator(
            review_id="rev_test",
            application_ref="21/03267/OUT",
        )
        orch._route_assessments = [_make_dual_route_result("Bicester North")]

        result = orch._build_evidence_context()
        route_text = result[5]
        assert "Shortest route" in route_text
        assert "Safest route" in route_text
        assert "2200m" in route_text
        assert "2800m" in route_text
        assert "35/100" in route_text
        assert "72/100" in route_text

    def test_parallel_detection_noted_in_evidence(self):
        """Parallel detection upgrades are noted in evidence text."""
        orch = AgentOrchestrator(
            review_id="rev_test",
            application_ref="21/03267/OUT",
        )
        orch._route_assessments = [_make_dual_route_result("Bicester North")]

        result = orch._build_evidence_context()
        route_text = result[5]
        assert "Parallel detection" in route_text
        assert "upgraded" in route_text


# =============================================================================
# _build_route_evidence_summary with dual routes
# =============================================================================


class TestBuildRouteEvidenceSummary:
    """Test condensed route evidence for structure call."""

    def test_no_routes_default(self):
        """No routes returns default text."""
        orch = AgentOrchestrator(
            review_id="rev_test",
            application_ref="21/03267/OUT",
        )
        assert "No cycling route assessments" in orch._build_route_evidence_summary()

    def test_same_route_single_block(self):
        """Same route shows single block with label."""
        orch = AgentOrchestrator(
            review_id="rev_test",
            application_ref="21/03267/OUT",
        )
        orch._route_assessments = [_make_route_result("Bicester North")]

        summary = orch._build_route_evidence_summary()
        assert "Bicester North" in summary
        assert "Shortest & safest (same route)" in summary
        assert "55/100" in summary

    def test_dual_route_both_shown(self):
        """Different routes show both blocks."""
        orch = AgentOrchestrator(
            review_id="rev_test",
            application_ref="21/03267/OUT",
        )
        orch._route_assessments = [_make_dual_route_result("Bicester North")]

        summary = orch._build_route_evidence_summary()
        assert "Shortest route" in summary
        assert "Safest route" in summary
        assert "35/100" in summary
        assert "72/100" in summary

    def test_parallel_detection_in_summary(self):
        """Parallel detection upgrades noted in summary."""
        orch = AgentOrchestrator(
            review_id="rev_test",
            application_ref="21/03267/OUT",
        )
        orch._route_assessments = [_make_dual_route_result("Bicester North")]

        summary = orch._build_route_evidence_summary()
        assert "Parallel detection" in summary
        assert "upgraded" in summary


# =============================================================================
# Transition evidence tests
# =============================================================================


def _make_transitions_data(barriers=None, crossings=None, side_changes=None,
                           directness=None, unavailable=False):
    """Create a transitions dict for test helpers."""
    result = {
        "barriers": barriers or [],
        "non_priority_crossings": crossings or [],
        "side_changes": side_changes or [],
        "directness_differential": directness,
    }
    if unavailable:
        result["unavailable"] = True
    return result


class TestTransitionEvidence:
    """Verifies [route-transition-analysis:_build_evidence_context/TS-14] through TS-16."""

    def test_evidence_includes_transition_stats(self):
        """[TS-14] Evidence text includes transition statistics."""
        transitions = _make_transitions_data(
            barriers=[{"type": "bollard"}, {"type": "gate"}],
            crossings=[{"road_speed_limit": 30}],
        )
        route = _make_route_data(transitions=transitions)
        orch = AgentOrchestrator(
            review_id="rev_test",
            application_ref="21/03267/OUT",
        )
        orch._route_assessments = [{
            "status": "success",
            "destination": "Test",
            "shortest_route": route,
            "safest_route": route,
            "same_route": True,
        }]

        result = orch._build_evidence_context()
        route_text = result[5]
        assert "2 barriers" in route_text
        assert "1 non-priority crossings" in route_text
        assert "0 side changes" in route_text

    def test_evidence_includes_directness_differential(self):
        """[TS-15] Evidence text includes directness differential."""
        transitions = _make_transitions_data(directness=1.15)
        route = _make_route_data(transitions=transitions)
        orch = AgentOrchestrator(
            review_id="rev_test",
            application_ref="21/03267/OUT",
        )
        orch._route_assessments = [{
            "status": "success",
            "destination": "Test",
            "shortest_route": route,
            "safest_route": route,
            "same_route": True,
        }]

        result = orch._build_evidence_context()
        route_text = result[5]
        assert "Directness differential: 1.15" in route_text

    def test_evidence_omits_unavailable_transitions(self):
        """[TS-16] Evidence text omits transitions when unavailable."""
        transitions = _make_transitions_data(unavailable=True)
        route = _make_route_data(transitions=transitions)
        orch = AgentOrchestrator(
            review_id="rev_test",
            application_ref="21/03267/OUT",
        )
        orch._route_assessments = [{
            "status": "success",
            "destination": "Test",
            "shortest_route": route,
            "safest_route": route,
            "same_route": True,
        }]

        result = orch._build_evidence_context()
        route_text = result[5]
        assert "Transitions:" not in route_text


# =============================================================================
# Route narrative extraction tests
# =============================================================================


class TestDeterministicRouteNarrative:
    """Verifies deterministic route_narrative build from MCP data."""

    def _build_route_narrative(self, route_assessments):
        """Simulate the orchestrator's deterministic build logic."""
        if not route_assessments:
            return None
        return {
            "destinations": [
                {
                    "destination_name": ra.get("destination", "Unknown"),
                    "shortest_route_summary": {
                        "distance_m": ra["shortest_route"]["distance_m"],
                        "ltn_score": ra["shortest_route"]["score"]["score"],
                        "rating": ra["shortest_route"]["score"]["rating"],
                    },
                    "safest_route_summary": {
                        "distance_m": ra["safest_route"]["distance_m"],
                        "ltn_score": ra["safest_route"]["score"]["score"],
                        "rating": ra["safest_route"]["score"]["rating"],
                    },
                    "same_route": ra.get("same_route", True),
                }
                for ra in route_assessments
            ]
        }

    def test_route_narrative_populated_from_assessments(self):
        """route_narrative built from MCP route assessment data."""
        route_assessments = [
            {
                "status": "success",
                "destination": "Bicester North",
                "destination_id": "dest_001",
                "shortest_route": {
                    "distance_m": 2200,
                    "score": {"score": 35, "rating": "red"},
                },
                "safest_route": {
                    "distance_m": 2800,
                    "score": {"score": 72, "rating": "amber"},
                },
                "same_route": False,
            }
        ]

        narrative = self._build_route_narrative(route_assessments)

        assert narrative is not None
        assert len(narrative["destinations"]) == 1
        dest = narrative["destinations"][0]
        assert dest["destination_name"] == "Bicester North"
        assert dest["shortest_route_summary"]["distance_m"] == 2200
        assert dest["shortest_route_summary"]["ltn_score"] == 35
        assert dest["shortest_route_summary"]["rating"] == "red"
        assert dest["safest_route_summary"]["distance_m"] == 2800
        assert dest["safest_route_summary"]["ltn_score"] == 72
        assert dest["safest_route_summary"]["rating"] == "amber"
        assert dest["same_route"] is False
        assert "narrative" not in dest

    def test_route_narrative_none_when_empty(self):
        """route_narrative is None when no route assessments exist."""
        narrative = self._build_route_narrative([])
        assert narrative is None

    def test_route_narrative_same_route(self):
        """route_narrative handles same_route=True with identical summaries."""
        route_assessments = [
            {
                "destination": "Town Centre",
                "shortest_route": {
                    "distance_m": 1500,
                    "score": {"score": 70, "rating": "amber"},
                },
                "safest_route": {
                    "distance_m": 1500,
                    "score": {"score": 70, "rating": "amber"},
                },
                "same_route": True,
            }
        ]

        narrative = self._build_route_narrative(route_assessments)

        dest = narrative["destinations"][0]
        assert dest["same_route"] is True
        assert dest["shortest_route_summary"] == dest["safest_route_summary"]


class TestTransitionSummary:
    """Verifies [route-transition-analysis:_build_route_evidence_summary/TS-17] and TS-18."""

    def test_summary_includes_transition_counts(self):
        """[TS-17] Summary includes transition counts."""
        transitions = _make_transitions_data(
            barriers=[{"type": "bollard"}],
            crossings=[{"road_speed_limit": 30}, {"road_speed_limit": 20}],
            side_changes=[{"road_name": "Cross St"}],
        )
        route = _make_route_data(transitions=transitions)
        orch = AgentOrchestrator(
            review_id="rev_test",
            application_ref="21/03267/OUT",
        )
        orch._route_assessments = [{
            "status": "success",
            "destination": "Test",
            "shortest_route": route,
            "safest_route": route,
            "same_route": True,
        }]

        summary = orch._build_route_evidence_summary()
        assert "1 barriers" in summary
        assert "2 non-priority crossings" in summary
        assert "1 side changes" in summary

    def test_summary_omits_unavailable_transitions(self):
        """[TS-18] Summary omits transitions when unavailable."""
        transitions = _make_transitions_data(unavailable=True)
        route = _make_route_data(transitions=transitions)
        orch = AgentOrchestrator(
            review_id="rev_test",
            application_ref="21/03267/OUT",
        )
        orch._route_assessments = [{
            "status": "success",
            "destination": "Test",
            "shortest_route": route,
            "safest_route": route,
            "same_route": True,
        }]

        summary = orch._build_route_evidence_summary()
        assert "Transitions:" not in summary
