# Design: Response Letter

**Version:** 1.0
**Date:** 2026-02-07
**Status:** Draft
**Linked Specification** `.sdd/response-letter/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The system already supports a complete review lifecycle: a consumer submits a review request, the worker orchestrates a multi-phase AI analysis, and the API serves results via download endpoints. The review result (stored in Redis as `review_result:{review_id}`) contains all the raw material needed for a letter — application metadata, assessment aspects with ratings, policy citations, recommendations, and suggested conditions.

The response-letter feature adds an **async letter generation pipeline** that sits alongside the existing review pipeline. It reuses the same infrastructure: arq job queue for async processing, Redis for state storage, and the Claude API for LLM generation. The letter is a new resource (`letter:{letter_id}`) that references a completed review.

### Proposed Architecture

```
Consumer                      API Gateway                  Worker
   │                              │                          │
   │ POST /reviews/{id}/letter    │                          │
   │ {stance, tone, ...}          │                          │
   │─────────────────────────────>│                          │
   │                              │ validate review complete │
   │                              │ create letter record     │
   │                              │ enqueue letter_job       │
   │      202 {letter_id, ...}    │──────────────────────── >│
   │<─────────────────────────────│                          │
   │                              │                  letter_job()
   │                              │                  fetch review result
   │                              │                  build LLM prompt
   │                              │                  call Claude API
   │                              │                  store letter content
   │                              │                  update status
   │ GET /letters/{letter_id}     │                          │
   │─────────────────────────────>│                          │
   │      200 {content, ...}      │                          │
   │<─────────────────────────────│                          │
```

**Key pattern:** The letter generation follows the same async job pattern as review generation — `POST` returns 202 with a resource ID, and a `GET` endpoint returns status or completed content.

### Technology Decisions

- **arq** for job queuing — same as review jobs, shares the worker process and Redis connection
- **Claude API** (via anthropic SDK) for letter prose generation — same client as review generation
- **Redis** for letter state/content storage — same patterns as review results with TTL
- **Pydantic** for request/response models — consistent with existing API schemas

### Quality Attributes

- **Scalability:** Letter jobs share the existing worker pool; no new services needed. The `max_jobs` limit on the worker applies to both review and letter jobs combined.
- **Maintainability:** Letter generation is isolated in its own module (`src/worker/letter_jobs.py`) with a clear interface to the review result data. The LLM prompt is a standalone module (`src/worker/letter_prompt.py`) that can be tuned independently.

---

## API Design

### POST /api/v1/reviews/{review_id}/letter — Generate Letter

Accepts a letter generation request for a completed review. Returns 202 with a letter ID.

**Request shape:**
- `stance` (required): one of `object`, `support`, `conditional`, `neutral`
- `tone` (optional, default `formal`): one of `formal`, `accessible`
- `case_officer` (optional): overrides the case officer name from scraper data
- `letter_date` (optional): YYYY-MM-DD date for the letter; defaults to today

**Response shape (202):**
- `letter_id`: unique identifier (`ltr_` + ULID)
- `review_id`: the source review
- `status`: `generating`
- `created_at`: ISO 8601 timestamp
- `links.self`: `/api/v1/letters/{letter_id}`

**Error conditions:**
- 400 `review_incomplete`: review is not in `completed` status
- 404 `review_not_found`: review ID doesn't exist
- 422 `validation_error`: invalid stance, tone, or date format

### GET /api/v1/letters/{letter_id} — Retrieve Letter

Returns the letter when generation is complete, or current status if still generating.

**Response shape (200, generating):**
- `letter_id`, `review_id`, `status: "generating"`, `created_at`

**Response shape (200, completed):**
- `letter_id`, `review_id`, `status: "completed"`
- `content`: the full letter as Markdown string
- `stance`, `tone`: the parameters used
- `metadata.model`, `metadata.input_tokens`, `metadata.output_tokens`, `metadata.processing_time_seconds`
- `created_at`, `completed_at`

**Response shape (200, failed):**
- `letter_id`, `review_id`, `status: "failed"`, `error: {code, message}`

**Error conditions:**
- 404 `letter_not_found`: letter ID doesn't exist

### Data Models

**LetterStatus enum:** `generating`, `completed`, `failed`

**LetterStance enum:** `object`, `support`, `conditional`, `neutral`

**LetterTone enum:** `formal`, `accessible`

**LetterJob (Redis record):** `letter_id`, `review_id`, `application_ref`, `stance`, `tone`, `case_officer`, `letter_date`, `status`, `content`, `metadata`, `error`, `created_at`, `completed_at`

**Redis keys:**
- `letter:{letter_id}` — letter job record (JSON, TTL 30 days)

### Error Handling Strategy

Follows project error format:
```json
{
  "error": {
    "code": "error_code",
    "message": "Human-readable message",
    "details": {}
  }
}
```

Error codes: `review_not_found`, `review_incomplete`, `letter_not_found`, `letter_generation_failed`, `validation_error`.

---

## Modified Components

### Worker Settings
**Change Description:** Currently registers four job functions (`ingest_application_documents`, `ingest_directory`, `search_documents`, `review_job`). Must add `letter_job` to the functions list so the worker can process letter generation jobs.

**Dependants:** None

**Kind:** Class (`WorkerSettings` in `src/worker/main.py`)

**Requirements References**
- [response-letter:FR-001]: Letter jobs must be processed by the worker
- [response-letter:FR-010]: LLM calls happen in the worker context which has the Anthropic API key

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| WorkerSettings/TS-01 | Letter job registered | Worker starts | Functions list is inspected | `letter_job` is in the list |

### FastAPI App Router Registration
**Change Description:** Currently registers routers for health, reviews, downloads, and policies. Must add the new letters router.

**Dependants:** None

**Kind:** Function (`create_app` in `src/api/main.py`)

**Requirements References**
- [response-letter:FR-001]: Letter endpoints must be accessible
- [response-letter:FR-008]: Letter retrieval must be routable

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| AppRouter/TS-01 | Letters router registered | App is created | Routes are inspected | `/api/v1/letters/` and `/api/v1/reviews/{review_id}/letter` routes exist |

### Docker Compose — Worker Environment
**Change Description:** The worker service environment must include the three advocacy group environment variables (`ADVOCACY_GROUP_NAME`, `ADVOCACY_GROUP_STYLISED`, `ADVOCACY_GROUP_SHORT`) so the letter prompt can read them. The API service also needs them for validation/display purposes.

**Dependants:** None

**Kind:** Configuration (`docker-compose.yml`)

**Requirements References**
- [response-letter:FR-003]: Advocacy group identity must be configurable via environment variables

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| DockerCompose/TS-01 | Env vars present | Docker compose config is read | Worker environment is inspected | `ADVOCACY_GROUP_NAME`, `ADVOCACY_GROUP_STYLISED`, `ADVOCACY_GROUP_SHORT` are defined with defaults |

---

## Added Components

### LetterRequest / LetterResponse Schemas
**Description:** Pydantic models for the letter API endpoints. `LetterRequest` validates stance (required), tone (optional), case_officer (optional), and letter_date (optional). `LetterResponse` represents the full letter resource. `LetterSubmitResponse` represents the 202 response. Includes `LetterStance`, `LetterTone`, and `LetterStatus` enums.

**Users:** Letters router, letter job

**Kind:** Module

**Location:** `src/api/schemas/letter.py`

**Requirements References**
- [response-letter:FR-001]: Request model defines the generate-letter contract
- [response-letter:FR-002]: `LetterStance` enum constrains valid stances
- [response-letter:FR-006]: `letter_date` field with date validation
- [response-letter:FR-007]: `LetterTone` enum constrains valid tones
- [response-letter:FR-008]: Response models define retrieval contract

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| LetterSchemas/TS-01 | Valid request with all fields | A request with stance=object, tone=formal, case_officer="Ms Smith", letter_date=2026-02-10 | Request is validated | All fields are parsed correctly |
| LetterSchemas/TS-02 | Minimal request (stance only) | A request with only stance=neutral | Request is validated | Defaults applied: tone=formal, case_officer=None, letter_date=None |
| LetterSchemas/TS-03 | Invalid stance rejected | A request with stance=invalid | Request is validated | Pydantic raises validation error |
| LetterSchemas/TS-04 | Invalid date rejected | A request with letter_date=not-a-date | Request is validated | Pydantic raises validation error |
| LetterSchemas/TS-05 | Invalid tone rejected | A request with tone=casual | Request is validated | Pydantic raises validation error |

### Letters Router
**Description:** FastAPI router with two endpoints: `POST /reviews/{review_id}/letter` (generate letter) and `GET /letters/{letter_id}` (retrieve letter). The POST endpoint validates the review exists and is completed, creates a letter record in Redis, enqueues a `letter_job`, and returns 202. The GET endpoint fetches the letter record and returns status or content.

**Users:** API consumers

**Kind:** Module

**Location:** `src/api/routes/letters.py`

**Requirements References**
- [response-letter:FR-001]: POST endpoint accepts generation request and returns 202
- [response-letter:FR-002]: Validates stance is one of the four valid values
- [response-letter:FR-004]: Reads case_officer from request or review application data
- [response-letter:FR-006]: Accepts optional letter_date
- [response-letter:FR-007]: Accepts optional tone
- [response-letter:FR-008]: GET endpoint returns letter content or status
- [response-letter:NFR-004]: Endpoints sit behind existing auth middleware

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| LettersRouter/TS-01 | Generate letter for completed review | A completed review exists in Redis | POST /reviews/{id}/letter with stance=object | Returns 202 with letter_id, status=generating |
| LettersRouter/TS-02 | Reject for incomplete review | A processing review exists | POST /reviews/{id}/letter | Returns 400 with review_incomplete |
| LettersRouter/TS-03 | Reject for non-existent review | No review exists with given ID | POST /reviews/{id}/letter | Returns 404 with review_not_found |
| LettersRouter/TS-04 | Retrieve completed letter | A completed letter record exists in Redis | GET /letters/{letter_id} | Returns 200 with content as Markdown and metadata |
| LettersRouter/TS-05 | Retrieve generating letter | A generating letter record exists | GET /letters/{letter_id} | Returns 200 with status=generating, no content |
| LettersRouter/TS-06 | Retrieve failed letter | A failed letter record exists | GET /letters/{letter_id} | Returns 200 with status=failed and error details |
| LettersRouter/TS-07 | Non-existent letter | No letter exists with given ID | GET /letters/{letter_id} | Returns 404 with letter_not_found |
| LettersRouter/TS-08 | Case officer from request | Request includes case_officer field | POST with case_officer="Mr Jones" | Letter record stores case_officer="Mr Jones" |
| LettersRouter/TS-09 | Custom letter date | Request includes letter_date field | POST with letter_date=2026-03-01 | Letter record stores letter_date=2026-03-01 |

### Letter Job
**Description:** arq-compatible async job function that generates a response letter. Fetches the review result from Redis, reads advocacy group config from environment, builds the LLM prompt, calls Claude, and stores the letter content in the letter record. Updates status to `completed` or `failed`.

**Users:** arq worker (called when a letter_job is dequeued)

**Kind:** Module

**Location:** `src/worker/letter_jobs.py`

**Requirements References**
- [response-letter:FR-001]: Orchestrates the letter generation workflow
- [response-letter:FR-003]: Reads ADVOCACY_GROUP_NAME/STYLISED/SHORT from environment
- [response-letter:FR-004]: Resolves case officer from review data or request override
- [response-letter:FR-005]: Passes policy citation instructions to the LLM
- [response-letter:FR-009]: Instructs the LLM to produce all required letter sections
- [response-letter:FR-010]: Calls Claude API with the review content and prompt
- [response-letter:NFR-001]: Logs generation duration
- [response-letter:NFR-002]: Logs token counts

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| LetterJob/TS-01 | Successful generation | A completed review result in Redis and a letter record with status=generating | letter_job runs | Letter record updated to status=completed with Markdown content and metadata (model, tokens, duration) |
| LetterJob/TS-02 | Review result missing | Letter record exists but review result expired from Redis | letter_job runs | Letter record updated to status=failed with error code review_result_not_found |
| LetterJob/TS-03 | LLM call fails | Review result exists but Claude API returns an error | letter_job runs | Letter record updated to status=failed with error code letter_generation_failed |
| LetterJob/TS-04 | Advocacy group from environment | ADVOCACY_GROUP_STYLISED=TestGroup set in env | letter_job runs | Prompt includes "TestGroup" as the group name |
| LetterJob/TS-05 | Default advocacy group | No ADVOCACY_GROUP_* env vars set | letter_job runs | Prompt includes "Bicester BUG" as the group name |
| LetterJob/TS-06 | Case officer from review data | Review application data includes case_officer field, no override in request | letter_job runs | Prompt addresses the case officer by name |
| LetterJob/TS-07 | Case officer fallback | No case officer in review data or request | letter_job runs | Prompt uses "Dear Sir/Madam" |

### Letter Prompt Builder
**Description:** Builds the system prompt and user prompt for the Claude API call. The system prompt instructs Claude to write a formal consultee letter (not a review) with the correct structure, tone, and group identity. The user prompt provides the review content, application metadata, stance, case officer details, and letter date. Includes instructions for inline policy citations and bibliography.

**Users:** Letter job

**Kind:** Module

**Location:** `src/worker/letter_prompt.py`

**Requirements References**
- [response-letter:FR-002]: System prompt varies framing based on stance
- [response-letter:FR-003]: Group name/stylised/short injected into prompt
- [response-letter:FR-004]: Case officer name injected into prompt
- [response-letter:FR-005]: Prompt instructs LLM to cite policies inline and include bibliography
- [response-letter:FR-007]: Tone parameter adjusts the style instructions in the system prompt
- [response-letter:FR-009]: Prompt specifies all 10 required letter sections
- [response-letter:FR-010]: Produces the prompt pair (system, user) consumed by the Claude API call

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| LetterPrompt/TS-01 | Object stance prompt | stance=object, group="Bicester BUG" | System prompt is built | Prompt instructs LLM to frame letter as an objection |
| LetterPrompt/TS-02 | Support stance prompt | stance=support | System prompt is built | Prompt instructs LLM to frame letter as support |
| LetterPrompt/TS-03 | Conditional stance prompt | stance=conditional | System prompt is built | Prompt instructs LLM to frame as support-with-conditions and include suggested conditions section |
| LetterPrompt/TS-04 | Neutral stance prompt | stance=neutral | System prompt is built | Prompt instructs LLM to provide factual comments without explicit position |
| LetterPrompt/TS-05 | Formal tone | tone=formal | System prompt is built | Prompt includes formal technical language instructions |
| LetterPrompt/TS-06 | Accessible tone | tone=accessible | System prompt is built | Prompt includes accessible, jargon-light language instructions |
| LetterPrompt/TS-07 | Bibliography instruction | Review has policy references | User prompt is built | Prompt includes instruction to add a references/bibliography section |
| LetterPrompt/TS-08 | All letter sections specified | Any stance/tone | System prompt is built | Prompt lists all 10 required letter sections |
| LetterPrompt/TS-09 | Group identity injected | Custom group env vars | System prompt is built | Group name, stylised name, and abbreviation appear in the prompt |

### Letter Redis Operations
**Description:** Extension to the existing `RedisClient` class (or standalone helper functions) providing `store_letter`, `get_letter`, and `update_letter_status` methods. Letters are stored as JSON under `letter:{letter_id}` with a 30-day TTL matching review results.

**Users:** Letters router (store initial record, retrieve), letter job (update status, store content)

**Kind:** Functions (added to `src/shared/redis_client.py` or a new helper module)

**Location:** `src/shared/redis_client.py` (added methods to existing `RedisClient` class)

**Requirements References**
- [response-letter:FR-001]: Store initial letter record when job is created
- [response-letter:FR-008]: Retrieve letter record for GET endpoint

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| LetterRedis/TS-01 | Store and retrieve letter | A letter dict is stored | get_letter is called with the same letter_id | Returns the stored letter dict |
| LetterRedis/TS-02 | Letter not found | No letter exists | get_letter is called | Returns None |
| LetterRedis/TS-03 | Update letter status | A letter record exists with status=generating | update_letter_status is called with status=completed and content | Letter record has status=completed and content field populated |
| LetterRedis/TS-04 | Letter TTL | A letter is stored | TTL is checked | Key expires after 30 days |

---

## Used Components

### RedisClient
**Location:** `src/shared/redis_client.py`

**Provides:** Connection management, `get_job()` and `get_result()` methods for fetching review data. The letter router uses `get_job()` to validate the review exists and is completed, and the letter job uses `get_result()` to fetch review content for prompt building.

**Used By:** Letters Router, Letter Job, Letter Redis Operations

### arq Job Queue
**Location:** `src/worker/main.py` (WorkerSettings)

**Provides:** Async job processing with Redis-backed queue. The letter job is enqueued via `arq_pool.enqueue_job("letter_job", ...)` from the API and processed by the worker.

**Used By:** Letters Router (enqueue), Letter Job (execution)

### Anthropic Claude API Client
**Location:** Used in `src/agent/orchestrator.py` via `anthropic.AsyncAnthropic`

**Provides:** LLM inference. The letter job creates its own `AsyncAnthropic` client (same pattern as the orchestrator) to generate the letter prose.

**Used By:** Letter Job

### Existing Auth Middleware
**Location:** `src/api/middleware/auth.py`

**Provides:** Bearer token authentication on all non-exempt routes. The new letter endpoints are automatically protected.

**Used By:** Letters Router (implicitly via middleware)

### ArqPoolDep / RedisClientDep
**Location:** `src/api/dependencies.py`

**Provides:** Dependency injection of arq connection pool and Redis client into route handlers.

**Used By:** Letters Router

---

## Documentation Considerations

- **docs/API.md** must be updated with the two new endpoints (POST /reviews/{id}/letter, GET /letters/{id}), including request/response examples, error codes, and the new enums
- **docker-compose.yml** comments should document the new `ADVOCACY_GROUP_*` environment variables
- **.env.example** (if it exists) should include the new environment variables with defaults

---

## Instrumentation

| Requirement | Observability Criteria | Implementation | Component |
|-------------|------------------------|----------------|-----------|
| [response-letter:NFR-001] | Letter generation completes within 60s for p95 | Log `letter_generation_duration_seconds` at INFO level with letter_id; store `processing_time_seconds` in letter metadata | Letter Job |
| [response-letter:NFR-002] | Average output tokens < 6,000 | Log `input_tokens` and `output_tokens` at INFO level with letter_id; store in letter metadata | Letter Job |

---

## Integration Test Scenarios

| ID | Scenario | Given | When | Then | Components Involved |
|----|----------|-------|------|------|---------------------|
| ITS-01 | Full letter generation flow | A completed review with result in Redis | POST /reviews/{id}/letter with stance=object, then letter_job runs, then GET /letters/{id} | Letter content is Markdown containing group name, case officer, policy refs, and sign-off | Letters Router, Letter Job, Letter Prompt, Letter Redis, RedisClient |
| ITS-02 | Letter with custom parameters | A completed review exists | POST with stance=conditional, tone=accessible, case_officer="Ms Smith", letter_date=2026-03-01, then job runs | Letter is dated "1 March 2026", addressed to "Ms Smith", uses accessible language, frames as conditional support | Letters Router, Letter Job, Letter Prompt, Letter Redis |
| ITS-03 | Letter generation failure handling | A completed review exists but Claude API is mocked to fail | POST, then job runs | GET returns status=failed with letter_generation_failed error | Letters Router, Letter Job, Letter Redis |

---

## E2E Test Scenarios

| ID | Scenario | Given | When | Then | User Journey |
|----|----------|-------|------|------|--------------|
| E2E-01 | Generate and retrieve objection letter | A completed review for application 25/01178/REM | 1. POST /reviews/{id}/letter {stance: "object"} 2. Poll GET /letters/{id} until completed 3. Read content | Markdown letter contains: "Bicester BUG" header, "Dear" salutation, application reference in subject, objection framing, policy citations, recommendations, sign-off "On behalf of Bicester Bike Users' Group (BBUG)", references section | Submit letter request → wait for generation → retrieve completed letter |
| E2E-02 | Generate conditional support letter with overrides | A completed review | 1. POST /reviews/{id}/letter {stance: "conditional", tone: "accessible", case_officer: "Mr A. Jones", letter_date: "2026-03-15"} 2. Poll GET /letters/{id} | Letter dated "15 March 2026", addressed "Dear Mr A. Jones", accessible language, conditional support framing with conditions section | Submit with all overrides → retrieve and verify |

---

## Test Data

- Reuse existing review result fixtures from `tests/fixtures/` — a completed review result dict with application metadata, review content (aspects, recommendations, conditions, policy_refs), and metadata
- Add a fixture `tests/fixtures/completed_review_result.json` containing a representative completed review result for letter generation tests
- Mock the Claude API response in unit tests with a sample letter Markdown

---

## Test Feasibility

- Unit tests for schemas, prompt builder, and Redis operations require no special infrastructure (fakeredis, pure Python)
- Integration tests for the letter job require mocking the Anthropic client (same approach as `test_review_jobs.py`)
- E2E tests require a running Redis instance and mocked Anthropic client
- No missing infrastructure — all test patterns are established in the codebase

---

## Risks and Dependencies

**Technical Risks:**
1. **LLM output quality inconsistency** — The LLM may occasionally omit required sections or produce poor citations. *Mitigation:* Detailed system prompt with explicit section checklist; integration tests validate section presence.
2. **Token limit exceeded** — Long reviews with many policy references may produce prompts exceeding context limits. *Mitigation:* Truncate review content if needed; use the same model as review generation which handles similar-sized inputs.
3. **Concurrent letter requests for same review** — Multiple letters could be generated for the same review. *Mitigation:* This is acceptable — each letter is an independent resource with its own parameters (stance, tone). No dedup needed.

**External Dependencies:**
- Anthropic Claude API (same dependency as review generation — already integrated)
- Redis (same dependency as all other features — already integrated)

**Assumptions:**
- The `ANTHROPIC_API_KEY` environment variable is available in the worker context (already true for review generation)
- Review results remain in Redis long enough for letter generation (30-day TTL is sufficient)

---

## Feasability Review

No blocking dependencies. All required infrastructure (arq, Redis, Claude API, FastAPI) is already in place and proven. The feature is a pure addition — no existing functionality is at risk.

---

## Task Breakdown

> **CRITICAL: Tests are written WITH implementation, not after.**

### Phase 1: Core Models and Redis Storage

- Task 1: Add letter schemas (enums, request/response models)
  - Status: Done
  - Create `src/api/schemas/letter.py` with `LetterStance`, `LetterTone`, `LetterStatus` enums and `LetterRequest`, `LetterSubmitResponse`, `LetterResponse` Pydantic models. Update `src/api/schemas/__init__.py` to re-export. Write tests validating all enum values, required fields, defaults, and validation errors.
  - Requirements: [response-letter:FR-002], [response-letter:FR-006], [response-letter:FR-007]
  - Test Scenarios: [response-letter:LetterSchemas/TS-01], [response-letter:LetterSchemas/TS-02], [response-letter:LetterSchemas/TS-03], [response-letter:LetterSchemas/TS-04], [response-letter:LetterSchemas/TS-05]

- Task 2: Add letter Redis operations to RedisClient
  - Status: Done
  - Add `store_letter()`, `get_letter()`, and `update_letter_status()` methods to `RedisClient`. Letters are stored as JSON under `letter:{letter_id}` with 30-day TTL. Write tests with fakeredis.
  - Requirements: [response-letter:FR-001], [response-letter:FR-008]
  - Test Scenarios: [response-letter:LetterRedis/TS-01], [response-letter:LetterRedis/TS-02], [response-letter:LetterRedis/TS-03], [response-letter:LetterRedis/TS-04]

### Phase 2: Letter Prompt and Job

- Task 3: Build letter prompt module
  - Status: Done
  - Create `src/worker/letter_prompt.py` with a `build_letter_prompt(review_result, stance, tone, group_name, group_stylised, group_short, case_officer, letter_date, policy_revisions)` function returning `(system_prompt, user_prompt)`. The system prompt instructs Claude to write a consultee letter with all 10 required sections, the correct stance framing, and the specified tone. The user prompt provides the full review content and metadata. Write unit tests for each stance, tone, and edge case.
  - Requirements: [response-letter:FR-002], [response-letter:FR-003], [response-letter:FR-004], [response-letter:FR-005], [response-letter:FR-007], [response-letter:FR-009], [response-letter:FR-010]
  - Test Scenarios: [response-letter:LetterPrompt/TS-01], [response-letter:LetterPrompt/TS-02], [response-letter:LetterPrompt/TS-03], [response-letter:LetterPrompt/TS-04], [response-letter:LetterPrompt/TS-05], [response-letter:LetterPrompt/TS-06], [response-letter:LetterPrompt/TS-07], [response-letter:LetterPrompt/TS-08], [response-letter:LetterPrompt/TS-09]

- Task 4: Implement letter job
  - Status: Done
  - Create `src/worker/letter_jobs.py` with `letter_job(ctx, letter_id, review_id)` function. Fetches review result from Redis, reads advocacy group config from environment (with defaults), resolves case officer, calls `build_letter_prompt()`, calls Claude API, stores content and metadata in letter record, updates status. Handles errors (missing result, API failure). Write tests with mocked Claude client and fakeredis.
  - Requirements: [response-letter:FR-001], [response-letter:FR-003], [response-letter:FR-004], [response-letter:FR-010], [response-letter:NFR-001], [response-letter:NFR-002]
  - Test Scenarios: [response-letter:LetterJob/TS-01], [response-letter:LetterJob/TS-02], [response-letter:LetterJob/TS-03], [response-letter:LetterJob/TS-04], [response-letter:LetterJob/TS-05], [response-letter:LetterJob/TS-06], [response-letter:LetterJob/TS-07]

### Phase 3: API Endpoints and Wiring

- Task 5: Implement letters router
  - Status: Done
  - Create `src/api/routes/letters.py` with `POST /reviews/{review_id}/letter` and `GET /letters/{letter_id}` endpoints. POST validates review status, generates letter_id (`ltr_` + ULID), resolves case_officer (request override → review data → None), stores initial letter record, enqueues `letter_job`, returns 202. GET fetches letter record and returns status or content. Write tests with httpx TestClient, mocked Redis and arq pool.
  - Requirements: [response-letter:FR-001], [response-letter:FR-004], [response-letter:FR-006], [response-letter:FR-008], [response-letter:NFR-004]
  - Test Scenarios: [response-letter:LettersRouter/TS-01], [response-letter:LettersRouter/TS-02], [response-letter:LettersRouter/TS-03], [response-letter:LettersRouter/TS-04], [response-letter:LettersRouter/TS-05], [response-letter:LettersRouter/TS-06], [response-letter:LettersRouter/TS-07], [response-letter:LettersRouter/TS-08], [response-letter:LettersRouter/TS-09]

- Task 6: Register router and worker function
  - Status: Done
  - Add `letters` router to `src/api/main.py` (`app.include_router`). Add `letter_job` to `WorkerSettings.functions` in `src/worker/main.py`. Add `ADVOCACY_GROUP_NAME`, `ADVOCACY_GROUP_STYLISED`, `ADVOCACY_GROUP_SHORT` environment variables to both `api` and `worker` services in `docker-compose.yml` with defaults. Write smoke test verifying routes exist and worker function is registered.
  - Requirements: [response-letter:FR-001], [response-letter:FR-003]
  - Test Scenarios: [response-letter:WorkerSettings/TS-01], [response-letter:AppRouter/TS-01], [response-letter:DockerCompose/TS-01]

### Phase 4: Integration, E2E, and Documentation

- Task 7: Integration and E2E tests
  - Status: Done
  - Write integration tests verifying the full flow: POST letter → job runs → GET returns content. Test with mocked Claude client returning realistic letter Markdown. Validate letter content contains required sections (group name, salutation, subject, body, sign-off, bibliography). Test with all four stances and both tones. Test custom parameters (case_officer, letter_date). Test error paths (Claude failure).
  - Requirements: [response-letter:FR-001], [response-letter:FR-002], [response-letter:FR-004], [response-letter:FR-005], [response-letter:FR-009], [response-letter:NFR-003]
  - Test Scenarios: [response-letter:ITS-01], [response-letter:ITS-02], [response-letter:ITS-03], [response-letter:E2E-01], [response-letter:E2E-02]

- Task 8: Update API documentation
  - Status: Done
  - Update `docs/API.md` with the two new endpoints, request/response schemas, error codes, and curl examples. Add the new enums (LetterStance, LetterTone, LetterStatus) to the Enums section. Document the ADVOCACY_GROUP_* environment variables.
  - Requirements: [response-letter:FR-001], [response-letter:FR-008]
  - Test Scenarios: N/A (documentation task)

---

## Intermediate Dead Code Tracking

| Phase Introduced | Description | Used In Phase | Status |
|------------------|-------------|---------------|--------|
| — | No dead code expected | — | — |

---

## Intermediate Stub Tracking

| Phase Introduced | Test Name | Reason for Stub | Implemented In Phase | Status |
|------------------|-----------|-----------------|----------------------|--------|
| — | No stubs expected | — | — | — |

---

## Requirements Validation

- [response-letter:FR-001] Generate Response Letter
  - Phase 1 Task 2, Phase 2 Task 4, Phase 3 Task 5, Phase 3 Task 6
- [response-letter:FR-002] Stance Selection
  - Phase 1 Task 1, Phase 2 Task 3
- [response-letter:FR-003] Advocacy Group Configuration
  - Phase 2 Task 3, Phase 2 Task 4, Phase 3 Task 6
- [response-letter:FR-004] Case Officer Addressing
  - Phase 2 Task 3, Phase 2 Task 4, Phase 3 Task 5
- [response-letter:FR-005] Inline Policy Citations with Bibliography
  - Phase 2 Task 3
- [response-letter:FR-006] Letter Date
  - Phase 1 Task 1, Phase 3 Task 5
- [response-letter:FR-007] Tone Selection
  - Phase 1 Task 1, Phase 2 Task 3
- [response-letter:FR-008] Retrieve Generated Letter
  - Phase 1 Task 1, Phase 1 Task 2, Phase 3 Task 5
- [response-letter:FR-009] Letter Content Structure
  - Phase 2 Task 3
- [response-letter:FR-010] LLM-Based Letter Generation
  - Phase 2 Task 3, Phase 2 Task 4
- [response-letter:NFR-001] Generation Latency
  - Phase 2 Task 4
- [response-letter:NFR-002] Token Efficiency
  - Phase 2 Task 4
- [response-letter:NFR-003] Consistent Letter Quality
  - Phase 4 Task 7
- [response-letter:NFR-004] Authentication
  - Phase 3 Task 5

---

## Test Scenario Validation

### Component Scenarios
- [response-letter:LetterSchemas/TS-01]: Phase 1 Task 1
- [response-letter:LetterSchemas/TS-02]: Phase 1 Task 1
- [response-letter:LetterSchemas/TS-03]: Phase 1 Task 1
- [response-letter:LetterSchemas/TS-04]: Phase 1 Task 1
- [response-letter:LetterSchemas/TS-05]: Phase 1 Task 1
- [response-letter:LetterRedis/TS-01]: Phase 1 Task 2
- [response-letter:LetterRedis/TS-02]: Phase 1 Task 2
- [response-letter:LetterRedis/TS-03]: Phase 1 Task 2
- [response-letter:LetterRedis/TS-04]: Phase 1 Task 2
- [response-letter:LetterPrompt/TS-01]: Phase 2 Task 3
- [response-letter:LetterPrompt/TS-02]: Phase 2 Task 3
- [response-letter:LetterPrompt/TS-03]: Phase 2 Task 3
- [response-letter:LetterPrompt/TS-04]: Phase 2 Task 3
- [response-letter:LetterPrompt/TS-05]: Phase 2 Task 3
- [response-letter:LetterPrompt/TS-06]: Phase 2 Task 3
- [response-letter:LetterPrompt/TS-07]: Phase 2 Task 3
- [response-letter:LetterPrompt/TS-08]: Phase 2 Task 3
- [response-letter:LetterPrompt/TS-09]: Phase 2 Task 3
- [response-letter:LetterJob/TS-01]: Phase 2 Task 4
- [response-letter:LetterJob/TS-02]: Phase 2 Task 4
- [response-letter:LetterJob/TS-03]: Phase 2 Task 4
- [response-letter:LetterJob/TS-04]: Phase 2 Task 4
- [response-letter:LetterJob/TS-05]: Phase 2 Task 4
- [response-letter:LetterJob/TS-06]: Phase 2 Task 4
- [response-letter:LetterJob/TS-07]: Phase 2 Task 4
- [response-letter:LettersRouter/TS-01]: Phase 3 Task 5
- [response-letter:LettersRouter/TS-02]: Phase 3 Task 5
- [response-letter:LettersRouter/TS-03]: Phase 3 Task 5
- [response-letter:LettersRouter/TS-04]: Phase 3 Task 5
- [response-letter:LettersRouter/TS-05]: Phase 3 Task 5
- [response-letter:LettersRouter/TS-06]: Phase 3 Task 5
- [response-letter:LettersRouter/TS-07]: Phase 3 Task 5
- [response-letter:LettersRouter/TS-08]: Phase 3 Task 5
- [response-letter:LettersRouter/TS-09]: Phase 3 Task 5
- [response-letter:WorkerSettings/TS-01]: Phase 3 Task 6
- [response-letter:AppRouter/TS-01]: Phase 3 Task 6
- [response-letter:DockerCompose/TS-01]: Phase 3 Task 6

### Integration Scenarios
- [response-letter:ITS-01]: Phase 4 Task 7
- [response-letter:ITS-02]: Phase 4 Task 7
- [response-letter:ITS-03]: Phase 4 Task 7

### E2E Scenarios
- [response-letter:E2E-01]: Phase 4 Task 7
- [response-letter:E2E-02]: Phase 4 Task 7

---

## Appendix

### Glossary
- **Letter job:** An arq background job that generates a consultee letter from a review result
- **Stance:** The advocacy group's position: object, support, conditional, neutral
- **Letter prompt:** The system + user prompt pair sent to Claude to generate the letter
- **ULID:** Universally Unique Lexicographically Sortable Identifier — used for letter IDs with `ltr_` prefix

### References
- [.sdd/response-letter/specification.md](specification.md) — Feature specification
- [docs/API.md](../../docs/API.md) — API reference
- [src/worker/review_jobs.py](../../src/worker/review_jobs.py) — Review job pattern to follow
- [src/api/routes/downloads.py](../../src/api/routes/downloads.py) — Download endpoint pattern
- [src/agent/orchestrator.py](../../src/agent/orchestrator.py) — Claude API usage pattern

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-07 | SDD | Initial design |

---
