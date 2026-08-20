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
    santiment_api_key: str = ""
    # Santiment plan time restriction. SANAPI FREE only serves data older than
    # ~30d (and newer than 1y) and rejects the whole query if `to` is past that
    # ceiling — so the collector's window must end this many days back. Set to 0
    # on a paid plan to collect up to today.
    chainpulse_lag_days: int = 31

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

    # MajorsBot — independent paper bot on a FIXED majors universe (Bybit linear
    # perps, self-computed 1h-bar signals; no alerts). Strategy parameters
    # (thresholds, retrace depth, trail distances) are frozen in
    # app/modules/majorsbot/strategies.py to mirror the 12-month bake-off —
    # only operational knobs live here. Defaults keep it off.
    majorsbot_enabled: bool = False
    majorsbot_symbols: str = (
        "BTCUSDT,ETHUSDT,XRPUSDT,BNBUSDT,SOLUSDT,"
        "DOGEUSDT,ADAUSDT,TRXUSDT,LINKUSDT,AVAXUSDT"
    )
    majorsbot_paper_equity_initial: float = 10_000.0
    majorsbot_risk_per_trade_pct: float = 0.0025   # equity fraction at risk per trade
    majorsbot_position_size_pct: float = 0.05      # notional cap per trade (fraction of equity)
    majorsbot_max_concurrent: int = 6              # open positions across both strategies
    majorsbot_volevent_enabled: bool = True        # F4-A: vol-event momentum retrace
    majorsbot_fundingfade_enabled: bool = True     # F1-B: funding-extreme fade (99th pctile)
    # newsevent: two-leg news + volume confirmation. Off by default — it has
    # no backtest behind it, unlike the two above.
    majorsbot_newsevent_enabled: bool = False
    # Notional cap as a MULTIPLE of equity, i.e. effective leverage: 1.0 = 1x,
    # 20.0 = 20x. Separate from majorsbot_position_size_pct so newsevent can be
    # sized without touching volevent's live forward test. Leverage is still an
    # *outcome* of risk% / stop-distance — this is the ceiling, not the target.
    majorsbot_newsevent_position_size_pct: float = 1.0
    majorsbot_newsevent_risk_per_trade_pct: float = 0.0025
    majorsbot_newsevent_max_concurrent: int = 3
    # False = no protective stop; the position runs to the trail, the max-hold
    # cap, or LIQUIDATION. Sizing then comes purely from the notional cap,
    # since there is no stop distance to normalise risk against.
    majorsbot_newsevent_stop_enabled: bool = False
    majorsbot_maker_fee_pct: float = 0.0002        # limit entries + limit partial TPs
    majorsbot_taker_fee_pct: float = 0.0006        # market entries + market-style exits
    majorsbot_slippage_pct: float = 0.0002         # adverse slip on market-style exits only
    majorsbot_max_hold_hours: int = 168            # safety force-close. 0 disables.

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
