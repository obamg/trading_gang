# TradeCore — Database Schema
*Version 1.0 — April 2026*

---

## Architecture Overview

```
PostgreSQL (persistent storage)   Redis (in-memory, real-time)
├── Users & Auth                  ├── Live candle buffers
├── User Settings & Watchlists    ├── Current funding rates & OI
├── User Settings & Watchlists    ├── Live liquidation heatmap
├── RadarX Alerts                 ├── User sessions
├── WhaleRadar Events             └── WebSocket routing table
├── Notable Liquidation Events
├── SentimentPulse Snapshots
├── MacroPulse Snapshots
├── GemRadar Alerts
├── Oracle Signals & Outcomes
├── RiskCalc History
├── TradeLog
└── PerformanceCore Aggregates
```

---

## 1. Users & Authentication

### `users`
```sql
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           VARCHAR(255) UNIQUE NOT NULL,
    password_hash   VARCHAR(255),                        -- null if OAuth only
    full_name       VARCHAR(255),
    avatar_url      VARCHAR(500),
    is_active       BOOLEAN DEFAULT TRUE,
    is_verified     BOOLEAN DEFAULT FALSE,
    auth_provider   VARCHAR(50) DEFAULT 'email',         -- 'email', 'google'
    provider_id     VARCHAR(255),                        -- OAuth provider user ID
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
```

### `sessions`
```sql
CREATE TABLE sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash      VARCHAR(255) UNIQUE NOT NULL,
    ip_address      INET,
    user_agent      TEXT,
    expires_at      TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

### `email_verifications`
```sql
CREATE TABLE email_verifications (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  VARCHAR(255) UNIQUE NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
```

### `password_resets`
```sql
CREATE TABLE password_resets (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  VARCHAR(255) UNIQUE NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 2. User Settings & Preferences

### `user_settings`
```sql
CREATE TABLE user_settings (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    -- Notifications
    telegram_chat_id        VARCHAR(100),
    telegram_enabled        BOOLEAN DEFAULT FALSE,
    email_alerts_enabled    BOOLEAN DEFAULT TRUE,
    -- RadarX settings
    radarx_zscore_threshold     NUMERIC(4,2) DEFAULT 3.0,
    radarx_ratio_threshold      NUMERIC(4,2) DEFAULT 4.0,
    radarx_min_volume_usd       NUMERIC(20,2) DEFAULT 10000000,
    radarx_cooldown_minutes     INTEGER DEFAULT 30,
    radarx_timeframe            VARCHAR(10) DEFAULT '5m',
    -- WhaleRadar settings
    whaleradar_min_trade_usd    NUMERIC(20,2) DEFAULT 300000,
    whaleradar_min_onchain_usd  NUMERIC(20,2) DEFAULT 500000,
    -- GemRadar settings
    gemradar_min_mcap_usd       NUMERIC(20,2) DEFAULT 1000000,
    gemradar_max_mcap_usd       NUMERIC(20,2) DEFAULT 100000000,
    gemradar_min_price_change   NUMERIC(5,2) DEFAULT 10.0,
    gemradar_risk_tolerance     VARCHAR(20) DEFAULT 'medium', -- 'low', 'medium', 'high'
    -- Oracle settings
    oracle_min_score            INTEGER DEFAULT 65,
    oracle_min_confluence       INTEGER DEFAULT 4,
    oracle_paper_mode           BOOLEAN DEFAULT TRUE,
    oracle_auto_execute         BOOLEAN DEFAULT FALSE,
    oracle_max_daily_trades     INTEGER DEFAULT 5,
    oracle_max_account_risk_pct NUMERIC(4,2) DEFAULT 2.0,
    oracle_daily_loss_limit_pct NUMERIC(4,2) DEFAULT 5.0,
    -- Oracle weights (must sum to 100)
    weight_macropulse       INTEGER DEFAULT 25,
    weight_whaleradar       INTEGER DEFAULT 20,
    weight_radarx           INTEGER DEFAULT 15,
    weight_liquidmap        INTEGER DEFAULT 15,
    weight_sentimentpulse   INTEGER DEFAULT 15,
    weight_gemradar         INTEGER DEFAULT 10,
    -- General
    default_account_balance_usd NUMERIC(20,2),
    risk_per_trade_pct          NUMERIC(4,2) DEFAULT 1.0,
    timezone                    VARCHAR(50) DEFAULT 'UTC',
    theme                       VARCHAR(20) DEFAULT 'dark',
    created_at                  TIMESTAMPTZ DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ DEFAULT NOW()
);
```

### `watchlists`
```sql
CREATE TABLE watchlists (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        VARCHAR(100) NOT NULL,
    symbols     TEXT[] NOT NULL DEFAULT '{}',            -- ['BTCUSDT', 'ETHUSDT', ...]
    is_default  BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 4. Market Reference Data (Shared)

### `symbols`
```sql
CREATE TABLE symbols (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol              VARCHAR(50) UNIQUE NOT NULL,     -- 'BTCUSDT'
    base_asset          VARCHAR(20) NOT NULL,            -- 'BTC'
    quote_asset         VARCHAR(20) NOT NULL,            -- 'USDT'
    asset_type          VARCHAR(20) NOT NULL,            -- 'spot', 'futures'
    exchange            VARCHAR(50) DEFAULT 'binance',
    is_active           BOOLEAN DEFAULT TRUE,
    market_cap_usd      NUMERIC(30,2),
    avg_daily_volume_usd NUMERIC(30,2),
    last_price          NUMERIC(30,8),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_symbols_active ON symbols(is_active);
CREATE INDEX idx_symbols_type ON symbols(asset_type);
```

---

## 5. RadarX — Volume Spike Alerts

### `radarx_alerts`
```sql
CREATE TABLE radarx_alerts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol              VARCHAR(50) NOT NULL,
    timeframe           VARCHAR(10) NOT NULL DEFAULT '5m',
    z_score             NUMERIC(6,2) NOT NULL,
    ratio               NUMERIC(6,2) NOT NULL,
    candle_volume_usd   NUMERIC(30,2) NOT NULL,          -- volume of the spike candle
    avg_volume_usd      NUMERIC(30,2) NOT NULL,          -- baseline average
    price               NUMERIC(30,8) NOT NULL,
    price_change_pct    NUMERIC(6,2),                    -- % change during spike candle
    volume_24h_usd      NUMERIC(30,2),
    triggered_at        TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_radarx_symbol ON radarx_alerts(symbol);
CREATE INDEX idx_radarx_triggered ON radarx_alerts(triggered_at DESC);
CREATE INDEX idx_radarx_zscore ON radarx_alerts(z_score DESC);
```

---

## 6. WhaleRadar — Whale Activity Events

### `whale_trades`
```sql
CREATE TABLE whale_trades (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol          VARCHAR(50) NOT NULL,
    trade_size_usd  NUMERIC(30,2) NOT NULL,
    side            VARCHAR(10) NOT NULL,                -- 'buy', 'sell'
    price           NUMERIC(30,8) NOT NULL,
    exchange        VARCHAR(50) DEFAULT 'binance',
    is_futures      BOOLEAN DEFAULT TRUE,
    detected_at     TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_whale_trades_symbol ON whale_trades(symbol);
CREATE INDEX idx_whale_trades_detected ON whale_trades(detected_at DESC);
CREATE INDEX idx_whale_trades_size ON whale_trades(trade_size_usd DESC);
```

### `whale_onchain_transfers`
```sql
CREATE TABLE whale_onchain_transfers (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset           VARCHAR(20) NOT NULL,                -- 'BTC', 'ETH', 'USDT'
    amount          NUMERIC(30,8) NOT NULL,
    amount_usd      NUMERIC(30,2) NOT NULL,
    from_address    VARCHAR(100),
    to_address      VARCHAR(100),
    from_label      VARCHAR(100),                        -- 'Binance Hot Wallet', 'Unknown'
    to_label        VARCHAR(100),
    transfer_type   VARCHAR(50),                        -- 'exchange_inflow', 'exchange_outflow', 'whale_to_whale'
    tx_hash         VARCHAR(100) UNIQUE,
    chain           VARCHAR(50) NOT NULL,               -- 'bitcoin', 'ethereum', 'solana'
    detected_at     TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_onchain_asset ON whale_onchain_transfers(asset);
CREATE INDEX idx_onchain_detected ON whale_onchain_transfers(detected_at DESC);
CREATE INDEX idx_onchain_type ON whale_onchain_transfers(transfer_type);
```

### `oi_surge_events`
```sql
CREATE TABLE oi_surge_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol          VARCHAR(50) NOT NULL,
    oi_before_usd   NUMERIC(30,2) NOT NULL,
    oi_after_usd    NUMERIC(30,2) NOT NULL,
    oi_change_pct   NUMERIC(6,2) NOT NULL,
    price           NUMERIC(30,8) NOT NULL,
    price_change_pct NUMERIC(6,2),
    direction       VARCHAR(20),                        -- 'long_heavy', 'short_heavy', 'neutral'
    detected_at     TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_oi_symbol ON oi_surge_events(symbol);
CREATE INDEX idx_oi_detected ON oi_surge_events(detected_at DESC);
```

---

## 7. LiquidMap — Notable Liquidation Events

*Note: The live heatmap lives in Redis only. Only significant single liquidations are persisted.*

### `liquidation_events`
```sql
CREATE TABLE liquidation_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol      VARCHAR(50) NOT NULL,
    side        VARCHAR(10) NOT NULL,                    -- 'long', 'short'
    size_usd    NUMERIC(30,2) NOT NULL,
    price       NUMERIC(30,8) NOT NULL,
    is_cascade  BOOLEAN DEFAULT FALSE,                  -- TRUE if part of liquidation cascade
    detected_at TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_liq_symbol ON liquidation_events(symbol);
CREATE INDEX idx_liq_detected ON liquidation_events(detected_at DESC);
CREATE INDEX idx_liq_size ON liquidation_events(size_usd DESC);
```

---

## 8. SentimentPulse — Hourly Snapshots

### `sentiment_snapshots`
```sql
CREATE TABLE sentiment_snapshots (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol              VARCHAR(50) NOT NULL,
    funding_rate        NUMERIC(10,6),                  -- e.g. 0.0001 = 0.01%
    long_ratio          NUMERIC(5,2),                   -- % accounts long
    short_ratio         NUMERIC(5,2),                   -- % accounts short
    open_interest_usd   NUMERIC(30,2),
    snapshot_at         TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_sentiment_symbol_time ON sentiment_snapshots(symbol, snapshot_at);
CREATE INDEX idx_sentiment_snapshot ON sentiment_snapshots(snapshot_at DESC);
```

### `market_sentiment_snapshots`
```sql
CREATE TABLE market_sentiment_snapshots (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fear_greed_index    INTEGER,                        -- 0-100
    fear_greed_label    VARCHAR(50),                    -- 'Extreme Fear', 'Greed', etc.
    btc_dominance_pct   NUMERIC(5,2),
    total_mcap_usd      NUMERIC(30,2),
    snapshot_at         TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_market_sentiment_time ON market_sentiment_snapshots(snapshot_at);
```

---

## 9. MacroPulse — Daily Snapshots & Events

### `macro_snapshots`
```sql
CREATE TABLE macro_snapshots (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dxy                     NUMERIC(8,3),               -- US Dollar Index
    us10y                   NUMERIC(6,3),               -- 10Y Treasury yield %
    us2y                    NUMERIC(6,3),               -- 2Y Treasury yield %
    yield_spread            NUMERIC(6,3),               -- 10Y - 2Y spread
    vix                     NUMERIC(6,2),               -- Fear index
    sp500                   NUMERIC(10,2),
    nasdaq                  NUMERIC(10,2),
    gold_usd                NUMERIC(10,2),
    btc_etf_flows_usd       NUMERIC(30,2),              -- Net daily ETF flows
    stablecoin_mcap_usd     NUMERIC(30,2),
    fed_rate_pct            NUMERIC(5,2),               -- Current Fed funds rate
    macro_score             INTEGER,                    -- -100 to +100 composite score
    snapshot_at             TIMESTAMPTZ NOT NULL,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_macro_snapshot_time ON macro_snapshots(snapshot_at);
```

### `economic_events`
```sql
CREATE TABLE economic_events (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                    VARCHAR(200) NOT NULL,       -- 'US CPI MoM', 'Non-Farm Payrolls'
    category                VARCHAR(100),               -- 'inflation', 'employment', 'growth'
    country                 VARCHAR(10) DEFAULT 'US',
    impact                  VARCHAR(20) NOT NULL,        -- 'high', 'medium', 'low'
    scheduled_at            TIMESTAMPTZ NOT NULL,
    forecast_value          VARCHAR(50),                -- e.g. '3.2%'
    actual_value            VARCHAR(50),                -- filled after release
    previous_value          VARCHAR(50),
    surprise_direction      VARCHAR(20),                -- 'beat', 'miss', 'inline'
    btc_reaction_1h_pct     NUMERIC(6,2),               -- BTC % move 1h after release
    btc_reaction_4h_pct     NUMERIC(6,2),               -- BTC % move 4h after release
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_eco_scheduled ON economic_events(scheduled_at);
CREATE INDEX idx_eco_impact ON economic_events(impact);
```

---

## 10. GemRadar — Small-Cap Alerts

### `gemradar_alerts`
```sql
CREATE TABLE gemradar_alerts (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol                  VARCHAR(50) NOT NULL,
    name                    VARCHAR(100),
    contract_address        VARCHAR(100),
    chain                   VARCHAR(50),                -- 'solana', 'ethereum', 'bsc'
    dex                     VARCHAR(50),                -- 'raydium', 'uniswap', 'pancakeswap'
    is_cex_listed           BOOLEAN DEFAULT FALSE,
    cex_name                VARCHAR(50),
    price_usd               NUMERIC(30,10),
    price_change_pct        NUMERIC(8,2),               -- % change triggering alert
    price_change_period_min INTEGER,                    -- over how many minutes
    volume_usd_current      NUMERIC(30,2),
    volume_usd_avg          NUMERIC(30,2),
    volume_mcap_ratio       NUMERIC(10,2),
    market_cap_usd          NUMERIC(30,2),
    social_velocity         NUMERIC(8,2),               -- social mention multiplier
    -- Risk assessment
    risk_score              VARCHAR(20) NOT NULL,        -- 'low', 'medium', 'high', 'extreme'
    risk_score_numeric      INTEGER,                    -- 0-100
    is_contract_verified    BOOLEAN,
    is_liquidity_locked     BOOLEAN,
    has_mint_function       BOOLEAN,
    top10_wallet_pct        NUMERIC(5,2),               -- % supply held by top 10
    contract_age_hours      INTEGER,
    risk_flags              JSONB DEFAULT '[]',         -- array of specific risk warnings
    detected_at             TIMESTAMPTZ NOT NULL,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_gemradar_detected ON gemradar_alerts(detected_at DESC);
CREATE INDEX idx_gemradar_risk ON gemradar_alerts(risk_score);
CREATE INDEX idx_gemradar_chain ON gemradar_alerts(chain);
```

---

## 11. Oracle — Signals & Outcomes

### `oracle_signals`
```sql
CREATE TABLE oracle_signals (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol              VARCHAR(50) NOT NULL,
    asset_type          VARCHAR(20) DEFAULT 'futures',  -- 'spot', 'futures'
    score               INTEGER NOT NULL,               -- -100 to +100
    recommendation      VARCHAR(30) NOT NULL,           -- 'strong_long', 'long', 'neutral', 'short', 'strong_short'
    confidence          VARCHAR(20) NOT NULL,           -- 'high', 'medium', 'low'
    confluence_count    INTEGER NOT NULL,               -- how many modules agree
    -- Module signal breakdown
    signals_breakdown   JSONB NOT NULL,                 -- {radarx: {score, direction, intensity}, ...}
    -- Trade parameters
    entry_price         NUMERIC(30,8),
    stop_loss           NUMERIC(30,8),
    take_profit         NUMERIC(30,8),
    rr_ratio            NUMERIC(5,2),
    suggested_size      NUMERIC(30,8),
    suggested_leverage  NUMERIC(5,2),
    -- Macro context at signal time
    macro_score         INTEGER,
    vix_at_signal       NUMERIC(6,2),
    dxy_at_signal       NUMERIC(8,3),
    -- Signal metadata
    is_paper            BOOLEAN DEFAULT TRUE,
    timeframe           VARCHAR(10) DEFAULT '5m',
    signal_at           TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_oracle_symbol ON oracle_signals(symbol);
CREATE INDEX idx_oracle_signal_at ON oracle_signals(signal_at DESC);
CREATE INDEX idx_oracle_score ON oracle_signals(score DESC);
CREATE INDEX idx_oracle_recommendation ON oracle_signals(recommendation);
```

### `oracle_outcomes`
```sql
CREATE TABLE oracle_outcomes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id       UUID UNIQUE NOT NULL REFERENCES oracle_signals(id) ON DELETE CASCADE,
    price_at_signal NUMERIC(30,8) NOT NULL,
    price_15m       NUMERIC(30,8),
    price_1h        NUMERIC(30,8),
    price_4h        NUMERIC(30,8),
    price_24h       NUMERIC(30,8),
    pnl_15m_pct     NUMERIC(8,4),
    pnl_1h_pct      NUMERIC(8,4),
    pnl_4h_pct      NUMERIC(8,4),
    pnl_24h_pct     NUMERIC(8,4),
    was_correct_1h  BOOLEAN,                            -- did it move in predicted direction?
    was_correct_4h  BOOLEAN,
    measured_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_oracle_outcomes_signal ON oracle_outcomes(signal_id);
```

---

## 12. RiskCalc — Calculation History

### `riskcalc_history`
```sql
CREATE TABLE riskcalc_history (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol                  VARCHAR(50),
    account_balance_usd     NUMERIC(20,2) NOT NULL,
    risk_pct                NUMERIC(5,2) NOT NULL,
    risk_amount_usd         NUMERIC(20,2) NOT NULL,
    entry_price             NUMERIC(30,8) NOT NULL,
    stop_loss_price         NUMERIC(30,8) NOT NULL,
    take_profit_price       NUMERIC(30,8),
    stop_distance_pct       NUMERIC(6,2) NOT NULL,
    position_size           NUMERIC(20,8) NOT NULL,
    position_size_usd       NUMERIC(20,2) NOT NULL,
    leverage                NUMERIC(5,2),
    liquidation_price       NUMERIC(30,8),
    max_loss_usd            NUMERIC(20,2) NOT NULL,
    potential_profit_usd    NUMERIC(20,2),
    rr_ratio                NUMERIC(5,2),
    warnings                JSONB DEFAULT '[]',         -- risk warnings flagged
    oracle_signal_id        UUID REFERENCES oracle_signals(id),
    calculated_at           TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_riskcalc_user ON riskcalc_history(user_id);
CREATE INDEX idx_riskcalc_calculated ON riskcalc_history(calculated_at DESC);
```

---

## 13. TradeLog — Trade Journal

### `trades`
```sql
CREATE TABLE trades (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol              VARCHAR(50) NOT NULL,
    asset_type          VARCHAR(20) NOT NULL,           -- 'spot', 'futures'
    side                VARCHAR(10) NOT NULL,           -- 'long', 'short'
    status              VARCHAR(20) NOT NULL DEFAULT 'open', -- 'open', 'closed', 'cancelled'
    is_paper            BOOLEAN DEFAULT FALSE,
    -- Entry
    entry_price         NUMERIC(30,8) NOT NULL,
    entry_at            TIMESTAMPTZ NOT NULL,
    -- Exit
    exit_price          NUMERIC(30,8),
    exit_at             TIMESTAMPTZ,
    -- Size & Risk
    size                NUMERIC(20,8) NOT NULL,
    size_usd            NUMERIC(20,2),
    leverage            NUMERIC(5,2) DEFAULT 1,
    stop_loss_price     NUMERIC(30,8),
    take_profit_price   NUMERIC(30,8),
    -- P&L
    pnl_usd             NUMERIC(20,2),
    pnl_pct             NUMERIC(8,4),
    fees_usd            NUMERIC(20,2),
    net_pnl_usd         NUMERIC(20,2),
    r_multiple          NUMERIC(6,2),                  -- actual R achieved
    -- Context
    setup_name          VARCHAR(100),                  -- 'breakout', 'retest', 'whale_follow'
    notes               TEXT,
    emotion             VARCHAR(50),                   -- 'calm', 'fomo', 'revenge', 'confident'
    followed_oracle     BOOLEAN DEFAULT FALSE,
    oracle_signal_id    UUID REFERENCES oracle_signals(id),
    -- Exchange sync
    exchange_trade_id   VARCHAR(100),
    exchange            VARCHAR(50),
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_trades_user ON trades(user_id);
CREATE INDEX idx_trades_status ON trades(status);
CREATE INDEX idx_trades_entry ON trades(entry_at DESC);
CREATE INDEX idx_trades_symbol ON trades(symbol);
CREATE INDEX idx_trades_paper ON trades(is_paper);
```

### `trade_tags`
```sql
CREATE TABLE trade_tags (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trade_id    UUID NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
    tag         VARCHAR(50) NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_trade_tags_trade ON trade_tags(trade_id);
CREATE INDEX idx_trade_tags_tag ON trade_tags(tag);
```

---

## 14. PerformanceCore — Aggregated Analytics

### `performance_snapshots`
```sql
CREATE TABLE performance_snapshots (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    period                  VARCHAR(20) NOT NULL,        -- 'daily', 'weekly', 'monthly', 'all_time'
    period_start            TIMESTAMPTZ NOT NULL,
    period_end              TIMESTAMPTZ NOT NULL,
    is_paper                BOOLEAN DEFAULT FALSE,
    -- Trade stats
    total_trades            INTEGER DEFAULT 0,
    winning_trades          INTEGER DEFAULT 0,
    losing_trades           INTEGER DEFAULT 0,
    breakeven_trades        INTEGER DEFAULT 0,
    win_rate                NUMERIC(5,2),               -- %
    avg_win_pct             NUMERIC(8,4),
    avg_loss_pct            NUMERIC(8,4),
    avg_rr_achieved         NUMERIC(5,2),
    expectancy              NUMERIC(8,4),               -- avg $ per trade
    profit_factor           NUMERIC(6,2),               -- gross profit / gross loss
    -- P&L
    total_pnl_usd           NUMERIC(20,2),
    total_fees_usd          NUMERIC(20,2),
    net_pnl_usd             NUMERIC(20,2),
    -- Risk
    max_drawdown_pct        NUMERIC(6,2),
    max_drawdown_usd        NUMERIC(20,2),
    max_consecutive_losses  INTEGER,
    -- Best/worst
    best_trade_pnl_usd      NUMERIC(20,2),
    worst_trade_pnl_usd     NUMERIC(20,2),
    best_setup              VARCHAR(100),
    -- Account
    starting_balance_usd    NUMERIC(20,2),
    ending_balance_usd      NUMERIC(20,2),
    computed_at             TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_perf_user_period ON performance_snapshots(user_id, period);
CREATE INDEX idx_perf_computed ON performance_snapshots(computed_at DESC);
```

### `signal_performance`
```sql
CREATE TABLE signal_performance (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    module              VARCHAR(50) NOT NULL,           -- 'radarx', 'whaleradar', 'oracle', etc.
    symbol              VARCHAR(50),                    -- null = all symbols aggregate
    total_signals       INTEGER DEFAULT 0,
    correct_1h          INTEGER DEFAULT 0,
    correct_4h          INTEGER DEFAULT 0,
    accuracy_1h_pct     NUMERIC(5,2),
    accuracy_4h_pct     NUMERIC(5,2),
    avg_move_1h_pct     NUMERIC(6,2),
    avg_move_4h_pct     NUMERIC(6,2),
    computed_at         TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_signal_perf_module_symbol ON signal_performance(module, symbol, computed_at);
```

---

## 15. Alert Delivery & Cooldowns

### `user_alert_deliveries`
```sql
CREATE TABLE user_alert_deliveries (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    module          VARCHAR(50) NOT NULL,               -- 'radarx', 'whaleradar', etc.
    alert_ref_id    UUID NOT NULL,                      -- FK to the relevant alert table
    alert_ref_table VARCHAR(100) NOT NULL,              -- table name
    delivered_via   VARCHAR(50) NOT NULL,               -- 'telegram', 'email', 'web'
    delivered_at    TIMESTAMPTZ DEFAULT NOW(),
    was_read        BOOLEAN DEFAULT FALSE,
    read_at         TIMESTAMPTZ
);

CREATE INDEX idx_delivery_user ON user_alert_deliveries(user_id);
CREATE INDEX idx_delivery_module ON user_alert_deliveries(module);
CREATE INDEX idx_delivery_delivered ON user_alert_deliveries(delivered_at DESC);
```

### `alert_cooldowns`
```sql
CREATE TABLE alert_cooldowns (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol          VARCHAR(50) NOT NULL,
    module          VARCHAR(50) NOT NULL,               -- 'radarx', 'whaleradar', 'gemradar'
    last_alert_at   TIMESTAMPTZ NOT NULL,
    cooldown_until  TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_cooldown_symbol_module ON alert_cooldowns(symbol, module);
CREATE INDEX idx_cooldown_until ON alert_cooldowns(cooldown_until);
```

---

## Summary

| Category | Tables | Storage Type |
|----------|--------|-------------|
| Users & Auth | 4 tables | PostgreSQL |
| Settings | 2 tables | PostgreSQL |
| Market Reference | 1 table | PostgreSQL |
| RadarX | 1 table | PostgreSQL |
| WhaleRadar | 3 tables | PostgreSQL |
| LiquidMap | 1 table | PostgreSQL (events only) |
| SentimentPulse | 2 tables | PostgreSQL (hourly snapshots) |
| MacroPulse | 2 tables | PostgreSQL (daily snapshots) |
| GemRadar | 1 table | PostgreSQL |
| Oracle | 2 tables | PostgreSQL |
| RiskCalc | 1 table | PostgreSQL |
| TradeLog | 2 tables | PostgreSQL |
| PerformanceCore | 2 tables | PostgreSQL |
| Alert Delivery | 2 tables | PostgreSQL |
| **Total** | **29 tables** | |

### Live data (Redis only — never stored in PostgreSQL)
- Rolling 50-candle buffers per symbol
- Live liquidation heatmap
- Current funding rates & OI per symbol  
- Active WebSocket connections
- User session tokens
- Rate limiting counters
