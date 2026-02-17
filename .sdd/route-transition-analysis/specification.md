# Specification: Route Transition Analysis

**Version:** 1.0
**Date:** 2026-02-17
**Status:** Draft

---

## Problem Statement

The current route assessment scores cycle provision as independent segments without analysing the transitions between them. A route that alternates between segregated cycleway and unprotected road may score well on aggregate provision but be impractical due to barriers (cycle_barrier, bollard, gate), frequent non-priority road crossings, or the cycleway swapping sides of the carriageway. These transition characteristics materially affect route usability and LTN 1/20 compliance but are currently invisible.

## Beneficiaries

**Primary:**
- BBUG reviewers who need to understand whether cycling routes are continuously usable, not just statistically well-provisioned

**Secondary:**
- Planning officers assessing whether S106-funded improvements address real connectivity gaps

---

## Outcomes

**Must Haves**
- Each route assessment includes a transition analysis section identifying barriers, non-priority crossings, and side changes along the route
- Barriers on or adjacent to the route (cycle_barrier, bollard, gate, stile) are detected and reported
- Non-priority road crossings are counted and located (where a cyclist must cross a road without signal control or priority)
- Cycleway side changes (where the off-road provision switches from one side of the carriageway to the other) are detected and counted
- The directness differential between parallel cycle provision and the carriageway is calculated (ratio of cycleway distance to road distance for the same section)
- Transition quality factors are incorporated into the LTN 1/20 scoring
- Transition statistics are included in the assessment output

**Nice-to-haves**
- Detection of dropped kerbs or lack thereof at transition points

---

## Explicitly Out of Scope

- Modifying the OSRM routing request (handled by route-dual-routing spec)
- LLM-generated narrative report (handled by route-narrative-report spec)
- Detection of traffic signal phasing or timing
- Assessment of crossing visibility or sight lines
- Changes to the destinations API

---

## Functional Requirements

**FR-001: Detect Barriers Along Route**
- Description: For each route, the system must query Overpass for barrier nodes (`barrier=cycle_barrier`, `barrier=bollard`, `barrier=gate`, `barrier=stile`, `barrier=lift_gate`) within 15 metres of the route geometry.
- Acceptance criteria: Each detected barrier is returned with its type, OSM node ID, and approximate location along the route (distance from origin in metres). Barriers are included in the assessment output as a `barriers` array.
- Failure/edge cases: If Overpass query fails, barriers array is empty with a `barriers_unavailable: true` flag. Duplicate barriers at the same location (within 5m) are deduplicated.

**FR-002: Count Non-Priority Road Crossings**
- Description: The system must identify points where the route transitions from off-road provision (segregated, shared_use) to a road segment, or where the route crosses a road without signal control. This is detected by analysing consecutive segment transitions: an off-road segment followed by a road segment (or vice versa) where the road has no crossing infrastructure (no `crossing=traffic_signals` or `crossing=marked` on nearby nodes).
- Acceptance criteria: A `non_priority_crossings` count is returned, along with an array of crossing locations (distance from origin, road name, road speed limit).
- Failure/edge cases: If the transition is at a signalised crossing (`crossing=traffic_signals`), it is NOT counted as non-priority. If crossing data is unavailable from Overpass, crossings are estimated from segment transitions only.

**FR-003: Detect Cycleway Side Changes**
- Description: When a route follows off-road provision (segregated or shared_use) that runs parallel to a carriageway, the system must detect when that provision switches from one side of the road to the other. This is identified by a sequence: off-road segment → road crossing → off-road segment, where the two off-road segments are on opposite sides of the same carriageway.
- Acceptance criteria: A `side_changes` count is returned, along with locations (distance from origin, road name at crossing point).
- Failure/edge cases: Side determination requires geometric analysis (comparing cycleway bearing and offset from carriageway centre line). If geometry is insufficient to determine side, the transition is logged as `indeterminate` and not counted as a side change.

**FR-004: Calculate Directness Differential**
- Description: For route sections where parallel cycle provision exists alongside a carriageway, the system must calculate the ratio of the cycle provision distance to the carriageway distance for the same origin-destination section. This shows whether the cycle route is significantly longer than the direct road.
- Acceptance criteria: A `directness_differential` value is returned as a ratio (e.g. 1.15 means the cycle route is 15% longer than the carriageway for that section). This is calculated per parallel section and as a route-wide average.
- Failure/edge cases: If the cycle provision runs the same length as the carriageway (typical for roadside paths), the ratio is 1.0. If no parallel sections exist, the field is null.

**FR-005: Incorporate Transitions into Scoring**
- Description: Transition quality must be factored into the LTN 1/20 score. A new scoring factor "Transition Quality" (10 points) is added, redistributing existing weights. The factor penalises: barriers (high penalty per barrier), non-priority crossings on roads ≥30mph (medium penalty), side changes (low penalty).
- Acceptance criteria: The score breakdown includes a `transition_quality` factor. A route with zero barriers, zero non-priority crossings, and zero side changes scores full points. The total maximum score remains 100 — existing factor weights are reduced proportionally to accommodate the new 10 points.
- Failure/edge cases: If transition analysis fails entirely (Overpass unavailable), the transition_quality factor awards half points (5/10) as a neutral default, and the existing factors retain their current weights.

**FR-006: Return Transition Statistics**
- Description: The route assessment output must include a `transitions` object containing: barrier count and details, non-priority crossing count and details, side change count and details, and directness differential.
- Acceptance criteria: The `transitions` object is present in each route's assessment data (both shortest and safest routes from the dual routing spec).
- Failure/edge cases: If transition analysis is unavailable, the `transitions` object contains counts of 0 and an `unavailable: true` flag.

---

## Non-Functional Requirements

**NFR-001: Overpass Query Efficiency**
- Barrier and crossing detection must be batched into the existing Overpass route query where possible, adding node queries alongside way queries, to avoid additional API round-trips.

---

## QA Plan

**QA-01: Verify barrier detection on route with known barriers**
- Goal: Confirm barriers are detected along a route
- Steps:
  1. Identify a route in Bicester that passes through a known cycle barrier (e.g. bollards on a cycle path)
  2. Submit a review and inspect the route assessment transitions
- Expected: Barriers array contains entries with correct barrier type and approximate location

**QA-02: Verify non-priority crossing count**
- Goal: Confirm that road crossings without signals are counted
- Steps:
  1. Submit a review with a destination requiring the route to cross at least one road
  2. Inspect the transitions object
- Expected: `non_priority_crossings` count reflects the actual number of uncontrolled crossings

**QA-03: Verify transition scoring impact**
- Goal: Confirm that routes with poor transitions score lower
- Steps:
  1. Compare two destination assessments: one with a clean segregated route, one with multiple barriers and crossings
  2. Inspect the score breakdown
- Expected: The route with barriers/crossings has a lower `transition_quality` score factor

---

## Open Questions

None — all questions resolved during discovery.

---

## Appendix

### Glossary
- **Barrier**: A physical obstruction on a cycleway (bollard, gate, cycle_barrier, stile) that forces dismounting or slowing
- **Non-priority crossing**: A point where a cyclist must cross a road without having right of way (no traffic signals, no zebra crossing, no cycle priority markings)
- **Side change**: Where off-road cycle provision switches from one side of a carriageway to the other, requiring a road crossing
- **Directness differential**: The ratio of cycle route distance to carriageway distance for the same origin-destination section

### References
- LTN 1/20 Cycle Infrastructure Design (DfT, 2020) — Chapter 7 on transitions and crossings
- OSM wiki: `barrier=*` tag values
- OSM wiki: `crossing=*` tag values
- Depends on: `.sdd/route-dual-routing/specification.md` (dual route output structure)

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-17 | Claude | Initial specification |
