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
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 7

    # Stripe
    stripe_secret_key: str = "sk_test_placeholder"
    stripe_webhook_secret: str = "whsec_placeholder"
    stripe_pro_price_id_monthly: str = "price_pro_monthly_placeholder"
    stripe_pro_price_id_yearly: str = "price_pro_yearly_placeholder"
    stripe_elite_price_id_monthly: str = "price_elite_monthly_placeholder"
    stripe_elite_price_id_yearly: str = "price_elite_yearly_placeholder"

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"

    # Email
    sendgrid_api_key: str = ""
    email_from_address: str = "no-reply@tradecore.local"
    email_from_name: str = "TradeCore"

    # Binance
    binance_base_url: str = "wss://fstream.binance.com"
    binance_rest_url: str = "https://fapi.binance.com"
    binance_min_quote_volume_usd: float = 10_000_000.0
    binance_symbol_refresh_minutes: int = 60
    binance_streams_enabled: bool = False  # opt-in via env for local dev

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
    coinglass_api_key: str = ""
    coingecko_api_key: str = ""
    trading_economics_api_key: str = ""

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
