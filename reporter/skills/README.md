# Skills library

The "know" layer. Each folder is a custom skill (tight `SKILL.md` + optional
reference files), uploaded to the org via the Skills API and then referenced from
`agent/agent.yaml` by `skill_id`.

## Why skills (not system-prompt bloat)
Skills load **on demand** (progressive disclosure): the short description sits in
context always; the full body is read only when the task calls for it. This is the
main token lever alongside Managed Agents' automatic caching/compaction — policy
tables, scoring rubrics, and the condition-wording library don't sit in every turn.

## Upload (per skill)
```sh
# Beta header skills-2025-10-02 is set automatically by the SDK/CLI.
ant beta:skills create --name advocacy-positions --directory skills/advocacy-positions
# capture skill_id, then reference it in agent/agent.yaml and re-apply the agent.
```
(Or `POST /v1/skills` + `POST /v1/skills/{id}/versions` via the SDK.)

## The 14 skills (vision)
- Acquisition: planning-application-search, planning-application-fetch, cycle-route-query
- Domain: transport-policy-national, transport-policy-regional, transport-policy-cherwell, **advocacy-positions**
- Analysis: **transport-document-triage**, **mitigation-inference**, cycle-provision-assessment, transport-assessment-review
- Output: s106-and-conditions-drafter, advocacy-response

Implemented here first (biggest coherence/quality levers): **advocacy-positions**,
**transport-document-triage**, **mitigation-inference**. The rest follow the same
shape; port reference material from `../../src/agent/prompts/*` and the policy KB.
