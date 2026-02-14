# Design: Scraper Health Check

**Version:** 1.0
**Date:** 2026-02-14
**Status:** Implemented
**Linked Specification** `.sdd/scraper-health-check/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context
The cherwell-scraper-mcp is a Starlette application serving an MCP server over SSE transport. It exposes two routes: `/sse` (streaming connection) and `/messages` (POST endpoint for MCP messages). The Dockerfile HEALTHCHECK sends an HTTP GET to `/sse`, which hangs because the SSE endpoint initiates a persistent streaming connection rather than returning an HTTP response.

The other MCP servers (document-store-mcp, policy-kb-mcp) avoid this problem by health-checking ChromaDB internally (`chromadb.PersistentClient.heartbeat()`), bypassing the SSE endpoint entirely. The scraper has no such internal service to probe.

### Proposed Architecture
Add a `/health` route to the Starlette application that returns a simple JSON response. Update the Dockerfile HEALTHCHECK to probe `/health` instead of `/sse`. This mirrors the pattern used by the API service (`/api/v1/health`), adapted for the simpler MCP server context.

### Technology Decisions
- Use a plain Starlette `Route` returning a `JSONResponse` — no framework additions needed
- Keep the health check as a liveness probe (process running + accepting HTTP) rather than a readiness probe (no deep dependency checks needed — the scraper's only external dependency is the Cherwell portal, which is checked per-request)

### Quality Attributes
- Minimal code change (one route, one Dockerfile line)
- Consistent with existing API health check pattern

---

## API Design

The `/health` endpoint is an internal Docker health check, not a public API. It accepts GET requests and returns:

- **200 OK**: `{"status": "ok"}` — server is running
- No authentication required (internal container network only)
- No error cases beyond server being down (in which case the request fails at TCP level)

---

## Modified Components

### Starlette Route Table in `create_app()`
**Change Description** Currently returns a Starlette app with two routes (`/sse`, `/messages`). Add a third route `/health` that returns a static JSON response. This is the minimal change to make the server health-checkable via HTTP.

**Dependants** Dockerfile.scraper (health check command references this endpoint)

**Kind** Function

**Requirements References**
- [scraper-health-check:FR-001]: The `/health` route satisfies the requirement for an HTTP endpoint returning 200 with JSON body

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| create_app/TS-01 | Health endpoint returns 200 | The scraper Starlette app is running | GET `/health` is requested | Response is 200 with body `{"status": "ok"}` |
| create_app/TS-02 | Health endpoint has correct content type | The scraper Starlette app is running | GET `/health` is requested | Response Content-Type is `application/json` |

### Dockerfile.scraper HEALTHCHECK
**Change Description** Currently runs `httpx.get('http://localhost:3001/sse', ...)` which times out against the SSE streaming endpoint. Change to `httpx.get('http://localhost:3001/health', ...)` targeting the new health endpoint.

**Dependants** None

**Kind** Dockerfile

**Requirements References**
- [scraper-health-check:FR-002]: Docker health check uses `/health` endpoint for accurate container status reporting

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| Dockerfile/TS-01 | Health check targets correct endpoint | Dockerfile.scraper is read | HEALTHCHECK command is inspected | URL contains `/health` not `/sse` |

---

## Added Components

None — only modifying existing components.

---

## Used Components

### Starlette JSONResponse
**Location** `starlette.responses.JSONResponse`

**Provides** Simple JSON HTTP response construction for the health endpoint

**Used By** Modified `create_app()` route table

---

## Documentation Considerations
- Remove "cherwell-scraper-mcp reports unhealthy" from MEMORY.md known issues after implementation

---

## Instrumentation (if needed)

N/A — No observability-verified NFRs in the specification.

---

## Integration Test Scenarios (if needed)

N/A — The health endpoint is a simple static response with no component interactions. The Dockerfile health check is verified by Docker at runtime.

---

## E2E Test Scenarios (if needed)

| ID | Scenario | Given | When | Then | User Journey |
|----|----------|-------|------|------|--------------|
| E2E-01 | Container reports healthy after deploy | The scraper container is built and started with Docker | Docker executes the HEALTHCHECK after start-period | `docker ps` shows "(healthy)" for the scraper container | Build image, start container, wait for health check, verify status |

Note: E2E-01 is a manual verification — it requires Docker runtime and cannot be tested in CI unit tests.

---

## Test Data
- None required — the health endpoint returns static data

---

## Test Feasibility
- Unit tests use Starlette `TestClient` (already available in the project via `httpx`)
- E2E-01 requires Docker runtime — manual verification only

---

## Risks and Dependencies
- **Low risk**: The change is additive (new route) with a one-line Dockerfile update
- **No external dependencies**: The health endpoint has no dependencies beyond Starlette itself

---

## Feasability Review
- No blockers — all infrastructure is in place

---

## Task Breakdown

### Phase 1: Add health endpoint and update Dockerfile

- Task 1: Add `/health` route to scraper `create_app()` and update Dockerfile
  - Status: Done
  - Add a `/health` Starlette route returning `JSONResponse({"status": "ok"})` to `create_app()` in `src/mcp_servers/cherwell_scraper/server.py`. Update `docker/Dockerfile.scraper` HEALTHCHECK to target `/health`. Write unit tests using Starlette TestClient.
  - Requirements: [scraper-health-check:FR-001], [scraper-health-check:FR-002], [scraper-health-check:NFR-001]
  - Test Scenarios: [scraper-health-check:create_app/TS-01], [scraper-health-check:create_app/TS-02], [scraper-health-check:Dockerfile/TS-01]

---

## Intermediate Dead Code Tracking

N/A — single phase, no intermediate dead code.

---

## Intermediate Stub Tracking

N/A — single phase, no stubs.

---

## Requirements Validation

- [scraper-health-check:FR-001]
  - Phase 1 Task 1
- [scraper-health-check:FR-002]
  - Phase 1 Task 1
- [scraper-health-check:NFR-001]
  - Phase 1 Task 1

---

## Test Scenario Validation

### Component Scenarios
- [scraper-health-check:create_app/TS-01]: Phase 1 Task 1
- [scraper-health-check:create_app/TS-02]: Phase 1 Task 1
- [scraper-health-check:Dockerfile/TS-01]: Phase 1 Task 1

### Integration Scenarios
N/A

### E2E Scenarios
- [scraper-health-check:E2E-01]: Manual verification after deployment

---

## Appendix

### Glossary
- **Liveness probe**: Health check that confirms a process is running, without testing deep functionality

### References
- Docker HEALTHCHECK reference
- Starlette documentation — Routes and Responses

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-14 | Claude | Initial design |
