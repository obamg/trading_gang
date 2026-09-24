"""CMCPulse — CoinMarketCap regime + crowding context.

Collectors and one stamp, all observational — nothing in the bots reads this
to make a decision:

- ``collect_fear_greed`` (4h job): the CMC Fear & Greed index from the
  **official keyless public API** — no key, no signup, 1 credit.
- ``collect_global`` (4h job): BTC dominance + total market cap from
  ``/v1/global-metrics/quotes/latest``. Needs a key; no-ops without one.
- ``collect_trending`` (1h job): search-trending ranks. Prefers the
  documented ``/v1/cryptocurrency/trending/latest`` when a key is
  configured, and falls back to CMC's undocumented frontend data API
  otherwise. The 1-based *position in the list* is the crowding signal (the
  payload's ``rank``/``cmc_rank`` field is market-cap rank — not what we
  want).
- ``collect_crowding`` (1h job): the other three crowding populations —
  most-visited, gainers/losers, and community-trending. Key required. Each
  is a DIFFERENT population from search-trending, which is the point: at the
  gate we can ask which kind of attention (if any) predicts a worse entry.
Read the current picture with ``get_context()`` or ``GET /cmcpulse/context``.

The per-trade snapshot half of this module was removed with the bots — it
stamped regime context onto MajorsBot entries, and there are no entries now.
The collectors stand alone: they are the only source of Fear & Greed, CMC
search-trending and whole-market regime in the app.

On the keyed endpoints: which of them the free Basic plan actually covers is
UNVERIFIED. ``cmc_client`` marks a 403'd path dead for a day and logs
``cmc_not_entitled`` — so an unavailable endpoint costs one call per day and
leaves its columns NULL, rather than breaking collection. Everything here
degrades to exactly what it collected before.

Redis keys (load-bearing, see redis_service conventions):
  cmcpulse:fear_greed      hash {value, classification, update_time} TTL 8h
  cmcpulse:trending        hash {SYMBOL: json [position, change_24h]} TTL 2h
  cmcpulse:most_visited    hash {SYMBOL: position} TTL 2h
  cmcpulse:gainers_losers  hash {SYMBOL: position} TTL 2h
  cmcpulse:community       hash {SYMBOL: position} TTL 2h
  cmcpulse:global          hash {btc_dominance, total_mcap} TTL 8h
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from app.logging_config import log
from app.services import cmc_client, redis_service

FEAR_GREED_URL = "https://pro-api.coinmarketcap.com/public-api/v3/fear-and-greed/latest"
TRENDING_URL = "https://api.coinmarketcap.com/data-api/v3/topsearch/rank"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)

FEAR_GREED_KEY = "cmcpulse:fear_greed"
TRENDING_KEY = "cmcpulse:trending"
MOST_VISITED_KEY = "cmcpulse:most_visited"
GAINERS_LOSERS_KEY = "cmcpulse:gainers_losers"
COMMUNITY_KEY = "cmcpulse:community"
GLOBAL_KEY = "cmcpulse:global"

FEAR_GREED_TTL_S = 8 * 3600   # 2× the 4h job cadence
TRENDING_TTL_S = 2 * 3600     # 2× the 1h job cadence
GLOBAL_TTL_S = 8 * 3600       # 2× the 4h job cadence

# Suffixes stripped to map an exchange symbol (XRPUSDT) to CMC's coin symbol.
QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")


def base_coin(symbol: str) -> str:
    s = symbol.strip().upper()
    for suffix in QUOTE_SUFFIXES:
        if s.endswith(suffix) and len(s) > len(suffix):
            return s[: -len(suffix)]
    return s


def _decode(v):
    return v.decode() if isinstance(v, bytes) else v


def _iter_coins(data) -> list[dict]:
    """Normalize a CMC list payload to a flat list of coin dicts.

    The trending family returns a bare list; gainers-losers has also been
    observed wrapped as {"gainers": [...], "losers": [...]}. Accept both
    rather than depend on which one we happen to get — a shape change here
    should cost us a NULL column, not an exception.
    """
    if isinstance(data, list):
        return [c for c in data if isinstance(c, dict)]
    if isinstance(data, dict):
        out: list[dict] = []
        for value in data.values():
            if isinstance(value, list):
                out.extend(c for c in value if isinstance(c, dict))
        return out
    return []


def _pct_change_24h(coin: dict):
    quote = (coin.get("quote") or {}).get("USD") or {}
    return quote.get("percent_change_24h")


async def _store_rank_hash(key: str, mapping: dict[str, str]) -> int:
    """Replace a crowding hash wholesale — yesterday's list must not linger."""
    if not mapping:
        return 0
    r = redis_service.get_redis()
    await r.delete(key)
    await r.hset(key, mapping=mapping)
    await r.expire(key, TRENDING_TTL_S)
    return len(mapping)


# ---------- collectors ----------

async def collect_fear_greed() -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(FEAR_GREED_URL, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            payload = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("cmcpulse_fear_greed_fetch_failed", err=str(e))
        return None

    data = payload.get("data") or {}
    value = data.get("value")
    if value is None:
        log.warning("cmcpulse_fear_greed_empty", payload_keys=list(payload))
        return None

    entry = {
        "value": str(int(value)),
        "classification": str(data.get("value_classification") or ""),
        "update_time": str(data.get("update_time") or ""),
    }
    r = redis_service.get_redis()
    await r.hset(FEAR_GREED_KEY, mapping=entry)
    await r.expire(FEAR_GREED_KEY, FEAR_GREED_TTL_S)
    return entry


async def collect_global() -> dict | None:
    """BTC dominance + total market cap. No-ops without a CMC key."""
    if not cmc_client.enabled():
        return None
    data = await cmc_client.global_metrics()
    if not isinstance(data, dict):
        return None

    dominance = data.get("btc_dominance")
    total_mcap = ((data.get("quote") or {}).get("USD") or {}).get("total_market_cap")
    if dominance is None and total_mcap is None:
        log.warning("cmcpulse_global_empty", keys=list(data))
        return None

    entry = {
        "btc_dominance": "" if dominance is None else str(round(float(dominance), 4)),
        "total_mcap": "" if total_mcap is None else str(round(float(total_mcap), 2)),
    }
    r = redis_service.get_redis()
    await r.hset(GLOBAL_KEY, mapping=entry)
    await r.expire(GLOBAL_KEY, GLOBAL_TTL_S)
    return entry


async def _collect_trending_documented() -> int:
    """Search-trending via the supported endpoint. 0 = unavailable."""
    coins = _iter_coins(await cmc_client.trending("latest"))
    mapping: dict[str, str] = {}
    for position, coin in enumerate(coins, start=1):
        sym = (coin.get("symbol") or "").strip().upper()
        if not sym:
            continue
        mapping.setdefault(sym, json.dumps([position, _pct_change_24h(coin)]))
    return await _store_rank_hash(TRENDING_KEY, mapping)


async def _collect_trending_scraped() -> int:
    """Undocumented frontend data API — the pre-key fallback.

    Same trade-off as the Binance BAPI call in ``listingwatch/exchanges.py``:
    stable in practice, treat failures as soft, and expect it to break
    someday. Configuring a CMC key retires this path.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(TRENDING_URL, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            payload = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("cmcpulse_trending_fetch_failed", err=str(e))
        return 0

    ranks = ((payload.get("data") or {}).get("cryptoTopSearchRanks")) or []
    mapping: dict[str, str] = {}
    for position, item in enumerate(ranks, start=1):
        sym = (item.get("symbol") or "").strip().upper()
        if not sym:
            continue
        change = ((item.get("priceChange") or {}).get("priceChange24h"))
        mapping.setdefault(sym, json.dumps([position, change]))
    return await _store_rank_hash(TRENDING_KEY, mapping)


async def collect_trending() -> int:
    """Search-trending ranks: documented endpoint first, scrape as fallback."""
    if cmc_client.enabled():
        n = await _collect_trending_documented()
        if n:
            return n
        # Entitlement or transient failure — the scrape still works keyless,
        # so a plan that excludes trending costs us nothing.
        log.info("cmcpulse_trending_documented_unavailable_using_fallback")

    n = await _collect_trending_scraped()
    if not n:
        log.warning("cmcpulse_trending_empty")
    return n


async def collect_crowding() -> dict[str, int]:
    """Most-visited, gainers/losers and community ranks. Key required."""
    counts = {"most_visited": 0, "gainers_losers": 0, "community": 0}
    if not cmc_client.enabled():
        return counts

    for kind, key, name in (
        ("most-visited", MOST_VISITED_KEY, "most_visited"),
        ("gainers-losers", GAINERS_LOSERS_KEY, "gainers_losers"),
    ):
        coins = _iter_coins(await cmc_client.trending(kind))
        mapping: dict[str, str] = {}
        for position, coin in enumerate(coins, start=1):
            sym = (coin.get("symbol") or "").strip().upper()
            if sym:
                mapping.setdefault(sym, str(position))
        counts[name] = await _store_rank_hash(key, mapping)

    coins = _iter_coins(await cmc_client.community_trending_token())
    community_map: dict[str, str] = {}
    for position, coin in enumerate(coins, start=1):
        sym = (coin.get("symbol") or "").strip().upper()
        if sym:
            community_map.setdefault(sym, str(position))
    counts["community"] = await _store_rank_hash(COMMUNITY_KEY, community_map)
    return counts


# ---------- read side ----------

_NULL_CONTEXT: dict = {
    "fear_greed": None,
    "fear_greed_class": None,
    "trending_rank": None,
    "trending_change_24h": None,
    "most_visited_rank": None,
    "gainers_losers_rank": None,
    "community_rank": None,
    "btc_dominance_pct": None,
    "total_mcap_usd": None,
}


async def _rank_for(r, key: str, coin: str) -> int | None:
    raw = await r.hget(key, coin)
    if raw is None:
        return None
    try:
        return int(_decode(raw))
    except (TypeError, ValueError):
        return None


async def get_context(symbol: str | None = None) -> dict:
    """Current context, optionally with the per-symbol crowding entries."""
    out: dict = dict(_NULL_CONTEXT)
    try:
        # get_redis inside the try: with Redis down, context degrades to
        # all-nulls (and the snapshot row still records that we looked).
        r = redis_service.get_redis()
        fg = await r.hgetall(FEAR_GREED_KEY) or {}
        fg = {_decode(k): _decode(v) for k, v in fg.items()}
        if fg.get("value"):
            out["fear_greed"] = int(fg["value"])
            out["fear_greed_class"] = fg.get("classification") or None

        gl = await r.hgetall(GLOBAL_KEY) or {}
        gl = {_decode(k): _decode(v) for k, v in gl.items()}
        if gl.get("btc_dominance"):
            out["btc_dominance_pct"] = Decimal(str(round(float(gl["btc_dominance"]), 4)))
        if gl.get("total_mcap"):
            out["total_mcap_usd"] = Decimal(str(round(float(gl["total_mcap"]), 2)))

        if symbol is not None:
            coin = base_coin(symbol)
            raw = await r.hget(TRENDING_KEY, coin)
            if raw is not None:
                position, change = json.loads(_decode(raw))
                out["trending_rank"] = int(position)
                if change is not None:
                    out["trending_change_24h"] = Decimal(str(round(float(change), 4)))
            out["most_visited_rank"] = await _rank_for(r, MOST_VISITED_KEY, coin)
            out["gainers_losers_rank"] = await _rank_for(r, GAINERS_LOSERS_KEY, coin)
            out["community_rank"] = await _rank_for(r, COMMUNITY_KEY, coin)
    except Exception as e:
        log.warning("cmcpulse_context_read_failed", err=str(e))
    return out


# ---------- scheduler wrappers ----------

async def run_indices_job() -> None:
    try:
        await collect_fear_greed()
    except Exception as e:
        log.error("cmcpulse_indices_failed", error=str(e))
    try:
        await collect_global()
    except Exception as e:
        log.error("cmcpulse_global_failed", error=str(e))


async def run_trending_job() -> None:
    try:
        n = await collect_trending()
        if n:
            log.info("cmcpulse_trending_collected", symbols=n)
    except Exception as e:
        log.error("cmcpulse_trending_failed", error=str(e))
    try:
        counts = await collect_crowding()
        if any(counts.values()):
            log.info("cmcpulse_crowding_collected", **counts)
    except Exception as e:
        log.error("cmcpulse_crowding_failed", error=str(e))
