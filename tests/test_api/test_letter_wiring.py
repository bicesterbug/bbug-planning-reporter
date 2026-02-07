"""
Tests for letter feature wiring — router registration and worker function registration.

Implements test scenarios:
- [response-letter:AppRouter/TS-01] - Letters router registered
- [response-letter:WorkerSettings/TS-01] - Letter job registered
- [response-letter:DockerCompose/TS-01] - Env vars present
"""

import yaml

from src.api.main import app
from src.worker.main import WorkerSettings


class TestAppRouterRegistration:
    """Tests for letters router registration."""

    def test_letters_routes_registered(self) -> None:
        """
        Verifies [response-letter:AppRouter/TS-01]

        Given: App is created
        When: Routes are inspected
        Then: /api/v1/letters/ and /api/v1/reviews/{review_id}/letter routes exist
        """
        route_paths = [route.path for route in app.routes]

        assert "/api/v1/reviews/{review_id}/letter" in route_paths
        assert "/api/v1/letters/{letter_id}" in route_paths


class TestWorkerSettingsRegistration:
    """Tests for worker function registration."""

    def test_letter_job_registered(self) -> None:
        """
        Verifies [response-letter:WorkerSettings/TS-01]

        Given: Worker starts
        When: Functions list is inspected
        Then: letter_job is in the list
        """
        function_names = [f.__name__ for f in WorkerSettings.functions]
        assert "letter_job" in function_names


class TestDockerComposeEnvVars:
    """Tests for docker-compose environment variables."""

    def test_advocacy_group_env_vars_in_compose(self) -> None:
        """
        Verifies [response-letter:DockerCompose/TS-01]

        Given: Docker compose config is read
        When: Worker and API environment is inspected
        Then: ADVOCACY_GROUP_NAME, ADVOCACY_GROUP_STYLISED, ADVOCACY_GROUP_SHORT are defined
        """
        with open("docker-compose.yml") as f:
            compose = yaml.safe_load(f)

        # Check worker service
        worker_env = compose["services"]["worker"]["environment"]
        worker_env_names = [e.split("=")[0].lstrip("- ") for e in worker_env]
        assert "ADVOCACY_GROUP_NAME" in worker_env_names
        assert "ADVOCACY_GROUP_STYLISED" in worker_env_names
        assert "ADVOCACY_GROUP_SHORT" in worker_env_names

        # Check api service
        api_env = compose["services"]["api"]["environment"]
        api_env_names = [e.split("=")[0].lstrip("- ") for e in api_env]
        assert "ADVOCACY_GROUP_NAME" in api_env_names
        assert "ADVOCACY_GROUP_STYLISED" in api_env_names
        assert "ADVOCACY_GROUP_SHORT" in api_env_names
