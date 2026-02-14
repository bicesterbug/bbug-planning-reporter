# Specification: Review Workflow Redesign

**Version:** 1.0
**Date:** 2026-02-13
**Status:** Draft

---

## Problem Statement

The review workflow downloads and vectorises all documents from a planning application (often 100+), wasting bandwidth, storage, and processing time on irrelevant documents (ecology reports, heritage assessments, drainage strategies, etc.). Document filtering relies on brittle keyword/pattern matching against inconsistent portal naming, leading to both false inclusions and false exclusions. Search queries for evidence retrieval are hardcoded rather than tailored to the specific application, and the embedding pipeline truncates 75% of each chunk's content due to a chunk/embedding size mismatch, degrading retrieval quality. Finally, there is no verification stage to catch hallucinations in the generated review.

## Beneficiaries

**Primary:**
- System operators: reduced processing time, bandwidth, storage costs, and API token spend per review
- End users (advocacy group): more accurate, reliable reviews with less hallucinated content

**Secondary:**
- Cherwell planning portal: reduced scraping load (fewer document downloads)
- Developers: cleaner codebase with dead code removed and well-defined workflow stages

---

## Outcomes

**Must Haves**
- LLM-based document filtering that selects only cycling/transport-relevant documents before download, reducing document count by 60-80%
- Embedding chunk sizes aligned with the embedding model's actual input capacity, eliminating truncation
- Dynamically generated search queries tailored to each application's proposal
- Post-generation verification stage that validates citations and key claims against source evidence
- LLM-generated review summary replacing the current naive truncation
- Removal of dead code from the unused assessor/generator/policy_comparer pipeline (~1200 lines)

**Nice-to-haves**
- Structured logging of each workflow stage's duration, document counts, and token usage for operational visibility
- Reduced overall review processing time (fewer documents to download and ingest)

---

## Explicitly Out of Scope

- Changing the embedding model (all-MiniLM-L6-v2 stays; chunk sizes adjusted to fit it)
- Changing the two-phase structure+report LLM approach (kept as-is, with evidence sent to both calls)
- Adding retry logic to MCP client calls (separate concern)
- Replacing the MCP server architecture or transport layer
- Modifying the API contract (request/response schemas unchanged)
- Changing the Cherwell scraper's rate limiting or download mechanism
- Modifying the policy knowledge base or seed policies

---

## Functional Requirements

### FR-001: LLM-based document filtering
**Description:** After fetching the document list from the Cherwell portal, the system sends the full document list (titles, types, descriptions) along with the application metadata (proposal description, address, application type) to a fast LLM (Haiku) in a single call. The LLM returns a list of document IDs that are relevant to a cycling/transport-focused review of the specific application. Only these documents are downloaded and ingested.

**Examples:**
- Positive case: An application with 120 documents. The LLM identifies 15 as relevant (Transport Assessment, Design & Access Statement, Travel Plan, Site Layout, Highway Response, etc.). Only those 15 are downloaded.
- Positive case: A householder extension with 8 documents. The LLM identifies 4 as relevant (Application Form, Site Plan, Block Plan, Design & Access Statement). The filter understands smaller applications have fewer transport-relevant docs.
- Edge case: An application with only 3 documents, all generic names ("Document 1", "Document 2", "Document 3"). The LLM selects all 3 since it cannot determine relevance from names alone.

### FR-002: LLM filter failure handling
**Description:** If the LLM document filter call fails (API error, timeout, malformed response), the review job fails with a structured error. The system does not fall back to keyword-based filtering.

**Examples:**
- Positive case: LLM returns an error. The review fails with error code `document_filter_failed` and a descriptive message.
- Negative case: The system should NOT silently fall back to downloading all documents.

### FR-003: Chunk size alignment with embedding model
**Description:** The text chunking configuration is adjusted so that chunk sizes do not exceed the embedding model's maximum input length. For all-MiniLM-L6-v2 with a 256-token (1024 character) maximum sequence length, chunks must be sized to fit within this limit with appropriate overlap.

**Examples:**
- Positive case: A 10,000-character document is split into ~12 chunks of ~800 characters each with ~200 character overlap. Each chunk is fully represented in its embedding vector.
- Negative case: The system should NOT produce chunks where >50% of the content is truncated during embedding.

### FR-004: Dynamic search query generation
**Description:** In the analysis phase, instead of using hardcoded search queries, the system uses the LLM to generate targeted search queries based on the application's metadata (proposal description, address, application type) and the list of ingested documents. Queries are generated for both application document search and policy search.

**Examples:**
- Positive case: For a "residential development of 200 dwellings with new access road", the LLM generates queries like "vehicle access junction design capacity", "cycle parking provision residential development", "pedestrian and cycle connectivity to Bicester town centre".
- Positive case: For a "change of use from office to restaurant", the LLM generates queries about "customer and staff cycle parking", "delivery vehicle access", rather than irrelevant queries about junction design for residential developments.
- Edge case: The LLM generates queries but one returns zero results. The system proceeds with the queries that did return results.

### FR-005: Post-generation verification
**Description:** After the report is generated, the system performs a verification pass using the LLM. The verification check validates: (1) that documents cited in the review exist in the ingested document list, (2) that key factual claims (e.g. "the Transport Assessment states X") can be traced to actual evidence chunks, and (3) that ratings are consistent with the analysis text. The verification result is included in the review metadata.

**Examples:**
- Positive case: The review cites "Transport Assessment" which exists in the ingested documents. The verification passes for that citation.
- Positive case: The review claims "the application provides 50 cycle parking spaces" but no evidence chunk mentions this figure. The verification flags this as unverified.
- Edge case: The verification finds 2 unverified claims out of 15. The review is still returned but the metadata includes `verification: {status: "partial", unverified_claims: 2, total_claims: 15}`.

### FR-006: LLM-generated review summary
**Description:** The review output includes a concise summary (2-4 sentences) generated by the LLM as part of the review generation process, rather than naively truncating the full markdown at 500 characters.

**Examples:**
- Positive case: Summary reads "This major residential development provides adequate cycle parking but lacks safe cycling connectivity to the town centre. The Transport Assessment does not address LTN 1/20 requirements for junction design. Overall rating: Amber."
- Negative case: The summary should NOT be a mid-sentence truncation like "This application for 200 dwellings on land north of the A41 roundabout has been assessed against key cycling and transport policies including LTN 1/20, the NPPF, and the Cherwell Local Plan. The Transport Ass..."

### FR-007: Dead code removal
**Description:** The unused assessment pipeline code is removed: `src/agent/assessor.py`, `src/agent/generator.py`, `src/agent/policy_comparer.py`, `src/agent/templates.py`, and `src/agent/claude_client.py`. Associated tests for these modules are also removed.

**Examples:**
- Positive case: After removal, `grep -r "from src.agent.assessor" src/` returns no results.
- Positive case: The orchestrator workflow continues to function identically after removal since these files are not imported.

### FR-008: Application-aware filter context
**Description:** The LLM document filter receives the application's proposal description, address, and type alongside the document list. This enables context-aware filtering - for example, understanding that a major housing development needs transport assessments and travel plans, while a householder extension primarily needs site plans and design statements.

**Examples:**
- Positive case: For "Erection of single-storey rear extension", the LLM understands this is minor works and selects fewer, more targeted documents.
- Positive case: For "Outline application for 1,000 dwellings, new primary school, and community facilities", the LLM understands this is a major development and selects transport assessments, travel plans, highway agreements, and infrastructure reports.

---

## Non-Functional Requirements

### NFR-001: Document filter latency
**Category:** Performance
**Description:** The LLM document filtering step completes within a bounded time, adding minimal overhead to the overall review duration.
**Acceptance Threshold:** LLM document filter call completes in under 15 seconds for applications with up to 500 documents.
**Verification:** Observability - structured log of filter call duration emitted at INFO level.

### NFR-002: Reduced resource consumption
**Category:** Performance
**Description:** The redesigned workflow downloads and vectorises significantly fewer documents than the current approach for typical applications.
**Acceptance Threshold:** For applications with 50+ documents, the system downloads and vectorises at least 50% fewer documents compared to the current keyword filter.
**Verification:** Observability - structured log comparing total documents listed vs documents selected vs documents ingested.

### NFR-003: Verification overhead
**Category:** Performance
**Description:** The post-generation verification step adds bounded overhead to the review generation time.
**Acceptance Threshold:** Verification step completes in under 30 seconds.
**Verification:** Observability - structured log of verification call duration emitted at INFO level.

### NFR-004: Chunk embedding coverage
**Category:** Reliability
**Description:** Text chunks are fully represented in their embedding vectors with no truncation.
**Acceptance Threshold:** Zero truncation warnings during embedding generation. 100% of chunk content is within the embedding model's input limit.
**Verification:** Testing - unit test asserting all generated chunks are within the embedding model's max input length. Observability - truncation warning count in logs is zero.

### NFR-005: Review accuracy
**Category:** Reliability
**Description:** The verification stage catches hallucinated citations and unverified claims, providing transparency about review quality.
**Acceptance Threshold:** Verification metadata is present in every completed review output, including counts of verified and unverified claims.
**Verification:** Testing - integration test verifying verification metadata is populated. Observability - verification results logged at INFO level.

### NFR-006: Maintainability
**Category:** Maintainability
**Description:** Dead code from the unused assessment pipeline is removed, reducing codebase size and cognitive load.
**Acceptance Threshold:** Zero imports of removed modules (`assessor`, `generator`, `policy_comparer`, `templates`, `claude_client`) remain in the codebase.
**Verification:** Testing - `grep -r` confirms no remaining references.

---

## Open Questions

None - all design decisions clarified during discovery.

---

## Appendix

### Glossary
- **Document filter**: The stage that determines which planning application documents are relevant for download and review.
- **Evidence chunks**: Text segments from ingested documents, stored as vectors in ChromaDB, retrieved by semantic search during the analysis phase.
- **Structure call**: The first LLM call that produces a structured JSON representation of the review (ratings, aspects, compliance items).
- **Report call**: The second LLM call that produces the full prose markdown review, constrained by the structure JSON.
- **Verification pass**: A post-generation LLM call that cross-references the review output against source evidence to catch hallucinations.
- **Haiku**: Anthropic's fastest and cheapest Claude model, suitable for classification and filtering tasks.

### References
- Current orchestrator: `src/agent/orchestrator.py`
- Current document filter: `src/mcp_servers/cherwell_scraper/filters.py`
- Current chunker: `src/mcp_servers/document_store/chunker.py`
- Current embeddings: `src/mcp_servers/document_store/embeddings.py`
- Current prompts: `src/agent/prompts/structure_prompt.py`, `src/agent/prompts/report_prompt.py`
- Embedding model docs: https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2

### Change History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-13 | Claude | Initial specification |
