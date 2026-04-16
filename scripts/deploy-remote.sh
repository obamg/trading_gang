#!/usr/bin/env bash
# TradeCore selective deploy — run on the Hostinger VPS via SSH from CI.
#
# Called as:
#   deploy-remote.sh <api_tag> <frontend_tag> <services...>
#
#   <api_tag>        — image tag for ghcr.io/<repo>/api (usually a short sha)
#   <frontend_tag>   — image tag for ghcr.io/<repo>/frontend
#   <services...>    — compose service names to pull + restart
#                      (e.g. "api scheduler" if only the backend changed,
#                       "frontend" if only the SPA changed,
#                       or the full set for an infra change)
#
# Side effects:
#   • Pulls ONLY the listed services from GHCR (postgres/redis are never pulled).
#   • Restarts ONLY the listed services with `docker compose up -d --no-deps`.
#   • Cleans up dangling images older than 24h.
#
# Requires on the VPS:
#   • docker + docker compose v2
#   • /opt/tradecore/docker-compose.prod.yml (committed from this repo)
#   • /opt/tradecore/.env.production (populated out of band — never in git)
#   • GHCR login already done (`docker login ghcr.io`) — see README.

set -euo pipefail

API_TAG="${1:?missing api tag}"
FRONTEND_TAG="${2:?missing frontend tag}"
shift 2
SERVICES=("$@")

if [[ ${#SERVICES[@]} -eq 0 ]]; then
  echo "[deploy] no services to restart — nothing to do"
  exit 0
fi

DEPLOY_DIR="${DEPLOY_DIR:-/opt/tradecore}"
cd "$DEPLOY_DIR"

export API_IMAGE_TAG="$API_TAG"
export FRONTEND_IMAGE_TAG="$FRONTEND_TAG"

echo "[deploy] pinning API_IMAGE_TAG=$API_TAG FRONTEND_IMAGE_TAG=$FRONTEND_TAG"
echo "[deploy] services in scope: ${SERVICES[*]}"

# Pull only the services we are about to restart.
# Postgres/Redis are upstream images — never deploy-managed.
docker compose -f docker-compose.prod.yml --env-file .env.production \
  pull "${SERVICES[@]}"

# Recreate only the listed services; --no-deps leaves postgres/redis alone.
# --remove-orphans cleans up any stray containers left from old configs.
docker compose -f docker-compose.prod.yml --env-file .env.production \
  up -d --no-deps --remove-orphans "${SERVICES[@]}"

# Wait briefly and report status.
sleep 5
docker compose -f docker-compose.prod.yml --env-file .env.production ps "${SERVICES[@]}"

# Best-effort: reap dangling images older than 24h so the disk doesn't fill.
docker image prune -f --filter "until=24h" >/dev/null 2>&1 || true

echo "[deploy] ok"
