"""
Document classifier for planning application documents.

Implements [document-processing:FR-010] - Classify documents based on filename and content
"""

import re
from dataclasses import dataclass
from enum import StrEnum

import structlog

logger = structlog.get_logger(__name__)


class DocumentType(StrEnum):
    """Known document types for planning applications."""

    TRANSPORT_ASSESSMENT = "transport_assessment"
    DESIGN_ACCESS_STATEMENT = "design_access_statement"
    SITE_PLAN = "site_plan"
    FLOOR_PLAN = "floor_plan"
    ELEVATION = "elevation"
    PLANNING_STATEMENT = "planning_statement"
    ENVIRONMENTAL_STATEMENT = "environmental_statement"
    NOISE_ASSESSMENT = "noise_assessment"
    FLOOD_RISK_ASSESSMENT = "flood_risk_assessment"
    ECOLOGY_REPORT = "ecology_report"
    HERITAGE_STATEMENT = "heritage_statement"
    ARBORICULTURAL_REPORT = "arboricultural_report"
    OTHER = "other"


@dataclass
class ClassificationResult:
    """Result of document classification."""

    document_type: str
    confidence: str  # "high", "medium", "low"
    method: str  # "filename", "content", "fallback"
    matched_pattern: str | None = None


# Filename patterns for each document type
# Format: (pattern, document_type)
FILENAME_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Transport documents
    (re.compile(r"transport[_\s-]*(assessment|statement)", re.IGNORECASE), DocumentType.TRANSPORT_ASSESSMENT),
    (re.compile(r"travel[_\s-]*plan", re.IGNORECASE), DocumentType.TRANSPORT_ASSESSMENT),
    (re.compile(r"traffic[_\s-]*(impact|assessment)", re.IGNORECASE), DocumentType.TRANSPORT_ASSESSMENT),
    (re.compile(r"highways?[_\s-]*(statement|assessment)", re.IGNORECASE), DocumentType.TRANSPORT_ASSESSMENT),

    # Design and access
    (re.compile(r"design[_\s-]*(and[_\s-]*)?access[_\s-]*statement", re.IGNORECASE), DocumentType.DESIGN_ACCESS_STATEMENT),
    (re.compile(r"d[_\s-]*&?[_\s-]*a[_\s-]*statement", re.IGNORECASE), DocumentType.DESIGN_ACCESS_STATEMENT),

    # Site plans
    (re.compile(r"site[_\s-]*plan", re.IGNORECASE), DocumentType.SITE_PLAN),
    (re.compile(r"location[_\s-]*plan", re.IGNORECASE), DocumentType.SITE_PLAN),
    (re.compile(r"block[_\s-]*plan", re.IGNORECASE), DocumentType.SITE_PLAN),
    (re.compile(r"layout[_\s-]*plan", re.IGNORECASE), DocumentType.SITE_PLAN),

    # Floor plans
    (re.compile(r"floor[_\s-]*plan", re.IGNORECASE), DocumentType.FLOOR_PLAN),
    (re.compile(r"ground[_\s-]*floor", re.IGNORECASE), DocumentType.FLOOR_PLAN),
    (re.compile(r"first[_\s-]*floor", re.IGNORECASE), DocumentType.FLOOR_PLAN),

    # Elevations
    (re.compile(r"elevation", re.IGNORECASE), DocumentType.ELEVATION),
    (re.compile(r"street[_\s-]*scene", re.IGNORECASE), DocumentType.ELEVATION),

    # Planning statement
    (re.compile(r"planning[_\s-]*statement", re.IGNORECASE), DocumentType.PLANNING_STATEMENT),
    (re.compile(r"supporting[_\s-]*statement", re.IGNORECASE), DocumentType.PLANNING_STATEMENT),

    # Environmental
    (re.compile(r"environmental[_\s-]*(impact[_\s-]*)?(statement|assessment)", re.IGNORECASE), DocumentType.ENVIRONMENTAL_STATEMENT),
    (re.compile(r"e[_\s-]*i[_\s-]*a", re.IGNORECASE), DocumentType.ENVIRONMENTAL_STATEMENT),

    # Noise
    (re.compile(r"noise[_\s-]*(impact[_\s-]*)?(assessment|report|survey)", re.IGNORECASE), DocumentType.NOISE_ASSESSMENT),
    (re.compile(r"acoustic", re.IGNORECASE), DocumentType.NOISE_ASSESSMENT),

    # Flood risk
    (re.compile(r"flood[_\s-]*risk[_\s-]*(assessment)?", re.IGNORECASE), DocumentType.FLOOD_RISK_ASSESSMENT),
    (re.compile(r"fra", re.IGNORECASE), DocumentType.FLOOD_RISK_ASSESSMENT),
    (re.compile(r"drainage[_\s-]*strategy", re.IGNORECASE), DocumentType.FLOOD_RISK_ASSESSMENT),

    # Ecology
    (re.compile(r"ecology[_\s-]*(report|survey|assessment)", re.IGNORECASE), DocumentType.ECOLOGY_REPORT),
    (re.compile(r"ecological[_\s-]*(appraisal|assessment)", re.IGNORECASE), DocumentType.ECOLOGY_REPORT),
    (re.compile(r"bat[_\s-]*survey", re.IGNORECASE), DocumentType.ECOLOGY_REPORT),
    (re.compile(r"biodiversity", re.IGNORECASE), DocumentType.ECOLOGY_REPORT),

    # Heritage
    (re.compile(r"heritage[_\s-]*(statement|assessment)", re.IGNORECASE), DocumentType.HERITAGE_STATEMENT),
    (re.compile(r"archaeological", re.IGNORECASE), DocumentType.HERITAGE_STATEMENT),

    # Arboricultural
    (re.compile(r"arboricultural[_\s-]*(report|survey|assessment)", re.IGNORECASE), DocumentType.ARBORICULTURAL_REPORT),
    (re.compile(r"tree[_\s-]*survey", re.IGNORECASE), DocumentType.ARBORICULTURAL_REPORT),
]

# Content keywords for classification fallback
# Format: (keywords, document_type, minimum_matches)
CONTENT_PATTERNS: list[tuple[list[str], str, int]] = [
    # Transport assessment keywords
    (
        ["trip generation", "traffic flow", "junction capacity", "parking provision",
         "cycle parking", "pedestrian", "highway network", "vehicle movements",
         "transport assessment", "travel plan", "modal split", "trics"],
        DocumentType.TRANSPORT_ASSESSMENT,
        2,
    ),

    # Design and access statement keywords
    (
        ["design principles", "access arrangements", "inclusive design",
         "character and appearance", "scale and massing", "visual impact",
         "design and access", "layout and design"],
        DocumentType.DESIGN_ACCESS_STATEMENT,
        2,
    ),

    # Planning statement keywords
    (
        ["planning policy", "local plan", "nppf", "national planning policy",
         "material considerations", "planning balance", "development plan"],
        DocumentType.PLANNING_STATEMENT,
        2,
    ),

    # Flood risk keywords
    (
        ["flood zone", "surface water", "drainage", "suds", "attenuation",
         "flood risk", "sequential test", "exception test"],
        DocumentType.FLOOD_RISK_ASSESSMENT,
        2,
    ),

    # Ecology keywords
    (
        ["protected species", "habitat", "biodiversity net gain", "bat survey",
         "great crested newt", "breeding birds", "ecological appraisal"],
        DocumentType.ECOLOGY_REPORT,
        2,
    ),

    # Noise assessment keywords
    (
        ["noise level", "decibel", "acoustic", "sound insulation",
         "noise sensitive", "background noise", "noise impact"],
        DocumentType.NOISE_ASSESSMENT,
        2,
    ),

    # Heritage keywords
    (
        ["listed building", "conservation area", "heritage asset", "significance",
         "archaeological", "historic environment", "setting"],
        DocumentType.HERITAGE_STATEMENT,
        2,
    ),
]


class DocumentClassifier:
    """
    Classifies planning application documents by type.

    Implements [document-processing:DocumentClassifier/TS-01] through [TS-06]

    Classification strategy:
    1. First attempts filename pattern matching (high confidence)
    2. Falls back to content keyword analysis (medium confidence)
    3. Returns 'other' if no match found (low confidence)
    """

    def classify(
        self,
        filename: str,
        content: str | None = None,
    ) -> ClassificationResult:
        """
        Classify a document based on filename and optionally content.

        Args:
            filename: The document filename (e.g., "Transport_Assessment_v2.pdf")
            content: Optional document text content for fallback classification

        Returns:
            ClassificationResult with document type, confidence, and method
        """
        # Try filename classification first
        result = self._classify_by_filename(filename)
        if result.document_type != DocumentType.OTHER:
            logger.debug(
                "Document classified by filename",
                filename=filename,
                document_type=result.document_type,
                pattern=result.matched_pattern,
            )
            return result

        # Try content classification if content provided
        if content:
            result = self._classify_by_content(content)
            if result.document_type != DocumentType.OTHER:
                logger.debug(
                    "Document classified by content",
                    filename=filename,
                    document_type=result.document_type,
                )
                return result

        # Fallback to 'other'
        logger.debug(
            "Document classification fallback to other",
            filename=filename,
        )
        return ClassificationResult(
            document_type=DocumentType.OTHER,
            confidence="low",
            method="fallback",
        )

    def _classify_by_filename(self, filename: str) -> ClassificationResult:
        """Classify based on filename patterns."""
        for pattern, doc_type in FILENAME_PATTERNS:
            if pattern.search(filename):
                return ClassificationResult(
                    document_type=doc_type,
                    confidence="high",
                    method="filename",
                    matched_pattern=pattern.pattern,
                )

        return ClassificationResult(
            document_type=DocumentType.OTHER,
            confidence="low",
            method="filename",
        )

    def _classify_by_content(self, content: str) -> ClassificationResult:
        """Classify based on content keyword analysis."""
        content_lower = content.lower()

        best_match: tuple[str, int] | None = None
        best_score = 0

        for keywords, doc_type, min_matches in CONTENT_PATTERNS:
            matches = sum(1 for kw in keywords if kw.lower() in content_lower)
            if matches >= min_matches and matches > best_score:
                best_match = (doc_type, matches)
                best_score = matches

        if best_match:
            return ClassificationResult(
                document_type=best_match[0],
                confidence="medium",
                method="content",
                matched_pattern=f"{best_score} keyword matches",
            )

        return ClassificationResult(
            document_type=DocumentType.OTHER,
            confidence="low",
            method="content",
        )

    @staticmethod
    def get_document_types() -> list[str]:
        """Get list of all known document types."""
        return [dt.value for dt in DocumentType]
