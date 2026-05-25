---
name: transport-policy-cherwell
description: >
  How to apply Cherwell transport/active-travel policy in an advocacy assessment —
  which policies bind, which are aspirational, and where the development is the
  delivery mechanism for an LCWIP route. Load when assessing policy compliance or
  drafting asks. Use search_policy / get_policy_section for the verbatim text.
---

# Cherwell transport policy (advocate framing)

The policy *text* lives in the policy KB — call `search_policy` (semantic) or
`get_policy_section` (verbatim) and **always pass the application's validation date
as `effective_date`** so you cite the revision that was in force. This skill is the
judgement layer: what to do with that text.

## Binding vs aspirational (the advocate distinction)
- **Binding** (what the council *must* secure): Cherwell Local Plan 2011–2031
  Part 1 — **SLE 4** (sustainable transport), **ESD 1–5** (climate/design),
  **INF 1** (infrastructure & developer contributions); adopted parking standards
  SPD. The `binding: true` flag on search results marks these.
- **Aspirational / material-weight** (what the council *should* be pushed to
  secure): the **Local Plan Review 2040 (emerging)** — weight grows as it advances,
  note its stage; the **Cherwell LCWIP**; Oxfordshire LTCP and Cycling Design
  Standards; national best practice (LTN 1/20, Gear Change). Advocates push these.

Always state which kind you are relying on. "Must" arguments anchor the asks;
"should" arguments expand them.

## LCWIP delivery dependencies (the biggest lever)
When a site sits **on or adjacent to an LCWIP route**, the application is the
*mechanism* for delivering that route — argue for delivery or a proportionate S106
contribution, not vague "support for active travel". This is also a hard-escalation
trigger (high strategic significance): surface it prominently. Check the LCWIP
scheme the site could contribute to and name it.

## Area action plans
Banbury, Bicester, and Kidlington have site-specific plans — check for one covering
the application site and apply its transport requirements.

## How to use in the assessment
1. Identify the validation date → pass as `effective_date` to every policy lookup.
2. For each transport issue, cite the binding policy first, then the aspirational
   layer that justifies going further.
3. Where a commitment is vague (`POS-VAGUE-01`), pair the policy with a condition
   ask from the s106-and-conditions-drafter.
