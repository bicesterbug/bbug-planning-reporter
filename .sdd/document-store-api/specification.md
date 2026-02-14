# Specification: Document Store MCP Server API

**Version:** 1.0
**Date:** 2026-02-14
**Status:** As-Built

---

## Problem Statement

The AI review agent needs programmatic access to ingested planning application documents through a well-defined tool interface. The Document Store MCP server exposes four tools over the Model Context Protocol that allow the agent (and the worker orchestrator) to ingest PDFs, search document content semantically, retrieve full document text, and list ingested documents for a given application. This specification documents the server's tool contracts, transport configuration, processing pipeline internals, and storage schema as they exist in the running system.

## Beneficiaries

**Primary:**
- AI review agent that calls MCP tools to search and retrieve document content during application review
- Worker service that calls `ingest_document` to load downloaded PDFs into the vector store

**Secondary:**
- System operators monitoring ingestion outcomes via tool response statuses
- Developers integrating new MCP clients against the documented tool contracts

---

## Outcomes

**Must Haves**
- Four MCP tools (`ingest_document`, `search_application_docs`, `get_document_text`, `list_ingested_documents`) accessible over dual SSE and Streamable HTTP transports
- PDF text extraction with OCR fallback for scanned pages
- Recursive character chunking with page number tracking
- 384-dimensional embeddings via all-MiniLM-L6-v2
- ChromaDB persistence with per-chunk metadata
- Bearer token authentication on all endpoints except `/health`
- Idempotent ingestion (deduplication by file content hash + application ref)

**Nice-to-haves**
- Automatic document type classification by filename and content keywords
- Image-based document detection and skip (avoids wasting time on drawings/renderings)

---

## Explicitly Out of Scope

- REST API endpoints (all access is via MCP protocol only)
- Other MCP servers in the stack (cherwell-scraper-mcp, policy-kb-mcp)
- Policy document storage and retrieval (handled by the policy-kb-mcp server with a separate ChromaDB collection)
- Review generation logic (handled by the worker/agent layer)
- Document download and file management (handled by the worker before calling `ingest_document`)

---

## Functional Requirements

### [document-store-api:FR-001] ingest_document Tool

**Description:** Ingests a document into the vector store. Accepts a file path, application reference, and optional document type. Processes the file through classification, text extraction, chunking, embedding, and ChromaDB storage. Returns ingestion outcome with metadata.

**Input Schema:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file_path` | string | yes | Absolute path to the document file on disk |
| `application_ref` | string | yes | Planning application reference (e.g. `"25/01178/REM"`) |
| `document_type` | string | no | Document type override (e.g. `"transport_assessment"`). Auto-classified from filename/content if not provided. |

**Output Schema (success):**

```json
{
  "status": "success",
  "document_id": "25_01178_REM_a1b2c3",
  "chunks_created": 42,
  "extraction_method": "text_layer",
  "contains_drawings": false,
  "total_chars": 85210,
  "total_words": 14035
}
```

**Output Schema (already ingested):**

```json
{
  "status": "already_ingested",
  "document_id": "25_01178_REM_a1b2c3",
  "message": "Document has already been ingested with the same content"
}
```

**Output Schema (skipped - image-based):**

```json
{
  "status": "skipped",
  "reason": "image_based",
  "image_ratio": 0.891,
  "total_pages": 4
}
```

**Output Schema (error):**

```json
{
  "status": "error",
  "error_type": "file_not_found | unsupported_file_type | extraction_failed | no_content | no_chunks",
  "message": "Human-readable error description"
}
```

**Behaviour:**
1. Validates file exists and has a supported extension (`.pdf`, `.png`, `.jpg`, `.jpeg`, `.tiff`, `.tif`).
2. Computes SHA-256 file hash. Generates `document_id` as `{sanitized_ref}_{hash_prefix_6}`. Checks the document registry; if already ingested with the same hash, returns `"already_ingested"`.
3. Classifies the document via `DocumentProcessor.classify_document()`. If the average image ratio across all pages exceeds the threshold (default 0.7, configurable via `IMAGE_RATIO_THRESHOLD` env var), returns `"skipped"` with `reason: "image_based"`. Architectural renderings (detected by filename pattern) also skip OCR.
4. Extracts text via PyMuPDF text layer (primary). For pages with fewer than 10 characters, falls back to Tesseract OCR at 300 DPI if OCR is enabled.
5. Chunks extracted text using `RecursiveCharacterTextSplitter` (see [document-store-api:FR-006]).
6. Generates embeddings for all chunks in batch (see [document-store-api:FR-007]).
7. If `document_type` was not provided, auto-classifies using `DocumentClassifier` (filename pattern matching first, then content keyword analysis, then fallback to `"other"`).
8. Upserts chunk records into the `application_docs` ChromaDB collection and registers the document in the `document_registry` collection.

### [document-store-api:FR-002] search_application_docs Tool

**Description:** Performs semantic search across ingested document chunks. Embeds the query string and queries ChromaDB for the nearest neighbours, optionally filtered by application reference and/or document types.

**Input Schema:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | yes | -- | Natural language search query |
| `application_ref` | string | no | null | Filter results to a specific application |
| `document_types` | string[] | no | null | Filter by document types (uses ChromaDB `$in` operator) |
| `max_results` | int | no | 10 | Maximum number of results to return |

**Output Schema:**

```json
{
  "status": "success",
  "query": "cycle parking provision",
  "results_count": 5,
  "results": [
    {
      "chunk_id": "25_01178_REM_a1b2c3_014_042",
      "text": "The development provides 200 covered cycle parking spaces...",
      "relevance_score": 0.87,
      "metadata": {
        "application_ref": "25/01178/REM",
        "document_id": "25_01178_REM_a1b2c3",
        "source_file": "Transport_Assessment.pdf",
        "document_type": "transport_assessment",
        "page_numbers": "14,15",
        "chunk_index": 42,
        "total_chunks": 68,
        "extraction_method": "text_layer",
        "char_count": 782,
        "word_count": 131
      }
    }
  ]
}
```

**Behaviour:**
1. Embeds the query string using the same all-MiniLM-L6-v2 model used for document embeddings.
2. Builds a ChromaDB `where` clause from the optional filters. When both `application_ref` and `document_types` are provided, they are combined with `$and`.
3. Queries the `application_docs` collection with `n_results` set to `max_results`.
4. Converts ChromaDB L2 distances to relevance scores via `max(0, 1 - (distance / 2))`.
5. Returns results ordered by relevance (highest score first).

### [document-store-api:FR-003] get_document_text Tool

**Description:** Retrieves the full text of an ingested document by fetching all its chunks from ChromaDB and reassembling them in chunk-index order.

**Input Schema:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `document_id` | string | yes | Document ID (e.g. `"25_01178_REM_a1b2c3"`) |

**Output Schema (success):**

```json
{
  "status": "success",
  "document_id": "25_01178_REM_a1b2c3",
  "file_path": "/data/raw/25_01178_REM/Transport_Assessment.pdf",
  "document_type": "transport_assessment",
  "chunk_count": 68,
  "text": "Full document text reconstructed from chunks..."
}
```

**Output Schema (error):**

```json
{
  "status": "error",
  "error_type": "document_not_found | no_chunks",
  "message": "Document not found: 25_01178_REM_a1b2c3"
}
```

**Behaviour:**
1. Looks up the document in the `document_registry` collection. Returns `"document_not_found"` error if absent.
2. Fetches all chunks from `application_docs` where `metadata.document_id` matches, sorted by `chunk_index`.
3. Concatenates chunk texts with `"\n\n"` separator.

### [document-store-api:FR-004] list_ingested_documents Tool

**Description:** Lists all documents ingested for a given planning application reference.

**Input Schema:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `application_ref` | string | yes | Application reference to list documents for (e.g. `"25/01178/REM"`) |

**Output Schema:**

```json
{
  "status": "success",
  "application_ref": "25/01178/REM",
  "document_count": 12,
  "documents": [
    {
      "document_id": "25_01178_REM_a1b2c3",
      "file_path": "/data/raw/25_01178_REM/Transport_Assessment.pdf",
      "document_type": "transport_assessment",
      "chunk_count": 68,
      "ingested_at": "2026-02-14T10:30:00+00:00",
      "extraction_method": "text_layer",
      "contains_drawings": false
    }
  ]
}
```

**Behaviour:**
1. Queries the `document_registry` collection where `metadata.application_ref` matches the input.
2. Returns an empty `documents` array (with `document_count: 0`) if no documents are found for the application.

### [document-store-api:FR-005] Document Processing Pipeline

**Description:** PDF text extraction pipeline with OCR fallback for scanned content and image-heavy document detection.

**Implementation Details:**
- **Primary extraction:** PyMuPDF (`fitz`) text layer extraction via `page.get_text("text")`.
- **OCR fallback:** When a page yields fewer than 10 characters and OCR is enabled, the page is rendered to a 300 DPI pixmap and processed with `pytesseract.image_to_string()`. OCR confidence scores are captured via `pytesseract.image_to_data()`.
- **Image ratio detection:** For each page, the ratio of total image area to page area is computed. If the average ratio across all pages exceeds 0.7 (configurable via `IMAGE_RATIO_THRESHOLD` env var), the document is classified as image-based and skipped before extraction.
- **Rendering detection:** Filenames matching architectural rendering patterns (bird's eye, perspective, CGI, 3D visual, artist's impression, photomontage, street scene, render) bypass OCR entirely, as these are 3D renders that produce no useful text.
- **Supported file types:** `.pdf`, `.png`, `.jpg`, `.jpeg`, `.tiff`, `.tif`, `.bmp`.
- **Image files:** Non-PDF image files are processed entirely via OCR (rendered as single-page documents).
- **Extraction method tracking:** Each page records its method (`"text_layer"`, `"ocr"`, or `"mixed"` when the document uses both across pages). The document-level method summarises the overall approach.

### [document-store-api:FR-006] Text Chunking

**Description:** Recursive character splitting of extracted text into chunks suitable for embedding, with page number tracking.

**Implementation Details:**
- **Library:** `langchain_text_splitters.RecursiveCharacterTextSplitter`.
- **Default chunk size:** 200 tokens (approximated as 800 characters at 4 chars/token).
- **Default chunk overlap:** 50 tokens (200 characters).
- **Separator hierarchy:** `"\n\n"` (paragraph) > `"\n"` (line) > `". "` (sentence) > `"! "` > `"? "` > `"; "` (clause) > `", "` (phrase) > `" "` (word) > `""` (character). Separators are kept with the preceding chunk (`keep_separator=True`).
- **Page tracking:** When chunking multi-page documents via `chunk_pages()`, a character-position-to-page-number mapping is built so each chunk records which page(s) it spans. Page numbers are stored as a comma-separated string in metadata (ChromaDB does not support list metadata values).

### [document-store-api:FR-007] Embedding Generation

**Description:** Vector embedding generation for text chunks and search queries using a sentence transformer model.

**Implementation Details:**
- **Model:** `sentence-transformers/all-MiniLM-L6-v2` loaded via the `sentence_transformers` library.
- **Embedding dimensions:** 384.
- **Max sequence length:** 256 tokens. Text exceeding `256 * 4 = 1024` characters is truncated with a warning log.
- **Lazy loading:** The model is loaded on first use, not at server startup.
- **Batch encoding:** `embed_batch()` processes multiple texts with configurable `batch_size` (default 32).
- **Empty text rejection:** Both `embed()` and `embed_batch()` raise `ValueError` for empty/whitespace-only input.

### [document-store-api:FR-008] ChromaDB Storage

**Description:** Persistent vector storage and retrieval using ChromaDB with two collections.

**Implementation Details:**
- **Client:** `chromadb.PersistentClient` with `anonymized_telemetry=False`. Persistence directory defaults to `/data/chroma` (configurable via `CHROMA_PERSIST_DIR` env var). Falls back to in-memory client when no directory is specified.
- **Collection `application_docs`:** Stores document chunks with embeddings and metadata. Used for semantic search and chunk retrieval.
- **Collection `document_registry`:** Stores document-level metadata for idempotency checks and listing. Uses placeholder `[0.0] * 384` embeddings (not used for search).
- **Chunk ID format:** `{sanitized_ref}_{file_hash_6}_{page_number:03d}_{chunk_index:03d}` (e.g. `25_01178_REM_a1b2c3_014_042`).
- **Document ID format:** `{sanitized_ref}_{file_hash_6}` (e.g. `25_01178_REM_a1b2c3`).
- **File hashing:** SHA-256, read in 8192-byte blocks.
- **Chunk metadata fields:** `application_ref`, `document_id`, `source_file`, `document_type`, `page_numbers` (comma-separated string), `chunk_index`, `total_chunks`, `extraction_method`, `char_count`, `word_count`.
- **Document registry metadata fields:** `file_path`, `file_hash`, `application_ref`, `document_type`, `chunk_count`, `ingested_at`, `extraction_method`, `contains_drawings`.
- **Search:** Queries the `application_docs` collection using query embeddings with optional `where` filters. Returns L2 distances converted to relevance scores.
- **Deletion:** `delete_document()` removes all chunks from `application_docs` and the registry entry from `document_registry`.

### [document-store-api:FR-009] Transport Protocol

**Description:** The server exposes MCP tools over dual transports with a health check endpoint.

**Implementation Details:**
- **Framework:** Starlette ASGI application served by Uvicorn.
- **Port:** 3002 (configurable via `DOCUMENT_STORE_PORT` env var).
- **Host:** `0.0.0.0` (all interfaces).
- **Endpoints:**
  - `GET /health` -- Returns `{"status": "ok"}`. Exempt from authentication.
  - `GET /sse` -- SSE transport endpoint (legacy, used by the internal worker).
  - `POST /messages/` -- SSE message posting endpoint.
  - `GET|POST|DELETE /mcp` -- Streamable HTTP transport endpoint (current MCP standard).
- **Session management:** `StreamableHTTPSessionManager` manages Streamable HTTP sessions with a lifespan context manager.

### [document-store-api:FR-010] Authentication

**Description:** Bearer token authentication on all MCP endpoints.

**Implementation Details:**
- **Middleware:** `MCPAuthMiddleware` (Starlette `BaseHTTPMiddleware`).
- **Token source:** `MCP_API_KEY` environment variable. When unset or empty, authentication is disabled (no-op pass-through for backward compatibility).
- **Header format:** `Authorization: Bearer <token>`.
- **Exempt paths:** `/health` is always unauthenticated.
- **Token comparison:** Constant-time comparison via `hmac.compare_digest()`.
- **Rejection responses:** HTTP 401 with JSON body `{"error": {"code": "unauthorized", "message": "..."}}`.
- **Logging:** Failed authentication attempts are logged at WARNING level with client IP, endpoint, and HTTP method.
- **Scheme enforcement:** Only the `Bearer` scheme is accepted. Basic auth and other schemes are rejected.

---

## Non-Functional Requirements

### [document-store-api:NFR-001] OCR Quality

**Category:** Reliability
**Description:** The system must flag low-confidence OCR pages and detect drawing-heavy documents to avoid ingesting unusable text.
**Acceptance Threshold:** OCR pages with average confidence below 70% are logged at WARNING level. Documents with average image ratio above 0.7 are skipped entirely (not ingested).
**Verification:** Integration testing with scanned and image-heavy PDFs.

### [document-store-api:NFR-002] Idempotency

**Category:** Correctness
**Description:** Re-ingesting the same file (identical SHA-256 hash) for the same application reference must not create duplicate chunks. The system deduplicates by checking the document registry before processing.
**Acceptance Threshold:** Calling `ingest_document` twice with the same file produces `"already_ingested"` on the second call with zero additional chunks.
**Verification:** Unit and integration testing.

### [document-store-api:NFR-003] Memory

**Category:** Resource Management
**Description:** The document-store-mcp container must operate within its memory limit.
**Acceptance Threshold:** Container limited to 2GB. Embedding model, OCR processing, and ChromaDB must fit within this budget.
**Verification:** Docker container memory monitoring during load testing.

### [document-store-api:NFR-004] Embedding Consistency

**Category:** Reliability
**Description:** The same input text must always produce the same embedding vector to ensure deterministic search results.
**Acceptance Threshold:** Identical input text produces bit-for-bit identical output vectors across invocations.
**Verification:** Unit testing with `EmbeddingService.embed()` called multiple times on the same input.

---

## Open Questions

None at this time. This specification documents the as-built system.

---

## Appendix

### Glossary

- **MCP:** Model Context Protocol -- a standardised protocol for AI model tool invocation
- **SSE:** Server-Sent Events -- a legacy transport for MCP communication
- **Streamable HTTP:** The current MCP standard transport protocol
- **ChromaDB:** Open-source vector database for storing and searching embeddings
- **Chunk:** A segment of document text sized for embedding (default ~800 characters / ~200 tokens)
- **Embedding:** A 384-dimensional vector representation of text enabling semantic similarity search
- **OCR:** Optical Character Recognition -- extracting text from images via Tesseract
- **PyMuPDF (fitz):** PDF processing library for text layer extraction
- **all-MiniLM-L6-v2:** Sentence transformer model producing 384-dimensional embeddings

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DOCUMENT_STORE_PORT` | `3002` | Server listen port |
| `CHROMA_PERSIST_DIR` | `/data/chroma` | ChromaDB persistence directory |
| `ENABLE_OCR` | `true` | Enable Tesseract OCR fallback |
| `MCP_API_KEY` | (unset) | Bearer token for authentication. Unset = auth disabled. |
| `IMAGE_RATIO_THRESHOLD` | `0.7` | Average image ratio above which documents are skipped as image-based |

### Document Type Classification

The `DocumentClassifier` recognises the following document types, first by filename pattern matching (high confidence), then by content keyword analysis (medium confidence), with fallback to `"other"` (low confidence):

| Type | Example Filename Patterns |
|------|--------------------------|
| `transport_assessment` | transport assessment, travel plan, traffic impact |
| `design_access_statement` | design and access statement, D&A statement |
| `site_plan` | site plan, location plan, block plan, layout plan |
| `floor_plan` | floor plan, ground floor, first floor |
| `elevation` | elevation, street scene |
| `planning_statement` | planning statement, supporting statement |
| `environmental_statement` | environmental impact statement/assessment, EIA |
| `noise_assessment` | noise assessment/report/survey, acoustic |
| `flood_risk_assessment` | flood risk assessment, FRA, drainage strategy |
| `ecology_report` | ecology report/survey, ecological appraisal, bat survey, biodiversity |
| `heritage_statement` | heritage statement/assessment, archaeological |
| `arboricultural_report` | arboricultural report/survey, tree survey |
| `other` | Fallback when no pattern matches |

### References

- Source: `src/mcp_servers/document_store/server.py` -- Tool registrations and handlers
- Source: `src/mcp_servers/document_store/processor.py` -- PDF extraction and OCR
- Source: `src/mcp_servers/document_store/chunker.py` -- Text chunking
- Source: `src/mcp_servers/document_store/embeddings.py` -- Embedding generation
- Source: `src/mcp_servers/document_store/chroma_client.py` -- ChromaDB interface
- Source: `src/mcp_servers/document_store/classifier.py` -- Document type classification
- Source: `src/mcp_servers/shared/transport.py` -- Dual transport setup
- Source: `src/mcp_servers/shared/auth.py` -- Authentication middleware
- Related spec: [Document Processing Pipeline](../document-processing/specification.md)
- Related spec: [Document Type Detection](../document-type-detection/specification.md)

### Change History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-14 | SDD Agent | Initial as-built specification |
