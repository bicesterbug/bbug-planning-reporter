"""
Document filtering for Cherwell planning applications.

Implements [document-filtering:FR-001] - Filter public comments
Implements [document-filtering:FR-002] - Download core application documents
Implements [document-filtering:FR-003] - Download technical assessments
Implements [document-filtering:FR-004] - Download officer/decision documents
Implements [document-filtering:NFR-001] - Filter performance (<10ms per doc)
Implements [document-filtering:NFR-002] - Fail-safe defaults for unknown types
Implements [document-filtering:NFR-004] - Centralized filter rules

Implements:
- [document-filtering:DocumentFilter/TS-01] Core documents allowed
- [document-filtering:DocumentFilter/TS-02] Technical assessments allowed
- [document-filtering:DocumentFilter/TS-03] Officer reports allowed
- [document-filtering:DocumentFilter/TS-04] Public comments filtered
- [document-filtering:DocumentFilter/TS-05] Objection letters filtered
- [document-filtering:DocumentFilter/TS-06] Representations filtered
- [document-filtering:DocumentFilter/TS-07] Unknown type defaults to allow
- [document-filtering:DocumentFilter/TS-08] Case insensitive matching
- [document-filtering:DocumentFilter/TS-09] Partial pattern matching
- [document-filtering:DocumentFilter/TS-10] Skip filter override
"""

from dataclasses import dataclass
from datetime import date

import structlog

from src.mcp_servers.cherwell_scraper.models import DocumentInfo

logger = structlog.get_logger(__name__)


@dataclass
class FilteredDocumentInfo:
    """
    Information about a document that was filtered out.

    Implements [document-filtering:FR-006] - Report filtered documents

    Implements:
    - [document-filtering:FilteredDocumentInfo/TS-01] Convert to dict
    - [document-filtering:FilteredDocumentInfo/TS-02] None values handled
    """

    document_id: str
    """Unique identifier for the document"""

    description: str
    """Document description/title"""

    document_type: str | None = None
    """Type/category of document"""

    filter_reason: str = ""
    """Reason why document was filtered"""

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            "document_id": self.document_id,
            "description": self.description,
            "document_type": self.document_type,
            "filter_reason": self.filter_reason,
        }


class DocumentFilter:
    """
    Filters planning application documents based on type/category.

    Implements [document-filtering:FR-001] - Filter public comments by document type
    Implements [document-filtering:FR-002] - Download core application documents
    Implements [document-filtering:FR-003] - Download technical assessments
    Implements [document-filtering:FR-004] - Download officer/decision documents
    Implements [document-filtering:NFR-002] - Fail-safe defaults
    Implements [document-filtering:NFR-004] - Centralized filter rules

    Filter Strategy:
    1. Check allowlist first (core docs, assessments, officer docs)
    2. Check denylist second (public comments)
    3. Default to ALLOW if no match (fail-safe)

    All pattern matching is case-insensitive and uses substring matching
    to handle variations in Cherwell portal naming.
    """

    # Implements [document-filtering:FR-002] - Core application documents
    # These are essential submission materials and should always be downloaded
    ALLOWLIST_CORE_PATTERNS = [
        "planning statement",
        "design and access",
        "design & access",
        "application form",
        "proposed plan",
        "proposed drawing",
        "site plan",
        "location plan",
        "block plan",
        "elevation",
        "floor plan",
        "section",
    ]

    # Implements [document-filtering:FR-003] - Technical assessment documents
    # Specialist reports analyzing development impacts
    ALLOWLIST_ASSESSMENT_PATTERNS = [
        "transport assessment",
        "transport statement",
        "travel plan",
        "highway",
        "parking",
        "environmental impact",
        "environmental statement",
        "heritage statement",
        "heritage impact",
        "archaeological",
        "flood risk assessment",
        "drainage",
        "ecology",
        "ecological",
        "biodiversity",
        "arboricultural",
        "tree survey",
        "noise assessment",
        "air quality",
        "energy statement",
        "sustainability",
    ]

    # Implements [document-filtering:FR-004] - Officer and decision documents
    # Council-produced documents for decision-making
    ALLOWLIST_OFFICER_PATTERNS = [
        "officer report",
        "officer's report",
        "planning officer",
        "case officer",
        "committee report",
        "delegated report",
        "decision notice",
        "decision letter",
        "planning condition",
        "conditions document",
        "approval notice",
        "refusal notice",
        "s106",
        "section 106",
        "legal agreement",
    ]

    # Implements [document-filtering:FR-001] - Public comments
    # Documents submitted by members of the public - not relevant for policy review
    DENYLIST_PUBLIC_COMMENT_PATTERNS = [
        "public comment",
        "comment from",
        "objection",
        "representation from",
        "letter from resident",
        "letter from neighbour",
        "letter of objection",
        "letter of support",
        "petition",
        "consultation response",  # Cherwell portal's label for public comments
    ]

    def __init__(self) -> None:
        """Initialize the document filter."""
        # Combine all allowlist patterns for easier searching
        self._allowlist_patterns = (
            self.ALLOWLIST_CORE_PATTERNS
            + self.ALLOWLIST_ASSESSMENT_PATTERNS
            + self.ALLOWLIST_OFFICER_PATTERNS
        )

    def filter_documents(
        self,
        documents: list[DocumentInfo],
        skip_filter: bool = False,
        application_ref: str | None = None,
    ) -> tuple[list[DocumentInfo], list[FilteredDocumentInfo]]:
        """
        Filter documents based on type, separating relevant from irrelevant.

        Implements [document-filtering:FR-005] - Override filter with skip_filter flag
        Implements [document-filtering:FR-006] - Return filtered documents with reasons
        Implements [document-filtering:NFR-003] - Log all filter decisions

        Args:
            documents: List of documents to filter
            skip_filter: If True, bypass filtering and allow all documents
            application_ref: Application reference for logging context

        Returns:
            Tuple of (documents_to_download, filtered_documents)
        """
        # Implements [document-filtering:DocumentFilter/TS-10] - Skip filter override
        if skip_filter:
            logger.info(
                "Filter bypassed - downloading all documents",
                application_ref=application_ref,
                total_documents=len(documents),
            )
            return (documents, [])

        allowed_documents: list[DocumentInfo] = []
        filtered_documents: list[FilteredDocumentInfo] = []

        for doc in documents:
            should_download, reason = self._should_download(doc.document_type)

            # Implements [document-filtering:NFR-003] - Log every filter decision
            logger.info(
                "Document filter decision",
                application_ref=application_ref,
                document_id=doc.document_id,
                document_type=doc.document_type,
                description=doc.description[:100] if doc.description else None,
                decision="download" if should_download else "skip",
                filter_reason=reason,
            )

            if should_download:
                allowed_documents.append(doc)
            else:
                filtered_documents.append(
                    FilteredDocumentInfo(
                        document_id=doc.document_id,
                        description=doc.description,
                        document_type=doc.document_type,
                        filter_reason=reason,
                    )
                )

        logger.info(
            "Document filtering complete",
            application_ref=application_ref,
            total_documents=len(documents),
            allowed=len(allowed_documents),
            filtered=len(filtered_documents),
        )

        return (allowed_documents, filtered_documents)

    def _should_download(self, document_type: str | None) -> tuple[bool, str]:
        """
        Determine if a document should be downloaded based on its type.

        Implements [document-filtering:NFR-002] - Fail-safe defaults
        Implements [document-filtering:DocumentFilter/TS-07] - Unknown type defaults to allow
        Implements [document-filtering:DocumentFilter/TS-08] - Case insensitive matching
        Implements [document-filtering:DocumentFilter/TS-09] - Partial pattern matching

        Strategy:
        1. If no document_type → ALLOW (fail-safe)
        2. Check allowlist → ALLOW with reason
        3. Check denylist → DENY with reason
        4. Default → ALLOW (fail-safe for unknown types)

        Args:
            document_type: Document type/category from Cherwell portal

        Returns:
            Tuple of (should_download, reason)
        """
        # Implements [document-filtering:DocumentFilter/TS-07] - Unknown type defaults to allow
        # Fail-safe: if no type provided, allow the document
        if not document_type:
            return (True, "No document type - allowed by default (fail-safe)")

        # Normalize for case-insensitive matching
        doc_type_lower = document_type.lower()

        # Check allowlist first (highest priority)
        for pattern in self._allowlist_patterns:
            # Implements [document-filtering:DocumentFilter/TS-08] - Case insensitive
            # Implements [document-filtering:DocumentFilter/TS-09] - Partial pattern matching
            if pattern in doc_type_lower:
                # Determine category for clearer reason
                if pattern in self.ALLOWLIST_CORE_PATTERNS:
                    return (True, "Core application document")
                elif pattern in self.ALLOWLIST_ASSESSMENT_PATTERNS:
                    return (True, "Technical assessment document")
                elif pattern in self.ALLOWLIST_OFFICER_PATTERNS:
                    return (True, "Officer/decision document")

        # Check denylist (public comments)
        # Implements [document-filtering:DocumentFilter/TS-04] - Public comments filtered
        # Implements [document-filtering:DocumentFilter/TS-05] - Objection letters filtered
        # Implements [document-filtering:DocumentFilter/TS-06] - Representations filtered
        for pattern in self.DENYLIST_PUBLIC_COMMENT_PATTERNS:
            if pattern in doc_type_lower:
                return (False, "Public comment - not relevant for policy review")

        # Default to allowing (fail-safe for unknown document types)
        # Implements [document-filtering:NFR-002] - Fail-safe reliability
        return (True, "Unknown document type - allowed by default (fail-safe)")
