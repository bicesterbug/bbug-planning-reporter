# Production Deployment Reference

Server: `192.168.1.18` (pete@)
Deploy dir: `~/bbug-planning-reporter/deploy/`
Compose project: `deploy`

## Container Configuration

### Traefik Labels (api service)

The API container has these Traefik labels for HTTPS reverse proxy:

```
traefik.enable=true
traefik.http.routers.bbug-api.rule=Host(`bbug-planning.mmsio.com`)
traefik.http.routers.bbug-api.entrypoints=websecure
traefik.http.routers.bbug-api.tls.certresolver=myresolver
traefik.http.services.bbug-api.loadbalancer.server.port=8080
```

These are defined in `docker-compose.yml` and the `traefik_default` external network
connects the API to the server's Traefik instance.

### Networks

- `agent-net` (bridge) - internal communication between services
- `traefik_default` (external) - connects API to Traefik reverse proxy

### Environment Variables

Production `.env` on server (secrets redacted):

```shell
# Required
ANTHROPIC_API_KEY=sk-ant-...
API_KEYS=sk-bbug-...

# Deployment
IMAGE_TAG=v0.1.6
API_PORT=8180

# Webhooks
WEBHOOK_URL=https://a.mms-app.com/webhook/9191ad3e-8406-40ac-9995-374343e41e06

# S3 storage (DigitalOcean Spaces, London region)
S3_ENDPOINT_URL=https://lon1.digitaloceanspaces.com
S3_BUCKET=bbug-files
S3_ACCESS_KEY_ID=DO801...
S3_SECRET_ACCESS_KEY=...
S3_KEY_PREFIX=planning-prod
S3_REGION=lon1

# Data directory (external disk on server)
DATA_DIR=/mnt/sda1/bbug-data

# Logging
LOG_LEVEL=INFO
```

### Data Volumes

All persistent data lives on `/mnt/sda1/bbug-data/` on the server, mapped via
`DATA_DIR` in `.env`. Subdirectories:

| Path | Used by | Purpose |
|------|---------|---------|
| `chroma/` | worker, document-store-mcp, policy-kb-mcp | ChromaDB vector store |
| `raw/` | worker, document-store-mcp, cherwell-scraper-mcp | Downloaded planning documents |
| `output/` | worker | Generated review output |
| `policy/` | worker, policy-kb-mcp | Policy PDFs and seed config |
| `redis/` | redis | Persistent job queue data |

### Resource Limits

| Service | Memory Limit |
|---------|-------------|
| api | 512M |
| worker | 4G |
| redis | 256M |
| cherwell-scraper-mcp | 2G |
| document-store-mcp | 2G |
| policy-kb-mcp | 2G |

## Deployment Process

```bash
# From local machine:
# 1. Merge to main, tag, push
git tag v0.x.y && git push origin main v0.x.y

# 2. Wait for GitHub Actions release-build to complete (~5 min)
gh run watch

# 3. SSH to server and deploy
ssh pete@192.168.1.18
cd ~/bbug-planning-reporter/deploy
# Update IMAGE_TAG in .env
sed -i 's/IMAGE_TAG=.*/IMAGE_TAG=v0.x.y/' .env
./deploy.sh --tag v0.x.y
```

## Version History

| Date | Tag | Notes |
|------|-----|-------|
| 2026-02-14 | v0.1.10 | Fix category filtering: parse <th> section headers, match real portal categories |
| 2026-02-14 | v0.1.9 | Detect image-based PDFs (plans/drawings), skip vector ingestion |
| 2026-02-14 | v0.1.8 | Fix document download filename extraction from Cherwell portal URLs |
| 2026-02-14 | v0.1.7 | Fix scraper health check: add /health endpoint replacing SSE probe |
| 2026-02-14 | v0.1.6 | Review workflow redesign: 7-phase pipeline, LLM filtering, verification |
| 2026-02-13 | v0.1.5 | Global webhooks, static secret auth |
