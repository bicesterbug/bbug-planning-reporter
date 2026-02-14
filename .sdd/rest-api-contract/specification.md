# Specification: REST API Contract

**Version:** 1.0
**Date:** 2026-02-14
**Status:** As-Built

---

## Problem Statement

The Cherwell Cycle Advocacy Agent exposes multiple internal capabilities (document scraping, policy knowledge, AI review generation, letter drafting) through a set of worker processes and MCP servers. External consumers -- primarily the bbug-website (Vercel) and potentially other integrators -- need a single, stable, well-documented HTTP interface to submit review requests, retrieve results, manage policy documents, configure route-assessment destinations, and receive asynchronous event notifications. Without a clearly specified REST API contract, consumers risk coupling to internal implementation details and breaking when the system evolves.

## Beneficiaries

**Primary:**
- bbug-website frontend (Vercel) consuming all review, letter, and policy endpoints
- Cycling advocacy group administrators managing policy documents and destinations

**Secondary:**
- Third-party integrators building automation on top of the API
- System operators monitoring health and diagnosing issues via request tracing
- Developers extending the API or building new consumers

---

## Outcomes

**Must Haves**
- Every public HTTP endpoint fully documented with method, path, request/response schemas, status codes, and example payloads
- Authentication, rate limiting, error handling, and request tracing behaviour specified
- Webhook event contract specified with envelope structure, event types, and delivery guarantees
- Clear delineation of what is and is not part of the REST API surface

**Nice-to-haves**
- OpenAPI schema auto-generated from these definitions (already implemented at `/openapi.json`)
- Versioning strategy for future breaking changes

---

## Explicitly Out of Scope

- **MCP server tools** -- Cherwell Scraper MCP, Document Store MCP, and Policy KB MCP are internal tool interfaces documented in their own specifications
- **Worker internals** -- Job processing logic, agent orchestration, document ingestion pipelines, and PDF text extraction are implementation details behind the queue boundary
- **Database schemas** -- Redis key structures and ChromaDB collection layouts are internal storage concerns
- **Agent orchestration logic** -- LLM prompt chains, tool selection, and review generation workflow
- **Frontend implementation** -- bbug-website rendering, caching, and client-side state management

---

## Functional Requirements

### [rest-api-contract:FR-001] Review Submission

**Description:** The API must accept a POST request to create a new review job for a Cherwell planning application. The request body contains a mandatory application reference and optional review configuration. The system validates the reference format (`YY/NNNNN/XXX`), checks for duplicate active reviews, creates a job record in Redis, enqueues it to the arq worker queue, and returns a 202 Accepted response with the review ID, status, and HATEOAS links.

**Endpoint:** `POST /api/v1/reviews`

**Request Body:**
```json
{
  "application_ref": "25/01178/REM",
  "options": {
    "focus_areas": ["cycle_parking", "cycle_routes"],
    "output_format": "markdown",
    "include_policy_matrix": true,
    "include_suggested_conditions": true,
    "include_consultation_responses": false,
    "include_public_comments": false,
    "destination_ids": ["dest_bicester_north", "dest_bicester_village"]
  }
}
```

**Request Fields:**
| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `application_ref` | string | Yes | -- | Cherwell reference matching `^\d{2}/\d{4,5}/[A-Z]{1,4}$` |
| `options.focus_areas` | string[] \| null | No | null | Specific areas: cycle_parking, cycle_routes, junctions, permeability |
| `options.output_format` | string | No | "markdown" | Output format: "markdown" or "json" |
| `options.include_policy_matrix` | boolean | No | true | Include policy compliance matrix |
| `options.include_suggested_conditions` | boolean | No | true | Include suggested planning conditions |
| `options.include_consultation_responses` | boolean | No | false | Include statutory consultee responses as LLM evidence |
| `options.include_public_comments` | boolean | No | false | Include public comments/objection letters as LLM evidence |
| `options.destination_ids` | string[] \| null | No | null | Destination IDs for route assessment. null = all destinations, [] = skip assessment |

**Response (202 Accepted):**
```json
{
  "review_id": "rev_01JMABCDEF1234567890AB",
  "application_ref": "25/01178/REM",
  "status": "queued",
  "created_at": "2026-02-14T10:30:00Z",
  "estimated_duration_seconds": 180,
  "links": {
    "self": "/api/v1/reviews/rev_01JMABCDEF1234567890AB",
    "status": "/api/v1/reviews/rev_01JMABCDEF1234567890AB/status",
    "cancel": "/api/v1/reviews/rev_01JMABCDEF1234567890AB/cancel"
  }
}
```

**Error Responses:**
- **400** `invalid_reference` -- Application reference does not match expected format
- **409** `review_already_exists` -- An active (queued or processing) review already exists for this application reference
- **422** `validation_error` -- Request body fails Pydantic validation

**Examples:**
- Positive: Submit `{"application_ref": "25/01178/REM"}` with no options returns 202 with review_id
- Positive: Submit with full options including `destination_ids: []` to skip route assessment returns 202
- Negative: Submit `{"application_ref": "INVALID"}` returns 400 with validation error
- Edge case: Submit same `application_ref` while first review is still queued returns 409

---

### [rest-api-contract:FR-002] Review Listing

**Description:** The API must provide a paginated list of review jobs with optional filtering by status and application reference. Results are returned in reverse chronological order.

**Endpoint:** `GET /api/v1/reviews`

**Query Parameters:**
| Parameter | Type | Required | Default | Constraints | Description |
|---|---|---|---|---|---|
| `status` | string | No | -- | One of: queued, processing, completed, failed, cancelled | Filter by review status |
| `application_ref` | string | No | -- | -- | Filter by application reference |
| `limit` | integer | No | 20 | 1-100 | Maximum results per page |
| `offset` | integer | No | 0 | >= 0 | Pagination offset |

**Response (200 OK):**
```json
{
  "reviews": [
    {
      "review_id": "rev_01JMABCDEF1234567890AB",
      "application_ref": "25/01178/REM",
      "status": "completed",
      "overall_rating": "compliant",
      "created_at": "2026-02-14T10:30:00Z",
      "completed_at": "2026-02-14T10:33:45Z"
    }
  ],
  "total": 42,
  "limit": 20,
  "offset": 0
}
```

**Error Responses:**
- **400** `invalid_status` -- Status filter value not in allowed enum; response `details` includes `valid_statuses` array

**Examples:**
- Positive: `GET /api/v1/reviews?status=completed&limit=10` returns completed reviews, 10 per page
- Positive: `GET /api/v1/reviews?application_ref=25/01178/REM` returns all reviews for that application
- Edge case: `GET /api/v1/reviews?offset=9999` returns `{"reviews": [], "total": 42, "limit": 20, "offset": 9999}`
- Negative: `GET /api/v1/reviews?status=bogus` returns 400 with valid_statuses list

---

### [rest-api-contract:FR-003] Review Detail

**Description:** The API must return the full review result for a completed review, or the current status with progress information for an in-progress review. When the review is complete, the response includes application metadata, review content (aspects, policy compliance, recommendations, suggested conditions, route assessments, key documents), processing metadata, and site boundary GeoJSON.

**Endpoint:** `GET /api/v1/reviews/{review_id}`

**Path Parameters:**
| Parameter | Type | Description |
|---|---|---|
| `review_id` | string | Review identifier (e.g., `rev_01JMABCDEF1234567890AB`) |

**Response (200 OK) -- Completed Review:**
```json
{
  "review_id": "rev_01JMABCDEF1234567890AB",
  "application_ref": "25/01178/REM",
  "status": "completed",
  "created_at": "2026-02-14T10:30:00Z",
  "started_at": "2026-02-14T10:30:05Z",
  "completed_at": "2026-02-14T10:33:45Z",
  "progress": null,
  "application": {
    "reference": "25/01178/REM",
    "address": "Land at NW Bicester",
    "proposal": "Reserved matters for 120 dwellings",
    "applicant": "Example Homes Ltd",
    "status": "Pending Consideration",
    "consultation_end": "2026-03-01",
    "documents_fetched": 45,
    "documents_ingested": 38
  },
  "review": {
    "overall_rating": "non_compliant",
    "summary": "The application fails to provide adequate cycle parking...",
    "key_documents": [
      {
        "title": "Transport Assessment",
        "category": "Transport & Access",
        "summary": "Details cycle route connections but omits LTN 1/20 standards.",
        "url": "https://planningregister.cherwell.gov.uk/..."
      }
    ],
    "aspects": [
      {
        "name": "Cycle Parking",
        "rating": "non_compliant",
        "key_issue": "Sheffield stands only, no covered secure parking",
        "detail": "The proposed cycle parking does not meet...",
        "policy_refs": ["LTN_1_20:s11.2", "CHERWELL_LP:Policy BSC1"]
      }
    ],
    "policy_compliance": [
      {
        "requirement": "Secure covered cycle parking for residents",
        "policy_source": "LTN_1_20",
        "compliant": false,
        "notes": "Only Sheffield stands proposed"
      }
    ],
    "recommendations": [
      "Provide secure, covered cycle parking at 1 space per bedroom",
      "Widen shared-use path along Buckingham Road to 3m"
    ],
    "suggested_conditions": [
      "Prior to occupation, a revised Cycle Parking Strategy shall be submitted..."
    ],
    "route_assessments": [
      {
        "destination": "Bicester North Station",
        "destination_id": "dest_bicester_north",
        "distance_m": 2400,
        "duration_minutes": 9.5,
        "provision_breakdown": {"protected_lane": 0.3, "shared_use": 0.5, "on_road": 0.2},
        "score": {"overall": 62, "safety": 55, "directness": 78},
        "issues": [{"location": "A4095 junction", "severity": "high", "description": "No cycle phase at signals"}],
        "s106_suggestions": [{"item": "Toucan crossing at A4095", "estimated_cost": 150000}]
      }
    ],
    "full_markdown": "# Cycle Advocacy Review: 25/01178/REM\n\n..."
  },
  "metadata": {
    "model": "claude-sonnet-4-20250514",
    "total_tokens_used": 45000,
    "processing_time_seconds": 220,
    "documents_analysed": 38,
    "policy_sources_referenced": 4,
    "policy_effective_date": "2026-02-14",
    "policy_revisions_used": [
      {"source": "LTN_1_20", "revision_id": "rev_LTN_1_20_2020_07", "version_label": "July 2020"},
      {"source": "NPPF", "revision_id": "rev_NPPF_2024_12", "version_label": "December 2024"}
    ]
  },
  "site_boundary": {
    "type": "FeatureCollection",
    "features": [
      {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[...]]]}},
      {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-1.15, 51.89]}}
    ]
  },
  "error": null
}
```

**Response (200 OK) -- Processing Review:**
```json
{
  "review_id": "rev_01JMABCDEF1234567890AB",
  "application_ref": "25/01178/REM",
  "status": "processing",
  "created_at": "2026-02-14T10:30:00Z",
  "started_at": "2026-02-14T10:30:05Z",
  "completed_at": null,
  "progress": {
    "phase": "downloading_documents",
    "phase_number": 3,
    "total_phases": 8,
    "percent_complete": 35,
    "detail": "Downloaded 15 of 42 documents"
  },
  "application": null,
  "review": null,
  "metadata": null,
  "site_boundary": null,
  "error": null
}
```

**Error Responses:**
- **404** `review_not_found` -- No review exists with the given ID

**Examples:**
- Positive: GET completed review returns full result with all nested objects populated
- Positive: GET processing review returns status and progress, with review/application/metadata as null
- Positive: GET failed review returns status "failed" with error object populated
- Negative: GET with non-existent review_id returns 404

---

### [rest-api-contract:FR-004] Review Status

**Description:** The API must provide a lightweight endpoint returning only the review status and progress information. This is optimised for polling during review processing.

**Endpoint:** `GET /api/v1/reviews/{review_id}/status`

**Response (200 OK):**
```json
{
  "review_id": "rev_01JMABCDEF1234567890AB",
  "status": "processing",
  "progress": {
    "phase": "analysing_application",
    "phase_number": 5,
    "total_phases": 8,
    "percent_complete": 60,
    "detail": "Analysing transport assessment"
  }
}
```

**Processing Phases (in order):**
1. `fetching_metadata`
2. `filtering_documents`
3. `downloading_documents`
4. `ingesting_documents`
5. `analysing_application`
6. `assessing_routes`
7. `generating_review`
8. `verifying_review`

**Error Responses:**
- **404** `review_not_found`

**Examples:**
- Positive: GET status for queued review returns `{"status": "queued", "progress": null}`
- Positive: GET status for processing review returns progress with phase details
- Positive: GET status for completed review returns `{"status": "completed", "progress": null}`
- Negative: GET status for non-existent ID returns 404

---

### [rest-api-contract:FR-005] Review Cancel

**Description:** The API must allow cancellation of a review that is queued or currently processing. Reviews in terminal states (completed, failed, cancelled) cannot be cancelled and return 409.

**Endpoint:** `POST /api/v1/reviews/{review_id}/cancel`

**Request Body:** None

**Response (200 OK):**
```json
{
  "review_id": "rev_01JMABCDEF1234567890AB",
  "status": "cancelled",
  "progress": null
}
```

**Error Responses:**
- **404** `review_not_found`
- **409** `cannot_cancel` -- Review is in a terminal state (completed, failed, or already cancelled); `details` includes `current_status`

**Examples:**
- Positive: Cancel a queued review returns 200 with status "cancelled"
- Positive: Cancel a processing review returns 200 with status "cancelled"
- Negative: Cancel a completed review returns 409 with current_status "completed"
- Negative: Cancel non-existent review returns 404

---

### [rest-api-contract:FR-006] Review Download

**Description:** The API must provide a download endpoint for completed reviews in multiple formats: Markdown, JSON, and PDF. The response includes a `Content-Disposition` header for browser download. Non-completed reviews return 400.

**Endpoint:** `GET /api/v1/reviews/{review_id}/download`

**Query Parameters:**
| Parameter | Type | Required | Default | Values | Description |
|---|---|---|---|---|---|
| `format` | string | No | "markdown" | markdown, json, pdf | Output format |

**Response (200 OK):**

| Format | Content-Type | Filename Pattern |
|---|---|---|
| markdown | `text/markdown` | `review-{review_id}.md` |
| json | `application/json` | `review-{review_id}.json` |
| pdf | `application/pdf` | `review-{review_id}.pdf` |

All responses include `Content-Disposition: attachment; filename="review-{review_id}.{ext}"`.

**Error Responses:**
- **400** `review_incomplete` -- Review status is not "completed"; `details` includes current `status`
- **404** `review_not_found`

**Examples:**
- Positive: `GET /api/v1/reviews/{id}/download` (no format param) returns Markdown
- Positive: `GET /api/v1/reviews/{id}/download?format=pdf` returns PDF binary
- Positive: `GET /api/v1/reviews/{id}/download?format=json` returns full result JSON
- Negative: Download a queued review returns 400 with status "queued"
- Negative: Download non-existent review returns 404

---

### [rest-api-contract:FR-007] Site Boundary

**Description:** The API must provide a dedicated endpoint returning the site boundary as a GeoJSON FeatureCollection with `application/geo+json` content type. The FeatureCollection contains up to two features: the site polygon and a centroid point. Returns 404 if no boundary data is available.

**Endpoint:** `GET /api/v1/reviews/{review_id}/site-boundary`

**Response (200 OK):**
```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[-1.153, 51.893], [-1.151, 51.893], [-1.151, 51.891], [-1.153, 51.891], [-1.153, 51.893]]]
      },
      "properties": {}
    },
    {
      "type": "Feature",
      "geometry": {
        "type": "Point",
        "coordinates": [-1.152, 51.892]
      },
      "properties": {}
    }
  ]
}
```

Content-Type: `application/geo+json`

**Error Responses:**
- **404** `review_not_found` -- Review does not exist
- **404** `site_boundary_not_found` -- Review exists but has no site boundary data (e.g., review not yet completed, or scraper could not extract boundary)

**Examples:**
- Positive: GET site boundary for completed review with boundary data returns GeoJSON FeatureCollection
- Negative: GET site boundary for a review that has not completed returns 404 `site_boundary_not_found`
- Negative: GET site boundary for non-existent review returns 404 `review_not_found`

---

### [rest-api-contract:FR-008] Letter Generation

**Description:** The API must accept a request to generate a consultation response letter for a completed review. The caller specifies the advocacy group's stance, tone preference, optional case officer name, and optional letter date. The system returns 202 with a letter ID and enqueues a background generation job. The review must be in "completed" status.

**Endpoint:** `POST /api/v1/reviews/{review_id}/letter`

**Request Body:**
```json
{
  "stance": "object",
  "tone": "formal",
  "case_officer": "Ms J. Smith",
  "letter_date": "2026-02-14"
}
```

**Request Fields:**
| Field | Type | Required | Default | Values | Description |
|---|---|---|---|---|---|
| `stance` | string | Yes | -- | object, support, conditional, neutral | Group's position on the application |
| `tone` | string | No | "formal" | formal, accessible | Letter writing style |
| `case_officer` | string \| null | No | null | -- | Override case officer name |
| `letter_date` | date \| null | No | null | YYYY-MM-DD | Letter date; defaults to generation date |

**Response (202 Accepted):**
```json
{
  "letter_id": "ltr_01JMXYZ1234567890ABCDE",
  "review_id": "rev_01JMABCDEF1234567890AB",
  "status": "generating",
  "created_at": "2026-02-14T11:00:00Z",
  "links": {
    "self": "/api/v1/letters/ltr_01JMXYZ1234567890ABCDE"
  }
}
```

**Error Responses:**
- **400** `review_incomplete` -- Review status is not "completed"; `details` includes `current_status`
- **404** `review_not_found`
- **422** `validation_error` -- Invalid stance, tone, or date format

**Examples:**
- Positive: Generate objection letter for completed review returns 202 with letter_id
- Positive: Generate with only `stance` (minimal required field) uses defaults for tone and date
- Negative: Generate letter for a processing review returns 400 with current_status "processing"
- Negative: Generate letter for non-existent review returns 404

---

### [rest-api-contract:FR-009] Letter Retrieval

**Description:** The API must return a letter by its ID, including the full letter content (as Markdown) when generation is complete, or the current status when still generating. Failed letters include error information.

**Endpoint:** `GET /api/v1/letters/{letter_id}`

**Response (200 OK) -- Completed:**
```json
{
  "letter_id": "ltr_01JMXYZ1234567890ABCDE",
  "review_id": "rev_01JMABCDEF1234567890AB",
  "application_ref": "25/01178/REM",
  "status": "completed",
  "stance": "object",
  "tone": "formal",
  "case_officer": "Ms J. Smith",
  "letter_date": "2026-02-14",
  "content": "Dear Ms Smith,\n\nI am writing on behalf of Bicester Bike Users' Group...",
  "metadata": {
    "model": "claude-sonnet-4-20250514",
    "input_tokens": 12000,
    "output_tokens": 1500,
    "processing_time_seconds": 8.5
  },
  "error": null,
  "created_at": "2026-02-14T11:00:00Z",
  "completed_at": "2026-02-14T11:00:09Z"
}
```

**Response (200 OK) -- Generating:**
```json
{
  "letter_id": "ltr_01JMXYZ1234567890ABCDE",
  "review_id": "rev_01JMABCDEF1234567890AB",
  "application_ref": "25/01178/REM",
  "status": "generating",
  "stance": "object",
  "tone": "formal",
  "case_officer": null,
  "letter_date": null,
  "content": null,
  "metadata": null,
  "error": null,
  "created_at": "2026-02-14T11:00:00Z",
  "completed_at": null
}
```

**Error Responses:**
- **404** `letter_not_found`

**Examples:**
- Positive: GET completed letter returns full content and metadata
- Positive: GET generating letter returns status "generating" with content as null
- Positive: GET failed letter returns status "failed" with error object populated
- Negative: GET non-existent letter_id returns 404

---

### [rest-api-contract:FR-010] Policy Registration

**Description:** The API must accept registration of a new policy document with a unique source slug in UPPER_SNAKE_CASE format. The policy is created with no revisions; PDF revisions are uploaded separately. Returns 201 on success.

**Endpoint:** `POST /api/v1/policies`

**Request Body:**
```json
{
  "source": "LTN_1_20",
  "title": "Cycle Infrastructure Design (LTN 1/20)",
  "description": "Department for Transport guidance on cycle infrastructure",
  "category": "national_guidance"
}
```

**Request Fields:**
| Field | Type | Required | Values / Constraints | Description |
|---|---|---|---|---|
| `source` | string | Yes | `^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$` | Unique slug in UPPER_SNAKE_CASE |
| `title` | string | Yes | -- | Human-readable title |
| `description` | string \| null | No | -- | Policy description |
| `category` | string | Yes | national_policy, national_guidance, local_plan, local_guidance, county_strategy, supplementary | Policy category |

**Response (201 Created):**
```json
{
  "source": "LTN_1_20",
  "title": "Cycle Infrastructure Design (LTN 1/20)",
  "description": "Department for Transport guidance on cycle infrastructure",
  "category": "national_guidance",
  "revisions": [],
  "current_revision": null,
  "revision_count": 0,
  "created_at": "2026-02-14T09:00:00Z",
  "updated_at": null
}
```

**Error Responses:**
- **409** `policy_already_exists` -- A policy with this source slug already exists
- **422** `validation_error` -- Invalid source format or missing required fields

**Examples:**
- Positive: Register `{"source": "NPPF", "title": "National Planning Policy Framework", "category": "national_policy"}` returns 201
- Negative: Register with source "nppf" (lowercase) returns 422 validation error
- Negative: Register with duplicate source returns 409

---

### [rest-api-contract:FR-011] Policy Listing

**Description:** The API must return a list of all registered policy documents with optional filtering by category and source. Each summary includes the current active revision and total revision count.

**Endpoint:** `GET /api/v1/policies`

**Query Parameters:**
| Parameter | Type | Required | Description |
|---|---|---|---|
| `category` | string | No | Filter by policy category enum value |
| `source` | string | No | Filter by source slug (partial match) |

**Response (200 OK):**
```json
{
  "policies": [
    {
      "source": "LTN_1_20",
      "title": "Cycle Infrastructure Design (LTN 1/20)",
      "category": "national_guidance",
      "current_revision": {
        "revision_id": "rev_LTN_1_20_2020_07",
        "version_label": "July 2020",
        "effective_from": "2020-07-27",
        "effective_to": null,
        "status": "active",
        "chunk_count": 160,
        "ingested_at": "2026-02-10T14:00:00Z"
      },
      "revision_count": 1
    }
  ],
  "total": 6
}
```

**Examples:**
- Positive: `GET /api/v1/policies` returns all policies
- Positive: `GET /api/v1/policies?category=national_guidance` returns only national guidance policies
- Edge case: `GET /api/v1/policies?category=supplementary` with no supplementary policies returns `{"policies": [], "total": 0}`

---

### [rest-api-contract:FR-012] Policy Detail

**Description:** The API must return a single policy document with its full list of revisions (ordered by effective_from descending), the currently active revision, and total revision count.

**Endpoint:** `GET /api/v1/policies/{source}`

**Response (200 OK):**
```json
{
  "source": "NPPF",
  "title": "National Planning Policy Framework",
  "description": "The government's planning policies for England",
  "category": "national_policy",
  "revisions": [
    {
      "revision_id": "rev_NPPF_2024_12",
      "version_label": "December 2024",
      "effective_from": "2024-12-12",
      "effective_to": null,
      "status": "active",
      "chunk_count": 73,
      "ingested_at": "2026-02-10T14:05:00Z"
    },
    {
      "revision_id": "rev_NPPF_2023_09",
      "version_label": "September 2023",
      "effective_from": "2023-09-05",
      "effective_to": "2024-12-11",
      "status": "superseded",
      "chunk_count": 73,
      "ingested_at": "2026-02-10T14:10:00Z"
    }
  ],
  "current_revision": {
    "revision_id": "rev_NPPF_2024_12",
    "version_label": "December 2024",
    "effective_from": "2024-12-12",
    "effective_to": null,
    "status": "active",
    "chunk_count": 73,
    "ingested_at": "2026-02-10T14:05:00Z"
  },
  "revision_count": 2,
  "created_at": "2026-02-10T14:00:00Z",
  "updated_at": "2026-02-10T14:10:00Z"
}
```

**Error Responses:**
- **404** `policy_not_found`

**Examples:**
- Positive: GET policy with multiple revisions returns all revisions ordered by date
- Positive: GET newly registered policy with no revisions returns empty revisions array
- Negative: GET non-existent source returns 404

---

### [rest-api-contract:FR-013] Policy Update

**Description:** The API must allow partial updates to policy document metadata (title, description, category) using PATCH semantics. Only provided fields are updated; omitted fields remain unchanged.

**Endpoint:** `PATCH /api/v1/policies/{source}`

**Request Body:**
```json
{
  "title": "Updated Title",
  "description": "Updated description",
  "category": "local_guidance"
}
```

All fields are optional. At least one must be provided.

**Response (200 OK):** Returns the full `PolicyDocumentDetail` object (same shape as FR-012).

**Error Responses:**
- **404** `policy_not_found`

**Examples:**
- Positive: PATCH with only `{"title": "New Title"}` updates title, preserves other fields
- Positive: PATCH with `{"category": "supplementary"}` changes category only
- Negative: PATCH non-existent source returns 404

---

### [rest-api-contract:FR-014] Policy Effective Snapshot

**Description:** The API must return a snapshot showing which policy revision was in force for each registered policy on a given date. Policies whose earliest revision starts after the query date are listed in `policies_not_yet_effective`.

**Endpoint:** `GET /api/v1/policies/effective?date=YYYY-MM-DD`

**Query Parameters:**
| Parameter | Type | Required | Description |
|---|---|---|---|
| `date` | date (YYYY-MM-DD) | Yes | Date to resolve effective revisions for |

**Response (200 OK):**
```json
{
  "effective_date": "2024-01-15",
  "policies": [
    {
      "source": "NPPF",
      "title": "National Planning Policy Framework",
      "category": "national_policy",
      "effective_revision": {
        "revision_id": "rev_NPPF_2023_09",
        "version_label": "September 2023",
        "effective_from": "2023-09-05",
        "effective_to": "2024-12-11",
        "status": "superseded",
        "chunk_count": 73,
        "ingested_at": "2026-02-10T14:10:00Z"
      }
    },
    {
      "source": "LTN_1_20",
      "title": "Cycle Infrastructure Design (LTN 1/20)",
      "category": "national_guidance",
      "effective_revision": {
        "revision_id": "rev_LTN_1_20_2020_07",
        "version_label": "July 2020",
        "effective_from": "2020-07-27",
        "effective_to": null,
        "status": "active",
        "chunk_count": 160,
        "ingested_at": "2026-02-10T14:00:00Z"
      }
    }
  ],
  "policies_not_yet_effective": []
}
```

**Error Responses:**
- **400** -- Invalid date format (non-parseable as YYYY-MM-DD)
- **422** `validation_error` -- Missing required `date` query parameter

**Examples:**
- Positive: Query date 2024-01-15 resolves NPPF to September 2023 revision (before December 2024 took effect)
- Positive: Query date 2025-01-01 resolves NPPF to December 2024 revision
- Edge case: Query date before any revision's effective_from lists that policy in `policies_not_yet_effective`

---

### [rest-api-contract:FR-015] Revision Upload

**Description:** The API must accept a multipart form upload to create a new policy revision with a PDF file. The file is validated as PDF, saved to disk, and the revision is registered in the policy registry. Overlapping effective date ranges are rejected. If the new revision supersedes an existing open-ended revision, the system automatically sets the previous revision's `effective_to` and reports this as a side effect.

**Endpoint:** `POST /api/v1/policies/{source}/revisions`

**Request (multipart/form-data):**
| Field | Type | Required | Description |
|---|---|---|---|
| `file` | file (PDF) | Yes | The PDF file to upload |
| `version_label` | string | Yes | Human-readable version (e.g., "December 2024") |
| `effective_from` | date | Yes | Date from which revision is in force (YYYY-MM-DD) |
| `effective_to` | date \| null | No | Date until which revision is in force (null = open-ended / currently in force) |
| `notes` | string \| null | No | Notes about this revision |

**Response (202 Accepted):**
```json
{
  "source": "NPPF",
  "revision_id": "rev_NPPF_2024_12",
  "version_label": "December 2024",
  "effective_from": "2024-12-12",
  "effective_to": null,
  "status": "processing",
  "ingestion_job_id": "job_a1b2c3d4e5f6",
  "links": {
    "self": "/api/v1/policies/NPPF/revisions/rev_NPPF_2024_12",
    "status": "/api/v1/policies/NPPF/revisions/rev_NPPF_2024_12/status",
    "policy": "/api/v1/policies/NPPF"
  },
  "side_effects": {
    "superseded_revision": "rev_NPPF_2023_09",
    "superseded_effective_to": "2024-12-11"
  }
}
```

**Error Responses:**
- **404** `policy_not_found` -- Parent policy does not exist
- **409** `revision_overlap` -- New revision's effective dates overlap an existing revision
- **422** `unsupported_file_type` -- Uploaded file is not a PDF

**Examples:**
- Positive: Upload PDF for existing policy with non-overlapping dates returns 202
- Positive: Upload new open-ended revision that supersedes previous open-ended revision returns 202 with `side_effects` showing the superseded revision
- Negative: Upload a .docx file returns 422
- Negative: Upload to non-existent policy source returns 404
- Negative: Upload with dates overlapping existing revision returns 409

---

### [rest-api-contract:FR-016] Revision Detail

**Description:** The API must return full details of a specific policy revision, including file metadata, ingestion status, chunk count, and any error information.

**Endpoint:** `GET /api/v1/policies/{source}/revisions/{revision_id}`

**Response (200 OK):**
```json
{
  "revision_id": "rev_LTN_1_20_2020_07",
  "source": "LTN_1_20",
  "version_label": "July 2020",
  "effective_from": "2020-07-27",
  "effective_to": null,
  "status": "active",
  "file_path": "/data/policy/LTN_1_20/rev_LTN_1_20_2020_07/ltn-1-20.pdf",
  "file_size_bytes": 15234567,
  "page_count": 208,
  "chunk_count": 160,
  "notes": "Current edition of LTN 1/20",
  "created_at": "2026-02-10T14:00:00Z",
  "ingested_at": "2026-02-10T14:15:00Z",
  "error": null
}
```

**Revision Statuses:** `processing`, `active`, `failed`, `superseded`

**Error Responses:**
- **404** `policy_not_found` -- Parent policy does not exist
- **404** `revision_not_found` -- Revision does not exist under this policy

**Examples:**
- Positive: GET active revision returns full details with chunk_count and ingested_at populated
- Positive: GET failed revision returns error message in `error` field
- Negative: GET revision under non-existent policy returns 404 `policy_not_found`
- Negative: GET non-existent revision_id returns 404 `revision_not_found`

---

### [rest-api-contract:FR-017] Revision Status

**Description:** The API must provide a lightweight status endpoint for tracking revision ingestion progress.

**Endpoint:** `GET /api/v1/policies/{source}/revisions/{revision_id}/status`

**Response (200 OK):**
```json
{
  "revision_id": "rev_NPPF_2024_12",
  "status": "processing",
  "progress": {
    "phase": "pending",
    "percent_complete": 0,
    "chunks_processed": 0
  }
}
```

When active:
```json
{
  "revision_id": "rev_NPPF_2024_12",
  "status": "active",
  "progress": {
    "phase": "complete",
    "percent_complete": 100,
    "chunks_processed": 73
  }
}
```

**Error Responses:**
- **404** `policy_not_found`
- **404** `revision_not_found`

---

### [rest-api-contract:FR-018] Revision Update

**Description:** The API must allow partial updates to revision metadata (version_label, effective dates, notes) using PATCH semantics. Date changes are validated against other revisions for overlap.

**Endpoint:** `PATCH /api/v1/policies/{source}/revisions/{revision_id}`

**Request Body:**
```json
{
  "version_label": "Updated Label",
  "effective_from": "2024-12-15",
  "effective_to": null,
  "notes": "Updated notes"
}
```

All fields are optional.

**Response (200 OK):** Returns full `PolicyRevisionDetail` (same shape as FR-016).

**Error Responses:**
- **404** `policy_not_found`
- **404** `revision_not_found`
- **409** `revision_overlap` -- Updated dates would overlap another revision

**Examples:**
- Positive: PATCH notes field only preserves all other fields
- Positive: PATCH effective_from to a non-overlapping date succeeds
- Negative: PATCH effective dates to overlap existing revision returns 409

---

### [rest-api-contract:FR-019] Revision Delete

**Description:** The API must allow deletion of a policy revision, removing its metadata and cleaning up the associated PDF file from disk. A policy's sole active revision cannot be deleted (returns 409).

**Endpoint:** `DELETE /api/v1/policies/{source}/revisions/{revision_id}`

**Response (200 OK):**
```json
{
  "source": "NPPF",
  "revision_id": "rev_NPPF_2023_09",
  "status": "deleted",
  "chunks_removed": 73
}
```

**Error Responses:**
- **404** `policy_not_found`
- **404** `revision_not_found`
- **409** `cannot_delete_sole_revision` -- Cannot delete the only active revision of a policy

**Examples:**
- Positive: Delete a superseded revision succeeds and reports chunks_removed count
- Negative: Delete the only revision of a policy returns 409
- Negative: Delete non-existent revision returns 404

---

### [rest-api-contract:FR-020] Revision Reindex

**Description:** The API must allow re-running the ingestion pipeline for an existing revision (e.g., after fixing ingestion bugs). The revision status is set to "processing" and returns 202. A revision that is already processing cannot be reindexed.

**Endpoint:** `POST /api/v1/policies/{source}/revisions/{revision_id}/reindex`

**Request Body:** None

**Response (202 Accepted):**
```json
{
  "revision_id": "rev_LTN_1_20_2020_07",
  "status": "processing",
  "progress": {
    "phase": "pending",
    "percent_complete": 0,
    "chunks_processed": 0
  }
}
```

**Error Responses:**
- **404** `policy_not_found`
- **404** `revision_not_found`
- **409** `cannot_reindex` -- Revision is already in "processing" status

**Examples:**
- Positive: Reindex an active revision returns 202 with status "processing"
- Positive: Reindex a failed revision returns 202 (retry after fixing issue)
- Negative: Reindex a revision that is already processing returns 409

---

### [rest-api-contract:FR-021] Destination Listing

**Description:** The API must return all configured cycle route assessment destinations. Destinations define the key points (stations, bus stops, town centres) used for route quality analysis.

**Endpoint:** `GET /api/v1/destinations`

**Response (200 OK):**
```json
{
  "destinations": [
    {
      "id": "dest_bicester_north",
      "name": "Bicester North Station",
      "lat": 51.9025,
      "lon": -1.1508,
      "category": "rail"
    },
    {
      "id": "dest_bicester_village",
      "name": "Bicester Village Station",
      "lat": 51.8889,
      "lon": -1.1461,
      "category": "rail"
    }
  ],
  "total": 2
}
```

**Examples:**
- Positive: GET with configured destinations returns full list
- Edge case: GET with no destinations configured returns `{"destinations": [], "total": 0}`

---

### [rest-api-contract:FR-022] Destination Creation

**Description:** The API must allow adding new cycle route assessment destinations with a name, coordinates, and category.

**Endpoint:** `POST /api/v1/destinations`

**Request Body:**
```json
{
  "name": "Bicester Town Centre",
  "lat": 51.8984,
  "lon": -1.1536,
  "category": "other"
}
```

**Request Fields:**
| Field | Type | Required | Constraints | Description |
|---|---|---|---|---|
| `name` | string | Yes | 1-200 characters | Destination name |
| `lat` | float | Yes | -90 to 90 | Latitude |
| `lon` | float | Yes | -180 to 180 | Longitude |
| `category` | string | No | `^(rail\|bus\|other)$` | Default: "other" |

**Response (201 Created):**
```json
{
  "id": "dest_abc123",
  "name": "Bicester Town Centre",
  "lat": 51.8984,
  "lon": -1.1536,
  "category": "other"
}
```

**Error Responses:**
- **422** `validation_error` -- Invalid coordinates, empty name, or invalid category

**Examples:**
- Positive: Create destination with valid coordinates returns 201
- Negative: Create with lat=999 returns 422 validation error
- Negative: Create with empty name returns 422 validation error
- Negative: Create with category "train" returns 422 (must be rail, bus, or other)

---

### [rest-api-contract:FR-023] Destination Deletion

**Description:** The API must allow removal of a destination by ID. Returns 404 if the destination does not exist.

**Endpoint:** `DELETE /api/v1/destinations/{destination_id}`

**Response (200 OK):**
```json
{
  "deleted": true,
  "destination_id": "dest_abc123"
}
```

**Error Responses:**
- **404** `destination_not_found`

**Examples:**
- Positive: Delete existing destination returns 200 with `deleted: true`
- Negative: Delete non-existent destination_id returns 404

---

### [rest-api-contract:FR-024] Health Check

**Description:** The API must expose a health check endpoint that does not require authentication. It reports overall system status ("healthy" or "degraded") and individual service connectivity (currently Redis). The endpoint is exempt from auth middleware and rate limiting.

**Endpoint:** `GET /api/v1/health`

**Response (200 OK):**
```json
{
  "status": "healthy",
  "services": {
    "redis": "connected"
  },
  "version": "0.1.0"
}
```

Degraded example:
```json
{
  "status": "degraded",
  "services": {
    "redis": "disconnected"
  },
  "version": "0.1.0"
}
```

**Examples:**
- Positive: Health check with all services up returns "healthy"
- Positive: Health check without Authorization header still succeeds (no auth required)
- Edge case: Redis unreachable returns 200 with status "degraded" and redis "disconnected"

---

### [rest-api-contract:FR-025] Webhook Delivery

**Description:** The system must deliver HTTP POST callbacks to a globally configured URL (`WEBHOOK_URL` environment variable) for review and letter lifecycle events. Webhooks are fire-and-forget from the worker process and never block or crash the caller. Delivery uses exponential backoff retries. If `WEBHOOK_URL` is not configured, no webhooks are sent.

**Configuration:**
| Environment Variable | Default | Description |
|---|---|---|
| `WEBHOOK_URL` | (none) | Target URL for webhook delivery; unset = disabled |
| `WEBHOOK_MAX_RETRIES` | 5 | Maximum delivery attempts |
| `WEBHOOK_TIMEOUT` | 10 | Timeout per delivery attempt (seconds) |

**Event Types:**
| Event | Trigger | Data Payload |
|---|---|---|
| `review.completed` | Review finishes successfully | `application_ref`, `overall_rating`, full `review` object, `metadata` |
| `review.completed.markdown` | Sent immediately after `review.completed` | `application_ref`, `full_markdown` |
| `review.failed` | Review processing fails | `application_ref`, `error` object with code and message |
| `letter.completed` | Letter generation finishes | `letter_id`, `review_id`, `application_ref`, `content`, `metadata` |

**Envelope Structure:**
```json
{
  "delivery_id": "550e8400-e29b-41d4-a716-446655440000",
  "event": "review.completed",
  "review_id": "rev_01JMABCDEF1234567890AB",
  "timestamp": 1707912345.678,
  "data": {
    "application_ref": "25/01178/REM",
    "overall_rating": "non_compliant",
    "review": { "..." : "..." },
    "metadata": { "..." : "..." }
  }
}
```

**HTTP Headers:**
| Header | Value |
|---|---|
| `Content-Type` | `application/json` |
| `X-Webhook-Event` | Event name (e.g., `review.completed`) |
| `X-Webhook-Delivery-Id` | Unique delivery UUID |
| `X-Webhook-Timestamp` | Unix timestamp (integer) of delivery attempt |

**Retry Strategy:**
- Up to 5 attempts (configurable via `WEBHOOK_MAX_RETRIES`)
- Exponential backoff delays: 1s, 2s, 4s, 8s, 16s (formula: `2^(attempt-1)` seconds)
- Non-2xx responses and connection/timeout errors trigger retries
- All failures are logged; delivery never raises exceptions

**Examples:**
- Positive: Review completes, webhook delivers `review.completed` then `review.completed.markdown` to configured URL
- Positive: First delivery attempt gets 503; system retries after 1s and succeeds on second attempt
- Edge case: All 5 retry attempts fail; error is logged but worker continues normally
- Edge case: `WEBHOOK_URL` not set; `fire_webhook` returns immediately without action

---

### [rest-api-contract:FR-026] API Key Authentication

**Description:** All API endpoints except exempt paths must require a valid API key passed as a Bearer token in the Authorization header. The system validates keys against a configured list loaded from environment variable or JSON file. Invalid or missing keys return 401 with consistent error format.

**Exempt Paths:** `/api/v1/health`, `/health`, `/docs`, `/redoc`, `/openapi.json`

**Configuration:**
| Source | Environment Variable | Format |
|---|---|---|
| Environment | `API_KEYS` | Comma-separated API keys |
| File | `API_KEYS_FILE` | Path to JSON file: `["key1", "key2"]` or `{"keys": ["key1", "key2"]}` |

Environment variable takes precedence over file. If neither is configured, auth middleware is not mounted (development mode).

**Request Header:**
```
Authorization: Bearer your-api-key-here
```

**Error Responses:**
- **401** `unauthorized` -- "Missing Authorization header"
- **401** `unauthorized` -- "Invalid Authorization header format. Expected: Bearer <token>"
- **401** `unauthorized` -- "Invalid API key"

**Examples:**
- Positive: Request with valid Bearer token passes through to endpoint
- Positive: Health check request without Authorization header succeeds (exempt path)
- Negative: Request with no Authorization header returns 401
- Negative: Request with `Authorization: Basic abc123` returns 401 (wrong scheme)
- Negative: Request with `Authorization: Bearer invalid-key` returns 401
- Edge case: No API_KEYS or API_KEYS_FILE configured; auth middleware not active (development mode)

---

### [rest-api-contract:FR-027] Error Response Format

**Description:** All API errors must follow a consistent JSON structure. HTTP exceptions, Pydantic validation errors, and unhandled exceptions are all normalised to the same envelope. Stack traces are never exposed in production (controlled by `ENVIRONMENT` env var).

**Error Envelope:**
```json
{
  "error": {
    "code": "machine_readable_code",
    "message": "Human-readable description",
    "details": {}
  }
}
```

**Standard Error Codes:**
| HTTP Status | Error Code | Description |
|---|---|---|
| 400 | `bad_request` | Malformed request |
| 401 | `unauthorized` | Missing or invalid API key |
| 404 | `not_found` | Resource not found |
| 409 | `conflict` | Resource conflict |
| 422 | `validation_error` | Pydantic validation failure |
| 429 | `rate_limited` | Rate limit exceeded |
| 500 | `internal_error` | Unhandled server error |

**Validation Error Detail:**
```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed",
    "details": {
      "errors": [
        {
          "field": "body.application_ref",
          "message": "Value error, Invalid application reference format: INVALID...",
          "type": "value_error"
        }
      ]
    }
  }
}
```

**Production vs Development:**
- In production (`ENVIRONMENT=production`): `details` is omitted from 500 errors; no stack traces
- In development: `details` includes `exception_type`, `exception_message`, and `traceback` array

**Error responses include `X-Request-ID` header when available.**

**Examples:**
- Positive: POST with invalid JSON body returns 422 with field-level errors array
- Positive: GET non-existent resource returns 404 with domain-specific error code (e.g., `review_not_found`)
- Positive: Unhandled exception in production returns 500 with generic message, no stack trace
- Edge case: Unhandled exception in development returns 500 with full traceback in details

---

### [rest-api-contract:FR-028] Request ID Tracking

**Description:** Every API response must include an `X-Request-ID` header. If the client provides an `X-Request-ID` in the request, it is preserved. Otherwise, a UUID v4 is auto-generated. The request ID is bound to the structlog context for the duration of the request, ensuring all log entries for a given request can be correlated.

**Behaviour:**
1. Client sends `X-Request-ID: my-trace-123` -- response includes `X-Request-ID: my-trace-123`
2. Client omits header -- response includes `X-Request-ID: 550e8400-e29b-41d4-a716-446655440000` (auto-generated)
3. Request ID is stored in `request.state.request_id` for use by exception handlers
4. Request ID is bound to structlog context via `structlog.contextvars.bound_contextvars`

**Additional Response Headers:**
| Header | Description |
|---|---|
| `X-API-Version` | API version string (currently "1.0.0"), added by APIVersionMiddleware on all responses |
| `X-RateLimit-Limit` | Maximum requests per window (on authenticated requests) |
| `X-RateLimit-Remaining` | Remaining requests in current window |
| `X-RateLimit-Reset` | Unix timestamp when rate limit window resets |

**Examples:**
- Positive: Response includes auto-generated X-Request-ID UUID
- Positive: Client-provided X-Request-ID is echoed back unchanged
- Positive: Error response (e.g., 404) still includes X-Request-ID
- Positive: All responses include X-API-Version header

---

## Non-Functional Requirements

### [rest-api-contract:NFR-001] Performance

**Description:** Non-blocking API endpoints (those that do not perform heavy processing) must respond within 200ms. This includes status checks, listing endpoints, health checks, and error responses. Review submission (which only creates a Redis record and enqueues a job) should also meet this target. Actual review processing and letter generation happen asynchronously in worker processes.

---

### [rest-api-contract:NFR-002] Rate Limiting

**Description:** The API must enforce per-API-key rate limiting using a Redis sliding window algorithm. The default limit is 60 requests per minute, configurable via the `API_RATE_LIMIT` environment variable. Rate limit state is tracked per hashed API key in Redis sorted sets. Health check and other unauthenticated paths are exempt from rate limiting.

**Response headers on all authenticated requests:**
- `X-RateLimit-Limit` -- Maximum requests per window
- `X-RateLimit-Remaining` -- Remaining requests
- `X-RateLimit-Reset` -- Unix timestamp of window reset

**When limit is exceeded (429 Too Many Requests):**
```json
{
  "error": {
    "code": "rate_limited",
    "message": "Too many requests. Please retry after the specified time.",
    "details": {
      "retry_after_seconds": 15
    }
  }
}
```
Includes `Retry-After` header with seconds until retry is allowed.

**Graceful degradation:** If Redis is unavailable, rate limiting is skipped and a warning is logged.

---

### [rest-api-contract:NFR-003] Security

**Description:** The API must not expose stack traces, internal paths, or secrets in production error responses. The `ENVIRONMENT` environment variable controls this behaviour. API keys are validated using constant-time string comparison via Python set lookups. API keys are hashed (SHA-256, truncated to 16 chars) before use in Redis rate-limit keys for privacy.

---

### [rest-api-contract:NFR-004] Availability

**Description:** The health endpoint (`/api/v1/health`) must remain available regardless of authentication state. It is exempt from API key validation and rate limiting. This allows external monitoring tools (e.g., Docker health checks, uptime monitors) to probe the service without credentials.

---

### [rest-api-contract:NFR-005] Observability

**Description:** The API must use structured logging via `structlog` with request IDs bound to the logging context for every request. This enables correlating all log entries for a single request across middleware, route handlers, and exception handlers. The `X-Request-ID` header is included on all responses (including errors) to allow clients to reference specific requests in support tickets.

---

### [rest-api-contract:NFR-006] Webhook Reliability

**Description:** Webhook delivery must provide at-least-once delivery semantics with configurable retry behaviour. The delivery mechanism never raises exceptions or blocks the worker -- all errors are caught, logged, and swallowed. Failed deliveries after all retries are logged at error level for operator alerting.

**Retry Configuration:**
- Maximum retries: 5 (configurable via `WEBHOOK_MAX_RETRIES`)
- Timeout per attempt: 10 seconds (configurable via `WEBHOOK_TIMEOUT`)
- Backoff schedule: 1s, 2s, 4s, 8s, 16s (exponential: `2^(attempt-1)`)
- Retried on: non-2xx responses, connection errors, timeout errors
- Payload size logged on successful delivery for monitoring

---

## Open Questions

1. **Webhook authentication** -- The current implementation sends no authentication headers with webhook deliveries. Should an HMAC signature or shared secret be added for receiver verification?
2. **Rate limit per endpoint** -- The current rate limit is global (60/min per key). Should expensive endpoints (review submission, letter generation) have lower individual limits?
3. **Pagination cursor** -- The current offset-based pagination may perform poorly at high offsets. Should cursor-based pagination be added for the review listing endpoint?
4. **API versioning strategy** -- The `X-API-Version` header is set to "1.0.0" and routes are under `/api/v1/`. What is the plan for introducing v2 breaking changes?
5. **Webhook for letter.failed** -- The `letter.failed` event is not currently implemented in the worker. Should it be added for symmetry with `review.failed`?

---

## Appendix

### Glossary

| Term | Definition |
|---|---|
| **Application Reference** | Cherwell planning portal identifier in format `YY/NNNNN/XXX` (e.g., `25/01178/REM`) |
| **Review** | An AI-generated analysis of a planning application from a cycling advocacy perspective |
| **Letter** | A generated consultation response letter derived from a completed review |
| **Policy** | A planning policy document (e.g., LTN 1/20, NPPF) registered in the knowledge base |
| **Revision** | A specific version of a policy document, with temporal effective dates and an uploaded PDF |
| **Destination** | A key location (station, bus stop, town centre) used for cycle route quality assessment |
| **Webhook** | An HTTP POST callback sent to a configured URL when asynchronous events occur |
| **ULID** | Universally Unique Lexicographically Sortable Identifier, used for review_id and letter_id prefixed with `rev_` and `ltr_` respectively |
| **Source Slug** | UPPER_SNAKE_CASE identifier for a policy (e.g., `LTN_1_20`, `NPPF`, `CHERWELL_LOCAL_PLAN`) |
| **Effective Date** | The date range during which a policy revision is considered "in force" |
| **Supersession** | When a new open-ended revision causes the previous open-ended revision to have its `effective_to` automatically set |

### References

| Reference | Location |
|---|---|
| FastAPI application entry point | `src/api/main.py` |
| Review route handlers | `src/api/routes/reviews.py` |
| Download route handlers | `src/api/routes/downloads.py` |
| Policy route handlers | `src/api/routes/policies.py` |
| Letter route handlers | `src/api/routes/letters.py` |
| Destination route handlers | `src/api/routes/destinations.py` |
| Health check route handler | `src/api/routes/health.py` |
| Review and error schemas | `src/api/schemas.py` |
| Policy schemas | `src/api/schemas/policy.py` |
| Letter schemas | `src/api/schemas/letter.py` |
| Auth middleware | `src/api/middleware/auth.py` |
| Rate limit middleware | `src/api/middleware/rate_limit.py` |
| Request ID middleware | `src/api/middleware/request_id.py` |
| API version middleware | `src/api/middleware/api_version.py` |
| Exception handlers | `src/api/exception_handlers.py` |
| API key validator | `src/api/auth/key_validator.py` |
| Webhook delivery | `src/worker/webhook.py` |
| Shared models (ReviewJob, ReviewStatus) | `src/shared/models.py` |
| OpenAPI docs | `/docs` (Swagger UI), `/redoc` (ReDoc), `/openapi.json` (raw schema) |

### Change History

| Date | Version | Author | Description |
|---|---|---|---|
| 2026-02-14 | 1.0 | -- | Initial as-built specification capturing all existing REST API endpoints |
