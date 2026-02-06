# Cherwell Planning Application Cycle Advocacy Agent

## Architecture & Development Plan

---

## 1. Project Overview

An AI agent system that accepts a Cherwell District Council planning application reference number, automatically fetches and processes all associated documents, stores them in a vector database, and produces a structured review from the perspective of a cycling advocacy group — benchmarking proposals against local policy (Cherwell Local Plan 2040, Oxfordshire LTCP, LCWIP) and national guidance (LTN 1/20, NPPF, Manual for Streets).

---

## 2. System Architecture

### 2.1 High-Level Component Diagram

```
                External Consumers
                       │
          ┌────────────┼──────────────┐
          │  POST /reviews            │  Webhook callbacks
          │  GET  /reviews/{id}       │◀──────────────────────┐
          │  GET  /reviews/{id}/status│                       │
          └────────────┬──────────────┘                       │
                       │                                      │
┌──────────────────────┼──────────────────────────────────────┼──────┐
│                  Docker Compose Stack                        │      │
│                      │                                      │      │
│  ┌───────────────────▼───────────┐    ┌──────────────────┐  │      │
│  │  API Gateway (FastAPI)        │───▶│  Redis            │  │      │
│  │  - REST endpoints             │◀───│  (Job Queue +     │  │      │
│  │  - Webhook dispatcher         │    │   State Store)    │  │      │
│  │  - Auth (API key)             │    └──────────────────┘  │      │
│  └───────────────┬───────────────┘                          │      │
│                  │                                          │      │
│                  ▼                                          │      │
│  ┌──────────────────────────────┐    ┌──────────────────┐  │      │
│  │  Agent Worker                │───▶│  Claude API      │  │      │
│  │  (Orchestrator)              │◀───│  (claude-sonnet  │  │      │
│  │  - Picks jobs from Redis     │    │   -4-5)          │  │      │
│  │  - Posts status → Redis      │    └──────────────────┘  │      │
│  │  - Fires webhooks on events ─┼───────────────────────────┘      │
│  └────────────┬─────────────────┘                                  │
│               │                                                     │
│  ┌────────────┼────────────────┐                                   │
│  ▼            ▼                ▼                                    │
│  ┌──────────────┐ ┌─────────────┐ ┌────────────────────┐          │
│  │ MCP Server:  │ │ MCP Server: │ │ MCP Server:        │          │
│  │ Cherwell     │ │ Document    │ │ Policy Knowledge   │          │
│  │ Scraper      │ │ Store       │ │ Base               │          │
│  └──────┬───────┘ └──────┬──────┘ └────────┬───────────┘          │
│         │                │                  │                       │
│         ▼                ▼                  ▼                       │
│  ┌──────────────┐ ┌──────────────────────────────┐                 │
│  │ Cherwell     │ │ ChromaDB (Persistent)        │                 │
│  │ Planning     │ │  ├── application_docs         │                 │
│  │ Portal       │ │  └── policy_docs              │                 │
│  └──────────────┘ └──────────────────────────────┘                 │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Component Responsibilities

| Component | Role |
|---|---|
| **API Gateway** | FastAPI REST API. Accepts review requests, returns job IDs, serves status/results, dispatches webhooks. The single external interface to the system. |
| **Redis** | Job queue (review requests), state store (job status, progress events), and pub/sub bus for webhook triggers. Lightweight, no persistence needed beyond container lifetime. |
| **Agent Worker** | Claude-powered orchestrator. Picks jobs from the Redis queue, coordinates MCP tool calls, builds the review, publishes progress events and final results back to Redis. |
| **MCP: Cherwell Scraper** | Navigates the Cherwell planning register, extracts application metadata, downloads all associated documents (PDFs, images, etc.). |
| **MCP: Document Store** | Processes raw documents (PDF extraction, OCR, chunking), stores embeddings in ChromaDB, provides semantic search. |
| **MCP: Policy Knowledge Base** | Pre-loaded with LTN 1/20, Cherwell Local Plan, NPPF, LCWIP, Manual for Streets. Provides policy-aware retrieval. |
| **ChromaDB** | Local vector database for document embeddings. Two collections: `application_docs` and `policy_docs`. |

---

## 3. Detailed Component Design

### 3.1 MCP Server: Cherwell Scraper (`cherwell-scraper-mcp`)

**Purpose:** Fetch planning application data and documents from the Cherwell planning register.

**Target Site:** `https://planningregister.cherwell.gov.uk`

**URL Pattern:** `https://planningregister.cherwell.gov.uk/Planning/Display/{reference}`
- Example reference: `25/01178/REM`, `23/01421/TCA`, `08/00707/F`

**Tools Exposed:**

```typescript
// Fetch application metadata (description, applicant, status, dates, address)
get_application_details(reference: string): ApplicationMetadata

// List all documents associated with an application
list_application_documents(reference: string): DocumentInfo[]

// Download a specific document by its URL/ID
download_document(document_url: string, output_dir: string): FilePath

// Download all documents for an application
download_all_documents(reference: string, output_dir: string): FilePath[]
```

**Implementation Notes:**
- Built in Python using `httpx` + `beautifulsoup4` for HTML parsing
- The planning register is a server-rendered ASP.NET application; document lists are on the "Documents" tab of each application page
- Documents are typically PDFs (plans, design & access statements, transport assessments) and some images (site photos, drawings)
- Implement polite scraping: respect `robots.txt`, rate-limit requests (1–2 req/sec), set a descriptive `User-Agent`
- Handle session cookies and any anti-scraping measures (the site uses `__RequestVerificationToken`)
- Store raw downloads in `/data/raw/{reference}/`
- **Important:** The documents tab may paginate — handle multi-page document lists
- Extract the document metadata table: Document Type, Date, Description

**MCP Server Configuration:**

```json
{
  "name": "cherwell-scraper",
  "version": "1.0.0",
  "transport": "stdio",
  "tools": [
    "get_application_details",
    "list_application_documents",
    "download_document",
    "download_all_documents"
  ]
}
```

### 3.2 MCP Server: Document Store (`document-store-mcp`)

**Purpose:** Process documents into LLM-readable chunks and store/retrieve from ChromaDB.

**Tools Exposed:**

```typescript
// Ingest a document: extract text, chunk, embed, store
ingest_document(
  file_path: string,
  application_ref: string,
  document_type: string,   // e.g. "transport_assessment", "design_access_statement"
  metadata: object
): IngestResult

// Semantic search across application documents
search_application_docs(
  query: string,
  application_ref?: string,  // optional filter
  n_results?: number
): SearchResult[]

// Get full text of a specific document
get_document_text(document_id: string): string

// List all ingested documents for an application
list_ingested_documents(application_ref: string): DocumentSummary[]
```

**Document Processing Pipeline:**

```
Raw PDF/Image
    │
    ├── PDF with text layer ──▶ PyMuPDF text extraction
    │
    ├── Scanned PDF / Image ──▶ Tesseract OCR
    │
    └── Structured extraction ──▶ Table detection (camelot/tabula)
            │
            ▼
    Text Cleaning & Normalisation
            │
            ▼
    Chunking (recursive character splitter)
    - chunk_size: 1000 tokens
    - chunk_overlap: 200 tokens
    - respect section boundaries where possible
            │
            ▼
    Embedding (sentence-transformers/all-MiniLM-L6-v2)
            │
            ▼
    ChromaDB collection: "application_docs"
    - metadata: {application_ref, document_type, page_number, chunk_index, source_filename}
```

**Implementation Notes:**
- Python-based: `pymupdf`, `pytesseract`, `Pillow`, `chromadb`, `sentence-transformers`
- Use `all-MiniLM-L6-v2` for embeddings (384-dim, fast, runs on CPU) — good balance for a local setup
- ChromaDB runs in-process (persistent mode to `/data/chroma`) — no separate server needed
- For planning drawings and site plans (images), extract any text via OCR but also store a reference so the agent can note that drawings exist and flag them for human review
- Special handling for Transport Assessments and Design & Access Statements — these are the primary documents a cycle advocacy review cares about

### 3.3 MCP Server: Policy Knowledge Base (`policy-kb-mcp`)

**Purpose:** Versioned reference library of cycling and transport policy. Supports multiple revisions of the same policy document with effective dates, so the agent can retrieve the policy that was in force at the time a planning application was submitted.

**Tools Exposed:**

```typescript
// Search policy documents by topic (revision-aware)
search_policy(
  query: string,
  sources?: string[],       // filter to specific documents
  effective_date?: string,  // ISO date — return only revisions in force on this date
  n_results?: number
): PolicySearchResult[]

// Get a specific policy section by reference
get_policy_section(
  source: string,           // e.g. "LTN_1_20"
  section_ref: string,      // e.g. "Chapter 5", "Table 5-2"
  revision_id?: string      // specific revision; defaults to latest effective
): PolicySection

// List all available policy documents with their revisions
list_policy_documents(): PolicyDocumentInfo[]

// List all revisions for a specific policy document
list_policy_revisions(
  source: string            // e.g. "NPPF"
): PolicyRevisionInfo[]

// Ingest a new revision of a policy document (called by the API worker)
ingest_policy_revision(
  source: string,
  revision_id: string,
  file_path: string,
  effective_from: string,
  effective_to?: string,
  metadata: object
): IngestResult

// Remove a specific revision's chunks from the vector store
remove_policy_revision(
  source: string,
  revision_id: string
): RemoveResult
```

**Pre-loaded Policy Documents (seeded at first run):**

| Document | Key Content for Cycle Advocacy |
|---|---|
| **LTN 1/20** (Cycle Infrastructure Design) | Design standards for cycle infrastructure — widths, junction design, separation requirements, inclusivity, traffic flow thresholds for segregation |
| **NPPF** (National Planning Policy Framework) | Para 108–117 on sustainable transport; requirement to give priority to pedestrian/cycle movements |
| **Manual for Streets 1 & 2** | Street design principles, shared space, speed reduction, cycle integration |
| **Cherwell Local Plan 2011–2031** (Adopted) | Policies SLE4 (transport), BSC policies on Bicester/Banbury development |
| **Cherwell Local Plan 2040** (Emerging Draft) | Core policies on walking/cycling, filtered permeability, active travel |
| **Oxfordshire LTCP** (Local Transport & Connectivity Plan) | County-wide transport strategy, active travel targets |
| **Oxfordshire LCWIP** (Local Cycling & Walking Infrastructure Plan) | Specific route proposals and priority corridors in Cherwell area |
| **Cherwell Residential Design Guide SPD** | Design criteria for accommodating pedestrians and cyclists in new developments |
| **Active Travel England Guidance** | Assessment criteria used by statutory consultee |

**Implementation Notes:**
- Same ChromaDB instance, separate collection: `policy_docs`
- Seed policy documents at first run via init script; subsequent revisions managed via REST API
- All chunks tagged with `revision_id`, `effective_from`, and `effective_to` in metadata — enabling temporal filtering
- When `effective_date` is provided in a search, only chunks from the revision where `effective_from ≤ effective_date` and (`effective_to` is null or `effective_to > effective_date`) are returned
- For LTN 1/20, pay special attention to indexing Table 5-2 (flow/speed criteria for segregation), Chapter 6 (junction design), Chapter 10 (integration with highways) and the summary principles
- A **policy registry** in Redis tracks all documents, their revisions, and effective dates — this is the source of truth for which revisions exist; ChromaDB stores the actual embeddings
- When a new revision is ingested, if no `effective_to` date is provided, the previous revision's `effective_to` is automatically set to the day before the new revision's `effective_from`

---

## 4. Agent Workflow

### 4.1 End-to-End Flow

```
Consumer: POST /api/v1/reviews { "application_ref": "25/01178/REM", "webhook": {...} }
    │
    ▼
API Gateway: Validates request, creates job in Redis, returns 202 + review_id
    │
    ▼
Redis Queue: Job enqueued with review_id + application_ref + webhook config
    │
    ▼
Worker: Picks job from queue
    ├── Publishes "review.started" → Redis → API dispatches webhook
    │
    ▼
Step 1: FETCH APPLICATION
    Worker calls: cherwell-scraper.get_application_details("25/01178/REM")
    Worker calls: cherwell-scraper.download_all_documents("25/01178/REM")
    ├── Publishes progress (phase 1→2) → webhook: "review.progress"
    ▶ Retrieves metadata + all PDFs/documents
    │
    ▼
Step 2: INGEST DOCUMENTS
    For each document:
        Worker calls: document-store.ingest_document(file, ref, type, metadata)
    ├── Publishes progress (phase 3, per-document) → webhook: "review.progress"
    ▶ Documents chunked, embedded, stored in ChromaDB
    │
    ▼
Step 3: INITIAL ASSESSMENT
    Worker calls: document-store.search_application_docs(
        "transport assessment cycling pedestrian active travel", ref
    )
    Worker calls: document-store.search_application_docs(
        "highway access junction road layout", ref
    )
    ├── Publishes progress (phase 4) → webhook: "review.progress"
    ▶ Identifies key transport/cycling-related content
    │
    ▼
Step 4: POLICY COMPARISON
    For each identified issue:
        Worker calls: policy-kb.search_policy(
            "cycle lane width segregation requirement",
            sources=["LTN_1_20", "Cherwell_Local_Plan_2040"],
            effective_date=application.date_validated   # uses revision in force at validation
        )
    ▶ Retrieves relevant policy requirements (correct revision) to compare against proposals
    │
    ▼
Step 5: GENERATE REVIEW
    Agent synthesises findings into structured review document
    ├── Publishes progress (phase 5) → webhook: "review.progress"
    ▶ Structured JSON + Markdown review stored in Redis
    │
    ▼
Step 6: COMPLETE
    Worker stores result in Redis (review_result:{review_id})
    Worker updates job status to "completed"
    ├── Publishes "review.completed" → webhook with full review payload
    │
    ▼
Consumer: Receives webhook OR polls GET /api/v1/reviews/{review_id}
    ▶ Full structured review available via API and webhook
```

### 4.2 Agent System Prompt (Core)

```
You are a planning application reviewer acting on behalf of a local cycling
advocacy group in the Cherwell District. Your role is to assess planning
applications from the perspective of people who walk and cycle.

For each application you review, you should:

1. UNDERSTAND THE PROPOSAL — Summarise what is being proposed, its location,
   and scale.

2. ASSESS TRANSPORT & MOVEMENT — Evaluate:
   - Cycle parking provision (quantity, type, location, security, accessibility)
   - Cycle route provision (on-site and connections to existing network)
   - Whether cycle infrastructure meets LTN 1/20 standards
   - Pedestrian permeability and filtered permeability for cycles
   - Impact on existing cycle routes and rights of way
   - Trip generation and modal split assumptions in any Transport Assessment

3. COMPARE AGAINST POLICY — Reference specific policies:
   - LTN 1/20 design standards (especially Table 5-2 on segregation triggers,
     Chapter 5 on geometric design, Chapter 6 on junctions)
   - Cherwell Local Plan policies on sustainable transport
   - NPPF paragraphs on sustainable transport
   - Oxfordshire LCWIP route proposals
   - Active Travel England assessment criteria

4. IDENTIFY ISSUES — Flag:
   - Non-compliance with LTN 1/20
   - Missing or inadequate cycle parking
   - Missed opportunities for active travel connections
   - Hostile junction designs for cyclists
   - Failure to provide filtered permeability
   - Inadequate width or shared-use paths where segregation is warranted

5. MAKE RECOMMENDATIONS — Suggest specific, constructive improvements
   with policy justification.

Always cite specific policy references. Be constructive and evidence-based.
Your review should be suitable for submission as a formal consultation response.
```

---

## 5. Review Output Template

The agent produces a structured review in the following format:

```markdown
# Cycle Advocacy Review: [Application Reference]

## Application Summary
- **Reference:** [ref]
- **Site:** [address]
- **Proposal:** [description]
- **Applicant:** [name]
- **Status:** [current status]

## Assessment Summary
**Overall Rating:** 🔴 Red / 🟡 Amber / 🟢 Green

| Aspect | Rating | Key Issue |
|--------|--------|-----------|
| Cycle Parking | 🟡 | Quantity adequate but no cargo bike spaces |
| Cycle Routes | 🔴 | Shared-use path where LTN 1/20 requires segregation |
| Junctions | 🔴 | No protected cycle provision at site access |
| Permeability | 🟡 | Internal routes connect but no link to [X] |
| Policy Compliance | 🔴 | Does not meet NPPF para 116(a) |

## Detailed Assessment

### 1. Cycle Parking
[Analysis with specific quantities, types, and policy requirements]

### 2. Cycle Route Provision
[Analysis of on-site and connecting routes against LTN 1/20]

### 3. Junction Design
[Assessment of junctions against LTN 1/20 Chapter 6]

### 4. Pedestrian & Cycle Permeability
[Assessment of connectivity and filtered permeability]

### 5. Transport Assessment Review
[Critique of modal split assumptions, trip generation, etc.]

## Policy Compliance Matrix

| Requirement | Policy Source | Compliant? | Notes |
|---|---|---|---|
| Segregated cycle track where >2500 PCU/day | LTN 1/20 Table 5-2 | ❌ | [detail] |
| Cycle parking to Local Plan standards | Cherwell LP Policy X | ✅ | [detail] |
| ... | ... | ... | ... |

## Recommendations
1. [Specific, actionable recommendation with policy justification]
2. ...

## Suggested Conditions
If the council is minded to approve, the following conditions are recommended:
1. ...
```

---

## 6. REST API Specification

The system exposes a REST API via FastAPI as the sole external interface. All reviews are processed asynchronously — the API returns a job ID immediately, and consumers either poll for status or receive webhook callbacks.

### 6.1 Authentication

All endpoints require an API key passed as a `Bearer` token:

```
Authorization: Bearer <API_KEY>
```

API keys are configured via environment variable or a simple keys file. In a production deployment, this would be upgraded to OAuth2 or mutual TLS.

### 6.2 Endpoints

#### `POST /api/v1/reviews` — Submit a Review Request

Accepts an application reference and optional configuration. Returns immediately with a job ID.

**Request:**

```json
POST /api/v1/reviews
Content-Type: application/json
Authorization: Bearer sk-cycle-...

{
    "application_ref": "25/01178/REM",
    "options": {
        "focus_areas": ["cycle_parking", "cycle_routes", "junctions", "permeability"],
        "output_format": "markdown",
        "include_policy_matrix": true,
        "include_suggested_conditions": true
    },
    "webhook": {
        "url": "https://your-app.example.com/hooks/cherwell",
        "secret": "whsec_abc123...",
        "events": ["review.started", "review.progress", "review.completed", "review.failed"]
    }
}
```

**Response (202 Accepted):**

```json
{
    "review_id": "rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
    "application_ref": "25/01178/REM",
    "status": "queued",
    "created_at": "2025-02-05T14:30:00Z",
    "estimated_duration_seconds": 180,
    "links": {
        "self": "/api/v1/reviews/rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
        "status": "/api/v1/reviews/rev_01HQXK7V3WNPB8MTJF2R5ADGX9/status",
        "cancel": "/api/v1/reviews/rev_01HQXK7V3WNPB8MTJF2R5ADGX9/cancel"
    }
}
```

#### `GET /api/v1/reviews/{review_id}` — Get Review Result

Returns the full review once complete, or current status if still processing.

**Response (200 OK — completed):**

```json
{
    "review_id": "rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
    "application_ref": "25/01178/REM",
    "status": "completed",
    "created_at": "2025-02-05T14:30:00Z",
    "completed_at": "2025-02-05T14:33:12Z",
    "application": {
        "reference": "25/01178/REM",
        "address": "Land at ..., Bicester",
        "proposal": "Reserved matters application for ...",
        "applicant": "Example Developments Ltd",
        "status": "Under consideration",
        "consultation_end": "2025-02-15",
        "documents_fetched": 24,
        "documents_ingested": 22
    },
    "review": {
        "overall_rating": "red",
        "summary": "The application fails to meet several key requirements...",
        "aspects": [
            {
                "name": "Cycle Parking",
                "rating": "amber",
                "key_issue": "Quantity adequate but no cargo bike spaces",
                "detail": "The application proposes 48 Sheffield stands...",
                "policy_refs": ["Cherwell LP Policy SLE4", "LTN 1/20 Ch.11"]
            }
        ],
        "policy_compliance": [
            {
                "requirement": "Segregated cycle track where >2500 PCU/day",
                "policy_source": "LTN 1/20 Table 5-2",
                "compliant": false,
                "notes": "Shared-use path proposed on road with 4200 PCU/day"
            }
        ],
        "recommendations": [
            "Provide segregated cycle track on the eastern boundary...",
            "Add 4 cargo/adapted bike parking spaces..."
        ],
        "suggested_conditions": [
            "Prior to occupation, a detailed cycle parking layout..."
        ],
        "full_markdown": "# Cycle Advocacy Review: 25/01178/REM\n\n## Application Summary\n..."
    },
    "metadata": {
        "model": "claude-sonnet-4-5-20250929",
        "total_tokens_used": 45200,
        "processing_time_seconds": 192,
        "documents_analysed": 22,
        "policy_sources_referenced": 6,
        "policy_effective_date": "2025-01-20",
        "policy_revisions_used": [
            { "source": "LTN_1_20", "revision_id": "rev_LTN120_2020_07", "version_label": "July 2020" },
            { "source": "NPPF", "revision_id": "rev_NPPF_2024_12", "version_label": "December 2024" },
            { "source": "CHERWELL_LP_2040", "revision_id": "rev_CLP2040_2023_09", "version_label": "September 2023 Draft" }
        ]
    }
}
```

**Response (200 OK — still processing):**

```json
{
    "review_id": "rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
    "status": "processing",
    "progress": {
        "phase": "ingesting_documents",
        "phase_number": 2,
        "total_phases": 5,
        "detail": "Processing document 14 of 24: Transport_Assessment_v2.pdf",
        "percent_complete": 35
    }
}
```

#### `GET /api/v1/reviews/{review_id}/status` — Lightweight Status Check

Minimal payload for polling. Returns only status and progress.

**Response (200 OK):**

```json
{
    "review_id": "rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
    "status": "processing",
    "progress": {
        "phase": "policy_comparison",
        "percent_complete": 72
    }
}
```

#### `GET /api/v1/reviews/{review_id}/download` — Download Review as File

Returns the review in the requested format.

**Query params:** `?format=markdown` (default), `?format=pdf`, `?format=json`

**Response:** File download with appropriate `Content-Type`.

#### `POST /api/v1/reviews/{review_id}/cancel` — Cancel a Running Review

**Response (200 OK):**

```json
{
    "review_id": "rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
    "status": "cancelled"
}
```

#### `GET /api/v1/reviews` — List Reviews

Paginated list of all submitted reviews.

**Query params:** `?status=completed`, `?limit=20`, `?offset=0`, `?application_ref=25/01178/REM`

**Response (200 OK):**

```json
{
    "reviews": [
        {
            "review_id": "rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
            "application_ref": "25/01178/REM",
            "status": "completed",
            "overall_rating": "red",
            "created_at": "2025-02-05T14:30:00Z",
            "completed_at": "2025-02-05T14:33:12Z"
        }
    ],
    "total": 47,
    "limit": 20,
    "offset": 0
}
```

#### `GET /api/v1/health` — Health Check

**Response (200 OK):**

```json
{
    "status": "healthy",
    "services": {
        "redis": "connected",
        "chromadb": "connected",
        "cherwell_scraper_mcp": "connected",
        "document_store_mcp": "connected",
        "policy_kb_mcp": "connected"
    },
    "policy_store": {
        "documents_registered": 9,
        "total_revisions": 14,
        "active_revisions": 9,
        "last_revision_ingested_at": "2025-02-03T14:22:00Z"
    },
    "version": "1.0.0"
}
```

### 6.3 Policy Document Management Endpoints

The policy knowledge base is managed through the API. Documents have a stable identity (`source` slug, e.g. `LTN_1_20`) and one or more **revisions**, each with an effective date range. When a review runs, the agent automatically selects the revision that was in force on the application's validation date.

#### `GET /api/v1/policies` — List All Policy Documents

Returns all registered policy documents with their current (latest effective) revision.

**Query params:** `?status=active` (default), `?status=all`, `?source=LTN_1_20`

**Response (200 OK):**

```json
{
    "policies": [
        {
            "source": "LTN_1_20",
            "title": "Cycle Infrastructure Design (LTN 1/20)",
            "description": "Department for Transport local transport note on cycle infrastructure design standards",
            "category": "national_guidance",
            "current_revision": {
                "revision_id": "rev_LTN120_2020_07",
                "version_label": "July 2020",
                "effective_from": "2020-07-27",
                "effective_to": null,
                "status": "active",
                "ingested_at": "2025-02-01T10:00:00Z",
                "chunk_count": 847,
                "source_filename": "ltn-1-20-cycle-infrastructure-design.pdf"
            },
            "revision_count": 1,
            "created_at": "2025-02-01T10:00:00Z"
        },
        {
            "source": "NPPF",
            "title": "National Planning Policy Framework",
            "description": "Government planning policy for England",
            "category": "national_policy",
            "current_revision": {
                "revision_id": "rev_NPPF_2024_12",
                "version_label": "December 2024",
                "effective_from": "2024-12-12",
                "effective_to": null,
                "status": "active",
                "ingested_at": "2025-02-01T10:00:00Z",
                "chunk_count": 512,
                "source_filename": "nppf-december-2024.pdf"
            },
            "revision_count": 3,
            "created_at": "2025-02-01T10:00:00Z"
        }
    ],
    "total": 9
}
```

#### `POST /api/v1/policies` — Register a New Policy Document

Creates a new policy document entry (without any revisions yet). Revisions are added separately.

**Request:**

```json
POST /api/v1/policies
Content-Type: application/json

{
    "source": "ATE_STANDING_ADVICE",
    "title": "Active Travel England Standing Advice",
    "description": "Standing advice for local planning authorities on active travel matters",
    "category": "national_guidance"
}
```

**Response (201 Created):**

```json
{
    "source": "ATE_STANDING_ADVICE",
    "title": "Active Travel England Standing Advice",
    "description": "Standing advice for local planning authorities on active travel matters",
    "category": "national_guidance",
    "current_revision": null,
    "revision_count": 0,
    "created_at": "2025-02-05T16:00:00Z"
}
```

**Category values:** `national_policy`, `national_guidance`, `local_plan`, `local_guidance`, `county_strategy`, `supplementary`

#### `GET /api/v1/policies/{source}` — Get Policy Document Detail

Returns full detail for a policy document including all revisions.

**Response (200 OK):**

```json
{
    "source": "NPPF",
    "title": "National Planning Policy Framework",
    "description": "Government planning policy for England",
    "category": "national_policy",
    "revisions": [
        {
            "revision_id": "rev_NPPF_2024_12",
            "version_label": "December 2024",
            "effective_from": "2024-12-12",
            "effective_to": null,
            "status": "active",
            "ingested_at": "2025-02-01T10:00:00Z",
            "chunk_count": 512,
            "source_filename": "nppf-december-2024.pdf",
            "file_hash": "sha256:a1b2c3..."
        },
        {
            "revision_id": "rev_NPPF_2023_09",
            "version_label": "September 2023",
            "effective_from": "2023-09-05",
            "effective_to": "2024-12-11",
            "status": "superseded",
            "ingested_at": "2025-02-01T10:00:00Z",
            "chunk_count": 498,
            "source_filename": "nppf-september-2023.pdf",
            "file_hash": "sha256:d4e5f6..."
        },
        {
            "revision_id": "rev_NPPF_2021_07",
            "version_label": "July 2021",
            "effective_from": "2021-07-20",
            "effective_to": "2023-09-04",
            "status": "superseded",
            "ingested_at": "2025-02-01T10:00:00Z",
            "chunk_count": 483,
            "source_filename": "nppf-july-2021.pdf",
            "file_hash": "sha256:g7h8i9..."
        }
    ],
    "created_at": "2025-02-01T10:00:00Z",
    "updated_at": "2025-02-01T10:00:00Z"
}
```

#### `PATCH /api/v1/policies/{source}` — Update Policy Document Metadata

Update title, description, or category. Does not affect revisions.

**Request:**

```json
PATCH /api/v1/policies/NPPF
Content-Type: application/json

{
    "description": "Updated description of the NPPF"
}
```

#### `POST /api/v1/policies/{source}/revisions` — Upload a New Revision

Uploads a new revision of a policy document. The file is processed asynchronously (text extraction, chunking, embedding). The previous active revision's `effective_to` is automatically set to the day before the new revision's `effective_from` unless explicitly provided.

**Request (multipart/form-data):**

```
POST /api/v1/policies/NPPF/revisions
Content-Type: multipart/form-data

file:            <nppf-february-2026.pdf>
version_label:   "February 2026"
effective_from:  "2026-02-01"
effective_to:    (optional, null = currently in force)
notes:           "Updated paragraphs 114-117 on active travel"
```

**Response (202 Accepted):**

```json
{
    "source": "NPPF",
    "revision_id": "rev_NPPF_2026_02",
    "version_label": "February 2026",
    "effective_from": "2026-02-01",
    "effective_to": null,
    "status": "processing",
    "ingestion_job_id": "job_01HRZ...",
    "links": {
        "self": "/api/v1/policies/NPPF/revisions/rev_NPPF_2026_02",
        "status": "/api/v1/policies/NPPF/revisions/rev_NPPF_2026_02/status"
    },
    "side_effects": {
        "superseded_revision": "rev_NPPF_2024_12",
        "superseded_effective_to_set": "2026-01-31"
    }
}
```

**Processing:** The file is placed in `/data/policy/`, then a background job runs the same PDF extraction → chunking → embedding pipeline used for application documents. The revision status transitions: `processing → active` (or `processing → failed`).

#### `GET /api/v1/policies/{source}/revisions/{revision_id}` — Get Revision Detail

**Response (200 OK):**

```json
{
    "source": "NPPF",
    "revision_id": "rev_NPPF_2024_12",
    "version_label": "December 2024",
    "effective_from": "2024-12-12",
    "effective_to": null,
    "status": "active",
    "ingested_at": "2025-02-01T10:00:00Z",
    "chunk_count": 512,
    "source_filename": "nppf-december-2024.pdf",
    "file_hash": "sha256:a1b2c3...",
    "file_size_bytes": 2458624,
    "notes": null,
    "metadata": {
        "pages_extracted": 78,
        "tables_detected": 4,
        "ocr_required": false,
        "processing_time_seconds": 34
    }
}
```

#### `GET /api/v1/policies/{source}/revisions/{revision_id}/status` — Check Ingestion Status

For newly uploaded revisions that are still being processed.

**Response (200 OK):**

```json
{
    "revision_id": "rev_NPPF_2026_02",
    "status": "processing",
    "progress": {
        "phase": "embedding",
        "percent_complete": 65,
        "chunks_processed": 332,
        "chunks_total": 510
    }
}
```

#### `PATCH /api/v1/policies/{source}/revisions/{revision_id}` — Update Revision Metadata

Allows correcting effective dates, version labels, or notes. If `effective_from` or `effective_to` are changed, adjacent revisions' dates are **not** automatically adjusted — this must be done explicitly to avoid accidental cascading changes.

**Request:**

```json
PATCH /api/v1/policies/NPPF/revisions/rev_NPPF_2024_12
Content-Type: application/json

{
    "effective_to": "2026-01-31",
    "notes": "Superseded by February 2026 revision"
}
```

#### `DELETE /api/v1/policies/{source}/revisions/{revision_id}` — Remove a Revision

Removes the revision's chunks from ChromaDB and its registry entry from Redis. Cannot delete the only remaining active revision of a policy document — at least one active revision must remain.

**Response (200 OK):**

```json
{
    "source": "NPPF",
    "revision_id": "rev_NPPF_2021_07",
    "status": "deleted",
    "chunks_removed": 483
}
```

#### `GET /api/v1/policies/effective` — Get Policy Snapshot for a Date

Returns which revision of each policy was in force on a given date. Useful for understanding the policy context for a specific planning application.

**Query params:** `?date=2024-03-15` (required)

**Response (200 OK):**

```json
{
    "effective_date": "2024-03-15",
    "policies": [
        {
            "source": "NPPF",
            "title": "National Planning Policy Framework",
            "effective_revision": {
                "revision_id": "rev_NPPF_2023_09",
                "version_label": "September 2023",
                "effective_from": "2023-09-05",
                "effective_to": "2024-12-11"
            }
        },
        {
            "source": "LTN_1_20",
            "title": "Cycle Infrastructure Design (LTN 1/20)",
            "effective_revision": {
                "revision_id": "rev_LTN120_2020_07",
                "version_label": "July 2020",
                "effective_from": "2020-07-27",
                "effective_to": null
            }
        }
    ],
    "policies_not_yet_effective": [],
    "policies_with_no_revision_for_date": []
}
```

#### `POST /api/v1/policies/{source}/revisions/{revision_id}/reindex` — Reindex a Revision

Re-runs the chunking and embedding pipeline for an existing revision without re-uploading the file. Useful after tuning chunking parameters or upgrading the embedding model.

**Response (202 Accepted):**

```json
{
    "revision_id": "rev_LTN120_2020_07",
    "status": "reindexing",
    "ingestion_job_id": "job_01HRZ..."
}
```

### 6.4 Error Responses

All errors follow a consistent format:

```json
{
    "error": {
        "code": "application_not_found",
        "message": "No planning application found with reference 25/99999/F",
        "details": {
            "reference": "25/99999/F",
            "portal_url": "https://planningregister.cherwell.gov.uk/Planning/Display/25/99999/F"
        }
    }
}
```

**Error codes:**

| HTTP Status | Code | Description |
|---|---|---|
| 400 | `invalid_reference` | Malformed application reference |
| 400 | `invalid_effective_date` | Effective date is malformed or creates an impossible date range |
| 401 | `unauthorized` | Missing or invalid API key |
| 404 | `review_not_found` | Review ID does not exist |
| 404 | `application_not_found` | Planning application not found on Cherwell portal |
| 404 | `policy_not_found` | Policy source slug does not exist |
| 404 | `revision_not_found` | Revision ID does not exist for this policy |
| 409 | `review_already_exists` | A review for this application is already queued or processing |
| 409 | `policy_already_exists` | A policy with this source slug already exists |
| 409 | `revision_overlap` | New revision's effective dates overlap with an existing revision |
| 409 | `cannot_delete_sole_revision` | Cannot delete the only active revision of a policy |
| 422 | `invalid_webhook_url` | Webhook URL is not reachable or malformed |
| 422 | `unsupported_file_type` | Uploaded policy file is not a supported format (PDF, DOCX, HTML) |
| 429 | `rate_limited` | Too many requests |
| 500 | `internal_error` | Unexpected server error |
| 500 | `ingestion_failed` | Policy revision ingestion failed during processing |
| 502 | `scraper_error` | Cherwell planning portal is unreachable |

### 6.5 Status Lifecycle

```
queued → processing → completed
                    → failed
queued → cancelled
processing → cancelled
```

**Processing sub-phases** (reported in progress):

| Phase | phase_number | Description |
|---|---|---|
| `fetching_metadata` | 1 | Scraping application details from Cherwell portal |
| `downloading_documents` | 2 | Downloading all associated PDFs and files |
| `ingesting_documents` | 3 | Extracting text, chunking, embedding into ChromaDB |
| `analysing_application` | 4 | Agent reviewing documents against policy |
| `generating_review` | 5 | Producing the structured review output |

---

## 7. Webhook Specification

Webhooks allow external systems to receive real-time notifications as a review progresses, without polling. The system posts signed JSON payloads to URLs registered at review submission time.

### 7.1 Webhook Delivery

When a webhook URL is provided in the `POST /api/v1/reviews` request, the system will POST to that URL for each subscribed event type.

**Delivery guarantees:**
- At-least-once delivery with exponential backoff retry
- Retry schedule: 5s, 30s, 2m, 10m, 30m (5 attempts total)
- Timeout: 10 seconds per delivery attempt
- After all retries exhausted, the event is logged and marked as failed (retrievable via API)

### 7.2 Webhook Signing

Every webhook payload is signed using HMAC-SHA256 with the `secret` provided at registration. The signature is sent in the `X-Webhook-Signature-256` header.

**Verification pseudocode:**

```python
import hmac, hashlib

expected = hmac.new(
    key=webhook_secret.encode(),
    msg=raw_request_body,
    digestmod=hashlib.sha256
).hexdigest()

signature = request.headers["X-Webhook-Signature-256"]
assert hmac.compare_digest(f"sha256={expected}", signature)
```

### 7.3 Webhook Headers

Every webhook POST includes:

```
Content-Type: application/json
X-Webhook-Signature-256: sha256=a1b2c3d4...
X-Webhook-Event: review.completed
X-Webhook-Delivery-Id: dlv_01HQY4B...
X-Webhook-Timestamp: 2025-02-05T14:33:12Z
User-Agent: CherwellCycleAgent-Webhook/1.0
```

### 7.4 Webhook Events

#### `review.started`

Fired when the worker picks up the job and begins processing.

```json
{
    "event": "review.started",
    "delivery_id": "dlv_01HQY4BKRN...",
    "timestamp": "2025-02-05T14:30:05Z",
    "data": {
        "review_id": "rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
        "application_ref": "25/01178/REM",
        "phase": "fetching_metadata"
    }
}
```

#### `review.progress`

Fired at each phase transition. Useful for updating a UI or progress tracker.

```json
{
    "event": "review.progress",
    "delivery_id": "dlv_01HQY4CM7X...",
    "timestamp": "2025-02-05T14:31:20Z",
    "data": {
        "review_id": "rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
        "application_ref": "25/01178/REM",
        "phase": "ingesting_documents",
        "phase_number": 3,
        "total_phases": 5,
        "percent_complete": 45,
        "detail": "Ingested 14 of 24 documents"
    }
}
```

#### `review.completed`

Fired when the review is finished. Includes the full review result inline so the consumer doesn't need to call back to the API.

```json
{
    "event": "review.completed",
    "delivery_id": "dlv_01HQY4F2QP...",
    "timestamp": "2025-02-05T14:33:12Z",
    "data": {
        "review_id": "rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
        "application_ref": "25/01178/REM",
        "overall_rating": "red",
        "summary": "The application fails to meet several key requirements...",
        "recommendations_count": 5,
        "issues_count": 8,
        "review_url": "/api/v1/reviews/rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
        "download_url": "/api/v1/reviews/rev_01HQXK7V3WNPB8MTJF2R5ADGX9/download?format=markdown",
        "review": {
            "overall_rating": "red",
            "aspects": [ "..." ],
            "policy_compliance": [ "..." ],
            "recommendations": [ "..." ],
            "suggested_conditions": [ "..." ],
            "full_markdown": "# Cycle Advocacy Review: 25/01178/REM\n..."
        },
        "metadata": {
            "processing_time_seconds": 192,
            "documents_analysed": 22
        }
    }
}
```

#### `review.failed`

Fired if the review fails at any stage.

```json
{
    "event": "review.failed",
    "delivery_id": "dlv_01HQY4GN3M...",
    "timestamp": "2025-02-05T14:31:45Z",
    "data": {
        "review_id": "rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
        "application_ref": "25/01178/REM",
        "error": {
            "code": "scraper_error",
            "message": "Cherwell planning portal returned 503 after 3 retries",
            "failed_at_phase": "downloading_documents"
        }
    }
}
```

### 7.5 Webhook Management Endpoints

#### `GET /api/v1/reviews/{review_id}/webhooks` — List webhook deliveries

Returns the delivery history for a review's webhook, including status and any retry info.

```json
{
    "deliveries": [
        {
            "delivery_id": "dlv_01HQY4BKRN...",
            "event": "review.started",
            "status": "delivered",
            "response_code": 200,
            "delivered_at": "2025-02-05T14:30:05Z",
            "attempts": 1
        },
        {
            "delivery_id": "dlv_01HQY4F2QP...",
            "event": "review.completed",
            "status": "failed",
            "response_code": 503,
            "last_attempt_at": "2025-02-05T15:03:12Z",
            "attempts": 5,
            "next_retry": null
        }
    ]
}
```

#### `POST /api/v1/reviews/{review_id}/webhooks/{delivery_id}/redeliver` — Retry a failed delivery

Manually re-sends a specific webhook event.

### 7.6 Webhook Flow Diagram

```
Agent Worker                  Redis                API Gateway            Consumer
    │                          │                       │                     │
    ├─ publish("review.started")──▶│                   │                     │
    │                          ├──notify──▶│           │                     │
    │                          │           ├─sign payload──▶│               │
    │                          │           │           POST /hooks/cherwell──▶│
    │                          │           │                     │◀──200 OK──┤
    │                          │           │◀──mark delivered────┤           │
    │                          │           │                     │           │
    ├─ publish("review.progress")─▶│       │                     │           │
    │                          ├──notify──▶│                     │           │
    │                          │           ├──POST──────────────────────────▶│
    │                          │           │                     │◀──200 OK──┤
    │                          │           │                     │           │
    ├─ publish("review.completed")─▶│      │                     │           │
    │                          ├──notify──▶│                     │           │
    │                          │           ├──POST──────────────────────────▶│
    │                          │           │                     │◀──500─────┤
    │                          │           ├──schedule retry─────┤           │
    │                          │           │  (5s, 30s, 2m...)  │           │
```

---

## 8. Container Architecture

### 8.1 Docker Compose

```yaml
# docker-compose.yml
version: "3.9"

services:
  api:
    build:
      context: .
      dockerfile: docker/Dockerfile.api
    environment:
      - API_KEYS=${API_KEYS}
      - REDIS_URL=redis://redis:6379/0
      - LOG_LEVEL=${LOG_LEVEL:-INFO}
    ports:
      - "8080:8080"
    depends_on:
      redis:
        condition: service_healthy
    networks:
      - agent-net
    restart: unless-stopped

  worker:
    build:
      context: .
      dockerfile: docker/Dockerfile.worker
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - CLAUDE_MODEL=${CLAUDE_MODEL:-claude-sonnet-4-5-20250929}
      - REDIS_URL=redis://redis:6379/0
      - CHROMA_PERSIST_DIR=/data/chroma
      - RAW_DOCS_DIR=/data/raw
      - OUTPUT_DIR=/data/output
    volumes:
      - chroma_data:/data/chroma
      - raw_docs:/data/raw
      - output:/data/output
    depends_on:
      redis:
        condition: service_healthy
      policy-init:
        condition: service_completed_successfully
    networks:
      - agent-net
    restart: unless-stopped
    deploy:
      replicas: ${WORKER_REPLICAS:-1}  # Scale workers for throughput

  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data
    ports:
      - "6379:6379"         # Expose for local debugging; remove in prod
    networks:
      - agent-net
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5
    restart: unless-stopped

  cherwell-scraper-mcp:
    build:
      context: .
      dockerfile: docker/Dockerfile.scraper
    environment:
      - SCRAPER_RATE_LIMIT=${SCRAPER_RATE_LIMIT:-1.0}
      - SCRAPER_USER_AGENT=${SCRAPER_USER_AGENT:-CherwellCycleReview/1.0}
    volumes:
      - raw_docs:/data/raw
    networks:
      - agent-net
    restart: unless-stopped

  document-store-mcp:
    build:
      context: .
      dockerfile: docker/Dockerfile.docstore
    environment:
      - CHROMA_PERSIST_DIR=/data/chroma
      - EMBEDDING_MODEL=${EMBEDDING_MODEL:-all-MiniLM-L6-v2}
    volumes:
      - chroma_data:/data/chroma
      - raw_docs:/data/raw
    networks:
      - agent-net
    restart: unless-stopped

  policy-kb-mcp:
    build:
      context: .
      dockerfile: docker/Dockerfile.policy
    environment:
      - CHROMA_PERSIST_DIR=/data/chroma
    volumes:
      - chroma_data:/data/chroma
      - policy_docs:/data/policy
    networks:
      - agent-net
    restart: unless-stopped

  policy-init:
    build:
      context: .
      dockerfile: docker/Dockerfile.policy-init
    volumes:
      - chroma_data:/data/chroma
      - policy_docs:/data/policy
    networks:
      - agent-net
    # Runs once to ingest policy documents, then exits

volumes:
  chroma_data:
  raw_docs:
  output:
  policy_docs:
  redis_data:

networks:
  agent-net:
```

### 8.2 Dockerfile Strategy

**Base Image:** `python:3.12-slim` for all services

**Shared Dependencies Layer:**
```dockerfile
# docker/Dockerfile.base
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    chromadb \
    sentence-transformers \
    pymupdf \
    pytesseract \
    httpx \
    beautifulsoup4 \
    mcp \
    fastapi \
    uvicorn[standard] \
    redis[hiredis] \
    arq \
    pydantic \
    ulid-py
```

Each service Dockerfile extends this base with its specific code.

**API Gateway Dockerfile** (`docker/Dockerfile.api`):
```dockerfile
FROM cherwell-base:latest
COPY src/api/ /app/api/
COPY src/shared/ /app/shared/
WORKDIR /app
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

**Worker Dockerfile** (`docker/Dockerfile.worker`):
```dockerfile
FROM cherwell-base:latest
COPY src/worker/ /app/worker/
COPY src/agent/ /app/agent/
COPY src/shared/ /app/shared/
WORKDIR /app
CMD ["python", "-m", "worker.main"]
```

### 8.3 MCP Transport

Within Docker Compose, MCP servers communicate with the agent via **stdio** transport (the agent spawns MCP server processes) or via **SSE** over HTTP within the Docker network. For containerised deployment, **SSE transport** is recommended:

```
Agent ──HTTP/SSE──▶ cherwell-scraper-mcp:3001
Agent ──HTTP/SSE──▶ document-store-mcp:3002
Agent ──HTTP/SSE──▶ policy-kb-mcp:3003
```

---

## 9. Technology Stack

| Layer | Technology | Rationale |
|---|---|---|
| **LLM** | Claude Sonnet 4.5 (via API) | Best balance of intelligence, speed, and cost for structured analysis |
| **Agent Framework** | Anthropic SDK + MCP client | Native tool use; MCP for modular, reusable tool servers |
| **REST API** | FastAPI + Uvicorn | Async-native, auto-generated OpenAPI docs, Pydantic validation |
| **Job Queue** | Redis + arq | Lightweight async job queue; Redis also serves as state store and pub/sub for webhook events |
| **Webhook Dispatch** | httpx (async) + HMAC-SHA256 signing | Async delivery with retry backoff; standard signing for consumer verification |
| **Vector DB** | ChromaDB (persistent, local) | Lightweight, no separate server, Python-native, free |
| **Embeddings** | `all-MiniLM-L6-v2` (sentence-transformers) | Fast CPU inference, 384-dim, good retrieval quality |
| **PDF Extraction** | PyMuPDF (`fitz`) | Fast, handles most planning PDFs well |
| **OCR** | Tesseract | Free, handles scanned documents |
| **Web Scraping** | httpx + BeautifulSoup4 | Async HTTP + HTML parsing |
| **Containerisation** | Docker Compose | Simple multi-service orchestration |
| **ID Generation** | ULID (ulid-py) | Time-sortable, URL-safe unique IDs for review_id and delivery_id |
| **Language** | Python 3.12 | Ecosystem support for all dependencies |

---

## 10. Data Model

### 10.1 ChromaDB Collections

**Collection: `application_docs`**

```python
{
    "id": "25_01178_REM_transport_assessment_chunk_042",
    "embedding": [0.123, ...],  # 384-dim
    "document": "The proposed development will generate approximately 150...",
    "metadata": {
        "application_ref": "25/01178/REM",
        "source_file": "Transport_Assessment_v2.pdf",
        "document_type": "transport_assessment",
        "page_number": 14,
        "chunk_index": 42,
        "ingested_at": "2025-02-05T10:30:00Z"
    }
}
```

**Collection: `policy_docs`**

```python
{
    "id": "LTN_1_20__rev_LTN120_2020_07__ch05_section_5_3__chunk_007",
    "embedding": [0.456, ...],
    "document": "Where motor traffic flows exceed 2,500 PCU per day...",
    "metadata": {
        "source": "LTN_1_20",
        "source_title": "Cycle Infrastructure Design (LTN 1/20)",
        "revision_id": "rev_LTN120_2020_07",
        "version_label": "July 2020",
        "effective_from": "2020-07-27",       # ISO date string
        "effective_to": "",                    # empty string = still in force (ChromaDB doesn't support null)
        "chapter": "5",
        "section": "5.3",
        "section_title": "Separation from motor traffic",
        "page_number": 42,
        "chunk_index": 7,
        "table_ref": "Table 5-2"              # if applicable, else ""
    }
}
```

**Temporal query pattern:** When the agent searches with an `effective_date`, the MCP server applies a ChromaDB `where` filter:

```python
# Pseudocode for effective-date filtering
results = collection.query(
    query_texts=[query],
    n_results=n,
    where={
        "$and": [
            {"effective_from": {"$lte": effective_date}},
            {"$or": [
                {"effective_to": {"$eq": ""}},           # still in force
                {"effective_to": {"$gte": effective_date}}  # was in force on that date
            ]}
        ]
    }
)
```

### 10.2 Application Metadata (JSON on disk)

```json
{
    "reference": "25/01178/REM",
    "address": "Land at ..., Bicester",
    "proposal": "Reserved matters application for ...",
    "applicant": "Example Developments Ltd",
    "agent": "Planning Consultants LLP",
    "status": "Under consideration",
    "date_received": "2025-01-15",
    "date_validated": "2025-01-20",
    "consultation_end": "2025-02-15",
    "documents": [
        {
            "title": "Transport Assessment",
            "type": "transport_assessment",
            "url": "https://planningregister.cherwell.gov.uk/...",
            "filename": "Transport_Assessment_v2.pdf",
            "date_published": "2025-01-15",
            "downloaded": true,
            "ingested": true
        }
    ],
    "fetched_at": "2025-02-05T10:00:00Z"
}
```

### 10.3 Redis Data Structures

Review jobs and webhook state are stored in Redis. This keeps the API gateway stateless and allows multiple workers to process jobs concurrently.

**Job record** (`review:{review_id}`):

```json
{
    "review_id": "rev_01HQXK7V3WNPB8MTJF2R5ADGX9",
    "application_ref": "25/01178/REM",
    "status": "processing",
    "options": { "focus_areas": ["cycle_parking", "cycle_routes"], "output_format": "markdown" },
    "webhook": {
        "url": "https://your-app.example.com/hooks/cherwell",
        "secret_hash": "sha256:a1b2c3...",
        "events": ["review.started", "review.completed", "review.failed"]
    },
    "progress": {
        "phase": "ingesting_documents",
        "phase_number": 3,
        "total_phases": 5,
        "percent_complete": 45,
        "detail": "Ingested 14 of 24 documents"
    },
    "created_at": "2025-02-05T14:30:00Z",
    "started_at": "2025-02-05T14:30:05Z",
    "completed_at": null,
    "error": null,
    "result_key": null
}
```

**Review result** (`review_result:{review_id}`) — stored separately due to size:

```
Key:   review_result:rev_01HQXK7V3WNPB8MTJF2R5ADGX9
Value: (compressed JSON of full review output)
TTL:   30 days
```

**Webhook delivery log** (`webhook_deliveries:{review_id}`) — Redis list:

```json
{
    "delivery_id": "dlv_01HQY4BKRN...",
    "event": "review.started",
    "status": "delivered",
    "response_code": 200,
    "attempts": 1,
    "created_at": "2025-02-05T14:30:05Z",
    "delivered_at": "2025-02-05T14:30:06Z"
}
```

**Job queue**: Uses `arq` (async Redis queue) with the key `arq:queue`:

```
Queue name:  "review_jobs"
Job payload: { "review_id": "rev_...", "application_ref": "25/01178/REM" }
```

**Index for listing** (`reviews_by_status:{status}`) — Redis sorted set, scored by created_at timestamp, for efficient paginated listing by status.

### 10.4 Policy Registry (Redis)

The policy registry is the source of truth for which policy documents and revisions exist. ChromaDB stores the embeddings; Redis stores the catalogue.

**Policy document record** (`policy:{source}`):

```json
{
    "source": "NPPF",
    "title": "National Planning Policy Framework",
    "description": "Government planning policy for England",
    "category": "national_policy",
    "created_at": "2025-02-01T10:00:00Z",
    "updated_at": "2025-02-05T16:00:00Z"
}
```

**Revision record** (`policy_revision:{source}:{revision_id}`):

```json
{
    "source": "NPPF",
    "revision_id": "rev_NPPF_2024_12",
    "version_label": "December 2024",
    "effective_from": "2024-12-12",
    "effective_to": null,
    "status": "active",
    "source_filename": "nppf-december-2024.pdf",
    "file_path": "/data/policy/NPPF/rev_NPPF_2024_12/nppf-december-2024.pdf",
    "file_hash": "sha256:a1b2c3...",
    "file_size_bytes": 2458624,
    "chunk_count": 512,
    "notes": null,
    "ingested_at": "2025-02-01T10:00:00Z",
    "ingestion_metadata": {
        "pages_extracted": 78,
        "tables_detected": 4,
        "ocr_required": false,
        "processing_time_seconds": 34,
        "embedding_model": "all-MiniLM-L6-v2"
    }
}
```

**Revision status values:** `processing`, `active`, `superseded`, `failed`, `deleted`

**Revision index** (`policy_revisions:{source}`) — Redis sorted set, scored by `effective_from` as Unix timestamp. Enables efficient lookup of the active revision for a given date:

```
ZRANGEBYSCORE policy_revisions:NPPF -inf +inf
→ ["rev_NPPF_2021_07", "rev_NPPF_2023_09", "rev_NPPF_2024_12"]
```

**Policy listing index** (`policies_all`) — Redis set of all source slugs, for efficient listing:

```
SMEMBERS policies_all
→ ["LTN_1_20", "NPPF", "MANUAL_FOR_STREETS", "CHERWELL_LP_2031", ...]
```

**Effective date resolution algorithm:**

```python
def get_effective_revision(source: str, date: str) -> str | None:
    """Find the revision in force on a given date."""
    # Get all revisions ordered by effective_from
    revision_ids = redis.zrangebyscore(f"policy_revisions:{source}", "-inf", date_to_score(date))
    if not revision_ids:
        return None
    # The last one whose effective_from ≤ date is the candidate
    candidate = revision_ids[-1]
    revision = redis.hgetall(f"policy_revision:{source}:{candidate}")
    # Check it hasn't expired
    if revision["effective_to"] and revision["effective_to"] < date:
        return None
    return candidate
```

**File storage layout** on the `policy_docs` volume:

```
/data/policy/
├── NPPF/
│   ├── rev_NPPF_2024_12/
│   │   └── nppf-december-2024.pdf
│   ├── rev_NPPF_2023_09/
│   │   └── nppf-september-2023.pdf
│   └── rev_NPPF_2021_07/
│       └── nppf-july-2021.pdf
├── LTN_1_20/
│   └── rev_LTN120_2020_07/
│       └── ltn-1-20-cycle-infrastructure-design.pdf
└── CHERWELL_LP_2040/
    └── rev_CLP2040_2023_09/
        └── cherwell-local-plan-2040-draft.pdf
```

---

## 11. Development Plan

### Phase 1: Foundation & API Skeleton (Weeks 1–2)

| Task | Description | Deliverable |
|---|---|---|
| 1.1 | Project scaffolding, Docker Compose (all services incl. Redis), shared base image | Working container stack (empty services) |
| 1.2 | FastAPI REST API skeleton — `POST /reviews`, `GET /reviews/{id}`, `GET /health` | API accepting requests, returning 202 with job IDs |
| 1.3 | Redis job queue integration (arq) — API enqueues, worker dequeues | End-to-end job flow (stub worker) |
| 1.4 | Webhook delivery framework — signing, retry logic, delivery logging | Webhook POSTs firing to test endpoint |
| 1.5 | Cherwell scraper — reverse-engineer the planning register HTML, build metadata extraction | `get_application_details` tool working |
| 1.6 | Cherwell scraper — document list parsing and download | `download_all_documents` tool working |
| 1.7 | Test scraper against 5–10 real application references | Validated scraper output |

### Phase 2: Document Processing (Weeks 3–4)

| Task | Description | Deliverable |
|---|---|---|
| 2.1 | PDF text extraction pipeline (PyMuPDF + fallback OCR) | Clean text from planning PDFs |
| 2.2 | Chunking strategy — test with real Transport Assessments | Tuned chunk parameters |
| 2.3 | ChromaDB integration — ingest and search | `ingest_document` and `search_application_docs` tools |
| 2.4 | Document type classification (heuristic/filename-based) | Automatic tagging of document types |
| 2.5 | Wire progress events — worker publishes phase transitions to Redis | `review.progress` webhooks firing during document processing |

### Phase 3: Policy Knowledge Base & Management API (Weeks 4–5)

| Task | Description | Deliverable |
|---|---|---|
| 3.1 | Policy registry data model in Redis — documents, revisions, indexes | Redis schema for policy catalogue |
| 3.2 | REST API: `POST/GET /policies`, `POST /policies/{source}/revisions` endpoints | Policy CRUD endpoints working |
| 3.3 | Policy revision upload and async ingestion pipeline — PDF → chunks → ChromaDB with revision metadata | Upload a PDF, get it embedded and searchable |
| 3.4 | Effective date resolution — revision lookup by date, temporal filtering in ChromaDB queries | `GET /policies/effective?date=2024-03-15` working |
| 3.5 | Seed script — initial ingestion of LTN 1/20, NPPF, Local Plan, etc. as first revisions with correct effective dates | `policy_docs` collection populated with versioned data |
| 3.6 | Build policy search MCP tools — revision-aware `search_policy` and `get_policy_section` | MCP tools returning temporally correct results |
| 3.7 | Revision management endpoints — `PATCH`, `DELETE`, `reindex` | Full revision lifecycle via API |
| 3.8 | Test policy retrieval with common cycling planning queries across different effective dates | Validated retrieval quality; correct revision selected for historical applications |

### Phase 4: Agent Integration (Weeks 5–6)

| Task | Description | Deliverable |
|---|---|---|
| 4.1 | Agent core — MCP client connecting to all three servers | Agent can call all tools |
| 4.2 | Agent system prompt and workflow orchestration | End-to-end pipeline working |
| 4.3 | Review output generation — structured Markdown + JSON | Review document produced |
| 4.4 | Wire full lifecycle — API → queue → worker → webhooks → result | Complete `POST /reviews` → `review.completed` webhook flow |
| 4.5 | Structured JSON review output matching API response schema | `GET /reviews/{id}` returns full structured review |

### Phase 5: API Hardening & Testing (Weeks 7–8)

| Task | Description | Deliverable |
|---|---|---|
| 5.1 | Test against 10+ real applications of varying types | Validated reviews |
| 5.2 | Tune system prompt based on review quality | Improved output |
| 5.3 | Add output formatting (Markdown → PDF for download endpoint) | `/download?format=pdf` working |
| 5.4 | API key authentication and rate limiting | Secured endpoints |
| 5.5 | Webhook reliability testing — failure scenarios, retry behaviour | Robust webhook delivery |
| 5.6 | Policy management API testing — upload revisions, verify effective date resolution, reindex | Fully tested policy lifecycle |
| 5.7 | OpenAPI spec generation and documentation | Auto-generated API docs at `/docs` |
| 5.8 | Error handling, retry logic, logging, monitoring | Production-hardened system |
| 5.9 | Worker scaling test — run 2–3 workers, verify concurrent reviews | Horizontally scalable |
| 5.10 | Documentation: README, API guide, webhook integration guide, policy management guide | Developer and user docs |

---

## 12. Project Structure

```
cherwell-cycle-agent/
├── docker/
│   ├── Dockerfile.base
│   ├── Dockerfile.api
│   ├── Dockerfile.worker
│   ├── Dockerfile.scraper
│   ├── Dockerfile.docstore
│   ├── Dockerfile.policy
│   └── Dockerfile.policy-init
├── docker-compose.yml
├── src/
│   ├── api/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI app entry point
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── reviews.py       # POST/GET/LIST/CANCEL review endpoints
│   │   │   ├── policies.py      # Policy document CRUD + revision management
│   │   │   ├── webhooks.py      # Webhook delivery listing and redelivery
│   │   │   └── health.py        # Health check endpoint
│   │   ├── middleware/
│   │   │   ├── __init__.py
│   │   │   ├── auth.py          # API key authentication
│   │   │   └── rate_limit.py    # Rate limiting
│   │   ├── schemas.py           # Pydantic request/response models
│   │   └── dependencies.py      # Redis, config injection
│   ├── worker/
│   │   ├── __init__.py
│   │   ├── main.py              # Worker entry point (arq worker)
│   │   ├── jobs.py              # Review job handler
│   │   ├── policy_jobs.py       # Policy revision ingestion/reindex job handler
│   │   ├── progress.py          # Progress tracking and event publishing
│   │   └── webhook_dispatcher.py # Async webhook delivery with signing and retry
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── orchestrator.py      # Agent workflow logic (called by worker)
│   │   ├── prompts.py           # System prompts and templates
│   │   ├── review_template.py   # Output formatting (Markdown + structured JSON)
│   │   └── config.py
│   ├── mcp_servers/
│   │   ├── cherwell_scraper/
│   │   │   ├── __init__.py
│   │   │   ├── server.py        # MCP server definition
│   │   │   ├── scraper.py       # Cherwell portal scraping logic
│   │   │   ├── models.py        # Data models
│   │   │   └── parsers.py       # HTML parsing
│   │   ├── document_store/
│   │   │   ├── __init__.py
│   │   │   ├── server.py        # MCP server definition
│   │   │   ├── processor.py     # PDF extraction, OCR, chunking
│   │   │   ├── embeddings.py    # Embedding generation
│   │   │   └── chroma_client.py # ChromaDB interface
│   │   └── policy_kb/
│   │       ├── __init__.py
│   │       ├── server.py        # MCP server definition
│   │       ├── ingest.py        # Policy document ingestion
│   │       └── retriever.py     # Policy search logic
│   └── shared/
│       ├── __init__.py
│       ├── models.py            # Shared data models (ReviewStatus, PolicyRevision, etc.)
│       ├── redis_client.py      # Redis connection and helpers
│       ├── policy_registry.py   # Policy/revision CRUD operations against Redis
│       ├── webhook_signing.py   # HMAC-SHA256 signing utilities
│       └── utils.py
├── data/
│   └── policy/                  # Policy PDFs (gitignored, fetched at build)
├── scripts/
│   ├── fetch_policies.sh        # Download policy documents
│   └── init_policy_db.py        # One-time policy ingestion
├── tests/
│   ├── test_api/
│   │   ├── test_reviews.py      # API endpoint tests
│   │   ├── test_policies.py     # Policy CRUD and revision management tests
│   │   ├── test_webhooks.py     # Webhook delivery tests
│   │   └── test_auth.py         # Authentication tests
│   ├── test_worker/
│   │   ├── test_jobs.py         # Worker job processing tests
│   │   ├── test_policy_jobs.py  # Policy ingestion/reindex job tests
│   │   └── test_webhook_dispatch.py  # Signing, retry logic tests
│   ├── test_shared/
│   │   ├── test_policy_registry.py   # Policy registry Redis operations tests
│   │   └── test_effective_date.py    # Effective date resolution tests
│   ├── test_scraper.py
│   ├── test_processor.py
│   ├── test_policy_retrieval.py
│   └── fixtures/                # Sample HTML, PDFs for testing
├── .env.example
├── requirements.txt
├── pyproject.toml
└── README.md
```

---

## 13. Key Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| **Cherwell portal changes HTML structure** | Scraper breaks | Use resilient selectors; add integration tests; alert on parse failures |
| **Scanned PDFs with poor OCR quality** | Missed content | Flag low-confidence OCR; store original for human review; use Claude's vision API as OCR fallback for key documents |
| **Policy documents updated** | Stale policy references | Version-aware policy registry; upload new revisions via API with effective dates; agent auto-selects correct revision based on application validation date |
| **Large applications (100+ documents)** | Slow processing, high token cost | Prioritise key document types (Transport Assessment, D&A Statement); allow selective review |
| **Rate limiting / blocking by Cherwell portal** | Cannot fetch documents | Polite scraping; caching; respect robots.txt; consider contacting the council |
| **LLM hallucination of policy references** | Incorrect citations | Always ground citations in RAG results; validate policy refs against known index |
| **Overlapping revision effective dates** | Wrong policy version cited in review | Strict validation on upload — reject overlapping ranges; `GET /policies/effective` endpoint for auditing; integration tests with historical applications |
| **Policy revision ingestion failure** | New policy version unavailable | Revision status tracks `processing → active/failed`; previous revision remains active until new one succeeds; reindex endpoint for retry |
| **Webhook consumer downtime** | Missed notifications | At-least-once delivery with 5-attempt retry over 30 mins; delivery log and manual redeliver endpoint |
| **Webhook secret leakage** | Spoofed callbacks | Store only hashed secrets in Redis; rotate via new review submission; HTTPS-only webhook URLs |
| **Redis data loss** | Lost job state | Redis AOF persistence; review results also written to disk; stateless API can recover from ChromaDB |
| **Concurrent reviews for same application** | Wasted resources, race conditions | 409 Conflict response when duplicate review is queued/processing; deduplication check in API |

---

## 14. Future Enhancements

- **Batch processing endpoint:** `POST /api/v1/reviews/batch` — accept multiple application refs, return a batch ID, webhook when all complete
- **Weekly list monitor:** Scheduled job that scrapes the Cherwell weekly list, auto-submits reviews, posts webhooks for new applications of interest
- **Server-Sent Events (SSE) streaming:** `GET /api/v1/reviews/{id}/stream` — real-time progress streaming as an alternative to polling or webhooks
- **Drawing analysis:** Use Claude's vision capabilities to assess site plans and layout drawings for cycle provision
- **Comparison mode:** Compare an application against similar approved schemes
- **Consultation response drafting:** Generate a formal response letter ready for submission before the consultation deadline
- **Multi-authority support:** Extend the scraper to support other Oxfordshire councils (Oxford City, South Oxfordshire, Vale of White Horse, West Oxfordshire) — abstract the scraper MCP behind a common interface
- **Integration with Oxfordshire LCWIP map data:** Overlay application sites on the LCWIP network to identify connections
- **Slack/Discord integration:** Webhook consumer that posts review summaries to advocacy group chat channels
- **Policy change monitoring:** Automated scraping of gov.uk and council websites to detect when policy documents are updated, with alerts to upload new revisions
- **Policy diff analysis:** When a new revision is uploaded, automatically compare it against the previous revision and summarise changes relevant to cycling advocacy
- **Admin dashboard:** Simple web UI for viewing review history, webhook delivery status, policy revision timeline, and system health

---

## 15. Environment Variables

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_MODEL=claude-sonnet-4-5-20250929

# API
API_KEYS=sk-cycle-key1,sk-cycle-key2          # Comma-separated valid API keys
API_RATE_LIMIT=60                               # Requests per minute per key

# Redis
REDIS_URL=redis://redis:6379/0

# Worker
WORKER_REPLICAS=1                               # Number of worker containers
CHROMA_PERSIST_DIR=/data/chroma
RAW_DOCS_DIR=/data/raw
OUTPUT_DIR=/data/output
POLICY_DOCS_DIR=/data/policy
EMBEDDING_MODEL=all-MiniLM-L6-v2

# Scraper
SCRAPER_RATE_LIMIT=1.0                          # Seconds between requests
SCRAPER_USER_AGENT="CherwellCycleReview/1.0 (cycling-advocacy-tool)"

# Webhooks
WEBHOOK_TIMEOUT=10                              # Seconds per delivery attempt
WEBHOOK_MAX_RETRIES=5                           # Total retry attempts
WEBHOOK_RETRY_BACKOFF=5,30,120,600,1800         # Retry delays in seconds
WEBHOOK_REQUIRE_HTTPS=true                      # Reject non-HTTPS webhook URLs in prod

# General
LOG_LEVEL=INFO
REVIEW_RESULT_TTL_DAYS=30                       # How long to keep results in Redis
```