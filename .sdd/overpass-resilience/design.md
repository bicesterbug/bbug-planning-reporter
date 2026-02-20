# Design: Overpass Resilience

**Version:** 1.0
**Date:** 2026-02-20
**Status:** Implemented
**Linked Specification:** `.sdd/overpass-resilience/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The cycle route MCP server (`src/mcp_servers/cycle_route/server.py`) queries the Overpass API to fetch OSM infrastructure data along each route. The call path is:

1. `_assess_cycle_route()` calls `_assess_single_route()` twice (shortest + safest)
2. `_assess_single_route()` builds a query via `build_overpass_query()`, then makes a direct `self.http.post(OVERPASS_API_URL, data={"data": query})` call
3. `raise_for_status()` throws on any non-2xx response
4. If an exception propagates, the top-level `call_tool()` handler catches it and returns a generic error — the entire destination assessment is lost

The Overpass endpoint (`OVERPASS_API_URL`) is a module-level constant in `infrastructure.py`. There is no retry, fallback, or transient error handling.

### Proposed Architecture

Insert a resilient query function between `_assess_single_route()` and the raw HTTP POST. This function encapsulates:

1. **Primary endpoint with retries** — up to 3 attempts (1 initial + 2 retries) against `overpass-api.de` with exponential backoff (2s, 4s)
2. **Fallback endpoint** — 1 attempt against `overpass.kumi.systems` if all primary attempts fail
3. **Logging** — WARNING-level entry for each retry and fallback attempt

```
_assess_single_route()
  └─ query_overpass_resilient(client, query, destination=...)
       ├─ POST overpass-api.de  (attempt 1)
       ├─ sleep 2s → POST overpass-api.de  (retry 1)
       ├─ sleep 4s → POST overpass-api.de  (retry 2)
       └─ POST overpass.kumi.systems  (fallback)
       → returns dict | None
```

On success at any stage, the parsed JSON is returned. If all 4 attempts fail, `None` is returned and `_assess_single_route()` returns `None` — which the existing `_assess_cycle_route()` already handles via `_empty_assessment()`.

### Technology Decisions

- **Exponential backoff** (2s, 4s) — standard pattern that avoids hammering a struggling server. Base delay 2s keeps total worst-case wait under 10s for primary retries.
- **Single fallback attempt** — the kumi.systems mirror is a volunteer-run service; multiple retries against it would be anti-social.
- **Return None on exhaustion** instead of raising — matches the existing graceful degradation pattern in `_assess_single_route()` (returns `None` when no segments found).
- **Transient status set** — `{429, 500, 502, 504}` covers the observed production errors (504) plus standard transient HTTP errors. `400` and other 4xx are not retried as they indicate query issues.

---

## Added Components

### `query_overpass_resilient` function

**Description:** Async function that queries the Overpass API with retry and fallback. Replaces the direct HTTP POST in `_assess_single_route()`.

**Users:** `CycleRouteMCP._assess_single_route()`

**Kind:** Function

**Location:** `src/mcp_servers/cycle_route/infrastructure.py`

**Details:**

```
OVERPASS_FALLBACK_URL = "https://overpass.kumi.systems/api/interpreter"
OVERPASS_MAX_RETRIES = 2
OVERPASS_BACKOFF_BASE = 2.0
OVERPASS_TRANSIENT_STATUSES = {429, 500, 502, 504}

async query_overpass_resilient(
    client: httpx.AsyncClient,
    query: str,
    *,
    destination: str = "",
) -> dict[str, Any] | None

Behaviour:
  For each endpoint in [OVERPASS_API_URL, OVERPASS_FALLBACK_URL]:
    - OVERPASS_API_URL: up to (1 + OVERPASS_MAX_RETRIES) attempts
    - OVERPASS_FALLBACK_URL: 1 attempt
    On each attempt:
      - POST to endpoint with data={"data": query}
      - On 2xx: return response.json()
      - On transient status (429/500/502/504): log WARNING, sleep backoff, retry
      - On httpx transport error (ConnectTimeout, ReadTimeout, etc.): log WARNING, sleep backoff, retry
      - On non-transient HTTP error (e.g. 400): log WARNING, return None immediately
    Between primary retries: sleep OVERPASS_BACKOFF_BASE ** attempt_number seconds
  If all attempts exhausted: return None
```

**Requirements References:**
- [overpass-resilience:FR-001]: Retry with exponential backoff on transient errors
- [overpass-resilience:FR-002]: Fallback to secondary endpoint after primary exhausted
- [overpass-resilience:FR-003]: Encapsulated resilient function replacing direct POST
- [overpass-resilience:FR-004]: WARNING-level logging for each retry/fallback attempt

**Test Scenarios**

**TS-01: First attempt succeeds**
- Given: Overpass primary returns 200 on first attempt
- When: `query_overpass_resilient` is called
- Then: Returns parsed JSON dict. No WARNING logs emitted.

**TS-02: Succeeds after one retry**
- Given: Overpass primary returns 504 on first attempt, 200 on second
- When: `query_overpass_resilient` is called
- Then: Returns parsed JSON from second attempt. One WARNING log emitted with status 504 and attempt number.

**TS-03: Succeeds after all primary retries**
- Given: Overpass primary returns 504 three times (all attempts exhausted)
- When: Fallback endpoint returns 200
- Then: Returns parsed JSON from fallback. Three WARNING logs for primary failures plus one for fallback attempt.

**TS-04: All attempts exhausted**
- Given: Overpass primary returns 504 three times, fallback returns 504
- When: `query_overpass_resilient` is called
- Then: Returns None. Four WARNING logs emitted (3 primary + 1 fallback).

**TS-05: Non-transient error not retried**
- Given: Overpass primary returns 400 on first attempt
- When: `query_overpass_resilient` is called
- Then: Returns None immediately. One WARNING log. No retry attempts made.

**TS-06: Connection timeout retried**
- Given: Overpass primary raises httpx.ConnectTimeout on first attempt, returns 200 on second
- When: `query_overpass_resilient` is called
- Then: Returns parsed JSON from second attempt. One WARNING log for the timeout.

**TS-07: Rate limit (429) retried**
- Given: Overpass primary returns 429 on first attempt, 200 on second
- When: `query_overpass_resilient` is called
- Then: Returns parsed JSON from second attempt. One WARNING log.

**TS-08: Destination context in log entries**
- Given: Overpass primary returns 504, then 200
- When: `query_overpass_resilient` is called with destination="Bicester North"
- Then: WARNING log entry includes destination="Bicester North".

---

## Modified Components

### `CycleRouteMCP._assess_single_route` method

**Change Description:** Currently makes a direct `self.http.post(OVERPASS_API_URL, ...)` call followed by `raise_for_status()`. Change to call `query_overpass_resilient()` instead. Handle `None` return by returning `None` from `_assess_single_route` (existing graceful degradation).

**Dependants:** None — `_assess_cycle_route()` already handles `None` from `_assess_single_route()`.

**Kind:** Method

**Details:**

Replace lines 229-235 in server.py:
```
# Before:
overpass_query = build_overpass_query(route_coords)
overpass_response = await self.http.post(OVERPASS_API_URL, data={"data": overpass_query})
overpass_response.raise_for_status()
overpass_data = overpass_response.json()

# After:
overpass_query = build_overpass_query(route_coords)
overpass_data = await query_overpass_resilient(self.http, overpass_query, destination=dest_name)
if overpass_data is None:
    return None
```

Also remove the `OVERPASS_API_URL` import from server.py since it's no longer used directly there.

**Requirements References:**
- [overpass-resilience:FR-003]: Resilient function replaces direct POST

**Test Scenarios**

**TS-09: Overpass total failure degrades gracefully**
- Given: Overpass returns 504 for all attempts (primary + fallback)
- When: `_assess_single_route` is called
- Then: Returns None instead of raising an exception. The `_assess_cycle_route` caller produces an empty assessment stub.

---

## Used Components

### `build_overpass_query`
**Location:** `src/mcp_servers/cycle_route/infrastructure.py`

**Provides:** Builds the Overpass QL query string from route coordinates. Called before `query_overpass_resilient`.

**Used By:** `CycleRouteMCP._assess_single_route` (unchanged call pattern)

### `httpx.AsyncClient`
**Location:** httpx library

**Provides:** Async HTTP client passed into `query_overpass_resilient` for making POST requests.

**Used By:** `query_overpass_resilient`

---

## Documentation Considerations

- None — this is an internal resilience improvement with no API surface changes

---

## Test Data

- Mock HTTP responses via `httpx.MockTransport` (existing pattern)
- A mock transport that tracks call count and returns different responses per attempt to simulate transient failures

---

## Test Feasibility

- All test scenarios are fully testable using existing mock infrastructure
- Backoff delays must be patched (mock `asyncio.sleep`) to avoid slow tests

---

## Risks and Dependencies

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| kumi.systems mirror is unavailable or slow | Medium | Low | It's one extra attempt — if it fails, the same graceful degradation applies. No worse than current behaviour. |
| Backoff delays add latency to review processing | Low | Low | Worst case is ~6s extra per route (2s+4s backoff). Review timeout is 30s per destination, well within budget. |
| kumi.systems changes API path or goes offline permanently | Low | Low | Fallback URL is a constant, easy to update. Function returns None on failure so system continues. |

---

## Feasability Review

- No blockers. All infrastructure exists.

---

## Task Breakdown

### Phase 1: Resilient Overpass Query

**Task 1: Add `query_overpass_resilient` function**
- Status: Done
- Requirements: [overpass-resilience:FR-001], [overpass-resilience:FR-002], [overpass-resilience:FR-003], [overpass-resilience:FR-004]
- Test Scenarios: [overpass-resilience:query_overpass_resilient/TS-01], [overpass-resilience:query_overpass_resilient/TS-02], [overpass-resilience:query_overpass_resilient/TS-03], [overpass-resilience:query_overpass_resilient/TS-04], [overpass-resilience:query_overpass_resilient/TS-05], [overpass-resilience:query_overpass_resilient/TS-06], [overpass-resilience:query_overpass_resilient/TS-07], [overpass-resilience:query_overpass_resilient/TS-08]
- Details:
  - Add constants: `OVERPASS_FALLBACK_URL`, `OVERPASS_MAX_RETRIES`, `OVERPASS_BACKOFF_BASE`, `OVERPASS_TRANSIENT_STATUSES`
  - Add `query_overpass_resilient()` async function to `infrastructure.py`
  - Add test class `TestQueryOverpassResilient` to `test_infrastructure.py` with 8 test methods
  - Patch `asyncio.sleep` in tests to avoid real delays

**Task 2: Wire resilient function into server**
- Status: Done
- Requirements: [overpass-resilience:FR-003]
- Test Scenarios: [overpass-resilience:CycleRouteMCP/TS-09]
- Details:
  - Replace direct Overpass POST in `_assess_single_route()` with `query_overpass_resilient()` call
  - Import `query_overpass_resilient` from infrastructure module
  - Remove unused `OVERPASS_API_URL` import from server.py
  - Add test for total Overpass failure graceful degradation in `test_server.py`
  - Verify all existing server tests still pass (no changes needed — mock transport returns 200 for Overpass)

---

## Intermediate Dead Code Tracking

None — no dead code introduced.

---

## Intermediate Stub Tracking

None — no stubs.

---

## Appendix

### QA Feasibility Analysis

**QA-01 (Retry succeeds after transient failure):** Can only be verified by observing production logs during real Overpass instability. No white-box setup needed — the behaviour is triggered by real external failures. Alternatively, testable by temporarily pointing the primary URL to a non-existent host and verifying fallback kicks in, but this requires manual env configuration.

**QA-02 (Fallback endpoint used):** Same as QA-01 — observable via production logs. The kumi.systems URL will appear in WARNING log entries when the fallback is attempted.

### References
- [Overpass API status](https://overpass-api.de/api/status) — check current server load
- [kumi.systems Overpass](https://overpass.kumi.systems) — community mirror

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-20 | Claude | Initial design |
