# Specification: Reliable Structure Extraction

**Version:** 1.0
**Date:** 2026-02-14
**Status:** Draft

---

## Problem Statement

The two-phase review generation pipeline ([structured-review-output]) asks Claude to return a raw JSON text string for the structure call. In production, this call frequently fails Pydantic validation — due to invalid JSON syntax, markdown code fences wrapping the response, truncation mid-object, or enum values not matching strict validators (e.g. document categories). When validation fails, the system falls back to a markdown-only review with ALL six structured fields set to null (`aspects`, `policy_compliance`, `recommendations`, `suggested_conditions`, `key_documents`, `summary`). Additionally, the structure call receives the full unabridged route assessment data (170+ road segments, 80+ issues, 80+ S106 suggestions per destination), inflating the input context and increasing the likelihood of confused or truncated output.

## Beneficiaries

**Primary:**
- API consumers (bbug-website dashboard, letter generator) who need reliable structured data for rendering review cards, aspect ratings, and recommendation lists
- End users (cycling advocacy group members) who see incomplete reviews with missing aspects and recommendations on the website

**Secondary:**
- Developers debugging production issues — currently difficult to distinguish "no data available" from "structure call failed"
- The review verification step, which cannot verify structured field consistency when those fields are null

---

## Outcomes

**Must Haves**
- The structure call uses Anthropic's tool_use feature instead of raw JSON text extraction, eliminating JSON syntax errors, markdown fencing, and truncation as failure modes
- Enum fields (ratings, document categories) are constrained in the tool schema so the model receives explicit valid values
- The structure call receives a condensed route evidence summary instead of the full segment-level data, reducing input token count and context confusion
- The report call continues to receive the full route evidence for detailed prose writing
- Assessment aspects are flexible — the model returns the aspects relevant to the application rather than being forced into exactly 5 fixed aspects
- The existing fallback behaviour is preserved: if the structure call fails, the system still produces a markdown-only review (structured fields null)

**Nice-to-haves**
- Log the tool_use `stop_reason` to detect output truncation even within tool_use
- Log the input token count reduction from route evidence summarization for monitoring

---

## Explicitly Out of Scope

- Adding an extraction fallback call to recover structured data from markdown when the structure call fails — tool_use should be reliable enough
- Changing the report call prompt or behaviour (it continues to receive the structure JSON and full evidence)
- Changing the Phase 4 evidence-gathering queries, document download, or ingestion pipelines
- Retroactively fixing existing completed reviews with null structured fields
- Changing the letter generation pipeline (it consumes `review["full_markdown"]` only)
- Changing the API response schema (`ReviewContent`, `ReviewResponse`) — the dict shape is identical
- Adding new assessment aspects — the model chooses from the existing set based on relevance
- Changing the verification step

---

## Functional Requirements

### FR-001: Structure Call via Tool Use
**Description:** The structure call must use Anthropic's `tools` parameter with `tool_choice` forcing a specific tool, instead of requesting raw JSON text in the response. The tool's `input_schema` must be generated from the `ReviewStructure` Pydantic model's `model_json_schema()` method. The response is parsed by extracting the `tool_use` content block and validating with `ReviewStructure.model_validate()`.

**Examples:**
- Positive case: Claude returns a `tool_use` content block with a valid dict matching the ReviewStructure schema. The dict is validated by Pydantic and all six structured fields are populated in the review result.
- Positive case: Claude returns a rating as `"Amber"` (title case). The `mode="before"` validator normalises it to `"amber"` before the `Literal` type check, and validation succeeds.
- Edge case: Claude returns no `tool_use` block in the response (unexpected). The system catches this as a `ValueError`, logs a warning, and falls through to the existing fallback path.
- Negative case: Claude returns a `tool_use` block but with an invalid field value (e.g. `overall_rating: "yellow"`). Pydantic `ValidationError` is raised, caught, logged, and the fallback path is taken.

### FR-002: Enum Constraints in Tool Schema
**Description:** The `ReviewStructure` Pydantic model must use `Literal` types for all enum fields so that `model_json_schema()` produces JSON Schema `enum` arrays. This gives Claude explicit valid values in the tool definition. Existing `@field_validator` methods for rating fields must use `mode="before"` to normalise casing before the `Literal` type check. The `validate_category` validator on `KeyDocumentItem` must be removed since `Literal` handles it and no casing normalisation is needed.

Fields to constrain:
- `ReviewAspectItem.rating`: `Literal["red", "amber", "green"]`
- `ReviewStructure.overall_rating`: `Literal["red", "amber", "green"]`
- `KeyDocumentItem.category`: `Literal["Transport & Access", "Design & Layout", "Application Core"]`

**Examples:**
- Positive case: `ReviewStructure.model_json_schema()` output contains `"enum": ["red", "amber", "green"]` for `overall_rating` and `"enum": ["Transport & Access", "Design & Layout", "Application Core"]` for `category`.
- Positive case: `ReviewStructure.model_validate({"overall_rating": "RED", ...})` succeeds because the `mode="before"` validator lowercases before Literal check.
- Negative case: `ReviewStructure.model_validate({"overall_rating": "yellow", ...})` raises `ValidationError`.

### FR-003: Flexible Assessment Aspects
**Description:** The structure call must no longer require exactly 5 fixed aspects in a specific order. Instead, the system prompt must provide the list of possible aspects (Cycle Parking, Cycle Routes, Junctions, Permeability, Policy Compliance) as guidance, and instruct Claude to include the aspects that are relevant to the application under review. The `ReviewStructure.aspects` field must accept a list of 1 or more `ReviewAspectItem` objects. The `ReviewAspectItem.name` field must remain a free string (not constrained by Literal) so Claude can use appropriate names.

**Examples:**
- Positive case: A screening opinion application with no site layout details returns 3 aspects: "Cycle Routes", "Policy Compliance", "Sustainable Transport". All three are accepted.
- Positive case: A full reserved matters application returns 6 aspects covering all five standard areas plus "Construction Phase Impacts". All six are accepted.
- Edge case: Claude returns only 1 aspect. The list is valid (minimum 1 item).
- Negative case: Claude returns an empty aspects array. Validation fails (minimum 1 required).

### FR-004: Route Evidence Summarization for Structure Call
**Description:** The orchestrator must build a condensed route evidence summary for the structure call, separate from the full route evidence used by the report call. The summary must include per destination: distance, LTN 1/20 score and rating, provision breakdown as percentages, issue counts by severity, and the top 5 highest-severity issues with their descriptions. The summary must exclude: full segment lists, route geometry coordinates, S106 suggestion details, and low-severity issue details beyond the top 5.

**Examples:**
- Positive case: A route assessment with 170 segments and 80 issues is summarised to approximately 15-25 lines of text per destination (vs. 200+ lines for full evidence). The structure call receives the summary; the report call receives the full detail.
- Edge case: No route assessments were performed (`self._route_assessments` is empty or None). The summary returns "No cycling route assessments were performed." (same as current behaviour).
- Edge case: A route has zero issues. The summary shows "Issues: 0 high, 0 medium, 0 low" with no issue details.

### FR-005: Structure Prompt Update for Tool Use
**Description:** The structure call system prompt must be updated to remove the instruction "You MUST respond with a single JSON object and nothing else" and the inline JSON schema (both are now handled by the tool definition). The prompt must retain all field guidance text (aspect descriptions, rating meanings, policy compliance guidance, key document categorisation). The prompt must reference the tool by name and instruct Claude to use it. The prompt must describe flexible aspect selection rather than mandating exactly 5 aspects in a fixed order.

**Examples:**
- Positive case: The system prompt no longer contains a JSON schema block or "respond with JSON only" instruction.
- Positive case: The system prompt contains "Use the submit_review_structure tool" or equivalent instruction.
- Positive case: The system prompt says aspects should be selected based on relevance to the application.

### FR-006: Fallback Behaviour Preservation
**Description:** The existing fallback path must be preserved unchanged. When the structure call fails (Pydantic `ValidationError`, `ValueError` from missing tool_use block, or Anthropic `APIError`), the system must log a warning, generate a markdown-only review via the fallback call, and set all structured fields to null. The `json.JSONDecodeError` catch can be removed since tool_use never produces invalid JSON syntax.

**Examples:**
- Positive case: Structure call raises `ValidationError` (e.g. empty aspects array). System logs warning with error details, runs fallback markdown call, returns review with `aspects: null`, `policy_compliance: null`, etc.
- Positive case: Anthropic API returns 529 (overloaded). `APIError` is caught, fallback runs.
- Negative case: Both the structure call AND the fallback markdown call fail. Review fails with `OrchestratorError` (same as current behaviour).

---

## Non-Functional Requirements

### NFR-001: Structure Call Reliability
**Category:** Reliability
**Description:** The structure call must succeed (return valid structured data) for at least 95% of reviews, compared to the current estimated success rate which is significantly lower due to JSON parsing failures.
**Acceptance Threshold:** Structure call success rate >= 95% measured over 20 consecutive reviews
**Verification:** Observability — the existing log entry "Review generated" includes `two_phase=True/False` which indicates structure call success. Monitor this field across production reviews.

### NFR-002: Token Budget
**Category:** Performance
**Description:** The route evidence summarization must reduce the structure call input token count compared to using full route evidence. The combined token usage of both calls (structure + report) must not exceed the existing NFR-001 budget from [structured-review-output] (16,000 output tokens total).
**Acceptance Threshold:** Structure call input tokens reduced by >= 50% for reviews with route assessments; combined output tokens <= 16,000
**Verification:** Observability — log token usage for both calls (already implemented in orchestrator).

### NFR-003: Backward Compatibility
**Category:** Compatibility
**Description:** The API response schema (`ReviewContent`, `ReviewResponse`) must not change. The review dict shape must be identical. Existing API consumers (bbug-website, letter generator, webhook consumers) must continue to work without modification. The `aspects` array may now contain fewer or more than 5 items.
**Acceptance Threshold:** No changes to Pydantic API response models; existing test suite passes; letter generator works unchanged
**Verification:** Testing — existing API and letter generation tests pass without modification.

### NFR-004: SDK Compatibility
**Category:** Compatibility
**Description:** The `anthropic` Python SDK minimum version must be bumped to support the `tool_choice` parameter with `{"type": "tool", "name": "..."}` syntax.
**Acceptance Threshold:** `pyproject.toml` specifies `anthropic>=0.25.0` (or higher if needed for tool_choice support)
**Verification:** Testing — structure call works in CI with the specified SDK version.

---

## Open Questions

None.

---

## Appendix

### Glossary
- **Structure call**: The first Claude API call in the two-phase review generation that returns structured assessment data
- **Report call**: The second Claude API call that produces detailed markdown prose from the structure call's JSON outline
- **Tool use**: Anthropic API feature where Claude returns structured data by "calling" a tool with a defined JSON Schema, rather than writing free-text JSON
- **tool_choice**: Anthropic API parameter that forces Claude to call a specific tool, ensuring the response contains a `tool_use` content block
- **Fallback**: The degraded path taken when the structure call fails — produces markdown only with null structured fields
- **Route evidence**: Cycling route assessment data from the cycle-route MCP server, including road segments, provision types, LTN 1/20 scores, and infrastructure issues

### References
- [structured-review-output specification](../structured-review-output/specification.md) — The two-phase generation approach this feature improves
- [cycle-route-assessment specification](../cycle-route-assessment/specification.md) — Route assessment pipeline that produces the evidence being summarized
- [rest-api-contract specification](../rest-api-contract/specification.md) — API response schema that must remain unchanged
- Anthropic tool use documentation — Defines the `tools`, `tool_choice`, and `tool_use` content block API

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-14 | Claude Opus 4.6 | Initial specification |
