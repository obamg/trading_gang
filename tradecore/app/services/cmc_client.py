"""CoinMarketCap Pro API — one shared, soft-failing, credit-aware client.

Every CMC call in the codebase goes through :func:`get`. The point is not
convenience, it is that the failure handling lives in exactly one place.

Three failure modes are handled distinctly, because they want opposite
responses:

- **429 rate limited** → trip a global circuit breaker for
  ``BREAKER_SECONDS``. The quota is per *key*, so a different endpoint would
  fail identically; retrying is how you turn one 429 into a spiral. This is
  the walletwatch CoinGecko lesson (13,955 failed calls in 24h against a
  30/min quota) applied before it can happen again — see
  ``walletwatch/pricing.py``.
- **403 not entitled** → the plan does not include this endpoint. Retrying
  costs credits and can never succeed, so the path is marked dead for
  ``NOT_ENTITLED_TTL`` and skipped locally. As of writing it is UNVERIFIED
  which endpoints the free Basic plan covers; a 403 is how we find out, and
  ``cmc_not_entitled`` in the logs is the answer.
- **Anything else** (timeout, 5xx, bad JSON) → soft None. Callers fall back
  to their previous source.

Credit accounting: CMC returns ``status.credit_count`` on every response. We
accumulate it into a per-UTC-month Redis counter so usage is observable
*before* the monthly limit bites, rather than discovered as a wall of 429s.
``/v1/key/info`` is free and returns the authoritative figure; this counter
is the cheap continuous estimate between those calls.

No key configured → every call returns None without touching the network.
That is the default state, and it must stay boring: the modules that use
this all keep their previous source as a fallback.

Redis keys (see redis_service conventions):
  cmc:breaker                 string "1", TTL 300s — 429 lockout, all paths
  cmc:not_entitled:{path}     string "1", TTL 24h — 401/403 per endpoint
  cmc:credits:{YYYYMM}        int counter, TTL 35d — estimated credits spent
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from app.config import settings as app_settings
from app.logging_config import log
from app.services import redis_service

BASE_URL = "https://pro-api.coinmarketcap.com"

# --- resilience knobs -----------------------------------------------------
BREAKER_KEY = "cmc:breaker"
BREAKER_SECONDS = 300
NOT_ENTITLED_KEY = "cmc:not_entitled:{path}"
NOT_ENTITLED_TTL = 24 * 3600       # re-probe daily; plans do get upgraded
CREDITS_KEY = "cmc:credits:{month}"
CREDITS_TTL = 35 * 24 * 3600       # outlives the month it counts
DEFAULT_TIMEOUT = 15.0

# Free Basic allowance. Only used to log a proportion — never to block a
# call, because the real limit lives on CMC's side and this is an estimate.
BASIC_MONTHLY_CREDITS = 15_000
CREDIT_WARN_FRACTION = 0.8


def api_key() -> str:
    return (getattr(app_settings, "cmc_api_key", "") or "").strip()


def enabled() -> bool:
    return bool(api_key())


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m")


def _decode(v):
    return v.decode() if isinstance(v, bytes) else v


async def _record_credits(payload: dict) -> None:
    """Accumulate this response's credit cost into the monthly counter."""
    try:
        cost = int(((payload.get("status") or {}).get("credit_count")) or 0)
    except (TypeError, ValueError):
        return
    if cost <= 0:
        return
    try:
        r = redis_service.get_redis()
        key = CREDITS_KEY.format(month=_month())
        used = int(await r.incrby(key, cost))
        await r.expire(key, CREDITS_TTL)
        # Log only on the crossing, not on every call above the line.
        threshold = int(BASIC_MONTHLY_CREDITS * CREDIT_WARN_FRACTION)
        if used - cost < threshold <= used:
            log.warning(
                "cmc_credit_budget_high",
                used=used, budget=BASIC_MONTHLY_CREDITS, month=_month(),
            )
    except Exception as e:  # counter is telemetry; never fail a call for it
        log.debug("cmc_credit_record_failed", err=str(e))


async def get(
    path: str,
    params: dict | None = None,
    *,
    cache_key: str | None = None,
    cache_ttl: int | None = None,
) -> dict | list | None:
    """GET one CMC endpoint. Returns the ``data`` payload, or None.

    ``cache_key``/``cache_ttl`` add a Redis read-through cache around the
    parsed ``data`` object — use it for anything polled more often than it
    changes. Failures are NOT cached here: the breaker already covers the
    stampede case, and a short-lived 5xx should not blank a caller for an
    hour.
    """
    if not enabled():
        return None

    try:
        r = redis_service.get_redis()
    except Exception:
        r = None

    if r is not None and cache_key:
        try:
            cached = await r.get(cache_key)
            if cached is not None:
                return json.loads(_decode(cached))
        except Exception:
            pass

    if r is not None:
        try:
            if await r.get(BREAKER_KEY):
                return None
            if await r.get(NOT_ENTITLED_KEY.format(path=path)):
                return None
        except Exception:
            pass

    headers = {
        "X-CMC_PRO_API_KEY": api_key(),
        "Accept": "application/json",
    }
    payload: dict = {}
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                f"{BASE_URL}{path}", params=params or {}, headers=headers
            )
            # Parse before raising: CMC puts the useful error text in the body.
            try:
                parsed = resp.json()
                payload = parsed if isinstance(parsed, dict) else {}
            except ValueError:
                payload = {}
            resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        detail = ((payload.get("status") or {}).get("error_message")) or ""
        if status_code == 429:
            if r is not None:
                try:
                    await r.set(BREAKER_KEY, "1", ex=BREAKER_SECONDS)
                except Exception:
                    pass
            log.warning(
                "cmc_rate_limited", path=path, breaker_seconds=BREAKER_SECONDS,
                detail=detail,
            )
        elif status_code in (401, 403):
            if r is not None:
                try:
                    await r.set(
                        NOT_ENTITLED_KEY.format(path=path), "1", ex=NOT_ENTITLED_TTL
                    )
                except Exception:
                    pass
            # The signal that answers "does Basic cover this endpoint?".
            log.warning(
                "cmc_not_entitled", path=path, status=status_code, detail=detail,
                retry_after_s=NOT_ENTITLED_TTL,
            )
        else:
            log.warning("cmc_http_error", path=path, status=status_code, detail=detail)
        return None
    except (httpx.HTTPError, ValueError) as e:
        log.warning("cmc_fetch_failed", path=path, err=str(e))
        return None

    await _record_credits(payload)

    data = payload.get("data")
    if data is None:
        log.warning("cmc_empty_data", path=path, keys=list(payload))
        return None

    if r is not None and cache_key and cache_ttl:
        try:
            await r.set(cache_key, json.dumps(data), ex=cache_ttl)
        except Exception:
            pass
    return data


# ---------- typed helpers -------------------------------------------------

async def key_info() -> dict | None:
    """Authoritative plan + usage. Free (0 credits) — safe to poll."""
    data = await get("/v1/key/info")
    return data if isinstance(data, dict) else None


async def quotes_latest(symbols: list[str], convert: str = "USD") -> dict | None:
    """Latest quote per symbol. Batches up to 100 symbols for ONE credit."""
    syms = [s.strip().upper() for s in symbols if s and s.strip()]
    if not syms:
        return None
    data = await get(
        "/v1/cryptocurrency/quotes/latest",
        {"symbol": ",".join(syms[:100]), "convert": convert},
    )
    return data if isinstance(data, dict) else None


async def global_metrics(convert: str = "USD") -> dict | None:
    """BTC dominance + total market cap — the regime covariates."""
    data = await get("/v1/global-metrics/quotes/latest", {"convert": convert})
    return data if isinstance(data, dict) else None


async def listings_latest(limit: int = 500, convert: str = "USD") -> list | None:
    """Top coins by market cap. Used for symbol→name attribution."""
    data = await get(
        "/v1/cryptocurrency/listings/latest",
        {"start": 1, "limit": max(1, min(limit, 5000)), "convert": convert},
    )
    return data if isinstance(data, list) else None


TRENDING_PATHS = {
    "latest": "/v1/cryptocurrency/trending/latest",
    "most-visited": "/v1/cryptocurrency/trending/most-visited",
    "gainers-losers": "/v1/cryptocurrency/trending/gainers-losers",
}


async def trending(kind: str, limit: int = 100, time_period: str = "24h") -> list | None:
    """One of the three documented trending lists, in rank order.

    Plan gating here is UNVERIFIED — a 403 marks the path dead for a day and
    logs ``cmc_not_entitled``, which is how we learn what Basic covers.
    """
    path = TRENDING_PATHS.get(kind)
    if path is None:
        raise ValueError(f"unknown trending kind: {kind}")
    params: dict = {"limit": max(1, min(limit, 200))}
    if kind != "latest":
        params["time_period"] = time_period
    data = await get(path, params)
    return data if isinstance(data, list) else None


async def community_trending_token(limit: int = 100) -> list | None:
    """Social crowding — a different population from search trending."""
    data = await get("/v1/community/trending/token", {"limit": max(1, min(limit, 200))})
    return data if isinstance(data, list) else None


async def credits_used_this_month() -> int:
    try:
        r = redis_service.get_redis()
        raw = await r.get(CREDITS_KEY.format(month=_month()))
        return int(_decode(raw)) if raw is not None else 0
    except Exception:
        return 0


__all__ = [
    "enabled",
    "get",
    "key_info",
    "quotes_latest",
    "global_metrics",
    "listings_latest",
    "trending",
    "community_trending_token",
    "credits_used_this_month",
]
