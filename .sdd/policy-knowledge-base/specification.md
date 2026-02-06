# Specification: Policy Knowledge Base

**Version:** 1.0
**Date:** 2026-02-06
**Status:** Draft

---

## Problem Statement

The AI agent needs access to cycling and transport policy documents (LTN 1/20, NPPF, Local Plan, etc.) to benchmark planning applications against requirements. Policies are updated over time, and the system must retrieve the revision that was in force when an application was submitted to ensure accurate compliance assessment.

## Beneficiaries

**Primary:**
- AI agent that needs policy context for reviews
- System administrators who manage policy document updates

**Secondary:**
- End users who receive policy-grounded reviews
- Auditors verifying correct policy versions were used

---

## Outcomes

**Must Haves**
- Store multiple revisions of each policy document with effective date ranges
- Semantic search across policy content with temporal filtering
- Retrieve specific policy sections by reference
- REST API for managing policy documents and revisions
- Automatic selection of correct revision based on application validation date
- Seed initial policy documents at first deployment

**Nice-to-haves**
- Policy change detection and alerts
- Diff analysis between policy revisions

---

## Explicitly Out of Scope

- Automatic policy document fetching from gov.uk
- AI-generated policy summaries
- Integration with application document processing (handled separately)

---

## Functional Requirements

### FR-001: Register Policy Document
**Description:** The system must allow creating a new policy document entry with source slug, title, description, and category.

**Examples:**
- Positive case: POST creates `LTN_1_20` with title "Cycle Infrastructure Design (LTN 1/20)"
- Edge case: Duplicate source slug returns 409 `policy_already_exists`

### FR-002: Upload Policy Revision
**Description:** The system must accept PDF uploads for new policy revisions with version label, effective_from date, and optional effective_to date.

**Examples:**
- Positive case: Upload "nppf-december-2024.pdf" with effective_from "2024-12-12"
- Edge case: Previous revision's effective_to automatically set to day before new effective_from

### FR-003: Process Policy Revision
**Description:** Uploaded policy files must be processed asynchronously through text extraction, chunking, and embedding, storing in the `policy_docs` ChromaDB collection.

**Examples:**
- Positive case: Processing completes, status changes to "active"
- Edge case: Processing failure sets status to "failed" with error details

### FR-004: Temporal Metadata
**Description:** All policy chunks must include `effective_from` and `effective_to` metadata enabling temporal filtering in searches.

**Examples:**
- Positive case: Chunk metadata includes dates as ISO strings
- Edge case: Current revision has empty string for `effective_to`

### FR-005: Search Policy with Effective Date
**Description:** The policy KB MCP server must provide a `search_policy` tool that filters results to revisions in force on a specified date.

**Examples:**
- Positive case: Search with `effective_date="2024-03-15"` returns only chunks from revisions valid on that date
- Edge case: Date before any revision exists returns no results

### FR-006: Get Policy Section
**Description:** The policy KB MCP server must provide a `get_policy_section` tool to retrieve specific sections by document and reference.

**Examples:**
- Positive case: `get_policy_section("LTN_1_20", "Table 5-2")` returns table content
- Edge case: Non-existent section returns appropriate error

### FR-007: List Policy Documents
**Description:** The system must provide an endpoint listing all registered policies with their current revision.

**Examples:**
- Positive case: GET `/api/v1/policies` returns array with current revision info
- Edge case: Policy with no revisions shows `current_revision: null`

### FR-008: List Policy Revisions
**Description:** The system must provide an endpoint listing all revisions for a specific policy document.

**Examples:**
- Positive case: GET `/api/v1/policies/NPPF` returns array of revisions ordered by effective_from
- Edge case: Policy with single revision returns array of one

### FR-009: Get Effective Policy Snapshot
**Description:** The system must provide an endpoint returning which revision of each policy was in force on a given date.

**Examples:**
- Positive case: GET `/api/v1/policies/effective?date=2024-03-15` returns snapshot for all policies
- Edge case: Returns empty array for policies not yet effective on that date

### FR-010: Update Revision Metadata
**Description:** The system must allow updating revision metadata (effective dates, notes) via PATCH.

**Examples:**
- Positive case: PATCH updates effective_to date
- Edge case: Changing dates does not auto-cascade to adjacent revisions

### FR-011: Delete Policy Revision
**Description:** The system must allow deleting a revision, removing its chunks from ChromaDB.

**Examples:**
- Positive case: DELETE removes chunks and registry entry
- Edge case: Cannot delete sole active revision (returns 409)

### FR-012: Reindex Revision
**Description:** The system must allow re-running the ingestion pipeline for an existing revision without re-uploading the file.

**Examples:**
- Positive case: POST triggers re-chunking and re-embedding
- Edge case: Reindex while already processing returns error

### FR-013: Seed Initial Policies
**Description:** The system must seed policy documents (LTN 1/20, NPPF, Manual for Streets, Cherwell Local Plan, LTCP, LCWIP, etc.) with correct effective dates at first deployment.

**Examples:**
- Positive case: `policy-init` container processes all seed documents
- Edge case: Re-running seed is idempotent

### FR-014: Policy Registry in Redis
**Description:** The policy registry (documents, revisions, indexes) must be stored in Redis as the source of truth, with ChromaDB storing only the embeddings.

**Examples:**
- Positive case: Redis contains structured data; ChromaDB contains vectors
- Edge case: Redis and ChromaDB remain consistent after operations

---

## Non-Functional Requirements

### NFR-001: Revision Ingestion Time
**Category:** Performance
**Description:** Policy revision processing must complete in reasonable time.
**Acceptance Threshold:** Process 100-page PDF within 2 minutes
**Verification:** Load testing with sample policy documents

### NFR-002: Temporal Query Accuracy
**Category:** Reliability
**Description:** Effective date filtering must correctly select the right revision.
**Acceptance Threshold:** 100% correct revision selection for any valid date
**Verification:** Unit and integration testing with known date ranges

### NFR-003: Search Relevance
**Category:** Reliability
**Description:** Policy search must return relevant results for cycling/transport queries.
**Acceptance Threshold:** Top 5 results contain relevant content for standard queries
**Verification:** Manual evaluation with sample queries

### NFR-004: Data Consistency
**Category:** Reliability
**Description:** Redis registry and ChromaDB embeddings must remain synchronized.
**Acceptance Threshold:** No orphan chunks; no registry entries without embeddings
**Verification:** Consistency check script; integration testing

### NFR-005: Audit Trail
**Category:** Maintainability
**Description:** The system must track which policy revisions were used for each review.
**Acceptance Threshold:** Review metadata includes revision IDs and version labels used
**Verification:** Integration testing

---

## Open Questions

None at this time.

---

## Appendix

### Glossary

- **Effective Date:** The date from which a policy revision comes into force
- **Revision:** A specific version of a policy document with its own effective date range
- **Source Slug:** Unique identifier for a policy document (e.g., `LTN_1_20`)
- **Temporal Query:** Search filtered by a point-in-time effective date

### References

- [Master Design Document](../../docs/DESIGN.md) - Section 3.3 Policy Knowledge Base
- [LTN 1/20](https://www.gov.uk/government/publications/cycle-infrastructure-design-ltn-120) - Primary cycling infrastructure guidance

### Change History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-06 | SDD Agent | Initial specification |
