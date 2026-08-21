"""CMCPulse — free CoinMarketCap regime + crowding context.

Two collectors and one stamp, all observational — nothing in the bots reads
this to make a decision:

- ``collect_fear_greed`` (4h job): the CMC Fear & Greed index from the
  **official keyless public API** — no key, no signup, 1 credit. The other
  advertised keyless indices (Altcoin Season, CMC100) return errors on the
  public path as of 2026-08-21, so only F&G is collected.
- ``collect_trending`` (1h job): CMC's top-search ranks from the frontend
  data API. Undocumented endpoint — same trade-off as the Binance BAPI call
  in ``listingwatch/exchanges.py``: stable in practice, treat failures as
  soft, and expect it to break someday. The 1-based *position in the list*
  is the crowding signal (the payload's ``rank`` field is market-cap rank —
  not what we want).
- ``snapshot_trade_context``: called by the executor when any MajorsBot
  trade opens; stamps the current Redis context onto a
  ``trade_context_snapshots`` row. Reads Redis only — never HTTP — and
  swallows everything: a missing snapshot must never cost an entry.

Why this exists: the newsevent forward test runs with frozen dials ("on
attend"). These columns turn the waiting period into the dataset for the
next iteration — at the gate we can test "do entries during Greed do worse?"
and "was the symbol already trending when we entered?" on contemporaneous
data instead of reconstruction. Trade 1 (XRP, −9.1R) was almost certainly
top-of-trending at entry; from now on that is a recorded fact, not a guess.

Redis keys (load-bearing, see redis_service conventions):
  cmcpulse:fear_greed    hash {value, classification, update_time} TTL 8h
  cmcpulse:trending      hash {SYMBOL: json [position, change_24h]} TTL 2h
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from app.logging_config import log
from app.models.cmcpulse import TradeContextSnapshot
from app.services import redis_service

FEAR_GREED_URL = "https://pro-api.coinmarketcap.com/public-api/v3/fear-and-greed/latest"
TRENDING_URL = "https://api.coinmarketcap.com/data-api/v3/topsearch/rank"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)

FEAR_GREED_KEY = "cmcpulse:fear_greed"
TRENDING_KEY = "cmcpulse:trending"
FEAR_GREED_TTL_S = 8 * 3600   # 2× the 4h job cadence
TRENDING_TTL_S = 2 * 3600     # 2× the 1h job cadence

# Suffixes stripped to map an exchange symbol (XRPUSDT) to CMC's coin symbol.
QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")


def base_coin(symbol: str) -> str:
    s = symbol.strip().upper()
    for suffix in QUOTE_SUFFIXES:
        if s.endswith(suffix) and len(s) > len(suffix):
            return s[: -len(suffix)]
    return s


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


async def collect_trending() -> int:
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

    if not mapping:
        log.warning("cmcpulse_trending_empty")
        return 0

    r = redis_service.get_redis()
    # Replace wholesale — yesterday's trending must not linger as today's.
    await r.delete(TRENDING_KEY)
    await r.hset(TRENDING_KEY, mapping=mapping)
    await r.expire(TRENDING_KEY, TRENDING_TTL_S)
    return len(mapping)


# ---------- read side ----------

async def get_context(symbol: str | None = None) -> dict:
    """Current context, optionally with the trending entry for one symbol."""
    out: dict = {"fear_greed": None, "fear_greed_class": None,
                 "trending_rank": None, "trending_change_24h": None}
    try:
        # get_redis inside the try: with Redis down, context degrades to
        # all-nulls (and the snapshot row still records that we looked).
        r = redis_service.get_redis()
        fg = await r.hgetall(FEAR_GREED_KEY) or {}
        fg = {_decode(k): _decode(v) for k, v in fg.items()}
        if fg.get("value"):
            out["fear_greed"] = int(fg["value"])
            out["fear_greed_class"] = fg.get("classification") or None

        if symbol is not None:
            raw = await r.hget(TRENDING_KEY, base_coin(symbol))
            if raw is not None:
                position, change = json.loads(_decode(raw))
                out["trending_rank"] = int(position)
                if change is not None:
                    out["trending_change_24h"] = Decimal(str(round(float(change), 4)))
    except Exception as e:
        log.warning("cmcpulse_context_read_failed", err=str(e))
    return out


async def snapshot_trade_context(db, trade) -> None:
    """Stamp current context onto one just-opened trade. Never raises —
    context is a bonus, an entry must not fail for lack of it."""
    try:
        ctx = await get_context(trade.symbol)
        db.add(TradeContextSnapshot(
            trade_id=trade.id,
            symbol=trade.symbol,
            strategy=trade.strategy,
            fear_greed=ctx["fear_greed"],
            fear_greed_class=ctx["fear_greed_class"],
            trending_rank=ctx["trending_rank"],
            trending_change_24h=ctx["trending_change_24h"],
            captured_at=datetime.now(timezone.utc),
        ))
        await db.commit()
        log.info(
            "cmcpulse_trade_context_captured",
            trade_id=str(trade.id),
            symbol=trade.symbol,
            fear_greed=ctx["fear_greed"],
            trending_rank=ctx["trending_rank"],
        )
    except Exception as e:
        try:
            await db.rollback()
        except Exception:
            pass
        log.warning(
            "cmcpulse_trade_context_failed", trade_id=str(getattr(trade, "id", "?")), err=str(e)
        )


def _decode(v):
    return v.decode() if isinstance(v, bytes) else v


# ---------- scheduler wrappers ----------

async def run_indices_job() -> None:
    try:
        await collect_fear_greed()
    except Exception as e:
        log.error("cmcpulse_indices_failed", error=str(e))


async def run_trending_job() -> None:
    try:
        n = await collect_trending()
        if n:
            log.info("cmcpulse_trending_collected", symbols=n)
    except Exception as e:
        log.error("cmcpulse_trending_failed", error=str(e))
