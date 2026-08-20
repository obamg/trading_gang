"""MajorsBot — newsevent: two-leg news + volume confirmation.

A trade needs BOTH legs on the same symbol inside a 15-minute window, in
**either order**:

  leg V  a volume/price spike on the latest completed 5m bar
  leg N  a high-impact news item or exchange announcement for that symbol

Whichever lands first is parked in Redis with a window-length TTL; when the
other arrives the pair fires. Symmetric on purpose — a spike that precedes the
announcement is the market front-running it, one that follows is the market
reacting to it, and both describe the same event.

Why this shape, from measurement rather than preference:

- The news leg is 1-2 minutes late (HTTP polling; Binance's rate limit pins
  announcements at 2 min) while the volume leg is realtime off the Bybit WS.
  Making news the sole trigger means arriving after faster participants; the
  pairing lets the fast leg carry the timing.
- On the 10 majors alone the two legs are statistically independent, giving
  roughly one pair every 1-3 months — the n>=30 gate would be years away. The
  universe is therefore majors UNION any symbol with a live news leg, where a
  listing/delisting announcement *causes* the spike and the legs co-occur by
  construction.

Isolation is deliberate. volevent is mid-forward-test (n=21 of 30) and its
parameters mirror a 12-month backtest, so nothing here touches its path: own
bar series (5m, ``data.get_fast_market_data``), own tick job, own position
walker, own sizing knobs.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.config import settings as app_settings
from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.majorsbot import MajorsBotTrade
from app.models.news import NewsArticle
from app.modules.majorsbot import data, equity, executor, strategies
from app.services import redis_service

VOL_LEG_KEY = "majorsbot:newsevent:vol:{symbol}"
NEWS_LEG_KEY = "majorsbot:newsevent:news:{symbol}"
# Guards against re-entering the same event on the next tick while the spike
# bar is still the latest completed bar.
COOLDOWN_KEY = "majorsbot:newsevent:cooldown:{symbol}"
COOLDOWN_S = 3600

QUOTE = "USDT"
ACTIVE_SYMBOLS_KEY = "symbols:active"

# Sources whose items are primary events rather than media reporting. Anything
# from these counts as a news leg regardless of keyword importance, because
# importance there comes from the endpoint, not from a heuristic.
PRIMARY_SOURCES = ("Binance Announcements", "Upbit Notices")


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _dec(v) -> Decimal:
    return Decimal(str(v))


def _decode(v):
    return v.decode() if isinstance(v, bytes) else v


def symbol_for_coin(coin: str) -> str:
    return f"{coin.strip().upper()}{QUOTE}"


# ---------- leg sources ----------

async def recent_news_legs(db, window_s: int) -> dict[str, dict]:
    """One query per tick: symbol -> newest qualifying news leg in the window.

    Querying once and fanning out beats a per-symbol query, and the universe
    is derived from the result anyway.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_s)
    rows = (
        await db.execute(
            select(NewsArticle)
            .where(NewsArticle.published_at >= cutoff)
            .where(NewsArticle.coins.isnot(None))
            .order_by(NewsArticle.published_at.desc())
        )
    ).scalars().all()

    legs: dict[str, dict] = {}
    for row in rows:
        is_primary = row.source_name in PRIMARY_SOURCES
        if not is_primary and row.importance != "high":
            continue
        for coin in (row.coins or "").split(","):
            coin = coin.strip()
            if not coin:
                continue
            symbol = symbol_for_coin(coin)
            # rows are newest-first, so the first hit per symbol wins
            legs.setdefault(symbol, {
                "ts_ms": int(row.published_at.timestamp() * 1000),
                "sentiment": row.sentiment,
                "source": row.source_name,
                "primary": is_primary,
                "title": row.title,
            })
    return legs


async def tradeable_universe(news_symbols: set[str]) -> list[str]:
    """Majors UNION news-bearing symbols that Bybit actually lists.

    ``symbols:active`` is maintained by the live stream, so it is the cheapest
    proof that a symbol is a real linear perp before we spend a REST call.
    """
    from app.modules.majorsbot.engine import symbol_list

    majors = symbol_list()
    out = list(majors)
    if not news_symbols:
        return out

    r = redis_service.get_redis()
    try:
        active = {_decode(s).upper() for s in (await r.smembers(ACTIVE_SYMBOLS_KEY) or set())}
    except Exception as e:
        log.warning("newsevent_active_symbols_failed", err=str(e))
        active = set()

    for symbol in sorted(news_symbols):
        if symbol not in out and symbol in active:
            out.append(symbol)
    return out


# ---------- leg store ----------

async def store_leg(key: str, symbol: str, leg: dict, window_s: int) -> None:
    r = redis_service.get_redis()
    await r.set(key.format(symbol=symbol), json.dumps(leg), ex=window_s)


async def load_leg(key: str, symbol: str) -> dict | None:
    r = redis_service.get_redis()
    raw = await r.get(key.format(symbol=symbol))
    if raw is None:
        return None
    try:
        return json.loads(_decode(raw))
    except (ValueError, TypeError):
        return None


async def clear_legs(symbol: str) -> None:
    r = redis_service.get_redis()
    await r.delete(VOL_LEG_KEY.format(symbol=symbol))
    await r.delete(NEWS_LEG_KEY.format(symbol=symbol))


# ---------- entry ----------

async def _has_live_trade(db, symbol: str) -> bool:
    rows = (
        await db.execute(
            select(MajorsBotTrade)
            .where(MajorsBotTrade.symbol == symbol)
            .where(MajorsBotTrade.strategy == strategies.NEWSEVENT)
            .where(MajorsBotTrade.status.in_(("pending", "open")))
        )
    ).scalars().all()
    return bool(rows)


async def count_open(db) -> int:
    """Open newsevent positions only.

    Deliberately NOT equity.get_concurrent_count(), which is a bot-wide
    counter: with a per-strategy cap of 1, volevent's open positions would
    permanently block newsevent from ever entering.
    """
    rows = (
        await db.execute(
            select(MajorsBotTrade)
            .where(MajorsBotTrade.strategy == strategies.NEWSEVENT)
            .where(MajorsBotTrade.status.in_(("pending", "open")))
        )
    ).scalars().all()
    return len(rows)


async def _try_enter(db, symbol: str, vol_leg: dict, news_leg: dict) -> bool:
    """Both legs present — validate the pair and open at market."""
    if not strategies.legs_paired(
        int(vol_leg["ts_ms"]), int(news_leg["ts_ms"]), strategies.NEWSEVENT_PAIR_WINDOW_S
    ):
        return False

    direction = strategies.newsevent_direction(
        vol_leg["direction"], news_leg.get("sentiment")
    )
    if direction is None:
        log.info(
            "newsevent_legs_disagree",
            symbol=symbol,
            price_direction=vol_leg["direction"],
            news_sentiment=news_leg.get("sentiment"),
        )
        await clear_legs(symbol)
        return False

    r = redis_service.get_redis()
    if await r.get(COOLDOWN_KEY.format(symbol=symbol)):
        return False
    if await _has_live_trade(db, symbol):
        return False

    max_concurrent = int(app_settings.majorsbot_newsevent_max_concurrent)
    if await count_open(db) >= max_concurrent:
        log.info("newsevent_skipped", symbol=symbol, reason="max_concurrent")
        return False

    entry = _dec(vol_leg["close"])
    extreme = _dec(vol_leg["bar_low"] if direction == "long" else vol_leg["bar_high"])
    # Always computed: with stops on it is the exit, with stops off it is
    # still the risk unit R is measured in.
    ref_stop, _ref_risk = strategies.newsevent_stop(direction, entry, extreme)

    eq = await equity.get_paper_equity()
    cap_pct = _dec(app_settings.majorsbot_newsevent_position_size_pct)
    stop_enabled = bool(getattr(app_settings, "majorsbot_newsevent_stop_enabled", True))

    if stop_enabled:
        qty = strategies.compute_qty(
            paper_equity=eq,
            risk_per_trade_pct=_dec(
                app_settings.majorsbot_newsevent_risk_per_trade_pct
            ),
            entry_price=entry,
            stop_price=ref_stop,
            max_notional_pct=cap_pct,
        )
    else:
        # No stop distance to normalise against, so the notional cap IS the
        # size: qty = equity x leverage / entry.
        qty = (eq * cap_pct / entry) if entry > 0 else Decimal("0")
    if qty <= 0:
        return False

    leverage = strategies.effective_leverage(entry * qty, eq)
    liq = strategies.liquidation_price(direction, entry, leverage)
    # stop_price always means "where this position dies". With the protective
    # stop off that is the liquidation price; if leverage is low enough that
    # liquidation is unreachable, fall back to the reference stop so the
    # NOT NULL column still carries a meaningful number.
    exit_stop = ref_stop if stop_enabled else (liq if liq is not None else ref_stop)

    lag_s = abs(int(vol_leg["ts_ms"]) - int(news_leg["ts_ms"])) / 1000
    trade = await executor.open_market_trade(
        db,
        symbol=symbol,
        direction=direction,
        strategy=strategies.NEWSEVENT,
        signal_at=_dt(int(vol_leg["ts_ms"])),
        entry_price=entry,
        entry_bar_at=_dt(int(vol_leg["bar_ts"])),
        stop_price=exit_stop,
        initial_stop_price=ref_stop,
        qty=qty,
        paper_equity=eq,
        alert_extra={
            "leverage": float(leverage),
            "stop_kind": "liquidation" if (not stop_enabled and liq is not None) else "stop",
            "news_source": news_leg.get("source"),
            "news_title": news_leg.get("title"),
            "leg_order": "news_first" if news_leg["ts_ms"] <= vol_leg["ts_ms"] else "volume_first",
            "leg_gap_s": lag_s,
        },
    )
    await r.set(COOLDOWN_KEY.format(symbol=symbol), "1", ex=COOLDOWN_S)
    await clear_legs(symbol)

    log.info(
        "newsevent_entered",
        id=str(trade.id),
        symbol=symbol,
        direction=direction,
        leg_order="news_first" if news_leg["ts_ms"] <= vol_leg["ts_ms"] else "volume_first",
        leg_gap_s=lag_s,
        news_source=news_leg.get("source"),
        vol_mult=vol_leg.get("vol_mult"),
        entry=float(entry),
        qty=float(qty),
        leverage=float(leverage),
        stop_enabled=stop_enabled,
        exit_stop=float(exit_stop),
        liquidation=float(liq) if liq is not None else None,
        r_reference_stop=float(ref_stop),
    )
    return True


# ---------- position management (5m bars) ----------

async def _walk_open(db, trade: MajorsBotTrade, md: data.MarketData) -> bool:
    """Advance one open newsevent position over unseen 5m bars.

    A deliberately simpler walker than engine._walk_open: no funding accrual
    (holds are capped at 6h) and no pending-limit state (entries are market).
    Same pessimistic ordering as the bake-off — the stop is checked before the
    target within a bar and wins ties.
    """
    entry_bar = int(trade.entry_bar_at.timestamp() * 1000) if trade.entry_bar_at else 0
    bars = [b for b in md.bars if int(b["t"]) > entry_bar]
    if not bars:
        return False

    direction = trade.direction
    entry = _dec(trade.entry_price)
    initial_stop = _dec(trade.initial_stop_price or trade.stop_price)
    risk = abs(entry - initial_stop)
    if risk <= 0:
        return False

    stop_enabled = bool(getattr(app_settings, "majorsbot_newsevent_stop_enabled", True))
    # With the protective stop off, `stop` starts life as the liquidation
    # price and is inert until the trail arms at +1R. The trail is profit
    # protection, not loss limitation, so disabling the stop must not disable
    # it — otherwise a trail would be recorded and never acted on.
    stop = _dec(trade.stop_price)
    took_partial = trade.partial_qty is not None and _dec(trade.partial_qty) > 0
    tp = strategies.take_profit_price(
        direction, entry, risk, strategies.NEWSEVENT_TP_R
    )

    # Liquidation is checked whether or not a protective stop is configured —
    # it is the exchange's exit, not ours, and ignoring it would let a paper
    # position recover from a drawdown that would already have been closed.
    leverage = strategies.effective_leverage(
        entry * _dec(trade.qty), _dec(trade.paper_equity_at_entry)
    )
    liq = strategies.liquidation_price(direction, entry, leverage)

    # Whether the trail has already armed on an earlier tick, derived rather
    # than stored: at entry stop == entry_stop, and the trail only ever
    # ratchets it toward price, so a strictly tighter stop means it armed.
    entry_stop = liq if (not stop_enabled and liq is not None) else initial_stop
    trail_armed = (stop > entry_stop) if direction == "long" else (stop < entry_stop)

    peak = entry
    held = 0

    for bar in bars:
        held += 1
        high, low = _dec(bar["h"]), _dec(bar["l"])

        if liq is not None:
            hit_liq = low <= liq if direction == "long" else high >= liq
            if hit_liq:
                await executor.close_trade(
                    db, trade, raw_exit_price=liq,
                    reason=strategies.CLOSE_LIQUIDATION,
                )
                log.warning(
                    "newsevent_liquidated",
                    id=str(trade.id), symbol=trade.symbol,
                    direction=direction, entry=float(entry),
                    liquidation=float(liq), leverage=float(leverage),
                )
                return True

        if stop_enabled or trail_armed:
            hit_stop = low <= stop if direction == "long" else high >= stop
            if hit_stop:
                await executor.close_trade(
                    db, trade,
                    raw_exit_price=stop,
                    reason=strategies.CLOSE_TRAIL if trail_armed else strategies.CLOSE_STOP,
                )
                return True

        if not took_partial:
            hit_tp = high >= tp if direction == "long" else low <= tp
            if hit_tp:
                await executor.take_partial_profit(
                    db, trade,
                    exit_price=tp,
                    bar_close_at=_dt(int(bar["t"]) + strategies.NEWSEVENT_BAR_MS),
                    fraction=strategies.NEWSEVENT_PARTIAL_FRACTION,
                )
                took_partial = True

        peak = max(peak, high) if direction == "long" else min(peak, low)
        moved = (peak - entry) if direction == "long" else (entry - peak)
        if moved >= strategies.NEWSEVENT_TRAIL_ARM_R * risk:
            trail_dist = strategies.NEWSEVENT_TRAIL_DIST_R * risk
            new_stop = (
                peak - trail_dist if direction == "long" else peak + trail_dist
            )
            # Ratchet only — a trail never loosens. When the protective stop
            # is off, `stop` is still the liquidation price on the first arm,
            # and the trail is always tighter than that.
            stop = max(stop, new_stop) if direction == "long" else min(stop, new_stop)
            trade.stop_price = stop
            trail_armed = True

        if held >= strategies.NEWSEVENT_MAX_HOLD_BARS:
            await executor.close_trade(
                db, trade, raw_exit_price=_dec(bar["c"]),
                reason=strategies.CLOSE_MAX_HOLD,
            )
            return True

    await db.commit()
    return False


# ---------- tick ----------

async def run_newsevent_tick() -> dict:
    """One pass: manage open positions, refresh both legs, fire any pairs."""
    totals = {"symbols": 0, "opened": 0, "closed": 0, "vol_legs": 0, "news_legs": 0}
    if not getattr(app_settings, "majorsbot_enabled", False):
        return totals
    if not getattr(app_settings, "majorsbot_newsevent_enabled", False):
        return totals

    window_s = strategies.NEWSEVENT_PAIR_WINDOW_S

    async with AsyncSessionLocal() as db:
        news_legs = await recent_news_legs(db, window_s)
        totals["news_legs"] = len(news_legs)

        open_rows = (
            await db.execute(
                select(MajorsBotTrade)
                .where(MajorsBotTrade.strategy == strategies.NEWSEVENT)
                .where(MajorsBotTrade.status == "open")
            )
        ).scalars().all()

        universe = await tradeable_universe(set(news_legs))
        # An open position must be walked even if its symbol left the universe.
        for row in open_rows:
            if row.symbol not in universe:
                universe.append(row.symbol)

        open_by_symbol = {row.symbol: row for row in open_rows}

        for symbol in universe:
            totals["symbols"] += 1
            try:
                md = await data.get_fast_market_data(symbol)
                if md is None or not md.bars:
                    continue

                trade = open_by_symbol.get(symbol)
                if trade is not None:
                    if await _walk_open(db, trade, md):
                        totals["closed"] += 1
                    continue

                # --- leg V: volume spike on the latest completed 5m bar
                vol_leg = strategies.newsevent_volume_leg(md.bars)
                if vol_leg is not None:
                    vol_leg["ts_ms"] = int(vol_leg["bar_ts"]) + strategies.NEWSEVENT_BAR_MS
                    await store_leg(VOL_LEG_KEY, symbol, vol_leg, window_s)
                    totals["vol_legs"] += 1
                else:
                    vol_leg = await load_leg(VOL_LEG_KEY, symbol)

                # --- leg N: news in the window
                news_leg = news_legs.get(symbol)
                if news_leg is not None:
                    await store_leg(NEWS_LEG_KEY, symbol, news_leg, window_s)
                else:
                    news_leg = await load_leg(NEWS_LEG_KEY, symbol)

                if vol_leg is not None and news_leg is not None:
                    if await _try_enter(db, symbol, vol_leg, news_leg):
                        totals["opened"] += 1
            except Exception as e:
                log.warning("newsevent_symbol_failed", symbol=symbol, err=str(e))

    if totals["opened"] or totals["closed"] or totals["vol_legs"]:
        log.info("newsevent_tick", **totals)
    return totals


async def run_newsevent_job() -> None:
    try:
        await run_newsevent_tick()
    except Exception as e:
        log.error("newsevent_tick_failed", error=str(e))
