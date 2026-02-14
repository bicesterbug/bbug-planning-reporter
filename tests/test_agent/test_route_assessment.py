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


def _make_route_result(destination="Bicester North"):
    """Create a mock route assessment result."""
    return {
        "status": "success",
        "destination": destination,
        "distance_m": 2500,
        "duration_minutes": 10.0,
        "provision_breakdown": {"segregated": 1500, "none": 1000},
        "score": {"score": 55, "rating": "amber", "breakdown": {}},
        "issues": [
            {
                "severity": "high",
                "problem": "No cycling provision on A-road",
                "suggested_improvement": "Segregated cycleway needed",
            }
        ],
        "s106_suggestions": [
            {"suggestion": "Contribute to cycleway along Buckingham Road"},
        ],
        "segments": [],
        "route_geometry": {"type": "LineString", "coordinates": []},
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

    def test_routes_included_in_evidence(self):
        """Route assessment data included in evidence context."""
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
        assert "S106" in route_text
