#!/usr/bin/env bash
# TradeCore container entrypoint.
#
# Behaviour (controlled by env):
#   RUN_MIGRATIONS=1   → run `alembic upgrade head` before exec'ing CMD (default: 1)
#   RUN_SEED=1         → run `python -m app.scripts.seed_dev` after migrations (default: 0)
#   WAIT_FOR_DB=1      → poll Postgres until reachable before migrating (default: 1)
#
# Any CMD passed by docker (gunicorn, scheduler_standalone, seed_dev, etc.) is exec'd
# last so signals propagate correctly.

set -euo pipefail

RUN_MIGRATIONS="${RUN_MIGRATIONS:-1}"
RUN_SEED="${RUN_SEED:-0}"
WAIT_FOR_DB="${WAIT_FOR_DB:-1}"

log() { printf '[entrypoint] %s\n' "$*" >&2; }

if [[ "${WAIT_FOR_DB}" == "1" ]]; then
  log "waiting for database..."
  # Python is guaranteed available; use it to probe instead of requiring pg_isready.
  python - <<'PY'
import os, socket, sys, time
from urllib.parse import urlparse

url = os.environ.get("DATABASE_URL_SYNC") or os.environ.get("DATABASE_URL") or ""
if not url:
    print("[entrypoint] no DATABASE_URL set; skipping wait", flush=True)
    sys.exit(0)

# strip sqlalchemy dialect prefix (e.g. postgresql+asyncpg://)
if "://" in url:
    scheme, rest = url.split("://", 1)
    url = "tcp://" + rest
p = urlparse(url)
host = p.hostname or "postgres"
port = p.port or 5432

deadline = time.time() + 60
while time.time() < deadline:
    try:
        with socket.create_connection((host, port), timeout=2):
            print(f"[entrypoint] database reachable at {host}:{port}", flush=True)
            sys.exit(0)
    except OSError:
        time.sleep(1)
print(f"[entrypoint] database at {host}:{port} never became reachable", flush=True)
sys.exit(1)
PY
fi

if [[ "${RUN_MIGRATIONS}" == "1" ]]; then
  log "running alembic upgrade head"
  alembic upgrade head
fi

if [[ "${RUN_SEED}" == "1" ]]; then
  log "running dev seed (RUN_SEED=1)"
  python -m app.scripts.seed_dev
fi

log "exec: $*"
exec "$@"
