# Specification: Agent Integration

**Version:** 1.0
**Date:** 2026-02-06
**Status:** Draft

---

## Problem Statement

The system has document storage and policy knowledge base components, but needs an AI agent to orchestrate the review workflow: fetch documents, search for cycling-relevant content, compare against policy, and generate a structured review suitable for submission as a consultation response.

## Beneficiaries

**Primary:**
- Cycling advocacy group members receiving automated reviews
- Planning officers receiving structured consultation responses

**Secondary:**
- System operators monitoring agent performance
- Developers extending agent capabilities

---

## Outcomes

**Must Haves**
- AI agent that orchestrates the complete review workflow
- Agent calls MCP tools from all three servers (scraper, document store, policy KB)
- Structured review output in JSON and Markdown formats
- Review addresses: cycle parking, routes, junctions, permeability, policy compliance
- Full lifecycle from API request to webhook completion

**Nice-to-haves**
- Configurable focus areas (subset of review aspects)
- Iterative refinement of review based on follow-up queries

---

## Explicitly Out of Scope

- Interactive user conversation (reviews are fully automated)
- Drawing/image analysis (flagged for human review)
- Automatic submission to planning portal

---

## Functional Requirements

### FR-001: Connect to MCP Servers
**Description:** The agent worker must establish connections to all three MCP servers (cherwell-scraper, document-store, policy-kb) and maintain them during review processing.

**Examples:**
- Positive case: Worker connects to all servers at startup
- Edge case: Reconnection on transient failure

### FR-002: Orchestrate Review Workflow
**Description:** The agent must execute the review workflow: fetch metadata → download documents → ingest documents → search for cycling content → compare against policy → generate review.

**Examples:**
- Positive case: Complete workflow executes for valid application
- Edge case: Graceful failure with `review.failed` webhook if scraper fails

### FR-003: Use Application Validation Date
**Description:** The agent must use the application's validation date when querying the policy KB to ensure correct policy revision selection.

**Examples:**
- Positive case: Query for 2024 application retrieves 2024 NPPF, not 2025
- Edge case: Missing validation date falls back to current date

### FR-004: Assess Cycle Parking
**Description:** The agent must evaluate cycle parking provision: quantity, type, location, security, accessibility, cargo bike spaces.

**Examples:**
- Positive case: Review notes "48 Sheffield stands proposed" and compares to Local Plan requirement
- Edge case: No cycle parking mentioned flagged as issue

### FR-005: Assess Cycle Routes
**Description:** The agent must evaluate on-site cycle routes and connections to existing network, comparing against LTN 1/20 standards.

**Examples:**
- Positive case: Review assesses route widths against Table 5-2 segregation triggers
- Edge case: Shared-use paths flagged where segregation warranted

### FR-006: Assess Junction Design
**Description:** The agent must evaluate junction designs against LTN 1/20 Chapter 6 requirements for cycle safety.

**Examples:**
- Positive case: Review identifies missing protected cycle provision at site access
- Edge case: No junction modifications required noted as N/A

### FR-007: Assess Permeability
**Description:** The agent must evaluate pedestrian and cycle permeability, including filtered permeability opportunities.

**Examples:**
- Positive case: Review notes internal routes but missing connection to adjacent area
- Edge case: Application for single dwelling appropriately scoped

### FR-008: Generate Policy Compliance Matrix
**Description:** The agent must produce a structured matrix showing compliance status for each relevant policy requirement.

**Examples:**
- Positive case: Table shows LTN 1/20 Table 5-2 requirement, compliance status, notes
- Edge case: Policies not applicable to application type omitted

### FR-009: Generate Recommendations
**Description:** The agent must provide specific, actionable recommendations with policy justification.

**Examples:**
- Positive case: "Provide segregated cycle track on eastern boundary per LTN 1/20 Table 5-2"
- Edge case: Fully compliant application receives positive acknowledgment

### FR-010: Generate Suggested Conditions
**Description:** The agent must propose planning conditions if approval is recommended with modifications.

**Examples:**
- Positive case: "Prior to occupation, submit detailed cycle parking layout for approval"
- Edge case: Conditions section omitted for outright refusal recommendation

### FR-011: Produce Structured JSON Output
**Description:** The agent must produce a structured JSON review matching the API response schema, including overall rating, aspects, policy compliance, recommendations.

**Examples:**
- Positive case: JSON validates against schema with all required fields
- Edge case: Optional fields omitted cleanly when not applicable

### FR-012: Produce Markdown Output
**Description:** The agent must produce a Markdown-formatted review document suitable for submission as a consultation response.

**Examples:**
- Positive case: Well-formatted document with headers, tables, and policy citations
- Edge case: Markdown renders correctly in common viewers

### FR-013: Calculate Overall Rating
**Description:** The agent must assign an overall rating (red/amber/green) based on the severity of issues identified.

**Examples:**
- Positive case: Red rating for LTN 1/20 non-compliance on main access
- Edge case: Green rating for fully compliant minor application

### FR-014: Track Policy Revisions Used
**Description:** The review metadata must record which policy revisions were consulted, enabling audit.

**Examples:**
- Positive case: Metadata includes `policy_revisions_used` array with source, revision_id, version_label
- Edge case: Policies not consulted (no relevant content) not listed

### FR-015: Publish Progress Events
**Description:** The agent must publish progress events at each workflow phase for webhook delivery.

**Examples:**
- Positive case: `review.progress` webhooks fired for each phase transition
- Edge case: Long-running phases include sub-progress (e.g., "Analysing document 5 of 22")

### FR-016: Handle Missing Documents
**Description:** The agent must handle cases where key documents (Transport Assessment, Design & Access Statement) are missing from the application.

**Examples:**
- Positive case: Review notes absence and assesses based on available documents
- Edge case: Application with only drawings flagged for human review

---

## Non-Functional Requirements

### NFR-001: Review Generation Time
**Category:** Performance
**Description:** Complete review generation must finish in reasonable time.
**Acceptance Threshold:** Average review completed within 5 minutes; maximum 10 minutes
**Verification:** Load testing with sample applications

### NFR-002: Review Accuracy
**Category:** Reliability
**Description:** Reviews must correctly identify policy compliance issues.
**Acceptance Threshold:** No false positives for clear compliance; no missed critical issues
**Verification:** Manual review of sample outputs by domain expert

### NFR-003: Citation Accuracy
**Category:** Reliability
**Description:** Policy citations must be accurate and verifiable.
**Acceptance Threshold:** 100% of citations correspond to actual policy content
**Verification:** Manual verification of citations against source documents

### NFR-004: Token Efficiency
**Category:** Performance
**Description:** Agent must use tokens efficiently to control API costs.
**Acceptance Threshold:** Average review uses <50,000 tokens
**Verification:** Monitoring of token usage per review

### NFR-005: Graceful Degradation
**Category:** Reliability
**Description:** Partial failures must result in partial reviews rather than complete failure.
**Acceptance Threshold:** Review produced even if one aspect cannot be assessed
**Verification:** Integration testing with simulated failures

---

## Open Questions

None at this time.

---

## Appendix

### Glossary

- **LTN 1/20:** Local Transport Note 1/20 - Department for Transport guidance on cycle infrastructure design
- **Filtered Permeability:** Street design that allows through movement for cyclists but not motor vehicles
- **PCU:** Passenger Car Units - measure of traffic flow
- **Table 5-2:** LTN 1/20 table defining segregation requirements based on traffic flow and speed

### References

- [Master Design Document](../../docs/DESIGN.md) - Section 4 Agent Workflow
- [LTN 1/20](https://www.gov.uk/government/publications/cycle-infrastructure-design-ltn-120) - Primary cycling guidance

### Change History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-06 | SDD Agent | Initial specification |
