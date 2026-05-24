---
name: mitigation-inference
description: >
  The highest-risk step. Given the proposed-infrastructure references from the
  documents and the baseline route assessment, reason about whether the proposed
  measures would credibly change the baseline — there is NO tool to validate this.
  Always output confidence with reasoning. Load at checkpoint 4.
---

# Mitigation inference

The cycle-route tool reports today's network only. Proposed infrastructure must be
inferred from the documents and reasoned about against the baseline. This is
judgement, not measurement — the most error-prone part of the whole system. Treat
every output as draft.

## For each proposed measure
Submit one `submit_mitigation_inference` call with:
- **measure** — a clear summary of what is *actually* proposed.
- **committed_or_aspirational** — distinguish a binding commitment from "could /
  may / indicative". If unclear, say `unclear` (do not round up to committed).
- **affected_segments** — which baseline route segments it would touch.
- **would_change_baseline** — would it change the LTS/rating of those segments or
  close a gap to a key destination? Explain in `reasoning`.
- **confidence** (0–1) — and state the basis for it.
- **credibility_concerns** — see the red-flag checklist below.

## Credibility red flags (each lowers confidence and is a hard-escalation trigger)
- No funding mechanism identified.
- No land control (the works sit on third-party / highway land not in the applicant's
  control).
- Dependent on third-party works or future phases.
- Annotated "indicative only" / "illustrative" on drawings.
- Conflicts with the baseline geometry (e.g. a cycleway shown where there is no width).
- Design parameters fall short of LTN 1/20 with no justification.

## Confidence threshold
This stage's threshold is **0.90** — the highest in the system. Below it, the run
must be reviewed by a human (autonomous mode escalates automatically; cowork mode
pauses at checkpoint 4). Never present low-confidence inference as fact in the draft;
present it as "the applicant appears to propose X; this is not yet credible because…".

## Output
Write `03-mitigation-inference.md`: per-measure summary, the with-mitigation picture
(reasoned, not re-queried), and the specific concerns the response should raise.
