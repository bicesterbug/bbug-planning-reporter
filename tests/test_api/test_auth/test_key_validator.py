"""
Tests for APIKeyValidator.

Verifies [api-hardening:FR-002] - API key validation
Verifies [api-hardening:NFR-002] - Secure key storage
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

from src.api.auth.key_validator import APIKeyValidator


class TestAPIKeyValidatorFromEnvironment:
    """
    Tests for loading API keys from environment variable.

    Verifies [api-hardening:APIKeyValidator/TS-01] - Load from environment
    """

    def test_load_single_key_from_environment(self):
        """
        Verifies [api-hardening:APIKeyValidator/TS-01] - Load from environment

        Given: API_KEYS="sk-cycle-test-123" in environment
        When: Initialize validator
        Then: One key available for validation
        """
        with patch.dict(os.environ, {"API_KEYS": "sk-cycle-test-123"}):
            validator = APIKeyValidator()
            assert validator.key_count == 1
            assert validator.validate("sk-cycle-test-123") is True

    def test_load_multiple_keys_from_environment(self):
        """
        Verifies [api-hardening:APIKeyValidator/TS-01] - Load from environment

        Given: API_KEYS="key1,key2,key3" in environment
        When: Initialize validator
        Then: Three keys available for validation
        """
        with patch.dict(os.environ, {"API_KEYS": "key1,key2,key3"}):
            validator = APIKeyValidator()
            assert validator.key_count == 3
            assert validator.validate("key1") is True
            assert validator.validate("key2") is True
            assert validator.validate("key3") is True

    def test_handles_whitespace_in_environment(self):
        """
        Given: API_KEYS=" key1 , key2 " with whitespace
        When: Initialize validator
        Then: Keys are trimmed and valid
        """
        with patch.dict(os.environ, {"API_KEYS": " key1 , key2 "}):
            validator = APIKeyValidator()
            assert validator.key_count == 2
            assert validator.validate("key1") is True
            assert validator.validate("key2") is True


class TestAPIKeyValidatorFromFile:
    """
    Tests for loading API keys from JSON file.

    Verifies [api-hardening:APIKeyValidator/TS-02] - Load from JSON file
    """

    def test_load_keys_from_json_list(self, tmp_path: Path):
        """
        Verifies [api-hardening:APIKeyValidator/TS-02] - Load from JSON file

        Given: /config/api_keys.json exists with list format
        When: Initialize validator
        Then: Keys loaded from file
        """
        keys_file = tmp_path / "api_keys.json"
        keys_file.write_text(json.dumps(["sk-cycle-1", "sk-cycle-2"]))

        with patch.dict(os.environ, {"API_KEYS_FILE": str(keys_file)}, clear=True):
            # Clear API_KEYS to force file loading
            os.environ.pop("API_KEYS", None)
            validator = APIKeyValidator()
            assert validator.key_count == 2
            assert validator.validate("sk-cycle-1") is True
            assert validator.validate("sk-cycle-2") is True

    def test_load_keys_from_json_dict(self, tmp_path: Path):
        """
        Given: api_keys.json with {"keys": [...]} format
        When: Initialize validator
        Then: Keys loaded from file
        """
        keys_file = tmp_path / "api_keys.json"
        keys_file.write_text(json.dumps({"keys": ["key1", "key2"]}))

        with patch.dict(os.environ, {"API_KEYS_FILE": str(keys_file)}, clear=True):
            os.environ.pop("API_KEYS", None)
            validator = APIKeyValidator()
            assert validator.key_count == 2

    def test_handles_missing_file(self, tmp_path: Path):
        """
        Given: API_KEYS_FILE points to non-existent file
        When: Initialize validator
        Then: No keys loaded (empty set)
        """
        with patch.dict(
            os.environ, {"API_KEYS_FILE": str(tmp_path / "missing.json")}, clear=True
        ):
            os.environ.pop("API_KEYS", None)
            validator = APIKeyValidator()
            assert validator.key_count == 0

    def test_handles_invalid_json(self, tmp_path: Path):
        """
        Given: API_KEYS_FILE contains invalid JSON
        When: Initialize validator
        Then: No keys loaded
        """
        keys_file = tmp_path / "api_keys.json"
        keys_file.write_text("not valid json")

        with patch.dict(os.environ, {"API_KEYS_FILE": str(keys_file)}, clear=True):
            os.environ.pop("API_KEYS", None)
            validator = APIKeyValidator()
            assert validator.key_count == 0


class TestAPIKeyValidation:
    """
    Tests for key validation logic.

    Verifies [api-hardening:APIKeyValidator/TS-03] through [api-hardening:APIKeyValidator/TS-05]
    """

    def test_validate_valid_key(self):
        """
        Verifies [api-hardening:APIKeyValidator/TS-03] - Validate valid key

        Given: Key "sk-cycle-test" in list
        When: Call validate("sk-cycle-test")
        Then: Returns True
        """
        validator = APIKeyValidator(keys={"sk-cycle-test"})
        assert validator.validate("sk-cycle-test") is True

    def test_validate_invalid_key(self):
        """
        Verifies [api-hardening:APIKeyValidator/TS-04] - Validate invalid key

        Given: Key "invalid" not in list
        When: Call validate("invalid")
        Then: Returns False
        """
        validator = APIKeyValidator(keys={"sk-cycle-test"})
        assert validator.validate("invalid") is False

    def test_empty_key_rejected(self):
        """
        Verifies [api-hardening:APIKeyValidator/TS-05] - Empty key rejected

        Given: Empty string
        When: Call validate("")
        Then: Returns False
        """
        validator = APIKeyValidator(keys={"sk-cycle-test"})
        assert validator.validate("") is False
        assert validator.validate("   ") is False

    def test_none_key_rejected(self):
        """
        Given: None value
        When: Call validate with None-like behavior
        Then: Returns False
        """
        validator = APIKeyValidator(keys={"sk-cycle-test"})
        # Simulate None as empty string
        assert validator.validate("") is False


class TestEnvironmentPrecedence:
    """
    Tests for environment variable taking precedence over file.

    Verifies [api-hardening:APIKeyValidator/TS-06] - Environment takes precedence
    """

    def test_environment_takes_precedence_over_file(self, tmp_path: Path):
        """
        Verifies [api-hardening:APIKeyValidator/TS-06] - Environment takes precedence

        Given: Both env and file configured
        When: Initialize validator
        Then: Uses environment variable
        """
        keys_file = tmp_path / "api_keys.json"
        keys_file.write_text(json.dumps(["file-key-1", "file-key-2"]))

        with patch.dict(
            os.environ,
            {"API_KEYS": "env-key-1", "API_KEYS_FILE": str(keys_file)},
        ):
            validator = APIKeyValidator()
            # Should have env key, not file keys
            assert validator.validate("env-key-1") is True
            assert validator.validate("file-key-1") is False
            assert validator.key_count == 1


class TestKeyReload:
    """Tests for key reloading functionality."""

    def test_reload_updates_keys(self):
        """
        Given: Validator initialized with keys
        When: Reload called with new environment
        Then: New keys are loaded
        """
        with patch.dict(os.environ, {"API_KEYS": "old-key"}):
            validator = APIKeyValidator()
            assert validator.validate("old-key") is True
            assert validator.validate("new-key") is False

        with patch.dict(os.environ, {"API_KEYS": "new-key"}):
            validator.reload()
            assert validator.validate("old-key") is False
            assert validator.validate("new-key") is True
