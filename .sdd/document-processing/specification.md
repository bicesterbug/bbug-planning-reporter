# Specification: Document Processing Pipeline

**Version:** 1.0
**Date:** 2026-02-06
**Status:** Draft

---

## Problem Statement

Downloaded planning documents (PDFs, images) need to be converted into searchable text chunks and stored in a vector database so the AI agent can semantically search for relevant content when reviewing applications. The system must handle both text-based PDFs and scanned documents requiring OCR.

## Beneficiaries

**Primary:**
- AI agent that needs to search document content
- End users who receive reviews based on document analysis

**Secondary:**
- System operators who need to monitor ingestion success rates
- Developers debugging document processing issues

---

## Outcomes

**Must Haves**
- Extract text from PDF documents (native text layer)
- OCR scanned documents and images
- Chunk extracted text into semantically meaningful segments
- Generate embeddings for text chunks
- Store chunks with metadata in ChromaDB
- Provide semantic search across ingested documents
- Track ingestion progress and report via webhooks

**Nice-to-haves**
- Table extraction from structured documents
- Automatic document type classification
- Handling of drawing/plan images (flag for human review)

---

## Explicitly Out of Scope

- AI agent review generation (Phase 4)
- Policy document handling (Phase 3 - separate collection)
- API authentication (Phase 5)

---

## Functional Requirements

### FR-001: Extract Text from PDF
**Description:** The system must extract text content from PDFs that have a text layer using PyMuPDF.

**Examples:**
- Positive case: PDF with searchable text yields extracted content
- Edge case: PDF with mixed text/image pages extracts available text

### FR-002: OCR Scanned Documents
**Description:** The system must use Tesseract OCR to extract text from scanned PDFs and images when no text layer is present.

**Examples:**
- Positive case: Scanned PDF yields OCR-extracted text
- Edge case: Low-quality scan produces text with confidence metrics

### FR-003: Chunk Text Content
**Description:** The system must split extracted text into chunks suitable for embedding, using recursive character splitting with configurable size (default 1000 tokens) and overlap (default 200 tokens).

**Examples:**
- Positive case: Long document split into overlapping chunks
- Edge case: Short document produces single chunk

### FR-004: Generate Embeddings
**Description:** The system must generate vector embeddings for each text chunk using the `all-MiniLM-L6-v2` sentence transformer model (384 dimensions).

**Examples:**
- Positive case: Text chunk produces 384-dimensional embedding vector

### FR-005: Store in ChromaDB
**Description:** The system must store chunks with embeddings and metadata (application_ref, document_type, source_file, page_number, chunk_index) in the `application_docs` ChromaDB collection.

**Examples:**
- Positive case: Chunk stored with all metadata fields populated
- Edge case: Duplicate chunks (re-ingestion) are handled idempotently

### FR-006: Ingest Document
**Description:** The document store MCP server must provide an `ingest_document` tool that processes a file through extraction, chunking, and embedding.

**Examples:**
- Positive case: `ingest_document(path, ref, type, metadata)` returns chunk count
- Edge case: Unsupported file type returns error

### FR-007: Search Application Documents
**Description:** The document store MCP server must provide a `search_application_docs` tool for semantic search, optionally filtered by application reference.

**Examples:**
- Positive case: Query returns ranked results with relevance scores
- Edge case: Search with non-existent application_ref returns empty results

### FR-008: Get Document Text
**Description:** The document store MCP server must provide a `get_document_text` tool to retrieve the full text of a specific ingested document.

**Examples:**
- Positive case: Returns concatenated text of all chunks for document
- Edge case: Non-existent document_id returns error

### FR-009: List Ingested Documents
**Description:** The document store MCP server must provide a `list_ingested_documents` tool to enumerate all documents ingested for an application.

**Examples:**
- Positive case: Returns list with document IDs, types, and chunk counts
- Edge case: Application with no documents returns empty list

### FR-010: Document Type Classification
**Description:** The system should classify documents into types (transport_assessment, design_access_statement, site_plan, etc.) based on filename patterns and content heuristics.

**Examples:**
- Positive case: "Transport_Assessment_v2.pdf" classified as `transport_assessment`
- Edge case: Unrecognised filename falls back to `other`

### FR-011: Track Ingestion Progress
**Description:** The worker must publish progress events during document ingestion showing documents processed vs total.

**Examples:**
- Positive case: Webhook receives `review.progress` with "Ingested 14 of 24 documents"

### FR-012: Handle Images in Documents
**Description:** The system must detect image-heavy pages (site plans, drawings) and flag them for potential human review rather than attempting text extraction.

**Examples:**
- Positive case: Site plan PDF noted in metadata as "contains_drawings: true"

---

## Non-Functional Requirements

### NFR-001: Processing Throughput
**Category:** Performance
**Description:** Document ingestion must complete in reasonable time for typical planning applications.
**Acceptance Threshold:** Process 50 documents (average 20 pages each) within 5 minutes
**Verification:** Load testing with sample document set

### NFR-002: OCR Quality
**Category:** Reliability
**Description:** OCR must produce usable text from typical scanned planning documents.
**Acceptance Threshold:** >90% word accuracy on clean scans; >70% on degraded scans
**Verification:** Manual verification with sample documents

### NFR-003: Embedding Consistency
**Category:** Reliability
**Description:** Same text must produce identical embeddings for reproducible search results.
**Acceptance Threshold:** Identical input produces identical output vectors
**Verification:** Unit testing

### NFR-004: Storage Efficiency
**Category:** Scalability
**Description:** ChromaDB storage must be efficient for the expected document volume.
**Acceptance Threshold:** <100MB per application (assuming 50 documents, 1000 chunks average)
**Verification:** Measurement during integration testing

### NFR-005: Search Latency
**Category:** Performance
**Description:** Semantic search must return results quickly for agent queries.
**Acceptance Threshold:** Search returns top 10 results in <500ms
**Verification:** Load testing

### NFR-006: Graceful Degradation
**Category:** Reliability
**Description:** Failure to process one document must not prevent processing of others.
**Acceptance Threshold:** Individual document failures logged; overall job continues
**Verification:** Integration testing with intentionally corrupt files

---

## Open Questions

None at this time.

---

## Appendix

### Glossary

- **Chunk:** A segment of text sized appropriately for embedding and retrieval
- **Embedding:** A vector representation of text enabling semantic similarity search
- **OCR:** Optical Character Recognition - extracting text from images
- **ChromaDB:** Vector database for storing and searching embeddings

### References

- [Master Design Document](../../docs/DESIGN.md) - Section 3.2 Document Store
- [sentence-transformers](https://www.sbert.net/) - Embedding model documentation

### Change History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-06 | SDD Agent | Initial specification |
