# TradeCore — Claude Agent Prompts
*One prompt per team. Each is self-contained and ready to paste into a new Claude session.*

---

## How to use this document

1. Complete each team in the order shown — never skip ahead
2. Paste the full prompt into a new Claude session
3. Give Claude access to your project folder before starting
4. When a team finishes, verify the deliverables checklist before starting the next
5. Each prompt references files from previous teams — make sure those files exist first

---

---

# TEAM 1 — Foundation
## Database · Auth · Billing
### ⚠️ START HERE. All other teams depend on this one.

---

```
You are building the foundation layer of TradeCore — a professional crypto 
trading SaaS with 10 modules. Your job covers three areas: database setup, 
authentication system, and billing integration.

== WHAT TRADECORE IS ==
TradeCore is a SaaS platform for crypto traders. It has 10 modules:
RadarX (volume spikes), WhaleRadar (whale detection), LiquidMap (liquidations),
SentimentPulse (funding/sentiment), MacroPulse (macro economics), GemRadar 
(small-cap scanner), RiskCalc (position sizing), TradeLog (trade journal), 
PerformanceCore (analytics), and Oracle (AI decision engine).

Users subscribe to plans: Free, Pro ($29/mo), Elite ($79/mo).
Each plan unlocks different modules and features.

== YOUR SCOPE ==
Build exactly these three things. Nothing else.

1. DATABASE
   - PostgreSQL database with all 29 tables defined in TradeCore_Database_Schema.md
   - Use Alembic for migrations (never raw SQL ALTER in production)
   - Add all indexes specified in the schema
   - Seed file with: 3 default plans (free/pro/elite), test user, sample data
   - Database URL from environment variable: DATABASE_URL

2. AUTHENTICATION SYSTEM
   - FastAPI app skeleton at /app/main.py
   - Auth router at /app/routers/auth.py
   - Endpoints:
       POST /auth/register     — email + password, sends verification email
       POST /auth/login        — returns JWT access token + refresh token
       POST /auth/refresh      — refreshes access token
       POST /auth/logout       — invalidates session
       GET  /auth/me           — returns current user profile
       POST /auth/verify-email — verifies email token
       POST /auth/forgot-password
       POST /auth/reset-password
   - JWT: access token expires 15min, refresh token expires 7 days
   - Passwords hashed with bcrypt (passlib)
   - Google OAuth endpoint: GET /auth/google, GET /auth/google/callback
   - Middleware: get_current_user dependency for protected routes
   - Rate limiting: max 10 login attempts per IP per 15 minutes

3. BILLING
   - Stripe integration at /app/routers/billing.py
   - Endpoints:
       POST /billing/create-checkout   — creates Stripe checkout session
       POST /billing/portal            — opens Stripe customer portal
       POST /billing/webhook           — handles Stripe webhooks
       GET  /billing/subscription      — returns current subscription status
   - Webhook events to handle:
       customer.subscription.created
       customer.subscription.updated
       customer.subscription.deleted
       invoice.payment_succeeded
       invoice.payment_failed
   - After payment: update users subscription in DB, set plan features
   - Feature flag helper: user_has_access(user_id, feature_name) → bool

== TECH STACK ==
- Python 3.11+
- FastAPI
- SQLAlchemy 2.0 (async) with asyncpg driver
- Alembic for migrations
- Passlib + bcrypt for passwords
- python-jose for JWT
- Stripe Python SDK
- httpx for async HTTP
- Redis (aioredis) for rate limiting and session storage
- Pydantic v2 for all schemas
- python-dotenv for environment variables

== PROJECT STRUCTURE TO CREATE ==
tradecore/
├── app/
│   ├── main.py                  # FastAPI app, CORS, middleware, router mounts
│   ├── database.py              # Async SQLAlchemy engine, session factory
│   ├── config.py                # Settings from .env (pydantic BaseSettings)
│   ├── dependencies.py          # get_current_user, get_db, require_plan
│   ├── models/
│   │   ├── __init__.py
│   │   ├── user.py              # User, Session, EmailVerification, PasswordReset
│   │   ├── billing.py           # Plan, Subscription, Invoice
│   │   └── settings.py         # UserSettings, Watchlist
│   ├── schemas/
│   │   ├── auth.py              # Register/Login request+response schemas
│   │   └── billing.py           # Subscription schemas
│   ├── routers/
│   │   ├── auth.py
│   │   └── billing.py
│   └── services/
│       ├── auth_service.py      # Business logic for auth
│       ├── email_service.py     # Send verification/reset emails
│       └── billing_service.py   # Stripe logic
├── alembic/
│   ├── env.py
│   └── versions/
│       └── 001_initial_schema.py
├── .env.example
├── requirements.txt
└── docker-compose.yml           # PostgreSQL + Redis for local dev

== ENVIRONMENT VARIABLES NEEDED ==
DATABASE_URL=postgresql+asyncpg://user:pass@localhost/tradecore
REDIS_URL=redis://localhost:6379
JWT_SECRET=<random 64 char string>
JWT_ALGORITHM=HS256
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
FRONTEND_URL=http://localhost:5173
SENDGRID_API_KEY=...   # or SMTP settings

== IMPORTANT RULES ==
- All database operations must be async
- Never store plain text passwords
- Never log JWT tokens or passwords
- All endpoints must return consistent error format:
  {"error": "message", "code": "ERROR_CODE"}
- All protected endpoints return 401 if token missing/invalid
- Stripe webhook must verify signature before processing
- Write docstrings on all service functions

== DO NOT BUILD ==
- Any trading module logic
- Any frontend code  
- Any WebSocket connections
- Any Binance API integration

== DELIVERABLES CHECKLIST ==
When done, confirm each item works:
[ ] alembic upgrade head runs without errors
[ ] All 29 tables created in PostgreSQL
[ ] POST /auth/register creates user, returns 201
[ ] POST /auth/login returns valid JWT
[ ] GET /auth/me returns user profile with valid token
[ ] GET /auth/me returns 401 with invalid token
[ ] Stripe webhook receives and processes events
[ ] GET /billing/subscription returns plan details
[ ] user_has_access() correctly gates features by plan
[ ] docker-compose up starts PostgreSQL and Redis
[ ] .env.example documents all required variables
```

---

---

# TEAM 2 — Backend Core
## FastAPI Infrastructure · WebSocket · Redis · Telegram
### ⚠️ Requires Team 1 to be complete first.

---

```
You are building the backend infrastructure layer of TradeCore — a professional 
crypto trading SaaS. Team 1 has already built the database, auth system, and 
billing. Your job is to build the real-time data infrastructure that all trading 
modules will use.

== WHAT ALREADY EXISTS (from Team 1) ==
- FastAPI app at app/main.py
- PostgreSQL with all 29 tables via SQLAlchemy async models
- Auth system: JWT, get_current_user dependency
- Billing: Stripe subscriptions, user_has_access() feature gate
- Redis connected at REDIS_URL
- Project structure at tradecore/

== YOUR SCOPE ==
Build these four infrastructure components:

1. BINANCE WEBSOCKET MANAGER
   File: app/services/binance_stream.py

   - Connect to Binance USDT-M Futures WebSocket streams
   - Subscribe to: kline_5m (5-minute candles), aggTrade (aggregate trades),
     forceOrder (liquidations), bookTicker (best bid/ask)
   - Manage subscriptions for up to 300+ symbols simultaneously
     (Binance allows max 200 streams per connection — use multiple connections)
   - On each closed 5m candle: store last 50 candles per symbol in Redis
     Key format: candles:{symbol} → list of last 50 OHLCV dicts
   - On each aggTrade: store in Redis stream for whale detection
     Key format: trades:{symbol} → Redis stream, max 1000 entries
   - On each forceOrder: publish to Redis pub/sub channel: liquidations
   - Symbol discovery: on startup, fetch all active USDT-M futures from
     Binance REST API, filter to those with 24h quote volume > $10M USD
   - Re-discover symbols every 60 minutes
   - Reconnect automatically on disconnect (exponential backoff, max 30s)
   - Log connection status, disconnections, and reconnections

2. REDIS DATA LAYER
   File: app/services/redis_service.py

   Functions to implement:
   - get_candles(symbol, limit=50) → list of candle dicts
   - get_latest_candle(symbol) → single candle dict
   - get_symbol_list() → list of active symbol strings
   - get_funding_rate(symbol) → float
   - set_funding_rate(symbol, rate, ttl=3600)
   - get_open_interest(symbol) → dict {oi_usd, oi_contracts}
   - set_open_interest(symbol, data, ttl=300)
   - get_liquidation_heatmap(symbol) → dict of price_level → usd_size
   - update_liquidation_heatmap(symbol, price, size, side)
   - set_alert_cooldown(module, symbol, minutes)
   - is_on_cooldown(module, symbol) → bool
   - get_user_session(token_hash) → user_id or None
   - set_user_session(token_hash, user_id, ttl_seconds)
   - invalidate_user_session(token_hash)
   - publish_alert(module, alert_dict) — publishes to Redis pub/sub

3. WEBSOCKET API (for frontend)
   File: app/routers/ws.py

   - WebSocket endpoint: GET /ws?token=<jwt>
   - Authenticate user via JWT on connect
   - After auth, subscribe user to their personalised alert stream
   - Push events to user based on their watchlist and settings:
       {type: "radarx_alert", data: {...}}
       {type: "whale_alert", data: {...}}
       {type: "price_update", symbol: "SOLUSDT", price: 142.40}
       {type: "oracle_signal", data: {...}}
   - Handle disconnections gracefully
   - On reconnect, send last 20 unread alerts
   - WebSocket manager class: tracks all connected users
     Supports: broadcast_to_user(user_id, event), broadcast_all(event)

4. TELEGRAM BOT
   File: app/services/telegram_service.py

   - Bot using python-telegram-bot library (async)
   - Commands:
       /start   — welcome message, instructions to link account
       /link <token>  — links Telegram chat to TradeCore account
       /status  — shows current subscription and alert settings
       /pause   — pauses all alerts for 1 hour
       /resume  — resumes alerts
   - Alert delivery: send_alert(chat_id, module, alert_data) → bool
   - Format messages with proper Markdown:
       🚨 *RadarX Alert — SOLUSDT*
       Z-Score: `5.21` | Ratio: `7.4×`
       Volume: `$24.1M` | Price: `+2.31%`
       [View Chart](https://tradingview.com/...)
   - One Telegram bot serves all users — route by chat_id stored in user_settings
   - Linking flow: user clicks "Connect Telegram" in app → gets unique token →
     sends /link <token> to bot → bot stores chat_id in user_settings table

== TECH STACK ==
- websockets library for Binance streams
- aioredis for Redis operations
- FastAPI WebSockets for frontend connections
- python-telegram-bot >= 20.0 (async version)
- asyncio for concurrent stream management
- structlog for structured logging

== IMPORTANT RULES ==
- All stream handling must be non-blocking async
- Never block the event loop — use asyncio.create_task for background work
- Handle Binance rate limits gracefully (429 responses)
- Log every stream connection/disconnection with timestamp
- Redis keys must follow the naming convention defined above exactly
  (other teams depend on these exact key names)
- Binance streams are public — no API key needed for market data
- If a symbol stream fails, log error and continue with others

== DO NOT BUILD ==
- Any signal detection logic (Z-scores, whale detection) — that's Teams 4/5
- Any frontend code
- Any Oracle/AI logic
- Any user-facing API endpoints beyond /ws

== NEW ENVIRONMENT VARIABLES ==
TELEGRAM_BOT_TOKEN=...
BINANCE_BASE_URL=wss://fstream.binance.com
BINANCE_REST_URL=https://fapi.binance.com

== DELIVERABLES CHECKLIST ==
[ ] BinanceStreamManager starts and connects to Binance WebSocket
[ ] Candle data appears in Redis after first 5m candle closes
[ ] Symbol list in Redis is populated with 200+ symbols
[ ] GET /ws authenticates via JWT and accepts connection
[ ] Frontend WebSocket receives price_update events
[ ] Telegram bot responds to /start command
[ ] /link command successfully stores chat_id in database
[ ] Alert published to Redis pub/sub appears in connected WebSocket client
[ ] Reconnection works after simulated disconnect
```

---

---

# TEAM 3 — Frontend Core
## React App · Design System · Layout · Components
### ⚠️ Can run in parallel with Team 2. Requires Team 1 first.

---

```
You are building the frontend foundation of TradeCore — a professional crypto 
trading SaaS. Your job is to set up the React application, implement the design 
system, and build all reusable components. You are NOT building any module-specific 
pages yet — only the shell and component library.

== DESIGN SYSTEM (implement exactly as specified) ==
Refer to TradeCore_Design_System.md for the full specification. Key points:

Colors: dark navy background (#070C18), electric blue primary (#3B82F6),
profit green (#10B981), loss red (#EF4444). Each module has its own accent color.

Fonts: Inter for UI, JetBrains Mono for all numbers/prices.

Layout: Fixed top bar (48px) + left sidebar (240px, collapsible to 64px) + 
scrollable main content area.

The HTML preview at TradeCore_Design_Preview.html shows the target visual.
Match it precisely.

== YOUR SCOPE ==

1. PROJECT SETUP
   - Vite + React 18 + TypeScript
   - TailwindCSS with custom config matching the design system tokens
   - React Router v6 for page routing
   - Zustand for global state management
   - React Query (TanStack Query) for API data fetching and caching
   - Axios for HTTP client with JWT interceptor (auto-refresh on 401)
   - Socket.io-client or native WebSocket hook for real-time data
   - Recharts for charts, TradingView Lightweight Charts for candlestick

2. DESIGN SYSTEM IMPLEMENTATION
   - CSS variables in index.css matching all tokens in TradeCore_Design_System.md
   - TailwindCSS config extending with all custom colors and spacing
   - Google Fonts: Inter + JetBrains Mono loaded in index.html

3. LAYOUT COMPONENTS
   - <AppLayout> — wraps all authenticated pages, renders TopBar + Sidebar + main
   - <TopBar> — logo, macro strip (6 metrics), account balance, notifications, avatar
   - <Sidebar> — module navigation with dots, labels, badges, active state,
                  collapsible to icon-only mode, bottom settings/billing links
   - <AuthLayout> — centered card layout for login/register pages

4. CORE UI COMPONENTS (build as a component library)
   Components to build (each in src/components/ui/):

   <Card> — variants: default, alert (colored left border), active, danger
   <Button> — variants: primary, secondary, ghost, danger. Sizes: sm, md, lg
   <Badge> — variants: bullish, bearish, warning, neutral, new, module
   <Input> — with label, error state, focus ring
   <Select> — styled dropdown matching design system
   <Table> — sortable, hoverable, with number alignment
   <MetricCard> — label + large value + change indicator
   <AlertItem> — module-colored left border, symbol, stats, action buttons
   <LiveIndicator> — pulsing green dot + LIVE text
   <Skeleton> — shimmer loading state, used everywhere while data loads
   <Modal> — backdrop + centered card + close button
   <Tooltip> — appears on hover, used for icon-only sidebar
   <Tabs> — horizontal tab navigation within pages
   <Tag> — small rounded label, used for trade tags
   <NumberDisplay> — always uses JetBrains Mono, colors positive/negative
   <PercentChange> — shows +2.31% in profit color, -1.12% in loss color

5. AUTH PAGES
   - /login — email + password form, Google OAuth button, link to register
   - /register — email + password + name, terms checkbox
   - /verify-email — confirmation screen
   - /forgot-password — email input
   - /reset-password — new password form

6. ROUTING & AUTH GUARD
   - <ProtectedRoute> — redirects to /login if no valid JWT
   - <PlanGuard> — shows upgrade prompt if user lacks plan access
   - JWT stored in memory (not localStorage) + httpOnly cookie for refresh
   - On app load: check /auth/me — if valid, restore session; if 401, redirect to login

7. STATE MANAGEMENT
   Zustand stores:
   - useAuthStore — user, token, login(), logout()
   - useWebSocketStore — connection status, subscribe(), last alerts
   - useSettingsStore — user preferences (theme, watchlist, alert thresholds)
   - useMacroStore — top bar macro metrics (updated every 60s)

8. API CLIENT
   - src/api/client.ts — Axios instance, base URL from env, JWT header injection
   - src/api/auth.ts — login, register, logout, me
   - src/api/billing.ts — subscription, checkout, portal
   - Auto-refresh: on 401, attempt token refresh, retry original request once

== TECH STACK ==
- React 18 + TypeScript + Vite
- TailwindCSS 3
- React Router v6
- Zustand
- TanStack Query v5
- Axios
- Recharts + Lightweight Charts (TradingView)
- Lucide React for icons

== FILE STRUCTURE ==
frontend/
├── index.html
├── vite.config.ts
├── tailwind.config.ts
├── src/
│   ├── main.tsx
│   ├── App.tsx               # Router setup, auth check on load
│   ├── index.css             # CSS variables + Tailwind imports
│   ├── api/
│   │   ├── client.ts
│   │   ├── auth.ts
│   │   └── billing.ts
│   ├── components/
│   │   ├── layout/
│   │   │   ├── AppLayout.tsx
│   │   │   ├── TopBar.tsx
│   │   │   ├── Sidebar.tsx
│   │   │   └── AuthLayout.tsx
│   │   └── ui/
│   │       ├── Card.tsx
│   │       ├── Button.tsx
│   │       ├── Badge.tsx
│   │       ├── Input.tsx
│   │       ├── Table.tsx
│   │       ├── MetricCard.tsx
│   │       ├── AlertItem.tsx
│   │       ├── LiveIndicator.tsx
│   │       ├── Skeleton.tsx
│   │       ├── Modal.tsx
│   │       ├── NumberDisplay.tsx
│   │       └── PercentChange.tsx
│   ├── stores/
│   │   ├── authStore.ts
│   │   ├── webSocketStore.ts
│   │   ├── settingsStore.ts
│   │   └── macroStore.ts
│   ├── pages/
│   │   ├── auth/
│   │   │   ├── Login.tsx
│   │   │   ├── Register.tsx
│   │   │   └── ForgotPassword.tsx
│   │   └── dashboard/
│   │       └── index.tsx     # placeholder — modules added by Team 7
│   ├── hooks/
│   │   ├── useWebSocket.ts   # connects to /ws, dispatches to store
│   │   ├── useAuth.ts
│   │   └── useAlerts.ts
│   └── types/
│       ├── auth.ts
│       ├── alerts.ts
│       └── market.ts

== ENVIRONMENT VARIABLES ==
VITE_API_URL=http://localhost:8000
VITE_WS_URL=ws://localhost:8000/ws

== IMPORTANT RULES ==
- Never store JWT access token in localStorage — memory only
- All numbers must render with <NumberDisplay> or <PercentChange> components
- All loading states must show <Skeleton> — never a blank white space
- Sidebar must be fully keyboard navigable
- Match TradeCore_Design_Preview.html visually as closely as possible
- Mobile: sidebar collapses to hamburger menu at md breakpoint

== DO NOT BUILD ==
- Any module-specific pages (RadarX, Oracle, etc.) — that is Team 7
- Any trading logic
- Backend code

== DELIVERABLES CHECKLIST ==
[ ] npm run dev starts without errors
[ ] Login page renders and submits to POST /auth/login
[ ] JWT auth guard redirects unauthenticated users to /login
[ ] TopBar renders with all 6 macro metric placeholders
[ ] Sidebar renders all 10 modules with correct accent colors
[ ] Sidebar collapses to icon-only mode on toggle
[ ] All UI components render in isolation (test each one)
[ ] WebSocket connects to /ws on login and receives events
[ ] <NumberDisplay> shows positive in green, negative in red
[ ] <Skeleton> shimmer visible during loading states
```

---

---

# TEAM 4 — Detection Modules
## RadarX · WhaleRadar · GemRadar
### ⚠️ Requires Team 2 to be complete first.

---

```
You are building three detection modules for TradeCore: RadarX (volume spikes),
WhaleRadar (whale activity), and GemRadar (small-cap scanner). These modules
read live market data from Redis and PostgreSQL, detect signals, store alerts
in the database, and publish them for delivery.

== WHAT ALREADY EXISTS ==
- Redis: candle buffers (candles:{symbol}), trade streams (trades:{symbol}),
  liquidation pub/sub, symbol list (symbol_list key)
- PostgreSQL: radarx_alerts, whale_trades, whale_onchain_transfers,
  oi_surge_events, gemradar_alerts tables
- redis_service.py: get_candles(), is_on_cooldown(), set_alert_cooldown(),
  publish_alert()
- Auth: get_current_user dependency
- All Binance streams active via BinanceStreamManager

== MODULE 1: RadarX ==
File: app/modules/radarx/detector.py

Detection logic (runs every time a 5m candle closes):
1. Fetch last 21 closed candles for symbol from Redis
2. Separate: last candle = current, candles[0:20] = baseline
3. Compute baseline: mean_vol = mean(baseline volumes)
                     std_vol = std(baseline volumes)
4. Compute: z_score = (current_vol - mean_vol) / std_vol
            ratio = current_vol / mean_vol
5. Check: z_score >= threshold (default 3.0) AND ratio >= threshold (default 4.0)
          AND symbol NOT on cooldown (redis_service.is_on_cooldown)
          AND symbol 24h volume > min_volume_usd (default $10M)
6. If passes: 
   - Save to radarx_alerts table
   - Call redis_service.set_alert_cooldown('radarx', symbol, 30)
   - Call redis_service.publish_alert('radarx', alert_dict)
   - Return alert dict

Alert dict format:
{
  "module": "radarx",
  "symbol": "SOLUSDT",
  "z_score": 5.21,
  "ratio": 7.4,
  "candle_volume_usd": 24100000,
  "avg_volume_usd": 3256000,
  "price": 142.40,
  "price_change_pct": 2.31,
  "volume_24h_usd": 2100000000,
  "triggered_at": "2026-04-15T09:45:00Z",
  "tradingview_url": "https://tradingview.com/chart/?symbol=BINANCE:SOLUSDT.P"
}

File: app/modules/radarx/router.py
API endpoints:
  GET /radarx/alerts          — paginated list, query params: symbol, limit, offset
  GET /radarx/alerts/{id}     — single alert detail
  GET /radarx/top-movers      — top 20 symbols by current z-score (live, from Redis)
  GET /radarx/stats           — today's alert count, avg z-score, top symbol

== MODULE 2: WhaleRadar ==
File: app/modules/whaleradar/detector.py

Detection logic 1 — Large Trade Detector (runs on each aggTrade from Redis stream):
1. Read latest trades from Redis stream (trades:{symbol})
2. Flag any single trade where quote_qty >= min_trade_usd (default $300,000)
3. Determine side: buyer_maker=False → buy, buyer_maker=True → sell
4. If not on cooldown:
   - Save to whale_trades table
   - Publish alert with module='whaleradar', type='large_trade'
   - Set cooldown: 5 minutes per symbol (not 30 — whales can trade repeatedly)

Detection logic 2 — OI Surge Detector (runs every 5 minutes via scheduler):
1. Fetch current OI for all symbols from Binance REST:
   GET https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}
2. Compare to previous OI stored in Redis
3. If change >= 5% in 5 minutes:
   - Determine direction (long_heavy if price up + OI up, short_heavy if price down + OI up)
   - Save to oi_surge_events table
   - Publish alert with type='oi_surge'
4. Store new OI in Redis: set_open_interest(symbol, data)

Detection logic 3 — On-chain Transfer Monitor (polls Whale Alert API every 60s):
1. GET https://api.whale-alert.io/v1/transactions?api_key={key}&min_value=500000
2. For each new transaction not already processed:
   - Classify: from_label/to_label contains 'exchange' → exchange_inflow or outflow
   - Save to whale_onchain_transfers table
   - Publish alert with type='onchain_transfer'
3. Track last processed transaction ID in Redis to avoid duplicates

File: app/modules/whaleradar/router.py
  GET /whaleradar/trades              — large trade events, paginated
  GET /whaleradar/onchain             — on-chain transfers, paginated
  GET /whaleradar/oi-surges           — OI surge events
  GET /whaleradar/stats               — summary stats

== MODULE 3: GemRadar ==
File: app/modules/gemradar/detector.py

Detection logic — runs every 2 minutes:
1. Fetch all pairs from DexScreener API:
   GET https://api.dexscreener.com/latest/dex/tokens/solana (and ethereum, bsc)
2. Filter: market_cap between min ($1M) and max ($100M)
3. For each pair, check:
   - price_change_5m >= threshold (default 10%)
   - volume_5m / market_cap >= ratio_threshold (default 5×)
4. For flagged pairs, run risk check:
   - Call RugCheck API (Solana): GET https://api.rugcheck.xyz/v1/tokens/{mint}/report
   - Determine: contract_verified, liquidity_locked, has_mint_function,
                top10_wallet_pct, contract_age_hours
   - Compute risk_score: 
       start at 0, add points for each risk flag:
       +30 if not verified, +25 if liquidity unlocked, +20 if mint active,
       +15 if top10 > 70%, +10 if age < 24h
       0-30 = low, 31-50 = medium, 51-75 = high, 76+ = extreme
5. Save to gemradar_alerts table
6. Publish alert if risk_score < user's risk_tolerance setting

Also: New CEX listing detection
- Poll Binance announcements RSS every 15 minutes
- Parse for "will list" keywords
- Publish cex_listing alert immediately on detection

File: app/modules/gemradar/router.py
  GET /gemradar/alerts            — small-cap alerts, filterable by risk_score
  GET /gemradar/trending          — current trending on Solana/ETH DEXes
  GET /gemradar/new-listings      — recent CEX listing announcements

== SCHEDULER ==
File: app/services/scheduler.py
Use APScheduler (AsyncIOScheduler):
- Every 5m: RadarX detector runs on all symbols in symbol list
- Every 5m: WhaleRadar OI surge check
- Every 2m: GemRadar scan
- Every 60s: WhaleRadar on-chain poll
- Every 1h: Symbol list refresh

== NEW ENVIRONMENT VARIABLES ==
WHALE_ALERT_API_KEY=...
MIN_TRADE_USD=300000
MIN_VOLUME_24H_USD=10000000
RADARX_ZSCORE_THRESHOLD=3.0
RADARX_RATIO_THRESHOLD=4.0

== IMPORTANT RULES ==
- All detectors must read user-specific thresholds from user_settings table
  when delivering alerts (different users may have different thresholds)
- Log every alert fired with symbol, module, and key metric
- Handle API failures gracefully — log error and continue, never crash scheduler
- DexScreener and RugCheck have no API key needed for basic usage
- Whale Alert free tier: 10 req/min — implement rate limiting

== DO NOT BUILD ==
- Frontend code
- Oracle scoring logic
- SentimentPulse or MacroPulse modules

== DELIVERABLES CHECKLIST ==
[ ] RadarX detector fires alert when z_score > 3 on test data
[ ] RadarX alert saved to radarx_alerts table correctly
[ ] RadarX cooldown prevents re-alert within 30 minutes
[ ] GET /radarx/top-movers returns live ranked list
[ ] WhaleRadar large trade detector fires on $300k+ trade
[ ] OI surge detected and saved to oi_surge_events
[ ] Whale Alert API successfully polled and transactions saved
[ ] GemRadar scans DexScreener every 2 minutes
[ ] Risk score calculated correctly for test tokens
[ ] Scheduler starts all jobs on app startup without errors
```

---

---

# TEAM 5 — Analysis Modules
## SentimentPulse · MacroPulse · LiquidMap
### ⚠️ Requires Team 2 to be complete first. Can run in parallel with Team 4.

---

```
You are building three analysis modules for TradeCore: SentimentPulse 
(market sentiment data), MacroPulse (macroeconomic metrics), and LiquidMap 
(liquidation tracking). These modules provide context data that enriches 
other module signals.

== WHAT ALREADY EXISTS ==
- Redis: symbol list, candle buffers, liquidation pub/sub channel
- PostgreSQL: sentiment_snapshots, market_sentiment_snapshots, macro_snapshots,
  economic_events, liquidation_events tables
- redis_service.py with all helper functions
- Scheduler (APScheduler) running in app/services/scheduler.py

== MODULE 1: SentimentPulse ==
File: app/modules/sentimentpulse/collector.py

Data collection (scheduled):

Every 1 hour — per-symbol snapshot:
1. Fetch funding rates: GET https://fapi.binance.com/fapi/v1/fundingRate
2. Fetch long/short ratio: GET https://fapi.binance.com/futures/data/globalLongShortAccountRatio
3. Fetch open interest: GET https://fapi.binance.com/fapi/v1/openInterest
4. For each symbol in top 50 by volume: save to sentiment_snapshots table
5. Store current funding rate in Redis: set_funding_rate(symbol, rate)

Every 1 hour — market-wide snapshot:
1. Fetch Fear & Greed Index: GET https://api.alternative.me/fng/?limit=1
2. Fetch BTC dominance + total market cap: GET https://api.coingecko.com/api/v3/global
3. Save to market_sentiment_snapshots table

Alerts to publish (via redis_service.publish_alert):
- Extreme funding rate: if abs(funding_rate) > 0.05% → publish 'extreme_funding' alert
- Extreme long/short: if ratio > 70% one side → publish 'crowded_positioning' alert

File: app/modules/sentimentpulse/router.py
  GET /sentiment/overview           — current fear/greed, BTC dominance, market cap
  GET /sentiment/funding            — funding rates for top symbols, sortable
  GET /sentiment/long-short         — long/short ratios for top symbols
  GET /sentiment/history/{symbol}   — 7d sentiment history for a symbol

== MODULE 2: MacroPulse ==
File: app/modules/macropulse/collector.py

Data collection:

Every 1 hour — market data (Yahoo Finance via yfinance library):
Fetch: DXY (DX-Y.NYB), US10Y (^TNX), US2Y (^IRX), VIX (^VIX),
       S&P500 (^GSPC), NASDAQ (^IXIC), Gold (GC=F)
Store latest values in Redis with 2h TTL:
  Key: macro:{ticker} → {value, change_pct, timestamp}

Every 24 hours — daily snapshot:
1. Fetch all market data above
2. Fetch BTC ETF flows from CoinGlass:
   GET https://open-api.coinglass.com/public/v2/indicator/bitcoin_etf
3. Fetch stablecoin market cap from CoinGecko:
   GET https://api.coingecko.com/api/v3/coins/markets?category=stablecoins
4. Compute macro_score (-100 to +100):
   Start at 0
   DXY rising → -10, falling → +10
   VIX > 25 → -15, < 15 → +10
   US10Y rising → -10, falling → +5
   ETF flows positive → +15, negative → -15
   S&P500 up → +10, down → -10
5. Save to macro_snapshots table

Every 24 hours — economic calendar sync:
Fetch upcoming events from TradingEconomics or Investing.com
Store in economic_events table: name, impact, scheduled_at, forecast, previous

After each high-impact economic event releases:
1. Record actual_value in economic_events table
2. Fetch BTC price 1h and 4h after release
3. Compute btc_reaction_1h_pct and btc_reaction_4h_pct
4. Update economic_events row — this builds the historical reaction dataset

File: app/modules/macropulse/router.py
  GET /macro/snapshot       — latest values for all macro metrics
  GET /macro/score          — current macro_score with breakdown
  GET /macro/calendar       — upcoming economic events, filterable by impact
  GET /macro/etf-flows      — BTC ETF daily flows, last 30 days
  GET /macro/history        — historical macro snapshots, last 90 days

== MODULE 3: LiquidMap ==
File: app/modules/liquidmap/tracker.py

Real-time liquidation stream (from Redis pub/sub — published by Team 2):
1. Subscribe to Redis pub/sub channel: liquidations
2. On each liquidation event:
   - Parse: symbol, side (long/short), size_usd, price
   - If size_usd >= $100,000:
     → Save to liquidation_events table
   - If size_usd >= $1,000,000:
     → Also publish alert: module='liquidmap', type='large_liquidation'
   - Update in-memory liquidation heatmap:
     → Round price to nearest 0.1% bucket
     → Accumulate size per bucket in Redis hash
     → Key: liqmap:{symbol} → hash of {price_bucket: cumulative_usd}
     → TTL: 4 hours (heatmap decays — old data not relevant)

Heatmap computation:
- Cluster nearby price buckets (within 0.5%)
- Return top 20 concentration levels per symbol
- These represent likely "magnet" price levels

File: app/modules/liquidmap/router.py
  GET /liquidmap/heatmap/{symbol}   — top 20 liquidation concentration levels
  GET /liquidmap/recent             — recent large liquidations (>$1M), last 2h
  GET /liquidmap/stats/{symbol}     — total liquidated long vs short today

== MACRO SCORE ENDPOINT (shared) ==
File: app/modules/macropulse/score.py

compute_macro_context(symbol) → dict:
Returns macro context for any alert, used by Oracle module:
{
  "macro_score": 42,
  "dxy_trend": "rising",    # "rising", "falling", "neutral"
  "vix_level": "low",       # "low" (<15), "medium" (15-25), "high" (>25)
  "etf_flows": "positive",  # "positive", "negative", "neutral"
  "risk_environment": "favorable",  # "favorable", "neutral", "caution", "risk_off"
  "key_events_24h": ["CPI Release 13:30 UTC"]
}

This function is imported by Team 6 (Oracle) — make sure it's importable.

== NEW ENVIRONMENT VARIABLES ==
COINGLASS_API_KEY=...       # free tier available
COINGECKO_API_KEY=...       # free tier available
TRADING_ECONOMICS_API_KEY=... # optional, economic calendar

== IMPORTANT RULES ==
- yfinance is free but rate-limited — cache all values in Redis
- CoinGecko free tier: 30 req/min — implement request throttling
- Never store tick-by-tick data — hourly snapshots only for sentiment
- Economic calendar events: always store in UTC
- Liquidation heatmap lives in Redis only — PostgreSQL stores only notable events (>$100k)

== DO NOT BUILD ==
- Frontend code
- RadarX, WhaleRadar, GemRadar modules
- Oracle module

== DELIVERABLES CHECKLIST ==
[ ] Funding rates fetched from Binance and stored in Redis
[ ] sentiment_snapshots table populated every hour
[ ] Fear & Greed Index value in market_sentiment_snapshots
[ ] GET /sentiment/funding returns current rates for top 20 symbols
[ ] DXY, VIX, US10Y fetched via yfinance and stored
[ ] macro_snapshots table has daily snapshot entry
[ ] macro_score computed correctly (test with known market conditions)
[ ] GET /macro/snapshot returns all values
[ ] compute_macro_context() function importable and returns correct format
[ ] Liquidation events from Redis pub/sub saved to liquidation_events table
[ ] GET /liquidmap/heatmap/{symbol} returns price concentration levels
[ ] Large liquidation (>$1M) publishes alert correctly
```

---

---

# TEAM 6 — Intelligence & Execution Modules
## Oracle · RiskCalc · TradeLog · PerformanceCore
### ⚠️ Requires Teams 4 and 5 to be complete first.

---

```
You are building four modules for TradeCore: Oracle (AI decision engine), 
RiskCalc (position sizing), TradeLog (trade journal), and PerformanceCore 
(analytics). These are the highest-value modules in the system.

== WHAT ALREADY EXISTS ==
- All detection modules: RadarX, WhaleRadar, GemRadar (publish alerts to Redis)
- All analysis modules: SentimentPulse, MacroPulse, LiquidMap
- compute_macro_context() function from macropulse/score.py
- All alert tables in PostgreSQL
- redis_service.publish_alert() for broadcasting signals
- User auth, settings, and subscription system

== MODULE 1: Oracle ==
File: app/modules/oracle/engine.py

The Oracle aggregates signals from all modules and produces a scored recommendation.

Signal collection (runs after every RadarX or WhaleRadar alert):
1. Get triggered symbol from alert
2. Collect current signals from all 6 modules:

   RadarX signal:
   - If radarx alert fired in last 5 minutes for this symbol:
     direction = bullish if price_change > 0 else bearish
     intensity = min(z_score / 8.0, 1.0)  # normalize to 0-1
   
   WhaleRadar signal:
   - If whale_trade fired in last 10 minutes:
     direction = buy side of trade
     intensity = min(trade_size_usd / 2000000, 1.0)
   - If oi_surge fired: add intensity based on oi_change_pct
   
   LiquidMap signal:
   - Get heatmap for symbol
   - If large short liquidation cluster within 2% above price → bullish
   - If large long liquidation cluster within 2% below price → bearish
   - intensity = cluster_size / total_daily_liquidations
   
   SentimentPulse signal:
   - funding_rate > 0.03% → slightly bearish (longs paying too much)
   - funding_rate < -0.01% → bullish (shorts squeezable)
   - long_ratio > 65% → bearish (too many longs = top signal)
   - intensity based on extremity of reading
   
   MacroPulse signal:
   - Use compute_macro_context() result
   - macro_score > 30 → bullish, < -30 → bearish
   - intensity = abs(macro_score) / 100
   
   GemRadar signal:
   - Only relevant if symbol is a small-cap
   - Not applicable for BTC/ETH → intensity = 0, skip

3. Load user's weights from user_settings (or use defaults):
   macropulse=25, whaleradar=20, radarx=15, liquidmap=15, sentimentpulse=15, gemradar=10

4. Compute score:
   For each module: module_contribution = direction_value * intensity * weight
   (direction_value: bullish=+1, bearish=-1, neutral=0)
   score = sum(all contributions)  # range: -100 to +100

5. Map score to recommendation:
   score >= 75: "strong_long"
   score >= 50: "long"  
   score >= 25: "watch_long"
   score > -25: "neutral"
   score <= -75: "strong_short"
   score <= -50: "short"
   else: "watch_short"

6. Confluence count = number of modules with |intensity| > 0.3

7. Confidence:
   confluence >= 5: "high"
   confluence >= 3: "medium"
   else: "low"

8. Auto-calculate trade parameters (using RiskCalc logic):
   - Entry: current price
   - Stop: entry - (ATR * 1.5) where ATR = average true range last 14 candles
   - Target: entry + (stop_distance * 2.5) for R:R of 2.5×
   - Size: from user's risk_per_trade_pct setting

9. Save oracle_signal to database
10. Schedule outcome measurement: check price at 15m, 1h, 4h, 24h
    Use APScheduler delayed jobs to fill oracle_outcomes table

11. If score >= 65 (user's min_score threshold): publish_alert('oracle', signal_dict)

File: app/modules/oracle/router.py
  GET /oracle/signals               — recent signals, paginated
  GET /oracle/signals/{id}          — signal detail with full breakdown
  GET /oracle/performance           — accuracy stats (from oracle_outcomes)
  GET /oracle/live/{symbol}         — current live score for any symbol (on demand)
  POST /oracle/settings             — update user's module weights and thresholds

== MODULE 2: RiskCalc ==
File: app/modules/riskcalc/calculator.py

calculate_position(params: RiskCalcParams) → RiskCalcResult:

Inputs:
- account_balance_usd: float
- risk_pct: float (e.g. 1.0 for 1%)
- entry_price: float
- stop_loss_price: float
- take_profit_price: float (optional)
- is_futures: bool
- max_leverage: float (optional, default 20)

Computation:
- risk_amount = account_balance * (risk_pct / 100)
- stop_distance = abs(entry - stop_loss) / entry  # as decimal
- position_size_usd = risk_amount / stop_distance
- position_size_units = position_size_usd / entry_price
- leverage = position_size_usd / account_balance  # naive leverage
- leverage = min(leverage, max_leverage)  # cap at max
- liquidation_price (futures): entry * (1 - 1/leverage) for long
- rr_ratio = (take_profit - entry) / (entry - stop_loss) if take_profit else None
- max_loss_usd = risk_amount
- potential_profit_usd = risk_amount * rr_ratio if rr_ratio else None

Warnings to generate:
- "Leverage too high" if leverage > 10
- "Liquidation too close" if abs(liq_price - stop_loss) / entry < 0.02
- "R:R below 2" if rr_ratio and rr_ratio < 2.0
- "Risk too high" if risk_pct > 2.0

Save to riskcalc_history table. Return RiskCalcResult.

File: app/modules/riskcalc/router.py
  POST /riskcalc/calculate         — compute and save a calculation
  GET  /riskcalc/history           — user's calculation history, paginated
  GET  /riskcalc/history/{id}      — single calculation detail

== MODULE 3: TradeLog ==
File: app/modules/tradelog/service.py

Manual trade logging:
- create_trade(user_id, trade_data) → Trade
- update_trade(trade_id, updates) — close trade, add notes, set exit price
- delete_trade(trade_id) — soft delete only

Auto-computation on trade close:
- pnl_usd = (exit_price - entry_price) * size * direction_multiplier - fees_usd
- pnl_pct = pnl_usd / (entry_price * size) * 100
- r_multiple = pnl_usd / risk_amount  # requires stop_loss was set
- Update user's PerformanceCore snapshot trigger

Binance API sync (optional, user provides read-only API key):
File: app/modules/tradelog/exchange_sync.py
- sync_binance_trades(user_id, api_key, api_secret) → list[Trade]
- Fetch from: GET https://fapi.binance.com/fapi/v1/userTrades
- Match to existing trade records, create new ones for unmatched
- Store encrypted API key: encrypt with Fernet using APP_SECRET_KEY

File: app/modules/tradelog/router.py
  GET    /tradelog/trades               — user's trades, filterable, paginated
  POST   /tradelog/trades               — create manual trade
  PATCH  /tradelog/trades/{id}          — update (close, add notes, tags)
  DELETE /tradelog/trades/{id}          — soft delete
  POST   /tradelog/sync                 — trigger Binance API sync
  GET    /tradelog/tags                 — all tags used by this user
  GET    /tradelog/setups               — performance breakdown by setup_name

== MODULE 4: PerformanceCore ==
File: app/modules/performance/aggregator.py

compute_user_performance(user_id, period) → PerformanceSnapshot:
Runs after every trade closes, and also on daily schedule.

Computes from trades table:
- total_trades, winning_trades, losing_trades
- win_rate = winning / total * 100
- avg_win_pct = mean(pnl_pct where pnl > 0)
- avg_loss_pct = mean(pnl_pct where pnl < 0)
- expectancy = (win_rate/100 * avg_win) + ((1-win_rate/100) * avg_loss)
- profit_factor = sum(wins) / abs(sum(losses))
- max_drawdown: compute from equity curve (cumulative pnl over time)
- r_multiples: distribution of R achieved

Signal accuracy (from oracle_outcomes):
compute_signal_accuracy(module) → SignalPerformance:
- Count signals where was_correct_1h = True
- accuracy_1h_pct = correct / total * 100
- avg_move_1h_pct = mean(pnl_1h_pct) for this module's signals
- Save to signal_performance table

File: app/modules/performance/router.py
  GET /performance/overview         — summary stats for all periods
  GET /performance/equity-curve     — daily equity data for chart
  GET /performance/by-setup         — win rate / expectancy per setup name
  GET /performance/by-symbol        — best/worst symbols
  GET /performance/by-time          — best/worst hours, days of week
  GET /performance/signals          — accuracy by module (RadarX, Oracle, etc.)
  GET /performance/r-distribution   — histogram of R multiples achieved

== IMPORTANT RULES ==
- Oracle must be importable by other modules without circular imports
- RiskCalc must never modify the database unless explicitly told to save
- Binance API keys must be encrypted at rest — never stored plain text
- All performance calculations must be idempotent (safe to run multiple times)
- Oracle outcomes: use APScheduler delayed jobs, not async sleep
- Never expose another user's trades or performance data

== DO NOT BUILD ==
- Frontend code
- Binance WebSocket streams (Team 2)
- Detection module logic (Team 4)

== DELIVERABLES CHECKLIST ==
[ ] Oracle engine produces score between -100 and +100 for test symbol
[ ] Signal breakdown shows contribution from each module
[ ] oracle_signals row saved with correct fields
[ ] oracle_outcomes row populated 1h after signal (via scheduler)
[ ] GET /oracle/signals returns paginated list
[ ] RiskCalc compute_position returns correct size for test inputs
[ ] All 4 warnings trigger correctly on bad inputs
[ ] POST /tradelog/trades creates trade for authenticated user
[ ] PATCH /tradelog/trades/{id} closes trade and computes pnl_usd
[ ] PerformanceCore win_rate, expectancy computed correctly on test data
[ ] GET /performance/overview returns correct values
[ ] Signal accuracy computed from oracle_outcomes correctly
```

---

---

# TEAM 7 — Frontend Modules
## All 10 Module Pages
### ⚠️ Requires Teams 3, 4, 5, and 6 to be complete first.

---

```
You are building all 10 module pages for the TradeCore frontend. The React 
application shell already exists (Team 3). Your job is to build a dedicated 
page for each module, connecting to the backend APIs and WebSocket events.

== WHAT ALREADY EXISTS (from Team 3) ==
- React + TypeScript + Vite app running
- All UI components: Card, Button, Badge, Table, MetricCard, AlertItem,
  LiveIndicator, Skeleton, Modal, NumberDisplay, PercentChange
- AppLayout with TopBar and Sidebar
- Auth guards and routing
- Zustand stores, React Query, Axios client
- WebSocket connection to /ws (receives live events)
- TradeCore_Design_System.md and TradeCore_Design_Preview.html for visual reference

== IMPORTANT: Match the design exactly ==
Study TradeCore_Design_Preview.html carefully.
All pages must use the same color system, typography, and component patterns.

== FOR EACH MODULE, BUILD ==
A page component at src/pages/dashboard/{module}/index.tsx
Register route in App.tsx: /{module-slug}

---

MODULE 1: /radarx — RadarX
Sections:
- 4 metric cards: Alerts Today, Avg Z-Score, Top Symbol, Signal Accuracy
- Main layout: Left (alert feed) + Right (top movers table)
- Alert feed: live updating, sorted newest first, colored left borders
  Each alert shows: symbol, z-score, ratio, volume, price change, action buttons
  New alerts slide in from top via animation
- Top movers table: all symbols ranked by current z-score
  Columns: Rank, Symbol, Price, Z-Score, Ratio, Vol(5m), Price Δ, Funding, Signal, Action
  Updates live every 5 seconds from GET /radarx/top-movers
  Badge colors match signal (bullish=green, bearish=red, neutral=gray)
- "Open in RiskCalc" button on each alert: pre-fills RiskCalc with symbol + entry price
- Filters: All / Watchlist toggle at top of alert feed

---

MODULE 2: /whaleradar — WhaleRadar
Sections:
- 3 tabs: Large Trades | OI Surges | On-Chain Transfers
- Large Trades tab:
  Live feed of trades > $300k
  Each row: symbol, size in USD (large, bold), side (BUY/SELL badge), price, time
  Whale buys in profit color, sells in loss color
- OI Surges tab:
  Table: symbol, OI before, OI after, % change, price change, direction badge
- On-Chain tab:
  Table: asset, amount, USD value, from label, to label, type badge, time
  Exchange inflow = warning color (potential sell), outflow = profit (accumulation)
- Top right: CVD chart for selected symbol (buyer vs seller volume bar chart)
- Live indicator in top bar

---

MODULE 3: /liquidmap — LiquidMap
Sections:
- Symbol selector (search dropdown)
- Heatmap visualization:
  Vertical price axis, horizontal = liquidation size (bar width)
  Longs: profit color bars on right
  Shorts: loss color bars on left
  Current price: horizontal line across heatmap
  Thicker bars = more liquidations at that price = stronger magnet
- Right panel: Recent large liquidations (>$1M) feed, live
- Stats row: Total longs liquidated today vs shorts, net bias

---

MODULE 4: /sentiment — SentimentPulse
Sections:
- Overview row: Fear & Greed gauge, BTC dominance bar, total market cap
- Funding rates table:
  Top 30 symbols, sorted by absolute funding rate
  Positive rates: subtle loss background (longs paying too much = bearish signal)
  Negative rates: subtle profit background (shorts squeezable = bullish signal)
- Long/Short ratio table:
  Bar visualization showing % long vs % short per symbol
  Extreme readings (>65% one side) highlighted
- Historical chart: funding rate for selected symbol, last 7 days

---

MODULE 5: /macro — MacroPulse
Sections:
- Macro Score card: large number (-100 to +100), color coded, breakdown below
- Metrics grid (6 cards): DXY, US10Y, VIX, S&P500, BTC ETF Flows, Stablecoin MCap
  Each card: current value, daily change, trend arrow, signal label
- Upcoming events list:
  Economic calendar sorted by scheduled_at
  HIGH impact events prominent (red badge)
  Past events show actual vs forecast + BTC reaction
- Historical chart: overlay of macro_score vs BTC price, last 90 days

---

MODULE 6: /gemradar — GemRadar
Sections:
- Risk filter toggle: Low / Medium / High / All
- Alert cards grid (2 columns):
  Each card: symbol/name, chain badge, price change (large), vol/mcap ratio
  Risk score: colored badge + 3 key risk facts
  DEX/CEX indicator
  "View on DexScreener" link
- New CEX Listings section: recent Binance/Bybit listing announcements
- Trending on DEX: quick list from DexScreener trending API

---

MODULE 7: /riskcalc — RiskCalc
Sections:
- Left: Input form
  Symbol search (optional), Account Balance, Risk %, 
  Entry Price, Stop Loss, Take Profit, Leverage max, Spot/Futures toggle
  Calculate button
- Right: Results panel (appears after calculation)
  Position Size (large), Leverage, Max Loss (loss color), 
  Potential Profit (profit color), R:R Ratio, Liquidation Price
  Warnings list if any (orange badges)
  "Save Calculation" + "Open Trade in TradeLog" buttons
- Below: Calculation history table (last 20 calculations for this user)

---

MODULE 8: /tradelog — TradeLog
Sections:
- Stats row: Win Rate, Avg R, Expectancy, Total PnL (this month)
- Trades table:
  Columns: Symbol, Side (LONG/SHORT badge), Entry, Exit, Size, PnL($), PnL(%), R, 
  Setup, Tags, Date, Status
  Closed trades: profit/loss colored PnL
  Open trades: current PnL computed from live price
- "+ New Trade" button: modal form
  Fields: Symbol, Side, Entry Price, Stop Loss, Take Profit, Size, 
          Setup Name, Notes, Emotion, Is Paper
- Click any trade: side panel with full details, edit capability, chart
- Filters: Open / Closed / Paper / Real, date range, symbol search
- "Sync from Binance" button: triggers API sync (requires API key setup in Settings)

---

MODULE 9: /performance — PerformanceCore
Sections:
- Period tabs: Week / Month / 3 Months / All Time
- Key metrics row: Win Rate, Profit Factor, Expectancy, Max Drawdown, Total PnL
- Equity curve chart: line chart of cumulative PnL over time
  Drawdown shown as shaded red area below peak
- Performance breakdown tabs:
  By Setup: table of each setup name, trade count, win rate, avg R, total PnL
  By Symbol: best and worst performing symbols
  By Time: heatmap of P&L by hour of day and day of week
- Signal accuracy section:
  For each module: accuracy at 1h, accuracy at 4h, avg move
  Shows which signals are actually predictive

---

MODULE 10: /oracle — Oracle
Sections:
- Symbol search at top
- Oracle score display (match design exactly from TradeCore_Design_Preview.html):
  Large radial arc, score in center, recommendation label
  6 module signal bars below (matches preview)
  Confidence level, confluence count
- Trade parameters: Entry, Stop Loss, Take Profit, Suggested Size, R:R
- Action buttons: Paper Trade | Execute Trade | Dismiss
- Recent signals feed: last 10 Oracle signals for user
  Each row: symbol, score, recommendation, signal time, actual outcome (if measured)
  Outcome colored: profit if was_correct, loss if incorrect
- Performance: accuracy summary across all Oracle signals

---

== WEBSOCKET INTEGRATION ==
The useWebSocket hook dispatches events from /ws.
Map events to page updates:

{type: "radarx_alert"} → add to RadarX alert feed, show toast notification
{type: "whale_alert"} → add to WhaleRadar feed
{type: "oracle_signal"} → highlight in Oracle page, show notification
{type: "price_update"} → update prices in all tables

Use React Query's queryClient.invalidateQueries() for less frequent refreshes.
Use direct state updates (Zustand) for high-frequency live data.

== IMPORTANT RULES ==
- All pages must show <Skeleton> while loading
- Tables over 50 rows must use pagination (10 rows default)
- Mobile: tables become card lists at sm breakpoint
- All prices always use <NumberDisplay>, changes use <PercentChange>
- Live data tables must not cause layout shift when updating
- "Open in RiskCalc" must pass symbol + price via URL params or store

== DELIVERABLES CHECKLIST ==
[ ] All 10 routes registered and render without errors
[ ] RadarX page shows live alert feed with WebSocket updates
[ ] Top movers table refreshes every 5 seconds
[ ] WhaleRadar tabs all render with API data
[ ] RiskCalc computes and displays results on form submit
[ ] TradeLog new trade modal saves to database
[ ] Oracle score ring renders with correct arc length for given score
[ ] PerformanceCore equity curve chart renders
[ ] All pages show Skeleton state during loading
[ ] Toast notification appears when WebSocket delivers alert
```

---

---

# TEAM 8 — Infrastructure & QA
## Deployment · Security · Testing · Landing Page
### ⚠️ Can run in parallel with Teams 6 and 7.

---

```
You are building the deployment infrastructure, security hardening, test suite, 
and marketing landing page for TradeCore — a professional crypto trading SaaS.

== YOUR SCOPE ==

1. DOCKER & DEPLOYMENT
   File: docker-compose.prod.yml
   Services:
   - api: FastAPI app (gunicorn + uvicorn workers)
   - frontend: Nginx serving built React app + proxy to /api
   - postgres: PostgreSQL 15
   - redis: Redis 7
   - scheduler: Same FastAPI codebase, runs only APScheduler (no HTTP server)
   - nginx: Reverse proxy, SSL termination

   File: Dockerfile (backend)
   - Python 3.11-slim base
   - Install dependencies from requirements.txt
   - Non-root user
   - Health check on /health endpoint

   File: frontend/Dockerfile
   - Node 20-alpine for build stage
   - Nginx-alpine for serve stage
   - Build: npm run build → /dist
   - Nginx config: serve /dist, proxy /api/* to backend

   File: nginx.conf
   - Serve frontend on port 80/443
   - Proxy /api to backend:8000
   - Proxy /ws to backend:8000 (WebSocket upgrade headers)
   - Rate limiting: 100 req/min per IP on /api/auth/* endpoints
   - Gzip compression for all text assets

   CI/CD: GitHub Actions workflow (.github/workflows/deploy.yml)
   - On push to main: run tests → build Docker images → push to registry → deploy

2. HEALTH & MONITORING
   File: app/routers/health.py
   GET /health → {status: "ok", db: "ok", redis: "ok", binance_stream: "ok"}
   
   Checks:
   - Database: run simple SELECT 1
   - Redis: PING
   - Binance stream: check last candle timestamp for BTCUSDT — fail if >10min old

3. SECURITY HARDENING
   - Add rate limiting middleware using slowapi:
     /auth/login: 10 req/15min per IP
     /auth/register: 5 req/hour per IP
     /api/*: 300 req/min per user
   - Add request ID header to all responses (X-Request-ID)
   - CORS: allow only FRONTEND_URL, not *
   - Security headers middleware:
     X-Content-Type-Options: nosniff
     X-Frame-Options: DENY
     X-XSS-Protection: 1; mode=block
   - Binance API key encryption:
     Use cryptography.fernet for symmetric encryption
     Key from environment: ENCRYPTION_KEY (32 bytes, base64)
     Encrypt on save, decrypt on use — never return to frontend
   - SQL injection: already prevented by SQLAlchemy ORM, add check
   - Input validation: ensure all Pydantic models have max_length on strings

4. TEST SUITE
   Backend tests (pytest + httpx AsyncClient):
   
   tests/test_auth.py:
   - test_register_success
   - test_register_duplicate_email
   - test_login_success
   - test_login_wrong_password
   - test_protected_route_no_token
   - test_protected_route_valid_token
   - test_token_refresh

   tests/test_radarx.py:
   - test_detector_fires_above_threshold
   - test_detector_does_not_fire_below_threshold
   - test_cooldown_prevents_duplicate_alert
   - test_alert_saved_to_database

   tests/test_riskcalc.py:
   - test_correct_position_size
   - test_leverage_cap_applied
   - test_rr_ratio_below_2_warning
   - test_high_risk_warning

   tests/test_oracle.py:
   - test_score_within_bounds
   - test_strong_bullish_all_modules_agree
   - test_neutral_when_mixed_signals

   Frontend tests (Vitest + React Testing Library):
   tests/NumberDisplay.test.tsx — positive green, negative red
   tests/PercentChange.test.tsx — correct sign and color
   tests/RiskCalcForm.test.tsx — form submits, results appear

5. LANDING PAGE
   File: frontend/src/pages/landing/index.tsx
   Route: / (only when not logged in)

   Sections:
   a. Hero: "Trade with an edge" headline, subtitle about TradeCore,
      two CTAs: "Start for free" (→ /register), "See it live" (→ demo)
      Background: dark with subtle grid animation

   b. Modules showcase: 10 module cards with icons, names, one-line description
      Each card uses the module's accent color

   c. How it works: 3 steps
      1. Connect → link your exchange (read-only)
      2. Monitor → get real-time alerts across all modules  
      3. Decide → Oracle scores every opportunity for you

   d. Pricing: 3 tier cards (Free / Pro $29 / Elite $79)
      Feature comparison list per tier
      "Get started" CTA per tier → /register?plan=pro

   e. Social proof: "Join X traders already using TradeCore"
      3 short testimonial quotes (placeholder text for now)

   f. Footer: Logo, links (Privacy, Terms, Twitter, Discord)

   Style: matches design system exactly. Dark theme, electric blue CTAs.

== ENVIRONMENT VARIABLES TO DOCUMENT ==
Create .env.production.example with all required variables:
DATABASE_URL, REDIS_URL, JWT_SECRET, JWT_ALGORITHM,
STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, STRIPE_PRO_PRICE_ID, STRIPE_ELITE_PRICE_ID,
GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
SENDGRID_API_KEY, TELEGRAM_BOT_TOKEN,
WHALE_ALERT_API_KEY, COINGLASS_API_KEY, COINGECKO_API_KEY,
ENCRYPTION_KEY, FRONTEND_URL, APP_ENV=production

== DELIVERABLES CHECKLIST ==
[ ] docker-compose up -d starts all services successfully
[ ] GET /health returns {status: "ok"} for all checks
[ ] Frontend served at localhost:80 via Nginx
[ ] API accessible at localhost:80/api
[ ] WebSocket works through Nginx proxy
[ ] Rate limiting blocks 11th login attempt from same IP
[ ] All 15+ backend tests pass
[ ] All 3 frontend tests pass
[ ] Security headers present in all API responses
[ ] Fernet encryption working for API key storage
[ ] Landing page renders all 5 sections
[ ] Pricing page links to correct Stripe checkout per plan
[ ] .env.production.example documents all variables with comments
[ ] GitHub Actions workflow file created (can be triggered manually)
```

---

---

## Build sequence summary

```
Week 1-2:    Team 1 (Foundation) ← start here, block everything else
Week 2-4:    Team 2 (Backend Core) + Team 3 (Frontend Core) in parallel
Week 4-6:    Team 4 (Detection) + Team 5 (Analysis) in parallel
Week 6-8:    Team 6 (Intelligence) — depends on 4 and 5
Week 8-10:   Team 7 (Frontend Modules) + Team 8 (Infrastructure) in parallel
Week 10:     Integration testing, bug fixes, launch prep
```

## One rule above all others
Never start a team until the team it depends on has completed its checklist.
Skipping ahead produces broken code that's harder to fix than doing it right.
