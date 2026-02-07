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
  - [Downloads](#downloads)
  - [Policies](#policies)
  - [Policy Revisions](#policy-revisions)
- [Webhooks](#webhooks)
- [Enums & Validation](#enums--validation)

---

## Authentication

All endpoints except health and docs require a **Bearer token** in the `Authorization` header.

```
Authorization: Bearer <api-key>
```

API keys are loaded from the `API_KEYS` environment variable (comma-separated) or a JSON file at the path in `API_KEYS_FILE`. The file may be `["key1","key2"]` or `{"keys":["key1","key2"]}`.

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
- API key is hashed (SHA-256) for privacy in Redis keys

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

`details` is optional and may be `null`.

### Error Codes by Status

| Status | Code | Description |
|--------|------|-------------|
| 400 | `bad_request` | Malformed or invalid request |
| 401 | `unauthorized` | Missing or invalid API key |
| 403 | `forbidden` | Insufficient permissions |
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

Returns service health. No authentication required.

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

---

### Reviews

#### `POST /api/v1/reviews` — Submit Review

Queue an AI review of a Cherwell planning application.

**Request Body (`application/json`):**

```json
{
  "application_ref": "25/01178/REM",
  "options": {
    "focus_areas": ["cycle_parking", "cycle_routes"],
    "output_format": "markdown",
    "include_policy_matrix": true,
    "include_suggested_conditions": true
  },
  "webhook": {
    "url": "https://example.com/hooks/review",
    "secret": "whsec_abc123",
    "events": ["review.started", "review.completed", "review.failed"]
  }
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `application_ref` | string | Yes | — | Cherwell reference, format `YY/NNNNN/XXX` |
| `options` | object | No | `null` | Review configuration |
| `options.focus_areas` | string[] | No | `null` | Areas to focus on (e.g. `cycle_parking`, `cycle_routes`, `junctions`, `permeability`) |
| `options.output_format` | string | No | `"markdown"` | Output format (`markdown` or `json`) |
| `options.include_policy_matrix` | boolean | No | `true` | Include policy compliance matrix |
| `options.include_suggested_conditions` | boolean | No | `true` | Include suggested planning conditions |
| `webhook` | object | No | `null` | Webhook callback configuration (see [Webhooks](#webhooks)) |
| `webhook.url` | string | Yes* | — | Callback URL (`https://` required in production) |
| `webhook.secret` | string | Yes* | — | HMAC-SHA256 signing secret |
| `webhook.events` | string[] | No | All events | Events to subscribe to |

**Response `202 Accepted`:**

```json
{
  "review_id": "rev_01JKXYZ1234567890ABCDEF",
  "application_ref": "25/01178/REM",
  "status": "queued",
  "created_at": "2025-02-07T12:34:56.789000Z",
  "estimated_duration_seconds": 180,
  "links": {
    "self": "/api/v1/reviews/rev_01JKXYZ1234567890ABCDEF",
    "status": "/api/v1/reviews/rev_01JKXYZ1234567890ABCDEF/status",
    "cancel": "/api/v1/reviews/rev_01JKXYZ1234567890ABCDEF/cancel"
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

**curl example:**

```bash
curl -X POST http://localhost:8080/api/v1/reviews \
  -H "Authorization: Bearer sk-cycle-dev-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "application_ref": "25/01178/REM",
    "options": {
      "focus_areas": ["cycle_parking"],
      "include_policy_matrix": true
    }
  }'
```

---

#### `GET /api/v1/reviews/{review_id}` — Get Review

Returns the full review, including results when completed.

**Response `200 OK` (processing):**

```json
{
  "review_id": "rev_01JKXYZ1234567890ABCDEF",
  "application_ref": "25/01178/REM",
  "status": "processing",
  "created_at": "2025-02-07T12:34:56.789000Z",
  "started_at": "2025-02-07T12:34:57.123000Z",
  "completed_at": null,
  "progress": {
    "phase": "downloading_documents",
    "phase_number": 2,
    "total_phases": 5,
    "percent_complete": 40,
    "detail": "Downloading 5 documents"
  },
  "application": null,
  "review": null,
  "metadata": null,
  "error": null
}
```

**Response `200 OK` (completed):**

```json
{
  "review_id": "rev_01JKXYZ1234567890ABCDEF",
  "application_ref": "25/01178/REM",
  "status": "completed",
  "created_at": "2025-02-07T12:34:56.789000Z",
  "started_at": "2025-02-07T12:34:57.123000Z",
  "completed_at": "2025-02-07T12:39:56.456000Z",
  "progress": null,
  "application": {
    "reference": "25/01178/REM",
    "address": "Land North of Gavray Drive, Bicester",
    "proposal": "Reserved matters for 200 dwellings pursuant to outline approval 14/01384/OUT",
    "applicant": "Countryside Properties",
    "status": "Under consideration",
    "consultation_end": "2025-03-01",
    "documents_fetched": 12,
    "documents_ingested": 12
  },
  "review": {
    "overall_rating": "amber",
    "summary": "The application provides cycle parking at LTN 1/20 standards but cycle route connectivity to the town centre is weak.",
    "aspects": [
      {
        "name": "Cycle Parking",
        "rating": "green",
        "key_issue": "Provision meets LTN 1/20 standards",
        "detail": "Each dwelling has covered, secure cycle parking for 2 bikes. Visitor parking at 1 stand per 10 dwellings.",
        "policy_refs": ["LTN 1/20:11.1", "NPPF:para.110"]
      },
      {
        "name": "Cycle Routes",
        "rating": "red",
        "key_issue": "No dedicated cycle link to town centre",
        "detail": "The site connects to Gavray Drive but no segregated cycle route is provided to the town centre 2km away.",
        "policy_refs": ["LTN 1/20:4.2", "NPPF:para.112", "LCWIP:Route B3"]
      }
    ],
    "policy_compliance": [
      {
        "requirement": "Secure, covered cycle parking at LTN 1/20 rates",
        "policy_source": "LTN_1_20",
        "compliant": true,
        "notes": "2 spaces per dwelling, Sheffield stands in communal areas"
      },
      {
        "requirement": "Safe, direct and coherent cycle routes",
        "policy_source": "NPPF",
        "compliant": false,
        "notes": "No segregated link to town centre; relies on shared-use footway on Gavray Drive"
      }
    ],
    "recommendations": [
      "Provide a segregated 3m-wide cycle track along Gavray Drive to connect to Bicester town centre",
      "Install directional signage for cycle routes on site"
    ],
    "suggested_conditions": [
      "Prior to occupation, a 3m-wide segregated cycle track shall be constructed along Gavray Drive from the site entrance to the junction with Buckingham Road.",
      "A Travel Plan including cycle promotion measures shall be submitted and approved within 3 months of first occupation."
    ],
    "full_markdown": "# Cycle Advocacy Review: 25/01178/REM\n\n## Overall Rating: AMBER\n..."
  },
  "metadata": {
    "model": "claude-sonnet-4-5-20250929",
    "total_tokens_used": 28473,
    "processing_time_seconds": 295,
    "documents_analysed": 12,
    "policy_sources_referenced": 4,
    "policy_effective_date": "2025-02-07",
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
  "error": null
}
```

**Response `200 OK` (failed):**

```json
{
  "review_id": "rev_01JKXYZ1234567890ABCDEF",
  "application_ref": "25/99999/F",
  "status": "failed",
  "created_at": "2025-02-07T12:34:56.789000Z",
  "started_at": "2025-02-07T12:34:57.123000Z",
  "completed_at": null,
  "progress": null,
  "application": null,
  "review": null,
  "metadata": null,
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

#### `GET /api/v1/reviews/{review_id}/status` — Lightweight Status

Fast status check without full result payload.

**Response `200 OK`:**

```json
{
  "review_id": "rev_01JKXYZ1234567890ABCDEF",
  "status": "processing",
  "progress": {
    "phase": "analysing_application",
    "percent_complete": 75
  }
}
```

When completed or not processing:

```json
{
  "review_id": "rev_01JKXYZ1234567890ABCDEF",
  "status": "completed",
  "progress": null
}
```

**Error `404`:** Same as Get Review.

---

#### `GET /api/v1/reviews` — List Reviews

**Query Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `status` | string | No | — | Filter: `queued`, `processing`, `completed`, `failed`, `cancelled` |
| `application_ref` | string | No | — | Filter by application reference |
| `limit` | integer | No | `20` | Results per page (1–100) |
| `offset` | integer | No | `0` | Pagination offset |

**Response `200 OK`:**

```json
{
  "reviews": [
    {
      "review_id": "rev_01JKXYZ1234567890ABCDEF",
      "application_ref": "25/01178/REM",
      "status": "completed",
      "overall_rating": "amber",
      "created_at": "2025-02-07T12:34:56.789000Z",
      "completed_at": "2025-02-07T12:39:56.456000Z"
    },
    {
      "review_id": "rev_01JKWVU9876543210FEDCBA",
      "application_ref": "24/03456/F",
      "status": "queued",
      "overall_rating": null,
      "created_at": "2025-02-07T13:00:00.000000Z",
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

#### `POST /api/v1/reviews/{review_id}/cancel` — Cancel Review

Cancels a queued or processing review. No request body.

**Response `200 OK`:**

```json
{
  "review_id": "rev_01JKXYZ1234567890ABCDEF",
  "status": "cancelled",
  "progress": null
}
```

**Error `409 Conflict`:** Review already completed/failed/cancelled.

```json
{
  "error": {
    "code": "cannot_cancel",
    "message": "Cannot cancel review with status 'completed'",
    "details": {
      "review_id": "rev_01JKXYZ1234567890ABCDEF",
      "current_status": "completed"
    }
  }
}
```

---

### Downloads

#### `GET /api/v1/reviews/{review_id}/download` — Download Review

Download a completed review in the specified format.

**Query Parameters:**

| Parameter | Type | Required | Default | Values |
|-----------|------|----------|---------|--------|
| `format` | string | No | `markdown` | `markdown`, `json`, `pdf` |

**Response `200 OK` (markdown):**

```http
HTTP/1.1 200 OK
Content-Type: text/markdown
Content-Disposition: attachment; filename="review-rev_01JKXYZ1234567890ABCDEF.md"
```

```markdown
# Cycle Advocacy Review: 25/01178/REM

## Overall Rating: AMBER

## Summary
The application provides cycle parking at LTN 1/20 standards but cycle route
connectivity to the town centre is weak.

## Aspect Assessments

### Cycle Parking: GREEN
**Key Issue:** Provision meets LTN 1/20 standards
Each dwelling has covered, secure cycle parking for 2 bikes.

### Cycle Routes: RED
**Key Issue:** No dedicated cycle link to town centre
The site connects to Gavray Drive but no segregated cycle route is provided.

## Recommendations
- Provide a segregated 3m-wide cycle track along Gavray Drive
- Install directional signage for cycle routes on site
```

**Response `200 OK` (json):**

```http
Content-Type: application/json
Content-Disposition: attachment; filename="review-rev_01JKXYZ1234567890ABCDEF.json"
```

Returns the full result object (same shape as the `review` + `application` + `metadata` fields from `GET /reviews/{id}`).

**Response `200 OK` (pdf):**

```http
Content-Type: application/pdf
Content-Disposition: attachment; filename="review-rev_01JKXYZ1234567890ABCDEF.pdf"
```

Binary PDF with styled content, rating badges, and page headers/footers.

**Error `400`:** Review not yet complete.

```json
{
  "error": {
    "code": "review_incomplete",
    "message": "Cannot download review with status 'processing'. Wait for completion.",
    "details": {
      "review_id": "rev_01JKXYZ1234567890ABCDEF",
      "status": "processing"
    }
  }
}
```

**curl example:**

```bash
curl -o review.pdf \
  "http://localhost:8080/api/v1/reviews/rev_01JKXYZ1234567890ABCDEF/download?format=pdf" \
  -H "Authorization: Bearer sk-cycle-dev-key-1"
```

---

### Policies

#### `POST /api/v1/policies` — Create Policy

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
| `description` | string | No | Description of the policy |
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
  "created_at": "2025-02-07T12:34:56.789000Z",
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

---

#### `GET /api/v1/policies` — List Policies

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
        "effective_from": "2024-12-15",
        "effective_to": null,
        "status": "active",
        "chunk_count": 73,
        "ingested_at": "2025-02-07T12:35:00.000000Z"
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
        "ingested_at": "2025-02-07T12:35:00.000000Z"
      },
      "revision_count": 1
    }
  ],
  "total": 6
}
```

---

#### `GET /api/v1/policies/{source}` — Get Policy Detail

Returns the policy with all its revisions.

**Response `200 OK`:**

```json
{
  "source": "NPPF",
  "title": "National Planning Policy Framework",
  "description": "Central government planning policy for England",
  "category": "national_policy",
  "revisions": [
    {
      "revision_id": "rev_NPPF_2024_12",
      "version_label": "December 2024",
      "effective_from": "2024-12-15",
      "effective_to": null,
      "status": "active",
      "chunk_count": 73,
      "ingested_at": "2025-02-07T12:35:00.000000Z"
    },
    {
      "revision_id": "rev_NPPF_2023_09",
      "version_label": "September 2023",
      "effective_from": "2023-09-01",
      "effective_to": "2024-12-14",
      "status": "superseded",
      "chunk_count": 73,
      "ingested_at": "2025-02-07T12:35:00.000000Z"
    }
  ],
  "current_revision": {
    "revision_id": "rev_NPPF_2024_12",
    "version_label": "December 2024",
    "effective_from": "2024-12-15",
    "effective_to": null,
    "status": "active",
    "chunk_count": 73,
    "ingested_at": "2025-02-07T12:35:00.000000Z"
  },
  "revision_count": 2,
  "created_at": "2025-02-07T12:34:56.789000Z",
  "updated_at": "2025-02-07T12:35:00.000000Z"
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

#### `PATCH /api/v1/policies/{source}` — Update Policy

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

#### `GET /api/v1/policies/effective` — Effective Policies at Date

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
        "effective_from": "2023-09-01",
        "effective_to": "2024-12-14",
        "status": "superseded",
        "chunk_count": 73,
        "ingested_at": "2025-02-07T12:35:00.000000Z"
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
        "ingested_at": "2025-02-07T12:35:00.000000Z"
      }
    }
  ],
  "policies_not_yet_effective": []
}
```

**curl example:**

```bash
curl "http://localhost:8080/api/v1/policies/effective?date=2024-01-15" \
  -H "Authorization: Bearer sk-cycle-dev-key-1"
```

---

### Policy Revisions

#### `POST /api/v1/policies/{source}/revisions` — Upload Revision

Upload a PDF and create a new revision. Uses `multipart/form-data`.

**Request (`multipart/form-data`):**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | file | Yes | PDF file |
| `version_label` | string | Yes | Human-readable version (e.g. `"December 2024"`) |
| `effective_from` | date | Yes | `YYYY-MM-DD` when revision takes effect |
| `effective_to` | date | No | `YYYY-MM-DD` when revision is superseded (`null` = currently in force) |
| `notes` | string | No | Notes about this revision |

**Response `202 Accepted`:**

```json
{
  "source": "NPPF",
  "revision_id": "rev_NPPF_2024_12",
  "version_label": "December 2024",
  "effective_from": "2024-12-15",
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
    "superseded_effective_to": "2024-12-14"
  }
}
```

`side_effects` is `null` when no existing revision was superseded.

**Error `409 Conflict`:** Date range overlaps with existing revision.

```json
{
  "error": {
    "code": "revision_overlap",
    "message": "Revision dates overlap with existing revision rev_NPPF_2023_09",
    "details": {
      "source": "NPPF",
      "effective_from": "2024-12-15"
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
  -F "effective_from=2024-12-15"
```

---

#### `GET /api/v1/policies/{source}/revisions/{revision_id}` — Get Revision

**Response `200 OK`:**

```json
{
  "revision_id": "rev_NPPF_2024_12",
  "source": "NPPF",
  "version_label": "December 2024",
  "effective_from": "2024-12-15",
  "effective_to": null,
  "status": "active",
  "file_path": "/data/policy/NPPF/rev_NPPF_2024_12/nppf-dec-2024.pdf",
  "file_size_bytes": 2458624,
  "page_count": 84,
  "chunk_count": 73,
  "notes": null,
  "created_at": "2025-02-07T12:34:56.789000Z",
  "ingested_at": "2025-02-07T12:35:42.000000Z",
  "error": null
}
```

**Error `404`:** Policy or revision not found (error code indicates which).

---

#### `GET /api/v1/policies/{source}/revisions/{revision_id}/status` — Revision Status

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

---

#### `PATCH /api/v1/policies/{source}/revisions/{revision_id}` — Update Revision

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

**Error `409`:** Updated date range causes overlap.

---

#### `DELETE /api/v1/policies/{source}/revisions/{revision_id}` — Delete Revision

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

#### `POST /api/v1/policies/{source}/revisions/{revision_id}/reindex` — Reindex Revision

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

Webhooks provide push notifications for review lifecycle events. Configure them in the `webhook` field of `POST /api/v1/reviews`.

### Configuration

```json
{
  "webhook": {
    "url": "https://example.com/hooks/review",
    "secret": "whsec_your_signing_secret",
    "events": ["review.started", "review.completed", "review.failed"]
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | string | Yes | HTTPS callback URL (HTTP allowed in development only) |
| `secret` | string | Yes | Shared secret for HMAC-SHA256 signature verification |
| `events` | string[] | No | Events to receive; defaults to all |

### URL Validation

- Must have `http://` or `https://` scheme
- Must include a host
- **Production** (`ENVIRONMENT=production`): HTTPS required
- **Development**: HTTP allowed for local testing

### Events

| Event | Triggered When |
|-------|----------------|
| `review.started` | Job moves from `queued` to `processing` |
| `review.progress` | Processing phase changes (metadata, documents, analysis, etc.) |
| `review.completed` | Review finishes successfully |
| `review.failed` | Review encounters an unrecoverable error |

### Delivery

Webhook payloads are signed using **HMAC-SHA256** with the secret provided at registration time. The delivery includes:

| Header | Description |
|--------|-------------|
| `X-Webhook-Signature` | HMAC-SHA256 hex digest of the request body |
| `Content-Type` | `application/json` |

### Payload Format

**`review.started`:**

```json
{
  "event": "review.started",
  "review_id": "rev_01JKXYZ1234567890ABCDEF",
  "application_ref": "25/01178/REM",
  "timestamp": "2025-02-07T12:34:57.123000Z"
}
```

**`review.progress`:**

```json
{
  "event": "review.progress",
  "review_id": "rev_01JKXYZ1234567890ABCDEF",
  "application_ref": "25/01178/REM",
  "timestamp": "2025-02-07T12:35:15.456000Z",
  "progress": {
    "phase": "downloading_documents",
    "phase_number": 2,
    "total_phases": 5,
    "percent_complete": 40,
    "detail": "Downloading 12 documents"
  }
}
```

**`review.completed`:**

```json
{
  "event": "review.completed",
  "review_id": "rev_01JKXYZ1234567890ABCDEF",
  "application_ref": "25/01178/REM",
  "timestamp": "2025-02-07T12:39:56.456000Z",
  "overall_rating": "amber",
  "links": {
    "review": "/api/v1/reviews/rev_01JKXYZ1234567890ABCDEF",
    "download": "/api/v1/reviews/rev_01JKXYZ1234567890ABCDEF/download"
  }
}
```

**`review.failed`:**

```json
{
  "event": "review.failed",
  "review_id": "rev_01JKXYZ1234567890ABCDEF",
  "application_ref": "25/01178/REM",
  "timestamp": "2025-02-07T12:36:00.000000Z",
  "error": {
    "code": "scraper_error",
    "message": "Application not found on Cherwell planning portal"
  }
}
```

### Signature Verification

Verify webhook authenticity by computing HMAC-SHA256 of the raw request body using your shared secret:

```python
import hashlib
import hmac

def verify_signature(body: bytes, secret: str, signature: str) -> bool:
    expected = hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

```javascript
const crypto = require("crypto");

function verifySignature(body, secret, signature) {
  const expected = crypto
    .createHmac("sha256", secret)
    .update(body)
    .digest("hex");
  return crypto.timingSafeEqual(
    Buffer.from(expected),
    Buffer.from(signature)
  );
}
```

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
| `downloading_documents` | 2 | Fetching planning documents |
| `ingesting_documents` | 3 | Extracting and embedding document content |
| `analysing_application` | 4 | AI analysis using Claude |
| `generating_review` | 5 | Formatting and storing results |

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

- `YY` — 2-digit year
- `NNNNN` — 4 or 5 digit sequence number
- `XXX` — 1–4 letter application type code

### Policy Source Format

Pattern: `^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$`

Examples: `NPPF`, `LTN_1_20`, `CHERWELL_LOCAL_PLAN`, `OCC_LTCP`

---

## Status Code Summary

| Code | Meaning | Used By |
|------|---------|---------|
| `200` | OK | GET, PATCH, DELETE, cancel |
| `201` | Created | POST policies |
| `202` | Accepted | POST reviews, POST revisions, POST reindex |
| `400` | Bad Request | Invalid parameters |
| `401` | Unauthorized | Missing/invalid API key |
| `404` | Not Found | Resource doesn't exist |
| `409` | Conflict | Duplicates, overlaps, cannot cancel/delete |
| `422` | Validation Error | Pydantic field validation failure |
| `429` | Too Many Requests | Rate limit exceeded |
| `500` | Internal Error | Unhandled server error |
