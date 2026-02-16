# Design: Local Development Setup Guide

**Version:** 1.0
**Date:** 2026-02-16
**Status:** Draft
**Linked Specification** `.sdd/local-dev-setup/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The project has a `README.md` with a minimal "Local Development" section that shows `docker compose up -d` and a few `pytest` commands. There is a `.env.example` file with commented defaults. There are per-MCP-server API docs (`docs/mcp-*.md`) but no guide on how to actually run a server locally or connect it to an MCP client. The docker-compose.yml hardcodes volume paths to the project owner's external drive (`/media/pete/Files/bbug-reports/`), which will fail on any other machine without explanation.

### Proposed Architecture

Add a single new documentation file `docs/LOCAL_DEV.md` and link it from `README.md`. The document is structured in progressive sections:

1. **Prerequisites** — what to install
2. **Quick-reference table** — all services, ports, dependencies at a glance
3. **Docker Compose full stack** — build base image, override volumes, start, verify
4. **Running MCP servers via local Python** — per-server instructions with env vars
5. **Policy data seeding** — Docker and local Python
6. **Connecting to Claude Code and Claude Desktop** — config files with and without auth
7. **Environment variable reference** — Docker vs local-Python values
8. **Troubleshooting** — symptom/cause/fix for common issues

### Technology Decisions

- Plain markdown — no tooling or build step required
- Config examples use JSON (Claude Code) and JSON (Claude Desktop) — both use the same format
- Volume path overrides documented via `.env` variables rather than editing docker-compose.yml directly

### Quality Attributes

- **Maintainability**: Single file, structured with anchored headings so individual sections can be updated independently. Environment variable table cross-references `.env.example` to reduce duplication.
- **Discoverability**: Linked from README.md "Local Development" section.

---

## Added Components

### docs/LOCAL_DEV.md
**Description** Comprehensive local development setup guide covering Docker Compose, local Python, policy seeding, and MCP client configuration.

**Users** Contributors and external MCP users.

**Kind** Documentation file

**Location** `docs/LOCAL_DEV.md`

**Details**

Sections (in order):

1. **Prerequisites** — Docker, Docker Compose, Python 3.12+, git
2. **Quick Reference** — table: service | port | Python module | dependencies | key env vars
3. **Docker Compose Setup**
   - Build base image: `docker build -t cherwell-base:latest -f docker/Dockerfile.base .`
   - Volume path overrides: add `CHROMA_DIR`, `RAW_DIR`, `OUTPUT_DIR`, `REDIS_DIR` to `.env`
   - Note: current docker-compose.yml has hardcoded paths — document using `docker compose` override or env var substitution
   - Start: `docker compose up -d`
   - Verify: curl each service health endpoint
4. **Running MCP Servers Locally (Python)**
   - Common: activate venv, start Redis via Docker
   - Per-server blocks with exact env vars and `python -m` command
   - Policy-KB: emphasise Redis + ChromaDB dependency
5. **Policy Data Seeding**
   - Docker: `docker compose run --rm policy-init`
   - Local Python: env vars + `python -m src.scripts.seed_policies`
   - Verify: curl or Claude tool call
6. **Connecting to Claude Code**
   - `.claude/mcp.json` with `type: url` and `/mcp` endpoint
   - With and without `MCP_API_KEY` headers
7. **Connecting to Claude Desktop**
   - `claude_desktop_config.json` location and format
   - Same URL pattern as Claude Code
8. **Environment Variable Reference**
   - Table: variable | default (Docker) | default (local) | description | used by
9. **Troubleshooting**
   - Redis not running → policy-kb tools fail
   - ChromaDB path mismatch → empty search results
   - MCP_API_KEY mismatch → 401 from health/tool calls
   - Port conflict → override via env var
   - Base image not built → service build fails
   - Volume paths don't exist → Docker mount errors

**Requirements References**
- [local-dev-setup:FR-001]: Docker Compose quick start section
- [local-dev-setup:FR-002]: Per-service local Python section
- [local-dev-setup:FR-003]: Policy seeding section
- [local-dev-setup:FR-004]: Claude Code config section
- [local-dev-setup:FR-005]: Claude Desktop config section
- [local-dev-setup:FR-006]: Quick reference table
- [local-dev-setup:FR-007]: Environment variable reference table
- [local-dev-setup:FR-008]: Troubleshooting section

**Test Scenarios**

No automated tests — this is documentation. Validated via QA plan in specification.

---

### README.md (Modified)
**Change Description** Add a link to `docs/LOCAL_DEV.md` from the existing "Local Development" section.

**Dependants** None

**Kind** Documentation file

**Details**
- Add a line after the existing local dev instructions pointing to the full guide: `For detailed setup instructions including MCP server configuration, see [docs/LOCAL_DEV.md](docs/LOCAL_DEV.md).`

**Requirements References**
- [local-dev-setup:FR-001]: Makes the guide discoverable

---

## Used Components

### .env.example
**Location** `.env.example`

**Provides** Canonical list of environment variables with comments. The LOCAL_DEV.md references this file rather than duplicating every variable.

**Used By** docs/LOCAL_DEV.md (FR-007 environment variable reference)

### docker-compose.yml
**Location** `docker-compose.yml`

**Provides** Service definitions, port mappings, volume mounts, dependency chains. LOCAL_DEV.md documents how to use it and how to override hardcoded paths.

**Used By** docs/LOCAL_DEV.md (FR-001 Docker Compose setup)

### src/mcp_servers/shared/transport.py
**Location** `src/mcp_servers/shared/transport.py`

**Provides** Defines the `/mcp`, `/sse`, `/health` endpoint structure that MCP client configs must target.

**Used By** docs/LOCAL_DEV.md (FR-004, FR-005 MCP client configuration)

---

## Documentation Considerations

- `docs/LOCAL_DEV.md` is the primary deliverable
- `README.md` gets a one-line addition linking to it
- No other documentation files need changes

---

## Risks and Dependencies

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Docker-compose volume paths are hardcoded to one machine | High | Contributors get mount errors | Document how to override via `.env` or local compose override file |
| Claude Code MCP config format changes in future | Low | Config examples break | Note the Claude Code version tested against |
| Policy seeding requires ~2GB RAM for embedding model | Medium | OOM on small machines | Document memory requirement |

---

## Feasibility Review

No blockers. All information needed is available in the existing codebase.

---

## QA Feasibility

**QA-01 (Fresh clone to running stack):** Fully feasible — all steps use existing Docker infrastructure. Volume path override requires documenting the `.env` approach since the compose file has hardcoded paths.

**QA-02 (Local Python policy-kb with seeding):** Fully feasible — requires Redis running (Docker one-liner) and a local ChromaDB directory.

**QA-03 (Claude Code MCP connection):** Fully feasible — requires servers running and `.claude/mcp.json` in place.

---

## Task Breakdown

### Phase 1: Write documentation

**Task 1: Create docs/LOCAL_DEV.md**
- Status: Done
- Requirements: [local-dev-setup:FR-001], [local-dev-setup:FR-002], [local-dev-setup:FR-003], [local-dev-setup:FR-004], [local-dev-setup:FR-005], [local-dev-setup:FR-006], [local-dev-setup:FR-007], [local-dev-setup:FR-008]
- Test Scenarios: None (documentation only)
- Details:
  - Write the full LOCAL_DEV.md as described in the Added Components section
  - All 8 sections must be present and accurate

**Task 2: Link from README.md**
- Status: Done
- Requirements: [local-dev-setup:FR-001]
- Test Scenarios: None (documentation only)
- Details:
  - Add a link to docs/LOCAL_DEV.md from the Local Development section of README.md

---

## Intermediate Dead Code Tracking

None — documentation only.

---

## Intermediate Stub Tracking

None — documentation only.

---

## Appendix

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-16 | Claude | Initial design |
