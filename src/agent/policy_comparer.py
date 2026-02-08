"""
Policy Comparer for comparing application proposals against policy requirements.

Implements [agent-integration:FR-003] - Use application validation date for policy queries
Implements [agent-integration:FR-008] - Generate policy compliance matrix
Implements [agent-integration:FR-014] - Track policy revisions used
Implements [agent-integration:NFR-003] - Citation accuracy

Implements:
- [agent-integration:PolicyComparer/TS-01] Query with validation date
- [agent-integration:PolicyComparer/TS-02] NPPF revision selection
- [agent-integration:PolicyComparer/TS-03] Missing validation date fallback
- [agent-integration:PolicyComparer/TS-04] Generate compliance matrix
- [agent-integration:PolicyComparer/TS-05] Track revisions used
- [agent-integration:PolicyComparer/TS-06] Policy not applicable
- [agent-integration:PolicyComparer/TS-07] Citation verification
- [agent-integration:PolicyComparer/TS-08] Multiple sources for requirement
"""

from dataclasses import dataclass, field
from datetime import date

import structlog

from src.agent.assessor import AspectAssessment, AspectName, AspectRating
from src.agent.mcp_client import MCPClientManager, MCPToolError

logger = structlog.get_logger(__name__)


@dataclass
class PolicyRevision:
    """A policy revision that was used in the comparison."""

    source: str
    revision_id: str
    version_label: str
    effective_date: str | None = None


@dataclass
class PolicySearchResult:
    """A search result from the policy knowledge base."""

    chunk_id: str
    text: str
    relevance_score: float
    source: str
    revision_id: str
    section_ref: str | None = None


@dataclass
class ComplianceItem:
    """A single item in the policy compliance matrix."""

    requirement: str
    policy_source: str
    policy_revision: str
    compliant: bool
    notes: str
    section_ref: str | None = None
    evidence_chunk_id: str | None = None


@dataclass
class PolicyComparisonResult:
    """Complete result of policy comparison."""

    compliance_matrix: list[ComplianceItem] = field(default_factory=list)
    revisions_used: list[PolicyRevision] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# Policy queries for each aspect type
ASPECT_POLICY_QUERIES = {
    AspectName.CYCLE_PARKING: [
        ("cycle parking standards quantity", ["LTN_1_20", "LOCAL_PLAN"]),
        ("cycle parking design Sheffield stands", ["LTN_1_20"]),
        ("cargo bike parking accessible", ["LTN_1_20"]),
    ],
    AspectName.CYCLE_ROUTES: [
        ("segregated cycle track width requirements", ["LTN_1_20"]),
        ("shared use path traffic volume threshold", ["LTN_1_20"]),
        ("cycle infrastructure design standards", ["LTN_1_20", "LOCAL_PLAN"]),
    ],
    AspectName.JUNCTION_DESIGN: [
        ("junction design cycle safety", ["LTN_1_20"]),
        ("protected cycle crossing junction", ["LTN_1_20"]),
        ("cycle provision at junctions", ["LTN_1_20"]),
    ],
    AspectName.PERMEABILITY: [
        ("pedestrian cycle permeability", ["LTN_1_20", "MANUAL_FOR_STREETS"]),
        ("filtered permeability modal filter", ["LTN_1_20"]),
        ("cycle connectivity through routes", ["LOCAL_PLAN", "NPPF"]),
    ],
}


class PolicyComparer:
    """
    Compares application proposals against policy requirements.

    Implements [agent-integration:PolicyComparer/TS-01] through [TS-08]

    Uses the policy knowledge base to find relevant requirements and
    generates a compliance matrix based on assessment results.
    """

    # Known policy sources and their human-readable names
    POLICY_SOURCES = {
        "LTN_1_20": "LTN 1/20",
        "NPPF": "NPPF",
        "LOCAL_PLAN": "Cherwell Local Plan",
        "MANUAL_FOR_STREETS": "Manual for Streets",
    }

    def __init__(
        self,
        mcp_client: MCPClientManager,
        validation_date: str | None = None,
    ) -> None:
        """
        Initialize the policy comparer.

        Implements [agent-integration:PolicyComparer/TS-03] - Missing validation date fallback

        Args:
            mcp_client: MCP client for policy KB queries.
            validation_date: Application validation date (ISO format YYYY-MM-DD).
                           Falls back to current date if not provided.
        """
        self._mcp = mcp_client
        self._validation_date = validation_date
        self._effective_date: str | None = None
        self._revisions_used: dict[str, PolicyRevision] = {}
        self._warnings: list[str] = []

        # Set effective date
        if validation_date:
            self._effective_date = validation_date
        else:
            self._effective_date = date.today().isoformat()
            self._warnings.append(
                "No validation date provided - using current date for policy queries"
            )

        logger.info(
            "PolicyComparer initialized",
            validation_date=validation_date,
            effective_date=self._effective_date,
        )

    async def compare(
        self,
        assessments: list[AspectAssessment],
    ) -> PolicyComparisonResult:
        """
        Compare assessment results against policy requirements.

        Implements [agent-integration:PolicyComparer/TS-04] - Generate compliance matrix
        Implements [agent-integration:PolicyComparer/TS-06] - Policy not applicable

        Args:
            assessments: List of aspect assessments from ReviewAssessor.

        Returns:
            PolicyComparisonResult with compliance matrix and revision tracking.
        """
        logger.info(
            "Starting policy comparison",
            num_aspects=len(assessments),
            effective_date=self._effective_date,
        )

        result = PolicyComparisonResult(warnings=self._warnings.copy())

        for assessment in assessments:
            # Skip N/A aspects
            if assessment.rating == AspectRating.NOT_APPLICABLE:
                continue

            # Get policy requirements for this aspect
            aspect_compliance = await self._compare_aspect(assessment)
            result.compliance_matrix.extend(aspect_compliance)

        # Add revision tracking
        result.revisions_used = list(self._revisions_used.values())

        logger.info(
            "Policy comparison complete",
            compliance_items=len(result.compliance_matrix),
            revisions_used=len(result.revisions_used),
        )

        return result

    async def _compare_aspect(
        self,
        assessment: AspectAssessment,
    ) -> list[ComplianceItem]:
        """
        Compare a single aspect against relevant policies.

        Implements [agent-integration:PolicyComparer/TS-08] - Multiple sources for requirement
        """
        compliance_items: list[ComplianceItem] = []

        # Get queries for this aspect
        queries = ASPECT_POLICY_QUERIES.get(assessment.name, [])

        for query_text, sources in queries:
            try:
                # Search policy KB with effective date filter
                search_results = await self._search_policy(query_text, sources)

                if not search_results:
                    continue

                # Use the most relevant result
                best_result = search_results[0]

                # Track revision used
                self._track_revision(best_result)

                # Determine compliance based on assessment rating
                compliant = assessment.rating == AspectRating.GREEN

                # Generate compliance item
                compliance_items.append(
                    ComplianceItem(
                        requirement=self._extract_requirement(best_result.text),
                        policy_source=self._format_source(best_result.source),
                        policy_revision=best_result.revision_id,
                        compliant=compliant,
                        notes=self._generate_compliance_notes(assessment),
                        section_ref=best_result.section_ref,
                        evidence_chunk_id=best_result.chunk_id,
                    )
                )

            except MCPToolError as e:
                logger.warning(
                    "Policy search failed",
                    query=query_text,
                    error=str(e),
                )

        # Deduplicate by requirement (keep most specific source)
        return self._deduplicate_compliance(compliance_items)

    async def _search_policy(
        self,
        query: str,
        sources: list[str],
        max_results: int = 5,
    ) -> list[PolicySearchResult]:
        """
        Search the policy knowledge base.

        Implements [agent-integration:PolicyComparer/TS-01] - Query with validation date
        Implements [agent-integration:PolicyComparer/TS-02] - NPPF revision selection
        """
        try:
            response = await self._mcp.call_tool(
                "search_policy",
                {
                    "query": query,
                    "sources": sources,
                    "effective_date": self._effective_date,
                    "n_results": max_results,
                },
            )

            results = []
            for r in response.get("results", []):
                results.append(
                    PolicySearchResult(
                        chunk_id=r.get("chunk_id", r.get("id", "")),
                        text=r.get("text", ""),
                        relevance_score=r.get("relevance_score", 0.0),
                        source=r.get("metadata", {}).get("source", "unknown"),
                        revision_id=r.get("metadata", {}).get("revision_id", "unknown"),
                        section_ref=r.get("metadata", {}).get("section_ref"),
                    )
                )

            # Sort by relevance
            results.sort(key=lambda x: x.relevance_score, reverse=True)
            return results

        except MCPToolError as e:
            logger.warning("Policy search failed", query=query, error=str(e))
            return []

    def _track_revision(self, result: PolicySearchResult) -> None:
        """
        Track a policy revision that was used.

        Implements [agent-integration:PolicyComparer/TS-05] - Track revisions used
        """
        key = f"{result.source}:{result.revision_id}"
        if key not in self._revisions_used:
            self._revisions_used[key] = PolicyRevision(
                source=result.source,
                revision_id=result.revision_id,
                version_label=self._get_version_label(result.revision_id),
            )

    def _get_version_label(self, revision_id: str) -> str:
        """Generate a human-readable version label."""
        # Extract date from revision_id if present (e.g., rev_LTN120_2020_07 -> July 2020)
        if "_" in revision_id:
            parts = revision_id.split("_")
            if len(parts) >= 3:
                try:
                    year = parts[-2]
                    month = parts[-1]
                    month_names = {
                        "01": "January", "02": "February", "03": "March",
                        "04": "April", "05": "May", "06": "June",
                        "07": "July", "08": "August", "09": "September",
                        "10": "October", "11": "November", "12": "December",
                    }
                    month_name = month_names.get(month, month)
                    return f"{month_name} {year}"
                except (ValueError, IndexError):
                    pass
        return revision_id

    def _format_source(self, source: str) -> str:
        """Format a policy source for display."""
        return self.POLICY_SOURCES.get(source, source)

    def _extract_requirement(self, text: str) -> str:
        """Extract a concise requirement statement from policy text."""
        # Take the first sentence or first 150 chars
        text = text.strip()
        if ". " in text:
            first_sentence = text.split(". ")[0] + "."
            if len(first_sentence) <= 200:
                return first_sentence
        if len(text) <= 150:
            return text
        return text[:147] + "..."

    def _generate_compliance_notes(
        self,
        assessment: AspectAssessment,
    ) -> str:
        """Generate notes for a compliance item based on assessment."""
        if assessment.rating == AspectRating.GREEN:
            return f"Application meets requirement. {assessment.key_issue}"
        elif assessment.rating == AspectRating.AMBER:
            return f"Partial compliance. {assessment.key_issue}"
        else:
            return f"Non-compliant. {assessment.key_issue}"

    def _deduplicate_compliance(
        self,
        items: list[ComplianceItem],
    ) -> list[ComplianceItem]:
        """
        Deduplicate compliance items, preferring more specific sources.

        Implements [agent-integration:PolicyComparer/TS-08] - Multiple sources for requirement
        """
        # Group by requirement
        by_requirement: dict[str, list[ComplianceItem]] = {}
        for item in items:
            key = item.requirement[:50]  # Use first 50 chars as key
            if key not in by_requirement:
                by_requirement[key] = []
            by_requirement[key].append(item)

        # For each group, prefer most specific source
        # Priority: LOCAL_PLAN > LTN_1_20 > MANUAL_FOR_STREETS > NPPF
        source_priority = {
            "Cherwell Local Plan": 1,
            "LTN 1/20": 2,
            "Manual for Streets": 3,
            "NPPF": 4,
        }

        result = []
        for items_group in by_requirement.values():
            items_group.sort(
                key=lambda x: source_priority.get(x.policy_source, 99)
            )
            result.append(items_group[0])

        return result

    async def verify_citation(
        self,
        source: str,
        section_ref: str,
        expected_content: str,
    ) -> bool:
        """
        Verify that a citation corresponds to actual policy content.

        Implements [agent-integration:PolicyComparer/TS-07] - Citation verification
        """
        try:
            response = await self._mcp.call_tool(
                "get_policy_section",
                {
                    "source": source,
                    "section_ref": section_ref,
                },
            )

            actual_text = response.get("text", "")
            # Check if expected content appears in actual text
            # Use a simple substring check for now
            return expected_content.lower()[:50] in actual_text.lower()

        except MCPToolError as e:
            logger.warning(
                "Citation verification failed",
                source=source,
                section_ref=section_ref,
                error=str(e),
            )
            return False
