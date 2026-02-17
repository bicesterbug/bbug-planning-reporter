# Specification: Dual Route Assessment (Shortest + Safest)

**Version:** 1.0
**Date:** 2026-02-17
**Status:** Draft

---

## Problem Statement

The cycle route assessment currently requests a single route per destination from OSRM and scores it. This gives no indication of whether a safer alternative exists. OSRM's bike profile may route along a carriageway even when a parallel segregated cycleway exists as a separate OSM way object (e.g. Banbury Road in Bicester). Reviewers cannot compare the directness trade-off of the shortest route against one that maximises use of cycle provision.

## Beneficiaries

**Primary:**
- BBUG reviewers who need to assess whether cycling infrastructure to key destinations is adequate and understand the LTN 1/20 compliance of the most direct route vs the safest route

**Secondary:**
- Planning officers receiving reviews, who benefit from a clearer evidence base for S106 requests

---

## Outcomes

**Must Haves**
- For each destination, the assessment produces two routes: shortest (minimum distance) and safest (maximum cycle provision utilisation)
- The safest route is selected from OSRM alternatives based on provision scoring
- Road segments where OSRM does not route through an adjacent parallel cycleway still have their provision upgraded when such infrastructure exists
- Both routes are scored independently using LTN 1/20 scoring
- The comparison between shortest and safest routes makes the directness-vs-safety trade-off explicit

**Nice-to-haves**
- Visual distinction between the two routes in any future map rendering (geometry is already stored)

---

## Explicitly Out of Scope

- Custom graph-based routing that bypasses OSRM (too complex; we rely on OSRM alternatives)
- Transition analysis between provision types (separate spec: route-transition-analysis)
- LLM-generated narrative report (separate spec: route-narrative-report)
- Changes to the destinations API (already fully functional)
- Changes to the `include_route_assessment` toggle mechanism (destination_ids=[] already works)

---

## Functional Requirements

**FR-001: Request OSRM Alternative Routes**
- Description: The `assess_cycle_route` MCP tool must request alternative routes from OSRM by adding `alternatives=true` to the routing request, instead of only using the first route.
- Acceptance criteria: OSRM is called with `alternatives=true` and all returned routes (typically 1-3) are captured for evaluation.
- Failure/edge cases: If OSRM returns only one route, that route serves as both shortest and safest. If OSRM returns an error, the existing error handling applies unchanged.

**FR-002: Select Shortest Route**
- Description: From the set of OSRM alternatives, the system must identify the route with the minimum total cycling distance as the "shortest" route.
- Acceptance criteria: The shortest route is the one with the smallest `distance` value from the OSRM response.
- Failure/edge cases: If multiple routes have identical distances, pick the first one returned by OSRM.

**FR-003: Detect Parallel Cycle Provision**
- Description: For each road-classified segment (highway type in residential, tertiary, secondary, primary, trunk, unclassified) with provision `none`, `advisory_lane`, or `on_road_lane`, the system must query Overpass for `highway=cycleway` or `highway=path` with `bicycle=designated` ways within 30 metres and with a bearing within 30 degrees of the road segment.
- Acceptance criteria: When a parallel cycleway is detected, the segment's provision is upgraded to the detected provision type (e.g. `segregated`) for scoring purposes. The original provision is preserved alongside the upgraded provision so both are visible in the output.
- Failure/edge cases: If the Overpass query fails or times out, the segment retains its original provision (graceful degradation). If multiple parallel ways are found, use the one with the best provision classification.

**FR-004: Score All Alternatives with Parallel Detection**
- Description: Each OSRM alternative route must be scored using the existing LTN 1/20 scoring algorithm, but with provision upgraded where parallel cycle infrastructure is detected per FR-003.
- Acceptance criteria: Each alternative receives a full score breakdown (segregation, speed safety, surface quality, directness, junction safety) and RAG rating, with parallel detection applied.
- Failure/edge cases: If parallel detection fails for some segments, score those segments with original provision only.

**FR-005: Select Safest Route**
- Description: From the scored alternatives, the system must select the route with the highest overall LTN 1/20 score as the "safest" route.
- Acceptance criteria: The safest route is the one with the maximum `score` value after parallel detection upgrades. If multiple routes tie, prefer the shorter one.
- Failure/edge cases: If only one route is returned by OSRM, it is both shortest and safest (deduplicated in output).

**FR-006: Return Dual Route Assessment**
- Description: The `assess_cycle_route` tool must return both the shortest and safest route assessments in its response, each with full scoring, provision breakdown, segments, issues, S106 suggestions, and route geometry.
- Acceptance criteria: The response includes a `shortest_route` object and a `safest_route` object, each containing the same fields as the current single route response. If the same route is both shortest and safest, both fields reference the same data with a `same_route: true` flag.
- Failure/edge cases: Backward compatibility — the existing single-route fields (`distance_m`, `score`, etc.) at the top level are removed; consumers must use `shortest_route` and `safest_route` instead.

**FR-007: Update Orchestrator Route Evidence**
- Description: The orchestrator's `_build_route_evidence_summary()` must include both routes per destination in the evidence text passed to the LLM, clearly labelling which is shortest and which is safest with their respective scores and provision breakdowns.
- Acceptance criteria: The evidence text for each destination shows both routes with their distance, LTN 1/20 score, and provision percentages. Where a segment's provision was upgraded via parallel detection, this is noted.
- Failure/edge cases: If both routes are the same, the evidence text shows a single route with a note that it is both the shortest and safest option.

---

## Non-Functional Requirements

**NFR-001: External API Rate Limiting**
- OSRM and Overpass calls must maintain existing 0.5s inter-request delay. The parallel detection Overpass query should be batched into a single query per route (not per segment) to minimise API calls.

**NFR-002: Backward Compatibility**
- The review must still complete successfully if the cycle-route MCP is unavailable. The orchestrator's graceful degradation behaviour is unchanged.

---

## QA Plan

**QA-01: Verify dual routes for known destination**
- Goal: Confirm two distinct routes are returned with different scores
- Steps:
  1. Ensure Bicester North Station destination exists
  2. Submit a review for a planning application with route assessment enabled
  3. Wait for review to complete
  4. Inspect the review result's `route_assessments` for Bicester North Station
- Expected: Both `shortest_route` and `safest_route` objects present, each with distance, score, provision breakdown. If OSRM returns alternatives, the two routes should differ.

**QA-02: Verify parallel detection on Banbury Road**
- Goal: Confirm that the segregated cycleway alongside Banbury Road is detected
- Steps:
  1. Use a destination that requires routing along Banbury Road (e.g. a site north of town centre going to Bicester North Station)
  2. Submit a review and inspect the route assessment segments
- Expected: Road segments along Banbury Road show `original_provision: none` (or similar) and `provision: segregated` after parallel detection upgrade.

**QA-03: Verify single-route deduplication**
- Goal: Confirm that when OSRM returns only one route, it appears as both shortest and safest
- Steps:
  1. Test with a very short route or a destination with only one viable path
  2. Inspect the route assessment
- Expected: `same_route: true` flag present; both `shortest_route` and `safest_route` contain identical data.

---

## Open Questions

None — all questions resolved during discovery.

---

## Appendix

### Glossary
- **OSRM**: Open Source Routing Machine — external routing engine used for cycling directions
- **Overpass API**: Query language for OpenStreetMap data, used to retrieve way tags and infrastructure
- **Parallel cycleway**: A segregated cycleway or designated path that runs alongside a road as a separate OSM way object
- **Provision upgrade**: Reclassifying a road segment's cycle provision based on detected adjacent infrastructure

### References
- LTN 1/20 Cycle Infrastructure Design (DfT, 2020) — Table 4-1 for speed/separation requirements
- OSRM API documentation: `alternatives=true` parameter
- Overpass API: way queries with proximity and bearing filters
- Existing spec: `.sdd/cycle-route-assessment/specification.md`
- Existing as-built: `.sdd/cycle-route-api/specification.md`

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-17 | Claude | Initial specification |
