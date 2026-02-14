"""
Tests for ReviewStructure Pydantic model.

Verifies [structured-review-output:ReviewStructure/TS-01] through [TS-05]
Verifies [reliable-structure-extraction:ReviewStructure/TS-01] through [TS-06]
"""

import json

import pytest
from pydantic import ValidationError

from src.agent.review_schema import (
    ComplianceItem,
    ReviewStructure,
)

VALID_STRUCTURE_JSON = {
    "overall_rating": "red",
    "summary": "This application receives a RED rating due to serious deficiencies in cycling provision. There are no off-site cycle connections, no cycle priority at junctions, and the site layout is car-dominated. Key NPPF and LTN 1/20 requirements are not met.",
    "aspects": [
        {
            "name": "Cycle Parking",
            "rating": "amber",
            "key_issue": "Design quality unverified",
            "analysis": "The application provides the minimum number of cycle parking spaces but no details on stand type.",
        },
        {
            "name": "Cycle Routes",
            "rating": "red",
            "key_issue": "No off-site connections",
            "analysis": "There is no provision for connecting the site to existing cycle infrastructure.",
        },
        {
            "name": "Junctions",
            "rating": "red",
            "key_issue": "No cycle priority at junctions",
            "analysis": "Internal junctions are designed exclusively for motor vehicles.",
        },
        {
            "name": "Permeability",
            "rating": "red",
            "key_issue": "Car-only site access",
            "analysis": "The site has no filtered permeability for pedestrians or cyclists.",
        },
        {
            "name": "Policy Compliance",
            "rating": "red",
            "key_issue": "Fails key policy requirements",
            "analysis": "The proposal fails NPPF, LTN 1/20, and LCWIP requirements.",
        },
    ],
    "policy_compliance": [
        {
            "requirement": "Prioritise sustainable transport",
            "policy_source": "NPPF para 115(a)",
            "compliant": False,
            "notes": "Car-based design",
        },
        {
            "requirement": "Safe cycle access",
            "policy_source": "NPPF para 115(b)",
            "compliant": False,
            "notes": None,
        },
    ],
    "recommendations": [
        "Provide segregated cycle track along A41",
        "Install Sheffield stands for cycle parking",
    ],
    "suggested_conditions": [
        "Submit detailed cycle parking design prior to commencement",
    ],
    "key_documents": [
        {
            "title": "Transport Assessment",
            "category": "Transport & Access",
            "summary": "Analyses traffic impacts of the proposed development.",
            "url": "https://example.com/ta.pdf",
        },
        {
            "title": "Design and Access Statement",
            "category": "Design & Layout",
            "summary": "Describes site layout including internal roads.",
            "url": None,
        },
    ],
}


class TestReviewStructureValidJSON:
    """
    Verifies [structured-review-output:ReviewStructure/TS-01] - Valid JSON parses
    """

    def test_valid_json_parses(self):
        """
        Given: JSON string with all required fields
        When: ReviewStructure.model_validate() called
        Then: Model instance created with all fields populated
        """
        structure = ReviewStructure.model_validate(VALID_STRUCTURE_JSON)

        assert structure.overall_rating == "red"
        assert structure.summary.startswith("This application receives a RED rating")
        assert len(structure.aspects) == 5
        assert structure.aspects[0].name == "Cycle Parking"
        assert structure.aspects[0].rating == "amber"
        assert structure.aspects[0].key_issue == "Design quality unverified"
        assert "minimum number" in structure.aspects[0].analysis

        assert len(structure.policy_compliance) == 2
        assert structure.policy_compliance[0].compliant is False
        assert structure.policy_compliance[1].notes is None

        assert len(structure.recommendations) == 2
        assert "segregated cycle track" in structure.recommendations[0]

        assert len(structure.suggested_conditions) == 1

        assert len(structure.key_documents) == 2
        assert structure.key_documents[0].category == "Transport & Access"
        assert structure.key_documents[1].url is None

    def test_valid_json_string_parses(self):
        """
        Given: JSON string
        When: ReviewStructure.model_validate_json() called
        Then: Model parses correctly
        """
        json_str = json.dumps(VALID_STRUCTURE_JSON)
        structure = ReviewStructure.model_validate_json(json_str)

        assert structure.overall_rating == "red"
        assert len(structure.aspects) == 5


class TestReviewStructureMissingField:
    """
    Verifies [structured-review-output:ReviewStructure/TS-02] - Missing required field rejected
    """

    def test_missing_overall_rating(self):
        """
        Given: JSON missing overall_rating
        When: ReviewStructure.model_validate() called
        Then: ValidationError raised
        """
        data = {**VALID_STRUCTURE_JSON}
        del data["overall_rating"]

        with pytest.raises(ValidationError) as exc_info:
            ReviewStructure.model_validate(data)
        assert "overall_rating" in str(exc_info.value)

    def test_missing_summary(self):
        """
        Verifies [review-workflow-redesign:ReviewStructure/TS-02] - Missing summary fails validation

        Given: JSON missing summary field
        When: ReviewStructure.model_validate() called
        Then: ValidationError raised
        """
        data = {**VALID_STRUCTURE_JSON}
        del data["summary"]

        with pytest.raises(ValidationError) as exc_info:
            ReviewStructure.model_validate(data)
        assert "summary" in str(exc_info.value)

    def test_missing_aspects(self):
        """
        Given: JSON missing aspects
        When: ReviewStructure.model_validate() called
        Then: ValidationError raised
        """
        data = {**VALID_STRUCTURE_JSON}
        del data["aspects"]

        with pytest.raises(ValidationError):
            ReviewStructure.model_validate(data)

    def test_missing_aspect_analysis(self):
        """
        Given: An aspect missing the analysis field
        When: ReviewStructure.model_validate() called
        Then: ValidationError raised
        """
        data = json.loads(json.dumps(VALID_STRUCTURE_JSON))
        del data["aspects"][0]["analysis"]

        with pytest.raises(ValidationError) as exc_info:
            ReviewStructure.model_validate(data)
        assert "analysis" in str(exc_info.value)


class TestReviewStructureEmptyArrays:
    """
    Verifies [structured-review-output:ReviewStructure/TS-03] - Empty arrays accepted
    """

    def test_empty_suggested_conditions(self):
        """
        Given: JSON with suggested_conditions: []
        When: ReviewStructure.model_validate() called
        Then: Model valid; suggested_conditions is empty list
        """
        data = json.loads(json.dumps(VALID_STRUCTURE_JSON))
        data["suggested_conditions"] = []

        structure = ReviewStructure.model_validate(data)
        assert structure.suggested_conditions == []
        assert structure.suggested_conditions is not None

    def test_empty_recommendations(self):
        """
        Given: JSON with recommendations: []
        When: ReviewStructure.model_validate() called
        Then: Model valid; recommendations is empty list
        """
        data = json.loads(json.dumps(VALID_STRUCTURE_JSON))
        data["recommendations"] = []

        structure = ReviewStructure.model_validate(data)
        assert structure.recommendations == []


class TestReviewStructureRatingValidation:
    """
    Verifies [structured-review-output:ReviewStructure/TS-04] - Rating validation
    """

    def test_invalid_overall_rating(self):
        """
        Verifies [reliable-structure-extraction:ReviewStructure/TS-03] - Invalid rating rejected

        Given: JSON with overall_rating: "purple"
        When: ReviewStructure.model_validate() called
        Then: ValidationError raised
        """
        data = json.loads(json.dumps(VALID_STRUCTURE_JSON))
        data["overall_rating"] = "purple"

        with pytest.raises(ValidationError):
            ReviewStructure.model_validate(data)

    def test_invalid_aspect_rating(self):
        """
        Given: An aspect with rating: "yellow"
        When: ReviewStructure.model_validate() called
        Then: ValidationError raised
        """
        data = json.loads(json.dumps(VALID_STRUCTURE_JSON))
        data["aspects"][0]["rating"] = "yellow"

        with pytest.raises(ValidationError):
            ReviewStructure.model_validate(data)

    def test_rating_case_insensitive(self):
        """
        Given: Ratings in uppercase
        When: ReviewStructure.model_validate() called
        Then: Ratings lowercased
        """
        data = json.loads(json.dumps(VALID_STRUCTURE_JSON))
        data["overall_rating"] = "RED"
        data["aspects"][0]["rating"] = "AMBER"

        structure = ReviewStructure.model_validate(data)
        assert structure.overall_rating == "red"
        assert structure.aspects[0].rating == "amber"

    def test_invalid_category(self):
        """
        Verifies [reliable-structure-extraction:ReviewStructure/TS-06] - Category via Literal

        Given: A key document with invalid category
        When: ReviewStructure.model_validate() called
        Then: ValidationError raised
        """
        data = json.loads(json.dumps(VALID_STRUCTURE_JSON))
        data["key_documents"][0]["category"] = "Other"

        with pytest.raises(ValidationError):
            ReviewStructure.model_validate(data)


class TestComplianceBooleanCoercion:
    """
    Verifies [structured-review-output:ReviewStructure/TS-05] - Compliance boolean coercion
    """

    def test_string_yes_coerced_to_true(self):
        """
        Given: compliant: "yes"
        When: ComplianceItem created
        Then: compliant is True
        """
        item = ComplianceItem(
            requirement="Test",
            policy_source="Test",
            compliant="yes",
        )
        assert item.compliant is True

    def test_string_no_coerced_to_false(self):
        """
        Given: compliant: "no"
        When: ComplianceItem created
        Then: compliant is False
        """
        item = ComplianceItem(
            requirement="Test",
            policy_source="Test",
            compliant="no",
        )
        assert item.compliant is False

    def test_string_true_coerced(self):
        """
        Given: compliant: "true"
        When: ComplianceItem created
        Then: compliant is True
        """
        item = ComplianceItem(
            requirement="Test",
            policy_source="Test",
            compliant="true",
        )
        assert item.compliant is True

    def test_invalid_string_rejected(self):
        """
        Given: compliant: "maybe"
        When: ComplianceItem created
        Then: ValidationError raised
        """
        with pytest.raises(ValidationError):
            ComplianceItem(
                requirement="Test",
                policy_source="Test",
                compliant="maybe",
            )

    def test_native_boolean_works(self):
        """
        Given: compliant: true (native bool)
        When: ComplianceItem created
        Then: compliant is True
        """
        item = ComplianceItem(
            requirement="Test",
            policy_source="Test",
            compliant=True,
        )
        assert item.compliant is True


class TestLiteralEnumInSchema:
    """
    Verifies [reliable-structure-extraction:ReviewStructure/TS-01] - Literal enum in schema
    """

    def test_schema_contains_rating_enum(self):
        """
        Given: ReviewStructure model
        When: model_json_schema() called
        Then: overall_rating has enum: ["red", "amber", "green"]
        """
        schema = ReviewStructure.model_json_schema()
        rating_prop = schema["properties"]["overall_rating"]
        # May be a $ref — resolve it
        if "$ref" in rating_prop:
            ref_name = rating_prop["$ref"].split("/")[-1]
            rating_prop = schema["$defs"][ref_name]
        assert "enum" in rating_prop
        assert set(rating_prop["enum"]) == {"red", "amber", "green"}

    def test_schema_contains_category_enum(self):
        """
        Given: ReviewStructure model
        When: model_json_schema() called
        Then: KeyDocumentItem.category has enum with 3 categories
        """
        schema = ReviewStructure.model_json_schema()
        # Navigate to KeyDocumentItem definition
        defs = schema.get("$defs", {})
        key_doc_schema = defs.get("KeyDocumentItem", {})
        cat_prop = key_doc_schema.get("properties", {}).get("category", {})
        if "$ref" in cat_prop:
            ref_name = cat_prop["$ref"].split("/")[-1]
            cat_prop = defs[ref_name]
        assert "enum" in cat_prop
        assert set(cat_prop["enum"]) == {
            "Transport & Access",
            "Design & Layout",
            "Application Core",
        }


class TestFlexibleAspects:
    """
    Verifies [reliable-structure-extraction:ReviewStructure/TS-04] - Flexible aspect count
    Verifies [reliable-structure-extraction:ReviewStructure/TS-05] - Empty aspects rejected
    """

    def test_three_aspects_valid(self):
        """
        Given: JSON with 3 aspects instead of 5
        When: ReviewStructure.model_validate() called
        Then: Validates successfully with 3 aspects
        """
        data = json.loads(json.dumps(VALID_STRUCTURE_JSON))
        data["aspects"] = data["aspects"][:3]

        structure = ReviewStructure.model_validate(data)
        assert len(structure.aspects) == 3

    def test_single_aspect_valid(self):
        """
        Given: JSON with 1 aspect
        When: ReviewStructure.model_validate() called
        Then: Validates successfully with 1 aspect
        """
        data = json.loads(json.dumps(VALID_STRUCTURE_JSON))
        data["aspects"] = data["aspects"][:1]

        structure = ReviewStructure.model_validate(data)
        assert len(structure.aspects) == 1

    def test_six_aspects_valid(self):
        """
        Given: JSON with 6 aspects
        When: ReviewStructure.model_validate() called
        Then: Validates successfully with 6 aspects
        """
        data = json.loads(json.dumps(VALID_STRUCTURE_JSON))
        data["aspects"].append({
            "name": "Construction Phase Impacts",
            "rating": "amber",
            "key_issue": "Temporary road closures may affect cycle routes",
            "analysis": "Construction traffic management plan needed.",
        })

        structure = ReviewStructure.model_validate(data)
        assert len(structure.aspects) == 6

    def test_empty_aspects_rejected(self):
        """
        Given: JSON with aspects: []
        When: ReviewStructure.model_validate() called
        Then: ValidationError raised
        """
        data = json.loads(json.dumps(VALID_STRUCTURE_JSON))
        data["aspects"] = []

        with pytest.raises(ValidationError):
            ReviewStructure.model_validate(data)
