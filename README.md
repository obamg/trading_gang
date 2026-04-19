# TradeCore

Pro-grade crypto intelligence platform. Ten specialised modules (RadarX, WhaleRadar,
LiquidMap, SentimentPulse, MacroPulse, GemRadar, RiskCalc, TradeLog, PerformanceCore,
Oracle) feeding a meta-scorer that ranks every setup from −100 to +100.

---

## One-command dev boot

Everything runs in Docker. No local Python or Node install required.

```bash
docker compose up --build
```

On first boot this will:

1. Start **Postgres** (port `5432`) and **Redis** (port `6379`).
2. Run the one-shot **`seed`** service, which:
   - Applies all Alembic migrations (`alembic upgrade head`) — creates the schema and seeds the three plans (free/pro/elite).
   - Creates two ready-to-use test accounts.
3. Start the **api** (port `8000`), **scheduler** (background jobs), and **frontend** (port `8081` by default).

Once the stack is up, browse to **http://localhost:8081** and log in with:

| Email                   | Password   | Notes                           |
| ----------------------- | ---------- | ------------------------------- |
| `test@example.com`      | `test1234` | Watchlist: BTC / ETH / SOL      |
| `demo@example.com`      | `demo1234` | Empty watchlist                 |

Subsequent runs are instant — the seed service is idempotent and exits immediately
if the accounts already exist.

If `8081` is already in use too, override the host port for the frontend:

```bash
FRONTEND_PORT=8091 docker compose up --build
```

### Useful commands

```bash
# Stream logs for a single service
docker compose logs -f api

# Run the test account seeder again (e.g. after wiping the DB)
docker compose run --rm seed

# Reset everything (wipes DB + Redis volumes)
docker compose down -v

# Shell into the API container
docker compose exec api bash

# Manually re-run migrations
docker compose exec api alembic upgrade head
```

### Turning on live data

The dev compose runs with `BINANCE_STREAMS_ENABLED=false` so Binance websockets stay
off by default. To flip them on:

```bash
BINANCE_STREAMS_ENABLED=true docker compose up -d api scheduler
```

---

## Production

Full deployment guide: [docs/DEPLOY.md](docs/DEPLOY.md) — Hostinger VPS + GitHub
Actions selective deploy (only the changed services restart).

Use `docker-compose.prod.yml` with a populated `.env.production` (see
`.env.production.example` for the full list of required secrets — `JWT_SECRET`,
`APP_SECRET_KEY`, `ENCRYPTION_KEY`, Stripe keys, SendGrid, etc.).

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
```

The prod stack differs from dev in four ways:

- **No seed service.** Accounts are created through the real `/auth/register` flow.
- **Only the api container runs migrations** (scheduler has `RUN_MIGRATIONS=0`).
- **No exposed DB/Redis ports** — only the frontend (`80`) is published.
- **Real secrets required.** Startup aborts if `JWT_SECRET`, `APP_SECRET_KEY`, or
  `ENCRYPTION_KEY` still hold development defaults.

SSL termination is expected to happen at a layer in front of the stack (ALB, Cloudflare, or a second nginx with certbot).

---

## Repo layout

```
tradecore/            FastAPI backend (SQLAlchemy async, APScheduler, Redis pub/sub)
  app/                FastAPI app + 10 module packages
  alembic/            Migrations (001_initial + 002_seed_plans)
  tests/              pytest + pytest-asyncio
  Dockerfile          gunicorn+uvicorn image, migrations-on-boot via entrypoint
frontend/             React 18 + TS + Vite + Tailwind + Zustand + TanStack Query v5
  src/pages/          Auth, landing, and 10 dashboard modules
  src/components/     Shared UI primitives + module layouts
  Dockerfile          Multi-stage node → nginx serve
  nginx.conf          SPA fallback + /api + /ws proxy with auth rate limits
docker-compose.yml        Dev stack (this file)
docker-compose.prod.yml   Production stack
.env.production.example   Reference env for production deploys
```

---

## Running tests

```bash
# Backend (inside the api container or locally with a venv)
docker compose exec api pytest

# Frontend
cd frontend && npm test
```

CI runs both suites + type-check + build on every push (see `.github/workflows/deploy.yml`).
