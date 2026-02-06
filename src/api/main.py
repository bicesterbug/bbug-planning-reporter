"""
FastAPI application entry point.

Implements [foundation-api:FR-013] - Health check endpoint
Implements [policy-knowledge-base:FR-001] - Policy Knowledge Base API
"""

from fastapi import FastAPI

from src.api.routes import health, policies, reviews

app = FastAPI(
    title="Cherwell Cycle Advocacy Agent",
    description="AI-powered planning application review from a cycling advocacy perspective",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Include routers
app.include_router(health.router, prefix="/api/v1", tags=["health"])
app.include_router(reviews.router, prefix="/api/v1", tags=["reviews"])
app.include_router(policies.router, prefix="/api/v1", tags=["policies"])
