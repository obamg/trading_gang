"""slowapi rate limiter, backed by Redis."""
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.rate_limit_storage_url,
    default_limits=[],
    headers_enabled=False,
)
