"""
Review Assessor for cycling-focused assessment of planning applications.

Implements [agent-integration:FR-004] - Assess cycle parking provision
Implements [agent-integration:FR-005] - Assess cycle routes
Implements [agent-integration:FR-006] - Assess junction design
Implements [agent-integration:FR-007] - Assess permeability
Implements [agent-integration:FR-016] - Handle missing documents

Implements:
- [agent-integration:ReviewAssessor/TS-01] Assess adequate cycle parking
- [agent-integration:ReviewAssessor/TS-02] Assess no cycle parking mentioned
- [agent-integration:ReviewAssessor/TS-03] Assess compliant cycle routes
- [agent-integration:ReviewAssessor/TS-04] Assess non-compliant shared use
- [agent-integration:ReviewAssessor/TS-05] Assess junction with protection
- [agent-integration:ReviewAssessor/TS-06] Assess junction without protection
- [agent-integration:ReviewAssessor/TS-07] Assess good permeability
- [agent-integration:ReviewAssessor/TS-08] Assess missing permeability
- [agent-integration:ReviewAssessor/TS-09] Assess single dwelling application
- [agent-integration:ReviewAssessor/TS-10] Handle missing Transport Assessment
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

from src.agent.claude_client import ClaudeClient
from src.agent.mcp_client import MCPClientManager, MCPToolError

logger = structlog.get_logger(__name__)


class AspectRating(str, Enum):
    """Rating for a review aspect."""

    GREEN = "green"
    AMBER = "amber"
    RED = "red"
    NOT_APPLICABLE = "n/a"


class AspectName(str, Enum):
    """Names of review aspects."""

    CYCLE_PARKING = "Cycle Parking"
    CYCLE_ROUTES = "Cycle Routes"
    JUNCTION_DESIGN = "Junction Design"
    PERMEABILITY = "Permeability"


@dataclass
class AspectAssessment:
    """Assessment result for a single aspect."""

    name: AspectName
    rating: AspectRating
    key_issue: str
    detail: str
    policy_refs: list[str] = field(default_factory=list)
    evidence_chunks: list[str] = field(default_factory=list)


@dataclass
class SearchResult:
    """A document search result."""

    chunk_id: str
    text: str
    relevance_score: float
    document_name: str | None = None
    document_type: str | None = None


@dataclass
class AssessmentResult:
    """Complete assessment result."""

    aspects: list[AspectAssessment] = field(default_factory=list)
    missing_documents: list[str] = field(default_factory=list)
    human_review_flags: list[str] = field(default_factory=list)
    documents_analysed: int = 0


# Assessment prompts for Claude
CYCLE_PARKING_PROMPT = """You are assessing a planning application's cycle parking provision against UK standards.

Review the following document excerpts and assess:
1. Quantity of cycle parking spaces (compare against local plan requirements)
2. Type of stands (Sheffield stands preferred)
3. Location (convenient, secure, covered)
4. Cargo/adapted bike provision (required by LTN 1/20 Chapter 11)
5. Security features (lockable, lit)

Document excerpts:
{excerpts}

Application reference: {application_ref}
Application type: {application_type}

Provide your assessment in the following JSON format:
{{
    "rating": "green" | "amber" | "red" | "n/a",
    "key_issue": "Single sentence summary of main finding",
    "detail": "Full analysis paragraph(s)",
    "policy_refs": ["List of relevant policy references cited"]
}}

Rating guidance:
- GREEN: Meets or exceeds standards; includes cargo spaces
- AMBER: Meets minimum; minor issues (e.g., missing cargo spaces)
- RED: Below standards or missing entirely
- N/A: Not applicable for this application type (e.g., minor householder)
"""

CYCLE_ROUTES_PROMPT = """You are assessing a planning application's cycle routes against LTN 1/20 standards.

Review the following document excerpts and assess:
1. On-site cycle routes (width, surface, gradients)
2. Connections to existing cycle network
3. Segregation from motor traffic (compare against LTN 1/20 Table 5-2 based on traffic volume)
4. Route directness and legibility

Document excerpts:
{excerpts}

Application reference: {application_ref}
Application type: {application_type}

Key LTN 1/20 standards:
- Shared use only acceptable where traffic <2500 PCU/day
- Segregated track required where >2500 PCU/day
- Minimum width 3m for two-way cycle track

Provide your assessment in the following JSON format:
{{
    "rating": "green" | "amber" | "red" | "n/a",
    "key_issue": "Single sentence summary of main finding",
    "detail": "Full analysis paragraph(s)",
    "policy_refs": ["List of relevant policy references cited"]
}}

Rating guidance:
- GREEN: Meets LTN 1/20 fully
- AMBER: Partial compliance; minor deviations
- RED: Non-compliant with core standards
- N/A: Not applicable for this application type
"""

JUNCTION_DESIGN_PROMPT = """You are assessing a planning application's junction designs against LTN 1/20 Chapter 6.

Review the following document excerpts and assess:
1. Cycle provision at site access junctions
2. Protected crossings where required
3. Visibility splays for cyclists
4. Priority arrangements at junctions

Document excerpts:
{excerpts}

Application reference: {application_ref}
Application type: {application_type}

Key LTN 1/20 Chapter 6 requirements:
- Protected provision required at junctions with significant traffic
- Cycle bypass of traffic signals where possible
- Advance stop lines at signalised junctions

Provide your assessment in the following JSON format:
{{
    "rating": "green" | "amber" | "red" | "n/a",
    "key_issue": "Single sentence summary of main finding",
    "detail": "Full analysis paragraph(s)",
    "policy_refs": ["List of relevant policy references cited"]
}}

Rating guidance:
- GREEN: Protected provision where required
- AMBER: Some protection; minor gaps
- RED: No protection where LTN 1/20 requires
- N/A: Not applicable (no significant junctions)
"""

PERMEABILITY_PROMPT = """You are assessing a planning application's pedestrian and cycle permeability.

Review the following document excerpts and assess:
1. Internal route connectivity
2. Connections to adjacent areas
3. Filtered permeability (allowing cycles through but not motor traffic)
4. Desire line alignment

Document excerpts:
{excerpts}

Application reference: {application_ref}
Application type: {application_type}

Key considerations:
- Development should enhance walking and cycling connectivity
- Filtered permeability creates low-traffic neighbourhoods
- Cul-de-sacs should have cycle/pedestrian through routes

Provide your assessment in the following JSON format:
{{
    "rating": "green" | "amber" | "red" | "n/a",
    "key_issue": "Single sentence summary of main finding",
    "detail": "Full analysis paragraph(s)",
    "policy_refs": ["List of relevant policy references cited"]
}}

Rating guidance:
- GREEN: Full connectivity; filtered permeability provided
- AMBER: Internal connectivity only; missing external connections
- RED: Poor permeability; barriers to cycling
- N/A: Not applicable for this application type
"""


class ReviewAssessor:
    """
    Performs cycling-focused assessment of planning application documents.

    Implements [agent-integration:ReviewAssessor/TS-01] through [TS-10]

    Uses document search to find relevant content and Claude to analyse
    and rate each aspect of the application.
    """

    # Key document types that should be present for a full assessment
    KEY_DOCUMENT_TYPES = [
        "transport_assessment",
        "design_access_statement",
        "site_plan",
    ]

    # Search queries for each aspect
    ASPECT_QUERIES = {
        AspectName.CYCLE_PARKING: [
            "cycle parking spaces Sheffield stands",
            "bicycle storage provision",
            "cycle parking location security",
        ],
        AspectName.CYCLE_ROUTES: [
            "cycle routes cycleway infrastructure",
            "cycling provision segregated track",
            "cycle network connections",
        ],
        AspectName.JUNCTION_DESIGN: [
            "junction design cycle crossing",
            "site access junction cyclists",
            "traffic signals cycle provision",
        ],
        AspectName.PERMEABILITY: [
            "pedestrian cycle permeability",
            "filtered permeability modal filter",
            "through routes connectivity",
        ],
    }

    def __init__(
        self,
        mcp_client: MCPClientManager,
        claude_client: ClaudeClient,
        application_ref: str,
        application_type: str | None = None,
    ) -> None:
        """
        Initialize the assessor.

        Args:
            mcp_client: MCP client for document search.
            claude_client: Claude client for AI analysis.
            application_ref: Planning application reference.
            application_type: Type of application (e.g., "major", "minor", "householder").
        """
        self._mcp = mcp_client
        self._claude = claude_client
        self._application_ref = application_ref
        self._application_type = application_type or "unknown"
        self._available_document_types: set[str] = set()

    async def assess(self) -> AssessmentResult:
        """
        Perform complete assessment of all aspects.

        Implements [agent-integration:ReviewAssessor/TS-09] - Single dwelling application

        Returns:
            AssessmentResult with all aspect assessments and flags.
        """
        logger.info(
            "Starting assessment",
            application_ref=self._application_ref,
            application_type=self._application_type,
        )

        result = AssessmentResult()

        # Check for missing key documents
        await self._check_missing_documents(result)

        # Assess each aspect
        aspects_to_assess = [
            (AspectName.CYCLE_PARKING, self._assess_cycle_parking),
            (AspectName.CYCLE_ROUTES, self._assess_cycle_routes),
            (AspectName.JUNCTION_DESIGN, self._assess_junction_design),
            (AspectName.PERMEABILITY, self._assess_permeability),
        ]

        for aspect_name, assess_func in aspects_to_assess:
            try:
                assessment = await assess_func()
                result.aspects.append(assessment)
            except Exception as e:
                logger.warning(
                    "Aspect assessment failed",
                    aspect=aspect_name.value,
                    error=str(e),
                )
                # Add a degraded assessment
                result.aspects.append(
                    AspectAssessment(
                        name=aspect_name,
                        rating=AspectRating.AMBER,
                        key_issue="Assessment could not be completed",
                        detail=f"Unable to fully assess due to: {e}",
                        policy_refs=[],
                    )
                )
                result.human_review_flags.append(
                    f"{aspect_name.value} assessment requires manual review"
                )

        logger.info(
            "Assessment complete",
            application_ref=self._application_ref,
            aspects_assessed=len(result.aspects),
            human_review_flags=len(result.human_review_flags),
        )

        return result

    async def _check_missing_documents(self, result: AssessmentResult) -> None:
        """
        Check for missing key documents.

        Implements [agent-integration:ReviewAssessor/TS-10] - Missing Transport Assessment
        """
        # Query for document types available
        try:
            search_result = await self._mcp.call_tool(
                "search_application_docs",
                {
                    "query": "transport assessment design access statement",
                    "application_ref": self._application_ref,
                    "max_results": 20,
                },
            )

            results = search_result.get("results", [])
            for r in results:
                doc_type = r.get("metadata", {}).get("document_type")
                if doc_type:
                    self._available_document_types.add(doc_type.lower())

            result.documents_analysed = len(set(r.get("document_id") for r in results if r.get("document_id")))

        except MCPToolError as e:
            logger.warning("Could not check document types", error=str(e))

        # Check for missing key documents
        for doc_type in self.KEY_DOCUMENT_TYPES:
            if doc_type not in self._available_document_types:
                result.missing_documents.append(doc_type)
                result.human_review_flags.append(
                    f"Key document missing: {doc_type.replace('_', ' ').title()}"
                )

    async def _search_documents(
        self,
        queries: list[str],
        max_results_per_query: int = 5,
    ) -> list[SearchResult]:
        """
        Search documents with multiple queries and deduplicate results.

        Args:
            queries: List of search queries.
            max_results_per_query: Maximum results per query.

        Returns:
            Deduplicated list of search results.
        """
        all_results: dict[str, SearchResult] = {}

        for query in queries:
            try:
                response = await self._mcp.call_tool(
                    "search_application_docs",
                    {
                        "query": query,
                        "application_ref": self._application_ref,
                        "max_results": max_results_per_query,
                    },
                )

                for r in response.get("results", []):
                    chunk_id = r.get("chunk_id", r.get("id", ""))
                    if chunk_id and chunk_id not in all_results:
                        all_results[chunk_id] = SearchResult(
                            chunk_id=chunk_id,
                            text=r.get("text", ""),
                            relevance_score=r.get("relevance_score", 0.0),
                            document_name=r.get("metadata", {}).get("document_name"),
                            document_type=r.get("metadata", {}).get("document_type"),
                        )

            except MCPToolError as e:
                logger.warning(
                    "Document search failed",
                    query=query,
                    error=str(e),
                )

        # Sort by relevance and return
        return sorted(
            all_results.values(),
            key=lambda x: x.relevance_score,
            reverse=True,
        )

    async def _assess_with_claude(
        self,
        aspect_name: AspectName,
        prompt_template: str,
        search_results: list[SearchResult],
    ) -> AspectAssessment:
        """
        Use Claude to assess an aspect based on search results.

        Args:
            aspect_name: The aspect being assessed.
            prompt_template: The prompt template for this aspect.
            search_results: Document search results.

        Returns:
            AspectAssessment with Claude's analysis.
        """
        # Format excerpts
        if not search_results:
            excerpts = "No relevant content found in application documents."
        else:
            excerpt_parts = []
            for i, r in enumerate(search_results[:10], 1):
                source = r.document_name or "Unknown document"
                excerpt_parts.append(f"[{i}] Source: {source}\n{r.text}\n")
            excerpts = "\n---\n".join(excerpt_parts)

        # Build prompt
        prompt = prompt_template.format(
            excerpts=excerpts,
            application_ref=self._application_ref,
            application_type=self._application_type,
        )

        # Call Claude
        response = await self._claude.send_message(
            messages=[{"role": "user", "content": prompt}],
            system="You are an expert planning officer specialising in active travel assessment. Always respond with valid JSON.",
            max_tokens=1500,
        )

        # Parse response
        try:
            import json

            # Extract JSON from response
            content = response.content
            # Handle potential markdown code blocks
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            data = json.loads(content.strip())

            rating_str = data.get("rating", "amber").lower()
            if rating_str == "n/a":
                rating = AspectRating.NOT_APPLICABLE
            else:
                rating = AspectRating(rating_str)

            return AspectAssessment(
                name=aspect_name,
                rating=rating,
                key_issue=data.get("key_issue", "Assessment incomplete"),
                detail=data.get("detail", ""),
                policy_refs=data.get("policy_refs", []),
                evidence_chunks=[r.chunk_id for r in search_results[:5]],
            )

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(
                "Failed to parse Claude response",
                aspect=aspect_name.value,
                error=str(e),
                response=response.content[:500],
            )
            # Return a degraded assessment
            return AspectAssessment(
                name=aspect_name,
                rating=AspectRating.AMBER,
                key_issue="Assessment requires manual review",
                detail=f"Automated assessment could not be completed. Raw analysis: {response.content[:500]}",
                policy_refs=[],
            )

    async def _assess_cycle_parking(self) -> AspectAssessment:
        """
        Assess cycle parking provision.

        Implements [agent-integration:ReviewAssessor/TS-01] - Adequate parking
        Implements [agent-integration:ReviewAssessor/TS-02] - No parking mentioned
        """
        logger.debug("Assessing cycle parking", application_ref=self._application_ref)

        search_results = await self._search_documents(
            self.ASPECT_QUERIES[AspectName.CYCLE_PARKING]
        )

        # If no results found, this is likely a red rating situation
        if not search_results:
            return AspectAssessment(
                name=AspectName.CYCLE_PARKING,
                rating=AspectRating.RED,
                key_issue="No cycle parking information found in application documents",
                detail="The application documents do not appear to contain any information about cycle parking provision. This is a critical omission for cycling infrastructure assessment.",
                policy_refs=["LTN 1/20 Chapter 11", "Local Plan cycle parking standards"],
            )

        return await self._assess_with_claude(
            AspectName.CYCLE_PARKING,
            CYCLE_PARKING_PROMPT,
            search_results,
        )

    async def _assess_cycle_routes(self) -> AspectAssessment:
        """
        Assess cycle routes provision.

        Implements [agent-integration:ReviewAssessor/TS-03] - Compliant routes
        Implements [agent-integration:ReviewAssessor/TS-04] - Non-compliant shared use
        """
        logger.debug("Assessing cycle routes", application_ref=self._application_ref)

        search_results = await self._search_documents(
            self.ASPECT_QUERIES[AspectName.CYCLE_ROUTES]
        )

        # For minor/householder applications, routes may not be applicable
        if not search_results and self._application_type in ("householder", "minor"):
            return AspectAssessment(
                name=AspectName.CYCLE_ROUTES,
                rating=AspectRating.NOT_APPLICABLE,
                key_issue="Cycle routes assessment not applicable for this application type",
                detail=f"This {self._application_type} application does not include highway works or significant external areas where cycle route provision would be assessed.",
                policy_refs=[],
            )

        if not search_results:
            return AspectAssessment(
                name=AspectName.CYCLE_ROUTES,
                rating=AspectRating.AMBER,
                key_issue="No cycle route information found in application documents",
                detail="The application documents do not contain clear information about cycle route provision. Further information may be needed.",
                policy_refs=["LTN 1/20"],
            )

        return await self._assess_with_claude(
            AspectName.CYCLE_ROUTES,
            CYCLE_ROUTES_PROMPT,
            search_results,
        )

    async def _assess_junction_design(self) -> AspectAssessment:
        """
        Assess junction design for cycle safety.

        Implements [agent-integration:ReviewAssessor/TS-05] - Junction with protection
        Implements [agent-integration:ReviewAssessor/TS-06] - Junction without protection
        """
        logger.debug("Assessing junction design", application_ref=self._application_ref)

        search_results = await self._search_documents(
            self.ASPECT_QUERIES[AspectName.JUNCTION_DESIGN]
        )

        # For minor/householder applications, junctions may not be applicable
        if not search_results and self._application_type in ("householder", "minor"):
            return AspectAssessment(
                name=AspectName.JUNCTION_DESIGN,
                rating=AspectRating.NOT_APPLICABLE,
                key_issue="Junction design assessment not applicable for this application type",
                detail=f"This {self._application_type} application does not include new or modified junctions.",
                policy_refs=[],
            )

        if not search_results:
            return AspectAssessment(
                name=AspectName.JUNCTION_DESIGN,
                rating=AspectRating.AMBER,
                key_issue="No junction design information found in application documents",
                detail="The application documents do not contain clear information about junction design. If new junctions are proposed, further information is needed.",
                policy_refs=["LTN 1/20 Chapter 6"],
            )

        return await self._assess_with_claude(
            AspectName.JUNCTION_DESIGN,
            JUNCTION_DESIGN_PROMPT,
            search_results,
        )

    async def _assess_permeability(self) -> AspectAssessment:
        """
        Assess pedestrian and cycle permeability.

        Implements [agent-integration:ReviewAssessor/TS-07] - Good permeability
        Implements [agent-integration:ReviewAssessor/TS-08] - Missing permeability
        """
        logger.debug("Assessing permeability", application_ref=self._application_ref)

        search_results = await self._search_documents(
            self.ASPECT_QUERIES[AspectName.PERMEABILITY]
        )

        # For householder applications, permeability is typically not applicable
        if not search_results and self._application_type == "householder":
            return AspectAssessment(
                name=AspectName.PERMEABILITY,
                rating=AspectRating.NOT_APPLICABLE,
                key_issue="Permeability assessment not applicable for householder application",
                detail="This householder application does not involve site-wide permeability considerations.",
                policy_refs=[],
            )

        if not search_results:
            return AspectAssessment(
                name=AspectName.PERMEABILITY,
                rating=AspectRating.AMBER,
                key_issue="No permeability information found in application documents",
                detail="The application documents do not contain clear information about pedestrian and cycle permeability. This should be addressed to ensure good connectivity.",
                policy_refs=["Manual for Streets", "LTN 1/20"],
            )

        return await self._assess_with_claude(
            AspectName.PERMEABILITY,
            PERMEABILITY_PROMPT,
            search_results,
        )
