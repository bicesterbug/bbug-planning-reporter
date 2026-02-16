# Specification: Review Quality Improvements

**Version:** 1.0
**Date:** 2026-02-16
**Status:** Draft

---

## Problem Statement

Real-world feedback on a live review (25/03310/REM — a B8 data centre at Graven Hill) revealed three systematic weaknesses in the review agent's output: (1) rigid application of parking standards regardless of land-use context, ignoring applicant-provided justification for reduced provision; (2) failure to retrieve and cite local LCWIP-specific requirements (e.g. 3.5m shared path widths) from the policy knowledge base; (3) insufficient assessment of crossing types and quality from transport assessment text, defaulting to generic compliance statements when the text describes specific crossing designs.

## Beneficiaries

**Primary:**
- Cycling advocacy group members writing consultation responses (saves time by producing more accurate, nuanced reviews that don't need manual correction)

**Secondary:**
- Planning officers who receive better-calibrated responses grounded in proportionate analysis

---

## Outcomes

**Must Haves**
- When an applicant provides evidence for reduced cycle parking (industry data, low staffing levels, phased delivery), the review acknowledges the justification and assesses its reasonableness rather than rigidly demanding full standards
- Policy search queries target LCWIP-specific requirements (path widths, crossing standards, network connectivity) so the LLM has local policy evidence to cite
- The review assesses crossing types described in the transport assessment text (parallel, toucan, signal-controlled, uncontrolled) and flags inadequate types for cyclist safety

**Nice-to-haves**
- The review distinguishes between "non-compliance with no justification" and "departure from standards with reasoned justification"
- Policy compliance matrix entries cite the specific LCWIP paragraph when the Bicester LCWIP is relevant

---

## Explicitly Out of Scope

- Plan/drawing image analysis (the agent cannot read layout plans; this is a fundamental limitation)
- Injecting local knowledge (funded schemes, known infrastructure projects) into the review
- Changing the parking standard calculations themselves (the OCC standard is the reference; the change is how departures are assessed)
- Modifying the policy-kb MCP server or re-ingesting policy documents
- Changes to route assessment scoring (the cycle-route MCP server is unchanged)

---

## Functional Requirements

**FR-001: Proportionate parking assessment**
- Description: The structure call prompt must instruct the LLM to consider applicant-provided justification when assessing cycle parking provision. When the transport assessment or travel plan provides evidence for reduced provision (e.g. industry data, low occupancy, phased delivery with monitoring triggers), the review should acknowledge the justification and assess its reasonableness. The review should still note the shortfall against standards but avoid presenting a justified departure as a straightforward non-compliance.
- Acceptance criteria: Given an application where the applicant proposes 20% of parking standards with supporting evidence (e.g. low staffing data centre), the review's cycle parking aspect acknowledges the justification and assesses whether the evidence supports the reduced provision, rather than only stating "non-compliant with standards".
- Failure/edge cases: If no justification is provided, the review should continue to flag non-compliance firmly. If the justification is weak (e.g. "industry advice" with no data), the review should note the weakness.

**FR-002: LCWIP-targeted policy search queries**
- Description: The search query generation prompt must instruct the LLM to include at least one query targeting the Bicester LCWIP for local-specific standards (path widths, crossing types, network connectivity requirements). The existing prompt lists BICESTER_LCWIP as a valid source but doesn't guide queries toward the specific measurable requirements it contains.
- Acceptance criteria: Given any application in the Bicester area, the generated policy queries include at least one query with `"sources": ["BICESTER_LCWIP"]` that targets path width requirements, crossing standards, or network connectivity.
- Failure/edge cases: For applications outside the Bicester LCWIP area (if any), the query generator should still include LCWIP queries since all current applications are in Cherwell district.

**FR-003: Crossing type assessment**
- Description: The structure call prompt must instruct the LLM to assess crossing types described in the transport assessment text. When a TA describes specific crossing designs (parallel crossings, toucan crossings, signal-controlled, uncontrolled, raised tables), the review should evaluate whether these are appropriate for cyclist safety and comfort, considering factors like traffic speed, road width, and cyclist priority. The review should flag inadequate crossing types (e.g. uncontrolled crossings on busy roads, lack of cyclist priority at parallel crossings).
- Acceptance criteria: Given an application where the TA describes parallel crossings within the site, the review's assessment mentions the crossing types by name and evaluates their adequacy for cyclist safety, rather than generic statements about "LTN 1/20 compliant crossings".
- Failure/edge cases: If the TA doesn't describe crossing types, the review should note the absence of crossing design information as a concern.

**FR-004: Evidence-aware compliance assessment**
- Description: The structure call prompt must instruct the LLM to distinguish between "non-compliance with no justification" and "departure from standards with reasoned justification" in the policy compliance matrix. Compliance entries should use notes to explain when a departure is justified vs unjustified, rather than a binary compliant/non-compliant with no nuance.
- Acceptance criteria: The policy compliance matrix for a parking shortfall with evidence-backed justification reads differently (in the notes field) from one with no justification — the former acknowledges the justification while noting the departure, the latter flags the absence of justification.
- Failure/edge cases: The compliant field remains boolean (true/false) — nuance goes in the notes field only.

---

## QA Plan

**QA-01: Rerun the Graven Hill review**
- Goal: Validate that the improved prompts produce a more nuanced review for the same application
- Steps:
  1. Deploy the prompt changes
  2. Submit a review for application 25/03310/REM via the API
  3. Compare the new review output against the original and Paul's feedback
- Expected:
  - Cycle parking aspect acknowledges the applicant's industry evidence for reduced provision
  - Policy compliance matrix cites Bicester LCWIP path width requirements (3.5m)
  - Crossing assessment mentions specific crossing types described in the TA
  - Overall tone is more nuanced — recognises what the application does well while flagging genuine concerns

**QA-02: Check a standard residential application**
- Goal: Ensure the changes don't break reviews for applications without special justification
- Steps:
  1. Submit a review for a standard residential application (e.g. one with straightforward parking non-compliance)
  2. Check that parking non-compliance is still firmly flagged when no justification is provided
- Expected: The review remains firm on unjustified non-compliance; proportionality language only appears when there's evidence to weigh

---

## Open Questions

None.

---

## Appendix

### Glossary
- **LCWIP**: Local Cycling and Walking Infrastructure Plan — a statutory plan identifying cycling/walking network improvements
- **OCC standards**: Oxfordshire County Council parking standards — specifies cycle parking rates per sqm by land use
- **Parallel crossing**: A crossing where pedestrians and cyclists have priority, with a zebra-style crossing adjacent to a cycle crossing
- **Toucan crossing**: A signal-controlled crossing shared by pedestrians and cyclists

### References
- Live review: `rev_01KHHJ4WXM7AY6P18CMF1Y37BY` (application 25/03310/REM)
- [Structure prompt](../../src/agent/prompts/structure_prompt.py)
- [Search query prompt](../../src/agent/prompts/search_query_prompt.py)
- [Report prompt](../../src/agent/prompts/report_prompt.py)

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-16 | Claude | Initial specification based on Paul Troop feedback |
