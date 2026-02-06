# Project Guidelines

This file defines project-specific conventions for the Cherwell Planning Cycle Advocacy Agent that SDD agents must follow during all phases.

---

## Referenced Documentation

- [docs/DESIGN.md](../docs/DESIGN.md) - Master architecture and API specification

---

## Error Handling

### Error Categories

Errors are categorized by HTTP status codes following the API specification:

| HTTP Status | Code Pattern | Description |
|---|---|---|
| 400 | `invalid_*` | Malformed input (reference, date, etc.) |
| 401 | `unauthorized` | Missing or invalid API key |
| 404 | `*_not_found` | Resource does not exist |
| 409 | `*_already_exists`, `*_overlap`, `cannot_*` | Conflict with existing state |
| 422 | `invalid_*`, `unsupported_*` | Semantic validation failure |
| 429 | `rate_limited` | Too many requests |
| 500 | `internal_error`, `*_failed` | Server-side errors |
| 502 | `scraper_error` | External service unavailable |

### Error Response Format

All errors follow this structure:

```python
{
    "error": {
        "code": "error_code_snake_case",
        "message": "Human-readable description",
        "details": {
            # Optional context-specific fields
        }
    }
}
```

### Error Propagation

- MCP servers raise tool-specific exceptions with error codes
- Worker catches and translates to API error format
- API gateway returns consistent JSON error responses
- Webhooks include error details in `review.failed` events

---

## Logging

### Framework

Use Python's standard `logging` module with structured logging via `structlog` for JSON output in production.

### Log Levels

| Level | Usage |
|-------|-------|
| DEBUG | Detailed diagnostic info (chunk processing, embedding generation) |
| INFO | Normal operations (job started, document ingested, phase completed) |
| WARNING | Recoverable issues (OCR confidence low, retry attempt) |
| ERROR | Failed operations (scraper timeout, ingestion failure) |
| CRITICAL | System-level failures (Redis connection lost, worker crash) |

### Required Context

All log entries must include:
- `review_id` or `revision_id` when processing jobs
- `application_ref` when processing planning applications
- `component` identifying the service/module
- Timestamps in ISO 8601 format

### Example

```python
logger.info(
    "Document ingested",
    review_id=review_id,
    application_ref=application_ref,
    document_type="transport_assessment",
    chunks_created=47
)
```

---

## Naming Conventions

### Files

- Python modules: `snake_case.py`
- Dockerfiles: `Dockerfile.<service>`
- Tests: `test_<module>.py`
- Fixtures: `fixtures/<name>/`

### Classes and Types

- Classes: `PascalCase` (e.g., `PolicyRegistry`, `ReviewJob`)
- Pydantic models: `PascalCase` with descriptive suffixes
  - Request models: `*Request` (e.g., `ReviewRequest`)
  - Response models: `*Response` (e.g., `ReviewResponse`)
  - Internal models: descriptive name (e.g., `ApplicationMetadata`)
- Enums: `PascalCase` with `UPPER_SNAKE_CASE` values

### Functions and Variables

- Functions/methods: `snake_case` (e.g., `ingest_document`, `search_policy`)
- Variables: `snake_case`
- Constants: `UPPER_SNAKE_CASE`
- Private members: `_leading_underscore`

### IDs

- Review IDs: `rev_` prefix + ULID (e.g., `rev_01HQXK7V3WNPB8MTJF2R5ADGX9`)
- Delivery IDs: `dlv_` prefix + ULID
- Job IDs: `job_` prefix + ULID
- Revision IDs: `rev_<SOURCE>_<DATE>` (e.g., `rev_NPPF_2024_12`)
- Policy sources: `UPPER_SNAKE_CASE` (e.g., `LTN_1_20`, `CHERWELL_LP_2040`)

### Redis Keys

- Review records: `review:{review_id}`
- Review results: `review_result:{review_id}`
- Webhook deliveries: `webhook_deliveries:{review_id}`
- Policy documents: `policy:{source}`
- Policy revisions: `policy_revision:{source}:{revision_id}`
- Revision index: `policy_revisions:{source}`
- Policy listing: `policies_all`

---

## Testing Conventions

### Test File Locations

```
tests/
├── test_api/           # API endpoint tests
│   ├── test_reviews.py
│   ├── test_policies.py
│   └── test_webhooks.py
├── test_worker/        # Worker job tests
├── test_shared/        # Shared utilities tests
├── test_scraper.py     # Cherwell scraper tests
├── test_processor.py   # Document processor tests
└── fixtures/           # Test data (HTML, PDFs)
```

### Test Framework

- pytest as the test runner
- pytest-asyncio for async tests
- httpx for API testing
- fakeredis for Redis mocking

### Assertion Style

- Use plain `assert` statements with descriptive messages
- Prefer specific assertions over generic ones

```python
# Good
assert response.status_code == 202, f"Expected 202, got {response.status_code}"
assert result["status"] == "queued"

# Avoid
assert response.ok
```

### Mocking Conventions

- Use `pytest.fixture` for reusable mocks
- Use `unittest.mock.patch` for targeted mocking
- Mock at service boundaries, not internal implementation
- Use `fakeredis` for Redis, not mocking Redis methods

### Test Data

- Store sample HTML/PDFs in `tests/fixtures/`
- Use factory functions for generating test models
- Real Cherwell application references for integration tests: document which ones in fixtures

---

## API Conventions

### Versioning

- All endpoints prefixed with `/api/v1/`
- Breaking changes require new version

### Pagination

- Use `limit` and `offset` query parameters
- Default limit: 20, max limit: 100
- Return `total` count in list responses

### Status Codes

- 200: Success (GET, PATCH, DELETE)
- 201: Created (POST creating new resource)
- 202: Accepted (POST initiating async operation)
- 4xx: Client errors
- 5xx: Server errors

### Authentication

- Bearer token in `Authorization` header
- API keys configured via environment variable

---

## MCP Server Conventions

### Transport

- Use SSE transport for containerized deployment
- Port allocation:
  - cherwell-scraper-mcp: 3001
  - document-store-mcp: 3002
  - policy-kb-mcp: 3003

### Tool Naming

- Use `snake_case` for tool names
- Verbs first: `get_*`, `list_*`, `search_*`, `ingest_*`, `download_*`

### Error Handling

- Raise `ToolError` with descriptive message
- Include error code that maps to API error codes

---

## Docker Conventions

### Image Naming

- Base image: `cherwell-base:latest`
- Service images: `cherwell-<service>:latest`

### Volume Mounts

- `/data/chroma` - ChromaDB persistence
- `/data/raw` - Downloaded application documents
- `/data/policy` - Policy document files
- `/data/output` - Generated review outputs

### Environment Variables

- Use `.env` file for local development
- Secrets via environment variables, never in code
- All config documented in `.env.example`

---

## Additional Guidelines

### Async Patterns

- Use `async/await` throughout for I/O operations
- Use `httpx.AsyncClient` for HTTP requests
- Use `arq` for job queue processing
- Connection pools for Redis and external services

### Rate Limiting

- Cherwell scraper: configurable, default 1 req/sec
- API rate limiting: configurable per API key

### Webhook Security

- HMAC-SHA256 signing for all webhook payloads
- Store only hashed secrets in Redis
- Require HTTPS in production

### Data Retention

- Review results TTL: 30 days (configurable)
- Raw documents: keep until review expired
- Policy documents: permanent

---

## Notes for Agents

When reading this file:
1. Read the referenced DESIGN.md for full architectural context
2. Apply these conventions when making implementation decisions
3. Validate that code aligns with these guidelines
4. Flag any implementation decisions that conflict with project conventions
