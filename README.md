# Cherwell Planning Cycle Advocacy Agent

An AI agent system that reviews Cherwell District Council planning applications from a cycling advocacy perspective, benchmarking proposals against LTN 1/20, NPPF, and local planning policy.

## Overview

This system:
- Accepts planning application references via REST API
- Automatically fetches application documents from the Cherwell planning portal
- Processes documents (PDF extraction, OCR, embeddings)
- Compares proposals against cycling and transport policy
- Generates structured reviews suitable for consultation responses

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Python 3.12+ (for local development)

### Running with Docker

```bash
# Copy environment file and configure
cp .env.example .env
# Edit .env with your Anthropic API key

# Start all services
docker compose up -d

# Check health
curl http://localhost:8080/api/v1/health
```

### Local Development

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run linting
ruff check .
black --check .

# Start Redis (required)
docker compose up redis -d

# Run API (in one terminal)
uvicorn src.api.main:app --reload --port 8080

# Run worker (in another terminal)
python -m src.worker.main
```

## API Usage

### Submit a Review

```bash
curl -X POST http://localhost:8080/api/v1/reviews \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-cycle-dev-key-1" \
  -d '{
    "application_ref": "25/01178/REM",
    "webhook": {
      "url": "https://your-app.example.com/hooks",
      "secret": "your-webhook-secret"
    }
  }'
```

### Check Status

```bash
curl http://localhost:8080/api/v1/reviews/{review_id}/status \
  -H "Authorization: Bearer sk-cycle-dev-key-1"
```

### Get Result

```bash
curl http://localhost:8080/api/v1/reviews/{review_id} \
  -H "Authorization: Bearer sk-cycle-dev-key-1"
```

## Project Structure

```
├── src/
│   ├── api/              # FastAPI REST API
│   ├── worker/           # arq job worker
│   ├── agent/            # AI agent orchestration
│   ├── mcp_servers/      # MCP tool servers
│   │   ├── cherwell_scraper/
│   │   ├── document_store/
│   │   └── policy_kb/
│   └── shared/           # Shared utilities
├── tests/                # Test suite
├── docker/               # Dockerfiles
├── data/                 # Runtime data (gitignored)
└── docs/                 # Documentation
```

## Architecture

See [docs/DESIGN.md](docs/DESIGN.md) for full architecture documentation.

## Development

See [.sdd/](/.sdd/) for Spec Driven Development artifacts including specifications and designs for each feature.

## License

MIT
