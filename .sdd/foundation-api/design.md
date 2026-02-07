# Design: Foundation API & Cherwell Scraper

**Version:** 1.0
**Date:** 2026-02-06
**Status:** Draft
**Linked Specification:** `.sdd/foundation-api/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

This is a greenfield project. There is no existing codebase. The foundation-api feature establishes the core infrastructure that all subsequent phases will build upon.

### Proposed Architecture

The foundation consists of three main components communicating through Redis:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Docker Compose Stack                              │
│                                                                          │
│  ┌─────────────────────┐      ┌─────────────────────┐                   │
│  │   API Gateway       │      │      Redis          │                   │
│  │   (FastAPI)         │◄────►│   - Job Queue (arq) │                   │
│  │   :8080             │      │   - State Store     │                   │
│  │                     │      │   - Pub/Sub         │                   │
│  │   POST /reviews     │      │   :6379             │                   │
│  │   GET  /reviews/*   │      └─────────┬───────────┘                   │
│  │   GET  /health      │                │                                │
│  └─────────┬───────────┘                │                                │
│            │                            │                                │
│            │ enqueue                    │ dequeue                        │
│            │                            ▼                                │
│            │              ┌─────────────────────────┐                   │
│            │              │    Worker (arq)         │                   │
│            │              │    - Job processing     │                   │
│            │              │    - Webhook dispatch   │                   │
│            └─────────────►│    - Progress tracking  │                   │
│                           └─────────────┬───────────┘                   │
│                                         │                                │
│                                         │ MCP calls (stdio)              │
│                                         ▼                                │
│                           ┌─────────────────────────┐                   │
│                           │  Cherwell Scraper MCP   │                   │
│                           │  - get_application_*    │                   │
│                           │  - download_*           │                   │
│                           └─────────────┬───────────┘                   │
│                                         │                                │
│                                         │ HTTP (rate-limited)            │
│                                         ▼                                │
│                           ┌─────────────────────────┐                   │
│                           │  Cherwell Planning      │                   │
│                           │  Portal (external)      │                   │
│                           └─────────────────────────┘                   │
└─────────────────────────────────────────────────────────────────────────┘
```

**Request Flow:**
1. Consumer POSTs review request to API Gateway
2. API validates, creates job record in Redis, enqueues to arq queue
3. Worker dequeues job, spawns Cherwell Scraper MCP process
4. Worker calls MCP tools to fetch metadata and download documents
5. Worker publishes progress events to Redis
6. API dispatches webhooks based on Redis pub/sub events
7. Consumer receives webhooks or polls for status/results

### Technology Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Web Framework | FastAPI | Async-native, auto OpenAPI docs, Pydantic validation |
| Job Queue | arq (Redis-based) | Lightweight, async, Python-native, Redis already needed |
| State Store | Redis | Shared state between API and worker, pub/sub for events |
| HTTP Client | httpx | Async, modern, excellent API |
| HTML Parser | BeautifulSoup4 | Robust, handles malformed HTML |
| ID Generation | ulid-py | Time-sortable, URL-safe |
| Containerisation | Docker Compose | Simple orchestration for development |

### Quality Attributes

**Scalability:**
- Worker can be replicated (arq supports multiple workers)
- Redis handles concurrent access
- API is stateless (all state in Redis)

**Maintainability:**
- Clear separation: API, Worker, Scraper MCP
- Structured logging with consistent context
- Type hints throughout with Pydantic models

---

## API Design

### Resource Model

The API centers on a single primary resource: **Review**. Reviews represent asynchronous jobs that process planning applications.

```
/api/v1/
├── reviews                    # Collection of reviews
│   ├── POST                   # Create new review (returns 202)
│   └── GET                    # List reviews (paginated)
├── reviews/{review_id}        # Individual review
│   ├── GET                    # Get review result or status
│   └── status                 # Lightweight status check
│       └── GET
│   └── cancel                 # Cancel review
│       └── POST
└── health                     # System health check
    └── GET
```

### Request/Response Contracts

**POST /api/v1/reviews**

Creates an asynchronous review job. Returns immediately with job ID.

Input:
- `application_ref` (required): Cherwell reference in format `YY/NNNNN/XXX`
- `options` (optional): Configuration for review output
- `webhook` (optional): Callback configuration with URL, secret, event filter

Output (202 Accepted):
- `review_id`: ULID with `rev_` prefix
- `application_ref`: Echo of input
- `status`: Always "queued"
- `created_at`: ISO 8601 timestamp
- `estimated_duration_seconds`: Rough estimate (180s default)
- `links`: HATEOAS links to related resources

Errors:
- 400 `invalid_reference`: Malformed application reference
- 409 `review_already_exists`: Duplicate review in progress
- 422 `invalid_webhook_url`: Webhook URL validation failed

**GET /api/v1/reviews/{review_id}**

Returns full review result when complete, or current status when processing.

Output (200 OK - processing):
- `review_id`, `status`, `progress` object

Output (200 OK - completed):
- Full review payload including `application`, `review`, `metadata`

Errors:
- 404 `review_not_found`: Review ID does not exist

**GET /api/v1/reviews/{review_id}/status**

Lightweight status endpoint for polling.

Output (200 OK):
- `review_id`, `status`, `progress` (minimal fields)

**GET /api/v1/reviews**

Paginated list of reviews with optional filtering.

Query Parameters:
- `status`: Filter by status (queued, processing, completed, failed, cancelled)
- `application_ref`: Filter by application reference
- `limit`: Page size (default 20, max 100)
- `offset`: Pagination offset

Output (200 OK):
- `reviews`: Array of review summaries
- `total`: Total count for pagination
- `limit`, `offset`: Echo of pagination params

**POST /api/v1/reviews/{review_id}/cancel**

Cancels a queued or processing review.

Output (200 OK):
- `review_id`, `status`: "cancelled"

Errors:
- 404 `review_not_found`
- 409 `cannot_cancel`: Review already completed/failed

**GET /api/v1/health**

System health check reporting service connectivity.

Output (200 OK):
- `status`: "healthy" or "degraded"
- `services`: Connection status for each service
- `version`: API version

### Error Response Format

All errors follow this structure:

```json
{
    "error": {
        "code": "error_code_snake_case",
        "message": "Human-readable description",
        "details": {}
    }
}
```

### Webhook Payload Format

All webhooks include:
- `event`: Event type (review.started, review.progress, review.completed, review.failed)
- `delivery_id`: Unique ULID with `dlv_` prefix
- `timestamp`: ISO 8601
- `data`: Event-specific payload

Headers:
- `X-Webhook-Signature-256`: HMAC-SHA256 signature
- `X-Webhook-Event`: Event type
- `X-Webhook-Delivery-Id`: Delivery ID
- `X-Webhook-Timestamp`: Timestamp
- `Content-Type`: application/json

---

## Added Components

### ReviewRouter

**Description:** FastAPI router handling all `/api/v1/reviews` endpoints. Validates requests, interacts with Redis for job management, returns appropriate responses.

**Users:** External API consumers, system integrations

**Kind:** Module (FastAPI Router)

**Location:** `src/api/routes/reviews.py`

**Requirements References:**
- [foundation-api:FR-001]: Submit review endpoint implementation
- [foundation-api:FR-002]: Reference validation logic
- [foundation-api:FR-003]: Status endpoint implementation
- [foundation-api:FR-004]: Result endpoint implementation
- [foundation-api:FR-005]: List endpoint implementation
- [foundation-api:FR-006]: Cancel endpoint implementation
- [foundation-api:FR-014]: Duplicate prevention check
- [foundation-api:NFR-001]: Fast response times via async Redis operations

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Valid review submission | Valid application_ref "25/01178/REM" | POST /reviews with valid payload | Returns 202 with review_id and status "queued" |
| TS-02 | Invalid reference format | Malformed reference "INVALID" | POST /reviews | Returns 400 with error code "invalid_reference" |
| TS-03 | Duplicate review prevention | Review for "25/01178/REM" already queued | POST /reviews with same ref | Returns 409 with error code "review_already_exists" |
| TS-04 | Get processing review | Review in "processing" status | GET /reviews/{id} | Returns 200 with status and progress |
| TS-05 | Get completed review | Review in "completed" status | GET /reviews/{id} | Returns 200 with full review payload |
| TS-06 | Get non-existent review | No review with given ID | GET /reviews/{id} | Returns 404 with error code "review_not_found" |
| TS-07 | List reviews with filter | Multiple reviews exist | GET /reviews?status=completed | Returns paginated list filtered by status |
| TS-08 | Cancel queued review | Review in "queued" status | POST /reviews/{id}/cancel | Returns 200 with status "cancelled" |
| TS-09 | Cancel completed review | Review in "completed" status | POST /reviews/{id}/cancel | Returns 409 with error code "cannot_cancel" |
| TS-10 | Lightweight status check | Review in "processing" status | GET /reviews/{id}/status | Returns 200 with minimal progress payload |

### HealthRouter

**Description:** FastAPI router for `/api/v1/health` endpoint. Checks connectivity to Redis and reports overall system health.

**Users:** Load balancers, monitoring systems, operators

**Kind:** Module (FastAPI Router)

**Location:** `src/api/routes/health.py`

**Requirements References:**
- [foundation-api:FR-013]: Health check endpoint implementation

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Healthy system | Redis connected | GET /health | Returns 200 with status "healthy" |
| TS-02 | Degraded system | Redis disconnected | GET /health | Returns 200 with status "degraded" and redis "disconnected" |

### ReviewRequest / ReviewResponse Models

**Description:** Pydantic models for API request/response validation and serialization. Enforces application reference format, webhook URL validation, and consistent response structure.

**Users:** ReviewRouter, API consumers (via OpenAPI docs)

**Kind:** Module (Pydantic Models)

**Location:** `src/api/schemas.py`

**Requirements References:**
- [foundation-api:FR-001]: Request model for review submission
- [foundation-api:FR-002]: Reference pattern validation
- [foundation-api:FR-004]: Response model for review result
- [foundation-api:NFR-001]: Pydantic for fast validation

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Valid reference patterns | Various valid refs | Validate against pattern | All pass: "25/01178/REM", "08/00707/F", "23/01421/TCA" |
| TS-02 | Invalid reference patterns | Various invalid refs | Validate against pattern | All fail: "INVALID", "25-01178-REM", "25/1178/REM" |
| TS-03 | Webhook URL validation | HTTPS URL | Create webhook config | Passes validation |
| TS-04 | Optional fields handling | Minimal request | Parse request body | Defaults applied, optional fields None |

### RedisClient

**Description:** Async Redis client wrapper providing typed methods for job storage, status updates, and pub/sub operations. Handles connection pooling and reconnection.

**Users:** ReviewRouter, ReviewWorker, WebhookDispatcher

**Kind:** Class

**Location:** `src/shared/redis_client.py`

**Requirements References:**
- [foundation-api:NFR-002]: Reliable job storage
- [foundation-api:NFR-001]: Async operations for performance

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Store and retrieve job | Job data | Store then retrieve by review_id | Data matches |
| TS-02 | Update job status | Existing job | Update status to "processing" | Status persisted |
| TS-03 | Check for existing job | Job for ref exists with status "processing" | Query active jobs for ref | Returns True |
| TS-04 | List jobs by status | Multiple jobs | List with status filter | Returns filtered list |
| TS-05 | Connection recovery | Redis temporarily unavailable | Operation after reconnect | Succeeds |

### ReviewWorker

**Description:** arq worker that processes review jobs from the queue. In Phase 1, it fetches application data via the Cherwell Scraper MCP and downloads documents. Publishes progress events to Redis.

**Users:** arq task queue (automatic invocation)

**Kind:** Module (arq worker)

**Location:** `src/worker/jobs.py`

**Requirements References:**
- [foundation-api:FR-009]: Calls scraper to fetch metadata
- [foundation-api:FR-011]: Calls scraper to download documents
- [foundation-api:NFR-002]: At-least-once processing via arq
- [foundation-api:NFR-006]: Structured logging with review context

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Successful job processing | Valid job in queue | Worker picks up job | Status transitions: queued → processing; scraper tools called |
| TS-02 | Job with scraper failure | Scraper returns error | Worker processes job | Status transitions to "failed" with error details |
| TS-03 | Progress event publishing | Job processing | Worker completes phase | Progress event published to Redis |
| TS-04 | Job cancellation handling | Job cancelled mid-processing | Worker checks cancellation flag | Processing stops gracefully |

### WebhookDispatcher

**Description:** Async webhook delivery service. Signs payloads with HMAC-SHA256, delivers with retry on failure, logs delivery attempts.

**Users:** ReviewWorker (via Redis pub/sub trigger)

**Kind:** Class

**Location:** `src/worker/webhook_dispatcher.py`

**Requirements References:**
- [foundation-api:FR-007]: Webhook delivery with retry
- [foundation-api:FR-008]: HMAC-SHA256 signing
- [foundation-api:NFR-003]: 5 retries over 30 minutes

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Successful delivery | Valid webhook URL | Dispatch event | POST sent with correct headers, delivery logged as success |
| TS-02 | Signature verification | Payload and secret | Sign and verify | Signature matches expected format "sha256=..." |
| TS-03 | Retry on 5xx error | Webhook returns 503 | Dispatch event | Retried with backoff schedule |
| TS-04 | Retry exhaustion | Webhook always fails | Dispatch event | 5 attempts made, final failure logged |
| TS-05 | Timeout handling | Webhook hangs | Dispatch event | Times out after 10s, triggers retry |

### CherwellScraperMCP

**Description:** MCP server providing tools for scraping the Cherwell planning portal. Implements rate limiting, session handling, and robust HTML parsing.

**Users:** ReviewWorker (via MCP protocol)

**Kind:** Module (MCP Server)

**Location:** `src/mcp_servers/cherwell_scraper/server.py`

**Requirements References:**
- [foundation-api:FR-009]: get_application_details tool
- [foundation-api:FR-010]: list_application_documents tool
- [foundation-api:FR-011]: download_document, download_all_documents tools
- [foundation-api:FR-012]: Rate limiting and User-Agent
- [foundation-api:NFR-004]: Retry on transient errors

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Fetch application details | Valid reference "25/01178/REM" | Call get_application_details | Returns structured metadata (address, proposal, dates, status) |
| TS-02 | Non-existent application | Invalid reference | Call get_application_details | Returns error with code "application_not_found" |
| TS-03 | List documents | Application with documents | Call list_application_documents | Returns array with document info (type, date, URL) |
| TS-04 | Download single document | Valid document URL | Call download_document | File saved to output_dir, path returned |
| TS-05 | Download all documents | Application with 5 documents | Call download_all_documents | All 5 files saved, paths returned |
| TS-06 | Rate limiting | Multiple rapid calls | Call tools in sequence | Requests spaced by rate limit delay |
| TS-07 | Transient error retry | Portal returns 503 | Call any tool | Retried up to 3 times with backoff |
| TS-08 | Paginated document list | Application with >20 documents | Call list_application_documents | All pages fetched, complete list returned |

### CherwellParser

**Description:** HTML parser for Cherwell planning portal pages. Extracts structured data from application detail and document list pages.

**Users:** CherwellScraperMCP

**Kind:** Module

**Location:** `src/mcp_servers/cherwell_scraper/parsers.py`

**Requirements References:**
- [foundation-api:FR-009]: Parse application metadata from HTML
- [foundation-api:FR-010]: Parse document table from HTML

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Parse application page | Sample application HTML | Parse metadata | Extracts reference, address, proposal, applicant, status, dates |
| TS-02 | Parse document table | Sample documents tab HTML | Parse documents | Extracts list with type, date, description, URL |
| TS-03 | Handle missing fields | HTML with some fields missing | Parse metadata | Returns available fields, None for missing |
| TS-04 | Handle malformed HTML | Slightly broken HTML | Parse | Gracefully handles, extracts what's possible |

### ApplicationMetadata / DocumentInfo Models

**Description:** Data models for scraper output. Defines the structure of application metadata and document information.

**Users:** CherwellScraperMCP, CherwellParser, ReviewWorker

**Kind:** Module (Pydantic Models)

**Location:** `src/mcp_servers/cherwell_scraper/models.py`

**Requirements References:**
- [foundation-api:FR-009]: ApplicationMetadata structure
- [foundation-api:FR-010]: DocumentInfo structure

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Valid application metadata | Complete data | Create model | All fields populated |
| TS-02 | Optional date handling | Some dates missing | Create model | None for missing dates |

### Docker Compose Configuration

**Description:** Docker Compose configuration defining all services: api, worker, redis, cherwell-scraper-mcp. Includes health checks, volume mounts, and networking.

**Users:** Developers, operators

**Kind:** Configuration

**Location:** `docker-compose.yml`, `docker/Dockerfile.*`

**Requirements References:**
- [foundation-api:NFR-005]: All services containerised, health checks passing

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Stack startup | Clean environment | docker compose up | All services start, health checks pass |
| TS-02 | Service discovery | Stack running | API calls Redis | Connection successful via service name |
| TS-03 | Volume persistence | Data written to /data/raw | Stack restarted | Data persists |

---

## Used Components

### Redis (External)

**Location:** Docker image `redis:7-alpine`

**Provides:** Key-value storage, pub/sub messaging, arq job queue backend

**Used By:** RedisClient, ReviewRouter, ReviewWorker, WebhookDispatcher

### httpx (Library)

**Location:** Python package

**Provides:** Async HTTP client for scraping and webhook delivery

**Used By:** CherwellScraperMCP, WebhookDispatcher

### BeautifulSoup4 (Library)

**Location:** Python package

**Provides:** HTML parsing with tolerance for malformed markup

**Used By:** CherwellParser

### arq (Library)

**Location:** Python package

**Provides:** Async Redis-based job queue

**Used By:** ReviewWorker, ReviewRouter (enqueue)

### ulid-py (Library)

**Location:** Python package

**Provides:** ULID generation for IDs

**Used By:** ReviewRouter, WebhookDispatcher

---

## Documentation Considerations

- README.md with quick start, docker compose instructions
- API reference auto-generated via FastAPI OpenAPI
- Sample curl commands for all endpoints
- Webhook integration guide with signature verification example
- Environment variables documented in .env.example

---

## Instrumentation (if needed)

| Requirement | Observability Criteria | Implementation | Component |
|-------------|------------------------|----------------|-----------|
| [foundation-api:NFR-006] | All log entries include timestamp, level, component, review_id | structlog with processors for JSON output and context binding | All components |

---

## Integration Test Scenarios (if needed)

| ID | Scenario | Given | When | Then | Components Involved |
|----|----------|-------|------|------|---------------------|
| ITS-01 | End-to-end review submission | Stack running | POST /reviews with valid ref | Job queued, worker picks up, scraper called | API, Redis, Worker, Scraper |
| ITS-02 | Webhook delivery on job start | Review submitted with webhook | Worker starts processing | Webhook POSTed with "review.started" event | Worker, WebhookDispatcher |
| ITS-03 | Status polling during processing | Review processing | Poll GET /reviews/{id}/status | Returns current phase and progress | API, Redis |
| ITS-04 | Duplicate review prevention | Review queued for ref | Submit another for same ref | Returns 409 conflict | API, Redis |
| ITS-05 | Scraper portal error handling | Portal returns 503 | Worker calls scraper | Scraper retries, eventually fails, job marked failed | Worker, Scraper |

---

## E2E Test Scenarios (if needed)

| ID | Scenario | Given | When | Then | User Journey |
|----|----------|-------|------|------|--------------|
| E2E-01 | Complete review lifecycle | System running, valid Cherwell ref | Submit review, wait for completion | Review status progresses queued→processing→completed, documents downloaded | Submit → Poll status → Get result |
| E2E-02 | Webhook-driven integration | System running, webhook endpoint configured | Submit review with webhook | Receive webhooks for started, progress, completed | Submit → Receive webhooks |

---

## Test Data

**Requirements:**
- Sample Cherwell HTML pages (application detail, documents tab)
- Known valid application references for integration tests
- Mock webhook endpoint for testing delivery

**Sources:**
- Manually captured HTML saved in `tests/fixtures/cherwell/`
- List of test references documented in `tests/fixtures/README.md`
- httpbin.org or custom mock server for webhook testing

---

## Test Feasibility

- **Cherwell portal mocking:** Required for unit/integration tests. Capture real HTML responses as fixtures.
- **Webhook testing:** Use mock HTTP server (pytest-httpserver or respx)
- **Redis testing:** Use fakeredis for unit tests, real Redis in Docker for integration

---

## Risks and Dependencies

| Risk | Impact | Mitigation |
|------|--------|------------|
| Cherwell portal HTML changes | Scraper breaks | Capture multiple page samples; add integration test against live site (optional, flagged) |
| Rate limiting by Cherwell | Blocked IP | Configurable rate limit; polite User-Agent; consider caching |
| Large document downloads | Timeout, memory | Stream downloads to disk; configurable timeout |
| Redis unavailability | Job loss | Redis AOF persistence; health checks; reconnection logic |

**External Dependencies:**
- Cherwell Planning Portal availability
- Redis container health

**Assumptions:**
- Cherwell portal structure remains stable
- Application references follow documented pattern
- Docker and Docker Compose available in deployment environment

---

## Feasibility Review

No large missing features or infrastructure. All dependencies are well-established libraries.

---

## Task Breakdown

> **CRITICAL: Tests are written WITH implementation, not after.**

### Phase 1: Project Scaffolding & Docker Setup

- Task 1: Create project structure and pyproject.toml
  - Status: Backlog
  - Set up directory structure per DESIGN.md section 12
  - Configure pytest, linting (ruff), formatting (black)
  - Requirements: [foundation-api:NFR-005]
  - Test Scenarios: N/A (scaffolding)

- Task 2: Create Docker base image and compose configuration
  - Status: Backlog
  - Dockerfile.base with Python 3.12, common dependencies
  - docker-compose.yml with api, worker, redis services (scraper placeholder)
  - Health checks for all services
  - Requirements: [foundation-api:NFR-005]
  - Test Scenarios: [foundation-api:DockerCompose/TS-01], [foundation-api:DockerCompose/TS-02], [foundation-api:DockerCompose/TS-03]

- Task 3: Implement RedisClient with connection management
  - Status: Backlog
  - Async client wrapper with connection pooling
  - Methods for job CRUD, status updates
  - Requirements: [foundation-api:NFR-002], [foundation-api:NFR-001]
  - Test Scenarios: [foundation-api:RedisClient/TS-01], [foundation-api:RedisClient/TS-02], [foundation-api:RedisClient/TS-03], [foundation-api:RedisClient/TS-04], [foundation-api:RedisClient/TS-05]

### Phase 2: API Gateway Core

- Task 4: Implement Pydantic schemas for requests/responses
  - Status: Backlog
  - ReviewRequest, ReviewResponse, ErrorResponse models
  - Application reference regex validation
  - Requirements: [foundation-api:FR-001], [foundation-api:FR-002], [foundation-api:FR-004]
  - Test Scenarios: [foundation-api:ReviewRequestModels/TS-01], [foundation-api:ReviewRequestModels/TS-02], [foundation-api:ReviewRequestModels/TS-03], [foundation-api:ReviewRequestModels/TS-04]

- Task 5: Implement POST /reviews endpoint
  - Status: Backlog
  - Validate request, check for duplicates, create job, enqueue, return 202
  - Requirements: [foundation-api:FR-001], [foundation-api:FR-002], [foundation-api:FR-014]
  - Test Scenarios: [foundation-api:ReviewRouter/TS-01], [foundation-api:ReviewRouter/TS-02], [foundation-api:ReviewRouter/TS-03]

- Task 6: Implement GET /reviews/{id} and /reviews/{id}/status endpoints
  - Status: Backlog
  - Retrieve job from Redis, return status or full result
  - Requirements: [foundation-api:FR-003], [foundation-api:FR-004]
  - Test Scenarios: [foundation-api:ReviewRouter/TS-04], [foundation-api:ReviewRouter/TS-05], [foundation-api:ReviewRouter/TS-06], [foundation-api:ReviewRouter/TS-10]

- Task 7: Implement GET /reviews list endpoint
  - Status: Backlog
  - Paginated listing with status and ref filters
  - Requirements: [foundation-api:FR-005]
  - Test Scenarios: [foundation-api:ReviewRouter/TS-07]

- Task 8: Implement POST /reviews/{id}/cancel endpoint
  - Status: Backlog
  - Cancel queued/processing jobs, reject completed
  - Requirements: [foundation-api:FR-006]
  - Test Scenarios: [foundation-api:ReviewRouter/TS-08], [foundation-api:ReviewRouter/TS-09]

- Task 9: Implement GET /health endpoint
  - Status: Backlog
  - Check Redis connectivity, return health status
  - Requirements: [foundation-api:FR-013]
  - Test Scenarios: [foundation-api:HealthRouter/TS-01], [foundation-api:HealthRouter/TS-02]

### Phase 3: Worker & Webhook Infrastructure

- Task 10: Implement arq worker skeleton
  - Status: Backlog
  - Worker entry point, job handler registration
  - Status transitions: queued → processing → (complete/failed)
  - Requirements: [foundation-api:NFR-002]
  - Test Scenarios: [foundation-api:ReviewWorker/TS-01]

- Task 11: Implement WebhookDispatcher with signing
  - Status: Backlog
  - HMAC-SHA256 signing, async delivery, retry with backoff
  - Requirements: [foundation-api:FR-007], [foundation-api:FR-008], [foundation-api:NFR-003]
  - Test Scenarios: [foundation-api:WebhookDispatcher/TS-01], [foundation-api:WebhookDispatcher/TS-02], [foundation-api:WebhookDispatcher/TS-03], [foundation-api:WebhookDispatcher/TS-04], [foundation-api:WebhookDispatcher/TS-05]

- Task 12: Implement progress event publishing
  - Status: Backlog
  - Worker publishes phase transitions to Redis
  - API triggers webhook dispatch on events
  - Requirements: [foundation-api:FR-007]
  - Test Scenarios: [foundation-api:ReviewWorker/TS-03], [foundation-api:ITS-02]

### Phase 4: Cherwell Scraper MCP

- Task 13: Implement CherwellParser for HTML extraction
  - Status: Complete
  - Parse application detail page, parse documents table
  - Handle pagination, missing fields, malformed HTML
  - Requirements: [foundation-api:FR-009], [foundation-api:FR-010]
  - Test Scenarios: [foundation-api:CherwellParser/TS-01], [foundation-api:CherwellParser/TS-02], [foundation-api:CherwellParser/TS-03], [foundation-api:CherwellParser/TS-04]

- Task 14: Implement Cherwell HTTP client with rate limiting
  - Status: Complete
  - Async httpx client, configurable rate limit, User-Agent
  - Session handling, retry on 5xx/timeout
  - Requirements: [foundation-api:FR-012], [foundation-api:NFR-004]
  - Test Scenarios: [foundation-api:CherwellScraperMCP/TS-06], [foundation-api:CherwellScraperMCP/TS-07]

- Task 15: Implement MCP server with get_application_details tool
  - Status: Complete
  - MCP server setup, tool registration
  - Fetch and parse application page
  - Requirements: [foundation-api:FR-009]
  - Test Scenarios: [foundation-api:CherwellScraperMCP/TS-01], [foundation-api:CherwellScraperMCP/TS-02]

- Task 16: Implement list_application_documents tool
  - Status: Complete
  - Fetch documents tab, parse table, handle pagination
  - Requirements: [foundation-api:FR-010]
  - Test Scenarios: [foundation-api:CherwellScraperMCP/TS-03], [foundation-api:CherwellScraperMCP/TS-08]

- Task 17: Implement download_document and download_all_documents tools
  - Status: Complete
  - Stream downloads to /data/raw/{ref}/, return paths
  - Requirements: [foundation-api:FR-011]
  - Test Scenarios: [foundation-api:CherwellScraperMCP/TS-04], [foundation-api:CherwellScraperMCP/TS-05]

### Phase 5: Integration & End-to-End Testing

- Task 18: Wire worker to scraper MCP
  - Status: Backlog
  - Worker spawns MCP process, calls tools
  - Handle scraper errors, update job status
  - Requirements: [foundation-api:FR-009], [foundation-api:FR-011]
  - Test Scenarios: [foundation-api:ReviewWorker/TS-02], [foundation-api:ITS-01], [foundation-api:ITS-05]

- Task 19: Integration tests with mock portal
  - Status: Backlog
  - Test full flow with captured HTML fixtures
  - Verify webhooks, status transitions, document downloads
  - Requirements: All FRs
  - Test Scenarios: [foundation-api:ITS-03], [foundation-api:ITS-04]

- Task 20: End-to-end smoke test with real portal (optional)
  - Status: Backlog
  - Test against live Cherwell portal with known ref
  - Flagged as optional, requires network access
  - Requirements: [foundation-api:NFR-004]
  - Test Scenarios: [foundation-api:E2E-01], [foundation-api:E2E-02]

- Task 21: Structured logging implementation
  - Status: Backlog
  - Configure structlog with JSON output
  - Add context binding for review_id, application_ref
  - Requirements: [foundation-api:NFR-006]
  - Test Scenarios: N/A (verified by log inspection)

---

## Intermediate Dead Code Tracking

| Phase Introduced | Description | Used In Phase | Status |
|------------------|-------------|---------------|--------|
| N/A | No intermediate dead code expected | N/A | N/A |

---

## Intermediate Stub Tracking

| Phase Introduced | Test Name | Reason for Stub | Implemented In Phase | Status |
|------------------|-----------|-----------------|----------------------|--------|
| N/A | No stubs expected | N/A | N/A | N/A |

---

## Requirements Validation

- [foundation-api:FR-001]: Phase 2 Task 5
- [foundation-api:FR-002]: Phase 2 Task 4, Phase 2 Task 5
- [foundation-api:FR-003]: Phase 2 Task 6
- [foundation-api:FR-004]: Phase 2 Task 4, Phase 2 Task 6
- [foundation-api:FR-005]: Phase 2 Task 7
- [foundation-api:FR-006]: Phase 2 Task 8
- [foundation-api:FR-007]: Phase 3 Task 11, Phase 3 Task 12
- [foundation-api:FR-008]: Phase 3 Task 11
- [foundation-api:FR-009]: Phase 4 Task 13, Phase 4 Task 15, Phase 5 Task 18
- [foundation-api:FR-010]: Phase 4 Task 13, Phase 4 Task 16
- [foundation-api:FR-011]: Phase 4 Task 17, Phase 5 Task 18
- [foundation-api:FR-012]: Phase 4 Task 14
- [foundation-api:FR-013]: Phase 2 Task 9
- [foundation-api:FR-014]: Phase 2 Task 5

- [foundation-api:NFR-001]: Phase 1 Task 3, Phase 2 Task 4
- [foundation-api:NFR-002]: Phase 1 Task 3, Phase 3 Task 10
- [foundation-api:NFR-003]: Phase 3 Task 11
- [foundation-api:NFR-004]: Phase 4 Task 14, Phase 5 Task 20
- [foundation-api:NFR-005]: Phase 1 Task 1, Phase 1 Task 2
- [foundation-api:NFR-006]: Phase 5 Task 21

---

## Test Scenario Validation

### Component Scenarios

- [foundation-api:ReviewRouter/TS-01]: Phase 2 Task 5
- [foundation-api:ReviewRouter/TS-02]: Phase 2 Task 5
- [foundation-api:ReviewRouter/TS-03]: Phase 2 Task 5
- [foundation-api:ReviewRouter/TS-04]: Phase 2 Task 6
- [foundation-api:ReviewRouter/TS-05]: Phase 2 Task 6
- [foundation-api:ReviewRouter/TS-06]: Phase 2 Task 6
- [foundation-api:ReviewRouter/TS-07]: Phase 2 Task 7
- [foundation-api:ReviewRouter/TS-08]: Phase 2 Task 8
- [foundation-api:ReviewRouter/TS-09]: Phase 2 Task 8
- [foundation-api:ReviewRouter/TS-10]: Phase 2 Task 6
- [foundation-api:HealthRouter/TS-01]: Phase 2 Task 9
- [foundation-api:HealthRouter/TS-02]: Phase 2 Task 9
- [foundation-api:ReviewRequestModels/TS-01]: Phase 2 Task 4
- [foundation-api:ReviewRequestModels/TS-02]: Phase 2 Task 4
- [foundation-api:ReviewRequestModels/TS-03]: Phase 2 Task 4
- [foundation-api:ReviewRequestModels/TS-04]: Phase 2 Task 4
- [foundation-api:RedisClient/TS-01]: Phase 1 Task 3
- [foundation-api:RedisClient/TS-02]: Phase 1 Task 3
- [foundation-api:RedisClient/TS-03]: Phase 1 Task 3
- [foundation-api:RedisClient/TS-04]: Phase 1 Task 3
- [foundation-api:RedisClient/TS-05]: Phase 1 Task 3
- [foundation-api:ReviewWorker/TS-01]: Phase 3 Task 10
- [foundation-api:ReviewWorker/TS-02]: Phase 5 Task 18
- [foundation-api:ReviewWorker/TS-03]: Phase 3 Task 12
- [foundation-api:ReviewWorker/TS-04]: Phase 3 Task 10
- [foundation-api:WebhookDispatcher/TS-01]: Phase 3 Task 11
- [foundation-api:WebhookDispatcher/TS-02]: Phase 3 Task 11
- [foundation-api:WebhookDispatcher/TS-03]: Phase 3 Task 11
- [foundation-api:WebhookDispatcher/TS-04]: Phase 3 Task 11
- [foundation-api:WebhookDispatcher/TS-05]: Phase 3 Task 11
- [foundation-api:CherwellParser/TS-01]: Phase 4 Task 13
- [foundation-api:CherwellParser/TS-02]: Phase 4 Task 13
- [foundation-api:CherwellParser/TS-03]: Phase 4 Task 13
- [foundation-api:CherwellParser/TS-04]: Phase 4 Task 13
- [foundation-api:CherwellScraperMCP/TS-01]: Phase 4 Task 15
- [foundation-api:CherwellScraperMCP/TS-02]: Phase 4 Task 15
- [foundation-api:CherwellScraperMCP/TS-03]: Phase 4 Task 16
- [foundation-api:CherwellScraperMCP/TS-04]: Phase 4 Task 17
- [foundation-api:CherwellScraperMCP/TS-05]: Phase 4 Task 17
- [foundation-api:CherwellScraperMCP/TS-06]: Phase 4 Task 14
- [foundation-api:CherwellScraperMCP/TS-07]: Phase 4 Task 14
- [foundation-api:CherwellScraperMCP/TS-08]: Phase 4 Task 16
- [foundation-api:DockerCompose/TS-01]: Phase 1 Task 2
- [foundation-api:DockerCompose/TS-02]: Phase 1 Task 2
- [foundation-api:DockerCompose/TS-03]: Phase 1 Task 2
- [foundation-api:ApplicationMetadataModels/TS-01]: Phase 4 Task 15
- [foundation-api:ApplicationMetadataModels/TS-02]: Phase 4 Task 15

### Integration Scenarios

- [foundation-api:ITS-01]: Phase 5 Task 18
- [foundation-api:ITS-02]: Phase 3 Task 12
- [foundation-api:ITS-03]: Phase 5 Task 19
- [foundation-api:ITS-04]: Phase 5 Task 19
- [foundation-api:ITS-05]: Phase 5 Task 18

### E2E Scenarios

- [foundation-api:E2E-01]: Phase 5 Task 20
- [foundation-api:E2E-02]: Phase 5 Task 20

---

## Appendix

### Glossary

- **arq:** Async Redis Queue - Python job queue library
- **MCP:** Model Context Protocol - tool interface for AI agents
- **ULID:** Universally Unique Lexicographically Sortable Identifier
- **HATEOAS:** Hypermedia as the Engine of Application State

### References

- [Master Design Document](../../docs/DESIGN.md)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [arq Documentation](https://arq-docs.helpmanual.io/)
- [MCP Specification](https://modelcontextprotocol.io/)

### Change History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-06 | SDD Agent | Initial design |
