# API Routes
"""
API route modules.

Implements:
- [foundation-api:FR-001] through [FR-006] - Review endpoints
- [foundation-api:FR-013] - Health endpoint
- [policy-knowledge-base:FR-001] - Policy endpoints
- [api-hardening:FR-005] through [FR-007] - Download endpoints
- [cycle-route-assessment:FR-005] - Destination endpoints
"""

from src.api.routes import destinations, downloads, health, policies, reviews

__all__ = ["destinations", "downloads", "health", "policies", "reviews"]
