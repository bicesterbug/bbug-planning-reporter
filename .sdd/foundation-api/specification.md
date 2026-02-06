# Specification: Foundation API & Cherwell Scraper

**Version:** 1.0
**Date:** 2026-02-06
**Status:** Draft

---

## Problem Statement

Cycling advocacy groups need to review Cherwell planning applications for compliance with cycling policy, but manually downloading documents and cross-referencing policies is time-consuming. The foundation must provide an API to accept review requests, queue them for processing, and scrape the Cherwell planning portal for application data and documents.

## Beneficiaries

**Primary:**
- Cycling advocacy group members who need to submit review requests
- System operators who need to monitor review processing

**Secondary:**
- External systems that integrate via webhooks
- Developers building on top of the API

---

## Outcomes

**Must Haves**
- REST API accepting review requests and returning job IDs
- Asynchronous job queue with status tracking
- Webhook notifications for review lifecycle events
- Scraper that extracts application metadata from Cherwell portal
- Scraper that downloads all documents for a planning application
- Docker Compose stack with all services containerised

**Nice-to-haves**
- Detailed progress tracking during document download phase
- Graceful handling of Cherwell portal downtime

---

## Explicitly Out of Scope

- Document text extraction and embedding (Phase 2)
- Policy knowledge base (Phase 3)
- AI agent review generation (Phase 4)
- API authentication and rate limiting (Phase 5)
- PDF export of reviews (Phase 5)

---

## Functional Requirements

### FR-001: Submit Review Request
**Description:** The system must accept a POST request with a planning application reference and optional webhook configuration, validate the reference format, create a job record, enqueue it for processing, and return a job ID immediately.

**Examples:**
- Positive case: POST with `{"application_ref": "25/01178/REM"}` returns 202 with `review_id`
- Edge case: POST with `{"application_ref": "25/01178/REM", "webhook": {"url": "https://example.com/hook"}}` stores webhook config

### FR-002: Validate Application Reference Format
**Description:** The system must validate that the application reference matches the Cherwell format pattern (YY/NNNNN/XXX where YY is year, NNNNN is sequence, XXX is type code).

**Examples:**
- Positive case: `25/01178/REM` is valid
- Negative case: `INVALID-REF` returns 400 with `invalid_reference` error

### FR-003: Get Review Status
**Description:** The system must provide an endpoint to retrieve the current status of a review job, including processing phase and progress percentage when available.

**Examples:**
- Positive case: GET `/api/v1/reviews/{id}/status` returns `{"status": "processing", "progress": {"phase": "downloading_documents", "percent_complete": 45}}`
- Edge case: GET for non-existent ID returns 404 with `review_not_found` error

### FR-004: Get Review Result
**Description:** The system must provide an endpoint to retrieve the full review result once complete, or current status if still processing.

**Examples:**
- Positive case: GET `/api/v1/reviews/{id}` for completed review returns full result JSON
- Edge case: GET for processing review returns status with progress

### FR-005: List Reviews
**Description:** The system must provide a paginated endpoint to list all submitted reviews with optional status filtering.

**Examples:**
- Positive case: GET `/api/v1/reviews?status=completed&limit=20` returns paginated list
- Edge case: Empty result set returns `{"reviews": [], "total": 0}`

### FR-006: Cancel Review
**Description:** The system must allow cancellation of queued or processing reviews.

**Examples:**
- Positive case: POST `/api/v1/reviews/{id}/cancel` for queued job returns `{"status": "cancelled"}`
- Edge case: Cancel already completed review returns error

### FR-007: Webhook Delivery
**Description:** The system must POST signed webhook payloads to configured URLs for review lifecycle events (started, progress, completed, failed) with retry on failure.

**Examples:**
- Positive case: `review.completed` webhook delivered with HMAC signature in header
- Edge case: Failed delivery retried with exponential backoff (5s, 30s, 2m, 10m, 30m)

### FR-008: Webhook Signing
**Description:** All webhook payloads must be signed using HMAC-SHA256 with the consumer's secret, included in the `X-Webhook-Signature-256` header.

**Examples:**
- Positive case: Header contains `sha256=<hex_digest>` that validates against payload

### FR-009: Scrape Application Metadata
**Description:** The Cherwell scraper must fetch application details (reference, address, proposal, applicant, status, dates) from the planning portal.

**Examples:**
- Positive case: `get_application_details("25/01178/REM")` returns structured metadata
- Edge case: Non-existent reference returns `application_not_found` error

### FR-010: List Application Documents
**Description:** The Cherwell scraper must extract the list of all documents associated with an application, including document type, date, and download URL.

**Examples:**
- Positive case: Returns array of document info with URLs
- Edge case: Application with no documents returns empty array

### FR-011: Download Application Documents
**Description:** The Cherwell scraper must download all documents for an application to local storage.

**Examples:**
- Positive case: `download_all_documents("25/01178/REM", "/data/raw")` saves PDFs to disk
- Edge case: Handles large files (>100MB) without timeout

### FR-012: Polite Scraping
**Description:** The scraper must respect rate limits (configurable, default 1 req/sec) and set a descriptive User-Agent header.

**Examples:**
- Positive case: Sequential requests spaced by rate limit delay
- Edge case: Respects Retry-After headers from server

### FR-013: Health Check Endpoint
**Description:** The system must provide a health check endpoint reporting connectivity to all services.

**Examples:**
- Positive case: GET `/api/v1/health` returns `{"status": "healthy", "services": {"redis": "connected", ...}}`

### FR-014: Prevent Duplicate Reviews
**Description:** The system must reject requests for an application that already has a queued or processing review.

**Examples:**
- Positive case: Second request for same ref while first is processing returns 409 `review_already_exists`

---

## Non-Functional Requirements

### NFR-001: API Response Time
**Category:** Performance
**Description:** API endpoints for status checks and job submission must respond quickly.
**Acceptance Threshold:** Response time < 200ms for 95th percentile (excluding external calls)
**Verification:** Load testing with k6 or locust

### NFR-002: Job Queue Reliability
**Category:** Reliability
**Description:** Jobs must not be lost between submission and processing.
**Acceptance Threshold:** Zero job loss under normal operation; at-least-once processing semantics
**Verification:** Integration testing with Redis restart scenarios

### NFR-003: Webhook Delivery Reliability
**Category:** Reliability
**Description:** Webhooks must be delivered with retry logic for transient failures.
**Acceptance Threshold:** At-least-once delivery with 5 retry attempts over 30 minutes
**Verification:** Integration testing with mock webhook endpoint returning failures

### NFR-004: Scraper Resilience
**Category:** Reliability
**Description:** The scraper must handle Cherwell portal errors gracefully.
**Acceptance Threshold:** Retry transient errors (5xx, timeout) 3 times with backoff; fail with descriptive error
**Verification:** Integration testing with mock portal responses

### NFR-005: Containerisation
**Category:** Maintainability
**Description:** All services must run in Docker containers orchestrated by Docker Compose.
**Acceptance Threshold:** `docker compose up` starts all services with health checks passing
**Verification:** Manual verification

### NFR-006: Structured Logging
**Category:** Maintainability
**Description:** All services must emit structured JSON logs with consistent context fields.
**Acceptance Threshold:** All log entries include timestamp, level, component, and relevant IDs
**Verification:** Code review and log inspection

---

## Open Questions

None at this time.

---

## Appendix

### Glossary

- **Application Reference:** Cherwell planning application identifier (e.g., `25/01178/REM`)
- **Review:** The complete analysis output for a planning application
- **Job:** An asynchronous task representing a review in progress
- **MCP:** Model Context Protocol - tool interface for the AI agent
- **ULID:** Universally Unique Lexicographically Sortable Identifier

### References

- [Master Design Document](../../docs/DESIGN.md) - Full architecture specification
- [Cherwell Planning Portal](https://planningregister.cherwell.gov.uk) - Target scraping site

### Change History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-06 | SDD Agent | Initial specification |
