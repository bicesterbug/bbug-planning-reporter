# Specification: Policy Authority Model & Bulk Import

**Version:** 1.0
**Date:** 2026-02-17
**Status:** Draft

---

## Problem Statement

All policies currently exist in a flat list with no association to the authority or organisation that issued them. This prevents the system from being reused for authorities other than Cherwell, since there is no way to segregate which policies apply to which area. Additionally, adding a new authority's policy set requires multiple individual API calls — there is no way to bulk-import a set of policies from a configuration file with PDF download URLs.

## Beneficiaries

**Primary:**
- System operator setting up the system for a new authority (e.g. a cycling group in another district)

**Secondary:**
- Review consumers who get more accurate reviews because only relevant authority policies are queried

---

## Outcomes

**Must Haves**
- Each policy has an `authority_id` identifying the issuing authority or organisation (e.g. `national`, `oxfordshire`, `cherwell`, `bicester`)
- Each authority has a configuration that defines which parent authorities it inherits policies from (e.g. `bicester` inherits from `cherwell` → `oxfordshire` → `national`)
- A bulk import API endpoint accepts a JSON configuration with policy definitions and PDF download URLs, and asynchronously downloads and ingests all revisions
- Existing seed policies are tagged with appropriate authority IDs
- The sample seed_config.json format is extended with authority_id fields

**Nice-to-haves**
- Review requests can specify an authority context, and the system automatically includes all inherited policies in the search
- Authority hierarchy is queryable via the API

---

## Explicitly Out of Scope

- Automatic resolution of authority from application address/location (geocoding)
- Separate ChromaDB collections per authority (metadata filtering is sufficient)
- Changes to the ingestion pipeline itself
- UI for managing authorities

---

## Functional Requirements

**FR-001: Authority metadata on policies**
- Description: Each policy must have an `authority_id` field identifying the issuing authority or organisation (e.g. `national`, `oxfordshire`, `cherwell`, `bicester`, `sustrans`). The field is required when creating a policy. The authority_id follows the same slug conventions as policy sources (lowercase_snake_case).
- Acceptance criteria: `POST /api/v1/policies` requires `authority_id`. `GET /api/v1/policies` returns `authority_id` for each policy. Policies can be filtered by `authority_id`.
- Failure/edge cases: Creating a policy without `authority_id` returns 422. Filtering by a non-existent authority_id returns an empty list (not an error).

**FR-002: Authority hierarchy configuration**
- Description: An authority configuration defines the inheritance chain — which parent authorities' policies should be included when querying for a given authority. For example, `bicester` inherits `cherwell`, `oxfordshire`, and `national`. The configuration is stored in Redis and managed via API endpoints.
- Acceptance criteria: `POST /api/v1/authorities` creates an authority with a `parent_authorities` list. `GET /api/v1/authorities/{authority_id}` returns the authority and its resolved hierarchy (all ancestors). Querying policies for `bicester` returns policies from `bicester`, `cherwell`, `oxfordshire`, and `national`.
- Failure/edge cases: Circular inheritance (A inherits B, B inherits A) must be detected and rejected. An authority with no parents only returns its own policies.

**FR-003: Bulk import endpoint**
- Description: A new endpoint `POST /api/v1/policies/import` accepts a JSON body defining multiple policies with their revisions and PDF download URLs. The endpoint validates the configuration, creates all policies and revisions, downloads PDFs from the provided URLs, and enqueues ingestion jobs for each revision. The operation is asynchronous — returns immediately with a batch job ID for tracking.
- Acceptance criteria: Submitting a JSON config with 3 policies (each with a PDF URL) returns 202 with a batch import ID. All 3 PDFs are downloaded and ingested. Individual revision statuses can be polled via the existing status endpoint.
- Failure/edge cases: If a PDF URL is unreachable, that revision fails but others continue. If a policy already exists, it is skipped (idempotent). The config is validated before any downloads begin — if the schema is invalid, nothing is created.

**FR-004: Bulk import JSON schema**
- Description: The bulk import config must support the following structure: an `authority_id` for the batch, and a list of policies each with source, title, category, description, and a list of revisions each with version_label, effective_from, effective_to, and `pdf_url` (the URL to download the PDF from).
- Acceptance criteria: The JSON schema is documented and validated. An example config file (`data/policy/seed_config_example.json`) demonstrates the format.
- Failure/edge cases: Missing required fields return 422 with details of which fields are missing.

**FR-005: Tag existing seed policies with authority_id**
- Description: The existing `seed_config.json` must be extended with `authority_id` fields. National policies (NPPF, LTN_1_20, MANUAL_FOR_STREETS) get `national`. County policies (OCC_LTCP) get `oxfordshire`. District policies (CHERWELL_LP_2015) get `cherwell`. Town policies (BICESTER_LCWIP) get `bicester`. The seed script must pass authority_id when creating policies.
- Acceptance criteria: After re-seeding, all policies have the correct authority_id. `GET /api/v1/policies?authority_id=national` returns only NPPF, LTN_1_20, MANUAL_FOR_STREETS.
- Failure/edge cases: Existing policies without authority_id (from before this change) should be handled gracefully — default to null or require migration.

**FR-006: Policy query by authority with inheritance**
- Description: `GET /api/v1/policies` supports an `authority_id` parameter that returns all policies for that authority and all its ancestors. For example, `?authority_id=bicester` returns policies from `bicester`, `cherwell`, `oxfordshire`, and `national`.
- Acceptance criteria: Querying with `authority_id=bicester` returns all 6+ seed policies. Querying with `authority_id=national` returns only the 3 national policies.
- Failure/edge cases: If the authority has no hierarchy defined, only policies with that exact authority_id are returned.

---

## QA Plan

**QA-01: Bulk import a set of policies for a new authority**
- Goal: Validate end-to-end bulk import
- Steps:
  1. Create authority `test_authority` with parents `[national]`
  2. Submit bulk import config with 2 policies, each with a PDF URL pointing to a public PDF
  3. Poll until all revisions are `active`
  4. Query `GET /api/v1/policies?authority_id=test_authority` — should include the 2 new policies plus national ones
- Expected: All policies created, PDFs downloaded, chunks ingested, authority filtering works.

**QA-02: Verify seed policies have correct authority_id**
- Goal: Validate existing data tagging
- Steps:
  1. Re-seed policies on a fresh environment
  2. Query `GET /api/v1/policies?authority_id=bicester`
  3. Verify BICESTER_LCWIP is returned
  4. Query `GET /api/v1/policies?authority_id=national`
  5. Verify NPPF, LTN_1_20, MANUAL_FOR_STREETS are returned
- Expected: Authority filtering correctly resolves inheritance.

**QA-03: Authority hierarchy prevents circular references**
- Goal: Validate hierarchy safety
- Steps:
  1. Create authority A with parent B
  2. Try to create authority B with parent A
- Expected: 409 or 422 error indicating circular inheritance.

---

## Open Questions

None.

---

## Appendix

### Glossary
- **Authority**: An organisation that issues planning policy documents (e.g. national government, county council, district council, town council, Sustrans)
- **Authority hierarchy**: The inheritance chain defining which parent authorities' policies apply when querying for a given authority
- **Bulk import**: Creating multiple policies and revisions from a single JSON configuration with PDF download URLs

### References
- Current seed config: `data/policy/seed_config.json`
- Policy registry: `src/shared/policy_registry.py`
- Policy API: `src/api/routes/policies.py`

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-17 | Claude | Initial specification |
