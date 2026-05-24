---
name: transport-document-triage
description: >
  Classify each application document and extract structured transport facts, with
  advocate-specific flags: missing documents, inter-document discrepancies, vague
  or aspirational commitments, and construction-phase impacts on existing cyclists.
  Load during the triage stage (checkpoint 2).
---

# Transport document triage

Goal: turn a pile of PDFs into a structured, citeable picture and a list of things
worth questioning. The deterministic work (download, OCR, chunk, embed, search)
happens in the tools — your job is interpretation.

## Workflow
1. Call `fetch_application` → metadata + document manifest + `missing_document_flags`.
2. Call `classify_documents` to select transport-relevant documents (it excludes
   consultation responses / public comments by default).
3. For facts, call `search_application_docs` with targeted queries (trip rates,
   parking schedule, proposed cycleways/crossings, construction management) rather
   than reading whole documents. Cite every extracted fact `[Doc: file p.N]`.
4. Write `01-triage.md`: document table, extracted facts, and the flag lists below.

## Advocate-specific flags (the point of triage)
- **Missing documents.** Trust `missing_document_flags`, then sanity-check against
  scale: e.g. residential > ~50 dwellings with no Travel Plan, or a major
  application with no Transport Assessment. State what *should* have triggered a
  TA/TS/TP and didn't.
- **Discrepancies.** Cross-check figures between documents (e.g. parking schedule in
  the TS vs the site plan; dwelling count in the planning statement vs the TA trip
  rates). Flag each with both citations.
- **Vague / aspirational language.** Quote phrases like "could include", "may
  provide", "to be agreed", "indicative only" verbatim with their location. These
  need hardening into conditions — pass them to the asks stage.
- **Construction phase.** Flag haul routes, temporary closures, or works that affect
  existing cycle users during build-out.
- **Document quality.** If a document failed OCR or appears redacted, say so — it is
  a hard-escalation trigger.

## Confidence
Emit a per-document classification confidence and an overall gap-detection
confidence. False positives on gaps are cheap; false negatives are expensive — lean
toward flagging.
