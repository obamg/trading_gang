"""Application settings loaded from environment variables."""
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # Core
    app_env: str = "development"
    app_secret_key: str = "change-me"
    frontend_url: str = "http://localhost:5173"

    # Database
    database_url: str = "postgresql+asyncpg://tradecore:tradecore@localhost:5432/tradecore"
    database_url_sync: str = "postgresql+psycopg2://tradecore:tradecore@localhost:5432/tradecore"

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    rate_limit_storage_url: str = "redis://localhost:6379/1"

    # JWT
    jwt_secret: str = "change-me-generate-with-openssl-rand-hex-32"
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_minutes: int = 60 * 24 * 7
    jwt_refresh_ttl_days: int = 30

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"

    # Email
    # Provider: "sendgrid" | "smtp" | "" (empty = dev stub, just logs).
    # When empty but sendgrid_api_key is set, falls back to "sendgrid" for
    # backwards compatibility with older deployments.
    email_provider: str = ""
    email_from_address: str = "no-reply@tradecore.local"
    email_from_name: str = "TradeCore"
    # SendGrid
    sendgrid_api_key: str = ""
    # Generic SMTP (works with Gmail, Fastmail, Mailgun SMTP, self-hosted Postfix, etc.)
    # Gmail preset:
    #   SMTP_HOST=smtp.gmail.com  SMTP_PORT=587  SMTP_USE_TLS=true
    #   SMTP_USER=<your-gmail-address>
    #   SMTP_PASSWORD=<16-char App Password>   # NOT your real Google password
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True      # STARTTLS on non-465 ports; ignored when port=465 (implicit SMTPS)
    smtp_timeout_seconds: int = 10

    # Binance
    binance_base_url: str = "wss://fstream.binance.com"
    binance_rest_url: str = "https://fapi.binance.com"
    binance_min_quote_volume_usd: float = 10_000_000.0
    binance_symbol_refresh_minutes: int = 60
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_streams_enabled: bool = False  # opt-in via env for local dev

    # Market data source switch — pick the WS provider used for kline/trade/
    # bookticker/liquidation feeds. Some hosting regions are geo-blocked by
    # Binance's WS edge (TCP accepts, no data sent). Bybit is unrestricted in
    # most regions and serves the same USDT-perp universe.
    # Values: "bybit" | "binance" | "none"
    market_data_source: str = "bybit"
    bybit_base_url: str = "wss://stream.bybit.com/v5/public/linear"
    bybit_rest_url: str = "https://api.bybit.com"

    # Telegram
    telegram_bot_token: str = ""
    telegram_bot_enabled: bool = False

    # Symmetric encryption (Fernet) — used for stored exchange API keys.
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    encryption_key: str = ""

    # Detection modules
    whale_alert_api_key: str = ""
    scheduler_enabled: bool = True
    min_trade_usd: float = 300_000.0
    radarx_zscore_threshold: float = 3.0
    radarx_ratio_threshold: float = 4.0
    min_volume_24h_usd: float = 10_000_000.0

    # Analysis modules (Team 5)
    coingecko_api_key: str = ""
    trading_economics_api_key: str = ""

    # WalletWatch — DEX swap tracking for labeled smart-money wallets.
    walletwatch_enabled: bool = False
    walletwatch_min_usd: float = 25_000.0
    alchemy_api_key: str = ""
    helius_api_key: str = ""
    # One Etherscan V2 key covers BSC, Arbitrum, Base, Polygon, Optimism, etc.
    # via the chainid parameter — no per-explorer signup needed.
    etherscan_api_key: str = ""

    # WalletWatch discovery (Layer 2) — PnL-based wallet scoring + leaderboard.
    discovery_enabled: bool = False

    # Auto-promote: when enabled, the scheduler promotes WalletPnlScore rows
    # that clear ALL four gates (score, realized PnL, win rate, token count)
    # straight into whale_entities so the WalletWatch detector starts alerting
    # on their swaps. Off by default — turn on after watching the discovery
    # leaderboard for a few cycles. Thresholds are conservative on purpose: a
    # false positive pollutes the alert stream with a junk wallet, and one
    # bad promotion costs more than a missed good one.
    discovery_auto_promote_enabled: bool = False
    discovery_auto_promote_min_score: float = 250_000.0
    discovery_auto_promote_min_realized_usd: float = 50_000.0
    discovery_auto_promote_min_win_rate: float = 0.7
    discovery_auto_promote_min_token_count: int = 5
    discovery_auto_promote_max_per_tick: int = 5

    # ListingWatch — detect new listings on Bybit/Binance/OKX (perp + spot)
    # and run a 4h post-listing signal watcher.
    listingwatch_enabled: bool = False

    # Awakening — detect mid-cap perps whose 24h turnover spikes off a
    # 7-day baseline (catches assets like ZEC before they cross the
    # binance_min_quote_volume_usd universe gate).
    awakening_enabled: bool = False
    awakening_ratio_threshold: float = 3.0
    awakening_min_turnover_usd: float = 2_000_000.0

    # WaveWatch — continuous surveillance of Bybit Innovation Zone assets.
    # Fires a "wave incoming" alert when an asset shows prior accumulation
    # (volume baseline rising + range compression + buy-side bias) AND a
    # fresh volume + range break confirms the move is starting.
    wavewatch_enabled: bool = False
    wavewatch_score_threshold: float = 0.6           # 0..1, pre-wave readiness gate
    wavewatch_score_dwell_minutes: int = 15          # score must hold above threshold this long
    wavewatch_max_alerts_per_hour: int = 5           # global cap across all symbols
    wavewatch_symbol_cooldown_hours: int = 2         # per-symbol re-alert lockout

    # wave_active — cascade/squeeze alerts (different thesis from wave_incoming:
    # catches the move while it's flushing, not the coil before it). Fires when
    # the latest 5m bar moves ≥ pct AND vol ≥ ratio× 4h median AND funding is
    # one-sided against the candle direction (green vs negative funding =
    # short squeeze; red vs positive funding = long flush).
    wavewatch_active_pct_threshold: float = 0.03        # 3% in one 5m bar
    wavewatch_active_vol_ratio: float = 4.0             # 4× the 4h median volume
    wavewatch_active_funding_extreme: float = 0.001     # 0.1% — funding must be at least this far from neutral
    wavewatch_active_cooldown_minutes: int = 30         # per-symbol; shorter than incoming because cascades restart
    wavewatch_active_max_per_hour: int = 10             # separate cap from wave_incoming

    # WaveBot — paper-trading bot driven by wave_active alerts. Off by default;
    # flip BOT_ENABLED once you've watched the listener log for a session and
    # trust the veto behaviour. v1 is paper-only — no order routing.
    bot_enabled: bool = False
    bot_paper_equity_initial: float = 10_000.0
    bot_position_size_pct: float = 0.05          # hard cap on notional per trade (5% of equity)
    bot_risk_per_trade_pct: float = 0.0025       # risk-normalized sizing: 0.25% equity at risk per
                                                 # trade. notional = min(risk/stop_dist, cap above).
                                                 # 0 → fall back to fixed-notional (cap) sizing.
    bot_max_concurrent: int = 5                  # → 25% max exposure at full
    bot_take_profit_r_multiple: float = 2.0      # TP at 2R, simple to evaluate
    bot_stop_buffer_pct: float = 0.0005          # 0.05% past the candle extreme
    bot_per_symbol_cooldown_minutes: int = 120   # no re-entry for 2h after close
    bot_max_hold_hours: int = 24                 # force-close positions open longer than this
                                                 # (frees orphans whose candle stream went stale). 0 disables.
    bot_fee_pct_per_side: float = 0.0006         # taker fee per side (Bybit ~0.06%); charged on entry+exit
    bot_slippage_pct: float = 0.0005             # adverse slippage on market exits (stop/manual/timeout)
    # Asset selection — code defaults OFF (no behaviour change); the active prod
    # policy is set via compose env. See bot/vetoes.py.
    bot_perp_only: bool = False                  # skip non-perp (spot) signals — spot can't be shorted live
    bot_symbol_blocklist: str = ""               # comma-separated symbols to never trade (case-insensitive)
    bot_min_turnover_usd: float = 0.0            # skip if recent rolling turnover < this (0 disables). Needs calibration.
    bot_daily_drawdown_cap_pct: float = 0.05     # kill switch at -5% from daily anchor
    bot_oracle_veto_long_below: float = -30.0    # skip longs when oracle is bearish
    bot_oracle_veto_short_above: float = 30.0    # skip shorts when oracle is bullish
    bot_news_veto_window_minutes: int = 30       # skip if high-impact news in last N min
    bot_entry_delay_seconds: int = 60            # settle delay before pulling the entry fill
    bot_monitor_tick_seconds: int = 30           # how often to check open positions for stop/TP
    bot_live_enabled: bool = False               # v2: when true, route fills to a real exchange
    bot_live_leverage: int = 5                   # v2: isolated leverage on Bybit perps

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.is_production:
        if s.jwt_secret.startswith("change-me"):
            raise RuntimeError("JWT_SECRET must be set in production")
        if s.app_secret_key.startswith("change-me"):
            raise RuntimeError("APP_SECRET_KEY must be set in production")
        if not s.encryption_key:
            raise RuntimeError("ENCRYPTION_KEY must be set in production (Fernet key)")
    return s


settings = get_settings()
