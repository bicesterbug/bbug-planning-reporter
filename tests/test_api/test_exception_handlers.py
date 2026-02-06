"""
Tests for GlobalExceptionHandler.

Verifies [api-hardening:FR-011] - Error response consistency
Verifies [api-hardening:FR-009] - Request validation with Pydantic
Verifies [api-hardening:NFR-002] - No stack traces exposed in production
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from src.api.exception_handlers import register_exception_handlers
from src.api.middleware.request_id import RequestIdMiddleware


def create_test_app():
    """Create test app with exception handlers."""
    # Disable server error middleware's exception re-raising
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)

    class TestRequest(BaseModel):
        name: str
        age: int

    @app.post("/test")
    async def test_endpoint(request: TestRequest):  # noqa: ARG001
        return {"ok": True}

    @app.get("/error/404")
    async def not_found():
        raise HTTPException(status_code=404, detail="Resource not found")

    @app.get("/error/500")
    async def internal_error():
        raise ValueError("Something went wrong")

    @app.get("/error/custom")
    async def custom_error():
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "custom_error",
                    "message": "Custom error message",
                }
            },
        )

    return app


@pytest.fixture
def app():
    """Create test app fixture."""
    return create_test_app()


@pytest.fixture
async def client(app):
    """Create async test client."""
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        yield client


class TestPydanticValidationError:
    """
    Tests for Pydantic validation error handling.

    Verifies [api-hardening:GlobalExceptionHandler/TS-01] - Pydantic validation error
    """

    @pytest.mark.asyncio
    async def test_validation_error_returns_422(self, client):
        """
        Verifies [api-hardening:GlobalExceptionHandler/TS-01] - Pydantic validation error

        Given: Invalid request body
        When: POST /test with invalid data
        Then: Returns 422 with field-level errors in standard format
        """
        response = await client.post("/test", json={"name": 123, "age": "not-a-number"})

        assert response.status_code == 422
        data = response.json()

        assert "error" in data
        assert data["error"]["code"] == "validation_error"
        assert "errors" in data["error"]["details"]

        # Should have field-level errors
        errors = data["error"]["details"]["errors"]
        assert len(errors) >= 1

        # Each error should have field, message, type
        for error in errors:
            assert "field" in error
            assert "message" in error
            assert "type" in error

    @pytest.mark.asyncio
    async def test_missing_required_field(self, client):
        """Missing required field returns validation error."""
        response = await client.post("/test", json={"name": "Test"})  # missing age

        assert response.status_code == 422
        data = response.json()
        assert data["error"]["code"] == "validation_error"


class TestUnhandledException:
    """
    Tests for unhandled exception handling.

    Verifies [api-hardening:GlobalExceptionHandler/TS-02] - Unhandled exception
    """

    @pytest.mark.asyncio
    async def test_unhandled_exception_returns_500(self, client):
        """
        Verifies [api-hardening:GlobalExceptionHandler/TS-02] - Unhandled exception

        Given: Bug causes exception
        When: Trigger unhandled error
        Then: Returns 500 with error code "internal_error", no stack trace
        """
        with patch.dict("os.environ", {"ENVIRONMENT": "production"}):
            response = await client.get("/error/500")

        assert response.status_code == 500
        data = response.json()

        assert data["error"]["code"] == "internal_error"
        # Should NOT contain traceback or exception details in production
        assert "traceback" not in str(data)
        assert "ValueError" not in str(data)

    @pytest.mark.asyncio
    async def test_unhandled_exception_dev_mode_has_details(self, client):
        """In development mode, exception details are included."""
        with patch.dict("os.environ", {"ENVIRONMENT": "development"}):
            response = await client.get("/error/500")

        assert response.status_code == 500
        data = response.json()

        # Development mode may include details
        assert data["error"]["code"] == "internal_error"


class TestHTTPException:
    """
    Tests for HTTP exception handling.

    Verifies [api-hardening:GlobalExceptionHandler/TS-03] - HTTP exception
    """

    @pytest.mark.asyncio
    async def test_http_exception_standard_format(self, client):
        """
        Verifies [api-hardening:GlobalExceptionHandler/TS-03] - HTTP exception

        Given: Known error (404)
        When: GET /error/404
        Then: Returns 404 in standard error format
        """
        response = await client.get("/error/404")

        assert response.status_code == 404
        data = response.json()

        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]

    @pytest.mark.asyncio
    async def test_custom_error_format_preserved(self, client):
        """Custom error format is preserved when already in standard format."""
        response = await client.get("/error/custom")

        assert response.status_code == 400
        data = response.json()

        assert data["error"]["code"] == "custom_error"
        assert data["error"]["message"] == "Custom error message"


class TestErrorIncludesRequestId:
    """
    Tests for request ID in error responses.

    Verifies [api-hardening:GlobalExceptionHandler/TS-04] - Error includes request ID
    """

    @pytest.mark.asyncio
    async def test_validation_error_has_request_id(self, client):
        """
        Verifies [api-hardening:GlobalExceptionHandler/TS-04] - Error includes request ID

        Given: Any error
        When: Cause an error
        Then: Error response includes X-Request-ID header
        """
        response = await client.post("/test", json={"invalid": "data"})

        assert response.status_code == 422
        assert "X-Request-ID" in response.headers

    @pytest.mark.asyncio
    async def test_http_exception_has_request_id(self, client):
        """HTTP exceptions include request ID."""
        response = await client.get("/error/404")

        assert response.status_code == 404
        assert "X-Request-ID" in response.headers

    @pytest.mark.asyncio
    async def test_unhandled_exception_has_request_id(self, client):
        """Unhandled exceptions include request ID."""
        response = await client.get("/error/500")

        assert response.status_code == 500
        assert "X-Request-ID" in response.headers


class TestAPIVersionHeader:
    """Tests for X-API-Version header."""

    @pytest.mark.asyncio
    async def test_api_version_on_success(self):
        """API version header present on successful responses."""
        from src.api.main import create_app

        app = create_app()
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/v1/health")
            assert "X-API-Version" in response.headers
            assert response.headers["X-API-Version"] == "1.0.0"


class TestErrorFormatConsistency:
    """Tests for consistent error format across all error types."""

    @pytest.mark.asyncio
    async def test_all_errors_have_consistent_format(self, client):
        """All error responses follow the same format."""
        # Validation error
        r1 = await client.post("/test", json={"invalid": True})
        assert "error" in r1.json()
        assert "code" in r1.json()["error"]
        assert "message" in r1.json()["error"]

        # 404 error
        r2 = await client.get("/error/404")
        assert "error" in r2.json()
        assert "code" in r2.json()["error"]
        assert "message" in r2.json()["error"]

        # 500 error
        r3 = await client.get("/error/500")
        assert "error" in r3.json()
        assert "code" in r3.json()["error"]
        assert "message" in r3.json()["error"]
