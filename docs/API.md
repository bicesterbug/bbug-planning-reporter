# Cherwell Cycle Advocacy Agent — API Reference

**Base URL:** `http://localhost:8080/api/v1`
**API Version:** `1.0.0`
**OpenAPI:** `/openapi.json` &bull; **Swagger UI:** `/docs` &bull; **ReDoc:** `/redoc`

---

## Table of Contents

- [Authentication](#authentication)
- [Common Headers](#common-headers)
- [Rate Limiting](#rate-limiting)
- [Error Format](#error-format)
- [Endpoints](#endpoints)
  - [Health](#health)
  - [Reviews](#reviews)
  - [Site Boundary](#site-boundary)
  - [Letters](#letters)
  - [Downloads](#downloads)
  - [Destinations](#destinations)
  - [Policies](#policies)
  - [Policy Revisions](#policy-revisions)
- [Webhooks](#webhooks)
- [Enums & Validation](#enums--validation)
- [Environment Variables](#environment-variables)

---

## Authentication

All endpoints except health and docs require a **Bearer token** in the `Authorization` header.

```
Authorization: Bearer <api-key>
```

API keys are loaded from the `API_KEYS` environment variable (comma-separated) or a JSON file at the path in `API_KEYS_FILE`. The file may be `["key1","key2"]` or `{"keys":["key1","key2"]}`. Environment variable takes precedence over file.

**Exempt paths** (no auth required): `/api/v1/health`, `/health`, `/docs`, `/redoc`, `/openapi.json`

Auth is disabled entirely when neither `API_KEYS` nor `API_KEYS_FILE` is set (development mode).

### 401 Unauthorized

```json
{
  "error": {
    "code": "unauthorized",
    "message": "Unauthorized"
  }
}
```

---

## Common Headers

### Request Headers

| Header | Required | Description |
|--------|----------|-------------|
| `Authorization` | Yes* | `Bearer <api-key>` (* except exempt paths) |
| `X-Request-ID` | No | Client-supplied request trace ID. Auto-generated UUID if omitted. |
| `Content-Type` | Varies | `application/json` for JSON bodies, `multipart/form-data` for file uploads |

### Response Headers

Every response includes:

| Header | Description |
|--------|-------------|
| `X-API-Version` | API version (`1.0.0`) |
| `X-Request-ID` | Unique request identifier (echoed or generated) |

Authenticated responses additionally include:

| Header | Description |
|--------|-------------|
| `X-RateLimit-Limit` | Max requests per window |
| `X-RateLimit-Remaining` | Requests remaining in current window |
| `X-RateLimit-Reset` | Unix timestamp when window resets |

---

## Rate Limiting

- **Algorithm:** Redis sliding window (sorted set)
- **Default:** 60 requests per 60-second window per API key
- **Configuration:** `API_RATE_LIMIT` environment variable
- API key is hashed (SHA-256, truncated to 16 chars) for privacy in Redis keys
- **Graceful degradation:** If Redis is unavailable, rate limiting is skipped and a warning is logged

### 429 Too Many Requests

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 42
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1738920000
```

```json
{
  "error": {
    "code": "rate_limited",
    "message": "Too many requests. Please retry after the specified time.",
    "details": {
      "retry_after_seconds": 42
    }
  }
}
```

---

## Error Format

All errors follow a consistent envelope:

```json
{
  "error": {
    "code": "<machine_readable_code>",
    "message": "<human_readable_message>",
    "details": {}
  }
}
```

`details` is optional and may be `null`. In production (`ENVIRONMENT=production`), `details` is omitted from 500 errors and no stack traces are exposed.

### Error Codes by Status

| Status | Code | Description |
|--------|------|-------------|
| 400 | `bad_request` | Malformed or invalid request |
| 401 | `unauthorized` | Missing or invalid API key |
| 404 | `not_found` | Resource not found |
| 409 | `conflict` | Duplicate or conflicting operation |
| 422 | `validation_error` | Pydantic field validation failed |
| 429 | `rate_limited` | Rate limit exceeded |
| 500 | `internal_error` | Unhandled server error |

### Validation Error (422)

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed",
    "details": {
      "errors": [
        {
          "field": "body.application_ref",
          "message": "Value error, Invalid application reference format: ABC. Expected format: YY/NNNNN/XXX (e.g., 25/01178/REM)",
          "type": "value_error"
        }
      ]
    }
  }
}
```

---

## Endpoints

### Health

#### `GET /api/v1/health`

Returns service health. No authentication required. Exempt from rate limiting.

**Response `200 OK`:**

```json
{
  "status": "healthy",
  "services": {
    "redis": "connected"
  },
  "version": "0.1.0"
}
```

| Field | Type | Values |
|-------|------|--------|
| `status` | string | `healthy` or `degraded` |
| `services.redis` | string | `connected` or `disconnected` |
| `version` | string | Application version |

When Redis is unreachable, returns `200` with `"status": "degraded"` and `"redis": "disconnected"`.

---

### Reviews

#### `POST /api/v1/reviews` -- Submit Review

Queue an AI review of a Cherwell planning application.

**Request Body (`application/json`):**

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

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `application_ref` | string | Yes | -- | Cherwell reference, format `YY/NNNNN/XXX` |
| `options` | object | No | `null` | Review configuration |
| `options.focus_areas` | string[] \| null | No | `null` | Areas to focus on (e.g. `cycle_parking`, `cycle_routes`, `junctions`, `permeability`) |
| `options.output_format` | string | No | `"markdown"` | Output format: `markdown` or `json` |
| `options.include_policy_matrix` | boolean | No | `true` | Include policy compliance matrix |
| `options.include_suggested_conditions` | boolean | No | `true` | Include suggested planning conditions |
| `options.include_consultation_responses` | boolean | No | `false` | Include statutory consultee responses as LLM evidence |
| `options.include_public_comments` | boolean | No | `false` | Include public comments/objection letters as LLM evidence |
| `options.destination_ids` | string[] \| null | No | `null` | Destination IDs for route assessment. `null` = all destinations, `[]` = skip route assessment |

**Response `202 Accepted`:**

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

**Error `409 Conflict`:** Active review already exists for this application.

```json
{
  "error": {
    "code": "review_already_exists",
    "message": "A review for application 25/01178/REM is already queued or processing",
    "details": {
      "application_ref": "25/01178/REM"
    }
  }
}
```

**Error `422 Validation Error`:** Invalid application reference or request body.

**curl example:**

```bash
curl -X POST http://localhost:8080/api/v1/reviews \
  -H "Authorization: Bearer sk-cycle-dev-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "application_ref": "25/01178/REM",
    "options": {
      "focus_areas": ["cycle_parking"],
      "include_policy_matrix": true,
      "destination_ids": null
    }
  }'
```

---

#### `GET /api/v1/reviews/{review_id}` -- Get Review

Returns the full review, including results when completed.

**Response `200 OK` (queued):**

```json
{
  "review_id": "rev_01JMABCDEF1234567890AB",
  "application_ref": "25/01178/REM",
  "status": "queued",
  "created_at": "2026-02-14T10:30:00Z",
  "started_at": null,
  "completed_at": null,
  "progress": null,
  "application": null,
  "review": null,
  "metadata": null,
  "site_boundary": null,
  "error": null
}
```

**Response `200 OK` (processing):**

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

**Response `200 OK` (completed):**

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
    "summary": "The application fails to provide adequate cycle parking and has no segregated cycle route to the town centre.",
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
        "detail": "The proposed cycle parking does not meet LTN 1/20 standards for residential development.",
        "policy_refs": ["LTN_1_20:s11.2", "CHERWELL_LP:Policy BSC1"]
      },
      {
        "name": "Cycle Routes",
        "rating": "non_compliant",
        "key_issue": "No dedicated cycle link to town centre",
        "detail": "The site connects to Gavray Drive but no segregated cycle route is provided to the town centre 2km away.",
        "policy_refs": ["LTN_1_20:4.2", "NPPF:para.112", "LCWIP:Route B3"]
      }
    ],
    "policy_compliance": [
      {
        "requirement": "Secure, covered cycle parking at LTN 1/20 rates",
        "policy_source": "LTN_1_20",
        "compliant": false,
        "notes": "Only Sheffield stands proposed; no covered or secure provision"
      },
      {
        "requirement": "Safe, direct and coherent cycle routes",
        "policy_source": "NPPF",
        "compliant": false,
        "notes": "No segregated link to town centre; relies on shared-use footway on Gavray Drive"
      }
    ],
    "recommendations": [
      "Provide secure, covered cycle parking at 1 space per bedroom",
      "Widen shared-use path along Buckingham Road to 3m"
    ],
    "suggested_conditions": [
      "Prior to occupation, a revised Cycle Parking Strategy shall be submitted and approved.",
      "A 3m-wide segregated cycle track shall be constructed along Gavray Drive from the site entrance to the junction with Buckingham Road."
    ],
    "route_assessments": [
      {
        "destination": "Bicester North Station",
        "destination_id": "dest_bicester_north",
        "distance_m": 2400,
        "duration_minutes": 9.5,
        "provision_breakdown": {
          "protected_lane": 0.3,
          "shared_use": 0.5,
          "on_road": 0.2
        },
        "score": {
          "overall": 62,
          "safety": 55,
          "directness": 78
        },
        "issues": [
          {
            "location": "A4095 junction",
            "severity": "high",
            "description": "No cycle phase at signals"
          }
        ],
        "s106_suggestions": [
          {
            "item": "Toucan crossing at A4095",
            "estimated_cost": 150000
          }
        ]
      }
    ],
    "full_markdown": "# Cycle Advocacy Review: 25/01178/REM\n\n## Overall Rating: NON-COMPLIANT\n..."
  },
  "metadata": {
    "model": "claude-sonnet-4-20250514",
    "total_tokens_used": 45000,
    "processing_time_seconds": 220,
    "documents_analysed": 38,
    "policy_sources_referenced": 4,
    "policy_effective_date": "2026-02-14",
    "policy_revisions_used": [
      {
        "source": "LTN_1_20",
        "revision_id": "rev_LTN_1_20_2020_07",
        "version_label": "July 2020"
      },
      {
        "source": "NPPF",
        "revision_id": "rev_NPPF_2024_12",
        "version_label": "December 2024"
      }
    ]
  },
  "site_boundary": {
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
  },
  "error": null
}
```

**Response `200 OK` (failed):**

```json
{
  "review_id": "rev_01JMABCDEF1234567890AB",
  "application_ref": "25/99999/F",
  "status": "failed",
  "created_at": "2026-02-14T10:30:00Z",
  "started_at": "2026-02-14T10:30:05Z",
  "completed_at": null,
  "progress": null,
  "application": null,
  "review": null,
  "metadata": null,
  "site_boundary": null,
  "error": {
    "code": "scraper_error",
    "message": "Application not found on Cherwell planning portal"
  }
}
```

**Error `404 Not Found`:**

```json
{
  "error": {
    "code": "review_not_found",
    "message": "No review found with ID rev_nonexistent",
    "details": {
      "review_id": "rev_nonexistent"
    }
  }
}
```

---

#### `GET /api/v1/reviews/{review_id}/status` -- Lightweight Status

Fast status check without full result payload. Optimised for polling during review processing.

**Response `200 OK` (processing):**

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

**Response `200 OK` (queued):**

```json
{
  "review_id": "rev_01JMABCDEF1234567890AB",
  "status": "queued",
  "progress": null
}
```

**Response `200 OK` (completed):**

```json
{
  "review_id": "rev_01JMABCDEF1234567890AB",
  "status": "completed",
  "progress": null
}
```

**Error `404`:** Same as Get Review.

---

#### `GET /api/v1/reviews` -- List Reviews

**Query Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `status` | string | No | -- | Filter: `queued`, `processing`, `completed`, `failed`, `cancelled` |
| `application_ref` | string | No | -- | Filter by application reference |
| `limit` | integer | No | `20` | Results per page (1--100) |
| `offset` | integer | No | `0` | Pagination offset |

**Response `200 OK`:**

```json
{
  "reviews": [
    {
      "review_id": "rev_01JMABCDEF1234567890AB",
      "application_ref": "25/01178/REM",
      "status": "completed",
      "overall_rating": "non_compliant",
      "created_at": "2026-02-14T10:30:00Z",
      "completed_at": "2026-02-14T10:33:45Z"
    },
    {
      "review_id": "rev_01JKWVU9876543210FEDCBA",
      "application_ref": "24/03456/F",
      "status": "queued",
      "overall_rating": null,
      "created_at": "2026-02-14T13:00:00Z",
      "completed_at": null
    }
  ],
  "total": 47,
  "limit": 20,
  "offset": 0
}
```

**Error `400`:** Invalid status value.

```json
{
  "error": {
    "code": "invalid_status",
    "message": "Invalid status: bogus",
    "details": {
      "valid_statuses": ["queued", "processing", "completed", "failed", "cancelled"]
    }
  }
}
```

**curl example:**

```bash
curl "http://localhost:8080/api/v1/reviews?status=completed&limit=5" \
  -H "Authorization: Bearer sk-cycle-dev-key-1"
```

---

#### `POST /api/v1/reviews/{review_id}/cancel` -- Cancel Review

Cancels a queued or processing review. No request body.

**Response `200 OK`:**

```json
{
  "review_id": "rev_01JMABCDEF1234567890AB",
  "status": "cancelled",
  "progress": null
}
```

**Error `404`:** Review not found.

**Error `409 Conflict`:** Review already in a terminal state.

```json
{
  "error": {
    "code": "cannot_cancel",
    "message": "Cannot cancel review with status 'completed'",
    "details": {
      "review_id": "rev_01JMABCDEF1234567890AB",
      "current_status": "completed"
    }
  }
}
```

---

### Site Boundary

#### `GET /api/v1/reviews/{review_id}/site-boundary` -- Get Site Boundary

Returns the site boundary as a GeoJSON FeatureCollection. The collection contains up to two features: the site polygon and a centroid point.

**Response `200 OK`:**

```http
HTTP/1.1 200 OK
Content-Type: application/geo+json
```

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

**Error `404 Not Found`:** Two possible error codes:

- `review_not_found` -- Review does not exist
- `site_boundary_not_found` -- Review exists but has no site boundary data (e.g., review not yet completed, or scraper could not extract boundary)

**curl example:**

```bash
curl http://localhost:8080/api/v1/reviews/rev_01JMABCDEF1234567890AB/site-boundary \
  -H "Authorization: Bearer sk-cycle-dev-key-1"
```

---

### Letters

#### `POST /api/v1/reviews/{review_id}/letter` -- Generate Letter

Generate a consultee response letter from a completed review. The letter is produced asynchronously by an LLM, rewriting the review findings into formal letter prose addressed to the planning authority.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `review_id` | string | The review to generate a letter for (must be `completed`) |

**Request Body (`application/json`):**

```json
{
  "stance": "object",
  "tone": "formal",
  "case_officer": "Ms J. Smith",
  "letter_date": "2026-02-10"
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `stance` | string | Yes | -- | Group's position: `object`, `support`, `conditional`, or `neutral` |
| `tone` | string | No | `"formal"` | Letter tone: `formal` or `accessible` |
| `case_officer` | string \| null | No | `null` | Override case officer name (falls back to review data, then generic) |
| `letter_date` | date \| null | No | Today | Letter date in `YYYY-MM-DD` format |

**Response `202 Accepted`:**

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

**Error `400 Bad Request`:** Review is not completed.

```json
{
  "error": {
    "code": "review_incomplete",
    "message": "Review rev_01HQXK has status 'processing', must be 'completed'",
    "details": {
      "review_id": "rev_01HQXK",
      "current_status": "processing"
    }
  }
}
```

**Error `404 Not Found`:** Review does not exist.

**Error `422 Validation Error`:** Invalid stance, tone, or date format.

**curl example:**

```bash
curl -X POST http://localhost:8080/api/v1/reviews/rev_01JMABCDEF1234567890AB/letter \
  -H "Authorization: Bearer sk-cycle-dev-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "stance": "object",
    "tone": "formal",
    "case_officer": "Ms J. Smith"
  }'
```

---

#### `GET /api/v1/letters/{letter_id}` -- Retrieve Letter

Returns the letter content when generation is complete, or current status if still generating.

**Response `200 OK` (generating):**

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

**Response `200 OK` (completed):**

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

**Response `200 OK` (failed):**

```json
{
  "letter_id": "ltr_01JMXYZ1234567890ABCDE",
  "review_id": "rev_01JMABCDEF1234567890AB",
  "application_ref": "25/01178/REM",
  "status": "failed",
  "stance": "object",
  "tone": "formal",
  "content": null,
  "metadata": null,
  "error": {
    "code": "letter_generation_failed",
    "message": "Claude API error: rate limit exceeded"
  },
  "created_at": "2026-02-14T11:00:00Z",
  "completed_at": "2026-02-14T11:00:01Z"
}
```

**Error `404 Not Found`:**

```json
{
  "error": {
    "code": "letter_not_found",
    "message": "No letter found with ID ltr_nonexistent",
    "details": {
      "letter_id": "ltr_nonexistent"
    }
  }
}
```

**curl example:**

```bash
curl http://localhost:8080/api/v1/letters/ltr_01JMXYZ1234567890ABCDE \
  -H "Authorization: Bearer sk-cycle-dev-key-1"
```

---

### Downloads

#### `GET /api/v1/reviews/{review_id}/download` -- Download Review

Download a completed review in the specified format.

**Query Parameters:**

| Parameter | Type | Required | Default | Values |
|-----------|------|----------|---------|--------|
| `format` | string | No | `markdown` | `markdown`, `json`, `pdf` |

**Response `200 OK` (markdown):**

```http
HTTP/1.1 200 OK
Content-Type: text/markdown
Content-Disposition: attachment; filename="review-rev_01JMABCDEF1234567890AB.md"
```

**Response `200 OK` (json):**

```http
Content-Type: application/json
Content-Disposition: attachment; filename="review-rev_01JMABCDEF1234567890AB.json"
```

Returns the full result object (same shape as the `review` + `application` + `metadata` fields from `GET /reviews/{id}`).

**Response `200 OK` (pdf):**

```http
Content-Type: application/pdf
Content-Disposition: attachment; filename="review-rev_01JMABCDEF1234567890AB.pdf"
```

Binary PDF with styled content, rating badges, and page headers/footers.

**Error `400`:** Review not yet complete.

```json
{
  "error": {
    "code": "review_incomplete",
    "message": "Cannot download review with status 'processing'. Wait for completion.",
    "details": {
      "review_id": "rev_01JMABCDEF1234567890AB",
      "status": "processing"
    }
  }
}
```

**Error `404`:** Review not found.

**curl example:**

```bash
curl -o review.pdf \
  "http://localhost:8080/api/v1/reviews/rev_01JMABCDEF1234567890AB/download?format=pdf" \
  -H "Authorization: Bearer sk-cycle-dev-key-1"
```

---

### Destinations

Destinations define the key points (stations, bus stops, town centres) used for cycle route quality assessment during reviews.

#### `GET /api/v1/destinations` -- List Destinations

**Response `200 OK`:**

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
    },
    {
      "id": "dest_town_centre",
      "name": "Bicester Town Centre",
      "lat": 51.8984,
      "lon": -1.1536,
      "category": "other"
    }
  ],
  "total": 3
}
```

Returns an empty list when no destinations are configured:

```json
{
  "destinations": [],
  "total": 0
}
```

**curl example:**

```bash
curl http://localhost:8080/api/v1/destinations \
  -H "Authorization: Bearer sk-cycle-dev-key-1"
```

---

#### `POST /api/v1/destinations` -- Create Destination

**Request Body (`application/json`):**

```json
{
  "name": "Bicester Town Centre",
  "lat": 51.8984,
  "lon": -1.1536,
  "category": "other"
}
```

| Field | Type | Required | Default | Constraints | Description |
|-------|------|----------|---------|-------------|-------------|
| `name` | string | Yes | -- | 1--200 characters | Destination name |
| `lat` | float | Yes | -- | -90 to 90 | Latitude (WGS84) |
| `lon` | float | Yes | -- | -180 to 180 | Longitude (WGS84) |
| `category` | string | No | `"other"` | `rail`, `bus`, or `other` | Destination category |

**Response `201 Created`:**

```json
{
  "id": "dest_abc123",
  "name": "Bicester Town Centre",
  "lat": 51.8984,
  "lon": -1.1536,
  "category": "other"
}
```

**Error `422 Validation Error`:** Invalid coordinates, empty name, or invalid category.

**curl example:**

```bash
curl -X POST http://localhost:8080/api/v1/destinations \
  -H "Authorization: Bearer sk-cycle-dev-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Bicester North Station",
    "lat": 51.9025,
    "lon": -1.1508,
    "category": "rail"
  }'
```

---

#### `DELETE /api/v1/destinations/{destination_id}` -- Delete Destination

**Response `200 OK`:**

```json
{
  "deleted": true,
  "destination_id": "dest_abc123"
}
```

**Error `404 Not Found`:**

```json
{
  "error": {
    "code": "destination_not_found",
    "message": "No destination found with ID dest_nonexistent",
    "details": {
      "destination_id": "dest_nonexistent"
    }
  }
}
```

**curl example:**

```bash
curl -X DELETE http://localhost:8080/api/v1/destinations/dest_abc123 \
  -H "Authorization: Bearer sk-cycle-dev-key-1"
```

---

### Policies

#### `POST /api/v1/policies` -- Create Policy

Register a new policy document.

**Request Body (`application/json`):**

```json
{
  "source": "LTN_1_20",
  "title": "Cycle Infrastructure Design (LTN 1/20)",
  "description": "Department for Transport guidance on designing high-quality cycle infrastructure",
  "category": "national_guidance"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source` | string | Yes | Unique slug in `UPPER_SNAKE_CASE` (e.g. `LTN_1_20`, `NPPF`) |
| `title` | string | Yes | Human-readable title |
| `description` | string \| null | No | Description of the policy |
| `category` | string | Yes | One of the [PolicyCategory](#policycategory) values |

**Response `201 Created`:**

```json
{
  "source": "LTN_1_20",
  "title": "Cycle Infrastructure Design (LTN 1/20)",
  "description": "Department for Transport guidance on designing high-quality cycle infrastructure",
  "category": "national_guidance",
  "revisions": [],
  "current_revision": null,
  "revision_count": 0,
  "created_at": "2026-02-14T09:00:00Z",
  "updated_at": null
}
```

**Error `409 Conflict`:**

```json
{
  "error": {
    "code": "policy_already_exists",
    "message": "A policy with source 'LTN_1_20' already exists",
    "details": {
      "source": "LTN_1_20"
    }
  }
}
```

**Error `422`:** Invalid source format or missing required fields.

---

#### `GET /api/v1/policies` -- List Policies

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `category` | string | No | Filter by [PolicyCategory](#policycategory) |
| `source` | string | No | Filter by source slug |

**Response `200 OK`:**

```json
{
  "policies": [
    {
      "source": "NPPF",
      "title": "National Planning Policy Framework",
      "category": "national_policy",
      "current_revision": {
        "revision_id": "rev_NPPF_2024_12",
        "version_label": "December 2024",
        "effective_from": "2024-12-12",
        "effective_to": null,
        "status": "active",
        "chunk_count": 73,
        "ingested_at": "2026-02-10T14:05:00Z"
      },
      "revision_count": 2
    },
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

**curl example:**

```bash
curl "http://localhost:8080/api/v1/policies?category=national_guidance" \
  -H "Authorization: Bearer sk-cycle-dev-key-1"
```

---

#### `GET /api/v1/policies/{source}` -- Get Policy Detail

Returns the policy with all its revisions (ordered by `effective_from` descending).

**Response `200 OK`:**

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

**Error `404`:**

```json
{
  "error": {
    "code": "policy_not_found",
    "message": "No policy found with source 'NONEXISTENT'",
    "details": {
      "source": "NONEXISTENT"
    }
  }
}
```

---

#### `PATCH /api/v1/policies/{source}` -- Update Policy

Update policy metadata. All fields are optional; only provided fields are updated.

**Request Body (`application/json`):**

```json
{
  "title": "Updated Policy Title",
  "description": "Updated description",
  "category": "local_guidance"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | No | New title |
| `description` | string | No | New description |
| `category` | string | No | New [PolicyCategory](#policycategory) |

**Response `200 OK`:** Full `PolicyDocumentDetail` (same shape as GET).

**Error `404`:** Policy not found.

---

#### `GET /api/v1/policies/effective` -- Effective Policies at Date

Returns which revision of each policy was in force on the given date.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `date` | date | Yes | `YYYY-MM-DD` format |

**Response `200 OK`:**

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

**Error `400`:** Invalid date format.

**Error `422`:** Missing required `date` parameter.

**curl example:**

```bash
curl "http://localhost:8080/api/v1/policies/effective?date=2024-01-15" \
  -H "Authorization: Bearer sk-cycle-dev-key-1"
```

---

### Policy Revisions

#### `POST /api/v1/policies/{source}/revisions` -- Upload Revision

Upload a PDF and create a new revision. Uses `multipart/form-data`.

**Request (`multipart/form-data`):**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | file | Yes | PDF file |
| `version_label` | string | Yes | Human-readable version (e.g. `"December 2024"`) |
| `effective_from` | date | Yes | `YYYY-MM-DD` when revision takes effect |
| `effective_to` | date \| null | No | `YYYY-MM-DD` when revision is superseded (`null` = currently in force) |
| `notes` | string \| null | No | Notes about this revision |

**Response `202 Accepted`:**

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

`side_effects` is `null` when no existing revision was superseded.

**Error `404`:** Parent policy does not exist.

**Error `409 Conflict`:** Date range overlaps with existing revision.

```json
{
  "error": {
    "code": "revision_overlap",
    "message": "Revision dates overlap with existing revision rev_NPPF_2023_09",
    "details": {
      "source": "NPPF",
      "effective_from": "2024-12-12"
    }
  }
}
```

**Error `422`:** Non-PDF file uploaded.

```json
{
  "error": {
    "code": "unsupported_file_type",
    "message": "Only PDF files are supported",
    "details": {
      "content_type": "image/png",
      "filename": "screenshot.png"
    }
  }
}
```

**curl example:**

```bash
curl -X POST http://localhost:8080/api/v1/policies/NPPF/revisions \
  -H "Authorization: Bearer sk-cycle-dev-key-1" \
  -F "file=@nppf-dec-2024.pdf" \
  -F "version_label=December 2024" \
  -F "effective_from=2024-12-12"
```

---

#### `GET /api/v1/policies/{source}/revisions/{revision_id}` -- Get Revision

**Response `200 OK`:**

```json
{
  "revision_id": "rev_NPPF_2024_12",
  "source": "NPPF",
  "version_label": "December 2024",
  "effective_from": "2024-12-12",
  "effective_to": null,
  "status": "active",
  "file_path": "/data/policy/NPPF/rev_NPPF_2024_12/nppf-dec-2024.pdf",
  "file_size_bytes": 2458624,
  "page_count": 84,
  "chunk_count": 73,
  "notes": null,
  "created_at": "2026-02-10T14:05:00Z",
  "ingested_at": "2026-02-10T14:15:00Z",
  "error": null
}
```

**Error `404`:** Policy or revision not found (error code indicates which: `policy_not_found` or `revision_not_found`).

---

#### `GET /api/v1/policies/{source}/revisions/{revision_id}/status` -- Revision Status

**Response `200 OK` (processing):**

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

**Response `200 OK` (active):**

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

**Error `404`:** Policy or revision not found.

---

#### `PATCH /api/v1/policies/{source}/revisions/{revision_id}` -- Update Revision

Update revision metadata. All fields optional.

**Request Body (`application/json`):**

```json
{
  "version_label": "December 2024 (Corrected)",
  "effective_to": "2025-12-31",
  "notes": "Corrected page count metadata"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `version_label` | string | No | New version label |
| `effective_from` | date | No | New effective-from date |
| `effective_to` | date | No | New effective-to date |
| `notes` | string | No | New notes |

**Response `200 OK`:** Full `PolicyRevisionDetail`.

**Error `404`:** Policy or revision not found.

**Error `409`:** Updated date range causes overlap.

---

#### `DELETE /api/v1/policies/{source}/revisions/{revision_id}` -- Delete Revision

Removes the revision, its vector chunks, and the PDF from disk.

**Response `200 OK`:**

```json
{
  "source": "NPPF",
  "revision_id": "rev_NPPF_2023_09",
  "status": "deleted",
  "chunks_removed": 73
}
```

**Error `404`:** Policy or revision not found.

**Error `409 Conflict`:** Cannot delete the sole active revision for a policy.

```json
{
  "error": {
    "code": "cannot_delete_sole_revision",
    "message": "Cannot delete the sole active revision for a policy",
    "details": {
      "source": "NPPF",
      "revision_id": "rev_NPPF_2024_12"
    }
  }
}
```

---

#### `POST /api/v1/policies/{source}/revisions/{revision_id}/reindex` -- Reindex Revision

Re-run the ingestion pipeline for an existing revision. No request body.

**Response `202 Accepted`:**

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

**Error `404`:** Policy or revision not found.

**Error `409`:** Already processing.

```json
{
  "error": {
    "code": "cannot_reindex",
    "message": "Revision is already processing",
    "details": {
      "source": "NPPF",
      "revision_id": "rev_NPPF_2024_12",
      "status": "processing"
    }
  }
}
```

---

## Webhooks

Webhooks provide push notifications for review and letter lifecycle events. They are configured globally via the `WEBHOOK_URL` environment variable -- there is no per-request webhook configuration.

### Configuration

Webhooks are controlled entirely by environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `WEBHOOK_URL` | (none) | Target URL for webhook delivery; unset = disabled |
| `WEBHOOK_MAX_RETRIES` | `5` | Maximum delivery attempts |
| `WEBHOOK_TIMEOUT` | `10` | Timeout per delivery attempt (seconds) |

When `WEBHOOK_URL` is not set, no webhooks are sent.

### Events

| Event | Triggered When | Data Payload |
|-------|----------------|--------------|
| `review.completed` | Review finishes successfully | `application_ref`, `overall_rating`, full `review` object, `metadata` |
| `review.completed.markdown` | Sent immediately after `review.completed` | `application_ref`, `full_markdown` |
| `review.failed` | Review processing fails | `application_ref`, `error` object with code and message |
| `letter.completed` | Letter generation finishes | `letter_id`, `review_id`, `application_ref`, `content`, `metadata` |

### Delivery Headers

Each webhook delivery includes the following HTTP headers:

| Header | Description |
|--------|-------------|
| `Content-Type` | `application/json` |
| `X-Webhook-Event` | Event name (e.g. `review.completed`) |
| `X-Webhook-Delivery-Id` | Unique UUID for this delivery |
| `X-Webhook-Timestamp` | Unix timestamp (integer) of delivery attempt |

There is no HMAC signing or authentication header on webhook deliveries.

### Payload Envelope

All webhook payloads share a common envelope structure:

```json
{
  "delivery_id": "550e8400-e29b-41d4-a716-446655440000",
  "event": "review.completed",
  "review_id": "rev_01JMABCDEF1234567890AB",
  "timestamp": 1707912345.678,
  "data": { }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `delivery_id` | string | Unique UUID for this delivery (matches `X-Webhook-Delivery-Id` header) |
| `event` | string | Event type |
| `review_id` | string | Associated review ID |
| `timestamp` | float | Unix timestamp when the event was created |
| `data` | object | Event-specific payload (see below) |

### Event Payloads

**`review.completed`:**

```json
{
  "delivery_id": "550e8400-e29b-41d4-a716-446655440000",
  "event": "review.completed",
  "review_id": "rev_01JMABCDEF1234567890AB",
  "timestamp": 1707912345.678,
  "data": {
    "application_ref": "25/01178/REM",
    "overall_rating": "non_compliant",
    "review": {
      "overall_rating": "non_compliant",
      "summary": "...",
      "aspects": [],
      "policy_compliance": [],
      "recommendations": [],
      "suggested_conditions": [],
      "route_assessments": [],
      "key_documents": []
    },
    "metadata": {
      "model": "claude-sonnet-4-20250514",
      "total_tokens_used": 45000,
      "processing_time_seconds": 220
    }
  }
}
```

**`review.completed.markdown`:**

```json
{
  "delivery_id": "660f9500-f39c-52e5-b827-557766551111",
  "event": "review.completed.markdown",
  "review_id": "rev_01JMABCDEF1234567890AB",
  "timestamp": 1707912346.123,
  "data": {
    "application_ref": "25/01178/REM",
    "full_markdown": "# Cycle Advocacy Review: 25/01178/REM\n\n## Overall Rating: NON-COMPLIANT\n..."
  }
}
```

**`review.failed`:**

```json
{
  "delivery_id": "770a0611-a40d-63f6-c938-668877662222",
  "event": "review.failed",
  "review_id": "rev_01JMABCDEF1234567890AB",
  "timestamp": 1707912400.000,
  "data": {
    "application_ref": "25/01178/REM",
    "error": {
      "code": "scraper_error",
      "message": "Application not found on Cherwell planning portal"
    }
  }
}
```

**`letter.completed`:**

```json
{
  "delivery_id": "880b1722-b51e-74g7-d049-779988773333",
  "event": "letter.completed",
  "review_id": "rev_01JMABCDEF1234567890AB",
  "timestamp": 1707912500.000,
  "data": {
    "letter_id": "ltr_01JMXYZ1234567890ABCDE",
    "review_id": "rev_01JMABCDEF1234567890AB",
    "application_ref": "25/01178/REM",
    "content": "Dear Ms Smith,\n\nI am writing on behalf of Bicester Bike Users' Group...",
    "metadata": {
      "model": "claude-sonnet-4-20250514",
      "input_tokens": 12000,
      "output_tokens": 1500,
      "processing_time_seconds": 8.5
    }
  }
}
```

### Retry Strategy

- Up to 5 attempts (configurable via `WEBHOOK_MAX_RETRIES`)
- Exponential backoff delays: 1s, 2s, 4s, 8s, 16s (formula: `2^(attempt-1)` seconds)
- Non-2xx responses and connection/timeout errors trigger retries
- All failures are logged; delivery never raises exceptions or blocks the worker
- Payload size is logged on successful delivery for monitoring

---

## Enums & Validation

### ReviewStatus

| Value | Description |
|-------|-------------|
| `queued` | Job accepted, waiting for worker |
| `processing` | Worker is executing the review |
| `completed` | Review finished successfully |
| `failed` | Review encountered an error |
| `cancelled` | Review cancelled by user |

### ProcessingPhase

| Value | Phase # | Description |
|-------|---------|-------------|
| `fetching_metadata` | 1 | Getting application details from Cherwell portal |
| `filtering_documents` | 2 | Filtering document list by type and relevance |
| `downloading_documents` | 3 | Fetching planning documents |
| `ingesting_documents` | 4 | Extracting and embedding document content |
| `analysing_application` | 5 | AI analysis using Claude |
| `assessing_routes` | 6 | Evaluating cycle routes to configured destinations |
| `generating_review` | 7 | Formatting and storing results |
| `verifying_review` | 8 | Validating output and running quality checks |

### OverallRating

| Value | Description |
|-------|-------------|
| `compliant` | Application meets cycling infrastructure requirements |
| `non_compliant` | Application fails to meet requirements |

### LetterStance

| Value | Description |
|-------|-------------|
| `object` | Group opposes the application |
| `support` | Group supports the application |
| `conditional` | Group supports subject to conditions |
| `neutral` | Group provides factual comments without a position |

### LetterTone

| Value | Description |
|-------|-------------|
| `formal` | Professional planning language with technical terminology |
| `accessible` | Clear, jargon-light language for councillors and the public |

### LetterStatus

| Value | Description |
|-------|-------------|
| `generating` | LLM is producing the letter |
| `completed` | Letter is ready for retrieval |
| `failed` | Letter generation encountered an error |

### DestinationCategory

| Value | Description |
|-------|-------------|
| `rail` | Railway station |
| `bus` | Bus stop or bus station |
| `other` | Town centre, school, workplace, or other destination |

### PolicyCategory

| Value | Description |
|-------|-------------|
| `national_policy` | National planning policy (e.g. NPPF) |
| `national_guidance` | National guidance (e.g. LTN 1/20) |
| `local_plan` | Local authority development plan |
| `local_guidance` | Local supplementary guidance |
| `county_strategy` | County-level transport/planning strategy |
| `supplementary` | Supplementary planning documents |

### RevisionStatus

| Value | Description |
|-------|-------------|
| `processing` | PDF is being ingested and chunked |
| `active` | Revision is the current version |
| `failed` | Ingestion failed |
| `superseded` | Replaced by a newer revision |

### Application Reference Format

Pattern: `^\d{2}/\d{4,5}/[A-Z]{1,4}$`

Examples: `25/01178/REM`, `23/01421/TCA`, `08/00707/F`

- `YY` -- 2-digit year
- `NNNNN` -- 4 or 5 digit sequence number
- `XXX` -- 1--4 letter application type code

### Policy Source Format

Pattern: `^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$`

Examples: `NPPF`, `LTN_1_20`, `CHERWELL_LOCAL_PLAN`, `OCC_LTCP`

### Webhook Event Names

Pattern: `^[a-z]+\.[a-z]+(\.[a-z]+)?$`

Values: `review.completed`, `review.completed.markdown`, `review.failed`, `letter.completed`

---

## Status Code Summary

| Code | Meaning | Used By |
|------|---------|---------|
| `200` | OK | GET, PATCH, DELETE, cancel |
| `201` | Created | POST policies, POST destinations |
| `202` | Accepted | POST reviews, POST letter, POST revisions, POST reindex |
| `400` | Bad Request | Invalid parameters, review incomplete |
| `401` | Unauthorized | Missing/invalid API key |
| `404` | Not Found | Resource doesn't exist |
| `409` | Conflict | Duplicates, overlaps, cannot cancel/delete |
| `422` | Validation Error | Pydantic field validation failure |
| `429` | Too Many Requests | Rate limit exceeded |
| `500` | Internal Error | Unhandled server error |

---

## Environment Variables

### API Server

| Variable | Default | Description |
|----------|---------|-------------|
| `API_KEYS` | (none) | Comma-separated API keys for Bearer auth |
| `API_KEYS_FILE` | (none) | Path to JSON file with API keys |
| `API_RATE_LIMIT` | `60` | Max requests per 60-second window per key |
| `ENVIRONMENT` | `development` | `production` suppresses stack traces in errors |

### Webhooks

| Variable | Default | Description |
|----------|---------|-------------|
| `WEBHOOK_URL` | (none) | Global webhook delivery URL; unset = disabled |
| `WEBHOOK_MAX_RETRIES` | `5` | Maximum delivery attempts per event |
| `WEBHOOK_TIMEOUT` | `10` | Timeout per delivery attempt (seconds) |

### Advocacy Group Configuration

The letter generation feature uses the following environment variables to configure the advocacy group identity. These are set in `docker-compose.yml` for both the `api` and `worker` services.

| Variable | Default | Description |
|----------|---------|-------------|
| `ADVOCACY_GROUP_NAME` | `Bicester Bike Users' Group` | Full legal name, used in sign-off |
| `ADVOCACY_GROUP_STYLISED` | `Bicester BUG` | Display name, used in letter body |
| `ADVOCACY_GROUP_SHORT` | `BBUG` | Abbreviation, used in parenthetical references |
