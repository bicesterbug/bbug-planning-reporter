"""Tests for file serving endpoint."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    """Create a temporary output directory with test files."""
    # Create a review output structure
    review_dir = tmp_path / "25_01178_REM" / "output"
    review_dir.mkdir(parents=True)

    (review_dir / "rev_xxx_review.json").write_text('{"overall_rating": "amber"}')
    (review_dir / "rev_xxx_review.md").write_text("# Review\n\nSome content")

    return tmp_path


class TestServeFile:
    """Tests for GET /api/v1/files/{path}."""

    def test_serves_json_file(self, client, output_dir: Path) -> None:
        """Existing JSON file is served with correct content type."""
        with patch("src.api.routes.files.OUTPUT_BASE_DIR", output_dir):
            response = client.get(
                "/api/v1/files/25_01178_REM/output/rev_xxx_review.json"
            )

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"
        assert response.json() == {"overall_rating": "amber"}

    def test_serves_markdown_file(self, client, output_dir: Path) -> None:
        """Existing markdown file is served with correct content type."""
        with patch("src.api.routes.files.OUTPUT_BASE_DIR", output_dir):
            response = client.get(
                "/api/v1/files/25_01178_REM/output/rev_xxx_review.md"
            )

        assert response.status_code == 200
        assert "text/markdown" in response.headers["content-type"]
        assert "# Review" in response.text

    def test_returns_404_for_nonexistent_file(self, client, output_dir: Path) -> None:
        """Non-existent file returns 404."""
        with patch("src.api.routes.files.OUTPUT_BASE_DIR", output_dir):
            response = client.get("/api/v1/files/nonexistent/file.json")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "file_not_found"

    def test_rejects_path_traversal(self, client, output_dir: Path) -> None:
        """Path traversal attempt is blocked.

        The HTTP framework normalizes ../.. in URLs before routing,
        so the request never reaches the handler (404 from router).
        This confirms the traversal is prevented at the framework level.
        """
        with patch("src.api.routes.files.OUTPUT_BASE_DIR", output_dir):
            response = client.get("/api/v1/files/../../etc/passwd")

        assert response.status_code != 200
        # The framework normalizes the path, so the route doesn't match (404)
        assert response.status_code in (400, 404)

    def test_returns_404_when_s3_configured(self, client) -> None:
        """All requests return 404 when S3 is configured."""
        with patch.dict(os.environ, {"S3_ENDPOINT_URL": "https://s3.example.com"}):
            response = client.get("/api/v1/files/any/path.json")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "local_files_not_available"
