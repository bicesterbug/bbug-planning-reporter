# Design: CI/CD GitHub Actions

**Version:** 1.0
**Date:** 2026-02-08
**Status:** Approved
**Linked Specification** `.sdd/ci-cd-github-actions/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context
- No CI/CD exists. Tests and builds are run manually on developer machines.
- The project uses pytest + ruff for testing/linting, configured in `pyproject.toml`.
- Docker images are built locally via `docker build -t cherwell-base:latest -f docker/Dockerfile.base .` followed by `docker compose build`.
- All 6 service Dockerfiles use `FROM cherwell-base:latest` — the base image must be built first.
- The GitHub repository is at `bicesterbug/bbug-planning-reporter`.

### Proposed Architecture
Two independent GitHub Actions workflow files:

1. **PR Workflow** (`.github/workflows/pr-checks.yml`) — Triggered on pull requests to `main`. Runs lint + test in a single job with Python 3.12, fakeredis for Redis mocking, and pip caching.

2. **Release Workflow** (`.github/workflows/release-build.yml`) — Triggered on `v*` tag push. Builds `cherwell-base` first, then builds all 6 service images in parallel using a matrix strategy, pushing to `ghcr.io`.

Sequence for PR workflow:
```
PR opened → checkout → setup python → cache pip → install deps → ruff check → pytest
```

Sequence for release workflow:
```
Tag pushed → checkout → setup buildx → login ghcr.io → build base image
           → matrix[api, worker, scraper, document-store, policy-kb, policy-init]
             → build + push service image (using base from previous step)
```

### Technology Decisions
- **GitHub-hosted runners** (`ubuntu-latest`) — no infrastructure to manage.
- **`actions/cache`** for pip dependency caching, keyed on `pyproject.toml` hash.
- **`docker/build-push-action`** with buildx for multi-stage builds and push in a single step.
- **`ghcr.io`** — free for public repos, integrated with GitHub permissions via `GITHUB_TOKEN`.
- **Matrix strategy** for parallel service image builds after base completes.

### Quality Attributes
- **Speed**: Pip caching avoids re-downloading ~2GB of dependencies (PyTorch CPU, sentence-transformers). Service builds run in parallel.
- **Maintainability**: Two small, focused workflow files. Only official GitHub Actions used. Versions pinned to major.

---

## API Design

N/A — No public interfaces. These are GitHub Actions workflow files (YAML configuration).

---

## Modified Components

None — all components are new additions.

---

## Added Components

### PR Checks Workflow
**Description** GitHub Actions workflow that runs on pull requests to `main`. Performs linting with ruff and runs the pytest suite (excluding integration/e2e markers).

**Users** GitHub — triggered automatically on PR events

**Kind** Workflow file (YAML)

**Location** `.github/workflows/pr-checks.yml`

**Requirements References**
- [ci-cd-github-actions:FR-001]: Runs full pytest suite on PR
- [ci-cd-github-actions:FR-002]: Runs ruff check on PR
- [ci-cd-github-actions:FR-006]: Test results visible in Actions UI
- [ci-cd-github-actions:NFR-001]: Must complete under 10 minutes
- [ci-cd-github-actions:NFR-002]: Caches pip dependencies
- [ci-cd-github-actions:NFR-003]: No secrets required
- [ci-cd-github-actions:NFR-004]: Only official GitHub Actions

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| PRWorkflow/TS-01 | Tests pass on clean PR | A PR with passing tests and clean lint | Workflow runs | Both lint and test steps succeed, check goes green |
| PRWorkflow/TS-02 | Lint failure blocks merge | A PR with an unused import | Workflow runs | Ruff step fails, overall check goes red |
| PRWorkflow/TS-03 | Test failure blocks merge | A PR with a failing test | Workflow runs | Pytest step fails, overall check goes red |
| PRWorkflow/TS-04 | Integration tests excluded | A PR exists | Workflow runs pytest | Only unit tests run (integration/e2e markers excluded via `-m "not integration and not e2e"`) |
| PRWorkflow/TS-05 | Pip cache used on repeat run | `pyproject.toml` unchanged between runs | Workflow runs | Cache hit reported in logs, install step is faster |

### Release Build Workflow
**Description** GitHub Actions workflow that runs on `v*` tag pushes. Builds the base Docker image, then builds all 6 service images in parallel using a matrix strategy, and pushes them to ghcr.io.

**Users** GitHub — triggered automatically on tag push

**Kind** Workflow file (YAML)

**Location** `.github/workflows/release-build.yml`

**Requirements References**
- [ci-cd-github-actions:FR-003]: Builds all images on tag push
- [ci-cd-github-actions:FR-004]: Base image built first, services depend on it
- [ci-cd-github-actions:FR-005]: Images tagged with version and conditionally `latest`
- [ci-cd-github-actions:NFR-003]: Uses only GITHUB_TOKEN
- [ci-cd-github-actions:NFR-004]: Only official GitHub/Docker Actions

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| ReleaseBuild/TS-01 | Full release tag builds all images | Tag `v1.0.0` pushed to main | Workflow runs | Base + 6 service images built and pushed to ghcr.io with `:v1.0.0` and `:latest` tags |
| ReleaseBuild/TS-02 | Pre-release tag skips latest | Tag `v1.0.0-rc1` pushed | Workflow runs | Images tagged `:v1.0.0-rc1` only, no `:latest` tag |
| ReleaseBuild/TS-03 | Base failure stops services | Base image build fails | Service build jobs check | Service build jobs are skipped (depend on base job via `needs`) |
| ReleaseBuild/TS-04 | Service images built in parallel | Base image build succeeds | Service matrix runs | All 6 services build concurrently as separate matrix entries |

---

## Used Components

### pyproject.toml
**Location** `pyproject.toml`

**Provides** pytest configuration (testpaths, markers, addopts), ruff configuration, dependency list for pip install

**Used By** PR Checks Workflow — `pip install -e ".[dev]"`, `ruff check`, `pytest`

### Docker/Dockerfile.base
**Location** `docker/Dockerfile.base`

**Provides** Base image with system deps (tesseract, poppler, weasyprint libs) and all Python packages

**Used By** Release Build Workflow — built first, then used as `FROM` for all service images

### Service Dockerfiles
**Location** `docker/Dockerfile.api`, `docker/Dockerfile.worker`, `docker/Dockerfile.scraper`, `docker/Dockerfile.document-store`, `docker/Dockerfile.policy-kb`, `docker/Dockerfile.policy-init`

**Provides** Service-specific CMD and HEALTHCHECK layered on the base image

**Used By** Release Build Workflow — each built as a separate matrix entry

---

## Documentation Considerations
- None required — workflow files are self-documenting via comments and GitHub Actions UI.

---

## Instrumentation (if needed)

| Requirement | Observability Criteria | Implementation | Component |
|-------------|------------------------|----------------|-----------|
| [ci-cd-github-actions:NFR-001] | Workflow duration under 10 minutes | GitHub Actions UI shows duration per run | PRWorkflow |
| [ci-cd-github-actions:NFR-002] | Cache hit/miss visible | `actions/cache` reports hit/miss in step output | PRWorkflow |

---

## Integration Test Scenarios (if needed)

N/A — GitHub Actions workflows cannot be integration-tested locally. Verification is by running the actual workflows on GitHub.

---

## E2E Test Scenarios (if needed)

| ID | Scenario | Given | When | Then | User Journey |
|----|----------|-------|------|------|--------------|
| E2E-01 | PR workflow runs on real PR | Workflow files committed to main | A PR is opened | Lint and test checks appear on the PR and pass/fail correctly | Push branch → open PR → observe checks → see results |
| E2E-02 | Release workflow builds on real tag | Workflow files committed to main | A `v*` tag is pushed | All 7 images appear in ghcr.io with correct tags | Push tag → observe workflow → verify images in registry |

---

## Test Data
- No test data required. Workflows use the existing test suite and Dockerfiles.

---

## Test Feasibility
- GitHub Actions workflows can only be truly tested by pushing them to GitHub and triggering them. There is no local simulation that fully replicates the GitHub Actions environment.
- Verification: Manual — push the workflow files, open a test PR, push a test tag, observe results.

---

## Risks and Dependencies
- **Risk**: Base image build is slow (~3 minutes) due to system packages and PyTorch CPU. **Mitigation**: Acceptable for release builds which are infrequent; could add Docker layer caching in future.
- **Risk**: PyTorch CPU wheel download is large (~200MB) and may slow pip install. **Mitigation**: pip cache between runs via `actions/cache`.
- **Risk**: sentence-transformers model download during tests. **Mitigation**: Tests mock external dependencies; model download only needed for integration tests (excluded).
- **Dependency**: `ghcr.io` access requires the repository to have packages enabled (default for GitHub repos).
- **Dependency**: Service Dockerfiles reference `cherwell-base:latest` — the release workflow must make the base image available to subsequent jobs.

---

## Feasability Review
- No blockers. All required infrastructure (GitHub Actions, ghcr.io) is available by default for GitHub repositories.

---

## Task Breakdown

> **CRITICAL: Tests are written WITH implementation, not after.**
> For this feature, "tests" means manual E2E verification since workflows can only be tested by running them on GitHub.

### Phase 1: Create workflow files

- Task 1: Create PR checks workflow
  - Status: Done
  - Create `.github/workflows/pr-checks.yml` with lint + test steps, pip caching, Python 3.12 setup
  - Requirements: [ci-cd-github-actions:FR-001], [ci-cd-github-actions:FR-002], [ci-cd-github-actions:FR-006], [ci-cd-github-actions:NFR-001], [ci-cd-github-actions:NFR-002], [ci-cd-github-actions:NFR-003], [ci-cd-github-actions:NFR-004]
  - Test Scenarios: [ci-cd-github-actions:PRWorkflow/TS-01], [ci-cd-github-actions:PRWorkflow/TS-02], [ci-cd-github-actions:PRWorkflow/TS-03], [ci-cd-github-actions:PRWorkflow/TS-04], [ci-cd-github-actions:PRWorkflow/TS-05]

- Task 2: Create release build workflow
  - Status: Done
  - Create `.github/workflows/release-build.yml` with base image build job + service matrix build job, ghcr.io login and push, version + latest tagging logic
  - Requirements: [ci-cd-github-actions:FR-003], [ci-cd-github-actions:FR-004], [ci-cd-github-actions:FR-005], [ci-cd-github-actions:NFR-003], [ci-cd-github-actions:NFR-004]
  - Test Scenarios: [ci-cd-github-actions:ReleaseBuild/TS-01], [ci-cd-github-actions:ReleaseBuild/TS-02], [ci-cd-github-actions:ReleaseBuild/TS-03], [ci-cd-github-actions:ReleaseBuild/TS-04]

- Task 3: Verify workflows via E2E
  - Status: Backlog
  - Push to GitHub, open a test PR to verify PR workflow, push a test tag to verify release workflow. Manual verification.
  - Requirements: All FRs and NFRs
  - Test Scenarios: [ci-cd-github-actions:E2E-01], [ci-cd-github-actions:E2E-02]

---

## Intermediate Dead Code Tracking

N/A — no dead code expected.

---

## Intermediate Stub Tracking

N/A — no stubs expected.

---

## Requirements Validation

- [ci-cd-github-actions:FR-001]: Phase 1 Task 1, Phase 1 Task 3
- [ci-cd-github-actions:FR-002]: Phase 1 Task 1, Phase 1 Task 3
- [ci-cd-github-actions:FR-003]: Phase 1 Task 2, Phase 1 Task 3
- [ci-cd-github-actions:FR-004]: Phase 1 Task 2, Phase 1 Task 3
- [ci-cd-github-actions:FR-005]: Phase 1 Task 2, Phase 1 Task 3
- [ci-cd-github-actions:FR-006]: Phase 1 Task 1, Phase 1 Task 3
- [ci-cd-github-actions:NFR-001]: Phase 1 Task 1
- [ci-cd-github-actions:NFR-002]: Phase 1 Task 1
- [ci-cd-github-actions:NFR-003]: Phase 1 Task 1, Phase 1 Task 2
- [ci-cd-github-actions:NFR-004]: Phase 1 Task 1, Phase 1 Task 2

---

## Test Scenario Validation

### Component Scenarios
- [ci-cd-github-actions:PRWorkflow/TS-01]: Phase 1 Task 1
- [ci-cd-github-actions:PRWorkflow/TS-02]: Phase 1 Task 1
- [ci-cd-github-actions:PRWorkflow/TS-03]: Phase 1 Task 1
- [ci-cd-github-actions:PRWorkflow/TS-04]: Phase 1 Task 1
- [ci-cd-github-actions:PRWorkflow/TS-05]: Phase 1 Task 1
- [ci-cd-github-actions:ReleaseBuild/TS-01]: Phase 1 Task 2
- [ci-cd-github-actions:ReleaseBuild/TS-02]: Phase 1 Task 2
- [ci-cd-github-actions:ReleaseBuild/TS-03]: Phase 1 Task 2
- [ci-cd-github-actions:ReleaseBuild/TS-04]: Phase 1 Task 2

### Integration Scenarios
N/A

### E2E Scenarios
- [ci-cd-github-actions:E2E-01]: Phase 1 Task 3
- [ci-cd-github-actions:E2E-02]: Phase 1 Task 3

---

## Appendix

### Glossary
- **ghcr.io**: GitHub Container Registry
- **buildx**: Docker CLI plugin for extended build capabilities including multi-platform and cache exports
- **matrix strategy**: GitHub Actions feature to run the same job multiple times with different parameters

### References
- [GitHub Actions: workflow syntax](https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions)
- [docker/build-push-action](https://github.com/docker/build-push-action)
- [GitHub Packages: Container registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-08 | Claude | Initial design |
