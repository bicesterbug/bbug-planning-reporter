"""
Tests for MCP server input schemas.

Verifies [document-filtering:DownloadAllDocumentsInput/TS-01] through TS-03
"""

import pytest
from pydantic import ValidationError

from src.mcp_servers.cherwell_scraper.server import DownloadAllDocumentsInput


class TestDownloadAllDocumentsInput:
    """Tests for DownloadAllDocumentsInput schema."""

    def test_default_skip_filter_is_false(self):
        """
        Verifies [document-filtering:DownloadAllDocumentsInput/TS-01] - Default skip_filter is false

        Given: Input JSON without skip_filter field
        When: Model is instantiated
        Then: skip_filter field defaults to false
        """
        input_data = {
            "application_ref": "25/01178/REM",
            "output_dir": "/data/raw",
        }

        model = DownloadAllDocumentsInput(**input_data)

        assert model.skip_filter is False

    def test_explicit_skip_filter_true(self):
        """
        Verifies [document-filtering:DownloadAllDocumentsInput/TS-02] - Explicit skip_filter=true

        Given: Input JSON with skip_filter: true
        When: Model is instantiated
        Then: skip_filter field is true
        """
        input_data = {
            "application_ref": "25/01178/REM",
            "output_dir": "/data/raw",
            "skip_filter": True,
        }

        model = DownloadAllDocumentsInput(**input_data)

        assert model.skip_filter is True

    def test_explicit_skip_filter_false(self):
        """
        Test explicit skip_filter=false.

        Given: Input JSON with skip_filter: false
        When: Model is instantiated
        Then: skip_filter field is false
        """
        input_data = {
            "application_ref": "25/01178/REM",
            "output_dir": "/data/raw",
            "skip_filter": False,
        }

        model = DownloadAllDocumentsInput(**input_data)

        assert model.skip_filter is False

    def test_invalid_skip_filter_value(self):
        """
        Verifies [document-filtering:DownloadAllDocumentsInput/TS-03] - Invalid skip_filter value

        Given: Input JSON with skip_filter: {} (dict instead of boolean)
        When: Model is instantiated
        Then: Pydantic validation error raised

        Note: Pydantic coerces many values to bool (strings, ints), so we use
        a dict which cannot be coerced.
        """
        input_data = {
            "application_ref": "25/01178/REM",
            "output_dir": "/data/raw",
            "skip_filter": {},  # Dict cannot be coerced to bool
        }

        with pytest.raises(ValidationError) as exc_info:
            DownloadAllDocumentsInput(**input_data)

        # Check that the error is about skip_filter field
        errors = exc_info.value.errors()
        assert len(errors) > 0
        assert any(err["loc"] == ("skip_filter",) for err in errors)

    def test_skip_filter_with_integer_coercion(self):
        """
        Test Pydantic's coercion behavior.

        Given: Input JSON with skip_filter: 1 (integer)
        When: Model is instantiated
        Then: Integer is coerced to boolean True
        """
        input_data = {
            "application_ref": "25/01178/REM",
            "output_dir": "/data/raw",
            "skip_filter": 1,
        }

        model = DownloadAllDocumentsInput(**input_data)

        assert model.skip_filter is True

    def test_skip_filter_with_zero_coercion(self):
        """
        Test Pydantic's coercion behavior for zero.

        Given: Input JSON with skip_filter: 0 (integer)
        When: Model is instantiated
        Then: Integer 0 is coerced to boolean False
        """
        input_data = {
            "application_ref": "25/01178/REM",
            "output_dir": "/data/raw",
            "skip_filter": 0,
        }

        model = DownloadAllDocumentsInput(**input_data)

        assert model.skip_filter is False

    def test_all_fields_present(self):
        """
        Test complete input with all fields.

        Given: Input with all fields including skip_filter
        When: Model is instantiated
        Then: All fields are correctly set
        """
        input_data = {
            "application_ref": "23/00123/F",
            "output_dir": "/tmp/docs",
            "skip_filter": True,
        }

        model = DownloadAllDocumentsInput(**input_data)

        assert model.application_ref == "23/00123/F"
        assert model.output_dir == "/tmp/docs"
        assert model.skip_filter is True
