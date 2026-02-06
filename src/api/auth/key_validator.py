"""
API Key Validator service.

Implements [api-hardening:FR-002] - Validate keys against environment variable or keys file
Implements [api-hardening:NFR-002] - Secure key storage

Implements test scenarios:
- [api-hardening:APIKeyValidator/TS-01] Load from environment
- [api-hardening:APIKeyValidator/TS-02] Load from JSON file
- [api-hardening:APIKeyValidator/TS-03] Validate valid key
- [api-hardening:APIKeyValidator/TS-04] Validate invalid key
- [api-hardening:APIKeyValidator/TS-05] Empty key rejected
- [api-hardening:APIKeyValidator/TS-06] Environment takes precedence
"""

import json
import os
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


class APIKeyValidator:
    """
    Validates API keys against a configured list.

    Supports loading keys from:
    1. Environment variable API_KEYS (comma-separated)
    2. JSON file at API_KEYS_FILE path

    Environment variable takes precedence over file.
    """

    def __init__(
        self,
        env_var: str = "API_KEYS",
        file_path_env: str = "API_KEYS_FILE",
        keys: set[str] | None = None,
    ) -> None:
        """
        Initialize the API key validator.

        Args:
            env_var: Environment variable name for comma-separated keys.
            file_path_env: Environment variable name for JSON file path.
            keys: Optional set of keys to use directly (for testing).
        """
        self._env_var = env_var
        self._file_path_env = file_path_env

        if keys is not None:
            self._keys = keys
        else:
            self._keys = self._load_keys()

        logger.info(
            "APIKeyValidator initialized",
            key_count=len(self._keys),
            source="direct" if keys else self._get_source(),
        )

    def _get_source(self) -> str:
        """Get the source of the keys for logging."""
        if os.getenv(self._env_var):
            return "environment"
        elif os.getenv(self._file_path_env):
            return "file"
        return "none"

    def _load_keys(self) -> set[str]:
        """
        Load API keys from environment or file.

        Environment variable takes precedence over file.
        """
        # Try environment variable first
        env_keys = os.getenv(self._env_var)
        if env_keys:
            keys = {k.strip() for k in env_keys.split(",") if k.strip()}
            logger.debug("Loaded API keys from environment", count=len(keys))
            return keys

        # Try JSON file
        file_path = os.getenv(self._file_path_env)
        if file_path:
            return self._load_from_file(file_path)

        logger.warning("No API keys configured")
        return set()

    def _load_from_file(self, file_path: str) -> set[str]:
        """Load keys from a JSON file."""
        path = Path(file_path)
        if not path.exists():
            logger.warning("API keys file not found", path=file_path)
            return set()

        try:
            with open(path) as f:
                data = json.load(f)

            # Support both list and {"keys": [...]} format
            if isinstance(data, list):
                keys = set(data)
            elif isinstance(data, dict) and "keys" in data:
                keys = set(data["keys"])
            else:
                logger.error("Invalid API keys file format", path=file_path)
                return set()

            logger.debug("Loaded API keys from file", count=len(keys), path=file_path)
            return keys

        except json.JSONDecodeError as e:
            logger.error("Failed to parse API keys file", path=file_path, error=str(e))
            return set()
        except OSError as e:
            logger.error("Failed to read API keys file", path=file_path, error=str(e))
            return set()

    def validate(self, key: str) -> bool:
        """
        Validate an API key.

        Args:
            key: The API key to validate.

        Returns:
            True if the key is valid, False otherwise.
        """
        # Empty key is always invalid
        if not key or not key.strip():
            return False

        return key in self._keys

    def reload(self) -> None:
        """Reload keys from configured source."""
        self._keys = self._load_keys()
        logger.info("API keys reloaded", count=len(self._keys))

    @property
    def key_count(self) -> int:
        """Get the number of configured keys."""
        return len(self._keys)
