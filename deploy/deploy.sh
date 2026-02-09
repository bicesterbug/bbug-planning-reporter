#!/usr/bin/env bash
# Deploy bbug-planning-reporter from pre-built GHCR images.
#
# Usage:
#   ./deploy.sh              # Pull latest images and start services
#   ./deploy.sh --seed       # Also download policy PDFs and seed ChromaDB
#   ./deploy.sh --tag v1.2.0 # Deploy a specific version
#   ./deploy.sh --down       # Stop and remove services
#   ./deploy.sh --status     # Show service status
#   ./deploy.sh --logs       # Follow logs

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

REGISTRY="ghcr.io"
COMPOSE_FILE="docker-compose.yml"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
ACTION="deploy"
SEED=false
TAG=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --seed)   SEED=true; shift ;;
        --tag)    TAG="$2"; shift 2 ;;
        --down)   ACTION="down"; shift ;;
        --status) ACTION="status"; shift ;;
        --logs)   ACTION="logs"; shift ;;
        --help|-h)
            sed -n '2,9p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Run '$0 --help' for usage." >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
info()  { echo "==> $*"; }
warn()  { echo "WARNING: $*" >&2; }
error() { echo "ERROR: $*" >&2; exit 1; }

compose() {
    docker compose -f "$COMPOSE_FILE" "$@"
}

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------
preflight() {
    # Check docker compose is available
    if ! docker compose version &>/dev/null; then
        error "docker compose is not available. Install Docker Compose v2+."
    fi

    # Check .env exists
    if [[ ! -f .env ]]; then
        error ".env file not found. Copy .env.example to .env and fill in required values."
    fi

    # Check seed_config.json exists (needed for --seed)
    if [[ "$SEED" == true && ! -f seed_config.json ]]; then
        error "seed_config.json not found in deploy directory."
    fi
}

# ---------------------------------------------------------------------------
# GHCR authentication
# ---------------------------------------------------------------------------
ghcr_auth() {
    # Already logged in?
    if docker pull "$REGISTRY/bicesterbug/bbug-planning-reporter/api:latest" &>/dev/null 2>&1; then
        return 0
    fi

    info "Authenticating with $REGISTRY..."

    # Try GHCR_TOKEN env var first
    if [[ -n "${GHCR_TOKEN:-}" ]]; then
        echo "$GHCR_TOKEN" | docker login "$REGISTRY" -u token --password-stdin && return 0
        warn "GHCR_TOKEN login failed, trying other methods..."
    fi

    # Try gh CLI
    if command -v gh &>/dev/null; then
        local token
        token=$(gh auth token 2>/dev/null || true)
        if [[ -n "$token" ]]; then
            echo "$token" | docker login "$REGISTRY" -u gh-cli --password-stdin && return 0
            warn "gh auth token login failed."
        fi
    fi

    warn "Could not authenticate with GHCR. If images are private, set GHCR_TOKEN or run 'gh auth login'."
    warn "Continuing anyway — pull will fail if images require authentication."
}

# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
do_deploy() {
    preflight

    # Export IMAGE_TAG if --tag was provided
    if [[ -n "$TAG" ]]; then
        export IMAGE_TAG="$TAG"
        info "Deploying version: $TAG"
    else
        info "Deploying latest images"
    fi

    ghcr_auth

    info "Pulling images..."
    compose pull

    info "Starting services..."
    compose up -d

    if [[ "$SEED" == true ]]; then
        info "Downloading seed policy PDFs..."
        compose --profile seed run --rm fetch-policies

        info "Seeding policy knowledge base..."
        compose --profile seed run --rm policy-init
    fi

    echo ""
    info "Services running:"
    compose ps

    # Show API URL
    local api_port
    api_port=$(grep -oP 'API_PORT=\K[0-9]+' .env 2>/dev/null || echo "8080")
    echo ""
    info "API available at: http://localhost:${api_port}"
    info "Health check:     curl http://localhost:${api_port}/health"
}

do_down() {
    info "Stopping services..."
    compose down
    info "Services stopped."
}

do_status() {
    compose ps
}

do_logs() {
    compose logs -f
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
case "$ACTION" in
    deploy) do_deploy ;;
    down)   do_down ;;
    status) do_status ;;
    logs)   do_logs ;;
esac
