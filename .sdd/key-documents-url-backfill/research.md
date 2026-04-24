# Research: Key Documents URL Backfill

**Date:** 2026-04-24
**Parent feature:** [key-documents](../key-documents/specification.md)

---

## Problem

The `key_documents` array returned from `GET /api/v1/reviews/{review_id}` sometimes contains entries where `url` is `null`, even though an authoritative URL is known for every ingested document. The field is currently populated by the LLM echoing URLs out of a free-text document listing, which is unreliable. The review-output-urls feature has a test asserting `url is not None` for key documents, confirming this is the intended contract.

The user's direction:
1. **Backfill deterministically** — don't try to make the LLM more reliable at echoing URLs; override with known-good metadata.
2. **Matching must be exact** — no fuzzy title/description matching.
3. **`url: null` is acceptable but should be rare** — log a warning when the LLM returns an identifier that doesn't match metadata.
4. **Source both `title` and `url` from metadata** — the LLM's role for each key document becomes: pick a document (by stable id), assign a category, write a summary.

---

## Current Flow

The key documents path runs through four places:

1. **Ingestion** (`src/agent/orchestrator.py:809-941`) — `_selected_documents` (list of dicts from the scraper) carry `document_id`, `url`, `description`, `document_type`. After download, metadata is persisted into `IngestionResult.document_metadata` (field on dataclass, line 83), keyed by local file path:

   ```python
   document_metadata[local_path] = {
       "description": desc,
       "document_type": doc.get("document_type"),
       "url": public_url or doc_url,
   }
   ```

   `document_id` is available in scope at line 813 (`doc_id = doc.get("document_id", "")`) but is **not carried into `document_metadata`**.

2. **Prompt construction** (`src/agent/orchestrator.py:1493-1504`) — `ingested_docs_text` is built for the structure call:

   ```python
   doc_lines.append(f"- {desc} (type: {doc_type}, url: {url})")
   ```

   No stable identifier is shown to Claude. Claude is expected to echo description as `title` and URL verbatim.

3. **Structure prompt** (`src/agent/prompts/structure_prompt.py:87-91`) — tells Claude:
   - `title`: "Document title matching the ingested documents list"
   - `url`: "Download URL from the ingested documents list, or null"

   Matching relies on string similarity rather than an explicit identifier.

4. **Orchestrator passthrough** (`src/agent/orchestrator.py:1838-1846`) — whatever Claude returned flows straight through to `KeyDocument` in the API response:

   ```python
   key_documents = [
       {"title": d.title, "category": d.category, "summary": d.summary, "url": d.url}
       for d in structure.key_documents
   ]
   ```

   No lookup, no validation.

### Why URLs go missing

Claude is being asked to copy a URL string out of free text. When it doesn't (forgetfulness, paraphrasing, decides it "fits better" as null), the response has `url: null` and nothing catches it. The URL is deterministically known server-side, so leaning on the model for this is the wrong layer.

---

## Stable Identifier Available

`document_id` is a 12-char MD5 hash of the source URL (`src/mcp_servers/cherwell_scraper/parsers.py:638-641`):

```python
def _generate_document_id(self, url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]
```

It is:
- Generated at scrape time, stable across re-scrapes of the same document.
- Already present in `_selected_documents` and used for download records, manifest lookups, event logging.
- Short enough to include in prompts without bloat.
- Opaque enough that Claude has no reason to paraphrase or modify it.

This is the ideal identifier for echo-back.

---

## Proposed Approach

Shift URL (and title) from "LLM-echoed" to "deterministic lookup via stable id":

1. **Persist `document_id` in `document_metadata`.** Change the metadata dict at `orchestrator.py:854` and the S3 URL update at `:906` to also store `document_id`. Re-key the dict from `local_path` → `document_id` (or keep `local_path` keying and add `document_id` as a value field — see open question).

2. **Show `document_id` to Claude** in `ingested_docs_text`. New line format, e.g.:
   ```
   - [abc123def456] {description} (type: {doc_type})
   ```
   URL is removed from this text — Claude doesn't need to see it anymore.

3. **Add `document_id` to `KeyDocumentItem`** (`src/agent/review_schema.py:77-98`) as a required field. Update `structure_prompt.py` to instruct Claude to echo back the `document_id` and to stop producing `url`/`title`. (Claude still produces `category` and `summary`.)

4. **Backfill in orchestrator.py:1838.** Look up `title` (from `description`) and `url` from metadata using the `document_id`. If the id isn't in metadata, log a warning and emit the key document with `url: null` and Claude's `title` (or drop the entry — open question).

### What the LLM still does

- Selects which documents to include in `key_documents`.
- Assigns one of the three categories.
- Writes the one-sentence summary.
- Echoes back the `document_id` it selected.

### What becomes deterministic

- `title` — from `document_metadata[document_id]["description"]`.
- `url` — from `document_metadata[document_id]["url"]`.

---

## Integration Points

| File | Lines | Change |
|---|---|---|
| `src/agent/orchestrator.py` | 83 | `IngestionResult.document_metadata` — re-key or add `document_id` field |
| `src/agent/orchestrator.py` | 854, 906, 941 | Populate `document_id` into metadata dict |
| `src/agent/orchestrator.py` | 1493-1504 | Rebuild `ingested_docs_text` with `document_id` prefix, drop URL |
| `src/agent/orchestrator.py` | 1838-1846 | Replace passthrough with metadata lookup + mismatch logging |
| `src/agent/review_schema.py` | 77-98 | Add `document_id` to `KeyDocumentItem`; `url`/`title` become populated server-side |
| `src/agent/prompts/structure_prompt.py` | 87-91 | Update instructions — echo `document_id`, no `title`/`url` |
| `src/api/schemas.py` | 166-188 | `KeyDocument` unchanged (still exposes `title`, `url`) — contract to consumers stays the same |

**Contract stability:** The public API response shape (`KeyDocument`) does not change. This is an internal reliability fix.

---

## Risks / Open Questions

1. **Keying of `document_metadata`.** Today it's keyed by `local_path`. Other code reads it by path (check usages before re-keying — likely the prompt-building code and anywhere else that iterates `.items()`). Safer option: keep `local_path` keys, add `document_id` as a value field, and build a `{document_id: metadata}` index at prompt-building time.

2. **LLM hallucinating a `document_id`.** Possible but unlikely given 12-char opaque hashes don't look like anything. Mitigation: the warning log on mismatch, plus leaving `url: null` and `title` as whatever Claude returned (or dropping the entry). User confirmed `null` is acceptable if rare.

3. **Claude returns a valid `document_id` that wasn't in the ingested list.** Shouldn't happen, but the same mismatch path handles it.

4. **Existing tests** — `tests/test_orchestrator.py:1049` asserts `url is not None`. With backfill in place, this becomes strictly stronger rather than weaker; check for other tests that construct `KeyDocumentItem` without `document_id` (they'll need the new required field).

5. **`url` optionality on `KeyDocumentItem`.** Stays `Optional[str]` for the rare mismatch case, but on the happy path is always populated by the backfill.

6. **Should mismatched entries be dropped rather than returned with null URL?** Consistent with user's "should not really happen" comment — either is defensible. Design phase call.

---

## Prior Art in `.sdd/`

- `.sdd/key-documents/` — original feature spec defining the `key_documents` array contract (categories, summary, URL).
- `.sdd/reliable-structure-extraction/` — precedent for hardening the structure call output (Literal categories, coercion) — same underlying concern that Claude-produced structured output is unreliable.
- `.sdd/review-output-urls/` — downstream consumer; its `urls_only` mode still returns the full `review` object including `key_documents`, so URLs matter for this flow.

---

## Convention Notes

- The codebase already treats the structure call as unreliable and layers deterministic post-processing on top (e.g. `coerce_category` validator in `KeyDocumentItem`). This proposal extends the same pattern.
- `[feature:FR-xxx]` traceability comments are standard on schema classes; keep them when modifying `KeyDocumentItem` / `KeyDocument`.
- Warnings use structured logging (`logger.warning(...)` with event-style message); match that style for the mismatch log.
