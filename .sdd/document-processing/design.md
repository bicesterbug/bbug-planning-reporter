# Design: Document Processing Pipeline

**Version:** 1.0
**Date:** 2026-02-06
**Status:** Draft
**Linked Specification:** `.sdd/document-processing/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

This feature builds upon the foundation-api phase. The API Gateway, Worker, Redis infrastructure, and Cherwell Scraper MCP already exist. Documents are downloaded to `/data/raw/{reference}/` by the scraper. This phase adds document processing capabilities to transform raw PDFs into searchable vector embeddings.

### Proposed Architecture

The document processing pipeline adds three new components to the existing stack:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Docker Compose Stack                                  │
│                                                                              │
│  ┌─────────────────────┐      ┌─────────────────────┐                       │
│  │   API Gateway       │      │      Redis          │                       │
│  │   (existing)        │◄────►│   (existing)        │                       │
│  └─────────┬───────────┘      └─────────────────────┘                       │
│            │                                                                 │
│            │ enqueue                                                         │
│            ▼                                                                 │
│  ┌─────────────────────────────────────────────────────────────┐            │
│  │    Worker (existing)                                         │            │
│  │    - Orchestrates document ingestion after download          │            │
│  │    - Publishes progress events                               │            │
│  └─────────────────────────┬────────────────────────────────────┘            │
│                            │                                                 │
│          ┌─────────────────┼─────────────────┐                              │
│          │ MCP calls       │ MCP calls       │                              │
│          ▼                 ▼                 │                              │
│  ┌─────────────────┐ ┌─────────────────────┐ │                              │
│  │ Cherwell Scraper│ │ Document Store MCP  │ │                              │
│  │ (existing)      │ │ :3002               │ │                              │
│  │                 │ │                     │ │                              │
│  │ download_*      │ │ ingest_document     │ │                              │
│  │                 │ │ search_*            │ │                              │
│  │                 │ │ get_document_text   │ │                              │
│  │                 │ │ list_ingested_*     │ │                              │
│  └────────┬────────┘ └──────────┬──────────┘ │                              │
│           │                     │            │                              │
│           ▼                     ▼            │                              │
│  ┌─────────────────┐ ┌─────────────────────┐ │                              │
│  │ /data/raw/      │ │ ChromaDB            │ │                              │
│  │ (downloaded     │ │ /data/chroma        │ │                              │
│  │  documents)     │ │                     │ │                              │
│  │                 │ │ Collection:         │ │                              │
│  │                 │ │ application_docs    │ │                              │
│  └─────────────────┘ └─────────────────────┘ │                              │
│                                              │                              │
└──────────────────────────────────────────────┴──────────────────────────────┘
```

**Document Processing Pipeline Flow:**

```
Raw PDF/Image (from /data/raw/{ref}/)
        │
        ▼
┌───────────────────────────────────────┐
│  DocumentProcessor                     │
│  ├── PDF with text layer → PyMuPDF    │
│  ├── Scanned PDF/Image → Tesseract    │
│  └── Detect image-heavy pages         │
└───────────────┬───────────────────────┘
                │
                ▼
┌───────────────────────────────────────┐
│  Text Cleaning & Normalisation        │
│  - Remove excessive whitespace        │
│  - Fix encoding issues                │
│  - Preserve structure markers         │
└───────────────┬───────────────────────┘
                │
                ▼
┌───────────────────────────────────────┐
│  Chunking (RecursiveCharacterSplitter)│
│  - chunk_size: 1000 tokens            │
│  - chunk_overlap: 200 tokens          │
│  - Respect paragraph boundaries       │
└───────────────┬───────────────────────┘
                │
                ▼
┌───────────────────────────────────────┐
│  EmbeddingService                     │
│  - all-MiniLM-L6-v2 (384-dim)         │
│  - Batch processing for efficiency    │
└───────────────┬───────────────────────┘
                │
                ▼
┌───────────────────────────────────────┐
│  ChromaClient                          │
│  - Store in application_docs collection│
│  - Include metadata (ref, type, page) │
│  - Idempotent upsert by chunk ID      │
└───────────────────────────────────────┘
```

### Data Pipeline Design

**Input Stage:**
- PDFs and images from `/data/raw/{application_ref}/`
- Metadata from Cherwell scraper (document type hints from filename/portal)

**Extraction Stage:**
- Primary: PyMuPDF for text-layer extraction (fast, high quality)
- Fallback: Tesseract OCR for scanned documents
- Detection: Identify image-heavy pages (drawings, site plans) via image-to-text ratio

**Transformation Stage:**
- Text cleaning: normalise whitespace, fix encoding, remove headers/footers
- Chunking: recursive character splitting respecting paragraph boundaries
- Token estimation: approximate using character count / 4

**Loading Stage:**
- Embedding generation: batch processing for efficiency
- ChromaDB storage: idempotent upsert using deterministic chunk IDs
- Metadata enrichment: application_ref, document_type, page_number, chunk_index

### Data Quality Considerations

**OCR Confidence Tracking:**
- Tesseract provides per-character confidence scores
- Store average confidence in chunk metadata
- Log warning when confidence < 70%
- Flag low-confidence documents for potential human review

**Text Extraction Accuracy:**
- Detect extraction failures (empty text from non-empty PDF)
- Track pages with <10 characters extracted (likely images/drawings)
- Store `extraction_method` in metadata (text_layer, ocr, mixed)

**Schema Design for ChromaDB:**

```python
# Chunk document schema
{
    "id": "{ref}_{filename_hash}_{page}_{chunk_idx}",  # Deterministic for idempotency
    "embedding": [0.123, ...],  # 384 dimensions
    "document": "The proposed development will generate...",  # Chunk text
    "metadata": {
        "application_ref": "25/01178/REM",
        "source_file": "Transport_Assessment_v2.pdf",
        "source_file_hash": "sha256:abc123...",  # For re-ingestion detection
        "document_type": "transport_assessment",
        "page_number": 14,
        "chunk_index": 42,
        "total_chunks": 156,
        "extraction_method": "text_layer",  # text_layer | ocr | mixed
        "ocr_confidence": 0.95,  # Only present if OCR used
        "contains_drawings": false,
        "ingested_at": "2025-02-05T10:30:00Z",
        "char_count": 847,
        "word_count": 142
    }
}
```

### Idempotency for Re-ingestion

**Chunk ID Strategy:**
- Format: `{sanitized_ref}_{file_hash_short}_{page}_{chunk_idx}`
- Example: `25_01178_REM_a1b2c3_014_042`
- Deterministic: same file produces same IDs
- File hash ensures re-ingestion of updated documents replaces old chunks

**Re-ingestion Workflow:**
1. Calculate file hash before processing
2. Query existing chunks for this file hash
3. If hash matches and chunks exist, skip (idempotent)
4. If hash differs, delete old chunks for this file, then ingest new
5. If no existing chunks, proceed with full ingestion

**Document-level Tracking:**
- Store document metadata in separate ChromaDB metadata or Redis
- Track: file_path, file_hash, chunk_count, ingested_at, status
- Enables `list_ingested_documents` without scanning all chunks

### Technology Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| PDF Extraction | PyMuPDF (fitz) | Fast, handles complex layouts, extracts text layer efficiently |
| OCR Engine | Tesseract 5 | Free, good accuracy, provides confidence scores |
| Chunking | LangChain RecursiveCharacterTextSplitter | Respects boundaries, configurable, well-tested |
| Embeddings | sentence-transformers/all-MiniLM-L6-v2 | 384-dim, fast CPU inference, good retrieval quality |
| Vector Store | ChromaDB (persistent) | Local, no server needed, Python-native, free |
| Image Detection | PyMuPDF image extraction + ratio analysis | Built into existing library |

### Quality Attributes

**Reliability:**
- Graceful degradation: failure in one document does not block others
- Retry logic for transient OCR failures
- Comprehensive logging for debugging

**Performance:**
- Batch embedding generation (up to 32 chunks at once)
- Async I/O for file operations
- Connection pooling for ChromaDB

**Maintainability:**
- Clear separation: Processor, Embedder, ChromaClient, MCP Server
- Structured logging with document context
- Type hints throughout

---

## API Design

### MCP Tools Interface

The Document Store MCP server exposes four tools for document management and search:

```
document-store-mcp
├── ingest_document      # Process and store a document
├── search_application_docs  # Semantic search with optional filters
├── get_document_text    # Retrieve full text of a document
└── list_ingested_documents  # List documents for an application
```

### Tool Contracts

**ingest_document**

Ingests a document through the full pipeline: extraction, chunking, embedding, storage.

Input:
- `file_path` (required): Absolute path to the document file
- `application_ref` (required): Planning application reference (e.g., "25/01178/REM")
- `document_type` (required): Classification (e.g., "transport_assessment")
- `metadata` (optional): Additional metadata to store with chunks

Output (success):
```json
{
    "status": "success",
    "document_id": "25_01178_REM_a1b2c3",
    "file_path": "/data/raw/25_01178_REM/Transport_Assessment_v2.pdf",
    "chunks_created": 47,
    "extraction_method": "text_layer",
    "contains_drawings": false,
    "processing_time_seconds": 12.4
}
```

Output (already ingested - idempotent):
```json
{
    "status": "already_ingested",
    "document_id": "25_01178_REM_a1b2c3",
    "chunks_existing": 47,
    "message": "Document already ingested with matching hash"
}
```

Errors:
- `file_not_found`: File does not exist at specified path
- `unsupported_file_type`: Not a PDF or supported image format
- `extraction_failed`: Could not extract any text from document
- `embedding_failed`: Embedding generation failed

**search_application_docs**

Performs semantic search across ingested documents with optional filtering.

Input:
- `query` (required): Natural language search query
- `application_ref` (optional): Filter to specific application
- `document_types` (optional): Filter to specific document types (array)
- `n_results` (optional): Number of results to return (default: 10, max: 50)

Output:
```json
{
    "results": [
        {
            "chunk_id": "25_01178_REM_a1b2c3_014_042",
            "text": "The proposed development will generate approximately 150...",
            "relevance_score": 0.87,
            "metadata": {
                "application_ref": "25/01178/REM",
                "source_file": "Transport_Assessment_v2.pdf",
                "document_type": "transport_assessment",
                "page_number": 14
            }
        }
    ],
    "total_results": 1,
    "query": "trip generation cycling"
}
```

Errors:
- `invalid_application_ref`: Reference format invalid
- `no_documents_found`: No documents ingested for this application

**get_document_text**

Retrieves the full concatenated text of an ingested document.

Input:
- `document_id` (required): Document identifier from ingestion

Output:
```json
{
    "document_id": "25_01178_REM_a1b2c3",
    "source_file": "Transport_Assessment_v2.pdf",
    "application_ref": "25/01178/REM",
    "document_type": "transport_assessment",
    "total_chunks": 47,
    "text": "Full concatenated text of all chunks...",
    "char_count": 45230,
    "word_count": 7620
}
```

Errors:
- `document_not_found`: No document with this ID exists

**list_ingested_documents**

Lists all documents ingested for an application.

Input:
- `application_ref` (required): Planning application reference

Output:
```json
{
    "application_ref": "25/01178/REM",
    "documents": [
        {
            "document_id": "25_01178_REM_a1b2c3",
            "source_file": "Transport_Assessment_v2.pdf",
            "document_type": "transport_assessment",
            "chunks": 47,
            "ingested_at": "2025-02-05T10:30:00Z",
            "extraction_method": "text_layer",
            "contains_drawings": false
        },
        {
            "document_id": "25_01178_REM_d4e5f6",
            "source_file": "Site_Plan.pdf",
            "document_type": "site_plan",
            "chunks": 3,
            "ingested_at": "2025-02-05T10:31:00Z",
            "extraction_method": "ocr",
            "contains_drawings": true
        }
    ],
    "total_documents": 2,
    "total_chunks": 50
}
```

Errors:
- `invalid_application_ref`: Reference format invalid

---

## Added Components

### DocumentProcessor

**Description:** Core document processing engine that handles text extraction from PDFs and images. Detects whether to use text-layer extraction or OCR, handles mixed documents, and identifies image-heavy pages (drawings).

**Users:** DocumentStoreMCP (via ingest_document tool)

**Kind:** Class

**Location:** `src/mcp_servers/document_store/processor.py`

**Requirements References:**
- [document-processing:FR-001]: PDF text extraction via PyMuPDF
- [document-processing:FR-002]: OCR via Tesseract for scanned documents
- [document-processing:FR-012]: Detection of image-heavy pages

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Extract text from PDF with text layer | PDF with embedded text | Call extract_text() | Returns extracted text with page boundaries |
| TS-02 | Extract text from scanned PDF | Scanned PDF without text layer | Call extract_text() | Falls back to OCR, returns text with confidence |
| TS-03 | Handle mixed PDF | PDF with some text pages, some scanned | Call extract_text() | Uses appropriate method per page, returns combined text |
| TS-04 | Detect image-heavy page | PDF page that is primarily a drawing | Call extract_text() | Sets contains_drawings=true in metadata |
| TS-05 | Handle empty PDF | Valid PDF with no extractable content | Call extract_text() | Returns empty text with appropriate metadata |
| TS-06 | Handle corrupt PDF | Malformed PDF file | Call extract_text() | Raises ExtractionError with descriptive message |
| TS-07 | Extract from image file | JPEG/PNG image of document | Call extract_text() | Uses OCR, returns text with confidence |
| TS-08 | Report OCR confidence | Scanned document with varying quality | Call extract_text() | Returns confidence scores in metadata |

### TextChunker

**Description:** Splits extracted text into chunks suitable for embedding. Uses recursive character splitting with configurable size and overlap, attempting to respect paragraph and sentence boundaries.

**Users:** DocumentProcessor

**Kind:** Class

**Location:** `src/mcp_servers/document_store/chunker.py`

**Requirements References:**
- [document-processing:FR-003]: Chunk text with configurable size (1000 tokens) and overlap (200 tokens)

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Chunk long document | Document with 5000 words | Call chunk_text() | Returns ~5 chunks with overlap |
| TS-02 | Preserve paragraph boundaries | Text with clear paragraphs | Call chunk_text() | Chunks break at paragraph boundaries when possible |
| TS-03 | Handle short document | Document shorter than chunk_size | Call chunk_text() | Returns single chunk |
| TS-04 | Maintain context with overlap | Multi-chunk document | Call chunk_text() | Each chunk overlaps with previous by ~200 tokens |
| TS-05 | Handle no natural boundaries | Dense text without paragraphs | Call chunk_text() | Falls back to sentence/word boundaries |
| TS-06 | Include page context | Multi-page text | Call chunk_text() with page_numbers | Each chunk includes source page in metadata |

### EmbeddingService

**Description:** Generates vector embeddings for text chunks using the sentence-transformers library. Handles batch processing for efficiency and provides consistent, reproducible embeddings.

**Users:** DocumentStoreMCP (via ingest_document tool)

**Kind:** Class

**Location:** `src/mcp_servers/document_store/embeddings.py`

**Requirements References:**
- [document-processing:FR-004]: Generate 384-dim embeddings with all-MiniLM-L6-v2
- [document-processing:NFR-003]: Embedding consistency (same input = same output)

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Generate single embedding | Text chunk | Call embed() | Returns 384-dimensional vector |
| TS-02 | Batch embedding | List of 32 chunks | Call embed_batch() | Returns list of 32 embeddings efficiently |
| TS-03 | Embedding consistency | Same text twice | Call embed() twice | Produces identical vectors |
| TS-04 | Handle empty text | Empty string | Call embed() | Raises ValueError or returns zero vector |
| TS-05 | Handle very long text | Text exceeding model max length | Call embed() | Truncates appropriately, logs warning |
| TS-06 | Model loading | First call to service | Call embed() | Model loaded lazily, subsequent calls fast |

### ChromaClient

**Description:** Client for ChromaDB operations on the application_docs collection. Handles connection management, idempotent upserts, semantic search, and document metadata queries.

**Users:** DocumentStoreMCP

**Kind:** Class

**Location:** `src/mcp_servers/document_store/chroma_client.py`

**Requirements References:**
- [document-processing:FR-005]: Store chunks with metadata in application_docs collection
- [document-processing:FR-007]: Semantic search with optional filtering
- [document-processing:NFR-004]: Storage efficiency
- [document-processing:NFR-005]: Search latency <500ms

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Store chunk with metadata | Chunk text, embedding, metadata | Call upsert_chunk() | Chunk stored, retrievable by ID |
| TS-02 | Idempotent upsert | Same chunk ID twice | Call upsert_chunk() twice | No duplicate, same data stored |
| TS-03 | Semantic search | Query and collection with docs | Call search() | Returns ranked results with scores |
| TS-04 | Search with filter | Query with application_ref filter | Call search() | Returns only matching application docs |
| TS-05 | Search empty collection | Query on empty collection | Call search() | Returns empty results, no error |
| TS-06 | Delete chunks by document | Document ID | Call delete_document() | All chunks for document removed |
| TS-07 | Get chunks by document | Document ID with 10 chunks | Call get_document_chunks() | Returns all 10 chunks in order |
| TS-08 | Search performance | Collection with 10000 chunks | Call search() | Returns in <500ms |
| TS-09 | Collection initialization | Fresh ChromaDB | Access collection | Creates application_docs if not exists |

### DocumentStoreMCP

**Description:** MCP server exposing document processing tools. Orchestrates the pipeline components and provides the tool interface for the worker agent.

**Users:** Worker (via MCP protocol)

**Kind:** Module (MCP Server)

**Location:** `src/mcp_servers/document_store/server.py`

**Requirements References:**
- [document-processing:FR-006]: ingest_document tool
- [document-processing:FR-007]: search_application_docs tool
- [document-processing:FR-008]: get_document_text tool
- [document-processing:FR-009]: list_ingested_documents tool
- [document-processing:FR-011]: Progress reporting during ingestion

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Ingest valid PDF | PDF file path | Call ingest_document | Returns success with chunk count |
| TS-02 | Ingest already processed | Same file ingested before | Call ingest_document | Returns already_ingested status |
| TS-03 | Ingest invalid file type | .docx file path | Call ingest_document | Returns unsupported_file_type error |
| TS-04 | Ingest non-existent file | Invalid path | Call ingest_document | Returns file_not_found error |
| TS-05 | Search with results | Query matching content | Call search_application_docs | Returns ranked results |
| TS-06 | Search with no results | Query not matching | Call search_application_docs | Returns empty results array |
| TS-07 | Search with filter | Query + application_ref | Call search_application_docs | Returns filtered results |
| TS-08 | Get document text | Valid document_id | Call get_document_text | Returns concatenated text |
| TS-09 | Get non-existent document | Invalid document_id | Call get_document_text | Returns document_not_found error |
| TS-10 | List ingested documents | Application with 5 docs | Call list_ingested_documents | Returns list of 5 documents |
| TS-11 | List for empty application | Application with no docs | Call list_ingested_documents | Returns empty list |
| TS-12 | MCP server initialization | Server startup | Start server | Registers all 4 tools, connects to ChromaDB |

### DocumentClassifier

**Description:** Heuristic classifier that determines document type based on filename patterns and content analysis. Falls back to 'other' for unrecognised documents.

**Users:** DocumentStoreMCP (can auto-classify during ingestion)

**Kind:** Class

**Location:** `src/mcp_servers/document_store/classifier.py`

**Requirements References:**
- [document-processing:FR-010]: Classify documents based on filename and content

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Classify by filename | "Transport_Assessment_v2.pdf" | Call classify() | Returns "transport_assessment" |
| TS-02 | Classify design statement | "Design_Access_Statement.pdf" | Call classify() | Returns "design_access_statement" |
| TS-03 | Classify site plan | "Site_Plan_Drawing.pdf" | Call classify() | Returns "site_plan" |
| TS-04 | Classify by content | Generic filename, contains "trip generation" | Call classify() with text | Returns "transport_assessment" |
| TS-05 | Fallback to other | Unrecognised filename and content | Call classify() | Returns "other" |
| TS-06 | Case insensitive | "TRANSPORT_assessment.PDF" | Call classify() | Returns "transport_assessment" |

### DocumentTracker

**Description:** Tracks document-level metadata for efficient listing and re-ingestion detection. Provides a registry of ingested documents separate from chunk storage.

**Users:** DocumentStoreMCP, ChromaClient

**Kind:** Class

**Location:** `src/mcp_servers/document_store/tracker.py`

**Requirements References:**
- [document-processing:FR-005]: Track ingestion metadata
- [document-processing:FR-009]: Support list_ingested_documents efficiently

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Register document | New document ingested | Call register() | Document metadata stored |
| TS-02 | Check if ingested | Document previously ingested | Call is_ingested() with file_hash | Returns True |
| TS-03 | Check if not ingested | New document | Call is_ingested() | Returns False |
| TS-04 | List by application | 3 docs for application | Call list_by_application() | Returns 3 document records |
| TS-05 | Update on re-ingestion | Document with new hash | Call register() | Updates record, increments version |
| TS-06 | Get document metadata | Valid document_id | Call get() | Returns full document record |

---

## Used Components

### ChromaDB (External)

**Location:** Python package + persistent storage at `/data/chroma`

**Provides:** Vector storage, semantic search, metadata filtering

**Used By:** ChromaClient

### sentence-transformers (Library)

**Location:** Python package

**Provides:** Pre-trained embedding models, efficient inference

**Used By:** EmbeddingService

### PyMuPDF / fitz (Library)

**Location:** Python package

**Provides:** PDF text extraction, image detection, page-level processing

**Used By:** DocumentProcessor

### Tesseract (External)

**Location:** System package (apt: tesseract-ocr)

**Provides:** OCR for scanned documents

**Used By:** DocumentProcessor (via pytesseract)

### LangChain (Library)

**Location:** Python package (langchain-text-splitters)

**Provides:** RecursiveCharacterTextSplitter for chunking

**Used By:** TextChunker

### Worker (Existing)

**Location:** `src/worker/`

**Provides:** Job orchestration, progress publishing

**Uses:** DocumentStoreMCP (via MCP protocol)

### Redis (Existing)

**Location:** Docker container

**Provides:** State storage, pub/sub for progress events

**Used By:** Worker, DocumentTracker (for document registry)

---

## Integration Test Scenarios

| ID | Scenario | Given | When | Then | Components Involved |
|----|----------|-------|------|------|---------------------|
| ITS-01 | Full ingestion pipeline | PDF in /data/raw/ | Call ingest_document via MCP | Text extracted, chunked, embedded, stored in ChromaDB | DocumentStoreMCP, DocumentProcessor, TextChunker, EmbeddingService, ChromaClient |
| ITS-02 | Search after ingestion | Documents ingested | Call search_application_docs | Returns relevant chunks with scores | DocumentStoreMCP, ChromaClient, EmbeddingService |
| ITS-03 | Worker ingestion flow | Downloaded documents | Worker calls ingest for each doc | Progress events published, all docs ingested | Worker, DocumentStoreMCP, Redis |
| ITS-04 | Re-ingestion with same file | Document already ingested | Call ingest_document again | Returns already_ingested, no duplicate chunks | DocumentStoreMCP, DocumentTracker, ChromaClient |
| ITS-05 | Re-ingestion with changed file | New version of document | Call ingest_document | Old chunks deleted, new chunks stored | DocumentStoreMCP, ChromaClient, DocumentTracker |
| ITS-06 | OCR fallback | Scanned PDF | Call ingest_document | OCR triggered, text extracted with confidence | DocumentStoreMCP, DocumentProcessor |
| ITS-07 | Graceful degradation | Corrupt PDF in batch | Worker ingests multiple docs | Corrupt doc fails, others succeed, job continues | Worker, DocumentStoreMCP |
| ITS-08 | Filter search by application | Docs from 2 applications | Search with application_ref filter | Returns only matching application | DocumentStoreMCP, ChromaClient |

---

## E2E Test Scenarios

| ID | Scenario | Given | When | Then | User Journey |
|----|----------|-------|------|------|--------------|
| E2E-01 | Document ingestion and search | Documents downloaded by scraper | Worker ingests, then agent searches | Agent finds relevant content for review | Download -> Ingest -> Search -> Review |
| E2E-02 | Progress reporting during ingestion | 10 documents to ingest | Worker processes batch | Webhooks report "Ingested 5 of 10 documents" | Submit review -> Receive progress webhooks |
| E2E-03 | Mixed document types | PDFs with text + scanned + drawings | Ingest all documents | Each processed appropriately, drawings flagged | Full application document set processed |

---

## Task Breakdown

> **CRITICAL: Tests are written WITH implementation, not after.**

### Phase 1: Core Extraction & Chunking

- Task 1: Implement DocumentProcessor with PyMuPDF text extraction
  - Status: Backlog
  - PDF text-layer extraction with page boundaries
  - Empty page detection, basic error handling
  - Requirements: [document-processing:FR-001]
  - Test Scenarios: [document-processing:DocumentProcessor/TS-01], [document-processing:DocumentProcessor/TS-05], [document-processing:DocumentProcessor/TS-06]

- Task 2: Add OCR fallback to DocumentProcessor
  - Status: Complete
  - Tesseract integration via pytesseract
  - Confidence score tracking, mixed document handling
  - Requirements: [document-processing:FR-002], [document-processing:NFR-002]
  - Test Scenarios: [document-processing:DocumentProcessor/TS-02], [document-processing:DocumentProcessor/TS-03], [document-processing:DocumentProcessor/TS-07], [document-processing:DocumentProcessor/TS-08]

- Task 3: Implement image-heavy page detection
  - Status: Complete
  - Detect drawings/site plans via image-to-text ratio
  - Set contains_drawings flag in metadata
  - Requirements: [document-processing:FR-012]
  - Test Scenarios: [document-processing:DocumentProcessor/TS-04]

- Task 4: Implement TextChunker with configurable parameters
  - Status: Backlog
  - RecursiveCharacterTextSplitter wrapper
  - Configurable chunk_size (1000), overlap (200)
  - Page number tracking through chunks
  - Requirements: [document-processing:FR-003]
  - Test Scenarios: [document-processing:TextChunker/TS-01], [document-processing:TextChunker/TS-02], [document-processing:TextChunker/TS-03], [document-processing:TextChunker/TS-04], [document-processing:TextChunker/TS-05], [document-processing:TextChunker/TS-06]

### Phase 2: Embedding & Storage

- Task 5: Implement EmbeddingService with sentence-transformers
  - Status: Backlog
  - Load all-MiniLM-L6-v2 model
  - Single and batch embedding generation
  - Lazy model loading for startup performance
  - Requirements: [document-processing:FR-004], [document-processing:NFR-003]
  - Test Scenarios: [document-processing:EmbeddingService/TS-01], [document-processing:EmbeddingService/TS-02], [document-processing:EmbeddingService/TS-03], [document-processing:EmbeddingService/TS-04], [document-processing:EmbeddingService/TS-05], [document-processing:EmbeddingService/TS-06]

- Task 6: Implement ChromaClient with idempotent upsert
  - Status: Backlog
  - Collection initialization, connection management
  - Deterministic chunk ID generation
  - Upsert, delete, get operations
  - Requirements: [document-processing:FR-005], [document-processing:NFR-004]
  - Test Scenarios: [document-processing:ChromaClient/TS-01], [document-processing:ChromaClient/TS-02], [document-processing:ChromaClient/TS-06], [document-processing:ChromaClient/TS-07], [document-processing:ChromaClient/TS-09]

- Task 7: Implement semantic search in ChromaClient
  - Status: Backlog
  - Query with embedding, metadata filtering
  - Score normalization, result formatting
  - Performance optimization for <500ms
  - Requirements: [document-processing:FR-007], [document-processing:NFR-005]
  - Test Scenarios: [document-processing:ChromaClient/TS-03], [document-processing:ChromaClient/TS-04], [document-processing:ChromaClient/TS-05], [document-processing:ChromaClient/TS-08]

- Task 8: Implement DocumentTracker for ingestion registry
  - Status: Backlog
  - Store document metadata in ChromaDB metadata collection
  - File hash tracking for re-ingestion detection
  - Efficient listing by application
  - Requirements: [document-processing:FR-005], [document-processing:FR-009]
  - Test Scenarios: [document-processing:DocumentTracker/TS-01], [document-processing:DocumentTracker/TS-02], [document-processing:DocumentTracker/TS-03], [document-processing:DocumentTracker/TS-04], [document-processing:DocumentTracker/TS-05], [document-processing:DocumentTracker/TS-06]

### Phase 3: MCP Server & Tools

- Task 9: Implement DocumentStoreMCP server skeleton
  - Status: Backlog
  - MCP server setup with SSE transport on port 3002
  - Tool registration framework
  - Connection to ChromaDB
  - Requirements: [document-processing:FR-006]
  - Test Scenarios: [document-processing:DocumentStoreMCP/TS-12]

- Task 10: Implement ingest_document tool
  - Status: Backlog
  - Orchestrate: extract -> chunk -> embed -> store
  - Idempotency check before processing
  - Error handling and response formatting
  - Requirements: [document-processing:FR-006]
  - Test Scenarios: [document-processing:DocumentStoreMCP/TS-01], [document-processing:DocumentStoreMCP/TS-02], [document-processing:DocumentStoreMCP/TS-03], [document-processing:DocumentStoreMCP/TS-04]

- Task 11: Implement search_application_docs tool
  - Status: Backlog
  - Semantic search with optional filters
  - Format results with metadata
  - Requirements: [document-processing:FR-007]
  - Test Scenarios: [document-processing:DocumentStoreMCP/TS-05], [document-processing:DocumentStoreMCP/TS-06], [document-processing:DocumentStoreMCP/TS-07]

- Task 12: Implement get_document_text and list_ingested_documents tools
  - Status: Backlog
  - Concatenate chunks for full text retrieval
  - List with document-level metadata
  - Requirements: [document-processing:FR-008], [document-processing:FR-009]
  - Test Scenarios: [document-processing:DocumentStoreMCP/TS-08], [document-processing:DocumentStoreMCP/TS-09], [document-processing:DocumentStoreMCP/TS-10], [document-processing:DocumentStoreMCP/TS-11]

### Phase 4: Classification & Progress

- Task 13: Implement DocumentClassifier
  - Status: Backlog
  - Filename pattern matching
  - Content-based classification fallback
  - Transport assessment, design statement, site plan detection
  - Requirements: [document-processing:FR-010]
  - Test Scenarios: [document-processing:DocumentClassifier/TS-01], [document-processing:DocumentClassifier/TS-02], [document-processing:DocumentClassifier/TS-03], [document-processing:DocumentClassifier/TS-04], [document-processing:DocumentClassifier/TS-05], [document-processing:DocumentClassifier/TS-06]

- Task 14: Implement progress reporting during ingestion
  - Status: Backlog
  - Publish progress events to Redis
  - Format: "Ingested X of Y documents"
  - Requirements: [document-processing:FR-011]
  - Test Scenarios: [document-processing:ITS-03], [document-processing:E2E-02]

### Phase 5: Integration & Testing

- Task 15: Wire worker to Document Store MCP
  - Status: Complete
  - Worker spawns MCP process or connects via SSE
  - Call ingest_document for each downloaded file
  - Handle failures gracefully
  - Requirements: [document-processing:FR-006], [document-processing:NFR-006]
  - Test Scenarios: [document-processing:ITS-01], [document-processing:ITS-07]

- Task 16: Integration tests with sample documents
  - Status: Complete
  - Test fixtures: text PDF, scanned PDF, drawing PDF
  - Full pipeline verification
  - Re-ingestion scenarios
  - Requirements: All FRs
  - Test Scenarios: [document-processing:ITS-02], [document-processing:ITS-04], [document-processing:ITS-05], [document-processing:ITS-06], [document-processing:ITS-08]

- Task 17: Performance testing and optimization
  - Status: Complete
  - Verify search latency <500ms
  - Test with 50 documents (NFR-001)
  - Optimize batch sizes, connection pooling
  - Requirements: [document-processing:NFR-001], [document-processing:NFR-005]
  - Test Scenarios: [document-processing:ChromaClient/TS-08]

### Phase 6: Docker & Documentation

- Task 18: Create Dockerfile for document-store-mcp
  - Status: Complete
  - Extend base image with Tesseract, sentence-transformers
  - Volume mount for /data/chroma
  - Health check for ChromaDB connection
  - Requirements: Project containerization

- Task 19: Update docker-compose.yml
  - Status: Complete
  - Add document-store-mcp service
  - Configure port 3002, volume mounts
  - Dependency on base image build
  - Requirements: Project containerization

- Task 20: E2E smoke test with real documents
  - Status: Complete
  - Test against sample Cherwell documents
  - Verify full flow: download -> ingest -> search
  - Requirements: [document-processing:NFR-001], [document-processing:NFR-002]
  - Test Scenarios: [document-processing:E2E-01], [document-processing:E2E-03]

---

## Requirements Validation

### Functional Requirements

- [document-processing:FR-001]: Phase 1 Task 1
- [document-processing:FR-002]: Phase 1 Task 2
- [document-processing:FR-003]: Phase 1 Task 4
- [document-processing:FR-004]: Phase 2 Task 5
- [document-processing:FR-005]: Phase 2 Task 6, Phase 2 Task 8
- [document-processing:FR-006]: Phase 3 Task 9, Phase 3 Task 10, Phase 5 Task 15
- [document-processing:FR-007]: Phase 2 Task 7, Phase 3 Task 11
- [document-processing:FR-008]: Phase 3 Task 12
- [document-processing:FR-009]: Phase 2 Task 8, Phase 3 Task 12
- [document-processing:FR-010]: Phase 4 Task 13
- [document-processing:FR-011]: Phase 4 Task 14
- [document-processing:FR-012]: Phase 1 Task 3

### Non-Functional Requirements

- [document-processing:NFR-001]: Phase 5 Task 17, Phase 6 Task 20
- [document-processing:NFR-002]: Phase 1 Task 2, Phase 6 Task 20
- [document-processing:NFR-003]: Phase 2 Task 5
- [document-processing:NFR-004]: Phase 2 Task 6
- [document-processing:NFR-005]: Phase 2 Task 7, Phase 5 Task 17
- [document-processing:NFR-006]: Phase 5 Task 15

---

## Test Scenario Validation

### Component Scenarios

- [document-processing:DocumentProcessor/TS-01]: Phase 1 Task 1
- [document-processing:DocumentProcessor/TS-02]: Phase 1 Task 2
- [document-processing:DocumentProcessor/TS-03]: Phase 1 Task 2
- [document-processing:DocumentProcessor/TS-04]: Phase 1 Task 3
- [document-processing:DocumentProcessor/TS-05]: Phase 1 Task 1
- [document-processing:DocumentProcessor/TS-06]: Phase 1 Task 1
- [document-processing:DocumentProcessor/TS-07]: Phase 1 Task 2
- [document-processing:DocumentProcessor/TS-08]: Phase 1 Task 2
- [document-processing:TextChunker/TS-01]: Phase 1 Task 4
- [document-processing:TextChunker/TS-02]: Phase 1 Task 4
- [document-processing:TextChunker/TS-03]: Phase 1 Task 4
- [document-processing:TextChunker/TS-04]: Phase 1 Task 4
- [document-processing:TextChunker/TS-05]: Phase 1 Task 4
- [document-processing:TextChunker/TS-06]: Phase 1 Task 4
- [document-processing:EmbeddingService/TS-01]: Phase 2 Task 5
- [document-processing:EmbeddingService/TS-02]: Phase 2 Task 5
- [document-processing:EmbeddingService/TS-03]: Phase 2 Task 5
- [document-processing:EmbeddingService/TS-04]: Phase 2 Task 5
- [document-processing:EmbeddingService/TS-05]: Phase 2 Task 5
- [document-processing:EmbeddingService/TS-06]: Phase 2 Task 5
- [document-processing:ChromaClient/TS-01]: Phase 2 Task 6
- [document-processing:ChromaClient/TS-02]: Phase 2 Task 6
- [document-processing:ChromaClient/TS-03]: Phase 2 Task 7
- [document-processing:ChromaClient/TS-04]: Phase 2 Task 7
- [document-processing:ChromaClient/TS-05]: Phase 2 Task 7
- [document-processing:ChromaClient/TS-06]: Phase 2 Task 6
- [document-processing:ChromaClient/TS-07]: Phase 2 Task 6
- [document-processing:ChromaClient/TS-08]: Phase 2 Task 7, Phase 5 Task 17
- [document-processing:ChromaClient/TS-09]: Phase 2 Task 6
- [document-processing:DocumentStoreMCP/TS-01]: Phase 3 Task 10
- [document-processing:DocumentStoreMCP/TS-02]: Phase 3 Task 10
- [document-processing:DocumentStoreMCP/TS-03]: Phase 3 Task 10
- [document-processing:DocumentStoreMCP/TS-04]: Phase 3 Task 10
- [document-processing:DocumentStoreMCP/TS-05]: Phase 3 Task 11
- [document-processing:DocumentStoreMCP/TS-06]: Phase 3 Task 11
- [document-processing:DocumentStoreMCP/TS-07]: Phase 3 Task 11
- [document-processing:DocumentStoreMCP/TS-08]: Phase 3 Task 12
- [document-processing:DocumentStoreMCP/TS-09]: Phase 3 Task 12
- [document-processing:DocumentStoreMCP/TS-10]: Phase 3 Task 12
- [document-processing:DocumentStoreMCP/TS-11]: Phase 3 Task 12
- [document-processing:DocumentStoreMCP/TS-12]: Phase 3 Task 9
- [document-processing:DocumentClassifier/TS-01]: Phase 4 Task 13
- [document-processing:DocumentClassifier/TS-02]: Phase 4 Task 13
- [document-processing:DocumentClassifier/TS-03]: Phase 4 Task 13
- [document-processing:DocumentClassifier/TS-04]: Phase 4 Task 13
- [document-processing:DocumentClassifier/TS-05]: Phase 4 Task 13
- [document-processing:DocumentClassifier/TS-06]: Phase 4 Task 13
- [document-processing:DocumentTracker/TS-01]: Phase 2 Task 8
- [document-processing:DocumentTracker/TS-02]: Phase 2 Task 8
- [document-processing:DocumentTracker/TS-03]: Phase 2 Task 8
- [document-processing:DocumentTracker/TS-04]: Phase 2 Task 8
- [document-processing:DocumentTracker/TS-05]: Phase 2 Task 8
- [document-processing:DocumentTracker/TS-06]: Phase 2 Task 8

### Integration Scenarios

- [document-processing:ITS-01]: Phase 5 Task 15
- [document-processing:ITS-02]: Phase 5 Task 16
- [document-processing:ITS-03]: Phase 4 Task 14
- [document-processing:ITS-04]: Phase 5 Task 16
- [document-processing:ITS-05]: Phase 5 Task 16
- [document-processing:ITS-06]: Phase 5 Task 16
- [document-processing:ITS-07]: Phase 5 Task 15
- [document-processing:ITS-08]: Phase 5 Task 16

### E2E Scenarios

- [document-processing:E2E-01]: Phase 6 Task 20
- [document-processing:E2E-02]: Phase 4 Task 14
- [document-processing:E2E-03]: Phase 6 Task 20

---

## Appendix

### Glossary

- **Chunk:** A segment of text sized appropriately for embedding (typically 1000 tokens)
- **Embedding:** A 384-dimensional vector representation of text for semantic similarity
- **OCR:** Optical Character Recognition - extracting text from images
- **ChromaDB:** Open-source vector database for storing and searching embeddings
- **Idempotent:** Operation that produces the same result regardless of how many times it's performed

### Document Type Classifications

| Type Slug | Filename Patterns | Content Keywords |
|-----------|-------------------|------------------|
| `transport_assessment` | transport, ta, traffic | trip generation, modal split, parking |
| `design_access_statement` | design, access, das, d&a | design principles, access arrangements |
| `site_plan` | site, plan, layout, drawing | N/A (primarily visual) |
| `floor_plans` | floor, plan, elevation | N/A (primarily visual) |
| `supporting_statement` | statement, planning, supporting | planning policy, justification |
| `consultation_response` | response, consultation, comments | N/A |
| `other` | (fallback) | (fallback) |

### References

- [Master Design Document Section 3.2](../../docs/DESIGN.md#32-mcp-server-document-store-document-store-mcp)
- [sentence-transformers Documentation](https://www.sbert.net/)
- [ChromaDB Documentation](https://docs.trychroma.com/)
- [PyMuPDF Documentation](https://pymupdf.readthedocs.io/)
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract)

### Change History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-06 | SDD Agent | Initial design |
