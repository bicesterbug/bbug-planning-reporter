# Agent module
"""
Agent module for review orchestration.

Phase 4 (agent-integration) implements the AI-powered review workflow
that coordinates multiple MCP servers to produce cycling advocacy reviews.
"""

from src.agent.claude_client import (
    ClaudeClient,
    ClaudeClientError,
    ClaudeResponse,
    TokenUsage,
    ToolCall,
)
from src.agent.mcp_client import (
    MCPClientManager,
    MCPConnectionError,
    MCPServerType,
    MCPToolError,
)
from src.agent.progress import (
    PHASE_WEIGHTS,
    ProgressTracker,
    ReviewPhase,
    WorkflowState,
)
from src.agent.assessor import (
    AspectAssessment,
    AspectName,
    AspectRating,
    AssessmentResult,
    ReviewAssessor,
    SearchResult,
)
from src.agent.policy_comparer import (
    ComplianceItem,
    PolicyComparer,
    PolicyComparisonResult,
    PolicyRevision,
    PolicySearchResult,
)
from src.agent.generator import (
    ApplicationSummary,
    ReviewGenerator,
    ReviewMetadata,
    ReviewOutput,
)
from src.agent.templates import ReviewTemplates

__all__ = [
    # MCP Client
    "MCPClientManager",
    "MCPConnectionError",
    "MCPServerType",
    "MCPToolError",
    # Progress Tracking
    "ProgressTracker",
    "ReviewPhase",
    "WorkflowState",
    "PHASE_WEIGHTS",
    # Claude Client
    "ClaudeClient",
    "ClaudeClientError",
    "ClaudeResponse",
    "TokenUsage",
    "ToolCall",
    # Assessor
    "ReviewAssessor",
    "AspectAssessment",
    "AspectName",
    "AspectRating",
    "AssessmentResult",
    "SearchResult",
    # Policy Comparer
    "PolicyComparer",
    "PolicyComparisonResult",
    "PolicyRevision",
    "PolicySearchResult",
    "ComplianceItem",
    # Generator
    "ReviewGenerator",
    "ReviewOutput",
    "ReviewMetadata",
    "ApplicationSummary",
    # Templates
    "ReviewTemplates",
]
