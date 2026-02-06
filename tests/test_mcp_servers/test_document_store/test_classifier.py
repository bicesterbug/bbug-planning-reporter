"""
Tests for DocumentClassifier.

Implements test scenarios from [document-processing:DocumentClassifier/TS-01] through [TS-06]
"""

import pytest

from src.mcp_servers.document_store.classifier import (
    ClassificationResult,
    DocumentClassifier,
    DocumentType,
)


@pytest.fixture
def classifier() -> DocumentClassifier:
    """Create a DocumentClassifier instance."""
    return DocumentClassifier()


class TestClassifyByFilename:
    """Tests for filename-based classification."""

    def test_classify_transport_assessment(self, classifier: DocumentClassifier) -> None:
        """
        Verifies [document-processing:DocumentClassifier/TS-01]

        Given: "Transport_Assessment_v2.pdf"
        When: Call classify()
        Then: Returns "transport_assessment"
        """
        result = classifier.classify("Transport_Assessment_v2.pdf")

        assert result.document_type == DocumentType.TRANSPORT_ASSESSMENT
        assert result.confidence == "high"
        assert result.method == "filename"

    def test_classify_design_access_statement(self, classifier: DocumentClassifier) -> None:
        """
        Verifies [document-processing:DocumentClassifier/TS-02]

        Given: "Design_Access_Statement.pdf"
        When: Call classify()
        Then: Returns "design_access_statement"
        """
        result = classifier.classify("Design_Access_Statement.pdf")

        assert result.document_type == DocumentType.DESIGN_ACCESS_STATEMENT
        assert result.confidence == "high"
        assert result.method == "filename"

    def test_classify_site_plan(self, classifier: DocumentClassifier) -> None:
        """
        Verifies [document-processing:DocumentClassifier/TS-03]

        Given: "Site_Plan_Drawing.pdf"
        When: Call classify()
        Then: Returns "site_plan"
        """
        result = classifier.classify("Site_Plan_Drawing.pdf")

        assert result.document_type == DocumentType.SITE_PLAN
        assert result.confidence == "high"
        assert result.method == "filename"

    def test_classify_case_insensitive(self, classifier: DocumentClassifier) -> None:
        """
        Verifies [document-processing:DocumentClassifier/TS-06]

        Given: "TRANSPORT_assessment.PDF"
        When: Call classify()
        Then: Returns "transport_assessment"
        """
        result = classifier.classify("TRANSPORT_assessment.PDF")

        assert result.document_type == DocumentType.TRANSPORT_ASSESSMENT
        assert result.confidence == "high"

    def test_classify_travel_plan(self, classifier: DocumentClassifier) -> None:
        """Test travel plan classification."""
        result = classifier.classify("Travel_Plan.pdf")

        assert result.document_type == DocumentType.TRANSPORT_ASSESSMENT

    def test_classify_flood_risk(self, classifier: DocumentClassifier) -> None:
        """Test flood risk assessment classification."""
        result = classifier.classify("Flood_Risk_Assessment.pdf")

        assert result.document_type == DocumentType.FLOOD_RISK_ASSESSMENT

    def test_classify_ecology_report(self, classifier: DocumentClassifier) -> None:
        """Test ecology report classification."""
        result = classifier.classify("Ecological_Appraisal.pdf")

        assert result.document_type == DocumentType.ECOLOGY_REPORT

    def test_classify_noise_assessment(self, classifier: DocumentClassifier) -> None:
        """Test noise assessment classification."""
        result = classifier.classify("Noise_Impact_Assessment.pdf")

        assert result.document_type == DocumentType.NOISE_ASSESSMENT

    def test_classify_heritage_statement(self, classifier: DocumentClassifier) -> None:
        """Test heritage statement classification."""
        result = classifier.classify("Heritage_Statement.pdf")

        assert result.document_type == DocumentType.HERITAGE_STATEMENT

    def test_classify_arboricultural_report(self, classifier: DocumentClassifier) -> None:
        """Test arboricultural report classification."""
        result = classifier.classify("Tree_Survey.pdf")

        assert result.document_type == DocumentType.ARBORICULTURAL_REPORT

    def test_classify_floor_plan(self, classifier: DocumentClassifier) -> None:
        """Test floor plan classification."""
        result = classifier.classify("Ground_Floor_Plan.pdf")

        assert result.document_type == DocumentType.FLOOR_PLAN

    def test_classify_elevation(self, classifier: DocumentClassifier) -> None:
        """Test elevation classification."""
        result = classifier.classify("Front_Elevation.pdf")

        assert result.document_type == DocumentType.ELEVATION


class TestClassifyByContent:
    """Tests for content-based classification."""

    def test_classify_by_content_transport(self, classifier: DocumentClassifier) -> None:
        """
        Verifies [document-processing:DocumentClassifier/TS-04]

        Given: Generic filename, contains "trip generation"
        When: Call classify() with text
        Then: Returns "transport_assessment"
        """
        content = """
        This document provides an assessment of the transport impacts.
        Trip generation has been calculated using TRICS data.
        The development will provide adequate cycle parking facilities.
        """
        result = classifier.classify("Document_001.pdf", content=content)

        assert result.document_type == DocumentType.TRANSPORT_ASSESSMENT
        assert result.confidence == "medium"
        assert result.method == "content"

    def test_classify_by_content_flood_risk(self, classifier: DocumentClassifier) -> None:
        """Test content-based flood risk classification."""
        content = """
        The site is located within Flood Zone 1.
        Surface water drainage will be managed through SuDS.
        Attenuation will be provided on site.
        """
        result = classifier.classify("Report.pdf", content=content)

        assert result.document_type == DocumentType.FLOOD_RISK_ASSESSMENT

    def test_classify_by_content_ecology(self, classifier: DocumentClassifier) -> None:
        """Test content-based ecology classification."""
        content = """
        A habitat survey was conducted on the site.
        No protected species were found during the surveys.
        Biodiversity net gain will be achieved through new planting.
        """
        result = classifier.classify("Survey.pdf", content=content)

        assert result.document_type == DocumentType.ECOLOGY_REPORT

    def test_classify_by_content_planning(self, classifier: DocumentClassifier) -> None:
        """Test content-based planning statement classification."""
        content = """
        This application accords with planning policy.
        The Local Plan identifies the site for development.
        The NPPF supports sustainable development.
        """
        result = classifier.classify("Statement.pdf", content=content)

        assert result.document_type == DocumentType.PLANNING_STATEMENT


class TestClassifyFallback:
    """Tests for fallback classification."""

    def test_fallback_to_other(self, classifier: DocumentClassifier) -> None:
        """
        Verifies [document-processing:DocumentClassifier/TS-05]

        Given: Unrecognised filename and content
        When: Call classify()
        Then: Returns "other"
        """
        result = classifier.classify("random_document_xyz.pdf")

        assert result.document_type == DocumentType.OTHER
        assert result.confidence == "low"
        assert result.method == "fallback"

    def test_fallback_with_unrelated_content(self, classifier: DocumentClassifier) -> None:
        """Test fallback with unrelated content."""
        content = "This is just some random text that doesn't match any patterns."
        result = classifier.classify("file.pdf", content=content)

        assert result.document_type == DocumentType.OTHER
        assert result.confidence == "low"


class TestClassificationResult:
    """Tests for ClassificationResult dataclass."""

    def test_classification_result_fields(self) -> None:
        """Test ClassificationResult has expected fields."""
        result = ClassificationResult(
            document_type="transport_assessment",
            confidence="high",
            method="filename",
            matched_pattern="transport.*assessment",
        )

        assert result.document_type == "transport_assessment"
        assert result.confidence == "high"
        assert result.method == "filename"
        assert result.matched_pattern == "transport.*assessment"

    def test_classification_result_defaults(self) -> None:
        """Test ClassificationResult default values."""
        result = ClassificationResult(
            document_type="other",
            confidence="low",
            method="fallback",
        )

        assert result.matched_pattern is None


class TestDocumentTypes:
    """Tests for DocumentType enum and helpers."""

    def test_get_document_types(self) -> None:
        """Test getting list of document types."""
        types = DocumentClassifier.get_document_types()

        assert "transport_assessment" in types
        assert "design_access_statement" in types
        assert "site_plan" in types
        assert "other" in types

    def test_document_type_values(self) -> None:
        """Test DocumentType enum values."""
        assert DocumentType.TRANSPORT_ASSESSMENT == "transport_assessment"
        assert DocumentType.DESIGN_ACCESS_STATEMENT == "design_access_statement"
        assert DocumentType.SITE_PLAN == "site_plan"
        assert DocumentType.OTHER == "other"


class TestVariousFilenameFormats:
    """Tests for various filename format variations."""

    @pytest.mark.parametrize(
        "filename,expected_type",
        [
            ("Transport Assessment.pdf", DocumentType.TRANSPORT_ASSESSMENT),
            ("transport-assessment.pdf", DocumentType.TRANSPORT_ASSESSMENT),
            ("Transport_Assessment_Rev_A.pdf", DocumentType.TRANSPORT_ASSESSMENT),
            ("Design and Access Statement.pdf", DocumentType.DESIGN_ACCESS_STATEMENT),
            ("D&A Statement.pdf", DocumentType.DESIGN_ACCESS_STATEMENT),
            ("Site Plan - Proposed.pdf", DocumentType.SITE_PLAN),
            ("Location Plan.pdf", DocumentType.SITE_PLAN),
            ("Block Plan.pdf", DocumentType.SITE_PLAN),
            ("FRA.pdf", DocumentType.FLOOD_RISK_ASSESSMENT),
            ("Drainage Strategy.pdf", DocumentType.FLOOD_RISK_ASSESSMENT),
            ("Bat Survey.pdf", DocumentType.ECOLOGY_REPORT),
            ("Acoustic Report.pdf", DocumentType.NOISE_ASSESSMENT),
            ("Archaeological Assessment.pdf", DocumentType.HERITAGE_STATEMENT),
        ],
    )
    def test_filename_variations(
        self, classifier: DocumentClassifier, filename: str, expected_type: str
    ) -> None:
        """Test various filename format variations."""
        result = classifier.classify(filename)
        assert result.document_type == expected_type
