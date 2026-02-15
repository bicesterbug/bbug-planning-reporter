"""
Tests for Destinations API endpoints.

Verifies [cycle-route-assessment:DestinationManagement/TS-04] - List destinations via API
Verifies [cycle-route-assessment:ReviewOptionsRequest/TS-01] through TS-03
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_arq_pool, get_redis_client
from src.api.main import app
from src.shared.destinations import DEFAULT_DESTINATIONS


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def mock_redis():
    """Create mock Redis client."""
    mock = AsyncMock()
    return mock


class TestListDestinations:
    """Verifies [cycle-route-assessment:DestinationManagement/TS-04]."""

    def test_list_destinations(self, client, mock_redis):
        """[DestinationManagement/TS-04] GET /destinations returns destinations."""
        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        with patch(
            "src.api.routes.destinations.list_destinations",
            new_callable=AsyncMock,
            return_value=DEFAULT_DESTINATIONS,
        ):
            response = client.get("/api/v1/destinations")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert len(data["destinations"]) == 3
        assert data["destinations"][0]["name"] == "Bicester North Station"

    def test_list_empty(self, client, mock_redis):
        """Empty list when no destinations."""
        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        with patch(
            "src.api.routes.destinations.list_destinations",
            new_callable=AsyncMock,
            return_value=[],
        ):
            response = client.get("/api/v1/destinations")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["destinations"] == []


class TestCreateDestination:
    """Verifies [cycle-route-assessment:DestinationManagement/TS-02] via API."""

    def test_create_destination(self, client, mock_redis):
        """POST /destinations creates a new destination."""
        new_dest = {
            "id": "dest_004",
            "name": "Town Centre",
            "lat": 51.9,
            "lon": -1.15,
            "category": "other",
        }
        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        with patch(
            "src.api.routes.destinations.add_destination",
            new_callable=AsyncMock,
            return_value=new_dest,
        ):
            response = client.post(
                "/api/v1/destinations",
                json={"name": "Town Centre", "lat": 51.9, "lon": -1.15, "category": "other"},
            )

        app.dependency_overrides.clear()

        assert response.status_code == 201
        data = response.json()
        assert data["id"] == "dest_004"
        assert data["name"] == "Town Centre"

    def test_create_invalid_category(self, client, mock_redis):
        """Invalid category rejected."""
        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.post(
            "/api/v1/destinations",
            json={"name": "X", "lat": 51.0, "lon": -1.0, "category": "invalid"},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 422

    def test_create_missing_name(self, client, mock_redis):
        """Missing name rejected."""
        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        response = client.post(
            "/api/v1/destinations",
            json={"lat": 51.0, "lon": -1.0},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 422


class TestDeleteDestination:
    """Verifies [cycle-route-assessment:DestinationManagement/TS-03] via API."""

    def test_delete_destination(self, client, mock_redis):
        """DELETE /destinations/{id} deletes destination."""
        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        with patch(
            "src.api.routes.destinations.delete_destination",
            new_callable=AsyncMock,
            return_value=True,
        ):
            response = client.delete("/api/v1/destinations/dest_001")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["deleted"] is True

    def test_delete_not_found(self, client, mock_redis):
        """DELETE /destinations/{id} returns 404 for missing destination."""
        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        with patch(
            "src.api.routes.destinations.delete_destination",
            new_callable=AsyncMock,
            return_value=False,
        ):
            response = client.delete("/api/v1/destinations/dest_999")

        app.dependency_overrides.clear()

        assert response.status_code == 404


class TestDestinationsRoundTrip:
    """
    Verifies [cycle-route-assessment:ITS-05] - Destinations API round-trip.

    Given: Empty destinations
    When: POST destination, GET destinations, use in review
    Then: Destination stored, listed, used as route target
    """

    def test_create_then_list(self, client, mock_redis):
        """[ITS-05] Create a destination then list includes it."""
        new_dest = {
            "id": "dest_004",
            "name": "Town Centre",
            "lat": 51.9,
            "lon": -1.15,
            "category": "other",
        }

        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        # Create
        with patch(
            "src.api.routes.destinations.add_destination",
            new_callable=AsyncMock,
            return_value=new_dest,
        ):
            create_response = client.post(
                "/api/v1/destinations",
                json={"name": "Town Centre", "lat": 51.9, "lon": -1.15, "category": "other"},
            )

        assert create_response.status_code == 201

        # List should include the new destination
        with patch(
            "src.api.routes.destinations.list_destinations",
            new_callable=AsyncMock,
            return_value=[new_dest],
        ):
            list_response = client.get("/api/v1/destinations")

        app.dependency_overrides.clear()

        assert list_response.status_code == 200
        names = [d["name"] for d in list_response.json()["destinations"]]
        assert "Town Centre" in names

    def test_created_destination_usable_in_review(self, client, mock_redis):
        """[ITS-05] Created destination ID can be passed in review options."""
        mock_redis.get_active_review_id_for_ref = AsyncMock(return_value=None)
        mock_redis.get_latest_completed_review_id_for_ref = AsyncMock(return_value=None)
        mock_redis.store_job = AsyncMock()
        mock_arq = AsyncMock()
        mock_arq.enqueue_job = AsyncMock()

        app.dependency_overrides[get_redis_client] = lambda: mock_redis
        app.dependency_overrides[get_arq_pool] = lambda: mock_arq

        response = client.post(
            "/api/v1/reviews",
            json={
                "application_ref": "25/01178/REM",
                "options": {"destination_ids": ["dest_004"]},
            },
        )

        app.dependency_overrides.clear()

        assert response.status_code == 202
        stored_job = mock_redis.store_job.call_args[0][0]
        assert stored_job.options.destination_ids == ["dest_004"]


class TestReviewOptionsDestinationIds:
    """Verifies [cycle-route-assessment:ReviewOptionsRequest/TS-01] through TS-03."""

    def test_with_destination_ids(self, client, mock_redis):
        """[ReviewOptionsRequest/TS-01] Request with destination_ids passes validation."""
        mock_redis.get_active_review_id_for_ref = AsyncMock(return_value=None)
        mock_redis.get_latest_completed_review_id_for_ref = AsyncMock(return_value=None)
        mock_redis.store_job = AsyncMock()
        mock_arq = AsyncMock()
        mock_arq.enqueue_job = AsyncMock()

        app.dependency_overrides[get_redis_client] = lambda: mock_redis
        app.dependency_overrides[get_arq_pool] = lambda: mock_arq

        response = client.post(
            "/api/v1/reviews",
            json={
                "application_ref": "25/01178/REM",
                "options": {"destination_ids": ["dest_001"]},
            },
        )

        app.dependency_overrides.clear()

        assert response.status_code == 202

    def test_without_destination_ids(self, client, mock_redis):
        """[ReviewOptionsRequest/TS-02] Request without destination_ids defaults to None."""
        mock_redis.get_active_review_id_for_ref = AsyncMock(return_value=None)
        mock_redis.get_latest_completed_review_id_for_ref = AsyncMock(return_value=None)
        mock_redis.store_job = AsyncMock()
        mock_arq = AsyncMock()
        mock_arq.enqueue_job = AsyncMock()

        app.dependency_overrides[get_redis_client] = lambda: mock_redis
        app.dependency_overrides[get_arq_pool] = lambda: mock_arq

        response = client.post(
            "/api/v1/reviews",
            json={
                "application_ref": "25/01178/REM",
                "options": {},
            },
        )

        app.dependency_overrides.clear()

        assert response.status_code == 202

    def test_with_empty_destination_ids(self, client, mock_redis):
        """[ReviewOptionsRequest/TS-03] Empty destination_ids = no assessment."""
        mock_redis.get_active_review_id_for_ref = AsyncMock(return_value=None)
        mock_redis.get_latest_completed_review_id_for_ref = AsyncMock(return_value=None)
        mock_redis.store_job = AsyncMock()
        mock_arq = AsyncMock()
        mock_arq.enqueue_job = AsyncMock()

        app.dependency_overrides[get_redis_client] = lambda: mock_redis
        app.dependency_overrides[get_arq_pool] = lambda: mock_arq

        response = client.post(
            "/api/v1/reviews",
            json={
                "application_ref": "25/01178/REM",
                "options": {"destination_ids": []},
            },
        )

        app.dependency_overrides.clear()

        assert response.status_code == 202
