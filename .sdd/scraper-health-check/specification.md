# Specification: Scraper Health Check

**Version:** 1.0
**Date:** 2026-02-14
**Status:** Implemented

---

## Problem Statement

The cherwell-scraper-mcp container permanently reports "unhealthy" in Docker despite functioning correctly. The Dockerfile HEALTHCHECK attempts an HTTP GET against the `/sse` endpoint, which is a persistent streaming MCP protocol connection that never returns a simple HTTP response within the health check timeout. This false-negative status prevents Docker-level orchestration from trusting the container's availability.

## Beneficiaries

**Primary:**
- Operators monitoring container health via `docker ps`, Portainer, or alerting systems

**Secondary:**
- Docker Compose dependency chains that may use `condition: service_healthy` on the scraper in future

---

## Outcomes

**Must Haves**
- The cherwell-scraper-mcp container reports "healthy" when the server is running and able to accept MCP connections
- The container reports "unhealthy" when the server process has crashed or is unresponsive

**Nice-to-haves**
- None

---

## Explicitly Out of Scope

- Adding health endpoints to document-store-mcp or policy-kb-mcp (they use ChromaDB heartbeat checks which work correctly)
- Deep liveness probes that test MCP protocol negotiation
- Exposing health status via the public API

---

## Functional Requirements

### FR-001: Health Endpoint
**Description:** The cherwell-scraper-mcp server must expose an HTTP GET `/health` endpoint that returns a 200 status code with a JSON body indicating service health when the server is running.

**Examples:**
- Positive case: GET `/health` returns `{"status": "ok"}` with HTTP 200
- Edge case: If the Starlette app is running but a future internal dependency (e.g. rate limiter) is broken, the endpoint still returns 200 (liveness check, not readiness)

### FR-002: Docker Health Check
**Description:** The Dockerfile HEALTHCHECK must use the `/health` endpoint instead of the `/sse` endpoint so Docker accurately reports container health.

**Examples:**
- Positive case: `docker ps` shows "(healthy)" for cherwell-scraper-mcp within start period
- Negative case: If the server process crashes, Docker shows "(unhealthy)" after retries

---

## Non-Functional Requirements

### NFR-001: Health Check Latency
**Category:** Performance
**Description:** The `/health` endpoint must respond within the Docker health check timeout.
**Acceptance Threshold:** Response time < 1 second
**Verification:** Testing — automated test verifying endpoint response

---

## Open Questions

None

---

## Appendix

### Glossary
- **SSE:** Server-Sent Events — the transport protocol used by MCP servers
- **MCP:** Model Context Protocol — the communication protocol between agent and tool servers
- **Liveness check:** Confirms the process is running; does not test deep functionality

### References
- Docker HEALTHCHECK documentation
- MCP SSE transport specification

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-14 | Claude | Initial specification |
