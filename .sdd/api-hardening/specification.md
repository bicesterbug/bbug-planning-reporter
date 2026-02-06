# Specification: API Hardening & Production Readiness

**Version:** 1.0
**Date:** 2026-02-06
**Status:** Draft

---

## Problem Statement

The system needs production-ready API security, reliability features, and documentation before deployment. This includes authentication, rate limiting, review export capabilities, comprehensive testing, and developer documentation.

## Beneficiaries

**Primary:**
- External API consumers who need secure, documented access
- System operators who need to monitor and control usage

**Secondary:**
- Developers integrating with the API
- Security auditors reviewing the system

---

## Outcomes

**Must Haves**
- API key authentication on all endpoints
- Rate limiting per API key
- Review download in multiple formats (Markdown, JSON, PDF)
- Comprehensive test suite with good coverage
- OpenAPI documentation auto-generated
- Developer guides for API and webhook integration

**Nice-to-haves**
- OAuth2 support for enterprise integrations
- Admin dashboard for system monitoring
- API usage analytics

---

## Explicitly Out of Scope

- User management and registration (API keys managed externally)
- Billing and usage-based charging
- Multi-tenant isolation
- Public API gateway deployment (infrastructure concern)

---

## Functional Requirements

### FR-001: API Key Authentication
**Description:** All API endpoints except `/health` must require a valid API key in the Authorization header.

**Examples:**
- Positive case: Request with `Authorization: Bearer sk-cycle-xxx` succeeds
- Edge case: Missing or invalid key returns 401 `unauthorized`

### FR-002: API Key Validation
**Description:** The system must validate API keys against a configured list stored in environment variables or a keys file.

**Examples:**
- Positive case: Valid key from list is accepted
- Edge case: Revoked key returns 401

### FR-003: Rate Limiting
**Description:** The system must limit request rates per API key to prevent abuse.

**Examples:**
- Positive case: Under limit, requests succeed normally
- Edge case: Exceeding limit returns 429 `rate_limited` with Retry-After header

### FR-004: Configurable Rate Limits
**Description:** Rate limits must be configurable via environment variable, with default of 60 requests per minute.

**Examples:**
- Positive case: Custom limit of 120/min applied when configured
- Edge case: Missing config uses default

### FR-005: Download Review as Markdown
**Description:** The system must provide endpoint to download completed review as Markdown file.

**Examples:**
- Positive case: GET `/api/v1/reviews/{id}/download?format=markdown` returns `.md` file
- Edge case: Download for incomplete review returns 400

### FR-006: Download Review as JSON
**Description:** The system must provide endpoint to download completed review as JSON file.

**Examples:**
- Positive case: GET `/api/v1/reviews/{id}/download?format=json` returns `.json` file

### FR-007: Download Review as PDF
**Description:** The system must provide endpoint to download completed review as formatted PDF.

**Examples:**
- Positive case: GET `/api/v1/reviews/{id}/download?format=pdf` returns styled PDF
- Edge case: PDF includes tables and formatting

### FR-008: OpenAPI Specification
**Description:** The API must auto-generate OpenAPI 3.0 specification from route definitions.

**Examples:**
- Positive case: GET `/docs` shows interactive Swagger UI
- Edge case: GET `/openapi.json` returns raw spec

### FR-009: Request Validation
**Description:** All API requests must be validated against Pydantic schemas with clear error messages.

**Examples:**
- Positive case: Valid request processed normally
- Edge case: Invalid field returns 422 with field-level errors

### FR-010: HTTPS Webhook Enforcement
**Description:** In production mode, the system must reject webhook URLs that are not HTTPS.

**Examples:**
- Positive case: HTTPS URL accepted
- Edge case: HTTP URL returns 422 `invalid_webhook_url` in production

### FR-011: Error Response Consistency
**Description:** All error responses must follow the standard error format with code, message, and optional details.

**Examples:**
- Positive case: Error includes `error.code` and `error.message`
- Edge case: Stack traces never exposed in production

### FR-012: API Version Header
**Description:** All responses must include an `X-API-Version` header indicating the API version.

**Examples:**
- Positive case: Response includes `X-API-Version: 1.0.0`

### FR-013: Request ID Tracking
**Description:** All requests must be assigned a unique request ID for tracing, returned in `X-Request-ID` header.

**Examples:**
- Positive case: Response includes `X-Request-ID` matching logs
- Edge case: Client-provided `X-Request-ID` is preserved

### FR-014: Worker Scaling Test
**Description:** The system must support running multiple worker replicas for concurrent review processing.

**Examples:**
- Positive case: 3 workers process 3 reviews concurrently
- Edge case: No job duplication or race conditions

---

## Non-Functional Requirements

### NFR-001: Test Coverage
**Category:** Maintainability
**Description:** The codebase must have comprehensive automated test coverage.
**Acceptance Threshold:** >80% line coverage; all critical paths covered
**Verification:** Coverage report from pytest-cov

### NFR-002: API Security
**Category:** Security
**Description:** The API must follow security best practices.
**Acceptance Threshold:** No OWASP Top 10 vulnerabilities; no exposed secrets
**Verification:** Security review; dependency scanning

### NFR-003: Documentation Quality
**Category:** Maintainability
**Description:** API and integration documentation must be clear and complete.
**Acceptance Threshold:** All endpoints documented; webhook integration guide complete
**Verification:** Documentation review

### NFR-004: Performance Under Load
**Category:** Performance
**Description:** The API must maintain performance under concurrent load.
**Acceptance Threshold:** 100 concurrent requests with <500ms p95 response time
**Verification:** Load testing with k6 or locust

### NFR-005: Horizontal Scalability
**Category:** Scalability
**Description:** The system must scale horizontally by adding worker replicas.
**Acceptance Threshold:** Linear throughput scaling with worker count (up to 5)
**Verification:** Load testing with varying replica counts

### NFR-006: PDF Generation Quality
**Category:** Reliability
**Description:** PDF exports must be well-formatted and readable.
**Acceptance Threshold:** Tables render correctly; policy citations legible
**Verification:** Manual review of sample PDFs

---

## Open Questions

None at this time.

---

## Appendix

### Glossary

- **Rate Limiting:** Restricting the number of API requests per time period
- **OpenAPI:** Specification format for describing REST APIs
- **p95:** 95th percentile response time

### References

- [Master Design Document](../../docs/DESIGN.md) - Section 6 REST API Specification
- [OWASP Top 10](https://owasp.org/www-project-top-ten/) - Web security vulnerabilities

### Change History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-06 | SDD Agent | Initial specification |
