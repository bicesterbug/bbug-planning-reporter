# Design: Agent Integration

**Version:** 1.0
**Date:** 2026-02-06
**Status:** Draft
**Linked Specification:** `.sdd/agent-integration/specification.md`

---

# Design Document

---

## Architecture Overview

### Current Architecture Context

Phases 1-3 have established the foundational infrastructure:
- **Phase 1 (Foundation API):** REST API gateway, Redis job queue, webhook dispatcher, Cherwell scraper MCP server
- **Phase 2 (Document Processing):** Document ingestion pipeline, ChromaDB storage, semantic search for application documents
- **Phase 3 (Policy Knowledge Base):** Versioned policy storage, temporal queries, policy search MCP tools

The agent integration phase connects these components through an AI orchestrator that coordinates the complete review workflow.

### Proposed Architecture

The agent worker orchestrates multiple MCP servers to execute the review workflow:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          Docker Compose Stack                                    │
│                                                                                  │
│  ┌─────────────────────┐      ┌─────────────────────┐                           │
│  │   API Gateway       │      │      Redis          │                           │
│  │   (FastAPI)         │◄────►│   - Job Queue (arq) │                           │
│  │   :8080             │      │   - State Store     │                           │
│  └─────────┬───────────┘      │   - Progress Events │                           │
│            │                   └─────────┬───────────┘                           │
│            │ enqueue                     │                                       │
│            │                             │ dequeue + publish                     │
│            ▼                             ▼                                       │
│  ┌───────────────────────────────────────────────────────────────────┐          │
│  │                    Agent Worker                                    │          │
│  │  ┌─────────────────────────────────────────────────────────────┐  │          │
│  │  │                  AgentOrchestrator                          │  │          │
│  │  │  - MCP client management (3 servers)                        │  │          │
│  │  │  - Workflow state machine                                   │  │          │
│  │  │  - Error recovery & partial failure handling                │  │          │
│  │  └──────────────────────────┬──────────────────────────────────┘  │          │
│  │                             │                                      │          │
│  │  ┌──────────────┐  ┌───────┴───────┐  ┌──────────────────────┐   │          │
│  │  │ProgressTracker│  │ReviewAssessor │  │   PolicyComparer     │   │          │
│  │  │ - Phase state │  │- Parking      │  │- Policy search       │   │          │
│  │  │ - Webhooks    │  │- Routes       │  │- Compliance check    │   │          │
│  │  │ - Sub-progress│  │- Junctions    │  │- Effective date      │   │          │
│  │  └──────────────┘  │- Permeability │  └──────────────────────┘   │          │
│  │                     └───────────────┘                              │          │
│  │  ┌──────────────────────────────────────────────────────────────┐ │          │
│  │  │               ReviewGenerator                                 │ │          │
│  │  │  - Structured JSON output                                     │ │          │
│  │  │  - Markdown formatting                                        │ │          │
│  │  │  - ReviewTemplates for output                                 │ │          │
│  │  └──────────────────────────────────────────────────────────────┘ │          │
│  └────────────────────────────┬──────────────────────────────────────┘          │
│                               │                                                  │
│           ┌───────────────────┼───────────────────┐                             │
│           │                   │                   │                              │
│           ▼                   ▼                   ▼                              │
│  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐                    │
│  │ Cherwell Scraper│ │ Document Store  │ │ Policy KB       │                    │
│  │ MCP Server      │ │ MCP Server      │ │ MCP Server      │                    │
│  │ :3001 (SSE)     │ │ :3002 (SSE)     │ │ :3003 (SSE)     │                    │
│  └────────┬────────┘ └────────┬────────┘ └────────┬────────┘                    │
│           │                   │                   │                              │
│           ▼                   └─────────┬─────────┘                              │
│  ┌─────────────────┐         ┌──────────▼──────────┐                            │
│  │ Cherwell Portal │         │    ChromaDB         │                            │
│  │ (external)      │         │  - application_docs │                            │
│  └─────────────────┘         │  - policy_docs      │                            │
│                              └─────────────────────┘                             │
└─────────────────────────────────────────────────────────────────────────────────┘

External:
┌─────────────────┐                        ┌─────────────────┐
│  Claude API     │◄───────────────────────│  Agent Worker   │
│  (claude-sonnet │                        │  (LLM calls)    │
│   -4-5)         │                        │                 │
└─────────────────┘                        └─────────────────┘
```

### Distributed Systems Considerations

**MCP Server Orchestration:**
- Three MCP servers run as separate processes with SSE transport
- AgentOrchestrator maintains persistent connections to all servers
- Connection health monitored; automatic reconnection on transient failures
- Graceful degradation: review can proceed with partial server availability

**Error Handling Strategy:**
| Failure Type | Detection | Recovery | Impact |
|--------------|-----------|----------|--------|
| MCP connection lost | Connection timeout/error | Reconnect with exponential backoff (3 attempts) | Delay in phase |
| Scraper portal unavailable | HTTP 5xx/timeout | Retry with backoff; fail job after 3 attempts | Review fails with `scraper_error` |
| Document ingestion failure | Exception from MCP tool | Log error; continue with remaining documents | Partial document coverage |
| Policy search failure | MCP tool error | Retry once; proceed without policy if fails | Review notes missing policy context |
| Claude API error | HTTP error/timeout | Retry with backoff (3 attempts) | Phase delayed or review fails |
| Claude rate limit | HTTP 429 | Wait and retry per retry-after header | Phase delayed |

**State Management:**
- Workflow state persisted in Redis at each phase transition
- State includes: current_phase, completed_phases, documents_processed, errors_encountered
- On worker restart: resume from last persisted phase (idempotent operations)
- Cancellation flag checked between phases

**Progress Tracking:**
- Phase transitions publish `review.progress` events via Redis pub/sub
- Sub-progress for long-running phases (e.g., "Analysing document 5 of 22")
- ProgressTracker maintains phase timing for performance monitoring
- All progress events include review_id for correlation

### Technology Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| LLM | Claude Sonnet 4.5 | Best balance of intelligence, speed, and cost for structured analysis |
| LLM Client | Anthropic SDK | Native tool use support, streaming, structured outputs |
| MCP Client | mcp Python library | Official client for MCP protocol |
| MCP Transport | SSE over HTTP | Container-friendly; persistent connections |
| Agent Pattern | Tool-using agent | LLM orchestrates tool calls; structured workflow |
| Output Format | JSON + Markdown | Structured data for API; human-readable for submission |

### Quality Attributes

**Reliability:**
- Graceful degradation on partial failures (NFR-005)
- Each aspect can be assessed independently
- Review produced even if one MCP server temporarily unavailable
- Retry logic for transient failures

**Performance:**
- Target <5 minutes average, <10 minutes maximum (NFR-001)
- Token-efficient prompts (<50,000 tokens average) (NFR-004)
- Parallel document analysis where possible
- Streaming Claude responses for faster first-token

**Accuracy:**
- Citation accuracy: 100% verifiable (NFR-003)
- All policy references grounded in RAG results
- Review accuracy validated by domain expert (NFR-002)

---

## API Design

### Review Output Schema

The agent produces a structured review matching the API response schema. This is the core contract between the agent and consumers.

**Review Response (GET /api/v1/reviews/{review_id} - completed)**

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
        "date_validated": "2025-01-20",
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
            },
            {
                "name": "Cycle Routes",
                "rating": "red",
                "key_issue": "Shared-use path where LTN 1/20 requires segregation",
                "detail": "The eastern boundary proposes a 3m shared-use path...",
                "policy_refs": ["LTN 1/20 Table 5-2", "LTN 1/20 Ch.5"]
            },
            {
                "name": "Junction Design",
                "rating": "red",
                "key_issue": "No protected cycle provision at site access",
                "detail": "The main site access junction...",
                "policy_refs": ["LTN 1/20 Ch.6"]
            },
            {
                "name": "Permeability",
                "rating": "amber",
                "key_issue": "Internal routes good but missing connection to adjacent area",
                "detail": "The internal layout provides...",
                "policy_refs": ["Cherwell LP Policy SLE4", "Manual for Streets Ch.4"]
            }
        ],
        "policy_compliance": [
            {
                "requirement": "Segregated cycle track where >2500 PCU/day",
                "policy_source": "LTN 1/20 Table 5-2",
                "policy_revision": "rev_LTN120_2020_07",
                "compliant": false,
                "notes": "Shared-use path proposed on road with 4200 PCU/day"
            },
            {
                "requirement": "Cycle parking to Local Plan standards",
                "policy_source": "Cherwell LP Policy SLE4",
                "policy_revision": "rev_CLP2031_2015_07",
                "compliant": true,
                "notes": "48 spaces exceeds minimum requirement of 40"
            }
        ],
        "recommendations": [
            "Provide segregated cycle track on the eastern boundary in accordance with LTN 1/20 Table 5-2, given traffic flows of 4200 PCU/day",
            "Add 4 cargo/adapted bike parking spaces near the main entrance per LTN 1/20 Chapter 11.3",
            "Incorporate protected cycle crossing at the main site access junction per LTN 1/20 Chapter 6",
            "Provide a filtered permeability connection to [adjacent area] for pedestrians and cyclists"
        ],
        "suggested_conditions": [
            "Prior to occupation, a detailed cycle parking layout showing Sheffield stands, cargo bike spaces, and accessible spaces shall be submitted for approval",
            "Prior to construction of the site access, details of protected cycle crossing facilities shall be submitted for approval"
        ],
        "human_review_flags": [
            "Site plan drawings require manual assessment for cycle route geometry",
            "Transport Assessment traffic figures should be verified"
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
            {
                "source": "LTN_1_20",
                "revision_id": "rev_LTN120_2020_07",
                "version_label": "July 2020"
            },
            {
                "source": "NPPF",
                "revision_id": "rev_NPPF_2024_12",
                "version_label": "December 2024"
            },
            {
                "source": "CHERWELL_LP_2031",
                "revision_id": "rev_CLP2031_2015_07",
                "version_label": "July 2015 Adopted"
            }
        ],
        "phases_completed": [
            {"phase": "fetching_metadata", "duration_seconds": 3},
            {"phase": "downloading_documents", "duration_seconds": 45},
            {"phase": "ingesting_documents", "duration_seconds": 62},
            {"phase": "analysing_application", "duration_seconds": 55},
            {"phase": "generating_review", "duration_seconds": 27}
        ],
        "errors_encountered": [
            {"phase": "ingesting_documents", "document": "scan_001.pdf", "error": "OCR failed - corrupt file"}
        ]
    }
}
```

### Aspect Rating Schema

```typescript
type Rating = "red" | "amber" | "green";

interface Aspect {
    name: "Cycle Parking" | "Cycle Routes" | "Junction Design" | "Permeability";
    rating: Rating;
    key_issue: string;      // Single sentence summary
    detail: string;         // Full analysis paragraph(s)
    policy_refs: string[];  // Human-readable policy references
}
```

### Rating Criteria

| Aspect | Green | Amber | Red |
|--------|-------|-------|-----|
| Cycle Parking | Meets/exceeds standards; includes cargo spaces | Meets minimum; minor issues | Below standards; missing entirely |
| Cycle Routes | Meets LTN 1/20 fully | Partial compliance; minor deviations | Non-compliant with core standards |
| Junction Design | Protected provision where required | Some protection; minor gaps | No protection where LTN 1/20 requires |
| Permeability | Full connectivity; filtered permeability | Internal connectivity only | Poor permeability; barriers to cycling |

### Overall Rating Logic

```python
def calculate_overall_rating(aspects: list[Aspect]) -> Rating:
    """
    Overall rating is determined by the worst aspect rating.
    Red in any aspect = Red overall
    No red but amber in any = Amber overall
    All green = Green overall
    """
    ratings = [a.rating for a in aspects]
    if "red" in ratings:
        return "red"
    elif "amber" in ratings:
        return "amber"
    return "green"
```

---

## Added Components

### AgentOrchestrator

**Description:** Central coordinator for the review workflow. Manages MCP client connections to all three servers, executes the review workflow phases, handles errors and partial failures, and coordinates between ReviewAssessor, PolicyComparer, and ReviewGenerator.

**Users:** ReviewWorker (invokes orchestrator for each review job)

**Kind:** Class

**Location:** `src/agent/orchestrator.py`

**Requirements References:**
- [agent-integration:FR-001]: Establishes and maintains MCP server connections
- [agent-integration:FR-002]: Orchestrates complete review workflow
- [agent-integration:NFR-001]: Workflow completes within time limits
- [agent-integration:NFR-005]: Handles partial failures gracefully

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Successful MCP connections | All 3 MCP servers running | Orchestrator initialises | Connections established to cherwell-scraper, document-store, policy-kb |
| TS-02 | Reconnection on transient failure | MCP connection drops mid-workflow | Connection error detected | Reconnects with exponential backoff; workflow resumes |
| TS-03 | Complete workflow execution | Valid review job | Orchestrator executes workflow | All 5 phases complete in order; review result produced |
| TS-04 | Scraper failure handling | Cherwell scraper returns error | Fetching metadata phase | Workflow fails gracefully; error details captured; `review.failed` published |
| TS-05 | Partial document ingestion | 2 of 24 documents fail ingestion | Ingesting documents phase | Workflow continues; failed documents logged; review produced with available docs |
| TS-06 | Cancellation handling | Cancellation flag set | Between phases | Workflow stops; status set to "cancelled" |
| TS-07 | State persistence | Workflow in phase 3 | Worker restarts | Orchestrator resumes from phase 3 (idempotent) |
| TS-08 | All servers unavailable | All MCP servers down | Orchestrator initialises | Fails with clear error after retry exhaustion |

### ReviewAssessor

**Description:** Performs the cycling-focused assessment of application documents. Evaluates cycle parking, routes, junctions, and permeability. Uses document search to find relevant content and structures findings for each aspect.

**Users:** AgentOrchestrator (delegates assessment task)

**Kind:** Class

**Location:** `src/agent/assessor.py`

**Requirements References:**
- [agent-integration:FR-004]: Assess cycle parking provision
- [agent-integration:FR-005]: Assess cycle routes
- [agent-integration:FR-006]: Assess junction design
- [agent-integration:FR-007]: Assess permeability
- [agent-integration:FR-016]: Handle missing documents

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Assess adequate cycle parking | Documents mention "48 Sheffield stands" | Assess cycle parking | Returns amber rating with note about cargo bike spaces |
| TS-02 | Assess no cycle parking mentioned | No cycling content in documents | Assess cycle parking | Returns red rating; flags as critical issue |
| TS-03 | Assess compliant cycle routes | Documents show 3m segregated track | Assess cycle routes | Returns green rating with LTN 1/20 compliance noted |
| TS-04 | Assess non-compliant shared use | Documents show shared-use path with high traffic | Assess cycle routes | Returns red rating; cites LTN 1/20 Table 5-2 |
| TS-05 | Assess junction with protection | Documents show protected cycle crossing | Assess junctions | Returns green rating; notes LTN 1/20 Ch.6 compliance |
| TS-06 | Assess junction without protection | Documents show unprotected junction | Assess junctions | Returns red rating; specific LTN 1/20 requirements cited |
| TS-07 | Assess good permeability | Documents show filtered connections | Assess permeability | Returns green rating; notes connections |
| TS-08 | Assess missing permeability | No through connections for cyclists | Assess permeability | Returns amber/red rating; notes missed opportunity |
| TS-09 | Assess single dwelling application | Minor application with limited scope | Assess all aspects | Appropriately scoped assessment; some aspects marked N/A |
| TS-10 | Handle missing Transport Assessment | Key document not in application | Assess transport aspects | Notes absence; assesses based on available documents; flags for human review |

### PolicyComparer

**Description:** Searches the policy knowledge base and compares application proposals against policy requirements. Uses the application's validation date to ensure correct policy revision selection. Produces the policy compliance matrix.

**Users:** AgentOrchestrator (delegates policy comparison task)

**Kind:** Class

**Location:** `src/agent/policy_comparer.py`

**Requirements References:**
- [agent-integration:FR-003]: Use application validation date for policy queries
- [agent-integration:FR-008]: Generate policy compliance matrix
- [agent-integration:FR-014]: Track policy revisions used
- [agent-integration:NFR-003]: Citation accuracy

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Query with validation date | Application validated 2024-03-15 | Search policy | Returns results from revisions effective on 2024-03-15 |
| TS-02 | NPPF revision selection | Application from 2024 | Query NPPF | Returns 2024 NPPF, not 2025 revision |
| TS-03 | Missing validation date fallback | Application has no validation date | Search policy | Falls back to current date with warning |
| TS-04 | Generate compliance matrix | Issues identified by assessor | Compare against policy | Matrix shows requirement, source, revision, compliance status |
| TS-05 | Track revisions used | Multiple policies queried | Complete comparison | Metadata records all revision_ids and version_labels used |
| TS-06 | Policy not applicable | Application type doesn't require LTN 1/20 compliance | Generate matrix | Policies not applicable are omitted from matrix |
| TS-07 | Citation verification | Policy reference in review | Verify citation | Reference corresponds to actual policy chunk retrieved |
| TS-08 | Multiple sources for requirement | Requirement covered by local and national policy | Compare | Cites most specific applicable policy |

### ReviewGenerator

**Description:** Transforms assessment results and policy comparison into structured JSON output and formatted Markdown document. Calculates overall rating, formats recommendations and conditions.

**Users:** AgentOrchestrator (delegates output generation)

**Kind:** Class

**Location:** `src/agent/generator.py`

**Requirements References:**
- [agent-integration:FR-009]: Generate specific recommendations
- [agent-integration:FR-010]: Generate suggested conditions
- [agent-integration:FR-011]: Produce structured JSON output
- [agent-integration:FR-012]: Produce Markdown output
- [agent-integration:FR-013]: Calculate overall rating

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Generate JSON output | Complete assessment results | Generate review | JSON validates against schema; all required fields present |
| TS-02 | Generate Markdown output | Complete assessment results | Generate review | Well-formatted Markdown with headers, tables, citations |
| TS-03 | Calculate red overall rating | One aspect rated red | Calculate rating | Overall rating is red |
| TS-04 | Calculate amber overall rating | No red, one amber aspect | Calculate rating | Overall rating is amber |
| TS-05 | Calculate green overall rating | All aspects green | Calculate rating | Overall rating is green |
| TS-06 | Generate actionable recommendations | Issues identified | Generate recommendations | Specific actions with policy justification |
| TS-07 | Generate conditions for approval with mods | Amber rating application | Generate conditions | Conditions address identified issues |
| TS-08 | Omit conditions for refusal | Red rating with fundamental issues | Generate conditions | Conditions section omitted or notes refusal recommended |
| TS-09 | Positive acknowledgment for compliance | Fully compliant application | Generate review | Positive tone; acknowledges good practice |
| TS-10 | Handle optional fields | Some aspects not applicable | Generate JSON | Optional fields cleanly omitted |

### ReviewTemplates

**Description:** Template strings and formatters for review output. Defines the Markdown structure, table formats, and section ordering. Ensures consistent output formatting across all reviews.

**Users:** ReviewGenerator

**Kind:** Module

**Location:** `src/agent/templates.py`

**Requirements References:**
- [agent-integration:FR-012]: Markdown template matching DESIGN.md Section 5

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Format application summary | Application metadata | Format summary section | Correct Markdown with all fields |
| TS-02 | Format assessment table | Aspect ratings | Format table | Markdown table with ratings and key issues |
| TS-03 | Format policy compliance matrix | Compliance items | Format matrix | Markdown table with requirement, source, status |
| TS-04 | Format recommendations list | List of recommendations | Format section | Numbered list with policy citations |
| TS-05 | Render in common viewers | Generated Markdown | View in GitHub, VS Code | Renders correctly with tables and formatting |

### ProgressTracker

**Description:** Manages phase transitions and webhook event publishing during review workflow. Tracks timing for each phase, publishes progress events to Redis, and provides sub-progress updates for long-running phases.

**Users:** AgentOrchestrator

**Kind:** Class

**Location:** `src/agent/progress.py`

**Requirements References:**
- [agent-integration:FR-015]: Publish progress events at each phase

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Publish phase transition | Workflow enters new phase | Transition to "ingesting_documents" | `review.progress` event published with phase info |
| TS-02 | Publish sub-progress | Long-running phase | Processing document 5 of 22 | Event includes "Ingesting document 5 of 22" detail |
| TS-03 | Track phase timing | Phase completes | Phase transition | Duration recorded in metadata |
| TS-04 | Calculate percent complete | In phase 3 of 5 | Query progress | Returns appropriate percentage (e.g., 50%) |
| TS-05 | Persist state to Redis | Phase transition | Transition occurs | State persisted for recovery |

### MCPClientManager

**Description:** Manages connections to all three MCP servers. Handles connection establishment, health monitoring, reconnection on failure, and clean shutdown. Provides tool call interface for orchestrator.

**Users:** AgentOrchestrator

**Kind:** Class

**Location:** `src/agent/mcp_client.py`

**Requirements References:**
- [agent-integration:FR-001]: Connect to MCP servers
- [agent-integration:NFR-005]: Reconnection on transient failure

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Connect to all servers | All servers healthy | Initialize manager | 3 connections established |
| TS-02 | Health check detection | One server goes down | Health check runs | Detects unhealthy server |
| TS-03 | Automatic reconnection | Connection lost | Next tool call | Reconnects before call; call succeeds |
| TS-04 | Reconnection backoff | Repeated failures | Reconnection attempts | Exponential backoff: 1s, 2s, 4s |
| TS-05 | Tool call routing | Tool call request | Call search_policy | Routed to policy-kb server |
| TS-06 | Clean shutdown | Workflow complete | Shutdown manager | All connections closed gracefully |
| TS-07 | Timeout handling | Server hangs | Tool call with timeout | Times out after configured period |

### ClaudeClient

**Description:** Wrapper for Anthropic Claude API. Handles message construction, tool use, streaming responses, token tracking, and error handling. Implements retry logic for transient API errors.

**Users:** ReviewAssessor, PolicyComparer, ReviewGenerator

**Kind:** Class

**Location:** `src/agent/claude_client.py`

**Requirements References:**
- [agent-integration:NFR-001]: Performance within time limits
- [agent-integration:NFR-004]: Token efficiency

**Test Scenarios:**

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| TS-01 | Successful completion | Valid prompt | Send message | Response returned; tokens tracked |
| TS-02 | Tool use handling | Response includes tool_use | Process response | Tool calls extracted and returned |
| TS-03 | Streaming response | Large response | Stream enabled | First token received quickly |
| TS-04 | Rate limit handling | 429 response | Send message | Waits per retry-after; retries |
| TS-05 | Transient error retry | 503 response | Send message | Retries with backoff; succeeds |
| TS-06 | Token usage tracking | Multiple calls | Track tokens | Cumulative count accurate |
| TS-07 | Context window management | Large document context | Build prompt | Truncates if necessary; warns |

---

## Used Components

### From Phase 1 (Foundation API)

**RedisClient**
- Location: `src/shared/redis_client.py`
- Provides: Job state storage, progress event publishing, pub/sub
- Used By: AgentOrchestrator, ProgressTracker

**WebhookDispatcher**
- Location: `src/worker/webhook_dispatcher.py`
- Provides: Signed webhook delivery with retry
- Used By: ProgressTracker (via Redis pub/sub)

**CherwellScraperMCP**
- Location: `src/mcp_servers/cherwell_scraper/server.py`
- Provides: `get_application_details`, `download_all_documents`
- Used By: AgentOrchestrator (via MCPClientManager)

### From Phase 2 (Document Processing)

**DocumentStoreMCP**
- Location: `src/mcp_servers/document_store/server.py`
- Provides: `ingest_document`, `search_application_docs`, `get_document_text`
- Used By: AgentOrchestrator (via MCPClientManager)

### From Phase 3 (Policy Knowledge Base)

**PolicyKBMCP**
- Location: `src/mcp_servers/policy_kb/server.py`
- Provides: `search_policy`, `get_policy_section`, `list_policy_revisions`
- Used By: AgentOrchestrator, PolicyComparer (via MCPClientManager)

**PolicyRegistry**
- Location: `src/shared/policy_registry.py`
- Provides: Revision lookup by effective date
- Used By: PolicyComparer

### External Dependencies

**Anthropic SDK**
- Location: Python package `anthropic`
- Provides: Claude API client with tool use support
- Used By: ClaudeClient

**MCP Python Library**
- Location: Python package `mcp`
- Provides: MCP client for SSE transport
- Used By: MCPClientManager

---

## Integration Test Scenarios

| ID | Scenario | Given | When | Then | Components Involved |
|----|----------|-------|------|------|---------------------|
| ITS-01 | Complete workflow with mocked MCP | Mock MCP servers with fixtures | Submit review job | All phases complete; review produced | Orchestrator, Assessor, PolicyComparer, Generator |
| ITS-02 | Validation date policy selection | Application validated 2024-03-15 | Policy search | 2024 NPPF revision returned, not 2025 | PolicyComparer, PolicyKBMCP |
| ITS-03 | Document ingestion progress | 10 documents to process | Ingest documents | 10 progress events with sub-progress | Orchestrator, ProgressTracker, DocumentStoreMCP |
| ITS-04 | Partial document failure recovery | 2 of 10 documents corrupt | Ingest documents | 8 documents processed; 2 errors logged; review produced | Orchestrator, DocumentStoreMCP |
| ITS-05 | MCP reconnection during workflow | MCP connection drops in phase 3 | Processing continues | Reconnects; phase 3 completes | MCPClientManager, Orchestrator |
| ITS-06 | Review with all green aspects | Compliant application fixtures | Generate review | Green overall rating; positive acknowledgment | Assessor, Generator |
| ITS-07 | Review with red aspects | Non-compliant application fixtures | Generate review | Red overall rating; refusal recommended | Assessor, Generator |
| ITS-08 | Token usage tracking | Complete review | Check metadata | Total tokens < 50,000 | ClaudeClient, Generator |
| ITS-09 | Missing Transport Assessment | Application without TA | Generate review | Flags for human review; assesses available docs | Assessor, Generator |
| ITS-10 | Policy citation verification | Review with policy citations | Verify citations | All citations correspond to retrieved policy content | PolicyComparer, Generator |

---

## E2E Test Scenarios

| ID | Scenario | Given | When | Then | User Journey |
|----|----------|-------|------|------|--------------|
| E2E-01 | Full review lifecycle | System running, valid Cherwell ref | POST /reviews, wait for completion | Status progresses through all phases; review.completed webhook received; GET /reviews/{id} returns full review | Submit → Receive webhooks → Retrieve result |
| E2E-02 | Review with webhook notifications | System running, webhook configured | POST /reviews with webhook | Receive review.started, multiple review.progress, review.completed webhooks | Submit → Monitor webhooks |
| E2E-03 | Review retrieval via polling | System running, no webhook | POST /reviews, poll status | Status endpoint shows phase progression; final GET returns complete review | Submit → Poll → Retrieve |
| E2E-04 | Failed review handling | Invalid application reference | POST /reviews | review.failed webhook with error details; GET returns failed status | Submit → Receive failure |
| E2E-05 | Real application review | System with real MCP servers | POST /reviews with known ref | Complete review produced with sensible content | Full production flow |

---

## Task Breakdown

> **CRITICAL: Tests are written WITH implementation, not after.**

### Phase 1: MCP Client Infrastructure & Progress Tracking

- Task 1: Implement MCPClientManager with connection lifecycle
  - Status: Complete
  - SSE connections to 3 MCP servers
  - Health monitoring and reconnection logic
  - Tool call routing
  - Requirements: [agent-integration:FR-001], [agent-integration:NFR-005]
  - Test Scenarios: [agent-integration:MCPClientManager/TS-01], [agent-integration:MCPClientManager/TS-02], [agent-integration:MCPClientManager/TS-03], [agent-integration:MCPClientManager/TS-04], [agent-integration:MCPClientManager/TS-05], [agent-integration:MCPClientManager/TS-06], [agent-integration:MCPClientManager/TS-07]

- Task 2: Implement ProgressTracker with phase management
  - Status: Complete
  - Phase transition events
  - Sub-progress for long phases
  - Redis state persistence
  - Requirements: [agent-integration:FR-015]
  - Test Scenarios: [agent-integration:ProgressTracker/TS-01], [agent-integration:ProgressTracker/TS-02], [agent-integration:ProgressTracker/TS-03], [agent-integration:ProgressTracker/TS-04], [agent-integration:ProgressTracker/TS-05]

- Task 3: Implement ClaudeClient wrapper
  - Status: Complete
  - Anthropic SDK integration
  - Tool use handling
  - Token tracking
  - Retry logic for transient errors
  - Requirements: [agent-integration:NFR-001], [agent-integration:NFR-004]
  - Test Scenarios: [agent-integration:ClaudeClient/TS-01], [agent-integration:ClaudeClient/TS-02], [agent-integration:ClaudeClient/TS-03], [agent-integration:ClaudeClient/TS-04], [agent-integration:ClaudeClient/TS-05], [agent-integration:ClaudeClient/TS-06], [agent-integration:ClaudeClient/TS-07]

### Phase 2: Core Orchestrator & Workflow

- Task 4: Implement AgentOrchestrator workflow skeleton
  - Status: Complete
  - Phase state machine
  - MCP tool call coordination
  - Error handling framework
  - Requirements: [agent-integration:FR-002], [agent-integration:NFR-001]
  - Test Scenarios: [agent-integration:AgentOrchestrator/TS-01], [agent-integration:AgentOrchestrator/TS-03], [agent-integration:AgentOrchestrator/TS-06], [agent-integration:AgentOrchestrator/TS-07]

- Task 5: Implement orchestrator error recovery
  - Status: Complete
  - Partial failure handling
  - State recovery on restart
  - Graceful degradation
  - Requirements: [agent-integration:NFR-005]
  - Test Scenarios: [agent-integration:AgentOrchestrator/TS-02], [agent-integration:AgentOrchestrator/TS-04], [agent-integration:AgentOrchestrator/TS-05], [agent-integration:AgentOrchestrator/TS-08]

- Task 6: Wire orchestrator to worker job handler
  - Status: Complete
  - Integration with arq worker
  - Review job processing
  - Result storage
  - Requirements: [agent-integration:FR-002]
  - Test Scenarios: [agent-integration:ITS-01]

### Phase 3: Review Assessment Logic

- Task 7: Implement ReviewAssessor core
  - Status: Complete
  - Document search integration
  - Assessment structure
  - Requirements: [agent-integration:FR-004], [agent-integration:FR-005], [agent-integration:FR-006], [agent-integration:FR-007]
  - Test Scenarios: [agent-integration:ReviewAssessor/TS-01], [agent-integration:ReviewAssessor/TS-03], [agent-integration:ReviewAssessor/TS-05], [agent-integration:ReviewAssessor/TS-07]

- Task 8: Implement cycle parking assessment
  - Status: Complete
  - Search for parking content
  - Evaluate against standards
  - Rate and document findings
  - Requirements: [agent-integration:FR-004]
  - Test Scenarios: [agent-integration:ReviewAssessor/TS-01], [agent-integration:ReviewAssessor/TS-02]

- Task 9: Implement cycle routes assessment
  - Status: Complete
  - Search for route content
  - Compare against LTN 1/20
  - Rate and document findings
  - Requirements: [agent-integration:FR-005]
  - Test Scenarios: [agent-integration:ReviewAssessor/TS-03], [agent-integration:ReviewAssessor/TS-04]

- Task 10: Implement junction design assessment
  - Status: Complete
  - Search for junction content
  - Compare against LTN 1/20 Ch.6
  - Rate and document findings
  - Requirements: [agent-integration:FR-006]
  - Test Scenarios: [agent-integration:ReviewAssessor/TS-05], [agent-integration:ReviewAssessor/TS-06]

- Task 11: Implement permeability assessment
  - Status: Complete
  - Search for connectivity content
  - Evaluate filtered permeability
  - Rate and document findings
  - Requirements: [agent-integration:FR-007]
  - Test Scenarios: [agent-integration:ReviewAssessor/TS-07], [agent-integration:ReviewAssessor/TS-08]

- Task 12: Implement missing document handling
  - Status: Complete
  - Detect missing key documents
  - Adjust assessment scope
  - Flag for human review
  - Requirements: [agent-integration:FR-016]
  - Test Scenarios: [agent-integration:ReviewAssessor/TS-09], [agent-integration:ReviewAssessor/TS-10]

### Phase 4: Policy Comparison & Output Generation

- Task 13: Implement PolicyComparer
  - Status: Complete
  - Validation date policy queries
  - Compliance matrix generation
  - Revision tracking
  - Requirements: [agent-integration:FR-003], [agent-integration:FR-008], [agent-integration:FR-014]
  - Test Scenarios: [agent-integration:PolicyComparer/TS-01], [agent-integration:PolicyComparer/TS-02], [agent-integration:PolicyComparer/TS-03], [agent-integration:PolicyComparer/TS-04], [agent-integration:PolicyComparer/TS-05], [agent-integration:PolicyComparer/TS-06], [agent-integration:PolicyComparer/TS-07], [agent-integration:PolicyComparer/TS-08]

- Task 14: Implement ReviewGenerator JSON output
  - Status: Complete
  - Structured JSON matching schema
  - Overall rating calculation
  - All required fields
  - Requirements: [agent-integration:FR-011], [agent-integration:FR-013]
  - Test Scenarios: [agent-integration:ReviewGenerator/TS-01], [agent-integration:ReviewGenerator/TS-03], [agent-integration:ReviewGenerator/TS-04], [agent-integration:ReviewGenerator/TS-05], [agent-integration:ReviewGenerator/TS-10]

- Task 15: Implement ReviewGenerator recommendations
  - Status: Complete
  - Actionable recommendations with policy
  - Suggested conditions
  - Requirements: [agent-integration:FR-009], [agent-integration:FR-010]
  - Test Scenarios: [agent-integration:ReviewGenerator/TS-06], [agent-integration:ReviewGenerator/TS-07], [agent-integration:ReviewGenerator/TS-08], [agent-integration:ReviewGenerator/TS-09]

- Task 16: Implement ReviewTemplates and Markdown output
  - Status: Complete
  - Template strings matching DESIGN.md Section 5
  - Markdown formatting
  - Table rendering
  - Requirements: [agent-integration:FR-012]
  - Test Scenarios: [agent-integration:ReviewGenerator/TS-02], [agent-integration:ReviewTemplates/TS-01], [agent-integration:ReviewTemplates/TS-02], [agent-integration:ReviewTemplates/TS-03], [agent-integration:ReviewTemplates/TS-04], [agent-integration:ReviewTemplates/TS-05]

### Phase 5: Integration & End-to-End Testing

- Task 17: Integration tests with mocked MCP servers
  - Status: Complete
  - Complete workflow testing
  - Partial failure scenarios
  - Policy revision selection
  - Requirements: All FRs and NFRs
  - Test Scenarios: [agent-integration:ITS-01], [agent-integration:ITS-02], [agent-integration:ITS-03], [agent-integration:ITS-04], [agent-integration:ITS-05], [agent-integration:ITS-06], [agent-integration:ITS-07], [agent-integration:ITS-08], [agent-integration:ITS-09], [agent-integration:ITS-10]

- Task 18: Citation accuracy verification
  - Status: Complete
  - Verify all citations grounded in RAG
  - Test citation format
  - Requirements: [agent-integration:NFR-002], [agent-integration:NFR-003]
  - Test Scenarios: [agent-integration:ITS-10]

- Task 19: Performance testing and token optimization
  - Status: Complete
  - Measure end-to-end timing
  - Optimize prompts for token efficiency
  - Verify <5 minute average
  - Requirements: [agent-integration:NFR-001], [agent-integration:NFR-004]
  - Test Scenarios: [agent-integration:ITS-08]

- Task 20: End-to-end smoke tests
  - Status: Complete
  - Full system tests with real servers
  - Webhook verification
  - Polling verification
  - Requirements: All FRs
  - Test Scenarios: [agent-integration:E2E-01], [agent-integration:E2E-02], [agent-integration:E2E-03], [agent-integration:E2E-04], [agent-integration:E2E-05]

---

## Requirements Validation

### Functional Requirements

- [agent-integration:FR-001]: Phase 1 Task 1
- [agent-integration:FR-002]: Phase 2 Task 4, Phase 2 Task 6
- [agent-integration:FR-003]: Phase 4 Task 13
- [agent-integration:FR-004]: Phase 3 Task 7, Phase 3 Task 8
- [agent-integration:FR-005]: Phase 3 Task 7, Phase 3 Task 9
- [agent-integration:FR-006]: Phase 3 Task 7, Phase 3 Task 10
- [agent-integration:FR-007]: Phase 3 Task 7, Phase 3 Task 11
- [agent-integration:FR-008]: Phase 4 Task 13
- [agent-integration:FR-009]: Phase 4 Task 15
- [agent-integration:FR-010]: Phase 4 Task 15
- [agent-integration:FR-011]: Phase 4 Task 14
- [agent-integration:FR-012]: Phase 4 Task 16
- [agent-integration:FR-013]: Phase 4 Task 14
- [agent-integration:FR-014]: Phase 4 Task 13
- [agent-integration:FR-015]: Phase 1 Task 2
- [agent-integration:FR-016]: Phase 3 Task 12

### Non-Functional Requirements

- [agent-integration:NFR-001]: Phase 1 Task 3, Phase 2 Task 4, Phase 5 Task 19
- [agent-integration:NFR-002]: Phase 5 Task 18
- [agent-integration:NFR-003]: Phase 5 Task 18
- [agent-integration:NFR-004]: Phase 1 Task 3, Phase 5 Task 19
- [agent-integration:NFR-005]: Phase 1 Task 1, Phase 2 Task 5

---

## Test Scenario Validation

### Component Scenarios

- [agent-integration:AgentOrchestrator/TS-01]: Phase 2 Task 4
- [agent-integration:AgentOrchestrator/TS-02]: Phase 2 Task 5
- [agent-integration:AgentOrchestrator/TS-03]: Phase 2 Task 4
- [agent-integration:AgentOrchestrator/TS-04]: Phase 2 Task 5
- [agent-integration:AgentOrchestrator/TS-05]: Phase 2 Task 5
- [agent-integration:AgentOrchestrator/TS-06]: Phase 2 Task 4
- [agent-integration:AgentOrchestrator/TS-07]: Phase 2 Task 4
- [agent-integration:AgentOrchestrator/TS-08]: Phase 2 Task 5

- [agent-integration:ReviewAssessor/TS-01]: Phase 3 Task 8
- [agent-integration:ReviewAssessor/TS-02]: Phase 3 Task 8
- [agent-integration:ReviewAssessor/TS-03]: Phase 3 Task 9
- [agent-integration:ReviewAssessor/TS-04]: Phase 3 Task 9
- [agent-integration:ReviewAssessor/TS-05]: Phase 3 Task 10
- [agent-integration:ReviewAssessor/TS-06]: Phase 3 Task 10
- [agent-integration:ReviewAssessor/TS-07]: Phase 3 Task 11
- [agent-integration:ReviewAssessor/TS-08]: Phase 3 Task 11
- [agent-integration:ReviewAssessor/TS-09]: Phase 3 Task 12
- [agent-integration:ReviewAssessor/TS-10]: Phase 3 Task 12

- [agent-integration:PolicyComparer/TS-01]: Phase 4 Task 13
- [agent-integration:PolicyComparer/TS-02]: Phase 4 Task 13
- [agent-integration:PolicyComparer/TS-03]: Phase 4 Task 13
- [agent-integration:PolicyComparer/TS-04]: Phase 4 Task 13
- [agent-integration:PolicyComparer/TS-05]: Phase 4 Task 13
- [agent-integration:PolicyComparer/TS-06]: Phase 4 Task 13
- [agent-integration:PolicyComparer/TS-07]: Phase 4 Task 13
- [agent-integration:PolicyComparer/TS-08]: Phase 4 Task 13

- [agent-integration:ReviewGenerator/TS-01]: Phase 4 Task 14
- [agent-integration:ReviewGenerator/TS-02]: Phase 4 Task 16
- [agent-integration:ReviewGenerator/TS-03]: Phase 4 Task 14
- [agent-integration:ReviewGenerator/TS-04]: Phase 4 Task 14
- [agent-integration:ReviewGenerator/TS-05]: Phase 4 Task 14
- [agent-integration:ReviewGenerator/TS-06]: Phase 4 Task 15
- [agent-integration:ReviewGenerator/TS-07]: Phase 4 Task 15
- [agent-integration:ReviewGenerator/TS-08]: Phase 4 Task 15
- [agent-integration:ReviewGenerator/TS-09]: Phase 4 Task 15
- [agent-integration:ReviewGenerator/TS-10]: Phase 4 Task 14

- [agent-integration:ReviewTemplates/TS-01]: Phase 4 Task 16
- [agent-integration:ReviewTemplates/TS-02]: Phase 4 Task 16
- [agent-integration:ReviewTemplates/TS-03]: Phase 4 Task 16
- [agent-integration:ReviewTemplates/TS-04]: Phase 4 Task 16
- [agent-integration:ReviewTemplates/TS-05]: Phase 4 Task 16

- [agent-integration:ProgressTracker/TS-01]: Phase 1 Task 2
- [agent-integration:ProgressTracker/TS-02]: Phase 1 Task 2
- [agent-integration:ProgressTracker/TS-03]: Phase 1 Task 2
- [agent-integration:ProgressTracker/TS-04]: Phase 1 Task 2
- [agent-integration:ProgressTracker/TS-05]: Phase 1 Task 2

- [agent-integration:MCPClientManager/TS-01]: Phase 1 Task 1
- [agent-integration:MCPClientManager/TS-02]: Phase 1 Task 1
- [agent-integration:MCPClientManager/TS-03]: Phase 1 Task 1
- [agent-integration:MCPClientManager/TS-04]: Phase 1 Task 1
- [agent-integration:MCPClientManager/TS-05]: Phase 1 Task 1
- [agent-integration:MCPClientManager/TS-06]: Phase 1 Task 1
- [agent-integration:MCPClientManager/TS-07]: Phase 1 Task 1

- [agent-integration:ClaudeClient/TS-01]: Phase 1 Task 3
- [agent-integration:ClaudeClient/TS-02]: Phase 1 Task 3
- [agent-integration:ClaudeClient/TS-03]: Phase 1 Task 3
- [agent-integration:ClaudeClient/TS-04]: Phase 1 Task 3
- [agent-integration:ClaudeClient/TS-05]: Phase 1 Task 3
- [agent-integration:ClaudeClient/TS-06]: Phase 1 Task 3
- [agent-integration:ClaudeClient/TS-07]: Phase 1 Task 3

### Integration Scenarios

- [agent-integration:ITS-01]: Phase 2 Task 6, Phase 5 Task 17
- [agent-integration:ITS-02]: Phase 5 Task 17
- [agent-integration:ITS-03]: Phase 5 Task 17
- [agent-integration:ITS-04]: Phase 5 Task 17
- [agent-integration:ITS-05]: Phase 5 Task 17
- [agent-integration:ITS-06]: Phase 5 Task 17
- [agent-integration:ITS-07]: Phase 5 Task 17
- [agent-integration:ITS-08]: Phase 5 Task 17, Phase 5 Task 19
- [agent-integration:ITS-09]: Phase 5 Task 17
- [agent-integration:ITS-10]: Phase 5 Task 17, Phase 5 Task 18

### E2E Scenarios

- [agent-integration:E2E-01]: Phase 5 Task 20
- [agent-integration:E2E-02]: Phase 5 Task 20
- [agent-integration:E2E-03]: Phase 5 Task 20
- [agent-integration:E2E-04]: Phase 5 Task 20
- [agent-integration:E2E-05]: Phase 5 Task 20

---

## Appendix

### Glossary

- **MCP:** Model Context Protocol - tool interface for AI agents
- **SSE:** Server-Sent Events - transport protocol for MCP
- **LTN 1/20:** Local Transport Note 1/20 - Department for Transport cycle infrastructure guidance
- **PCU:** Passenger Car Units - measure of traffic flow
- **Filtered Permeability:** Street design allowing cycle through-movement but not motor vehicles
- **RAG:** Retrieval-Augmented Generation - grounding LLM responses in retrieved documents

### Agent System Prompt

The core system prompt for the review agent (referenced from DESIGN.md Section 4.2):

```
You are a planning application reviewer acting on behalf of a local cycling
advocacy group in the Cherwell District. Your role is to assess planning
applications from the perspective of people who walk and cycle.

For each application you review, you should:

1. UNDERSTAND THE PROPOSAL - Summarise what is being proposed, its location,
   and scale.

2. ASSESS TRANSPORT & MOVEMENT - Evaluate:
   - Cycle parking provision (quantity, type, location, security, accessibility)
   - Cycle route provision (on-site and connections to existing network)
   - Whether cycle infrastructure meets LTN 1/20 standards
   - Pedestrian permeability and filtered permeability for cycles
   - Impact on existing cycle routes and rights of way
   - Trip generation and modal split assumptions in any Transport Assessment

3. COMPARE AGAINST POLICY - Reference specific policies:
   - LTN 1/20 design standards (especially Table 5-2 on segregation triggers,
     Chapter 5 on geometric design, Chapter 6 on junctions)
   - Cherwell Local Plan policies on sustainable transport
   - NPPF paragraphs on sustainable transport
   - Oxfordshire LCWIP route proposals
   - Active Travel England assessment criteria

4. IDENTIFY ISSUES - Flag:
   - Non-compliance with LTN 1/20
   - Missing or inadequate cycle parking
   - Missed opportunities for active travel connections
   - Hostile junction designs for cyclists
   - Failure to provide filtered permeability
   - Inadequate width or shared-use paths where segregation is warranted

5. MAKE RECOMMENDATIONS - Suggest specific, constructive improvements
   with policy justification.

Always cite specific policy references. Be constructive and evidence-based.
Your review should be suitable for submission as a formal consultation response.
```

### References

- [Master Design Document](../../docs/DESIGN.md) - Sections 4 and 5
- [Specification](./specification.md) - Functional and Non-Functional Requirements
- [Foundation API Design](../foundation-api/design.md) - Used components
- [Anthropic Claude Documentation](https://docs.anthropic.com/)
- [MCP Specification](https://modelcontextprotocol.io/)

### Change History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-06 | SDD Agent | Initial design |
