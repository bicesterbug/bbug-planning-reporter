# Specification: Overpass Resilience

**Version:** 1.0
**Date:** 2026-02-20
**Status:** Implemented

---

## Problem Statement

The Overpass API (`overpass-api.de`) frequently returns 504 Gateway Timeout errors under load, causing route assessments to fail silently for affected destinations. In production testing, 2 of 3 destinations failed due to Overpass 504s while Valhalla routing succeeded. With dual routing, there are now 2 Overpass calls per destination (shortest + safest routes), amplifying the impact of transient failures.

## Beneficiaries

**Primary:**
- Review quality — route assessments complete for all destinations instead of silently dropping data

**Secondary:**
- Operators — reduced need to resubmit reviews when Overpass is temporarily overloaded

---

## Outcomes

**Must Haves**
- Transient Overpass failures are retried automatically before giving up
- When the primary Overpass endpoint is down, a secondary mirror is tried
- Retry/fallback behaviour is logged at WARNING level for observability

**Nice-to-haves**
- Retry parameters (count, backoff) configurable via environment variables

---

## Explicitly Out of Scope

- Self-hosting an Overpass instance (separate, larger effort)
- Caching Overpass responses across reviews
- Switching away from Overpass to a different infrastructure data source (e.g. BRouter)
- Retry logic for Valhalla or ArcGIS calls (different failure profiles)

---

## Functional Requirements

**FR-001: Retry with Exponential Backoff**
- Description: When an Overpass API request fails with a transient error (HTTP 504, 502, 500, 429, or connection/timeout error), the system retries the same request up to 2 times with exponential backoff (2s, then 4s delay).
- Acceptance criteria: A request that fails twice then succeeds on the third attempt returns the successful response. Total of 3 attempts maximum (1 initial + 2 retries). Backoff delays are approximately 2s and 4s.
- Failure/edge cases: If all 3 attempts on the primary endpoint fail, the request proceeds to fallback (FR-002). Non-transient errors (4xx other than 429) are not retried.

**FR-002: Fallback Overpass Endpoint**
- Description: After exhausting retries on the primary endpoint, the system makes one attempt against a secondary Overpass mirror (`overpass.kumi.systems`). The fallback uses the same query and timeout parameters.
- Acceptance criteria: When all primary retries fail, the system sends the same query to `https://overpass.kumi.systems/api/interpreter`. If the fallback succeeds, its response is used as the Overpass result. If the fallback also fails, the route assessment degrades gracefully (existing behaviour — returns None from `_assess_single_route`).
- Failure/edge cases: The fallback endpoint may also be unavailable. In this case, the existing graceful degradation applies (empty assessment stub). The fallback must not be tried if the primary succeeded.

**FR-003: Resilient Request Function**
- Description: The retry and fallback logic is encapsulated in a single function that replaces the direct `self.http.post()` call to Overpass in `_assess_single_route()`. This function accepts the query data and returns the parsed JSON response or raises after all attempts are exhausted.
- Acceptance criteria: The `_assess_single_route()` method calls the resilient function instead of making a direct HTTP POST. The function signature accepts the query payload and returns the Overpass JSON dict. The existing `OVERPASS_API_URL` constant becomes the primary endpoint; a new constant holds the fallback URL.
- Failure/edge cases: The function must propagate non-transient HTTP errors (e.g. 400 Bad Request from malformed query) immediately without retry.

**FR-004: Observability**
- Description: Each retry attempt and fallback attempt is logged at WARNING level with the attempt number, endpoint URL, and error details (status code or exception type).
- Acceptance criteria: A request that retries twice then falls back produces 3 WARNING log entries (retry 1, retry 2, fallback attempt). A request that succeeds on first attempt produces no warning logs. Log entries include `destination` context when available.
- Failure/edge cases: Logging must not raise exceptions or affect the retry flow.

---

## QA Plan

**QA-01: Verify retry succeeds after transient failure**
- Goal: Confirm the system retries on 504 and eventually returns data
- Steps:
  1. Submit a review for an application with route destinations
  2. Monitor worker logs for retry WARNING entries
  3. Check the completed review for route_assessments data
- Expected: If Overpass is intermittently failing, retry log entries appear and route data is still populated for destinations where a retry succeeded

**QA-02: Verify fallback endpoint is used**
- Goal: Confirm the fallback mirror is contacted when the primary is exhausted
- Steps:
  1. Submit a review during a period of sustained Overpass 504 errors
  2. Search worker logs for "fallback" or the kumi.systems URL
- Expected: Log entries show the fallback endpoint being tried after primary retries are exhausted

---

## Open Questions

None — all questions resolved using production failure data from the v0.3.9 deployment test.

---

## Appendix

### Glossary
- **Overpass API:** Public API for querying OpenStreetMap data. Used to fetch road infrastructure tags along a cycle route.
- **Transient error:** An HTTP error that may resolve on retry — 502, 500, 504 (gateway/server issues) and 429 (rate limiting).

### References
- [Overpass API documentation](https://wiki.openstreetmap.org/wiki/Overpass_API)
- [overpass.kumi.systems](https://overpass.kumi.systems) — community-hosted Overpass mirror
- Current Overpass usage: `src/mcp_servers/cycle_route/infrastructure.py:44` and `src/mcp_servers/cycle_route/server.py:230-235`

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-20 | Claude | Initial specification |
