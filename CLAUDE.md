# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Stack

- **Backend** (`tradecore/`): FastAPI 0.115, SQLAlchemy 2 async, asyncpg, Alembic, APScheduler, Redis 7 (pubsub + streams), structlog, slowapi rate limiting. Python 3.11.
- **Frontend** (`frontend/`): React 18 + TypeScript, Vite, Tailwind, Zustand, TanStack Query v5, react-router v6. Path alias `@/` → `frontend/src`.
- **Infra**: Postgres 15, Docker Compose for both dev and prod. Production deploys via GitHub Actions to a Hostinger VPS, with selective rebuild based on `dorny/paths-filter` (`tradecore/**` → api+scheduler; `frontend/**` → frontend; compose/deploy script → full).

## Common commands

```bash
# Boot the whole stack (Postgres, Redis, seed, api, scheduler, frontend on :8081)
docker compose up --build

# Backend tests (inside the running api container)
docker compose exec api pytest
docker compose exec api pytest tradecore/tests/test_oracle.py::test_specific  # single test

# Run a fresh migration
docker compose exec api alembic revision -m "msg" --autogenerate
docker compose exec api alembic upgrade head

# Reset DB + Redis
docker compose down -v

# Frontend dev/build/test (run inside frontend/)
npm run dev          # vite on :5173
npm run typecheck    # tsc --noEmit
npm run build        # tsc -b && vite build
npm test             # vitest
```

Test login: `test@example.com` / `test1234`.

## Architecture

### Modules

The backend is organized as **module packages** under `tradecore/app/modules/`. Each module is a self-contained vertical (detector + router + sometimes a model file in `app/models/`):

`radarx`, `whaleradar`, `liquidmap`, `sentimentpulse`, `macropulse`, `gemradar`, `riskcalc`, `performance`, `oracle`, `flowpulse`, `newspulse`, `positionmonitor`, `exchanges`, `listingwatch`, `awakening`, `wavewatch`, `walletwatch` (+ `walletwatch/discovery` sub-module for PnL-based wallet auto-discovery), `chainpulse`, `majorsbot`.

Each module typically exposes:
- `router.py` — FastAPI router, mounted from `app/main.py`
- `detector.py` / `collector.py` / `tracker.py` — periodic scanning logic, called from the scheduler
- internal helpers (`engine.py`, `aggregator.py`, etc.)

`oracle` is the meta-scorer that consumes signals from the other modules and ranks setups −100…+100.

One-liners for the modules a new contributor is most likely to touch:
- `awakening` — turnover spikes off a 7-day sleepy baseline; force-subscribes the spiking symbol into the stream.
- `wavewatch` — Innovation Zone surveillance with two signals: `wave_incoming` (pre-wave coiling, dwell-gated) and `wave_active` (single-bar cascade/squeeze with funding alignment). Universe spans Bybit Innovation + Binance Innovation/Seed.
- `listingwatch` — diffs current exchange instrument lists to detect new listings; force-subscribes the bybit stream.
- `walletwatch` — labeled-address DEX swap monitor across ETH/BSC/Arbitrum/Base/Solana.
- `chainpulse` — daily Santiment on-chain macro snapshots (MVRV, NVT, exchange flows, active addresses) per asset, with a derived regime label. No-ops with a log line when `SANTIMENT_API_KEY` is unset.
- `majorsbot` — the paper-trading bot. Fixed 10-symbol Bybit linear-perp universe; self-computes 1h signals (it does not consume other modules' alerts). See below.

### MajorsBot — the trading bot

`majorsbot` is the only trading bot (a prior `bot`/WaveBot module was retired in 2026-08; don't look for `app/modules/bot/`, `BOT_*` env, or `bot_trades`). It runs **paper only** — there is no live order routing, and adding it requires user API keys plus a supervised go-live.

- Strategies live in `strategies.py` as pure functions with **frozen parameters that mirror a 12-month backtest** — changing them invalidates the forward test, so don't tune them casually.
- `volevent` (active): vol-event momentum retrace — a 1h bar with |return| ≥ 3× trailing 30d mean TR% AND volume ≥ 3× 30d median triggers a limit entry at the 50% retrace; stop at the trigger bar's adverse extreme (1% floor); 50% off at +1.5R then a 1R trail.
- `fundingfade` (disabled): funding-percentile fade. **Disabled after a percentile tie/cap bug** — Bybit pins funding at a 0.0001 ceiling that is both the modal AND max value, so a `<=` percentile returned 1.0 on ordinary funding and fired false shorts. **Any percentile logic over funding must use strict `<` and treat the pinned ceiling as ordinary.**
- Sizing is risk-normalized: `qty = equity × risk% / |entry − stop|`, capped at `equity × position_size_pct / entry`. If the notional cap binds, actual risk lands *below* the configured risk% — check which constraint binds before reasoning about exposure.
- Evaluation is **pre-committed**: judge a strategy at its agreed n (volevent: n≥30 closed trades) against its backtest average net R. A strategy failing its gate gets its flag disabled — it does not get retuned. Net R (`realized_r_net`) is the metric, not win rate; R is normalized to risk, so sizing changes don't affect it.
- Prod policy lives in `docker-compose.prod.yml` env (`MAJORSBOT_*`), not in code defaults, which stay conservative/off.

### Process layout — single API worker, separate scheduler container

`gunicorn` runs **one** uvicorn worker (see `tradecore/Dockerfile`). This is intentional: the WebSocket `ConnectionManager` and the Redis-pubsub→WS relay are in-process singletons. Multi-worker would split-brain WS state and duplicate alerts.

Scheduled jobs run in a separate container (`scheduler` service) via `python -m app.services.scheduler_standalone`. The api container has `SCHEDULER_ENABLED=false`.

### Singletons + leader election

Some long-running tasks must run on exactly one process: the Binance websocket stream, the Telegram bot, the LiquidMap liquidation listener, and the Oracle trigger listener. `app/main.py` runs a self-healing leader-election loop (`_run_leader_loop`) using a Redis lock at `tradecore:leader` with `LEADER_TTL_SECONDS=45` and a 15s refresh poll. A worker that loses the lock stops its singletons; another picks them up within ~45s.

When adding a new "must-run-once" service, register start/stop in `_start_singletons` / `_stop_singletons`, not in the lifespan directly.

### Redis as the message bus

Redis is the integration layer between the scheduler, detectors, the API, and WS clients. Key naming conventions in `app/services/redis_service.py` are **load-bearing** — multiple modules read each other's keys:

```
candles:{symbol}            list, last 50 OHLCV JSON (lpush + ltrim)
trades:{symbol}             Redis stream, capped 1000
symbols:active              set
funding:{symbol}            float, TTL 3600
oi:{symbol}                 hash, TTL 300
liq_heatmap:{symbol}        hash {price_bucket: usd_size}
cooldown:{module}:{symbol}  TTL key — exists = on cooldown
session:{token_hash}        user_id
pubsub: alerts:{module}     alert dicts → relayed to WS
pubsub: liquidations        forceOrder events

wavewatch:score:{symbol}              float, TTL 600s — latest composite score 0..1
wavewatch:since:{symbol}              ISO ts, TTL 7200s — first crossing of threshold (dwell tracking)
wavewatch:last_alert:{symbol}         ISO ts, TTL = cooldown — wave_incoming lockout
wavewatch:last_active_alert:{symbol}  ISO ts, TTL = active cooldown — wave_active lockout
wavewatch:hour_count / wavewatch:active_hour_count   int, TTL 3600s — hourly budgets per signal
{module}:force_subscribe:{exchange}   set, TTL set by writer — read by stream managers
                                      ({module} ∈ awakening | wavewatch; also legacy bybit:force_subscribe)
awakening:baseline                    hash {exchange:symbol → median_turnover_usd}
awakening:hist:{exchange}:{symbol}    list of last 7 daily turnover floats
listingwatch:known                    set of {exchange}:{market}:{symbol} — diff target
walletwatch:cursor:{chain}:{addr}     per-chain ingest cursor
```

Don't change a key shape without grepping for consumers across `app/modules/`.

**Force-subscribe pattern**: any detector that wants the WS stream to track a symbol below the volume gate writes to `{module}:force_subscribe:{exchange}` (set, with TTL). `binance_stream` merges `awakening:` and `wavewatch:` keys; `bybit_stream` merges those plus the legacy `bybit:force_subscribe`. When adding a new detector that needs forced symbols, write to a new key and add it to the relevant stream's merge list.

### Exchange sync (per-user trade ingestion)

Trades displayed in the Performance module come from connected exchange API keys, not from the Binance market stream. Architecture lives in `app/services/exchanges/`:

- `base.py` — `ExchangeAdapter` interface
- `binance.py`, `bybit.py` — adapters (implement `fetch_fills` etc.)
- `credentials.py` — Fernet-encrypted API key storage (uses `ENCRYPTION_KEY`)
- `pairing.py` — pairs fills into completed trades
- `sync.py` — `sync_all_active_credentials(db)` called every tick by the scheduler
- `upsert.py` — idempotent trade upsert

Two gotchas to preserve when editing:
1. **Bybit signing**: the signature must be over the query string in **wire-order**. Sort params before building the string-to-sign **and** before issuing the request, or signatures break.
2. **External entries**: trades created as placeholders (no real fill) are flagged `external_entry=True` and must be excluded from analytics aggregations in `performance/aggregator.py` and `performance/behavior.py`.

### Database / migrations

- Async URL: `postgresql+asyncpg://...`. Sync URL (used by Alembic + entrypoint DB-wait probe): `postgresql+psycopg2://...`.
- Migrations in `tradecore/alembic/versions/NNN_*.py`, applied automatically by `docker-entrypoint.sh` when `RUN_MIGRATIONS=1`. In dev, only the one-shot `seed` service runs them; `api` and `scheduler` have `RUN_MIGRATIONS=0`. In prod, only the `api` container runs them.
- Models grouped by domain in `app/models/`. `base.py` exposes the declarative `Base`; every model module must be imported (transitively) before `Base.metadata` is used.

### Auth

- JWT (HS256) access tokens + refresh tokens via `app/services/auth_service.py`.
- Frontend stores tokens in Zustand and proactively refreshes via `scheduleProactiveRefresh` in `frontend/src/api/client.ts`.
- Google OAuth is wired in `app/services/google_oauth.py` (optional; depends on env).
- `slowapi` rate-limits at the middleware layer; storage is a separate Redis DB (`RATE_LIMIT_STORAGE_URL`, db 1).

### Config

`app/config.py` defines a single `Settings` (pydantic-settings). Production startup **aborts** if `JWT_SECRET`, `APP_SECRET_KEY`, or `ENCRYPTION_KEY` still hold dev defaults — see `get_settings()`. Generate `ENCRYPTION_KEY` with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.

- `MARKET_DATA_SOURCE` (`bybit` | `binance`) selects which exchange's WS stream the leader runs. Switch at deploy time, not runtime.
- Frontend version chip reads `VITE_GIT_SHA` and `VITE_BUILD_DATE` (injected by CI at Docker build time, see `.github/workflows/deploy.yml`). Local `npm run dev` falls back to `v1.dev` — don't strip the chip code when refactoring TopBar.

### Tests

- Backend tests live in `tradecore/tests/`. `conftest.py` sets safe defaults (`APP_ENV=test`, dev JWT secret, a deterministic `ENCRYPTION_KEY`) **before** any app modules are imported, and exposes a `fake_redis` fixture (a minimal in-memory async Redis stand-in) — prefer it over real Redis for unit tests of detectors. Full FastAPI integration tests run against a real Postgres in CI.
- Frontend tests use Vitest + Testing Library + jsdom (`frontend/vitest.config.ts`).

### Frontend routing

`App.tsx` mounts `<AuthBootstrap>` (calls `/auth/me` if a token exists, otherwise marks bootstrapped) and `<WebSocketMount>` (opens the WS once per session). Dashboard pages live under `frontend/src/pages/dashboard/<module>/` and are guarded by `<ProtectedRoute>` inside `<AppLayout>`.

## Conventions worth following

- **Dev seed** (`app/scripts/seed_dev`) is idempotent — re-run with `docker compose run --rm seed` after wiping the DB.
- Detector jobs in `app/services/scheduler.py` swallow + log their own exceptions so a single failure can't take down the scheduler. Match that pattern when adding new jobs.
- WaveWatch has two coupled jobs: `wavewatch_universe` (every 15 min, refreshes Innovation Zone membership + force-subscribes) and `wavewatch_tick` (every 1 min, scores + alerts). The force-subscribe TTL is 30 min — 2× the universe cadence — so symbols stay subscribed even if one universe tick fails.
- Use `structlog` (`from app.logging_config import log`) — JSON logs with kwargs (`log.info("event_name", key=value)`), not f-strings.
- The `BINANCE_STREAMS_ENABLED` env flag gates the websocket consumer; default is **off** in compose so a local boot doesn't spam the network.
