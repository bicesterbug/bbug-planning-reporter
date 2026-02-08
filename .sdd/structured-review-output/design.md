# Design: Structured Review Output

**Version:** 1.0
**Date:** 2026-02-08
**Status:** Draft
**Linked Specification** `.sdd/structured-review-output/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The review generation pipeline is Phase 5 of the orchestrator workflow (`_phase_generate_review` in `src/agent/orchestrator.py`). It makes a single Claude API call that produces a freeform markdown report. Structured fields (aspects, policy compliance, recommendations, conditions) are then extracted via regex parsing (`ReviewMarkdownParser`) and ad-hoc string matching. Key documents are extracted from a `key_documents_json` code block embedded in the markdown.

This approach has two fundamental problems:
1. **Fragile parsing** — Claude varies its formatting between runs (inline vs. dedicated sections, emoji variations, table layout differences), causing regex parsers to fail silently and return `None`
2. **No consistency guarantee** — The structured JSON fields and the markdown report are derived independently, so they can diverge

### Proposed Architecture

Replace the single Claude call + regex parsing with two sequential Claude API calls:

```
Evidence (Phase 4)
       │
       ▼
┌─────────────────┐
│ Structure Call   │ ─── Claude returns JSON ──→ Structured fields
│ (max 4000 tok)  │                              (aspects, compliance,
└────────┬────────┘                               recommendations, etc.)
         │
         ▼
┌─────────────────┐
│ Report Call      │ ─── Claude returns MD ───→ review["full_markdown"]
│ (max 12000 tok) │
│ Input: JSON +   │
│ evidence        │
└─────────────────┘
```

The structure call produces the authoritative structured data. The report call uses that data as a binding outline to write the prose markdown. Because both outputs derive from the same JSON, they are guaranteed to be consistent.

**Fallback path:** If the structure call fails (invalid JSON, API error), the system falls back to the current single-call approach using the existing system prompt. The `ReviewMarkdownParser` is removed, so fallback structured fields will be `None` — but the markdown report will still be generated.

### Technology Decisions

- **No new dependencies** — Uses the existing `anthropic` SDK for both calls
- **JSON mode not used** — Claude's JSON mode (`response_format`) constrains output but is not available for the Messages API with system prompts of this complexity. Instead, the structure call prompt explicitly requests JSON and the response is validated after receipt.
- **Pydantic validation for structure call** — The JSON response is validated against a Pydantic model to ensure schema conformance before being accepted

### Quality Attributes

- **Maintainability** — Report format changes only require updating the report call prompt, not regex parsers
- **Reliability** — Structured fields come directly from Claude's JSON response, eliminating parsing failures
- **Consistency** — The report call is constrained to use the structure call's data, preventing divergence

---

## API Design

No changes to the public API. The `ReviewContent`, `ReviewResponse`, and related Pydantic models in `src/api/schemas.py` remain unchanged. The internal `review` dict stored in Redis retains the same shape.

---

## Modified Components

### AgentOrchestrator._phase_generate_review

**Change Description:** Currently makes a single Claude API call, then uses `ReviewMarkdownParser` and regex to extract structured fields. Must be replaced with two sequential Claude calls: a structure call returning JSON, then a report call returning markdown. The method also currently handles `key_documents_json` code block extraction — this is removed since key_documents come from the structure call.

**Dependants:** `ReviewResult.review` dict shape (unchanged), `review_jobs._handle_success` (unchanged)

**Kind:** Method

**Requirements References**
- [structured-review-output:FR-001]: Two-phase generation replaces single call
- [structured-review-output:FR-004]: Structured fields sourced from structure call JSON
- [structured-review-output:FR-005]: Remove ReviewMarkdownParser import and usage
- [structured-review-output:FR-007]: Fallback to single-call on structure call failure
- [structured-review-output:NFR-001]: Token budget split across both calls
- [structured-review-output:NFR-002]: Log duration of each sub-call

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Two-phase success | Claude returns valid JSON for structure call, valid markdown for report call | `_phase_generate_review()` completes | All structured fields populated from JSON; `full_markdown` from report call; no ReviewMarkdownParser used |
| TS-02 | Structure call returns invalid JSON | Claude returns non-JSON text for structure call | Structure call parsing fails | Falls back to single markdown call; logs warning; structured fields are None; full_markdown is populated |
| TS-03 | Structure call API error | Claude API raises error on structure call | Structure call fails | Falls back to single markdown call; logs warning; review still completes |
| TS-04 | Token usage tracked | Both calls complete successfully | Review result metadata examined | `input_tokens` and `output_tokens` are the sum of both calls; `model` is set |
| TS-05 | Existing review dict shape preserved | Two-phase completes | Review dict examined | Contains all expected keys: overall_rating, key_documents, aspects, policy_compliance, recommendations, suggested_conditions, full_markdown, summary, model, input_tokens, output_tokens |

---

## Added Components

### StructureCallPrompt

**Description:** Builds the system and user prompts for the structure call (phase 1). The system prompt instructs Claude to respond with a JSON object conforming to the defined schema. The user prompt provides application metadata and evidence chunks. The prompts emphasize that Claude must assess the application and return structured data only — no markdown prose.

**Users:** `AgentOrchestrator._phase_generate_review`

**Kind:** Module (functions)

**Location:** `src/agent/prompts/structure_prompt.py`

**Requirements References**
- [structured-review-output:FR-001]: Defines the structure call prompt
- [structured-review-output:FR-002]: Prompt specifies the JSON schema Claude must return

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Prompt includes schema | Application metadata and evidence provided | `build_structure_prompt()` called | System prompt contains JSON schema definition with all required fields |
| TS-02 | Evidence included | Evidence chunks with both app and policy sources | `build_structure_prompt()` called | User prompt contains formatted evidence from both sources |
| TS-03 | Document metadata included | Ingested document metadata provided | `build_structure_prompt()` called | User prompt contains ingested document list for key_documents selection |

### ReportCallPrompt

**Description:** Builds the system and user prompts for the report call (phase 2). The system prompt instructs Claude to write a detailed prose markdown report following the established format, using the structure call JSON as an authoritative outline. The user prompt provides the JSON data, application metadata, and evidence chunks.

**Users:** `AgentOrchestrator._phase_generate_review`

**Kind:** Module (functions)

**Location:** `src/agent/prompts/report_prompt.py`

**Requirements References**
- [structured-review-output:FR-001]: Defines the report call prompt
- [structured-review-output:FR-003]: Prompt constrains Claude to use JSON data for tables, ratings, compliance verdicts
- [structured-review-output:FR-006]: Prompt specifies the report section structure

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | JSON embedded in prompt | Structured JSON with 5 aspects | `build_report_prompt()` called | User prompt contains the full JSON data for Claude to reference |
| TS-02 | Report format specified | Any input | `build_report_prompt()` called | System prompt specifies all 8 report sections in order |
| TS-03 | Binding language | Any input | `build_report_prompt()` called | System prompt explicitly states Claude MUST use the JSON ratings, compliance verdicts, and recommendations verbatim |

### ReviewStructure (Pydantic model)

**Description:** Pydantic model defining the expected JSON schema from the structure call. Used to validate Claude's response. Contains nested models for aspects, policy compliance items, and key documents.

**Users:** `AgentOrchestrator._phase_generate_review` (for parsing/validation)

**Kind:** Class (Pydantic BaseModel)

**Location:** `src/agent/review_schema.py`

**Requirements References**
- [structured-review-output:FR-002]: Defines the structured JSON schema
- [structured-review-output:NFR-005]: Validation ensures all fields are present and non-null

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Valid JSON parses | JSON string with all required fields | `ReviewStructure.model_validate_json()` called | Model instance created with all fields populated |
| TS-02 | Missing required field rejected | JSON string missing `overall_rating` | `ReviewStructure.model_validate_json()` called | `ValidationError` raised |
| TS-03 | Empty arrays accepted | JSON with `suggested_conditions: []` | `ReviewStructure.model_validate_json()` called | Model valid; `suggested_conditions` is empty list, not None |
| TS-04 | Rating validation | JSON with `overall_rating: "purple"` | `ReviewStructure.model_validate_json()` called | `ValidationError` raised (must be red/amber/green) |
| TS-05 | Compliance boolean coercion | JSON with `compliant: "yes"` instead of `true` | `ReviewStructure.model_validate_json()` called | Handled gracefully — either coerced or rejected with clear error |

---

## Used Components

### anthropic.Anthropic

**Location:** `anthropic` package (external dependency)

**Provides:** Claude Messages API client for making both structure and report calls

**Used By:** AgentOrchestrator._phase_generate_review

### ProgressTracker

**Location:** `src/agent/progress.py`

**Provides:** Phase progress tracking and sub-progress updates for logging and status API

**Used By:** AgentOrchestrator._phase_generate_review (reports progress for both sub-calls)

### ReviewContent / ReviewAspect / PolicyCompliance / KeyDocument

**Location:** `src/api/schemas.py`

**Provides:** Pydantic models defining the API response shape. Not modified — the new `ReviewStructure` model mirrors these shapes for the structure call response.

**Used By:** API routes (unchanged)

---

## Documentation Considerations

- Update `docs/DESIGN.md` architecture section to reflect two-phase review generation
- No API doc changes needed (response schema unchanged)

---

## Instrumentation

| Requirement | Observability Criteria | Implementation | Component |
|-------------|------------------------|----------------|-----------|
| [structured-review-output:NFR-001] | Token usage for each call and combined total | Log `structure_call_tokens`, `report_call_tokens`, and `total_tokens` as structured log fields | AgentOrchestrator._phase_generate_review |
| [structured-review-output:NFR-002] | Duration of each call and total phase time | Log `structure_call_seconds`, `report_call_seconds` as structured log fields; phase duration tracked by ProgressTracker | AgentOrchestrator._phase_generate_review |

---

## Integration Test Scenarios

| ID | Scenario | Given | When | Then | Components Involved |
|----|----------|-------|------|------|---------------------|
| ITS-01 | Structure-to-report consistency | Structure call returns JSON with aspect "Cycle Parking" rated "green" | Report call generates markdown | Markdown Assessment Summary table contains "Cycle Parking" with "GREEN" rating | StructureCallPrompt, ReportCallPrompt, AgentOrchestrator |
| ITS-02 | Fallback produces valid review | Structure call mocked to return invalid JSON | Full `_phase_generate_review()` executes | Review completes with full_markdown populated; structured fields are None; no exception | AgentOrchestrator, fallback prompt |
| ITS-03 | Review dict matches API schema | Two-phase completes successfully | Review dict passed to ReviewContent Pydantic model | Model validates without error; all fields present | ReviewStructure, AgentOrchestrator, ReviewContent |

---

## E2E Test Scenarios

| ID | Scenario | Given | When | Then | User Journey |
|----|----------|-------|------|------|--------------|
| E2E-01 | Complete review with structured output | Application ref submitted, documents downloaded and ingested | Review workflow completes | API returns review with all structured fields non-null AND full_markdown populated with matching content | Submit review → poll status → download JSON → verify fields |

Note: E2E-01 requires a live Claude API call and is marked as manual verification. Unit/integration tests mock the Claude API.

---

## Test Data

- Existing evidence chunks from `tests/test_agent/test_orchestrator.py` fixtures
- Sample structure call JSON responses (valid and invalid variants)
- Sample report call markdown responses
- Reuse application metadata fixtures from existing orchestrator tests

---

## Test Feasibility

- All unit and integration tests can mock the `anthropic.Anthropic` client
- E2E test (E2E-01) requires a live API key and running MCP servers — documented as manual verification
- No missing test infrastructure

---

## Risks and Dependencies

1. **Claude may not return valid JSON in the structure call** — Mitigated by Pydantic validation and fallback to single-call approach. The structure call prompt explicitly requests JSON and includes the schema.
2. **Report call may not faithfully follow the JSON outline** — Mitigated by strong prompt language ("You MUST use the exact ratings from the JSON") and by the fact that the JSON is the source of truth for the API response — the markdown is for human reading.
3. **Increased cost and latency** — Two API calls instead of one. Mitigated by the token budget split (4K structure + 12K report vs. 8K single) and the NFR-002 latency threshold of 8 minutes.
4. **Fallback path has no structured field extraction** — When falling back, the `ReviewMarkdownParser` is gone, so structured fields will be `None`. This is acceptable per FR-007 — the review still completes with full_markdown.

---

## Feasibility Review

No blockers. All dependencies are available. The implementation is a refactor of an existing method with no new external dependencies.

---

## Task Breakdown

### Phase 1: Add new components and two-phase generation

- Task 1: Create ReviewStructure Pydantic model
  - Status: Complete
  - Create `src/agent/review_schema.py` with `ReviewStructure`, `ReviewAspect`, `ComplianceItem`, `KeyDocumentItem` Pydantic models. Include field validators for rating values (red/amber/green), category values, and boolean compliance.
  - Requirements: [structured-review-output:FR-002], [structured-review-output:NFR-005]
  - Test Scenarios: [structured-review-output:ReviewStructure/TS-01], [structured-review-output:ReviewStructure/TS-02], [structured-review-output:ReviewStructure/TS-03], [structured-review-output:ReviewStructure/TS-04], [structured-review-output:ReviewStructure/TS-05]

- Task 2: Create structure call prompt builder
  - Status: Complete
  - Create `src/agent/prompts/structure_prompt.py` with `build_structure_prompt(app_summary, ingested_docs_text, app_evidence_text, policy_evidence_text) -> tuple[str, str]`. The system prompt must define the JSON schema and instruct Claude to return only JSON. The user prompt provides the application context and evidence.
  - Requirements: [structured-review-output:FR-001], [structured-review-output:FR-002]
  - Test Scenarios: [structured-review-output:StructureCallPrompt/TS-01], [structured-review-output:StructureCallPrompt/TS-02], [structured-review-output:StructureCallPrompt/TS-03]

- Task 3: Create report call prompt builder
  - Status: Complete
  - Create `src/agent/prompts/report_prompt.py` with `build_report_prompt(structure_json, app_summary, ingested_docs_text, app_evidence_text, policy_evidence_text) -> tuple[str, str]`. The system prompt defines the report format and instructs Claude to use the JSON data verbatim for tables and lists. The user prompt provides the JSON, application context, and evidence.
  - Requirements: [structured-review-output:FR-001], [structured-review-output:FR-003], [structured-review-output:FR-006]
  - Test Scenarios: [structured-review-output:ReportCallPrompt/TS-01], [structured-review-output:ReportCallPrompt/TS-02], [structured-review-output:ReportCallPrompt/TS-03]

- Task 4: Replace `_phase_generate_review` with two-phase approach
  - Status: Complete
  - Rewrite `_phase_generate_review` to: (1) call Claude with the structure prompt and parse the JSON response via `ReviewStructure`, (2) call Claude with the report prompt to get the markdown, (3) assemble the review dict from the JSON (structured fields) and markdown (full_markdown). Include fallback: if the structure call fails (invalid JSON, API error), fall back to a single markdown call using the current system prompt (minus `key_documents_json` block instructions). Remove all `ReviewMarkdownParser` usage and `key_documents_json` extraction. Track combined token usage.
  - Requirements: [structured-review-output:FR-001], [structured-review-output:FR-004], [structured-review-output:FR-005], [structured-review-output:FR-007], [structured-review-output:NFR-001], [structured-review-output:NFR-002]
  - Test Scenarios: [structured-review-output:AgentOrchestrator/TS-01], [structured-review-output:AgentOrchestrator/TS-02], [structured-review-output:AgentOrchestrator/TS-03], [structured-review-output:AgentOrchestrator/TS-04], [structured-review-output:AgentOrchestrator/TS-05], [structured-review-output:ITS-01], [structured-review-output:ITS-02], [structured-review-output:ITS-03]

### Phase 2: Cleanup and validation

- Task 5: Delete ReviewMarkdownParser and its tests
  - Status: Complete
  - Delete `src/agent/review_parser.py` and `tests/test_agent/test_review_parser.py`. Remove the import from `src/agent/orchestrator.py`. Remove the `review-output-fixes` traceability comments from the orchestrator that reference the parser.
  - Requirements: [structured-review-output:FR-005]
  - Test Scenarios: None (deletion task — verified by test suite passing without these files)

- Task 6: Create `src/agent/prompts/__init__.py`
  - Status: Complete
  - Create the `prompts` package init file. Ensure imports work correctly.
  - Requirements: [structured-review-output:FR-001]
  - Test Scenarios: None (package structure — verified by import in Task 4 tests)

---

## Intermediate Dead Code Tracking

| Phase Introduced | Description | Used In Phase | Status |
|------------------|-------------|---------------|--------|
| Phase 1, Task 4 | Fallback single-call prompt (subset of current system prompt, kept for FR-007) | N/A — permanently used as fallback | Permanent |

---

## Intermediate Stub Tracking

None. No stubs are needed.

---

## Requirements Validation

- [structured-review-output:FR-001]
  - Phase 1 Task 2 (structure prompt)
  - Phase 1 Task 3 (report prompt)
  - Phase 1 Task 4 (two-phase orchestrator)
  - Phase 2 Task 6 (package init)
- [structured-review-output:FR-002]
  - Phase 1 Task 1 (ReviewStructure model)
  - Phase 1 Task 2 (structure prompt includes schema)
- [structured-review-output:FR-003]
  - Phase 1 Task 3 (report prompt constrains Claude to use JSON)
- [structured-review-output:FR-004]
  - Phase 1 Task 4 (structured fields from JSON, not parsed from markdown)
- [structured-review-output:FR-005]
  - Phase 1 Task 4 (remove parser usage from orchestrator)
  - Phase 2 Task 5 (delete parser files)
- [structured-review-output:FR-006]
  - Phase 1 Task 3 (report prompt specifies section structure)
- [structured-review-output:FR-007]
  - Phase 1 Task 4 (fallback to single-call on structure call failure)

- [structured-review-output:NFR-001]
  - Phase 1 Task 4 (token budget split and logging)
- [structured-review-output:NFR-002]
  - Phase 1 Task 4 (duration logging per call)
- [structured-review-output:NFR-003]
  - Phase 1 Task 4 (consistency guaranteed by design — JSON is source of truth)
  - Phase 1 Task 3 (report prompt enforces consistency)
- [structured-review-output:NFR-004]
  - Phase 1 Task 4 (review dict shape unchanged)
- [structured-review-output:NFR-005]
  - Phase 1 Task 1 (Pydantic model validates all fields present)
  - Phase 1 Task 4 (validation before accepting structure call response)

---

## Test Scenario Validation

### Component Scenarios
- [structured-review-output:ReviewStructure/TS-01]: Phase 1 Task 1
- [structured-review-output:ReviewStructure/TS-02]: Phase 1 Task 1
- [structured-review-output:ReviewStructure/TS-03]: Phase 1 Task 1
- [structured-review-output:ReviewStructure/TS-04]: Phase 1 Task 1
- [structured-review-output:ReviewStructure/TS-05]: Phase 1 Task 1
- [structured-review-output:StructureCallPrompt/TS-01]: Phase 1 Task 2
- [structured-review-output:StructureCallPrompt/TS-02]: Phase 1 Task 2
- [structured-review-output:StructureCallPrompt/TS-03]: Phase 1 Task 2
- [structured-review-output:ReportCallPrompt/TS-01]: Phase 1 Task 3
- [structured-review-output:ReportCallPrompt/TS-02]: Phase 1 Task 3
- [structured-review-output:ReportCallPrompt/TS-03]: Phase 1 Task 3
- [structured-review-output:AgentOrchestrator/TS-01]: Phase 1 Task 4
- [structured-review-output:AgentOrchestrator/TS-02]: Phase 1 Task 4
- [structured-review-output:AgentOrchestrator/TS-03]: Phase 1 Task 4
- [structured-review-output:AgentOrchestrator/TS-04]: Phase 1 Task 4
- [structured-review-output:AgentOrchestrator/TS-05]: Phase 1 Task 4

### Integration Scenarios
- [structured-review-output:ITS-01]: Phase 1 Task 4
- [structured-review-output:ITS-02]: Phase 1 Task 4
- [structured-review-output:ITS-03]: Phase 1 Task 4

### E2E Scenarios
- [structured-review-output:E2E-01]: Manual verification (requires live API)

---

## Appendix

### Glossary
- **Structure call**: The first Claude API call that returns a structured JSON assessment
- **Report call**: The second Claude API call that produces a detailed markdown report from the JSON outline
- **Two-phase approach**: The combined structure call + report call strategy
- **Fallback**: Reverting to the current single-call markdown approach when the structure call fails
- **ReviewStructure**: Pydantic model for validating the structure call JSON response

### References
- [structured-review-output specification](specification.md)
- [review-output-fixes specification](../review-output-fixes/specification.md) — Previous regex-based approach
- [key-documents specification](../key-documents/specification.md) — Key documents JSON extraction
- [agent-integration specification](../agent-integration/specification.md) — Review generation orchestrator
- [response-letter specification](../response-letter/specification.md) — Letter generator (downstream consumer)

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-08 | Claude Opus 4.6 | Initial design |
