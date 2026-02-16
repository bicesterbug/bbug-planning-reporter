# Local Development Setup

This guide covers running the full stack or individual services locally, seeding policy data, and connecting MCP servers to Claude Code or Claude Desktop.

For API usage and endpoint documentation, see [API.md](API.md).

---

## Prerequisites

- **Docker** and **Docker Compose** v2.21+
- **Python 3.12+** (for running services outside Docker)
- **Git**
- ~2 GB free RAM (the embedding model `all-MiniLM-L6-v2` loads into memory)

---

## Quick Reference

| Service | Port | Python Module | Dependencies | Key Env Vars |
|---------|------|---------------|-------------|--------------|
| API | 8080 | `src.api.main` (uvicorn) | Redis | `API_KEYS`, `REDIS_URL` |
| Worker | — | `src.worker.main` (arq) | Redis, all MCP servers, Anthropic API | `ANTHROPIC_API_KEY`, `REDIS_URL`, `*_URL` |
| Redis | 6379 | — (Docker image) | None | — |
| cherwell-scraper-mcp | 3001 | `src.mcp_servers.cherwell_scraper.server` | None | `CHERWELL_SCRAPER_PORT` |
| document-store-mcp | 3002 | `src.mcp_servers.document_store.server` | ChromaDB dir | `CHROMA_PERSIST_DIR`, `DOCUMENT_STORE_PORT` |
| policy-kb-mcp | 3003 | `src.mcp_servers.policy_kb.server` | Redis, ChromaDB dir, seeded data | `REDIS_URL`, `CHROMA_PERSIST_DIR`, `POLICY_KB_PORT` |
| cycle-route-mcp | 3004 | `src.mcp_servers.cycle_route.server` | None (external APIs: OSRM, Overpass, ArcGIS) | `CYCLE_ROUTE_PORT` |
| policy-init | — | `src.scripts.seed_policies` | Redis, ChromaDB dir, seed PDFs | `REDIS_URL`, `CHROMA_PERSIST_DIR`, `SEED_CONFIG_PATH`, `SEED_DIR` |

**Transport endpoints** (all MCP servers):
- `/health` — health check (always unauthenticated)
- `/mcp` — streamable HTTP (for Claude Code / Claude Desktop)
- `/sse` — SSE transport (legacy, used by the internal worker)

---

## Docker Compose Setup

### 1. Build the base image

All service Dockerfiles depend on `cherwell-base:latest`. Build it first:

```bash
docker build -t cherwell-base:latest -f docker/Dockerfile.base .
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:
- `ANTHROPIC_API_KEY` — required for the worker to call Claude
- `API_KEYS` — comma-separated API keys for the REST API (default: `sk-cycle-dev-key-1`)

### 3. Override volume paths

The `docker-compose.yml` has volume paths pointing to `/media/pete/Files/bbug-reports/` which won't exist on your machine. Create local directories and use a compose override:

```bash
# Create local data directories
mkdir -p data/chroma data/raw data/output data/redis
```

Create `docker-compose.override.yml` in the project root:

```yaml
services:
  worker:
    volumes:
      - ./data/chroma:/data/chroma
      - ./data/raw:/data/raw
      - ./data/output:/data/output
      - ./data/policy:/data/policy

  redis:
    volumes:
      - ./data/redis:/data

  document-store-mcp:
    volumes:
      - ./data/chroma:/data/chroma
      - ./data/raw:/data/raw

  cherwell-scraper-mcp:
    volumes:
      - ./data/raw:/data/raw

  policy-kb-mcp:
    volumes:
      - ./data/chroma:/data/chroma
      - ./data/policy:/data/policy

  policy-init:
    volumes:
      - ./data/chroma:/data/chroma
      - ./data/policy:/data/policy
```

Docker Compose automatically merges `docker-compose.override.yml` on top of `docker-compose.yml`.

### 4. Build and start

```bash
docker compose build
docker compose up -d
```

### 5. Seed policy data (first time only)

```bash
docker compose run --rm policy-init
```

This ingests the 6 seed policy PDFs from `data/policy/seed/` into ChromaDB and registers them in Redis. It is idempotent — running it again skips already-seeded policies.

### 6. Verify

```bash
# API
curl http://localhost:8080/api/v1/health
# Expected: {"status":"healthy","services":{"redis":"connected"},"version":"0.1.0"}

# MCP servers
curl http://localhost:3001/health   # cherwell-scraper
curl http://localhost:3002/health   # document-store
curl http://localhost:3003/health   # policy-kb
curl http://localhost:3004/health   # cycle-route
# Expected: {"status":"ok"}
```

---

## Running MCP Servers Locally (Python)

Running a server outside Docker is useful for debugging with breakpoints or rapid iteration. You still need Redis running for policy-kb (and the worker).

### Common setup

```bash
# Activate virtualenv
source .venv/bin/activate
# (or create one: python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]")

# Start Redis (if not already running)
docker compose up redis -d
```

### cherwell-scraper-mcp (port 3001)

No external dependencies. Stateless scraper.

```bash
CHERWELL_SCRAPER_PORT=3001 \
  python -m src.mcp_servers.cherwell_scraper.server
```

### document-store-mcp (port 3002)

Requires a ChromaDB directory.

```bash
mkdir -p /tmp/chroma

CHROMA_PERSIST_DIR=/tmp/chroma \
DOCUMENT_STORE_PORT=3002 \
  python -m src.mcp_servers.document_store.server
```

### policy-kb-mcp (port 3003)

Requires **both** Redis and a ChromaDB directory with seeded data. This is the most common source of issues — if Redis isn't running, the server starts but all tool calls that need metadata will fail.

```bash
mkdir -p /tmp/chroma

# Seed policies first (one-time, see next section)

REDIS_URL=redis://localhost:6379/0 \
CHROMA_PERSIST_DIR=/tmp/chroma \
POLICY_KB_PORT=3003 \
  python -m src.mcp_servers.policy_kb.server
```

### cycle-route-mcp (port 3004)

No local dependencies. Calls external APIs (OSRM, Overpass, ArcGIS) at runtime.

```bash
CYCLE_ROUTE_PORT=3004 \
  python -m src.mcp_servers.cycle_route.server
```

---

## Policy Data Seeding

The policy-kb-mcp server requires seeded policy data to return search results. Seeding loads 6 policy PDFs into ChromaDB (vector store) and registers metadata in Redis.

### Via Docker

```bash
docker compose run --rm policy-init
```

### Via local Python

Make sure Redis is running and your ChromaDB directory exists:

```bash
REDIS_URL=redis://localhost:6379/0 \
CHROMA_PERSIST_DIR=/tmp/chroma \
SEED_CONFIG_PATH=data/policy/seed_config.json \
SEED_DIR=data/policy/seed \
  python -m src.scripts.seed_policies
```

**Important:** The `CHROMA_PERSIST_DIR` used for seeding must be the same directory used when starting the policy-kb server. If you seed into `/tmp/chroma` but start the server pointing at `./data/chroma`, it won't find any data.

### Verify seeding

```bash
# If policy-kb is running on port 3003:
curl -X POST http://localhost:3003/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "list_policy_documents",
      "arguments": {}
    }
  }'
```

You should see 6 policies listed (LTN 1/20, NPPF, Manual for Streets, Cherwell Local Plan, OCC LTCP, Bicester LCWIP).

---

## Connecting to Claude Code

Claude Code connects to MCP servers via streamable HTTP at the `/mcp` endpoint.

### Project-level config

Create `.claude/mcp.json` in the project root:

```json
{
  "mcpServers": {
    "cherwell-scraper": {
      "type": "url",
      "url": "http://localhost:3001/mcp"
    },
    "document-store": {
      "type": "url",
      "url": "http://localhost:3002/mcp"
    },
    "policy-kb": {
      "type": "url",
      "url": "http://localhost:3003/mcp"
    },
    "cycle-route": {
      "type": "url",
      "url": "http://localhost:3004/mcp"
    }
  }
}
```

### With authentication

If `MCP_API_KEY` is set on the servers, add headers:

```json
{
  "mcpServers": {
    "policy-kb": {
      "type": "url",
      "url": "http://localhost:3003/mcp",
      "headers": {
        "Authorization": "Bearer your-mcp-api-key"
      }
    }
  }
}
```

### Verify

Start Claude Code in the project directory and run `/mcp` to list connected servers. You should see tools from all configured servers.

---

## Connecting to Claude Desktop

Claude Desktop uses the same streamable HTTP transport. Edit the config file:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Linux:** `~/.config/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

Add the servers:

```json
{
  "mcpServers": {
    "cherwell-scraper": {
      "type": "url",
      "url": "http://localhost:3001/mcp"
    },
    "document-store": {
      "type": "url",
      "url": "http://localhost:3002/mcp"
    },
    "policy-kb": {
      "type": "url",
      "url": "http://localhost:3003/mcp"
    },
    "cycle-route": {
      "type": "url",
      "url": "http://localhost:3004/mcp"
    }
  }
}
```

Restart Claude Desktop after editing the config. The servers must be running before Claude Desktop can connect.

To add authentication headers, use the same `"headers"` field shown in the Claude Code section above.

---

## Environment Variable Reference

Variables needed for local development. See `.env.example` for the full list with comments.

| Variable | Docker Default | Local Python Default | Description | Used By |
|----------|---------------|---------------------|-------------|---------|
| `ANTHROPIC_API_KEY` | — (required) | — (required) | Anthropic API key for Claude calls | worker |
| `CLAUDE_MODEL` | `claude-sonnet-4-5-20250929` | same | Model for review generation | worker |
| `DOCUMENT_FILTER_MODEL` | `claude-haiku-4-5-20251001` | same | Model for document classification | worker |
| `API_KEYS` | `sk-cycle-dev-key-1` | same | Comma-separated API keys | api |
| `REDIS_URL` | `redis://redis:6379/0` | `redis://localhost:6379/0` | Redis connection URL | api, worker, policy-kb, policy-init |
| `CHROMA_PERSIST_DIR` | `/data/chroma` | `/tmp/chroma` (or any local dir) | ChromaDB storage directory | worker, document-store, policy-kb, policy-init |
| `RAW_DOCS_DIR` | `/data/raw` | `/tmp/raw` | Downloaded document storage | worker |
| `OUTPUT_DIR` | `/data/output` | `/tmp/output` | Review output files | worker |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | same | Sentence transformer model | worker, document-store, policy-kb |
| `CHERWELL_SCRAPER_PORT` | `3001` | `3001` | Scraper server port | cherwell-scraper |
| `DOCUMENT_STORE_PORT` | `3002` | `3002` | Document store server port | document-store |
| `POLICY_KB_PORT` | `3003` | `3003` | Policy KB server port | policy-kb |
| `CYCLE_ROUTE_PORT` | `3004` | `3004` | Cycle route server port | cycle-route |
| `MCP_API_KEY` | — (empty = disabled) | — (empty = disabled) | Bearer token for MCP auth | all MCP servers |
| `SEED_CONFIG_PATH` | `/data/policy/seed_config.json` | `data/policy/seed_config.json` | Policy seed configuration | policy-init |
| `SEED_DIR` | `/data/policy/seed` | `data/policy/seed` | Directory containing seed PDFs | policy-init |
| `SCRAPER_RATE_LIMIT` | `1.0` | same | Seconds between scraper requests | cherwell-scraper, worker |
| `LOG_LEVEL` | `INFO` | same | Logging level | all services |

**Key difference:** In Docker, `REDIS_URL` uses `redis://redis:6379/0` (the Docker service name). When running locally, use `redis://localhost:6379/0`.

---

## Troubleshooting

### policy-kb tool calls fail but `/health` returns OK

**Symptom:** `search_policy` or `list_policy_documents` return errors, but `curl http://localhost:3003/health` works fine.

**Cause:** Redis is not running. The health endpoint only checks the HTTP server, not Redis. The `PolicyRegistry` needs Redis for all metadata operations.

**Fix:** Start Redis: `docker compose up redis -d` (or `redis-server &` if installed locally). Verify with `redis-cli ping`.

### Search returns empty results

**Symptom:** `search_policy` returns an empty list even though seeding appeared to succeed.

**Cause:** The server is reading from a different ChromaDB directory than the one that was seeded.

**Fix:** Ensure `CHROMA_PERSIST_DIR` is the same for both the seeder and the server. For example, if you seeded with `CHROMA_PERSIST_DIR=/tmp/chroma`, the server must also use `/tmp/chroma`.

### 401 Unauthorized from MCP server

**Symptom:** Claude Code or Claude Desktop fails to connect with a 401 error.

**Cause:** `MCP_API_KEY` is set on the server but the client config doesn't include the matching `Authorization` header.

**Fix:** Either:
- Add `"headers": {"Authorization": "Bearer <key>"}` to your MCP client config
- Or unset `MCP_API_KEY` on the server (leave it empty for local development)

### Port already in use

**Symptom:** Server fails to start with `Address already in use`.

**Cause:** Another process (or Docker container) is using the port.

**Fix:** Override the port via environment variable:
```bash
POLICY_KB_PORT=3013 python -m src.mcp_servers.policy_kb.server
```
Update your MCP client config to use the new port.

### Docker build fails: cherwell-base not found

**Symptom:** `docker compose build` fails with `FROM cherwell-base:latest` not found.

**Cause:** The base image hasn't been built yet.

**Fix:**
```bash
docker build -t cherwell-base:latest -f docker/Dockerfile.base .
```

### Docker volume mount errors

**Symptom:** Container fails to start with permission or path errors on volume mounts.

**Cause:** The `docker-compose.yml` has hardcoded paths (`/media/pete/Files/bbug-reports/...`) that don't exist on your machine.

**Fix:** Create a `docker-compose.override.yml` as described in the [Docker Compose Setup](#3-override-volume-paths) section, pointing volumes to local directories.

### Connection refused when Claude Code calls MCP tool

**Symptom:** Claude Code shows "connection refused" when trying to use an MCP tool.

**Cause:** The MCP server isn't running.

**Fix:** Start the server (Docker or local Python) before using Claude Code. Verify with `curl http://localhost:<port>/health`.
