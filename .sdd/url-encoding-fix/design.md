# Design: URL Encoding Fix

**Version:** 1.0
**Date:** 2026-02-15
**Status:** Draft
**Linked Specification:** `.sdd/url-encoding-fix/specification.md`

---

## Architecture Overview

### Current Architecture Context

URLs flow through the system without percent-encoding at any stage:

1. Cherwell scraper downloads documents with filenames containing spaces (e.g. `003_Delegated Officer Report.pdf`)
2. These filenames become S3 keys and local file paths
3. `StorageBackend.public_url()` concatenates the key into a URL without encoding
4. The orchestrator injects raw document URLs into the LLM prompt
5. The LLM reproduces unencoded URLs in markdown links
6. Markdown renderers break on spaces in URLs

### Proposed Architecture

Three targeted fixes at different layers:

1. **Source fix** — Replace spaces with underscores in download filenames (prevents spaces entering the system for new reviews)
2. **URL encoding in storage** — `public_url()` percent-encodes path segments in the returned URL
3. **URL encoding in prompt** — Document URLs are percent-encoded before injection into LLM evidence context

---

## Components

### Modified: Cherwell scraper filename generation

**File:** `src/mcp_servers/cherwell_scraper/server.py` line 465

**Current:** `c if c.isalnum() or c in "._- " else "_"` — allows spaces in filenames

**Change:** Remove space from the allowed character set: `c if c.isalnum() or c in "._-" else "_"`

**Requirements References:**
- [url-encoding-fix:FR-003]

**Test Scenarios**

**TS-01: Spaces replaced with underscores in filenames**
- Given: A document with description "Delegated Officer Report"
- When: Filename is generated
- Then: Filename is `003_Delegated_Officer_Report.pdf`

**TS-02: Other characters still sanitised**
- Given: A document with description "Report (Draft) v2"
- When: Filename is generated
- Then: Filename is `003_Report__Draft__v2.pdf` (parentheses replaced)

---

### Modified: StorageBackend.public_url() — all backends

**Files:** `src/shared/storage.py`

**Current:** All three backends build URLs via string concatenation without encoding.

**Change:** Use `urllib.parse.quote(key, safe="/")` to encode path segments while preserving `/` separators. Applied in `LocalStorageBackend.public_url()`, `S3StorageBackend.public_url()`, and `InMemoryStorageBackend.public_url()`.

**Requirements References:**
- [url-encoding-fix:FR-002]

**Test Scenarios**

**TS-03: S3 public_url encodes spaces**
- Given: S3StorageBackend with a key `25_01178_REM/003_Delegated Officer Report.pdf`
- When: `public_url()` is called
- Then: URL contains `003_Delegated%20Officer%20Report.pdf`

**TS-04: Local public_url encodes spaces**
- Given: LocalStorageBackend
- When: `public_url("path/file name.json")` is called
- Then: Returns `/api/v1/files/path/file%20name.json`

**TS-05: Slashes are preserved**
- Given: Any backend with key `25_01178_REM/output/review.json`
- When: `public_url()` is called
- Then: `/` characters are NOT encoded

**TS-06: InMemory public_url encodes spaces**
- Given: InMemoryStorageBackend
- When: `public_url("path/file name.pdf")` is called
- Then: URL contains `file%20name.pdf`

**TS-07: Keys without special characters unchanged**
- Given: Any backend with key `25_01178_REM/output/rev_xxx_review.json`
- When: `public_url()` is called
- Then: URL is unchanged (no encoding needed)

---

### Modified: Orchestrator evidence context URL encoding

**File:** `src/agent/orchestrator.py` line 1295-1296

**Current:** `url = meta.get("url") or "no URL"` — raw URL injected into prompt

**Change:** Apply `urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=-._~")` to encode spaces while preserving valid URL characters. This uses RFC 3986 reserved + unreserved characters as the safe set.

**Requirements References:**
- [url-encoding-fix:FR-001]

**Test Scenarios**

**TS-08: Document URLs with spaces are encoded in evidence context**
- Given: Document metadata with URL `https://example.com/docs/Officer Report.pdf`
- When: Evidence context is built
- Then: URL in prompt text is `https://example.com/docs/Officer%20Report.pdf`

**TS-09: Already-encoded URLs are not double-encoded**
- Given: Document metadata with URL `https://example.com/docs/Officer%20Report.pdf`
- When: Evidence context is built
- Then: URL remains `https://example.com/docs/Officer%20Report.pdf` (no `%2520`)

**TS-10: None/missing URLs unchanged**
- Given: Document metadata with no URL
- When: Evidence context is built
- Then: Text shows "no URL" as before

---

## Task Breakdown

### Phase 1: All fixes (single phase)

**Task 1: Replace spaces with underscores in download filenames**
- Status: Backlog
- Requirements: [url-encoding-fix:FR-003]
- Test Scenarios: [url-encoding-fix:TS-01], [url-encoding-fix:TS-02]
- Details:
  - In `src/mcp_servers/cherwell_scraper/server.py` line 465, remove space from the allowed character set
  - Update tests in `tests/test_mcp_servers/test_cherwell_scraper/test_download_filename.py` if any assert filenames with spaces

**Task 2: Percent-encode public_url() in all storage backends**
- Status: Backlog
- Requirements: [url-encoding-fix:FR-002]
- Test Scenarios: [url-encoding-fix:TS-03], [url-encoding-fix:TS-04], [url-encoding-fix:TS-05], [url-encoding-fix:TS-06], [url-encoding-fix:TS-07]
- Details:
  - Add `from urllib.parse import quote` to `src/shared/storage.py`
  - `LocalStorageBackend.public_url()`: `return f"/api/v1/files/{quote(key, safe='/')}"`
  - `S3StorageBackend.public_url()`: `return f"{self._public_base_url}/{quote(full_key, safe='/')}"`
  - `InMemoryStorageBackend.public_url()`: `return f"{self._base_url}/{quote(key, safe='/')}"`
  - Add/update tests in `tests/test_shared/test_storage.py`

**Task 3: Percent-encode document URLs in orchestrator evidence context**
- Status: Backlog
- Requirements: [url-encoding-fix:FR-001]
- Test Scenarios: [url-encoding-fix:TS-08], [url-encoding-fix:TS-09], [url-encoding-fix:TS-10]
- Details:
  - In `src/agent/orchestrator.py`, encode URLs before injecting into prompt
  - Use `urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=-._~")` to encode only unsafe characters (primarily spaces)
  - Add tests in `tests/test_agent/test_orchestrator.py`

---

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Double-encoding already-encoded URLs | Broken links | Use `quote(url, safe=...)` with broad safe set — only encodes spaces and similar |
| Existing S3 objects with spaces in keys still accessible | Old URLs break if re-encoded | `upload()` uses unencoded keys (S3 handles them); only `public_url()` encodes for HTTP |
| Changed filenames break idempotency | Different filename on re-review | Acceptable — reviews are per review_id, no collision |

---

## Appendix

### References
- [Specification](specification.md)
- `src/shared/storage.py` — Storage backends
- `src/agent/orchestrator.py` — Evidence context builder
- `src/mcp_servers/cherwell_scraper/server.py` — Filename generation

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-15 | SDD | Initial design |
