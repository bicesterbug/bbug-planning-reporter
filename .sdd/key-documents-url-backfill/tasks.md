# Tasks: Key Documents URL Backfill

**Linked Design:** `.sdd/key-documents-url-backfill/design.md`
**Linked Specification:** `.sdd/key-documents-url-backfill/research.md`

> **CRITICAL: Tests are written WITH implementation, not after.**
> Each task that adds or modifies functionality MUST include writing tests as part of that task.
> Do NOT create separate "Add tests" tasks or defer testing to later phases.
> TDD approach: Write failing test → Implement → Verify test passes → Refactor.

## Phase 1: Metadata plumbing

### Task 1.1: Persist `document_id` in `IngestionResult.document_metadata`

- **Status:** Backlog
- **Requirements:** key-documents-url-backfill:FR-001
- **Files to read:** `src/agent/orchestrator.py`, `src/mcp_servers/cherwell_scraper/parsers.py`, `tests/test_orchestrator.py`
- **Files to modify/create:** `src/agent/orchestrator.py`, `tests/test_orchestrator.py`

**Subtasks:**
- [ ] Extend the value-shape documented at `IngestionResult.document_metadata` (orchestrator.py:83) to include `document_id`.
- [ ] At the S3-reuse assignment site (~orchestrator.py:854), populate `document_id` in the metadata dict using `doc.get("document_id", "")`.
- [ ] At the fresh-download assignment site (~orchestrator.py:903), populate `document_id` in the metadata dict using `doc.get("document_id", "")`.
- [ ] Leave line ~941 alone — it is a URL mutation, not a population site.

**Tests:**
- [ ] key-documents-url-backfill:TS-01 — a selected document with `document_id="abc123def456"` results in `metadata[local_path]["document_id"] == "abc123def456"`.
- [ ] key-documents-url-backfill:TS-02 — documents passing through both the S3-reuse and fresh-download paths have non-empty `document_id` in their metadata value, and the ~line 941 URL update does not drop it.

**Details:**

This task adds the `document_id` field to the metadata value dict at two distinct population sites — the S3-reuse path (~line 854) and the fresh-download path (~line 903). Use `doc.get("document_id", "")` defensively so missing ids produce an empty string rather than a KeyError; the backfill helper added later will treat empty strings as mismatches. Do not touch line ~941 — it mutates `url` in place on an already-populated metadata value and does not need to set `document_id`. This phase is invisible to callers — no schema or prompt changes yet.

## Phase 2: Schema, prompt, and helper change atomically

### Task 2.1: Update `KeyDocumentItem` — require `document_id`, remove `title` and `url`

- **Status:** Backlog
- **Requirements:** key-documents-url-backfill:FR-003, key-documents-url-backfill:FR-004
- **Files to read:** `src/agent/review_schema.py`, `tests/` test files that reference `KeyDocumentItem`
- **Files to modify/create:** `src/agent/review_schema.py`, tests that construct `KeyDocumentItem(...)` directly

**Subtasks:**
- [ ] Add required `document_id: str` field to `KeyDocumentItem` (review_schema.py:77-98).
- [ ] Remove `title` and `url` fields entirely from `KeyDocumentItem`.
- [ ] Preserve the existing `coerce_category` validator.
- [ ] Add inline traceability comment `# Implements [key-documents-url-backfill:FR-004]` on the `document_id` field.
- [ ] Update every test or production call site that constructs `KeyDocumentItem(title=..., url=...)` to use `document_id=...` instead (grep for `KeyDocumentItem(` to enumerate).

**Tests:**
- [ ] key-documents-url-backfill:TS-05 — constructing `KeyDocumentItem` from a dict missing `document_id` raises a Pydantic validation error.

**Details:**

This is the hard break — every call site that builds `KeyDocumentItem(title=..., url=...)` will start failing until it is updated. Fix all of them in this same task; otherwise later phases will fail to import. The validator `coerce_category` must remain unchanged. `KeyDocument` (the public API shape at `src/api/schemas.py:166-188`) is a separate model and must not be changed — the public `title`/`url` contract is preserved and will be populated by the backfill helper introduced in Task 2.4.

### Task 2.2: Update `ingested_docs_text` builder to include `document_id` and drop URL

- **Status:** Backlog
- **Requirements:** key-documents-url-backfill:FR-002
- **Files to read:** `src/agent/orchestrator.py` (specifically lines 1493-1504), `tests/test_orchestrator.py`
- **Files to modify/create:** `src/agent/orchestrator.py`, `tests/test_orchestrator.py`

**Subtasks:**
- [ ] Replace the existing `(type: X, url: Y)` format at orchestrator.py:1493-1504 with a format that prefixes each line with `[{document_id}]` followed by description and `(type: {doc_type})`.
- [ ] Remove any `url:` substring from the generated text.

**Tests:**
- [ ] key-documents-url-backfill:TS-03 — rendering two documents with distinct `document_id`s produces text containing both ids in `[...]` prefix form and no `url:` substring.

**Details:**

This is a prompt-text-builder change only. The text-building block stays inline in the orchestrator at ~lines 1493-1504 — do not extract it into a separate function unless the existing test pattern already does so. Claude sees `document_id` and description — URL is no longer shown because it must not appear in LLM output either. The test constructs a small `document_metadata` dict, invokes the orchestrator path (or the relevant helper that returns `ingested_docs_text`) and asserts on the returned string.

### Task 2.3: Update structure prompt to request `document_id` (not `title`/`url`)

- **Status:** Backlog
- **Requirements:** key-documents-url-backfill:FR-003
- **Files to read:** `src/agent/prompts/structure_prompt.py` (specifically lines 87-91), existing test for prompt contents
- **Files to modify/create:** `src/agent/prompts/structure_prompt.py`, test file covering prompt contents

**Subtasks:**
- [ ] Rewrite the `key_documents` instruction section (structure_prompt.py:87-91) so Claude is told to echo `document_id` from the ingested documents list.
- [ ] Remove instructions that tell Claude to produce an authoritative `title` or `url`.

**Tests:**
- [ ] key-documents-url-backfill:TS-04 — inspecting the structure prompt string shows it instructs Claude to return `document_id` and does not request `title` or `url`.

**Details:**

Prompt engineering only. The instruction change must align with Task 2.2's new `[{document_id}]` format so Claude has an unambiguous source to echo. This change plus Task 2.1 and Task 2.4 must land together — if any lands alone, the pipeline breaks.

### Task 2.4: Add `_backfill_key_documents` helper and wire into orchestrator

- **Status:** Backlog
- **Requirements:** key-documents-url-backfill:FR-005, key-documents-url-backfill:FR-006, key-documents-url-backfill:FR-007
- **Files to read:** `src/agent/orchestrator.py` (helpers section and lines 1838-1846), `src/api/schemas.py` (for `KeyDocument` shape), `tests/test_orchestrator.py`
- **Files to modify/create:** `src/agent/orchestrator.py`, `tests/test_orchestrator.py`

**Subtasks:**
- [ ] Add private `_backfill_key_documents(items, document_metadata, review_id, application_ref) -> list[KeyDocument]` adjacent to the existing review-assembly helpers.
- [ ] Inside the helper, build a `{document_id: metadata}` index from `document_metadata` values.
- [ ] For each item with a matching `document_id`, emit `KeyDocument` with `title` from `description`, `url` from metadata `url`, and category/summary from the item.
- [ ] For mismatches, emit `logger.warning("key_document id not in metadata", review_id=..., application_ref=..., document_id=item.document_id)` and emit a `KeyDocument` with `title="(unknown document)"`, `url=None`, and the item's category/summary.
- [ ] Add inline `# Implements [key-documents-url-backfill:FR-005]` and `# Implements [key-documents-url-backfill:FR-006]` traceability comments on the helper.
- [ ] Replace the passthrough dict-comp at orchestrator.py:1838-1846 with a call to the new helper, threading `review_id` and `application_ref` from orchestrator state.

**Tests:**
- [ ] key-documents-url-backfill:TS-06 — matching `document_id` produces a `KeyDocument` with `title` from metadata description and `url` from metadata url.
- [ ] key-documents-url-backfill:TS-07 — unmatched `document_id` yields a `KeyDocument` with `url=None`, `title="(unknown document)"`, a warning is logged, and the entry is NOT dropped.
- [ ] key-documents-url-backfill:TS-08 — three items with one mismatch produce three entries; two have populated URLs; the mismatched entry has `url=None`.

**Details:**

The helper is the single place where LLM `document_id` meets metadata truth. Duplicate ids in metadata: later entry wins in the index (not expected in practice but deterministic). The logger call uses `review_id`, `application_ref`, and `document_id` keyword fields only — do not add a `component=` kwarg; that is not the orchestrator's logging convention here. Confirm the wiring point: the dict-comp at lines 1838-1846 currently copies `KeyDocumentItem` straight to `KeyDocument`; after this task, the helper produces the list of `KeyDocument` objects directly.

## Phase 3: End-to-end coverage

### Task 3.1: Integration tests for full review pipeline

- **Status:** Backlog
- **Requirements:** key-documents-url-backfill:FR-005, key-documents-url-backfill:FR-006, key-documents-url-backfill:FR-007
- **Files to read:** existing integration test files under `tests/` that exercise the review pipeline (locate the mocking infrastructure used by similar tests for structure-call stubbing)
- **Files to modify/create:** integration test file(s) under `tests/`

**Subtasks:**
- [ ] Add ITS-01: fixture review with two ingested documents whose `document_id`s are in `document_metadata`; run the full review pipeline; call `GET /api/v1/reviews/{id}`; assert every `KeyDocument` in the response has a non-null `url` matching the metadata entry's URL.
- [ ] Add ITS-02: stub the structure call response to return `document_id="notreal"` for one of three key documents; run the pipeline; assert the response has three entries, two with populated URLs, one with `url=None`, and a warning is recorded.

**Tests:**
- [ ] key-documents-url-backfill:ITS-01 — end-to-end URL population via real structure call.
- [ ] key-documents-url-backfill:ITS-02 — hallucinated id path yields `url=None` plus warning but does not drop the entry.

**Details:**

ITS-02 requires stubbing the structure call response; locate and reuse the mocking infrastructure that similar existing pipeline tests depend on (the implementation agent will identify these when the task runs — search for existing tests that mock the Claude structure call). Both tests exercise the orchestrator + API layer together. Log capture for the warning assertion should use whatever pattern the existing test suite uses (likely `caplog` or a structured-log capture fixture).

## Intermediate Dead Code Tracking
> Code introduced in earlier phases that will be used in later phases must be tracked here.
> All entries must be resolved (code used or removed) by the final phase.

- None

## Intermediate Stub Tracking
> All entries must be resolved by the final phase.

- None
