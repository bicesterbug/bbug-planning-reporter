# Specification: Review Progress Reporting

**Version:** 1.0
**Date:** 2026-02-10
**Status:** Draft

---

## Problem Statement

The review status endpoint (`GET /api/v1/reviews/{review_id}/status`) always returns `"progress": null` during processing, even though the system already tracks detailed phase and sub-progress internally via `ProgressTracker`. API consumers have no visibility into review progress and cannot tell whether a 5-minute review is stuck or at 80%.

## Beneficiaries

**Primary:**
- API consumers (n8n automations, future web UI) polling the status endpoint to show progress to end users

**Secondary:**
- Operators debugging slow or stuck reviews by inspecting the status endpoint

---

## Outcomes

**Must Haves**
- The status endpoint returns non-null `progress` with phase name, phase number, percent complete, and detail text while a review is processing
- Progress updates at every phase transition and at sub-progress updates within phases (e.g. "Downloaded 5 of 12 documents")

**Nice-to-haves**
- None

---

## Explicitly Out of Scope

- WebSocket or SSE streaming of progress events (pub/sub already exists for this — this feature only addresses the polling endpoint)
- Changing the progress weighting algorithm (already implemented in `ProgressTracker.calculate_percent_complete`)
- Adding new phases or changing the orchestrator workflow
- Persisting progress history after review completion (progress is only meaningful while processing)

---

## Functional Requirements

### FR-001: Populate progress on status endpoint during processing
**Description:** When a review has status `processing`, the `GET /api/v1/reviews/{review_id}/status` endpoint must return a non-null `progress` object containing `phase`, `phase_number`, `total_phases`, `percent_complete`, and `detail`.

**Examples:**
- Positive case: Review is in phase 2 downloading documents — response includes `{"phase": "downloading_documents", "phase_number": 2, "total_phases": 5, "percent_complete": 27, "detail": "Downloaded 5 of 12 documents"}`
- Edge case: Review just started and is in phase 1 with no sub-progress — response includes `{"phase": "fetching_metadata", "phase_number": 1, "total_phases": 5, "percent_complete": 0, "detail": null}`

### FR-002: Populate progress on full review endpoint during processing
**Description:** The `GET /api/v1/reviews/{review_id}` endpoint must also include the same non-null `progress` object when the review is processing.

**Examples:**
- Positive case: Same progress data as FR-001, included in the full review response body

### FR-003: Progress reflects current phase and sub-progress
**Description:** The progress object must reflect the ProgressTracker's current state: the active phase, the weighted percent complete across all phases, and the latest sub-progress detail string.

**Examples:**
- Positive case: Orchestrator enters phase 3 (ingesting), ProgressTracker records phase transition — next status poll returns `phase: "ingesting_documents"`, `phase_number: 3`
- Positive case: Within phase 2, sub-progress updates to "Downloaded 8 of 12 documents" — next status poll returns `detail: "Downloaded 8 of 12 documents"`, `percent_complete: 38`

### FR-004: Progress is null for non-processing statuses
**Description:** When a review is `queued`, `completed`, `failed`, or `cancelled`, the `progress` field must be null.

**Examples:**
- Positive case: Completed review returns `"progress": null`
- Positive case: Queued review returns `"progress": null`

---

## Non-Functional Requirements

### NFR-001: Progress update latency
**Category:** Performance
**Description:** Progress data visible to the status endpoint must reflect the ProgressTracker state within 1 second of an update.
**Acceptance Threshold:** < 1 second between ProgressTracker.update_sub_progress() call and visibility on status endpoint poll
**Verification:** Testing — unit test verifies progress data is written synchronously with phase/sub-progress updates

### NFR-002: No additional Redis round-trips on status endpoint
**Category:** Performance
**Description:** The status endpoint must not add extra Redis round-trips beyond what it already performs. Progress data should be available from the same ReviewJob object already fetched.
**Acceptance Threshold:** Zero additional Redis calls on the status endpoint path
**Verification:** Code review — verify the endpoint reads progress from the existing ReviewJob fetch

---

## Open Questions

None — the infrastructure (ProgressTracker, ReviewProgress model, ReviewProgressResponse schema) already exists. This is a wiring fix.

---

## Appendix

### Glossary
- **ProgressTracker**: Class in `src/agent/progress.py` that tracks workflow phase state in Redis key `workflow_state:{review_id}`
- **ReviewJob**: Pydantic model in `src/shared/models.py` persisted in Redis key `review:{review_id}`, includes optional `progress` field
- **WorkflowState**: Dataclass in `src/agent/progress.py` that holds per-phase timing and sub-progress data

### References
- [foundation-api specification](.sdd/foundation-api/specification.md) — FR-003 originally defined the progress requirement
- `src/agent/progress.py` — existing ProgressTracker implementation
- `src/shared/models.py` — ReviewProgress and ReviewJob models
- `src/api/schemas.py` — ReviewProgressResponse API schema

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-10 | Claude | Initial specification |
