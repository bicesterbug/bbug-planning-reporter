# Specification: CI/CD GitHub Actions

**Version:** 1.0
**Date:** 2026-02-08
**Status:** Approved

---

## Problem Statement

The project has no automated CI/CD pipeline. Tests are only run locally, meaning regressions can be merged undetected. Docker images are built manually on a developer machine, making deployments ad-hoc and unreproducible.

## Beneficiaries

**Primary:**
- Developers — automated test feedback on every PR, confidence in merges

**Secondary:**
- Deployers — tagged releases produce ready-to-use container images from a known-good commit

---

## Outcomes

**Must Haves**
- Every pull request to `main` runs the full test suite automatically and blocks merge on failure
- Pushing a version tag (e.g. `v1.2.3`) to `main` builds all Docker container images and pushes them to GitHub Container Registry (ghcr.io)

**Nice-to-haves**
- Linting (ruff) runs as part of the PR workflow
- Build status badges in README

---

## Explicitly Out of Scope

- Automated deployment to any hosting environment (this is build-only, not deploy)
- E2E tests requiring live external services (Cherwell portal, Anthropic API)
- Self-hosted runners — use GitHub-hosted runners only
- Docker Compose-based integration tests (these require inter-service networking not available in CI)
- MyPy type checking in CI (currently too many untyped third-party deps to pass cleanly)
- Secrets rotation or management beyond what GitHub Actions provides natively

---

## Functional Requirements

### FR-001: PR Test Workflow
**Description:** When a pull request is opened, synchronised, or reopened targeting `main`, a GitHub Actions workflow must install dependencies, then run the full pytest suite (excluding tests marked `integration` or `e2e`).

**Examples:**
- Positive case: Developer opens a PR; workflow runs, all unit tests pass, check goes green
- Edge case: PR from a fork — workflow runs but does not have access to repository secrets (acceptable; tests don't need secrets)

### FR-002: PR Lint Workflow
**Description:** The same PR workflow must run `ruff check` against the codebase. Lint failures must be reported as a failing check.

**Examples:**
- Positive case: Clean code passes ruff, check is green
- Negative case: Unused import introduced — ruff fails, check goes red

### FR-003: Tag-Triggered Container Build Workflow
**Description:** When a tag matching the pattern `v*` (e.g. `v1.0.0`, `v0.2.1-rc1`) is pushed, a workflow must build all Docker images (base + 6 service images) and push them to GitHub Container Registry (`ghcr.io/bicesterbug/bbug-planning-reporter`).

**Examples:**
- Positive case: `git tag v1.0.0 && git push --tags` triggers build of all 7 images, each tagged with the version and `latest`
- Edge case: Tag `v1.0.0-rc1` — still triggers the workflow, images tagged with `v1.0.0-rc1` (no `latest` for pre-release)

### FR-004: Base Image Build Order
**Description:** The container build workflow must build the `cherwell-base` image first, since all 6 service Dockerfiles use `FROM cherwell-base:latest`. Service images must only build after the base image succeeds.

**Examples:**
- Positive case: Base builds, then all 6 services build in parallel using the freshly built base
- Negative case: Base build fails — no service images are attempted

### FR-005: Image Tagging
**Description:** Each built image must be tagged with the git tag version (e.g. `v1.0.0`) and, for non-pre-release tags, also with `latest`. Images must be named using the pattern `ghcr.io/bicesterbug/bbug-planning-reporter/<service>`.

**Examples:**
- Tag `v1.0.0` produces: `ghcr.io/bicesterbug/bbug-planning-reporter/api:v1.0.0` and `:latest`
- Tag `v1.0.0-rc1` produces: `ghcr.io/bicesterbug/bbug-planning-reporter/api:v1.0.0-rc1` only (no `latest`)

### FR-006: Test Results Reporting
**Description:** The PR workflow must produce a test summary visible in the GitHub Actions UI. Failed tests must be clearly identifiable from the workflow logs.

**Examples:**
- Positive case: pytest output with pass/fail counts visible in the workflow step log
- Edge case: All tests skip — workflow still succeeds (skips are not failures)

---

## Non-Functional Requirements

### NFR-001: CI Speed
**Category:** Performance
**Description:** The PR test-and-lint workflow must complete within a reasonable time for developer feedback.
**Acceptance Threshold:** PR workflow completes in under 10 minutes on GitHub-hosted runners.
**Verification:** Observability — check workflow run duration in GitHub Actions UI.

### NFR-002: Dependency Caching
**Category:** Performance
**Description:** Python dependencies must be cached between workflow runs to avoid re-downloading on every PR.
**Acceptance Threshold:** Subsequent runs with unchanged `pyproject.toml` use cached dependencies.
**Verification:** Observability — cache hit/miss reported in workflow logs.

### NFR-003: Secret Safety
**Category:** Security
**Description:** No secrets (API keys, tokens) must be required for the PR test workflow. The container build workflow must only use the `GITHUB_TOKEN` provided automatically by GitHub Actions (for ghcr.io push).
**Acceptance Threshold:** PR workflow runs with zero configured secrets. Container build uses only `GITHUB_TOKEN`.
**Verification:** Code review of workflow files.

### NFR-004: Maintainability
**Category:** Maintainability
**Description:** Workflow files must be self-contained and understandable. Action versions must be pinned to major versions (e.g. `actions/checkout@v4`) for reproducibility while still receiving patches.
**Acceptance Threshold:** No custom composite actions or external marketplace actions beyond the official GitHub-maintained set (`actions/checkout`, `actions/setup-python`, `actions/cache`, `docker/login-action`, `docker/build-push-action`, `docker/setup-buildx-action`).
**Verification:** Code review of workflow files.

___

## Open Questions

None — requirements are clear.

---

## Appendix

### Glossary
- **ghcr.io**: GitHub Container Registry — Docker image registry provided by GitHub
- **PR**: Pull Request
- **ruff**: Fast Python linter written in Rust, already configured in `pyproject.toml`

### References
- [GitHub Actions documentation](https://docs.github.com/en/actions)
- [GitHub Container Registry documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
- Project `pyproject.toml` — pytest, ruff, and dependency configuration
- Project `docker/` — 7 Dockerfiles (base + 6 services)

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-08 | Claude | Initial specification |
