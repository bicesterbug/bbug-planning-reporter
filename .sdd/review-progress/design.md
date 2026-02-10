# Design: Review Progress Reporting

**Version:** 1.0
**Date:** 2026-02-10
**Status:** Draft
**Linked Specification** `.sdd/review-progress/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The review system has two separate progress tracking paths:

1. **ProgressTracker** (`src/agent/progress.py`) — Tracks workflow state in `workflow_state:{review_id}` Redis key and publishes `review.progress` events via Redis pub/sub. Used by the orchestrator during processing.

2. **ReviewJob** (`src/shared/models.py`) — Persisted in `review:{review_id}` Redis key, includes an optional `progress: ReviewProgress | None` field. Read by the API status endpoints.

The gap: `ProgressTracker` never writes to `ReviewJob.progress`. The API reads `ReviewJob` which always has `progress: None` during processing.

### Proposed Architecture

Bridge the gap by having `ProgressTracker` also update the `ReviewJob.progress` field whenever progress changes. The `ProgressTracker` already calls `_save_state()` and `_publish_progress()` on every phase transition and sub-progress update. We add a third action: `_sync_job_progress()` that writes a `ReviewProgress` snapshot into the `ReviewJob` record.

Sequence:
1. Orchestrator calls `tracker.start_phase()` or `tracker.update_sub_progress()`
2. ProgressTracker saves workflow state to `workflow_state:{review_id}` (existing)
3. ProgressTracker publishes pub/sub event (existing)
4. **NEW**: ProgressTracker builds a `ReviewProgress` dict and writes it into `review:{review_id}` via direct Redis update

The status endpoint (`GET /api/v1/reviews/{id}/status`) continues to read from `ReviewJob` as before — no endpoint changes needed.

### Technology Decisions

- **Direct Redis key update instead of RedisClient wrapper**: The `ProgressTracker` already holds a raw `redis.asyncio.Redis` client, not a `RedisClient` wrapper. Rather than introducing a dependency on `RedisClient`, we perform a targeted JSON patch on the existing `review:{review_id}` key. This avoids coupling ProgressTracker to the RedisClient class and avoids the read-modify-write overhead of `update_job_status()` (which does GET + full model parse + SET).
- **JSON patch approach**: Read the existing job JSON, parse as dict, update only the `progress` field, write back. This is one Redis GET + one SET per progress update — same cost as the existing `_save_state()` call.

### Quality Attributes

- **Performance**: One additional Redis round-trip per progress update (in the worker, not in the API endpoint path). The status endpoint performs zero additional calls — it reads progress from the existing `ReviewJob` fetch.
- **Maintainability**: Single new private method `_sync_job_progress()` in ProgressTracker. Clear, isolated responsibility.

---

## API Design

No API changes required. The existing `ReviewProgressResponse` schema and the `progress` field on `ReviewStatusResponse` and `ReviewResponse` already support the required shape. They are simply always null because `ReviewJob.progress` is never populated.

The status endpoint already maps `ReviewProgress` to `ReviewProgressResponse`:

```
{
  "phase": "downloading_documents",
  "phase_number": 2,
  "total_phases": 5,
  "percent_complete": 27,
  "detail": "Downloaded 5 of 12 documents"
}
```

Note: The lightweight status endpoint (`/status`) currently only returns `phase` and `percent_complete` from `ReviewProgressResponse`. It should also include `phase_number`, `total_phases`, and `detail` for consistency with FR-001.

---

## Modified Components

### ProgressTracker._sync_job_progress

**Change Description** Currently, `ProgressTracker` writes progress to `workflow_state:{review_id}` (via `_save_state()`) and publishes to pub/sub (via `_publish_progress()`), but never updates the `review:{review_id}` key that the API reads. A new private method `_sync_job_progress()` will be added that reads the `review:{review_id}` JSON, updates its `progress` field with a `ReviewProgress`-shaped dict, and writes it back. This method will be called from `start_phase()`, `update_sub_progress()`, `complete_workflow()`, and `start_workflow()`.

**Dependants** None — this is an internal addition. API endpoints already read `ReviewJob.progress`.

**Kind** Method (added to existing Class `ProgressTracker`)

**Requirements References**
- [review-progress:FR-001]: Progress must be non-null on status endpoint during processing — requires writing to ReviewJob
- [review-progress:FR-002]: Progress must be non-null on full review endpoint during processing — same mechanism
- [review-progress:FR-003]: Progress must reflect current phase and sub-progress — sync happens on every update
- [review-progress:FR-004]: Progress must be null for non-processing statuses — `complete_workflow()` sets progress to null
- [review-progress:NFR-001]: Progress update latency < 1 second — sync is synchronous with phase/sub-progress update
- [review-progress:NFR-002]: No additional Redis round-trips on status endpoint — updates happen in worker, not API

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| ProgressTracker/TS-01 | Phase start syncs progress to job | ProgressTracker with Redis, review job exists in Redis | `start_phase(DOWNLOADING_DOCUMENTS)` called | `review:{review_id}` JSON contains `progress.phase = "downloading_documents"`, `progress.phase_number = 2`, `progress.total_phases = 5` |
| ProgressTracker/TS-02 | Sub-progress syncs to job | ProgressTracker in DOWNLOADING_DOCUMENTS phase | `update_sub_progress("Downloaded 5 of 12", current=5, total=12)` called | `review:{review_id}` JSON contains `progress.detail = "Downloaded 5 of 12"`, `progress.percent_complete` reflects weighted calculation |
| ProgressTracker/TS-03 | Workflow completion clears progress | ProgressTracker in final phase | `complete_workflow(success=True)` called | `review:{review_id}` JSON contains `progress = null` |
| ProgressTracker/TS-04 | Sync tolerates missing job key | ProgressTracker with Redis | `start_phase()` called but `review:{review_id}` key does not exist in Redis | No error raised, sync silently skipped |
| ProgressTracker/TS-05 | Sync tolerates Redis failure | ProgressTracker with Redis that raises on GET | `start_phase()` called | No error raised, sync failure logged as warning |

### ReviewStatusResponse mapping (status endpoint)

**Change Description** The lightweight status endpoint currently only passes `phase` and `percent_complete` to `ReviewProgressResponse` when building the response. It should pass all five fields (`phase`, `phase_number`, `total_phases`, `percent_complete`, `detail`) to match the specification.

**Dependants** None

**Kind** Function (route handler in `src/api/routes/reviews.py`)

**Requirements References**
- [review-progress:FR-001]: Status endpoint must return phase_number, total_phases, and detail

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| ReviewStatusEndpoint/TS-01 | Status endpoint returns full progress | Review job with progress in Redis | `GET /api/v1/reviews/{id}/status` | Response contains `progress.phase`, `progress.phase_number`, `progress.total_phases`, `progress.percent_complete`, `progress.detail` |
| ReviewStatusEndpoint/TS-02 | Status endpoint returns null progress for queued | Review job with status `queued` | `GET /api/v1/reviews/{id}/status` | Response contains `progress: null` |

---

## Added Components

### PHASE_NUMBER_MAP

**Description** A constant dict mapping `ReviewPhase` enum values to their 1-based position number (1-5). Used by `_sync_job_progress()` to populate the `phase_number` field.

**Users** `ProgressTracker._sync_job_progress()`

**Kind** Module-level constant

**Location** `src/agent/progress.py`

**Requirements References**
- [review-progress:FR-001]: Progress must include `phase_number`
- [review-progress:FR-003]: Phase number must reflect current phase

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| PhaseNumberMap/TS-01 | All phases have sequential numbers | PHASE_NUMBER_MAP constant | Inspect all entries | 5 entries, values 1 through 5, matching ReviewPhase enum order |

---

## Used Components

### ReviewJob model
**Location** `src/shared/models.py`

**Provides** `ReviewProgress` Pydantic model with fields: `phase`, `phase_number`, `total_phases`, `percent_complete`, `detail`. The `ReviewJob` model includes `progress: ReviewProgress | None`.

**Used By** ProgressTracker._sync_job_progress (to understand the target schema), API routes (to read progress)

### RedisClient.update_job_status
**Location** `src/shared/redis_client.py`

**Provides** Method that accepts `progress: dict` parameter and writes it to the ReviewJob. Already supports progress updates.

**Used By** Not directly used — ProgressTracker does a direct Redis update instead. Referenced as an alternative that was considered but rejected for performance reasons.

### API status endpoint
**Location** `src/api/routes/reviews.py`

**Provides** `GET /api/v1/reviews/{id}/status` and `GET /api/v1/reviews/{id}` endpoints that read `ReviewJob.progress` and return `ReviewProgressResponse`.

**Used By** API consumers polling for progress

---

## Documentation Considerations

- No new API documentation needed — the `progress` field already exists in the API schema; it will simply be populated during processing instead of always being null
- No README changes needed

---

## Instrumentation (if needed)

N/A — Both NFRs are verified via testing and code review, not observability.

---

## Integration Test Scenarios (if needed)

| ID | Scenario | Given | When | Then | Components Involved |
|----|----------|-------|------|------|---------------------|
| ITS-01 | Progress visible on status endpoint during processing | A review job in Redis with `status: processing` and a ProgressTracker syncing progress | ProgressTracker calls `start_phase(DOWNLOADING_DOCUMENTS)` then `update_sub_progress("Downloaded 5 of 12", 5, 12)` | `GET /api/v1/reviews/{id}/status` returns `progress` with `phase: "downloading_documents"`, `phase_number: 2`, `percent_complete > 0`, `detail: "Downloaded 5 of 12"` | ProgressTracker, Redis, API status endpoint |
| ITS-02 | Progress null after completion | A review job that was processing with progress | Review completes, `complete_workflow(success=True)` called, job status updated to `completed` | `GET /api/v1/reviews/{id}/status` returns `progress: null` | ProgressTracker, Redis, API status endpoint |

---

## E2E Test Scenarios (if needed)

N/A — E2E testing would require running the full orchestrator with MCP servers. The integration tests above cover the progress wiring sufficiently. The existing E2E test infrastructure does not support mid-workflow status polling.

---

## Test Data

- Fake review job JSON stored in fakeredis for integration tests
- Review ID: `rev_test_progress` with `application_ref: "25/00001/F"`
- Pre-stored job with `status: processing` and `progress: null`

---

## Test Feasibility

- All tests can use fakeredis and the existing test infrastructure
- No external dependencies required
- No missing test infrastructure

---

## Risks and Dependencies

- **Risk**: Direct JSON patch on the `review:{review_id}` key could conflict with concurrent `update_job_status()` calls (e.g., worker setting status to `failed` at the same time as a progress sync). **Mitigation**: The worker is single-threaded per job — only one coroutine modifies the job at a time. Progress syncs happen within the orchestrator's sequential phase execution, not concurrently with status updates.
- **Risk**: Adding an extra Redis round-trip per sub-progress update could slow down document download/ingestion phases. **Mitigation**: Each round-trip is <1ms on local Redis. For 100 documents with per-document sub-progress, that's ~100ms total overhead — negligible compared to the 5+ minute review workflow.

---

## Feasability Review

- No missing features or infrastructure. All required components already exist.
- The `ReviewProgress` model, `ReviewProgressResponse` schema, and `progress` field on `ReviewJob` are already implemented. This is purely a wiring fix.

---

## Task Breakdown

> **CRITICAL: Tests are written WITH implementation, not after.**

### Phase 1: Wire progress to ReviewJob

- Task 1: Add `PHASE_NUMBER_MAP` constant and `_sync_job_progress()` method to ProgressTracker
  - Status: Done
  - Add `PHASE_NUMBER_MAP: dict[ReviewPhase, int]` mapping each phase to its 1-based number
  - Add `_sync_job_progress()` private method that reads `review:{review_id}`, updates the `progress` field with a `ReviewProgress`-shaped dict (phase, phase_number, total_phases, percent_complete, detail), and writes it back
  - Call `_sync_job_progress()` from `start_phase()`, `update_sub_progress()`, and `start_workflow()`
  - In `complete_workflow()`, call `_sync_job_progress()` with `progress=None` to clear it
  - Write unit tests using mock Redis verifying all 5 ProgressTracker test scenarios and the PhaseNumberMap scenario
  - Requirements: [review-progress:FR-001], [review-progress:FR-002], [review-progress:FR-003], [review-progress:FR-004], [review-progress:NFR-001], [review-progress:NFR-002]
  - Test Scenarios: [review-progress:ProgressTracker/TS-01], [review-progress:ProgressTracker/TS-02], [review-progress:ProgressTracker/TS-03], [review-progress:ProgressTracker/TS-04], [review-progress:ProgressTracker/TS-05], [review-progress:PhaseNumberMap/TS-01]

- Task 2: Fix status endpoint to return all progress fields
  - Status: Done
  - Update the `get_review_status()` route handler to pass all 5 fields (`phase`, `phase_number`, `total_phases`, `percent_complete`, `detail`) to `ReviewProgressResponse` instead of only `phase` and `percent_complete`
  - Write unit tests verifying both status endpoint test scenarios
  - Requirements: [review-progress:FR-001]
  - Test Scenarios: [review-progress:ReviewStatusEndpoint/TS-01], [review-progress:ReviewStatusEndpoint/TS-02]

- Task 3: Integration test — progress visible on status endpoint
  - Status: Done
  - Write integration tests using fakeredis that exercise the full path: ProgressTracker syncs progress → API endpoint reads it
  - Requirements: [review-progress:FR-001], [review-progress:FR-002], [review-progress:FR-004]
  - Test Scenarios: [review-progress:ITS-01], [review-progress:ITS-02]

---

## Intermediate Dead Code Tracking

| Phase Introduced | Description | Used In Phase | Status |
|------------------|-------------|---------------|--------|
| N/A | No dead code introduced | N/A | N/A |

---

## Intermediate Stub Tracking

| Phase Introduced | Test Name | Reason for Stub | Implemented In Phase | Status |
|------------------|-----------|-----------------|----------------------|--------|
| N/A | No stubs | N/A | N/A | N/A |

---

## Requirements Validation

- [review-progress:FR-001]
  - Phase 1 Task 1 (sync progress to job on start_phase/update_sub_progress)
  - Phase 1 Task 2 (status endpoint returns all progress fields)
  - Phase 1 Task 3 (integration test verifies end-to-end)
- [review-progress:FR-002]
  - Phase 1 Task 1 (same sync mechanism serves both endpoints)
  - Phase 1 Task 3 (integration test)
- [review-progress:FR-003]
  - Phase 1 Task 1 (sync reflects current phase and sub-progress)
- [review-progress:FR-004]
  - Phase 1 Task 1 (complete_workflow sets progress to null)
  - Phase 1 Task 3 (integration test verifies null after completion)

- [review-progress:NFR-001]
  - Phase 1 Task 1 (sync is synchronous — verified by unit test showing _sync_job_progress called in same await chain)
- [review-progress:NFR-002]
  - Phase 1 Task 1 (updates happen in worker, endpoint reads from existing fetch — verified by code review)

---

## Test Scenario Validation

### Component Scenarios
- [review-progress:ProgressTracker/TS-01]: Phase 1 Task 1
- [review-progress:ProgressTracker/TS-02]: Phase 1 Task 1
- [review-progress:ProgressTracker/TS-03]: Phase 1 Task 1
- [review-progress:ProgressTracker/TS-04]: Phase 1 Task 1
- [review-progress:ProgressTracker/TS-05]: Phase 1 Task 1
- [review-progress:PhaseNumberMap/TS-01]: Phase 1 Task 1
- [review-progress:ReviewStatusEndpoint/TS-01]: Phase 1 Task 2
- [review-progress:ReviewStatusEndpoint/TS-02]: Phase 1 Task 2

### Integration Scenarios
- [review-progress:ITS-01]: Phase 1 Task 3
- [review-progress:ITS-02]: Phase 1 Task 3

### E2E Scenarios
- N/A

---

## Appendix

### Glossary
- **ProgressTracker**: Class in `src/agent/progress.py` that tracks workflow phase state in Redis key `workflow_state:{review_id}`
- **ReviewJob**: Pydantic model in `src/shared/models.py` persisted in Redis key `review:{review_id}`, includes optional `progress` field
- **ReviewProgress**: Pydantic model with fields: phase, phase_number, total_phases, percent_complete, detail
- **WorkflowState**: Dataclass in `src/agent/progress.py` that holds per-phase timing and sub-progress data

### References
- [review-progress specification](.sdd/review-progress/specification.md)
- [foundation-api specification](.sdd/foundation-api/specification.md) — FR-003 originally defined the progress requirement
- `src/agent/progress.py` — existing ProgressTracker implementation
- `src/shared/models.py` — ReviewProgress and ReviewJob models
- `src/api/schemas.py` — ReviewProgressResponse API schema

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-10 | Claude | Initial design |

---
