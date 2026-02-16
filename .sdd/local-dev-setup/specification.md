# Specification: Local Development Setup Guide

**Version:** 1.0
**Date:** 2026-02-16
**Status:** Draft

---

## Problem Statement

New contributors who clone the repository have no documentation on how to run individual MCP servers locally (via Python or Docker), seed policy data, or connect the servers to Claude Code or Claude Desktop. The existing README covers only `docker compose up` with no detail on per-service setup, data dependencies, environment variables, or MCP client configuration. A contributor recently seeded the policy-kb container with their own data but could not connect to it because the required Redis dependency and correct transport URL were undocumented.

## Beneficiaries

**Primary:**
- Contributors cloning the repo who need to run, test, and debug services locally

**Secondary:**
- External users who want to connect the running MCP servers (Docker or production) to their own Claude Code or Claude Desktop instance

---

## Outcomes

**Must Haves**
- A contributor can go from fresh clone to a fully running local stack (API, worker, Redis, all four MCP servers) by following the guide
- A contributor can run any single MCP server via local Python for debugging, with correct environment variables and dependencies documented
- A contributor can connect any MCP server to Claude Code CLI and Claude Desktop using documented config files
- The guide clearly states the dependency chain: which services depend on Redis, ChromaDB, seeded data, or each other
- Policy seeding is documented as a distinct step with both Docker and local-Python instructions

**Nice-to-haves**
- Troubleshooting section covering the most common failure modes (Redis not running, wrong ChromaDB path, auth token mismatch, port conflicts)
- A quick-reference table of all environment variables, ports, and data paths

---

## Explicitly Out of Scope

- Production deployment instructions (already in `.sdd/project-guidelines.md` and deploy scripts)
- CI/CD pipeline setup (covered by `ci-cd-github-actions` spec)
- Writing or modifying application code (this is documentation only)
- Automated setup scripts or Makefiles (may be a follow-up)
- Documenting the review agent workflow or API usage (already in `docs/API.md`)

---

## Functional Requirements

**FR-001: Full-stack Docker Compose quick start**
- Description: The guide must document how to build images (`cherwell-base` first, then services) and start the full stack via Docker Compose, including health-check verification for every service.
- Acceptance criteria: A contributor following the steps can run `docker compose up -d`, verify all services are healthy, and hit the API health endpoint.
- Failure/edge cases: Base image not built first (build fails); external-drive volume paths don't exist on contributor's machine (document how to override).

**FR-002: Per-service local Python instructions**
- Description: For each of the four MCP servers (cherwell-scraper, document-store, policy-kb, cycle-route), the guide must document how to run it directly via `python -m` with the correct environment variables, noting which external services (Redis, ChromaDB directory) must be available.
- Acceptance criteria: A contributor can start any single MCP server locally, hit its `/health` endpoint, and see `{"status": "ok"}`.
- Failure/edge cases: Redis not running for policy-kb (document the error and fix); ChromaDB directory doesn't exist (document `mkdir`); port already in use (document how to override via env var).

**FR-003: Policy data seeding instructions**
- Description: The guide must document how to seed policy data via Docker (`docker compose run --rm policy-init`) and via local Python (`python -m src.scripts.seed_policies`), including required environment variables and the location of seed PDFs.
- Acceptance criteria: After seeding, `search_policy` returns results when queried via the MCP tool.
- Failure/edge cases: Seeding against different Redis/ChromaDB than the server reads from (document path consistency); re-running seeder is idempotent (document this).

**FR-004: Claude Code CLI MCP configuration**
- Description: The guide must provide a complete `.claude/mcp.json` file (project-level) that connects all four MCP servers, covering both Docker (localhost ports) and local-Python scenarios. Must document the `type: "url"` config with the `/mcp` streamable HTTP endpoint.
- Acceptance criteria: After adding the config and starting the servers, Claude Code's `/mcp` command lists the tools from all four servers.
- Failure/edge cases: MCP_API_KEY set on server but not in config headers (401 error — document how to add headers); servers not started (connection refused — document).

**FR-005: Claude Desktop MCP configuration**
- Description: The guide must provide a `claude_desktop_config.json` snippet for connecting all four MCP servers via streamable HTTP.
- Acceptance criteria: After adding the config and restarting Claude Desktop, tools from all four servers appear.
- Failure/edge cases: Same auth/connectivity issues as FR-004.

**FR-006: Dependency and port reference table**
- Description: The guide must include a quick-reference table listing each service, its port, its dependencies (Redis, ChromaDB, seed data, external APIs), the Python module to run it, and the relevant environment variables.
- Acceptance criteria: The table is present and accurate for all four MCP servers plus the API and worker.
- Failure/edge cases: Table becomes stale after code changes — document that it lives in `docs/LOCAL_DEV.md` and should be updated alongside service changes.

**FR-007: Environment variable reference**
- Description: The guide must document every environment variable needed for local development, with defaults, descriptions, and which services use them. This should supplement (not duplicate) `.env.example` by explaining what values to use for local-Python runs vs Docker.
- Acceptance criteria: A contributor can set up their environment for either Docker or local Python by consulting this section and `.env.example`.
- Failure/edge cases: Docker env vars (e.g. `redis://redis:6379`) differ from local Python (`redis://localhost:6379`) — document both.

**FR-008: Troubleshooting section**
- Description: The guide must include a troubleshooting section covering at least: Redis not running (policy-kb fails), ChromaDB path mismatch (empty search results), MCP_API_KEY mismatch (401 errors), port conflicts, base image not built, and volume path errors.
- Acceptance criteria: Each issue has a symptom, cause, and fix described.
- Failure/edge cases: N/A — this is reference documentation.

---

## QA Plan

**QA-01: Fresh clone to running stack**
- Goal: Validate that the Docker quick-start works end-to-end
- Steps:
  1. Clone the repo into a fresh directory
  2. Copy `.env.example` to `.env`, fill in `ANTHROPIC_API_KEY` and `API_KEYS`
  3. Follow the guide to build base image and start services
  4. Run `curl http://localhost:8080/api/v1/health`
  5. Run `curl http://localhost:3001/health` (cherwell-scraper)
  6. Run `curl http://localhost:3003/health` (policy-kb)
- Expected: All health checks return `{"status": "ok"}` or `{"status": "healthy"}`

**QA-02: Local Python policy-kb with seeding**
- Goal: Validate that policy-kb can be run and queried locally
- Steps:
  1. Start Redis via `docker compose up redis -d`
  2. Create a local ChromaDB directory
  3. Run the seeder via `python -m src.scripts.seed_policies` with documented env vars
  4. Start policy-kb server via `python -m src.mcp_servers.policy_kb.server`
  5. Verify `/health` returns OK
  6. Use Claude Code with the documented `.claude/mcp.json` to call `list_policy_documents`
- Expected: Returns the 6 seeded policies

**QA-03: Claude Code MCP connection**
- Goal: Validate that Claude Code can discover and call tools
- Steps:
  1. Start all four MCP servers (Docker or local)
  2. Add the documented `.claude/mcp.json` to the project
  3. Start Claude Code in the project directory
  4. Run `/mcp` to list connected servers
  5. Ask Claude to call `list_policy_documents`
- Expected: Claude lists all four servers' tools and successfully calls the policy-kb tool

---

## Open Questions

None — all requirements are clear from codebase exploration and user answers.

---

## Appendix

### Glossary
- **MCP**: Model Context Protocol — the protocol used by Claude to call external tool servers
- **Streamable HTTP**: The current MCP transport standard, served at `/mcp` endpoint
- **SSE**: Server-Sent Events — legacy MCP transport at `/sse`, used by the internal worker
- **Policy seeding**: One-time ingestion of reference policy PDFs into ChromaDB + Redis metadata

### References
- [.env.example](../../.env.example) — Environment variable template
- [docs/API.md](../../docs/API.md) — REST API reference
- [.sdd/project-guidelines.md](../project-guidelines.md) — Project conventions
- [docker-compose.yml](../../docker-compose.yml) — Full Docker Compose configuration

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-16 | Claude | Initial specification |
