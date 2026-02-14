# SDD Index

> Features ordered by creation date (newest first)

This project implements the Cherwell Planning Application Cycle Advocacy Agent - an AI agent system that reviews planning applications from a cycling advocacy perspective.

## Feature Breakdown

The system is decomposed into the following features, aligned with the development phases in [docs/DESIGN.md](../docs/DESIGN.md):

| Date | Feature | Specification | Design | Status | Phase |
|------|---------|---------------|--------|--------|-------|
| 2026-02-14 | [cycle-route-assessment](cycle-route-assessment/) | [spec](cycle-route-assessment/specification.md) | — | Draft | Enhancement |
| 2026-02-14 | [reliable-category-filtering](reliable-category-filtering/) | [spec](reliable-category-filtering/specification.md) | [design](reliable-category-filtering/design.md) | Implemented | Bugfix |
| 2026-02-14 | [document-type-detection](document-type-detection/) | [spec](document-type-detection/specification.md) | [design](document-type-detection/design.md) | Implemented | Enhancement |
| 2026-02-14 | [download-filename-fix](download-filename-fix/) | [spec](download-filename-fix/specification.md) | [design](download-filename-fix/design.md) | Implemented | Bugfix |
| 2026-02-14 | [scraper-health-check](scraper-health-check/) | [spec](scraper-health-check/specification.md) | [design](scraper-health-check/design.md) | Implemented | Bugfix |
| 2026-02-13 | [review-workflow-redesign](review-workflow-redesign/) | [spec](review-workflow-redesign/specification.md) | [design](review-workflow-redesign/design.md) | Implemented | Enhancement |
| 2026-02-13 | [global-webhooks](global-webhooks/) | [spec](global-webhooks/specification.md) | [design](global-webhooks/design.md) | Implementation Complete | Enhancement |
| 2026-02-13 | [webhook-review-data](webhook-review-data/) | [spec](webhook-review-data/specification.md) | [design](webhook-review-data/design.md) | Superseded by global-webhooks | Bugfix |
| 2026-02-10 | [review-progress](review-progress/) | [spec](review-progress/specification.md) | [design](review-progress/design.md) | Implementation Complete | Bugfix |
| 2026-02-08 | [ci-cd-github-actions](ci-cd-github-actions/) | [spec](ci-cd-github-actions/specification.md) | [design](ci-cd-github-actions/design.md) | Implementation Complete | Infrastructure |
| 2026-02-08 | [structured-review-output](structured-review-output/) | [spec](structured-review-output/specification.md) | [design](structured-review-output/design.md) | Implementation Complete | Enhancement |
| 2026-02-07 | [review-output-fixes](review-output-fixes/) | [spec](review-output-fixes/specification.md) | [design](review-output-fixes/design.md) | Implementation Complete | Bugfix |
| 2026-02-07 | [s3-document-storage](s3-document-storage/) | [spec](s3-document-storage/specification.md) | [design](s3-document-storage/design.md) | Design Complete | Enhancement |
| 2026-02-07 | [review-scope-control](review-scope-control/) | [spec](review-scope-control/specification.md) | [design](review-scope-control/design.md) | Implementation Complete | Enhancement |
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
