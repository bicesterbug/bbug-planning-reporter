# Design: Reliable Structure Extraction

**Version:** 1.0
**Date:** 2026-02-14
**Status:** Draft
**Linked Specification** `.sdd/reliable-structure-extraction/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

The review generation pipeline (`_phase_generate_review` in `orchestrator.py`) makes two sequential Claude API calls:

1. **Structure call** — asks Claude to return raw JSON text matching the `ReviewStructure` Pydantic schema. The system prompt contains an inline JSON schema and instructs "You MUST respond with a single JSON object and nothing else." The response text is stripped of markdown code fences, then parsed with `ReviewStructure.model_validate_json()`.

2. **Report call** — takes the validated JSON string as an outline and asks Claude to write detailed markdown prose.

When the structure call fails (invalid JSON, `ValidationError`, or API error), a fallback path runs a single markdown-only call with all structured fields set to null.

Both calls receive the same evidence context from `_build_evidence_context()`, including full unabridged route assessment data (every segment, every issue, every S106 suggestion).

### Proposed Architecture

The structure call switches from raw JSON text extraction to Anthropic's **tool_use** feature:

1. The `ReviewStructure` Pydantic model is updated with `Literal` types so `model_json_schema()` produces a JSON Schema with `enum` constraints.
2. The orchestrator passes `tools=[{"name": "submit_review_structure", "input_schema": <schema>}]` and `tool_choice={"type": "tool", "name": "submit_review_structure"}` to `client.messages.create()`.
3. The response is parsed by extracting the `tool_use` content block's `input` dict and validating with `ReviewStructure.model_validate()`.
4. A new `_build_route_evidence_summary()` method produces a condensed summary for the structure call, while the report call continues to receive the full evidence.

**Sequence (happy path):**
```
Orchestrator
  ├─ _build_evidence_context()        → full evidence (6 strings)
  ├─ _build_route_evidence_summary()  → condensed route summary
  ├─ build_structure_prompt(summary)  → system + user prompt
  ├─ client.messages.create(tools=..., tool_choice=...)
  │     └─ response.content → find tool_use block → .input dict
  ├─ ReviewStructure.model_validate(input_dict)
  ├─ build_report_prompt(full evidence) → system + user prompt
  └─ client.messages.create() → markdown
```

**Sequence (fallback):**
```
Orchestrator
  ├─ Structure call fails (ValidationError / ValueError / APIError)
  ├─ Log warning
  └─ Fallback markdown call → all structured fields = null
```

### Technology Decisions

- **Anthropic tool_use** — eliminates JSON syntax errors, markdown fencing, and truncation as failure modes. The SDK handles serialisation/deserialisation.
- **`model_json_schema()`** — generates the tool `input_schema` directly from the Pydantic model, ensuring the schema and validation stay in sync.
- **`tool_choice` forced** — guarantees Claude returns a `tool_use` block rather than text.
- **`Literal` types** — produce `enum` arrays in JSON Schema, giving Claude explicit valid values.

### Quality Attributes

- **Reliability** — tool_use eliminates the most common failure modes (JSON syntax, code fences, truncation).
- **Maintainability** — the tool schema is generated from the Pydantic model, so schema changes propagate automatically.
- **Backward compatibility** — the review dict shape is identical; only internal API call mechanics change.

---

## API Design

N/A — no public API changes. The `ReviewContent` and `ReviewResponse` models are unchanged. The review dict shape is identical.

---

## Modified Components

### ReviewStructure Pydantic Model
**Change Description** Currently uses `str` types for rating and category fields with `@field_validator` methods that check values manually. Change to `Literal` types so `model_json_schema()` produces `enum` constraints. Rating validators change to `mode="before"` to normalise casing before the `Literal` type check. The `validate_category` validator on `KeyDocumentItem` is removed (no casing normalisation needed, `Literal` handles validation). The `aspects` field adds `min_length=1` to enforce at least one aspect.

**Dependants** `_phase_generate_review()` in orchestrator.py (uses `model_validate` instead of `model_validate_json`)

**Kind** Module (`src/agent/review_schema.py`)

**Requirements References**
- [reliable-structure-extraction:FR-002]: Literal types produce enum arrays in tool schema
- [reliable-structure-extraction:FR-003]: aspects field accepts 1+ items instead of fixed 5

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| ReviewStructure/TS-01 | Literal enum in schema | ReviewStructure model | `model_json_schema()` called | Output contains `"enum": ["red", "amber", "green"]` for `overall_rating` and `"enum": ["Transport & Access", "Design & Layout", "Application Core"]` for `category` |
| ReviewStructure/TS-02 | Rating case normalisation | Rating value "RED" | `model_validate({"overall_rating": "RED", ...})` | Validates successfully, `overall_rating` is `"red"` |
| ReviewStructure/TS-03 | Invalid rating rejected | Rating value "yellow" | `model_validate({"overall_rating": "yellow", ...})` | `ValidationError` raised |
| ReviewStructure/TS-04 | Flexible aspect count | 3 valid aspects | `model_validate({..., "aspects": [3 items]})` | Validates successfully |
| ReviewStructure/TS-05 | Empty aspects rejected | Empty aspects array | `model_validate({..., "aspects": []})` | `ValidationError` raised |
| ReviewStructure/TS-06 | Category without validator | Category "Transport & Access" | `model_validate({..., "category": "Transport & Access"})` | Validates successfully via Literal type |

### Structure Prompt (`build_structure_prompt`)
**Change Description** Currently includes an inline JSON schema and instructs "respond with JSON only". Remove the inline schema and JSON-only instruction. Add instruction to use the `submit_review_structure` tool. Change aspect guidance from "Exactly 5 aspects, in this order" to flexible aspect selection. Retain all field guidance text (rating meanings, policy compliance guidance, key document categorisation).

**Dependants** `_phase_generate_review()` in orchestrator.py

**Kind** Function (`src/agent/prompts/structure_prompt.py`)

**Requirements References**
- [reliable-structure-extraction:FR-005]: Remove inline schema, add tool reference
- [reliable-structure-extraction:FR-003]: Flexible aspect selection guidance

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| StructurePrompt/TS-01 | No JSON-only instruction | Default arguments | `build_structure_prompt()` called | System prompt does NOT contain "respond with a single JSON object" |
| StructurePrompt/TS-02 | No inline schema | Default arguments | `build_structure_prompt()` called | System prompt does NOT contain `"overall_rating": "red" \| "amber" \| "green"` schema block |
| StructurePrompt/TS-03 | Tool reference | Default arguments | `build_structure_prompt()` called | System prompt contains "submit_review_structure" |
| StructurePrompt/TS-04 | Flexible aspects | Default arguments | `build_structure_prompt()` called | System prompt does NOT contain "Exactly 5 aspects" and DOES contain guidance about selecting relevant aspects |
| StructurePrompt/TS-05 | Field guidance retained | Default arguments | `build_structure_prompt()` called | System prompt contains rating meanings ("red", "amber", "green"), policy compliance guidance, and key document categorisation |
| StructurePrompt/TS-06 | Route evidence in user prompt | Route evidence text provided | `build_structure_prompt(route_evidence_text="...")` called | User prompt contains the provided route evidence text |

### Structure Call in Orchestrator (`_phase_generate_review`)
**Change Description** Currently calls `client.messages.create()` without `tools` parameter and parses `response.content[0].text` as raw JSON. Change to pass `tools` and `tool_choice` parameters. Parse response by finding the `tool_use` content block and extracting its `input` dict. Validate with `ReviewStructure.model_validate()` (dict, not JSON string). Remove markdown code fence stripping logic. Remove `json.JSONDecodeError` from the except clause (tool_use never produces invalid JSON). Add `ValueError` catch for missing `tool_use` block. The structure call receives the condensed route summary from `_build_route_evidence_summary()` instead of the full route evidence. The report call continues to receive full route evidence unchanged.

**Dependants** None (terminal in call chain)

**Kind** Method (`src/agent/orchestrator.py::ReviewOrchestrator._phase_generate_review`)

**Requirements References**
- [reliable-structure-extraction:FR-001]: Tool use API call with tool_choice
- [reliable-structure-extraction:FR-004]: Structure call receives condensed route summary
- [reliable-structure-extraction:FR-006]: Fallback path preserved
- [reliable-structure-extraction:NFR-001]: Reliability improvement
- [reliable-structure-extraction:NFR-002]: Token reduction from route summary

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| Orchestrator/TS-01 | Tool use parameters | Valid evidence context | Structure call made | `client.messages.create()` called with `tools` containing `submit_review_structure` and `tool_choice={"type": "tool", "name": "submit_review_structure"}` |
| Orchestrator/TS-02 | Tool use response parsed | Claude returns valid tool_use block | Response processed | `tool_use` content block's `input` dict extracted and validated with `ReviewStructure.model_validate()` |
| Orchestrator/TS-03 | Structured fields populated | Structure call succeeds | Review result built | All six structured fields (`aspects`, `policy_compliance`, `recommendations`, `suggested_conditions`, `key_documents`, `summary`) are non-null |
| Orchestrator/TS-04 | Missing tool_use block | Claude returns text-only response (no tool_use block) | Response processed | `ValueError` raised, caught, fallback path taken, structured fields are null |
| Orchestrator/TS-05 | ValidationError fallback | Claude returns tool_use with invalid field (e.g. empty aspects) | Response validated | `ValidationError` raised, caught, fallback path taken, structured fields are null |
| Orchestrator/TS-06 | APIError fallback | Anthropic API returns 529 | Structure call fails | `APIError` caught, fallback path taken |
| Orchestrator/TS-07 | Route summary for structure call | Route assessments available | Evidence context built | Structure call receives condensed route summary; report call receives full route evidence |
| Orchestrator/TS-08 | Token tracking preserved | Both calls complete | Review result built | `input_tokens` and `output_tokens` reflect combined totals from both calls |

### Route Evidence Summary Builder
**Change Description** Currently `_build_evidence_context()` builds a single `route_evidence_text` used by both calls. A new method `_build_route_evidence_summary()` builds a condensed version for the structure call. The full evidence from `_build_evidence_context()` continues to be used by the report call.

**Dependants** `_phase_generate_review()` uses the summary for the structure call

**Kind** Method (`src/agent/orchestrator.py::ReviewOrchestrator._build_route_evidence_summary`)

**Requirements References**
- [reliable-structure-extraction:FR-004]: Condensed route evidence for structure call
- [reliable-structure-extraction:NFR-002]: Input token reduction

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| RouteEvidenceSummary/TS-01 | Summary includes key metrics | Route assessment with score, distance, provision | `_build_route_evidence_summary()` called | Summary contains destination name, distance, LTN 1/20 score, rating, and provision breakdown as percentages |
| RouteEvidenceSummary/TS-02 | Top 5 issues only | Route with 20 issues (5 high, 8 medium, 7 low) | `_build_route_evidence_summary()` called | Summary contains issue counts by severity and only the top 5 highest-severity issues with descriptions |
| RouteEvidenceSummary/TS-03 | No S106 details | Route with S106 suggestions | `_build_route_evidence_summary()` called | Summary does NOT contain individual S106 suggestion text |
| RouteEvidenceSummary/TS-04 | No segment lists | Route with 170 segments | `_build_route_evidence_summary()` called | Summary does NOT contain individual segment data |
| RouteEvidenceSummary/TS-05 | No route assessments | `self._route_assessments` is empty | `_build_route_evidence_summary()` called | Returns "No cycling route assessments were performed." |
| RouteEvidenceSummary/TS-06 | Zero issues | Route with no issues | `_build_route_evidence_summary()` called | Summary shows "Issues: 0 high, 0 medium, 0 low" with no issue details |

### SDK Version Constraint
**Change Description** Currently `anthropic>=0.18.0` in `pyproject.toml`. Bump to `anthropic>=0.25.0` to ensure `tool_choice` with `{"type": "tool", "name": "..."}` syntax is supported.

**Dependants** All services using the anthropic SDK (worker)

**Kind** Configuration (`pyproject.toml`)

**Requirements References**
- [reliable-structure-extraction:NFR-004]: SDK version compatibility

**Test Scenarios**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| SDKVersion/TS-01 | Version constraint updated | `pyproject.toml` read | Check anthropic dependency | Constraint is `anthropic>=0.25.0` |

---

## Added Components

### `_build_route_evidence_summary` Method
**Description** Builds a condensed route evidence summary for the structure call. Per destination includes: distance, LTN 1/20 score and rating, provision breakdown as percentages, issue counts by severity, and the top 5 highest-severity issues with descriptions. Excludes full segment lists, geometry coordinates, S106 details, and low-severity issues beyond the top 5.

**Users** `_phase_generate_review()` — passes the summary to `build_structure_prompt()` for the structure call

**Kind** Method

**Location** `src/agent/orchestrator.py` as `ReviewOrchestrator._build_route_evidence_summary()`

**Requirements References**
- [reliable-structure-extraction:FR-004]: Condensed route evidence for structure call
- [reliable-structure-extraction:NFR-002]: Reduce structure call input tokens by >= 50%

**Test Scenarios**

See RouteEvidenceSummary/TS-01 through TS-06 in Modified Components above (the method is added but tested alongside the orchestrator modifications).

---

## Used Components

### `build_report_prompt`
**Location** `src/agent/prompts/report_prompt.py`

**Provides** Builds the system and user prompts for the report call. Takes the structure JSON string and full evidence context.

**Used By** Orchestrator `_phase_generate_review()` — unchanged, continues to receive full route evidence

### `ReviewResult`
**Location** `src/agent/orchestrator.py` (dataclass)

**Provides** Container for the review output dict with all structured fields, markdown, and metadata.

**Used By** Orchestrator `_phase_generate_review()` — the dict shape is unchanged

### `ReviewContent` / `ReviewResponse`
**Location** `src/api/models.py`

**Provides** Pydantic API response models that define the external API shape.

**Used By** API endpoint handlers — unchanged, no modifications needed

---

## Documentation Considerations

- No API documentation changes needed (response schema unchanged)
- No README changes needed
- The existing `docs/DESIGN.md` may note the tool_use approach but this is optional

---

## Instrumentation

| Requirement | Observability Criteria | Implementation | Component |
|-------------|------------------------|----------------|-----------|
| [reliable-structure-extraction:NFR-001] | Structure call success rate >= 95% over 20 reviews | Existing `two_phase=True/False` field in "Review generated" log entry. `True` = structure call succeeded. | Orchestrator `_phase_generate_review()` |
| [reliable-structure-extraction:NFR-002] | Structure call input tokens reduced >= 50% | Existing `structure_call_tokens` log field. Compare before/after deployment for reviews with route assessments. | Orchestrator `_phase_generate_review()` |

---

## Integration Test Scenarios

| ID | Scenario | Given | When | Then | Components Involved |
|----|----------|-------|------|------|---------------------|
| ITS-01 | Tool use end-to-end success | Mock Anthropic client returns valid `tool_use` content block with ReviewStructure-shaped dict | `_phase_generate_review()` runs | Structure validates, report call runs with full evidence, review dict has all structured fields populated | Orchestrator, ReviewStructure, build_structure_prompt, build_report_prompt |
| ITS-02 | Tool use fallback end-to-end | Mock Anthropic client returns `tool_use` block with invalid data (empty aspects) | `_phase_generate_review()` runs | ValidationError caught, fallback markdown call runs, review dict has structured fields as null | Orchestrator, ReviewStructure, build_structure_prompt |
| ITS-03 | Route summary vs full evidence | Route assessments set on orchestrator | `_phase_generate_review()` runs | Structure call prompt contains condensed summary; report call prompt contains full route evidence with all segments and issues | Orchestrator, build_structure_prompt, build_report_prompt, _build_route_evidence_summary |

---

## E2E Test Scenarios

N/A — the feature changes internal API call mechanics only. The existing E2E test coverage (API response shape, review workflow) validates backward compatibility through NFR-003.

---

## Test Data

- Existing `SAMPLE_STRUCTURE_JSON` in test fixtures provides a valid structure call response — update to work as a dict (tool_use input) instead of JSON string
- Mock Anthropic responses must be updated to return `tool_use` content blocks instead of text blocks. Use `SimpleNamespace` to create mock `ToolUseBlock` objects with `type="tool_use"`, `name="submit_review_structure"`, and `input={...}` attributes
- Route assessment test data: use existing fixture with multiple destinations, issues at varying severities, and S106 suggestions

---

## Test Feasibility

- All tests are unit/integration level using mocked Anthropic client — no external dependencies needed
- Existing test infrastructure (pytest, unittest.mock) is sufficient
- No new test infrastructure needed

---

## Risks and Dependencies

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `model_json_schema()` produces `$defs`/`$ref` that the Anthropic API doesn't handle | Low | High | Verified: Anthropic API resolves `$ref` in tool schemas. Tested with Pydantic v2 nested models. |
| SDK version bump breaks other SDK usage | Low | Medium | The bump is from >=0.18.0 to >=0.25.0. The current installed version is likely already >0.25.0 (required for Claude Opus 4.6 support). No breaking changes in this range. |
| Tool_use still truncates on very large structured output | Low | Medium | tool_use with 8000 max_tokens is sufficient for the structure call. The `stop_reason` can be logged (nice-to-have) to detect truncation. |
| Route evidence summary loses information needed for accurate aspect ratings | Low | Medium | The summary retains key metrics (scores, ratings, top issues). The structure call only needs to make rating decisions, not write detailed prose. Detailed evidence goes to the report call. |

**Dependencies:**
- `anthropic` Python SDK >= 0.25.0 (already available, just need version bump in pyproject.toml)

**Assumptions:**
- The Anthropic API correctly processes `tool_choice` with `{"type": "tool", "name": "submit_review_structure"}` to force a tool_use response
- `model_json_schema()` output is a valid JSON Schema accepted by the Anthropic `input_schema` parameter

---

## Feasability Review

No large missing features or infrastructure. All changes are within the existing codebase and use existing SDK capabilities.

---

## Task Breakdown

> **CRITICAL: Tests are written WITH implementation, not after.**

### Phase 1: Schema and Prompt Changes

- Task 1: Update ReviewStructure Pydantic model with Literal types and flexible aspects
  - Status: Backlog
  - Change `overall_rating` and `ReviewAspectItem.rating` from `str` to `Literal["red", "amber", "green"]` with `mode="before"` validators for case normalisation
  - Change `KeyDocumentItem.category` from `str` to `Literal["Transport & Access", "Design & Layout", "Application Core"]` and remove `validate_category` validator
  - Add `min_length=1` to `ReviewStructure.aspects` field
  - Update existing tests in `test_review_schema.py` and add new tests for Literal enum in schema output, flexible aspect count
  - Requirements: [reliable-structure-extraction:FR-002], [reliable-structure-extraction:FR-003]
  - Test Scenarios: [reliable-structure-extraction:ReviewStructure/TS-01], [reliable-structure-extraction:ReviewStructure/TS-02], [reliable-structure-extraction:ReviewStructure/TS-03], [reliable-structure-extraction:ReviewStructure/TS-04], [reliable-structure-extraction:ReviewStructure/TS-05], [reliable-structure-extraction:ReviewStructure/TS-06]

- Task 2: Update structure prompt for tool use and flexible aspects
  - Status: Backlog
  - Remove inline JSON schema and "respond with JSON only" instruction
  - Add "Use the submit_review_structure tool" instruction
  - Change "Exactly 5 aspects, in this order" to flexible aspect guidance
  - Retain all field guidance text (rating meanings, policy compliance, key document categorisation)
  - Update tests in `test_structure_prompt.py`
  - Requirements: [reliable-structure-extraction:FR-005], [reliable-structure-extraction:FR-003]
  - Test Scenarios: [reliable-structure-extraction:StructurePrompt/TS-01], [reliable-structure-extraction:StructurePrompt/TS-02], [reliable-structure-extraction:StructurePrompt/TS-03], [reliable-structure-extraction:StructurePrompt/TS-04], [reliable-structure-extraction:StructurePrompt/TS-05], [reliable-structure-extraction:StructurePrompt/TS-06]

- Task 3: Bump anthropic SDK version
  - Status: Backlog
  - Change `anthropic>=0.18.0` to `anthropic>=0.25.0` in `pyproject.toml`
  - Requirements: [reliable-structure-extraction:NFR-004]
  - Test Scenarios: [reliable-structure-extraction:SDKVersion/TS-01]

### Phase 2: Orchestrator Changes

- Task 4: Add route evidence summary builder
  - Status: Backlog
  - Implement `_build_route_evidence_summary()` method on `ReviewOrchestrator`
  - Per destination: distance, LTN 1/20 score and rating, provision breakdown as percentages, issue counts by severity, top 5 highest-severity issues
  - Exclude: full segment lists, geometry, S106 details, low-severity issues beyond top 5
  - Add tests in `test_orchestrator.py`
  - Requirements: [reliable-structure-extraction:FR-004], [reliable-structure-extraction:NFR-002]
  - Test Scenarios: [reliable-structure-extraction:RouteEvidenceSummary/TS-01], [reliable-structure-extraction:RouteEvidenceSummary/TS-02], [reliable-structure-extraction:RouteEvidenceSummary/TS-03], [reliable-structure-extraction:RouteEvidenceSummary/TS-04], [reliable-structure-extraction:RouteEvidenceSummary/TS-05], [reliable-structure-extraction:RouteEvidenceSummary/TS-06]

- Task 5: Convert structure call to tool_use
  - Status: Backlog
  - Pass `tools` and `tool_choice` to `client.messages.create()`
  - Extract `tool_use` content block's `input` dict from response
  - Validate with `ReviewStructure.model_validate()` (dict, not JSON string)
  - Remove markdown code fence stripping logic
  - Replace `json.JSONDecodeError` with `ValueError` in except clause
  - Pass condensed route summary to structure call, full evidence to report call
  - Store `structure_json_str` as `json.dumps(tool_use_input)` for the report call
  - Update mock helpers and existing orchestrator tests
  - Requirements: [reliable-structure-extraction:FR-001], [reliable-structure-extraction:FR-004], [reliable-structure-extraction:FR-006], [reliable-structure-extraction:NFR-001]
  - Test Scenarios: [reliable-structure-extraction:Orchestrator/TS-01], [reliable-structure-extraction:Orchestrator/TS-02], [reliable-structure-extraction:Orchestrator/TS-03], [reliable-structure-extraction:Orchestrator/TS-04], [reliable-structure-extraction:Orchestrator/TS-05], [reliable-structure-extraction:Orchestrator/TS-06], [reliable-structure-extraction:Orchestrator/TS-07], [reliable-structure-extraction:Orchestrator/TS-08], [reliable-structure-extraction:ITS-01], [reliable-structure-extraction:ITS-02], [reliable-structure-extraction:ITS-03]

---

## Intermediate Dead Code Tracking

| Phase Introduced | Description | Used In Phase | Status |
|------------------|-------------|---------------|--------|
| — | No dead code expected | — | — |

---

## Intermediate Stub Tracking

| Phase Introduced | Test Name | Reason for Stub | Implemented In Phase | Status |
|------------------|-----------|-----------------|----------------------|--------|
| — | No stubs expected | — | — | — |

---

## Requirements Validation

- [reliable-structure-extraction:FR-001]
  - Phase 2 Task 5
- [reliable-structure-extraction:FR-002]
  - Phase 1 Task 1
- [reliable-structure-extraction:FR-003]
  - Phase 1 Task 1
  - Phase 1 Task 2
- [reliable-structure-extraction:FR-004]
  - Phase 2 Task 4
  - Phase 2 Task 5
- [reliable-structure-extraction:FR-005]
  - Phase 1 Task 2
- [reliable-structure-extraction:FR-006]
  - Phase 2 Task 5

- [reliable-structure-extraction:NFR-001]
  - Phase 2 Task 5
- [reliable-structure-extraction:NFR-002]
  - Phase 2 Task 4
  - Phase 2 Task 5
- [reliable-structure-extraction:NFR-003]
  - Phase 1 Task 1 (aspects field accepts variable count)
  - Phase 2 Task 5 (review dict shape unchanged)
- [reliable-structure-extraction:NFR-004]
  - Phase 1 Task 3

---

## Test Scenario Validation

### Component Scenarios
- [reliable-structure-extraction:ReviewStructure/TS-01]: Phase 1 Task 1
- [reliable-structure-extraction:ReviewStructure/TS-02]: Phase 1 Task 1
- [reliable-structure-extraction:ReviewStructure/TS-03]: Phase 1 Task 1
- [reliable-structure-extraction:ReviewStructure/TS-04]: Phase 1 Task 1
- [reliable-structure-extraction:ReviewStructure/TS-05]: Phase 1 Task 1
- [reliable-structure-extraction:ReviewStructure/TS-06]: Phase 1 Task 1
- [reliable-structure-extraction:StructurePrompt/TS-01]: Phase 1 Task 2
- [reliable-structure-extraction:StructurePrompt/TS-02]: Phase 1 Task 2
- [reliable-structure-extraction:StructurePrompt/TS-03]: Phase 1 Task 2
- [reliable-structure-extraction:StructurePrompt/TS-04]: Phase 1 Task 2
- [reliable-structure-extraction:StructurePrompt/TS-05]: Phase 1 Task 2
- [reliable-structure-extraction:StructurePrompt/TS-06]: Phase 1 Task 2
- [reliable-structure-extraction:SDKVersion/TS-01]: Phase 1 Task 3
- [reliable-structure-extraction:RouteEvidenceSummary/TS-01]: Phase 2 Task 4
- [reliable-structure-extraction:RouteEvidenceSummary/TS-02]: Phase 2 Task 4
- [reliable-structure-extraction:RouteEvidenceSummary/TS-03]: Phase 2 Task 4
- [reliable-structure-extraction:RouteEvidenceSummary/TS-04]: Phase 2 Task 4
- [reliable-structure-extraction:RouteEvidenceSummary/TS-05]: Phase 2 Task 4
- [reliable-structure-extraction:RouteEvidenceSummary/TS-06]: Phase 2 Task 4
- [reliable-structure-extraction:Orchestrator/TS-01]: Phase 2 Task 5
- [reliable-structure-extraction:Orchestrator/TS-02]: Phase 2 Task 5
- [reliable-structure-extraction:Orchestrator/TS-03]: Phase 2 Task 5
- [reliable-structure-extraction:Orchestrator/TS-04]: Phase 2 Task 5
- [reliable-structure-extraction:Orchestrator/TS-05]: Phase 2 Task 5
- [reliable-structure-extraction:Orchestrator/TS-06]: Phase 2 Task 5
- [reliable-structure-extraction:Orchestrator/TS-07]: Phase 2 Task 5
- [reliable-structure-extraction:Orchestrator/TS-08]: Phase 2 Task 5

### Integration Scenarios
- [reliable-structure-extraction:ITS-01]: Phase 2 Task 5
- [reliable-structure-extraction:ITS-02]: Phase 2 Task 5
- [reliable-structure-extraction:ITS-03]: Phase 2 Task 5

### E2E Scenarios
N/A — no E2E scenarios needed (internal mechanics change only, backward compatible)

---

## Appendix

### Glossary
- **tool_use** — Anthropic API content block type where Claude "calls" a tool with structured input matching a JSON Schema
- **tool_choice** — Anthropic API parameter that forces Claude to call a specific tool
- **input_schema** — JSON Schema definition passed in the `tools` parameter, generated from `ReviewStructure.model_json_schema()`
- **submit_review_structure** — The tool name used for the structure call

### References
- [reliable-structure-extraction specification](specification.md)
- [structured-review-output design](../structured-review-output/design.md) — Current two-phase implementation being modified
- Anthropic tool use documentation — `tools`, `tool_choice`, `tool_use` content block

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-14 | Claude Opus 4.6 | Initial design |
