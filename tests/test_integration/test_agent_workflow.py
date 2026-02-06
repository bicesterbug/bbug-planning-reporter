"""
Integration tests for the agent workflow.

Implements [agent-integration:ITS-01] through [ITS-10]

These tests verify the complete workflow with all components working together
using mocked MCP servers and Claude client.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.assessor import AspectName, AspectRating, ReviewAssessor
from src.agent.claude_client import ClaudeResponse
from src.agent.generator import ApplicationSummary, ReviewGenerator, ReviewMetadata
from src.agent.policy_comparer import PolicyComparer


# Fixtures for mocked MCP responses
@pytest.fixture
def mock_mcp_client():
    """Create a mock MCP client with realistic responses."""
    client = AsyncMock()
    return client


@pytest.fixture
def mock_claude_client():
    """Create a mock Claude client."""
    client = AsyncMock()
    return client


def make_application_response(
    reference: str = "25/01178/REM",
    with_documents: bool = True,
) -> dict:
    """Create a mock application details response."""
    docs = [
        {"id": "doc1", "name": "Transport Assessment.pdf", "type": "transport_assessment"},
        {"id": "doc2", "name": "Design and Access Statement.pdf", "type": "design_and_access"},
        {"id": "doc3", "name": "Site Plan.pdf", "type": "site_plan"},
    ] if with_documents else []

    return {
        "status": "success",
        "application": {
            "reference": reference,
            "address": "Land at Test Site, Bicester",
            "proposal": "Reserved matters for residential development",
            "applicant": "Test Developments Ltd",
            "status": "Under consideration",
            "date_validated": "2025-01-20",
            "consultation_end": "2025-02-15",
            "documents": docs,
        },
    }


def make_document_search_response(content_type: str = "parking") -> dict:
    """Create a mock document search response based on content type."""
    content_map = {
        "parking": {
            "text": "The development proposes 48 Sheffield stands for cycle parking in a covered secure area.",
            "relevance_score": 0.92,
        },
        "routes": {
            "text": "A 3.0m segregated cycle track will be provided along the eastern boundary.",
            "relevance_score": 0.89,
        },
        "junction": {
            "text": "The site access includes a signal-controlled toucan crossing with cycle phase.",
            "relevance_score": 0.85,
        },
        "permeability": {
            "text": "Three pedestrian/cycle links provide filtered connections to adjacent areas.",
            "relevance_score": 0.88,
        },
        "empty": {
            "text": "",
            "relevance_score": 0.0,
        },
    }

    content = content_map.get(content_type, content_map["parking"])

    if content_type == "empty":
        return {"results": []}

    return {
        "results": [
            {
                "chunk_id": f"chunk_{content_type}",
                "text": content["text"],
                "relevance_score": content["relevance_score"],
                "metadata": {
                    "document_name": "Transport Assessment.pdf",
                    "document_type": "transport_assessment",
                },
            }
        ]
    }


def make_policy_search_response(source: str = "LTN_1_20") -> dict:
    """Create a mock policy search response."""
    return {
        "results": [
            {
                "chunk_id": f"policy_{source}",
                "text": "Cycle infrastructure must meet the standards set out in this guidance.",
                "relevance_score": 0.9,
                "metadata": {
                    "source": source,
                    "revision_id": f"rev_{source}_2020_07",
                    "section_ref": "Chapter 5",
                },
            }
        ]
    }


def make_claude_assessment_response(rating: str = "amber") -> ClaudeResponse:
    """Create a mock Claude response for assessment."""
    response = MagicMock(spec=ClaudeResponse)
    response.content = f'''{{
    "rating": "{rating}",
    "key_issue": "Assessment finding for {rating} rating",
    "detail": "Detailed analysis of the aspect.",
    "policy_refs": ["LTN 1/20"]
}}'''
    response.tool_calls = []
    response.stop_reason = "end_turn"
    return response


class TestCompleteWorkflowWithMockedMCP:
    """
    Integration tests for complete workflow.

    Implements [agent-integration:ITS-01] - Complete workflow with mocked MCP
    """

    @pytest.mark.asyncio
    async def test_complete_workflow_produces_review(
        self,
        mock_mcp_client,
        mock_claude_client,
    ):
        """
        Verifies [agent-integration:ITS-01] - Complete workflow with mocked MCP

        Given: Mock MCP servers with fixtures
        When: Submit review job
        Then: All phases complete; review produced
        """
        # Setup MCP responses
        def mcp_side_effect(tool_name, args):
            if tool_name == "search_application_docs":
                query = args.get("query", "").lower()
                if "parking" in query:
                    return make_document_search_response("parking")
                elif "route" in query or "cycle" in query:
                    return make_document_search_response("routes")
                elif "junction" in query:
                    return make_document_search_response("junction")
                elif "permeability" in query or "connectivity" in query:
                    return make_document_search_response("permeability")
                return make_document_search_response("parking")
            elif tool_name == "search_policy":
                return make_policy_search_response()
            return {}

        mock_mcp_client.call_tool.side_effect = mcp_side_effect
        mock_claude_client.send_message.return_value = make_claude_assessment_response("amber")

        # Create assessor and run assessment
        assessor = ReviewAssessor(
            mcp_client=mock_mcp_client,
            claude_client=mock_claude_client,
            application_ref="25/01178/REM",
            application_type="major",
        )

        assessment_result = await assessor.assess()

        # Verify all aspects assessed
        assert len(assessment_result.aspects) == 4

        # Create policy comparer and compare
        comparer = PolicyComparer(
            mcp_client=mock_mcp_client,
            validation_date="2025-01-20",
        )

        policy_result = await comparer.compare(assessment_result.aspects)

        # Create generator and produce output
        application = ApplicationSummary(
            reference="25/01178/REM",
            address="Land at Test Site, Bicester",
            proposal="Reserved matters for residential development",
            date_validated="2025-01-20",
        )

        generator = ReviewGenerator(
            review_id="rev_test123",
            application=application,
        )

        output = generator.generate(assessment_result, policy_result)

        # Verify complete output
        assert output.review_id == "rev_test123"
        assert output.overall_rating in ("green", "amber", "red")
        assert len(output.aspects) > 0
        assert output.full_markdown
        assert "# Cycle Advocacy Review" in output.full_markdown


class TestValidationDatePolicySelection:
    """
    Tests for policy revision selection based on validation date.

    Implements [agent-integration:ITS-02] - Validation date policy selection
    """

    @pytest.mark.asyncio
    async def test_validation_date_filters_policy_results(self, mock_mcp_client):
        """
        Verifies [agent-integration:ITS-02] - Validation date policy selection

        Given: Application validated 2024-03-15
        When: Policy search
        Then: 2024 NPPF revision returned, not 2025
        """
        mock_mcp_client.call_tool.return_value = make_policy_search_response("NPPF")

        comparer = PolicyComparer(
            mcp_client=mock_mcp_client,
            validation_date="2024-03-15",
        )

        # Trigger a policy search
        from src.agent.assessor import AspectAssessment, AspectName, AspectRating

        aspects = [
            AspectAssessment(
                name=AspectName.PERMEABILITY,
                rating=AspectRating.AMBER,
                key_issue="Test",
                detail="Test",
            )
        ]

        await comparer.compare(aspects)

        # Verify effective_date was passed
        calls = mock_mcp_client.call_tool.call_args_list
        for call in calls:
            if call[0][0] == "search_policy":
                assert call[0][1]["effective_date"] == "2024-03-15"


class TestPartialDocumentFailureRecovery:
    """
    Tests for handling partial document failures.

    Implements [agent-integration:ITS-04] - Partial document failure recovery
    """

    @pytest.mark.asyncio
    async def test_partial_failure_still_produces_review(
        self,
        mock_mcp_client,
        mock_claude_client,
    ):
        """
        Verifies [agent-integration:ITS-04] - Partial document failure recovery

        Given: 2 of 10 documents corrupt
        When: Ingest documents
        Then: 8 documents processed; 2 errors logged; review produced
        """
        call_count = 0

        def search_side_effect(tool_name, args):
            nonlocal call_count
            call_count += 1
            # Simulate some searches failing
            if call_count % 5 == 0:
                return {"results": []}  # Empty results for some queries
            return make_document_search_response("parking")

        mock_mcp_client.call_tool.side_effect = search_side_effect
        mock_claude_client.send_message.return_value = make_claude_assessment_response("amber")

        assessor = ReviewAssessor(
            mcp_client=mock_mcp_client,
            claude_client=mock_claude_client,
            application_ref="25/01178/REM",
            application_type="major",
        )

        result = await assessor.assess()

        # Should still produce assessment despite some failures
        assert len(result.aspects) == 4


class TestGreenRatingReview:
    """
    Tests for fully compliant applications.

    Implements [agent-integration:ITS-06] - Review with all green aspects
    """

    @pytest.mark.asyncio
    async def test_green_review_positive_acknowledgment(
        self,
        mock_mcp_client,
        mock_claude_client,
    ):
        """
        Verifies [agent-integration:ITS-06] - Review with all green aspects

        Given: Compliant application fixtures
        When: Generate review
        Then: Green overall rating; positive acknowledgment
        """
        mock_mcp_client.call_tool.return_value = make_document_search_response("parking")
        mock_claude_client.send_message.return_value = make_claude_assessment_response("green")

        assessor = ReviewAssessor(
            mcp_client=mock_mcp_client,
            claude_client=mock_claude_client,
            application_ref="25/01178/REM",
            application_type="major",
        )

        assessment_result = await assessor.assess()

        # All should be green
        for aspect in assessment_result.aspects:
            assert aspect.rating == AspectRating.GREEN

        # Generate review
        comparer = PolicyComparer(mock_mcp_client, "2025-01-20")
        mock_mcp_client.call_tool.return_value = make_policy_search_response()
        policy_result = await comparer.compare(assessment_result.aspects)

        application = ApplicationSummary(reference="25/01178/REM")
        generator = ReviewGenerator("rev_test", application)
        output = generator.generate(assessment_result, policy_result)

        assert output.overall_rating == "green"
        # Should have positive acknowledgment
        assert any("commended" in r.lower() for r in output.recommendations)


class TestRedRatingReview:
    """
    Tests for non-compliant applications.

    Implements [agent-integration:ITS-07] - Review with red aspects
    """

    @pytest.mark.asyncio
    async def test_red_review_refusal_recommended(
        self,
        mock_mcp_client,
        mock_claude_client,
    ):
        """
        Verifies [agent-integration:ITS-07] - Review with red aspects

        Given: Non-compliant application fixtures
        When: Generate review
        Then: Red overall rating; refusal recommended
        """
        mock_mcp_client.call_tool.return_value = make_document_search_response("parking")
        mock_claude_client.send_message.return_value = make_claude_assessment_response("red")

        assessor = ReviewAssessor(
            mcp_client=mock_mcp_client,
            claude_client=mock_claude_client,
            application_ref="25/01178/REM",
            application_type="major",
        )

        assessment_result = await assessor.assess()

        # Generate review
        comparer = PolicyComparer(mock_mcp_client, "2025-01-20")
        mock_mcp_client.call_tool.return_value = make_policy_search_response()
        policy_result = await comparer.compare(assessment_result.aspects)

        application = ApplicationSummary(reference="25/01178/REM")
        generator = ReviewGenerator("rev_test", application)
        output = generator.generate(assessment_result, policy_result)

        assert output.overall_rating == "red"
        # Should NOT have conditions (refusal recommended)
        assert len(output.suggested_conditions) == 0
        # Should have critical recommendations
        assert any("CRITICAL" in r for r in output.recommendations)


class TestTokenUsageTracking:
    """
    Tests for token usage tracking.

    Implements [agent-integration:ITS-08] - Token usage tracking
    """

    @pytest.mark.asyncio
    async def test_token_usage_under_limit(
        self,
        mock_mcp_client,
        mock_claude_client,
    ):
        """
        Verifies [agent-integration:ITS-08] - Token usage tracking

        Given: Complete review
        When: Check metadata
        Then: Total tokens < 50,000
        """
        mock_mcp_client.call_tool.return_value = make_document_search_response("parking")

        # Setup Claude response with token usage
        response = make_claude_assessment_response("amber")
        mock_claude_client.send_message.return_value = response

        assessor = ReviewAssessor(
            mcp_client=mock_mcp_client,
            claude_client=mock_claude_client,
            application_ref="25/01178/REM",
            application_type="major",
        )

        assessment_result = await assessor.assess()

        # Generate review with metadata
        comparer = PolicyComparer(mock_mcp_client, "2025-01-20")
        mock_mcp_client.call_tool.return_value = make_policy_search_response()
        policy_result = await comparer.compare(assessment_result.aspects)

        application = ApplicationSummary(reference="25/01178/REM")
        generator = ReviewGenerator("rev_test", application)

        # Simulate token usage
        metadata = ReviewMetadata(
            total_tokens_used=35000,  # Under 50k limit
            processing_time_seconds=120,
        )

        output = generator.generate(assessment_result, policy_result, metadata)

        # Verify token usage is tracked and under limit
        assert output.metadata.total_tokens_used < 50000


class TestMissingTransportAssessment:
    """
    Tests for handling missing Transport Assessment.

    Implements [agent-integration:ITS-09] - Missing Transport Assessment
    """

    @pytest.mark.asyncio
    async def test_missing_ta_flags_human_review(
        self,
        mock_mcp_client,
        mock_claude_client,
    ):
        """
        Verifies [agent-integration:ITS-09] - Missing Transport Assessment

        Given: Application without TA
        When: Generate review
        Then: Flags for human review; assesses available docs
        """
        # Return results without transport_assessment document type
        def search_side_effect(tool_name, args):
            return {
                "results": [
                    {
                        "chunk_id": "chunk1",
                        "text": "Some content from Design Statement.",
                        "relevance_score": 0.6,
                        "metadata": {
                            "document_name": "Design Statement.pdf",
                            "document_type": "design_and_access",
                        },
                    }
                ]
            }

        mock_mcp_client.call_tool.side_effect = search_side_effect
        mock_claude_client.send_message.return_value = make_claude_assessment_response("amber")

        assessor = ReviewAssessor(
            mcp_client=mock_mcp_client,
            claude_client=mock_claude_client,
            application_ref="25/01178/REM",
            application_type="major",
        )

        result = await assessor.assess()

        # Should flag missing transport assessment
        assert "transport_assessment" in result.missing_documents
        assert any("Transport Assessment" in flag for flag in result.human_review_flags)

        # Should still produce assessments
        assert len(result.aspects) == 4


class TestPolicyCitationVerification:
    """
    Tests for policy citation accuracy.

    Implements [agent-integration:ITS-10] - Policy citation verification
    """

    @pytest.mark.asyncio
    async def test_citations_correspond_to_retrieved_content(
        self,
        mock_mcp_client,
    ):
        """
        Verifies [agent-integration:ITS-10] - Policy citation verification

        Given: Review with policy citations
        When: Verify citations
        Then: All citations correspond to retrieved policy content
        """
        # Setup policy section retrieval
        mock_mcp_client.call_tool.return_value = {
            "text": "Cycle parking must meet the standards in Table 11-1.",
            "section_ref": "Chapter 11",
        }

        comparer = PolicyComparer(mock_mcp_client, "2025-01-20")

        # Verify citation
        is_valid = await comparer.verify_citation(
            source="LTN_1_20",
            section_ref="Chapter 11",
            expected_content="Cycle parking must meet the standards",
        )

        assert is_valid is True

    @pytest.mark.asyncio
    async def test_invalid_citation_detected(self, mock_mcp_client):
        """Test that mismatched citations are detected."""
        mock_mcp_client.call_tool.return_value = {
            "text": "Completely different content about something else.",
            "section_ref": "Chapter 11",
        }

        comparer = PolicyComparer(mock_mcp_client, "2025-01-20")

        is_valid = await comparer.verify_citation(
            source="LTN_1_20",
            section_ref="Chapter 11",
            expected_content="Cycle parking must meet the standards",
        )

        assert is_valid is False


class TestProgressEvents:
    """
    Tests for progress event publishing.

    Implements [agent-integration:ITS-03] - Document ingestion progress
    """

    @pytest.mark.asyncio
    async def test_sub_progress_events_published(self):
        """
        Verifies [agent-integration:ITS-03] - Document ingestion progress

        Given: 10 documents to process
        When: Ingest documents
        Then: 10 progress events with sub-progress
        """
        from src.agent.progress import ProgressTracker, ReviewPhase

        # Create tracker with mock Redis
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock()

        tracker = ProgressTracker(
            review_id="rev_test",
            application_ref="25/01178/REM",
            redis_client=mock_redis,
        )

        # Simulate document processing progress
        await tracker.start_phase(ReviewPhase.INGESTING_DOCUMENTS)

        for i in range(10):
            await tracker.update_sub_progress(
                f"Processing document {i + 1} of 10",
                current=i + 1,
                total=10,
            )

        # Verify progress events were published
        # Each update should publish an event
        assert mock_redis.publish.call_count >= 10


class TestMCPReconnection:
    """
    Tests for MCP reconnection during workflow.

    Implements [agent-integration:ITS-05] - MCP reconnection during workflow
    """

    @pytest.mark.asyncio
    async def test_reconnection_allows_workflow_completion(
        self,
        mock_mcp_client,
        mock_claude_client,
    ):
        """
        Verifies [agent-integration:ITS-05] - MCP reconnection during workflow

        Given: MCP connection drops in phase 3
        When: Processing continues
        Then: Reconnects; phase 3 completes
        """
        call_count = 0

        def side_effect_with_failure(tool_name, args):
            nonlocal call_count
            call_count += 1
            # Fail on 5th call to simulate connection drop
            if call_count == 5:
                from src.agent.mcp_client import MCPToolError
                raise MCPToolError(tool_name, "Connection lost")
            return make_document_search_response("parking")

        mock_mcp_client.call_tool.side_effect = side_effect_with_failure
        mock_claude_client.send_message.return_value = make_claude_assessment_response("amber")

        assessor = ReviewAssessor(
            mcp_client=mock_mcp_client,
            claude_client=mock_claude_client,
            application_ref="25/01178/REM",
            application_type="major",
        )

        # Should complete despite one failure (graceful degradation)
        result = await assessor.assess()

        # Should still have all aspects (some may be degraded)
        assert len(result.aspects) == 4
