"""
Document filtering for Cherwell planning applications.

Implements [document-filtering:FR-001] - Filter public comments
Implements [document-filtering:FR-002] - Download core application documents
Implements [document-filtering:FR-003] - Download technical assessments
Implements [document-filtering:FR-004] - Download officer/decision documents
Implements [document-filtering:NFR-001] - Filter performance (<10ms per doc)
Implements [document-filtering:NFR-002] - Fail-safe defaults for unknown types
Implements [document-filtering:NFR-004] - Centralized filter rules
Implements [review-output-fixes:FR-002] - Category-based filtering

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
- [review-output-fixes:DocumentFilter/TS-01] Category allowlist hit
- [review-output-fixes:DocumentFilter/TS-02] Category denylist hit
- [review-output-fixes:DocumentFilter/TS-03] Category denylist override
- [review-output-fixes:DocumentFilter/TS-04] No category fallback
- [review-output-fixes:DocumentFilter/TS-05] Unknown category fallback
- [review-output-fixes:DocumentFilter/TS-06] Comment category denied
"""

from dataclasses import dataclass

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
    1. Check portal category (if document_type matches a known category) → immediate decision
    2. Fall through to title-based pattern matching if category not recognised
    3. Default to ALLOW if no match (fail-safe)

    All pattern matching is case-insensitive and uses substring matching
    to handle variations in Cherwell portal naming.
    """

    # Implements [review-output-fixes:FR-002] - Portal category allowlist
    # Implements [reliable-category-filtering:FR-002] - Match all portal categories
    # These are Cherwell portal section headers that group documents.
    # Documents under these categories are always allowed regardless of title.
    CATEGORY_ALLOWLIST = [
        "application forms",
        "supporting documents",
        "site plans",
        "proposed plans",
        "officer/committee consideration",
        "decision and legal agreements",
        "planning application documents",
    ]

    # Implements [review-output-fixes:FR-002] - Portal category denylist (consultation)
    # Implements [reliable-category-filtering:FR-002] - "Consultee Responses" variant
    # Documents under these categories are denied unless toggled on.
    CATEGORY_DENYLIST_CONSULTATION = [
        "consultation responses",
        "consultee responses",
    ]

    # Implements [review-output-fixes:FR-002] - Portal category denylist (public comments)
    # Documents under these categories are denied unless toggled on.
    CATEGORY_DENYLIST_PUBLIC = [
        "public comments",
    ]

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
    # Transport-relevant specialist reports only
    ALLOWLIST_ASSESSMENT_PATTERNS = [
        "transport assessment",
        "transport statement",
        "travel plan",
        "highway",
        "parking",
    ]

    # Non-transport technical documents - explicitly filtered to reduce noise
    DENYLIST_NON_TRANSPORT_PATTERNS = [
        "ecology",
        "ecological",
        "biodiversity",
        "arboricultural",
        "tree survey",
        "heritage statement",
        "heritage impact",
        "archaeological",
        "flood risk",
        "drainage",
        "noise",
        "acoustic",
        "air quality",
        "energy statement",
        "sustainability",
        "landscape",
        "visual impact",
        "ground condition",
        "contamination",
        "geotechnical",
    ]

    # Documents under superseded sections or with "superseded" in the title
    # are always excluded — no toggle override.
    CATEGORY_DENYLIST_SUPERSEDED = [
        "superseded documents",
        "superseded",
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

    # Implements [review-scope-control:FR-001] - Consultation responses
    # Documents submitted by statutory consultees (Highway Authority, Environment Agency, etc.)
    # Checked BEFORE allowlist to prevent false matches (e.g. "highway" in "OCC Highways")
    DENYLIST_CONSULTATION_RESPONSE_PATTERNS = [
        "consultation response",
        "consultee response",
        "statutory consultee",
    ]

    # Implements [document-filtering:FR-001] - Public comments
    # Documents submitted by members of the public - not relevant for policy review
    # Checked BEFORE allowlist to prevent false matches
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
        include_consultation_responses: bool = False,
        include_public_comments: bool = False,
    ) -> tuple[list[DocumentInfo], list[FilteredDocumentInfo]]:
        """
        Filter documents based on type, separating relevant from irrelevant.

        Implements [document-filtering:FR-005] - Override filter with skip_filter flag
        Implements [document-filtering:FR-006] - Return filtered documents with reasons
        Implements [document-filtering:NFR-003] - Log all filter decisions
        Implements [review-scope-control:FR-003] - Per-review filter override toggles

        Args:
            documents: List of documents to filter
            skip_filter: If True, bypass filtering and allow all documents
            application_ref: Application reference for logging context
            include_consultation_responses: If True, skip consultation response denylist
            include_public_comments: If True, skip public comment denylist

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
            should_download, reason = self._should_download(
                doc.document_type,
                doc.description,
                include_consultation_responses=include_consultation_responses,
                include_public_comments=include_public_comments,
            )

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

    def _should_download(
        self,
        document_type: str | None,
        description: str | None = None,
        include_consultation_responses: bool = False,
        include_public_comments: bool = False,
    ) -> tuple[bool, str]:
        """
        Determine if a document should be downloaded based on its type and description.

        Implements [document-filtering:NFR-002] - Fail-safe defaults
        Implements [document-filtering:DocumentFilter/TS-07] - Unknown type defaults to allow
        Implements [document-filtering:DocumentFilter/TS-08] - Case insensitive matching
        Implements [document-filtering:DocumentFilter/TS-09] - Partial pattern matching
        Implements [review-scope-control:FR-001] - Consultation response toggle
        Implements [review-scope-control:FR-002] - Public comment toggle

        Strategy:
        1. If no document_type and no description → ALLOW (fail-safe)
        2. Check portal category (if document_type is a known category) → immediate decision
        3. Fall through to title-based logic if category not recognised
        4. Check consultation response denylist (unless toggled on) → DENY
        5. Check public comment denylist (unless toggled on) → DENY
        6. Check allowlist (type + description) → ALLOW with reason
        7. Check non-transport denylist → DENY with reason
        8. Default → ALLOW (fail-safe for unknown types)

        Portal category matching is checked first because it uses the
        portal's own document grouping, which is more reliable than
        title-based pattern matching.

        Args:
            document_type: Document type/category from Cherwell portal
            description: Document description/title from Cherwell portal
            include_consultation_responses: If True, skip consultation response denylist
            include_public_comments: If True, skip public comment denylist

        Returns:
            Tuple of (should_download, reason)
        """
        # Build list of text fields to match against
        match_texts: list[str] = []
        if document_type:
            match_texts.append(document_type.lower())
        if description:
            match_texts.append(description.lower())

        # Implements [document-filtering:DocumentFilter/TS-07] - Unknown type defaults to allow
        # Fail-safe: if no type or description provided, allow the document
        if not match_texts:
            return (True, "No document type - allowed by default (fail-safe)")

        # Superseded check — always denied, no toggle, checked before everything else
        # Documents under superseded sections or with "superseded" in the title
        # are always excluded regardless of category or allowlist matches.
        if document_type:
            category_lower = document_type.lower()

            # Category denylist — superseded documents (always denied, no toggle)
            for category in self.CATEGORY_DENYLIST_SUPERSEDED:
                if category_lower == category:
                    return (False, "Superseded document - excluded from review")

        # Check for "superseded" in title (always denied, no toggle)
        for text in match_texts:
            if "superseded" in text:
                return (False, "Superseded document - excluded from review")

        # Implements [review-output-fixes:FR-002] - Category-based filtering
        # Check portal category — these are section headers from the
        # Cherwell portal document table, more reliable than title matching.
        if document_type:
            category_lower = document_type.lower()

            # Category allowlist — documents under these headers are always relevant
            for category in self.CATEGORY_ALLOWLIST:
                if category_lower == category:
                    return (True, f"Portal category: {document_type}")

            # Category denylist — consultation responses
            if not include_consultation_responses:
                for category in self.CATEGORY_DENYLIST_CONSULTATION:
                    if category_lower == category:
                        return (False, "Portal category: consultation responses - not included in review scope")

            # Category denylist — public comments
            if not include_public_comments:
                for category in self.CATEGORY_DENYLIST_PUBLIC:
                    if category_lower == category:
                        return (False, "Portal category: public comments - not relevant for policy review")

        # Fall through to title-based pattern matching for unrecognised categories
        # or when document_type is None

        # Implements [review-scope-control:FR-001] - Check consultation response denylist
        # Checked BEFORE allowlist to prevent "highway" in "OCC Highways" matching allowlist
        if not include_consultation_responses:
            for pattern in self.DENYLIST_CONSULTATION_RESPONSE_PATTERNS:
                for text in match_texts:
                    if pattern in text:
                        return (False, "Consultation response - not included in review scope")

        # Implements [review-scope-control:FR-002] - Check public comment denylist
        # Checked BEFORE allowlist to prevent false matches
        # Implements [document-filtering:DocumentFilter/TS-04] - Public comments filtered
        # Implements [document-filtering:DocumentFilter/TS-05] - Objection letters filtered
        # Implements [document-filtering:DocumentFilter/TS-06] - Representations filtered
        if not include_public_comments:
            for pattern in self.DENYLIST_PUBLIC_COMMENT_PATTERNS:
                for text in match_texts:
                    if pattern in text:
                        return (False, "Public comment - not relevant for policy review")

        # Check allowlist
        for pattern in self._allowlist_patterns:
            # Implements [document-filtering:DocumentFilter/TS-08] - Case insensitive
            # Implements [document-filtering:DocumentFilter/TS-09] - Partial pattern matching
            for text in match_texts:
                if pattern in text:
                    # Determine category for clearer reason
                    if pattern in self.ALLOWLIST_CORE_PATTERNS:
                        return (True, "Core application document")
                    elif pattern in self.ALLOWLIST_ASSESSMENT_PATTERNS:
                        return (True, "Technical assessment document")
                    elif pattern in self.ALLOWLIST_OFFICER_PATTERNS:
                        return (True, "Officer/decision document")

        # Check denylist (non-transport technical documents)
        for pattern in self.DENYLIST_NON_TRANSPORT_PATTERNS:
            for text in match_texts:
                if pattern in text:
                    return (False, "Non-transport technical document - not relevant for cycling review")

        # Default to allowing (fail-safe for unknown document types)
        # Implements [document-filtering:NFR-002] - Fail-safe reliability
        return (True, "Unknown document type - allowed by default (fail-safe)")
