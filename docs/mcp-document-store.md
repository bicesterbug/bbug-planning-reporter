# Document Store MCP Server -- API Reference

**Port:** `3002` &bull; **Protocol:** Model Context Protocol (MCP) &bull; **Transport:** SSE + Streamable HTTP

The Document Store MCP server provides the AI review agent and worker orchestrator with programmatic access to planning application documents. It ingests PDFs into a ChromaDB vector store (with OCR fallback for scanned pages), exposes semantic search over document chunks, and supports full-text retrieval and per-application document listing -- all via four MCP tools.

---

## Table of Contents

- [Transport & Authentication](#transport--authentication)
- [Tools](#tools)
  - [ingest_document](#ingest_document)
  - [search_application_docs](#search_application_docs)
  - [get_document_text](#get_document_text)
  - [list_ingested_documents](#list_ingested_documents)
- [Processing Pipeline](#processing-pipeline)
- [Document Type Classification](#document-type-classification)
- [Configuration](#configuration)
- [Error Codes](#error-codes)

---

## Transport & Authentication

### Endpoints

| Method | Path | Auth Required | Description |
|--------|------|---------------|-------------|
| `GET` | `/health` | No | Health check. Returns `{"status": "ok"}`. |
| `GET` | `/sse` | Yes | SSE transport endpoint (legacy, used by internal worker) |
| `POST` | `/messages/` | Yes | SSE message posting endpoint |
| `GET` `POST` `DELETE` | `/mcp` | Yes | Streamable HTTP transport endpoint (current MCP standard) |

### Authentication

All endpoints except `/health` require a Bearer token in the `Authorization` header:

```
Authorization: Bearer <MCP_API_KEY>
```

- **Token source:** `MCP_API_KEY` environment variable. When unset or empty, authentication is disabled (pass-through for backward compatibility).
- **Scheme:** Only the `Bearer` scheme is accepted. Basic auth and other schemes are rejected.
- **Comparison:** Constant-time via `hmac.compare_digest()` to prevent timing attacks.
- **Rejection:** HTTP 401 with JSON body:

```json
{
  "error": {
    "code": "unauthorized",
    "message": "Missing or invalid authentication token"
  }
}
```

Failed authentication attempts are logged at WARNING level with client IP, endpoint, and HTTP method.

---

## Tools

### `ingest_document`

Ingests a document into the vector store. Processes the file through classification, text extraction, chunking, embedding, and ChromaDB storage.

#### Input Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file_path` | string | Yes | Absolute path to the document file on disk |
| `application_ref` | string | Yes | Planning application reference (e.g. `"25/01178/REM"`) |
| `document_type` | string | No | Document type override (e.g. `"transport_assessment"`). Auto-classified from filename/content if omitted. |

#### Output: `success`

Returned when the document is extracted, chunked, embedded, and stored successfully.

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

#### Output: `already_ingested`

Returned when a document with an identical SHA-256 hash has already been ingested for this application reference.

```json
{
  "status": "already_ingested",
  "document_id": "25_01178_REM_a1b2c3",
  "message": "Document has already been ingested with the same content"
}
```

#### Output: `skipped`

Returned when the document is classified as image-based (architectural drawings, renderings, etc.) and would not produce useful text.

```json
{
  "status": "skipped",
  "reason": "image_based",
  "image_ratio": 0.891,
  "total_pages": 4
}
```

#### Output: `error`

Returned when ingestion fails at any stage.

```json
{
  "status": "error",
  "error_type": "extraction_failed",
  "message": "Human-readable error description"
}
```

`error_type` is one of: `file_not_found`, `unsupported_file_type`, `extraction_failed`, `no_content`, `no_chunks`.

#### Key Behaviour

- Validates the file exists and has a supported extension (`.pdf`, `.png`, `.jpg`, `.jpeg`, `.tiff`, `.tif`).
- Computes a SHA-256 hash of the file contents. The `document_id` is `{sanitized_ref}_{hash_prefix_6}`.
- Checks the document registry before processing; identical content returns `"already_ingested"` immediately (idempotent).
- Detects image-heavy documents before extraction. If the average image-to-page-area ratio exceeds the threshold (default 0.7), the document is skipped.
- Filenames matching architectural rendering patterns (bird's eye, perspective, CGI, 3D visual, artist's impression, photomontage, street scene, render) bypass OCR entirely.
- When `document_type` is omitted, auto-classifies by filename pattern matching first, then content keyword analysis, with fallback to `"other"`.

---

### `search_application_docs`

Performs semantic search across ingested document chunks. Embeds the query and returns the nearest neighbours from ChromaDB, optionally filtered by application and/or document types.

#### Input Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | Yes | -- | Natural language search query |
| `application_ref` | string | No | `null` | Filter results to a specific application |
| `document_types` | string[] | No | `null` | Filter by document types (ChromaDB `$in` operator) |
| `max_results` | int | No | `10` | Maximum number of results to return |

#### Output: `success`

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

#### Key Behaviour

- Embeds the query using the same all-MiniLM-L6-v2 model used for document embeddings.
- When both `application_ref` and `document_types` are provided, the filters are combined with `$and`.
- ChromaDB L2 distances are converted to relevance scores via `max(0, 1 - (distance / 2))`.
- Results are ordered by relevance score, highest first.

---

### `get_document_text`

Retrieves the full text of an ingested document by fetching all its chunks from ChromaDB and reassembling them in order.

#### Input Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `document_id` | string | Yes | Document ID (e.g. `"25_01178_REM_a1b2c3"`) |

#### Output: `success`

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

#### Output: `error`

```json
{
  "status": "error",
  "error_type": "document_not_found",
  "message": "Document not found: 25_01178_REM_a1b2c3"
}
```

`error_type` is one of: `document_not_found`, `no_chunks`.

#### Key Behaviour

- Looks up the document in the `document_registry` collection first. Returns `"document_not_found"` if absent.
- Fetches all chunks from `application_docs` where `metadata.document_id` matches, sorted by `chunk_index`.
- Concatenates chunk texts with `"\n\n"` separator to reconstruct the full document.

---

### `list_ingested_documents`

Lists all documents that have been ingested for a given planning application reference.

#### Input Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `application_ref` | string | Yes | Application reference to list documents for (e.g. `"25/01178/REM"`) |

#### Output: `success`

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

#### Key Behaviour

- Queries the `document_registry` collection where `metadata.application_ref` matches the input.
- Returns an empty `documents` array with `document_count: 0` when no documents exist for the application (not an error).

---

## Processing Pipeline

The ingestion pipeline transforms raw PDF files into searchable vector embeddings stored in ChromaDB. The pipeline has five stages: image ratio detection, text extraction, document type classification, text chunking, and embedding generation.

### 1. Image Ratio Detection

Before any text extraction, the pipeline computes the ratio of image area to page area for every page in the document. If the average ratio across all pages exceeds the threshold (default **0.7**, configurable via `IMAGE_RATIO_THRESHOLD`), the document is classified as image-based and skipped entirely. This prevents wasting compute on architectural drawings, site photographs, and 3D renderings that contain no useful text.

Filenames matching rendering patterns (bird's eye, perspective, CGI, 3D visual, artist's impression, photomontage, street scene, render) also bypass OCR.

### 2. Text Extraction

**Primary method -- PyMuPDF text layer:**
Text is extracted using PyMuPDF (`fitz`) via `page.get_text("text")`. This is fast and reliable for digitally-authored PDFs.

**Fallback -- Tesseract OCR:**
When a page yields fewer than **10 characters** and OCR is enabled (`ENABLE_OCR=true`), the page is rendered to a **300 DPI** pixmap and processed with `pytesseract.image_to_string()`. OCR confidence scores are captured via `pytesseract.image_to_data()` and pages with average confidence below 70% are logged at WARNING level.

**Extraction method tracking:**
Each page records its method as `"text_layer"` or `"ocr"`. The document-level method is `"text_layer"` if all pages used text extraction, `"ocr"` if all used OCR, or `"mixed"` when both methods were used across pages.

**Supported file types:**
`.pdf`, `.png`, `.jpg`, `.jpeg`, `.tiff`, `.tif`, `.bmp`. Non-PDF image files are processed entirely via OCR as single-page documents.

### 3. Text Chunking

Extracted text is split into chunks suitable for embedding using `RecursiveCharacterTextSplitter` from LangChain.

| Setting | Value |
|---------|-------|
| Chunk size | 200 tokens (~800 characters at 4 chars/token) |
| Chunk overlap | 50 tokens (~200 characters) |
| Keep separator | Yes (attached to preceding chunk) |

**Separator hierarchy** (tried in order, splitting on the first match):

1. `"\n\n"` -- paragraph break
2. `"\n"` -- line break
3. `". "` -- sentence end
4. `"! "` -- exclamation
5. `"? "` -- question
6. `"; "` -- clause
7. `", "` -- phrase
8. `" "` -- word
9. `""` -- character (last resort)

**Page number tracking:**
For multi-page documents, a character-position-to-page-number mapping is built during extraction so each chunk records which page(s) it spans. Page numbers are stored as a comma-separated string in metadata (e.g. `"14,15"`) because ChromaDB does not support list metadata values.

### 4. Embedding Generation

| Setting | Value |
|---------|-------|
| Model | `sentence-transformers/all-MiniLM-L6-v2` |
| Dimensions | 384 |
| Max sequence length | 256 tokens |
| Truncation threshold | 1024 characters (256 tokens x 4 chars/token) |
| Batch size | 32 (default) |

- The model is **lazy-loaded** on first use, not at server startup.
- Text exceeding 1024 characters is truncated with a warning log.
- Empty or whitespace-only input raises `ValueError`.
- `embed_batch()` processes multiple texts in a single call for efficiency.

### 5. ChromaDB Storage

The server uses `chromadb.PersistentClient` with `anonymized_telemetry=False`. Persistence directory defaults to `/data/chroma` (configurable via `CHROMA_PERSIST_DIR`). Falls back to an in-memory client when no directory is specified.

**Collections:**

| Collection | Purpose | Embeddings |
|------------|---------|------------|
| `application_docs` | Document chunks with real embeddings | 384-dim vectors from all-MiniLM-L6-v2 |
| `document_registry` | Document-level metadata for idempotency and listing | Placeholder `[0.0] * 384` (not used for search) |

**ID formats:**

| Entity | Format | Example |
|--------|--------|---------|
| Document ID | `{sanitized_ref}_{hash_prefix_6}` | `25_01178_REM_a1b2c3` |
| Chunk ID | `{sanitized_ref}_{hash_prefix_6}_{page:03d}_{chunk:03d}` | `25_01178_REM_a1b2c3_014_042` |

**File hashing:** SHA-256, read in 8192-byte blocks.

**Chunk metadata fields** (stored in `application_docs`):

| Field | Type | Description |
|-------|------|-------------|
| `application_ref` | string | Original application reference (e.g. `"25/01178/REM"`) |
| `document_id` | string | Parent document ID |
| `source_file` | string | Original filename |
| `document_type` | string | Classified document type |
| `page_numbers` | string | Comma-separated page numbers this chunk spans |
| `chunk_index` | int | Zero-based chunk position within the document |
| `total_chunks` | int | Total chunks in the document |
| `extraction_method` | string | `"text_layer"`, `"ocr"`, or `"mixed"` |
| `char_count` | int | Character count of the chunk text |
| `word_count` | int | Word count of the chunk text |

**Document registry metadata fields** (stored in `document_registry`):

| Field | Type | Description |
|-------|------|-------------|
| `file_path` | string | Absolute path to the source file |
| `file_hash` | string | SHA-256 hash of the file |
| `application_ref` | string | Application reference |
| `document_type` | string | Classified document type |
| `chunk_count` | int | Number of chunks created |
| `ingested_at` | string | ISO 8601 timestamp |
| `extraction_method` | string | `"text_layer"`, `"ocr"`, or `"mixed"` |
| `contains_drawings` | bool | Whether drawings were detected |

**Deletion:** `delete_document()` removes all chunks from `application_docs` and the registry entry from `document_registry`.

---

## Document Type Classification

The `DocumentClassifier` assigns a type to each document using a three-tier strategy: filename pattern matching (high confidence), content keyword analysis (medium confidence), then fallback to `"other"` (low confidence).

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

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DOCUMENT_STORE_PORT` | `3002` | Server listen port |
| `CHROMA_PERSIST_DIR` | `/data/chroma` | ChromaDB persistence directory. Unset for in-memory mode. |
| `ENABLE_OCR` | `true` | Enable Tesseract OCR fallback for scanned pages |
| `MCP_API_KEY` | (unset) | Bearer token for authentication. Unset or empty disables auth. |
| `IMAGE_RATIO_THRESHOLD` | `0.7` | Average image-to-page-area ratio above which a document is skipped as image-based |

---

## Error Codes

### Tool-Level Errors

These are returned as `status: "error"` in tool responses (not HTTP errors).

| `error_type` | Tool | Description |
|--------------|------|-------------|
| `file_not_found` | `ingest_document` | The specified file path does not exist |
| `unsupported_file_type` | `ingest_document` | File extension is not one of the supported types |
| `extraction_failed` | `ingest_document` | PyMuPDF and OCR both failed to extract text |
| `no_content` | `ingest_document` | Extraction produced zero characters of text |
| `no_chunks` | `ingest_document` | Text was extracted but chunking produced zero chunks |
| `document_not_found` | `get_document_text` | No document with this ID exists in the registry |
| `no_chunks` | `get_document_text` | Document exists in the registry but has no chunks in `application_docs` |

### HTTP-Level Errors

These are returned by the transport/auth middleware before tool dispatch.

| Status | Code | Description |
|--------|------|-------------|
| 401 | `unauthorized` | Missing, malformed, or invalid Bearer token |
