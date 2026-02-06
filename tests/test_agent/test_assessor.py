"""
Tests for review assessor.

Implements [agent-integration:ReviewAssessor/TS-01] through [TS-10]
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.assessor import (
    AspectAssessment,
    AspectName,
    AspectRating,
    AssessmentResult,
    ReviewAssessor,
    SearchResult,
)


@pytest.fixture
def mock_mcp_client():
    """Create mock MCPClientManager."""
    client = AsyncMock()
    return client


@pytest.fixture
def mock_claude_client():
    """Create mock ClaudeClient."""
    client = AsyncMock()
    return client


@pytest.fixture
def assessor(mock_mcp_client, mock_claude_client):
    """Create ReviewAssessor with mocked clients."""
    return ReviewAssessor(
        mcp_client=mock_mcp_client,
        claude_client=mock_claude_client,
        application_ref="25/01178/REM",
        application_type="major",
    )


def make_search_response(results: list[dict]) -> dict:
    """Helper to create a search response."""
    return {"results": results}


def make_claude_response(content: str):
    """Helper to create a Claude response."""
    response = MagicMock()
    response.content = content
    return response


class TestCycleParkingAssessment:
    """
    Tests for cycle parking assessment.

    Implements [agent-integration:ReviewAssessor/TS-01], [TS-02]
    """

    @pytest.mark.asyncio
    async def test_adequate_cycle_parking(self, assessor, mock_mcp_client, mock_claude_client):
        """
        Verifies [agent-integration:ReviewAssessor/TS-01] - Assess adequate cycle parking

        Given: Documents mention "48 Sheffield stands"
        When: Assess cycle parking
        Then: Returns amber rating with note about cargo bike spaces
        """
        # Setup search results with cycle parking content
        mock_mcp_client.call_tool.return_value = make_search_response([
            {
                "chunk_id": "chunk1",
                "text": "The development proposes 48 Sheffield stands for cycle parking, located in a covered and secure area near the main entrance.",
                "relevance_score": 0.9,
                "metadata": {"document_name": "Transport Assessment.pdf"},
            }
        ])

        # Setup Claude response with amber rating (missing cargo spaces)
        mock_claude_client.send_message.return_value = make_claude_response('''
{
    "rating": "amber",
    "key_issue": "Adequate quantity but missing cargo bike provision",
    "detail": "The proposed 48 Sheffield stands exceed the minimum requirement. However, no provision for cargo or adapted bikes is included as required by LTN 1/20 Chapter 11.",
    "policy_refs": ["LTN 1/20 Chapter 11", "Local Plan Policy ESD1"]
}
''')

        assessment = await assessor._assess_cycle_parking()

        assert assessment.rating == AspectRating.AMBER
        assert "cargo" in assessment.key_issue.lower() or "cargo" in assessment.detail.lower()
        assert len(assessment.policy_refs) > 0

    @pytest.mark.asyncio
    async def test_no_cycle_parking_mentioned(self, assessor, mock_mcp_client, mock_claude_client):
        """
        Verifies [agent-integration:ReviewAssessor/TS-02] - Assess no cycle parking mentioned

        Given: No cycling content in documents
        When: Assess cycle parking
        Then: Returns red rating; flags as critical issue
        """
        # Setup empty search results
        mock_mcp_client.call_tool.return_value = make_search_response([])

        assessment = await assessor._assess_cycle_parking()

        assert assessment.rating == AspectRating.RED
        assert "no" in assessment.key_issue.lower() or "not found" in assessment.key_issue.lower()


class TestCycleRoutesAssessment:
    """
    Tests for cycle routes assessment.

    Implements [agent-integration:ReviewAssessor/TS-03], [TS-04]
    """

    @pytest.mark.asyncio
    async def test_compliant_cycle_routes(self, assessor, mock_mcp_client, mock_claude_client):
        """
        Verifies [agent-integration:ReviewAssessor/TS-03] - Assess compliant cycle routes

        Given: Documents show 3m segregated track
        When: Assess cycle routes
        Then: Returns green rating with LTN 1/20 compliance noted
        """
        mock_mcp_client.call_tool.return_value = make_search_response([
            {
                "chunk_id": "chunk1",
                "text": "A 3.0m wide segregated two-way cycle track will be provided along the eastern boundary, separated from the carriageway by a 0.5m buffer strip.",
                "relevance_score": 0.95,
                "metadata": {"document_name": "Transport Assessment.pdf"},
            }
        ])

        mock_claude_client.send_message.return_value = make_claude_response('''
{
    "rating": "green",
    "key_issue": "Compliant segregated cycle track provision",
    "detail": "The proposed 3.0m segregated cycle track meets LTN 1/20 requirements for a two-way facility. The 0.5m buffer provides adequate separation.",
    "policy_refs": ["LTN 1/20 Table 5-2", "LTN 1/20 Chapter 5"]
}
''')

        assessment = await assessor._assess_cycle_routes()

        assert assessment.rating == AspectRating.GREEN
        assert "LTN 1/20" in str(assessment.policy_refs) or "LTN" in assessment.detail

    @pytest.mark.asyncio
    async def test_non_compliant_shared_use(self, assessor, mock_mcp_client, mock_claude_client):
        """
        Verifies [agent-integration:ReviewAssessor/TS-04] - Assess non-compliant shared use

        Given: Documents show shared-use path with high traffic
        When: Assess cycle routes
        Then: Returns red rating; cites LTN 1/20 Table 5-2
        """
        mock_mcp_client.call_tool.return_value = make_search_response([
            {
                "chunk_id": "chunk1",
                "text": "A 3.0m shared-use footway/cycleway will be provided alongside the main access road. Traffic flows are expected to be 4,200 PCU/day.",
                "relevance_score": 0.92,
                "metadata": {"document_name": "Transport Assessment.pdf"},
            }
        ])

        mock_claude_client.send_message.return_value = make_claude_response('''
{
    "rating": "red",
    "key_issue": "Non-compliant shared-use path on high-traffic road",
    "detail": "The proposed shared-use path is not acceptable where traffic flows exceed 2,500 PCU/day. At 4,200 PCU/day, LTN 1/20 Table 5-2 requires a segregated cycle track.",
    "policy_refs": ["LTN 1/20 Table 5-2"]
}
''')

        assessment = await assessor._assess_cycle_routes()

        assert assessment.rating == AspectRating.RED
        assert "LTN 1/20" in str(assessment.policy_refs) or "Table 5-2" in assessment.detail


class TestJunctionDesignAssessment:
    """
    Tests for junction design assessment.

    Implements [agent-integration:ReviewAssessor/TS-05], [TS-06]
    """

    @pytest.mark.asyncio
    async def test_junction_with_protection(self, assessor, mock_mcp_client, mock_claude_client):
        """
        Verifies [agent-integration:ReviewAssessor/TS-05] - Assess junction with protection

        Given: Documents show protected cycle crossing
        When: Assess junctions
        Then: Returns green rating; notes LTN 1/20 Ch.6 compliance
        """
        mock_mcp_client.call_tool.return_value = make_search_response([
            {
                "chunk_id": "chunk1",
                "text": "The site access junction includes a signal-controlled toucan crossing with a separate cycle phase and advanced stop lines for cyclists.",
                "relevance_score": 0.88,
                "metadata": {"document_name": "Transport Assessment.pdf"},
            }
        ])

        mock_claude_client.send_message.return_value = make_claude_response('''
{
    "rating": "green",
    "key_issue": "Protected cycle provision at site access junction",
    "detail": "The signal-controlled crossing with dedicated cycle phase provides appropriate protection at the main site access. This meets LTN 1/20 Chapter 6 requirements.",
    "policy_refs": ["LTN 1/20 Chapter 6"]
}
''')

        assessment = await assessor._assess_junction_design()

        assert assessment.rating == AspectRating.GREEN
        assert "Chapter 6" in str(assessment.policy_refs) or "Ch.6" in assessment.detail or "Chapter 6" in assessment.detail

    @pytest.mark.asyncio
    async def test_junction_without_protection(self, assessor, mock_mcp_client, mock_claude_client):
        """
        Verifies [agent-integration:ReviewAssessor/TS-06] - Assess junction without protection

        Given: Documents show unprotected junction
        When: Assess junctions
        Then: Returns red rating; specific LTN 1/20 requirements cited
        """
        mock_mcp_client.call_tool.return_value = make_search_response([
            {
                "chunk_id": "chunk1",
                "text": "The site access will be a simple priority junction with a give-way marking. No specific provision is made for cyclists at this location.",
                "relevance_score": 0.85,
                "metadata": {"document_name": "Transport Assessment.pdf"},
            }
        ])

        mock_claude_client.send_message.return_value = make_claude_response('''
{
    "rating": "red",
    "key_issue": "No protected cycle provision at site access junction",
    "detail": "The proposed priority junction provides no protection for cyclists. Given the traffic volumes, LTN 1/20 Chapter 6 requires protected provision.",
    "policy_refs": ["LTN 1/20 Chapter 6"]
}
''')

        assessment = await assessor._assess_junction_design()

        assert assessment.rating == AspectRating.RED
        assert "LTN 1/20" in str(assessment.policy_refs)


class TestPermeabilityAssessment:
    """
    Tests for permeability assessment.

    Implements [agent-integration:ReviewAssessor/TS-07], [TS-08]
    """

    @pytest.mark.asyncio
    async def test_good_permeability(self, assessor, mock_mcp_client, mock_claude_client):
        """
        Verifies [agent-integration:ReviewAssessor/TS-07] - Assess good permeability

        Given: Documents show filtered connections
        When: Assess permeability
        Then: Returns green rating; notes connections
        """
        mock_mcp_client.call_tool.return_value = make_search_response([
            {
                "chunk_id": "chunk1",
                "text": "The development includes three pedestrian/cycle links to adjacent areas, including a filtered connection to the existing estate to the east that prevents motor traffic through-routing.",
                "relevance_score": 0.91,
                "metadata": {"document_name": "Design and Access Statement.pdf"},
            }
        ])

        mock_claude_client.send_message.return_value = make_claude_response('''
{
    "rating": "green",
    "key_issue": "Good permeability with filtered connections",
    "detail": "The three pedestrian/cycle links provide excellent connectivity. The filtered connection to the east prevents rat-running while enabling sustainable travel.",
    "policy_refs": ["Manual for Streets Chapter 4", "LTN 1/20"]
}
''')

        assessment = await assessor._assess_permeability()

        assert assessment.rating == AspectRating.GREEN
        assert len(assessment.policy_refs) > 0

    @pytest.mark.asyncio
    async def test_missing_permeability(self, assessor, mock_mcp_client, mock_claude_client):
        """
        Verifies [agent-integration:ReviewAssessor/TS-08] - Assess missing permeability

        Given: No through connections for cyclists
        When: Assess permeability
        Then: Returns amber/red rating; notes missed opportunity
        """
        mock_mcp_client.call_tool.return_value = make_search_response([
            {
                "chunk_id": "chunk1",
                "text": "The development is accessed via a single vehicular access from the main road. Internal roads follow a traditional cul-de-sac layout.",
                "relevance_score": 0.75,
                "metadata": {"document_name": "Design and Access Statement.pdf"},
            }
        ])

        mock_claude_client.send_message.return_value = make_claude_response('''
{
    "rating": "amber",
    "key_issue": "Limited permeability - cul-de-sac layout without through routes",
    "detail": "The cul-de-sac layout with single access point misses opportunities for pedestrian and cycle connectivity. Through routes should be provided to adjacent areas.",
    "policy_refs": ["Manual for Streets Chapter 4"]
}
''')

        assessment = await assessor._assess_permeability()

        assert assessment.rating in (AspectRating.AMBER, AspectRating.RED)


class TestSingleDwellingApplication:
    """
    Tests for single dwelling/householder applications.

    Implements [agent-integration:ReviewAssessor/TS-09]
    """

    @pytest.mark.asyncio
    async def test_householder_application_scoping(self, mock_mcp_client, mock_claude_client):
        """
        Verifies [agent-integration:ReviewAssessor/TS-09] - Assess single dwelling application

        Given: Minor application with limited scope
        When: Assess all aspects
        Then: Appropriately scoped assessment; some aspects marked N/A
        """
        # Create assessor for householder application
        assessor = ReviewAssessor(
            mcp_client=mock_mcp_client,
            claude_client=mock_claude_client,
            application_ref="25/00123/HOU",
            application_type="householder",
        )

        # Return empty results for most searches (typical for householder)
        mock_mcp_client.call_tool.return_value = make_search_response([])

        result = await assessor.assess()

        # Check that some aspects are marked N/A for householder
        na_aspects = [a for a in result.aspects if a.rating == AspectRating.NOT_APPLICABLE]
        assert len(na_aspects) >= 1  # At least permeability should be N/A

        # Cycle parking should still be assessed (even householders need bike storage)
        parking_aspect = next(a for a in result.aspects if a.name == AspectName.CYCLE_PARKING)
        assert parking_aspect.rating != AspectRating.NOT_APPLICABLE


class TestMissingDocumentHandling:
    """
    Tests for missing document handling.

    Implements [agent-integration:ReviewAssessor/TS-10]
    """

    @pytest.mark.asyncio
    async def test_missing_transport_assessment(self, assessor, mock_mcp_client, mock_claude_client):
        """
        Verifies [agent-integration:ReviewAssessor/TS-10] - Handle missing Transport Assessment

        Given: Key document not in application
        When: Assess transport aspects
        Then: Notes absence; assesses based on available documents; flags for human review
        """
        # Setup search to return results without transport assessment
        def search_side_effect(tool_name, args):
            if tool_name == "search_application_docs":
                query = args.get("query", "")
                if "transport assessment" in query.lower():
                    # Return results from other documents only
                    return make_search_response([
                        {
                            "chunk_id": "chunk1",
                            "text": "Some cycling information from the Design and Access Statement.",
                            "relevance_score": 0.6,
                            "metadata": {
                                "document_name": "Design and Access Statement.pdf",
                                "document_type": "design_and_access",
                            },
                        }
                    ])
                return make_search_response([
                    {
                        "chunk_id": "chunk2",
                        "text": "Basic cycle parking provision described.",
                        "relevance_score": 0.5,
                        "metadata": {
                            "document_name": "Planning Statement.pdf",
                            "document_type": "planning_statement",
                        },
                    }
                ])
            return {}

        mock_mcp_client.call_tool.side_effect = search_side_effect

        # Setup Claude to provide assessments
        mock_claude_client.send_message.return_value = make_claude_response('''
{
    "rating": "amber",
    "key_issue": "Limited information available",
    "detail": "Assessment based on limited information from available documents.",
    "policy_refs": ["LTN 1/20"]
}
''')

        result = await assessor.assess()

        # Should flag missing transport assessment
        assert "transport_assessment" in result.missing_documents
        # Should have human review flag
        assert any("Transport Assessment" in flag for flag in result.human_review_flags)
        # Should still produce assessments
        assert len(result.aspects) == 4


class TestCompleteAssessment:
    """Tests for complete assessment workflow."""

    @pytest.mark.asyncio
    async def test_complete_assessment_workflow(self, assessor, mock_mcp_client, mock_claude_client):
        """Test that assess() runs all aspect assessments."""
        # Setup all searches to return some results
        mock_mcp_client.call_tool.return_value = make_search_response([
            {
                "chunk_id": "chunk1",
                "text": "Sample document content for assessment.",
                "relevance_score": 0.8,
                "metadata": {
                    "document_name": "Transport Assessment.pdf",
                    "document_type": "transport_assessment",
                },
            }
        ])

        mock_claude_client.send_message.return_value = make_claude_response('''
{
    "rating": "amber",
    "key_issue": "Standard assessment",
    "detail": "Assessment details here.",
    "policy_refs": ["LTN 1/20"]
}
''')

        result = await assessor.assess()

        # Should have all 4 aspects assessed
        assert len(result.aspects) == 4
        assert set(a.name for a in result.aspects) == {
            AspectName.CYCLE_PARKING,
            AspectName.CYCLE_ROUTES,
            AspectName.JUNCTION_DESIGN,
            AspectName.PERMEABILITY,
        }

    @pytest.mark.asyncio
    async def test_assessment_continues_on_aspect_failure(self, assessor, mock_mcp_client, mock_claude_client):
        """Test that assessment continues even if one aspect fails."""
        call_count = 0

        def search_side_effect(tool_name, args):
            nonlocal call_count
            call_count += 1
            # First call is for missing document check - let it succeed
            if call_count == 1:
                return make_search_response([
                    {
                        "chunk_id": "doc_check",
                        "text": "Document check",
                        "relevance_score": 0.5,
                        "metadata": {"document_type": "transport_assessment"},
                    }
                ])
            # Next few calls are for first aspect - make them fail
            if call_count <= 4:
                raise Exception("Search failed")
            # Remaining calls succeed
            return make_search_response([
                {
                    "chunk_id": "chunk1",
                    "text": "Some content.",
                    "relevance_score": 0.7,
                    "metadata": {},
                }
            ])

        mock_mcp_client.call_tool.side_effect = search_side_effect

        mock_claude_client.send_message.return_value = make_claude_response('''
{
    "rating": "green",
    "key_issue": "Good provision",
    "detail": "Assessment details.",
    "policy_refs": []
}
''')

        result = await assessor.assess()

        # Should still have all 4 aspects (first one degraded)
        assert len(result.aspects) == 4
        # Should have human review flag for failed aspect
        assert len(result.human_review_flags) >= 1


class TestSearchResults:
    """Tests for document search functionality."""

    @pytest.mark.asyncio
    async def test_search_deduplicates_results(self, assessor, mock_mcp_client):
        """Test that duplicate search results are deduplicated."""
        # Setup search to return same chunk from multiple queries
        mock_mcp_client.call_tool.return_value = make_search_response([
            {
                "chunk_id": "chunk1",
                "text": "Same content from both queries.",
                "relevance_score": 0.9,
                "metadata": {},
            }
        ])

        results = await assessor._search_documents(["query1", "query2"])

        # Should only have one result despite two queries
        assert len(results) == 1
        assert results[0].chunk_id == "chunk1"

    @pytest.mark.asyncio
    async def test_search_sorts_by_relevance(self, assessor, mock_mcp_client):
        """Test that search results are sorted by relevance."""
        mock_mcp_client.call_tool.return_value = make_search_response([
            {"chunk_id": "low", "text": "Low relevance", "relevance_score": 0.3, "metadata": {}},
            {"chunk_id": "high", "text": "High relevance", "relevance_score": 0.9, "metadata": {}},
            {"chunk_id": "mid", "text": "Mid relevance", "relevance_score": 0.6, "metadata": {}},
        ])

        results = await assessor._search_documents(["query"])

        assert results[0].chunk_id == "high"
        assert results[1].chunk_id == "mid"
        assert results[2].chunk_id == "low"


class TestClaudeResponseParsing:
    """Tests for Claude response parsing."""

    @pytest.mark.asyncio
    async def test_parse_json_in_code_block(self, assessor, mock_mcp_client, mock_claude_client):
        """Test parsing JSON wrapped in markdown code blocks."""
        mock_mcp_client.call_tool.return_value = make_search_response([
            {"chunk_id": "chunk1", "text": "Content", "relevance_score": 0.8, "metadata": {}}
        ])

        mock_claude_client.send_message.return_value = make_claude_response('''
Here is my assessment:

```json
{
    "rating": "green",
    "key_issue": "Good provision",
    "detail": "Details here.",
    "policy_refs": ["LTN 1/20"]
}
```
''')

        assessment = await assessor._assess_cycle_parking()

        assert assessment.rating == AspectRating.GREEN
        assert assessment.key_issue == "Good provision"

    @pytest.mark.asyncio
    async def test_parse_invalid_json_returns_degraded_assessment(self, assessor, mock_mcp_client, mock_claude_client):
        """Test that invalid JSON returns a degraded assessment."""
        mock_mcp_client.call_tool.return_value = make_search_response([
            {"chunk_id": "chunk1", "text": "Content", "relevance_score": 0.8, "metadata": {}}
        ])

        mock_claude_client.send_message.return_value = make_claude_response('''
This is not valid JSON at all, just a text response about the assessment.
The cycle parking looks adequate.
''')

        assessment = await assessor._assess_cycle_parking()

        # Should return degraded assessment
        assert assessment.rating == AspectRating.AMBER
        assert "manual review" in assessment.key_issue.lower()

    @pytest.mark.asyncio
    async def test_parse_na_rating(self, assessor, mock_mcp_client, mock_claude_client):
        """Test parsing N/A rating."""
        mock_mcp_client.call_tool.return_value = make_search_response([
            {"chunk_id": "chunk1", "text": "Content", "relevance_score": 0.8, "metadata": {}}
        ])

        mock_claude_client.send_message.return_value = make_claude_response('''
{
    "rating": "n/a",
    "key_issue": "Not applicable for this application type",
    "detail": "This is a householder application.",
    "policy_refs": []
}
''')

        assessment = await assessor._assess_cycle_parking()

        assert assessment.rating == AspectRating.NOT_APPLICABLE
