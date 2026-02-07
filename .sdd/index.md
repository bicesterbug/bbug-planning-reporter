# SDD Index

> Features ordered by creation date (newest first)

This project implements the Cherwell Planning Application Cycle Advocacy Agent - an AI agent system that reviews planning applications from a cycling advocacy perspective.

## Feature Breakdown

The system is decomposed into the following features, aligned with the development phases in [docs/DESIGN.md](../docs/DESIGN.md):

| Date | Feature | Specification | Design | Status | Phase |
|------|---------|---------------|--------|--------|-------|
| 2026-02-07 | [key-documents](key-documents/) | [spec](key-documents/specification.md) | [design](key-documents/design.md) | Implementation Complete | Enhancement |
| 2026-02-07 | [response-letter](response-letter/) | [spec](response-letter/specification.md) | [design](response-letter/design.md) | Implementation Complete | Enhancement |
| 2026-02-07 | [document-filtering](document-filtering/) | [spec](document-filtering/specification.md) | [design](document-filtering/design.md) | Design Complete | Enhancement |
| 2026-02-06 | [api-hardening](api-hardening/) | [spec](api-hardening/specification.md) | [design](api-hardening/design.md) | Design Complete | 5 |
| 2026-02-06 | [agent-integration](agent-integration/) | [spec](agent-integration/specification.md) | [design](agent-integration/design.md) | Design Complete | 4 |
| 2026-02-06 | [policy-knowledge-base](policy-knowledge-base/) | [spec](policy-knowledge-base/specification.md) | [design](policy-knowledge-base/design.md) | Design Complete | 3 |
| 2026-02-06 | [document-processing](document-processing/) | [spec](document-processing/specification.md) | [design](document-processing/design.md) | Design Complete | 2 |
| 2026-02-06 | [foundation-api](foundation-api/) | [spec](foundation-api/specification.md) | [design](foundation-api/design.md) | Design Complete | 1 |

## Development Sequence

Features should be implemented in phase order (1 → 5). Each phase builds on the previous:

1. **foundation-api** - Project scaffolding, API skeleton, Redis queue, webhook framework, Cherwell scraper
2. **document-processing** - PDF extraction, OCR, chunking, ChromaDB integration, document ingestion
3. **policy-knowledge-base** - Policy registry, revision management, temporal queries, policy search
4. **agent-integration** - MCP client, agent orchestration, review generation, full workflow
5. **api-hardening** - Authentication, rate limiting, PDF export, testing, documentation

## Quick Links

- [Project Guidelines](project-guidelines.md) - Conventions for all development
- [Master Design Document](../docs/DESIGN.md) - Full architecture reference
