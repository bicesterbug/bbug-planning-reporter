"""
Tests for policies API endpoints.

Implements test scenarios from [policy-knowledge-base:PolicyRouter/TS-01] through [TS-11]
"""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_effective_date_resolver, get_policy_registry
from src.api.main import app
from src.api.schemas import PolicyCategory
from src.api.schemas.policy import (
    PolicyDocumentRecord,
    PolicyRevisionSummary,
    RevisionStatus,
)
from src.shared.effective_date_resolver import EffectivePolicyResult, EffectiveSnapshotResult
from src.shared.policy_registry import (
    CannotDeleteSoleRevisionError,
    PolicyAlreadyExistsError,
    PolicyNotFoundError,
    RevisionNotFoundError,
    RevisionOverlapError,
)


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def mock_registry():
    """Create mock PolicyRegistry."""
    mock = AsyncMock()
    mock.create_policy = AsyncMock()
    mock.get_policy = AsyncMock(return_value=None)
    mock.get_policy_with_revisions = AsyncMock(return_value=None)
    mock.list_policies = AsyncMock(return_value=[])
    mock.update_policy = AsyncMock()
    mock.create_revision = AsyncMock()
    mock.get_revision = AsyncMock(return_value=None)
    mock.list_revisions = AsyncMock(return_value=[])
    mock.update_revision = AsyncMock()
    mock.delete_revision = AsyncMock()
    return mock


@pytest.fixture
def mock_resolver():
    """Create mock EffectiveDateResolver."""
    mock = AsyncMock()
    mock.resolve_snapshot = AsyncMock()
    return mock


class TestCreatePolicy:
    """
    Tests for POST /api/v1/policies endpoint.

    Implements [policy-knowledge-base:PolicyRouter/TS-01] - Register new policy
    Implements [policy-knowledge-base:PolicyRouter/TS-02] - Duplicate source rejected
    Implements [policy-knowledge-base:PolicyRouter/TS-03] - Invalid source format
    """

    def test_register_new_policy(self, client, mock_registry):
        """
        Verifies [policy-knowledge-base:PolicyRouter/TS-01] - Register new policy

        Given: Valid payload
        When: POST /policies
        Then: Returns 201 with policy, current_revision null
        """
        created_at = datetime.now(UTC)
        mock_registry.create_policy = AsyncMock(
            return_value=PolicyDocumentRecord(
                source="LTN_1_20",
                title="Cycle Infrastructure Design (LTN 1/20)",
                description="National guidance on cycle infrastructure",
                category=PolicyCategory.NATIONAL_GUIDANCE,
                created_at=created_at,
            )
        )

        app.dependency_overrides[get_policy_registry] = lambda: mock_registry

        response = client.post(
            "/api/v1/policies",
            json={
                "source": "LTN_1_20",
                "title": "Cycle Infrastructure Design (LTN 1/20)",
                "description": "National guidance on cycle infrastructure",
                "category": "national_guidance",
            },
        )

        app.dependency_overrides.clear()

        assert response.status_code == 201
        data = response.json()
        assert data["source"] == "LTN_1_20"
        assert data["title"] == "Cycle Infrastructure Design (LTN 1/20)"
        assert data["category"] == "national_guidance"
        assert data["current_revision"] is None
        assert data["revision_count"] == 0
        assert data["revisions"] == []

    def test_duplicate_source_rejected(self, client, mock_registry):
        """
        Verifies [policy-knowledge-base:PolicyRouter/TS-02] - Duplicate source rejected

        Given: Policy "NPPF" exists
        When: POST /policies with source "NPPF"
        Then: Returns 409 policy_already_exists
        """
        mock_registry.create_policy = AsyncMock(
            side_effect=PolicyAlreadyExistsError("NPPF")
        )

        app.dependency_overrides[get_policy_registry] = lambda: mock_registry

        response = client.post(
            "/api/v1/policies",
            json={
                "source": "NPPF",
                "title": "National Planning Policy Framework",
                "category": "national_policy",
            },
        )

        app.dependency_overrides.clear()

        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "policy_already_exists"

    def test_invalid_source_format(self, client, mock_registry):
        """
        Verifies [policy-knowledge-base:PolicyRouter/TS-03] - Invalid source format

        Given: Source "invalid-format" (not UPPER_SNAKE_CASE)
        When: POST /policies
        Then: Returns 422 validation error
        """
        app.dependency_overrides[get_policy_registry] = lambda: mock_registry

        response = client.post(
            "/api/v1/policies",
            json={
                "source": "invalid-format",
                "title": "Invalid Policy",
                "category": "national_policy",
            },
        )

        app.dependency_overrides.clear()

        assert response.status_code == 422
        data = response.json()
        assert "error" in data
        # The validation error should mention source format
        assert any("source" in str(err).lower() for err in data["error"]["details"]["errors"])


class TestListPolicies:
    """
    Tests for GET /api/v1/policies endpoint.

    Implements [policy-knowledge-base:PolicyRouter/TS-07] - List policies
    """

    def test_list_policies(self, client, mock_registry):
        """
        Verifies [policy-knowledge-base:PolicyRouter/TS-07] - List policies

        Given: 3 policies exist
        When: GET /policies
        Then: Returns 200 with 3 policies
        """
        from src.api.schemas.policy import PolicyDocumentSummary

        policies = [
            PolicyDocumentSummary(
                source="LTN_1_20",
                title="LTN 1/20",
                category=PolicyCategory.NATIONAL_GUIDANCE,
                current_revision=None,
                revision_count=2,
            ),
            PolicyDocumentSummary(
                source="NPPF",
                title="NPPF",
                category=PolicyCategory.NATIONAL_POLICY,
                current_revision=PolicyRevisionSummary(
                    revision_id="rev_NPPF_2024_12",
                    version_label="December 2024",
                    effective_from=date(2024, 12, 12),
                    effective_to=None,
                    status=RevisionStatus.ACTIVE,
                ),
                revision_count=3,
            ),
            PolicyDocumentSummary(
                source="CHERWELL_LOCAL_PLAN",
                title="Cherwell Local Plan",
                category=PolicyCategory.LOCAL_PLAN,
                current_revision=None,
                revision_count=0,
            ),
        ]
        mock_registry.list_policies = AsyncMock(return_value=policies)

        app.dependency_overrides[get_policy_registry] = lambda: mock_registry

        response = client.get("/api/v1/policies")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert len(data["policies"]) == 3
        assert data["policies"][0]["source"] == "LTN_1_20"
        assert data["policies"][1]["source"] == "NPPF"
        assert data["policies"][1]["current_revision"]["revision_id"] == "rev_NPPF_2024_12"

    def test_list_policies_with_category_filter(self, client, mock_registry):
        """
        Verifies [policy-knowledge-base:PolicyRouter/TS-07] - List policies with filter

        Given: Multiple policies with different categories
        When: GET /policies?category=national_policy
        Then: Returns only national_policy policies
        """
        from src.api.schemas.policy import PolicyDocumentSummary

        policies = [
            PolicyDocumentSummary(
                source="NPPF",
                title="NPPF",
                category=PolicyCategory.NATIONAL_POLICY,
                current_revision=None,
                revision_count=1,
            ),
        ]
        mock_registry.list_policies = AsyncMock(return_value=policies)

        app.dependency_overrides[get_policy_registry] = lambda: mock_registry

        response = client.get("/api/v1/policies?category=national_policy")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        # Verify the filter was passed
        mock_registry.list_policies.assert_called_once()
        call_kwargs = mock_registry.list_policies.call_args[1]
        assert call_kwargs["category"] == PolicyCategory.NATIONAL_POLICY


class TestGetPolicy:
    """
    Tests for GET /api/v1/policies/{source} endpoint.

    Implements [policy-knowledge-base:PolicyRouter/TS-08] - Get policy detail
    Implements [policy-knowledge-base:PolicyRouter/TS-09] - Policy not found
    """

    def test_get_policy_detail(self, client, mock_registry):
        """
        Verifies [policy-knowledge-base:PolicyRouter/TS-08] - Get policy detail

        Given: Policy "NPPF" with 3 revisions
        When: GET /policies/NPPF
        Then: Returns 200 with all 3 revisions
        """
        from src.api.schemas.policy import PolicyDocumentDetail

        policy = PolicyDocumentDetail(
            source="NPPF",
            title="National Planning Policy Framework",
            description="Framework for planning in England",
            category=PolicyCategory.NATIONAL_POLICY,
            revisions=[
                PolicyRevisionSummary(
                    revision_id="rev_NPPF_2024_12",
                    version_label="December 2024",
                    effective_from=date(2024, 12, 12),
                    effective_to=None,
                    status=RevisionStatus.ACTIVE,
                ),
                PolicyRevisionSummary(
                    revision_id="rev_NPPF_2023_09",
                    version_label="September 2023",
                    effective_from=date(2023, 9, 5),
                    effective_to=date(2024, 12, 11),
                    status=RevisionStatus.SUPERSEDED,
                ),
                PolicyRevisionSummary(
                    revision_id="rev_NPPF_2021_07",
                    version_label="July 2021",
                    effective_from=date(2021, 7, 20),
                    effective_to=date(2023, 9, 4),
                    status=RevisionStatus.SUPERSEDED,
                ),
            ],
            current_revision=PolicyRevisionSummary(
                revision_id="rev_NPPF_2024_12",
                version_label="December 2024",
                effective_from=date(2024, 12, 12),
                effective_to=None,
                status=RevisionStatus.ACTIVE,
            ),
            revision_count=3,
            created_at=datetime.now(UTC),
        )
        mock_registry.get_policy_with_revisions = AsyncMock(return_value=policy)

        app.dependency_overrides[get_policy_registry] = lambda: mock_registry

        response = client.get("/api/v1/policies/NPPF")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "NPPF"
        assert data["revision_count"] == 3
        assert len(data["revisions"]) == 3
        assert data["current_revision"]["revision_id"] == "rev_NPPF_2024_12"

    def test_policy_not_found(self, client, mock_registry):
        """
        Verifies [policy-knowledge-base:PolicyRouter/TS-09] - Policy not found

        Given: No policy "INVALID"
        When: GET /policies/INVALID
        Then: Returns 404 policy_not_found
        """
        mock_registry.get_policy_with_revisions = AsyncMock(return_value=None)

        app.dependency_overrides[get_policy_registry] = lambda: mock_registry

        response = client.get("/api/v1/policies/INVALID")

        app.dependency_overrides.clear()

        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "policy_not_found"


class TestUpdatePolicy:
    """Tests for PATCH /api/v1/policies/{source} endpoint."""

    def test_update_policy_metadata(self, client, mock_registry):
        """
        Verifies [policy-knowledge-base:PolicyRouter/TS-08] - Update policy metadata

        Given: Existing policy "NPPF"
        When: PATCH /policies/NPPF with new title
        Then: Returns 200 with updated policy
        """
        from src.api.schemas.policy import PolicyDocumentDetail

        updated_policy = PolicyDocumentDetail(
            source="NPPF",
            title="National Planning Policy Framework (Updated)",
            description="Updated description",
            category=PolicyCategory.NATIONAL_POLICY,
            revisions=[],
            current_revision=None,
            revision_count=0,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_registry.update_policy = AsyncMock()
        mock_registry.get_policy_with_revisions = AsyncMock(return_value=updated_policy)

        app.dependency_overrides[get_policy_registry] = lambda: mock_registry

        response = client.patch(
            "/api/v1/policies/NPPF",
            json={
                "title": "National Planning Policy Framework (Updated)",
                "description": "Updated description",
            },
        )

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "National Planning Policy Framework (Updated)"

    def test_update_policy_not_found(self, client, mock_registry):
        """
        Verifies update policy not found case.

        Given: No policy "INVALID"
        When: PATCH /policies/INVALID
        Then: Returns 404 policy_not_found
        """
        mock_registry.update_policy = AsyncMock(
            side_effect=PolicyNotFoundError("INVALID")
        )

        app.dependency_overrides[get_policy_registry] = lambda: mock_registry

        response = client.patch(
            "/api/v1/policies/INVALID",
            json={"title": "Updated Title"},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "policy_not_found"


class TestGetEffectiveSnapshot:
    """
    Tests for GET /api/v1/policies/effective endpoint.

    Implements [policy-knowledge-base:PolicyRouter/TS-10] - Get effective snapshot
    Implements [policy-knowledge-base:PolicyRouter/TS-11] - Invalid date format
    """

    def test_get_effective_snapshot(self, client, mock_resolver):
        """
        Verifies [policy-knowledge-base:PolicyRouter/TS-10] - Get effective snapshot

        Given: Date 2024-03-15
        When: GET /policies/effective?date=2024-03-15
        Then: Returns 200 with snapshot
        """
        snapshot = EffectiveSnapshotResult(
            effective_date=date(2024, 3, 15),
            policies=[
                EffectivePolicyResult(
                    source="LTN_1_20",
                    title="LTN 1/20",
                    category=PolicyCategory.NATIONAL_GUIDANCE,
                    effective_revision=PolicyRevisionSummary(
                        revision_id="rev_LTN120_2020_07",
                        version_label="July 2020",
                        effective_from=date(2020, 7, 27),
                        effective_to=None,
                        status=RevisionStatus.ACTIVE,
                    ),
                ),
                EffectivePolicyResult(
                    source="NPPF",
                    title="NPPF",
                    category=PolicyCategory.NATIONAL_POLICY,
                    effective_revision=PolicyRevisionSummary(
                        revision_id="rev_NPPF_2023_09",
                        version_label="September 2023",
                        effective_from=date(2023, 9, 5),
                        effective_to=date(2024, 12, 11),
                        status=RevisionStatus.SUPERSEDED,
                    ),
                ),
            ],
            policies_with_revision=["LTN_1_20", "NPPF"],
            policies_not_yet_effective=[],
            policies_in_gap=[],
        )
        mock_resolver.resolve_snapshot = AsyncMock(return_value=snapshot)

        app.dependency_overrides[get_effective_date_resolver] = lambda: mock_resolver

        response = client.get("/api/v1/policies/effective?date=2024-03-15")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["effective_date"] == "2024-03-15"
        assert len(data["policies"]) == 2
        assert data["policies"][0]["source"] == "LTN_1_20"
        assert data["policies"][0]["effective_revision"]["revision_id"] == "rev_LTN120_2020_07"

    def test_effective_snapshot_with_not_yet_effective(self, client, mock_resolver):
        """
        Verifies effective snapshot with policies not yet effective.

        Given: Date before first revision of some policies
        When: GET /policies/effective?date=2019-01-01
        Then: Returns 200 with policies_not_yet_effective populated
        """
        snapshot = EffectiveSnapshotResult(
            effective_date=date(2019, 1, 1),
            policies=[
                EffectivePolicyResult(
                    source="LTN_1_20",
                    title="LTN 1/20",
                    category=PolicyCategory.NATIONAL_GUIDANCE,
                    effective_revision=None,
                    reason="date_before_first_revision",
                ),
            ],
            policies_with_revision=[],
            policies_not_yet_effective=["LTN_1_20"],
            policies_in_gap=[],
        )
        mock_resolver.resolve_snapshot = AsyncMock(return_value=snapshot)

        app.dependency_overrides[get_effective_date_resolver] = lambda: mock_resolver

        response = client.get("/api/v1/policies/effective?date=2019-01-01")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert "LTN_1_20" in data["policies_not_yet_effective"]
        assert data["policies"][0]["effective_revision"] is None

    def test_invalid_date_format(self, client, mock_resolver):
        """
        Verifies [policy-knowledge-base:PolicyRouter/TS-11] - Invalid date format

        Given: Date "invalid"
        When: GET /policies/effective?date=invalid
        Then: Returns 422 validation error
        """
        app.dependency_overrides[get_effective_date_resolver] = lambda: mock_resolver

        response = client.get("/api/v1/policies/effective?date=invalid")

        app.dependency_overrides.clear()

        assert response.status_code == 422
        data = response.json()
        assert "error" in data

    def test_missing_date_parameter(self, client, mock_resolver):
        """
        Verifies missing required date parameter.

        Given: No date parameter
        When: GET /policies/effective
        Then: Returns 422 missing required parameter
        """
        app.dependency_overrides[get_effective_date_resolver] = lambda: mock_resolver

        response = client.get("/api/v1/policies/effective")

        app.dependency_overrides.clear()

        assert response.status_code == 422
        data = response.json()
        assert "error" in data


# =============================================================================
# Revision Endpoint Tests
# =============================================================================


class TestUploadRevision:
    """
    Tests for POST /api/v1/policies/{source}/revisions endpoint.

    Implements [policy-knowledge-base:PolicyRouter/TS-04] - Upload revision
    Implements [policy-knowledge-base:PolicyRouter/TS-05] - Upload non-PDF rejected
    Implements [policy-knowledge-base:PolicyRouter/TS-06] - Overlapping dates rejected
    """

    def test_upload_revision(self, client, mock_registry, tmp_path, monkeypatch):
        """
        Verifies [policy-knowledge-base:PolicyRouter/TS-04] - Upload revision

        Given: PDF file, valid dates
        When: POST /policies/LTN_1_20/revisions
        Then: Returns 202 with status "processing"
        """
        from src.api.schemas.policy import PolicyRevisionRecord

        # Set up temp directory for file storage
        monkeypatch.setenv("POLICY_DATA_DIR", str(tmp_path))

        mock_registry.get_policy = AsyncMock(
            return_value=PolicyDocumentRecord(
                source="LTN_1_20",
                title="LTN 1/20",
                category=PolicyCategory.NATIONAL_GUIDANCE,
                created_at=datetime.now(UTC),
            )
        )
        mock_registry.create_revision = AsyncMock(
            return_value=PolicyRevisionRecord(
                revision_id="rev_LTN_1_20_2020_07",
                source="LTN_1_20",
                version_label="July 2020",
                effective_from=date(2020, 7, 27),
                effective_to=None,
                status=RevisionStatus.PROCESSING,
                created_at=datetime.now(UTC),
            )
        )
        mock_registry.list_revisions = AsyncMock(return_value=[])

        app.dependency_overrides[get_policy_registry] = lambda: mock_registry

        # Create a test PDF file
        pdf_content = b"%PDF-1.4 test content"

        response = client.post(
            "/api/v1/policies/LTN_1_20/revisions",
            data={
                "version_label": "July 2020",
                "effective_from": "2020-07-27",
            },
            files={"file": ("ltn_1_20.pdf", pdf_content, "application/pdf")},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 202
        data = response.json()
        assert data["source"] == "LTN_1_20"
        assert data["revision_id"] == "rev_LTN_1_20_2020_07"
        assert data["status"] == "processing"
        assert "ingestion_job_id" in data
        assert "links" in data

    def test_upload_non_pdf_rejected(self, client, mock_registry, tmp_path, monkeypatch):
        """
        Verifies [policy-knowledge-base:PolicyRouter/TS-05] - Upload non-PDF rejected

        Given: DOCX file
        When: POST /policies/LTN_1_20/revisions
        Then: Returns 422 unsupported_file_type
        """
        monkeypatch.setenv("POLICY_DATA_DIR", str(tmp_path))

        mock_registry.get_policy = AsyncMock(
            return_value=PolicyDocumentRecord(
                source="LTN_1_20",
                title="LTN 1/20",
                category=PolicyCategory.NATIONAL_GUIDANCE,
                created_at=datetime.now(UTC),
            )
        )

        app.dependency_overrides[get_policy_registry] = lambda: mock_registry

        response = client.post(
            "/api/v1/policies/LTN_1_20/revisions",
            data={
                "version_label": "July 2020",
                "effective_from": "2020-07-27",
            },
            files={"file": ("document.docx", b"docx content", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 422
        data = response.json()
        assert data["error"]["code"] == "unsupported_file_type"

    def test_upload_revision_overlap_rejected(self, client, mock_registry, tmp_path, monkeypatch):
        """
        Verifies [policy-knowledge-base:PolicyRouter/TS-06] - Overlapping dates rejected

        Given: Revision 2024-01-01 to 2024-12-31 exists
        When: Upload revision effective_from 2024-06-01
        Then: Returns 409 revision_overlap
        """
        monkeypatch.setenv("POLICY_DATA_DIR", str(tmp_path))

        mock_registry.get_policy = AsyncMock(
            return_value=PolicyDocumentRecord(
                source="NPPF",
                title="NPPF",
                category=PolicyCategory.NATIONAL_POLICY,
                created_at=datetime.now(UTC),
            )
        )
        mock_registry.create_revision = AsyncMock(
            side_effect=RevisionOverlapError(
                "NPPF", date(2024, 6, 1), None, "rev_NPPF_2024_01"
            )
        )

        app.dependency_overrides[get_policy_registry] = lambda: mock_registry

        pdf_content = b"%PDF-1.4 test content"

        response = client.post(
            "/api/v1/policies/NPPF/revisions",
            data={
                "version_label": "June 2024",
                "effective_from": "2024-06-01",
            },
            files={"file": ("nppf.pdf", pdf_content, "application/pdf")},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "revision_overlap"

    def test_upload_revision_policy_not_found(self, client, mock_registry, tmp_path, monkeypatch):
        """
        Verifies upload revision to non-existent policy.

        Given: No policy "INVALID"
        When: POST /policies/INVALID/revisions
        Then: Returns 404 policy_not_found
        """
        monkeypatch.setenv("POLICY_DATA_DIR", str(tmp_path))

        mock_registry.get_policy = AsyncMock(return_value=None)

        app.dependency_overrides[get_policy_registry] = lambda: mock_registry

        pdf_content = b"%PDF-1.4 test content"

        response = client.post(
            "/api/v1/policies/INVALID/revisions",
            data={
                "version_label": "July 2020",
                "effective_from": "2020-07-27",
            },
            files={"file": ("test.pdf", pdf_content, "application/pdf")},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "policy_not_found"


class TestGetRevision:
    """Tests for GET /api/v1/policies/{source}/revisions/{revision_id} endpoint."""

    def test_get_revision_detail(self, client, mock_registry):
        """
        Verifies [policy-knowledge-base:PolicyRouter/TS-12] - Get revision detail

        Given: Existing revision
        When: GET /policies/NPPF/revisions/rev_NPPF_2024_12
        Then: Returns 200 with revision details
        """
        from src.api.schemas.policy import PolicyRevisionRecord

        mock_registry.get_revision = AsyncMock(
            return_value=PolicyRevisionRecord(
                revision_id="rev_NPPF_2024_12",
                source="NPPF",
                version_label="December 2024",
                effective_from=date(2024, 12, 12),
                effective_to=None,
                status=RevisionStatus.ACTIVE,
                chunk_count=150,
                created_at=datetime.now(UTC),
                ingested_at=datetime.now(UTC),
            )
        )

        app.dependency_overrides[get_policy_registry] = lambda: mock_registry

        response = client.get("/api/v1/policies/NPPF/revisions/rev_NPPF_2024_12")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["revision_id"] == "rev_NPPF_2024_12"
        assert data["source"] == "NPPF"
        assert data["status"] == "active"
        assert data["chunk_count"] == 150

    def test_get_revision_not_found(self, client, mock_registry):
        """
        Verifies get revision not found.

        Given: No revision "rev_INVALID"
        When: GET /policies/NPPF/revisions/rev_INVALID
        Then: Returns 404 revision_not_found
        """
        mock_registry.get_revision = AsyncMock(return_value=None)
        mock_registry.get_policy = AsyncMock(
            return_value=PolicyDocumentRecord(
                source="NPPF",
                title="NPPF",
                category=PolicyCategory.NATIONAL_POLICY,
                created_at=datetime.now(UTC),
            )
        )

        app.dependency_overrides[get_policy_registry] = lambda: mock_registry

        response = client.get("/api/v1/policies/NPPF/revisions/rev_INVALID")

        app.dependency_overrides.clear()

        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "revision_not_found"


class TestUpdateRevision:
    """
    Tests for PATCH /api/v1/policies/{source}/revisions/{revision_id} endpoint.

    Implements [policy-knowledge-base:PolicyRouter/TS-12] - Update revision metadata
    """

    def test_update_revision_metadata(self, client, mock_registry):
        """
        Verifies [policy-knowledge-base:PolicyRouter/TS-12] - Update revision metadata

        Given: Existing revision
        When: PATCH /policies/NPPF/revisions/rev_NPPF_2024_12
        Then: Returns 200 with updated revision
        """
        from src.api.schemas.policy import PolicyRevisionRecord

        mock_registry.update_revision = AsyncMock()
        mock_registry.get_revision = AsyncMock(
            return_value=PolicyRevisionRecord(
                revision_id="rev_NPPF_2024_12",
                source="NPPF",
                version_label="December 2024 (Updated)",
                effective_from=date(2024, 12, 12),
                effective_to=None,
                status=RevisionStatus.ACTIVE,
                notes="Updated notes",
                created_at=datetime.now(UTC),
            )
        )

        app.dependency_overrides[get_policy_registry] = lambda: mock_registry

        response = client.patch(
            "/api/v1/policies/NPPF/revisions/rev_NPPF_2024_12",
            json={
                "version_label": "December 2024 (Updated)",
                "notes": "Updated notes",
            },
        )

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["version_label"] == "December 2024 (Updated)"
        assert data["notes"] == "Updated notes"

    def test_update_revision_not_found(self, client, mock_registry):
        """
        Verifies update revision not found.

        Given: No revision "rev_INVALID"
        When: PATCH /policies/NPPF/revisions/rev_INVALID
        Then: Returns 404 revision_not_found
        """
        mock_registry.update_revision = AsyncMock(
            side_effect=RevisionNotFoundError("NPPF", "rev_INVALID")
        )

        app.dependency_overrides[get_policy_registry] = lambda: mock_registry

        response = client.patch(
            "/api/v1/policies/NPPF/revisions/rev_INVALID",
            json={"version_label": "Updated"},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "revision_not_found"


class TestDeleteRevision:
    """
    Tests for DELETE /api/v1/policies/{source}/revisions/{revision_id} endpoint.

    Implements [policy-knowledge-base:PolicyRouter/TS-13] - Delete revision
    Implements [policy-knowledge-base:PolicyRouter/TS-14] - Cannot delete sole revision
    """

    def test_delete_revision(self, client, mock_registry):
        """
        Verifies [policy-knowledge-base:PolicyRouter/TS-13] - Delete revision

        Given: Revision with multiple active
        When: DELETE /policies/NPPF/revisions/rev_NPPF_2021_07
        Then: Returns 200 with chunks_removed count
        """
        from src.api.schemas.policy import PolicyRevisionRecord

        mock_registry.get_revision = AsyncMock(
            return_value=PolicyRevisionRecord(
                revision_id="rev_NPPF_2021_07",
                source="NPPF",
                version_label="July 2021",
                effective_from=date(2021, 7, 20),
                effective_to=date(2023, 9, 4),
                status=RevisionStatus.SUPERSEDED,
                chunk_count=120,
                created_at=datetime.now(UTC),
            )
        )
        mock_registry.delete_revision = AsyncMock()

        app.dependency_overrides[get_policy_registry] = lambda: mock_registry

        response = client.delete("/api/v1/policies/NPPF/revisions/rev_NPPF_2021_07")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "NPPF"
        assert data["revision_id"] == "rev_NPPF_2021_07"
        assert data["status"] == "deleted"
        assert data["chunks_removed"] == 120

    def test_cannot_delete_sole_revision(self, client, mock_registry):
        """
        Verifies [policy-knowledge-base:PolicyRouter/TS-14] - Cannot delete sole revision

        Given: Only one active revision
        When: DELETE sole revision
        Then: Returns 409 cannot_delete_sole_revision
        """
        from src.api.schemas.policy import PolicyRevisionRecord

        mock_registry.get_revision = AsyncMock(
            return_value=PolicyRevisionRecord(
                revision_id="rev_NPPF_2024_12",
                source="NPPF",
                version_label="December 2024",
                effective_from=date(2024, 12, 12),
                effective_to=None,
                status=RevisionStatus.ACTIVE,
                chunk_count=150,
                created_at=datetime.now(UTC),
            )
        )
        mock_registry.delete_revision = AsyncMock(
            side_effect=CannotDeleteSoleRevisionError("NPPF", "rev_NPPF_2024_12")
        )

        app.dependency_overrides[get_policy_registry] = lambda: mock_registry

        response = client.delete("/api/v1/policies/NPPF/revisions/rev_NPPF_2024_12")

        app.dependency_overrides.clear()

        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "cannot_delete_sole_revision"


class TestRevisionStatus:
    """
    Tests for GET /api/v1/policies/{source}/revisions/{revision_id}/status endpoint.

    Implements [policy-knowledge-base:PolicyRouter/TS-16] - Get revision status
    """

    def test_get_processing_revision_status(self, client, mock_registry):
        """
        Verifies [policy-knowledge-base:PolicyRouter/TS-16] - Get revision status

        Given: Processing revision
        When: GET /policies/NPPF/revisions/rev_NPPF_2026_02/status
        Then: Returns 200 with progress
        """
        from src.api.schemas.policy import PolicyRevisionRecord

        mock_registry.get_revision = AsyncMock(
            return_value=PolicyRevisionRecord(
                revision_id="rev_NPPF_2026_02",
                source="NPPF",
                version_label="February 2026",
                effective_from=date(2026, 2, 1),
                effective_to=None,
                status=RevisionStatus.PROCESSING,
                created_at=datetime.now(UTC),
            )
        )

        app.dependency_overrides[get_policy_registry] = lambda: mock_registry

        response = client.get("/api/v1/policies/NPPF/revisions/rev_NPPF_2026_02/status")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["revision_id"] == "rev_NPPF_2026_02"
        assert data["status"] == "processing"
        assert data["progress"] is not None

    def test_get_active_revision_status(self, client, mock_registry):
        """
        Verifies get active revision status shows complete.

        Given: Active revision with chunks
        When: GET /policies/NPPF/revisions/rev_NPPF_2024_12/status
        Then: Returns 200 with completed progress
        """
        from src.api.schemas.policy import PolicyRevisionRecord

        mock_registry.get_revision = AsyncMock(
            return_value=PolicyRevisionRecord(
                revision_id="rev_NPPF_2024_12",
                source="NPPF",
                version_label="December 2024",
                effective_from=date(2024, 12, 12),
                effective_to=None,
                status=RevisionStatus.ACTIVE,
                chunk_count=150,
                created_at=datetime.now(UTC),
                ingested_at=datetime.now(UTC),
            )
        )

        app.dependency_overrides[get_policy_registry] = lambda: mock_registry

        response = client.get("/api/v1/policies/NPPF/revisions/rev_NPPF_2024_12/status")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "active"
        assert data["progress"]["percent_complete"] == 100
        assert data["progress"]["chunks_processed"] == 150


class TestReindexRevision:
    """
    Tests for POST /api/v1/policies/{source}/revisions/{revision_id}/reindex endpoint.

    Implements [policy-knowledge-base:PolicyRouter/TS-15] - Reindex revision
    """

    def test_reindex_revision(self, client, mock_registry):
        """
        Verifies [policy-knowledge-base:PolicyRouter/TS-15] - Reindex revision

        Given: Existing active revision
        When: POST /policies/LTN_1_20/revisions/rev_LTN120_2020_07/reindex
        Then: Returns 202 with job_id
        """
        from src.api.schemas.policy import PolicyRevisionRecord

        mock_registry.get_revision = AsyncMock(
            return_value=PolicyRevisionRecord(
                revision_id="rev_LTN120_2020_07",
                source="LTN_1_20",
                version_label="July 2020",
                effective_from=date(2020, 7, 27),
                effective_to=None,
                status=RevisionStatus.ACTIVE,
                chunk_count=200,
                created_at=datetime.now(UTC),
            )
        )
        mock_registry.update_revision = AsyncMock()

        app.dependency_overrides[get_policy_registry] = lambda: mock_registry

        response = client.post("/api/v1/policies/LTN_1_20/revisions/rev_LTN120_2020_07/reindex")

        app.dependency_overrides.clear()

        assert response.status_code == 202
        data = response.json()
        assert data["revision_id"] == "rev_LTN120_2020_07"
        assert data["status"] == "processing"

    def test_cannot_reindex_processing_revision(self, client, mock_registry):
        """
        Verifies cannot reindex already processing revision.

        Given: Revision already processing
        When: POST /policies/LTN_1_20/revisions/rev_LTN120_2020_07/reindex
        Then: Returns 409 cannot_reindex
        """
        from src.api.schemas.policy import PolicyRevisionRecord

        mock_registry.get_revision = AsyncMock(
            return_value=PolicyRevisionRecord(
                revision_id="rev_LTN120_2020_07",
                source="LTN_1_20",
                version_label="July 2020",
                effective_from=date(2020, 7, 27),
                effective_to=None,
                status=RevisionStatus.PROCESSING,
                created_at=datetime.now(UTC),
            )
        )

        app.dependency_overrides[get_policy_registry] = lambda: mock_registry

        response = client.post("/api/v1/policies/LTN_1_20/revisions/rev_LTN120_2020_07/reindex")

        app.dependency_overrides.clear()

        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "cannot_reindex"
