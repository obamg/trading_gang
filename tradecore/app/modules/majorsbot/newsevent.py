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

Isolation is deliberate. volevent is mid-forward-test (n=8 of 30 closed) and
its parameters mirror a 12-month backtest, so nothing here touches its path:
own bar series (5m, ``data.get_fast_market_data``), own tick job, own
position walker, own sizing knobs, and — since the 2026-08-20 pre-mortem —
its OWN equity ledger and concurrent counter (``equity.ledger_for``): a
stopless high-leverage liquidation costs ~95% of its book and must not slash
volevent's sizing base.

The same pre-mortem drove the paper-realism rules encoded here: entries fill
at decision-time price with taker slippage (never at a stale spike-bar
close), the walker REPLAYS from entry-time state each tick (see
``_walk_open``), stop/trail exits take gap penalties, liquidations book the
bankruptcy price, and funding accrues over the hold.
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
# Which headline caused a resting limit. The pending row has no column for it,
# and the entry alert fires at FILL — up to an hour later — so it is stashed
# here and read back then. Losing it costs alert detail, never a trade.
NEWS_CTX_KEY = "majorsbot:newsevent:ctx:{trade_id}"

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

    primary_only = bool(
        getattr(app_settings, "majorsbot_newsevent_primary_only", True)
    )
    legs: dict[str, dict] = {}
    for row in rows:
        is_primary = row.source_name in PRIMARY_SOURCES
        if not is_primary and (primary_only or row.importance != "high"):
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


async def _load_news_ctx(trade_id: str) -> dict | None:
    """Headline context stashed at placement. Best-effort: an expired or
    unparseable stash just means a less detailed entry alert."""
    try:
        r = redis_service.get_redis()
        raw = await r.get(NEWS_CTX_KEY.format(trade_id=trade_id))
        if raw is None:
            return None
        return json.loads(_decode(raw))
    except Exception as e:
        log.warning("newsevent_ctx_read_failed", trade_id=trade_id, err=str(e))
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


async def count_open(db, statuses: tuple[str, ...] = ("pending", "open")) -> int:
    """Live newsevent rows only.

    Deliberately NOT equity.get_concurrent_count(), which is a bot-wide
    counter: with a per-strategy cap of 1, volevent's open positions would
    permanently block newsevent from ever entering.

    Default counts pendings so a resting limit blocks a second placement; the
    fill transition re-checks with ``statuses=("open",)`` since a pending holds
    no slot of its own.
    """
    rows = (
        await db.execute(
            select(MajorsBotTrade)
            .where(MajorsBotTrade.strategy == strategies.NEWSEVENT)
            .where(MajorsBotTrade.status.in_(statuses))
        )
    ).scalars().all()
    return len(rows)


def _mmr_for(symbol: str) -> Decimal:
    """Two-bucket maintenance margin: majors at tier-1, everything else at the
    small-cap rate. See strategies.NEWSEVENT_MMR_NON_MAJOR for why."""
    from app.modules.majorsbot.engine import symbol_list

    if symbol in symbol_list():
        return strategies.NEWSEVENT_MAINTENANCE_MARGIN_RATE
    return strategies.NEWSEVENT_MMR_NON_MAJOR


def _size_and_levels(symbol: str, direction: str, entry: Decimal, extreme: Decimal, eq: Decimal):
    """(qty, ref_stop, exit_stop, leverage, liq) for an entry at `entry`.

    Shared by the market path and the pending-limit fill so both size the same
    way. `ref_stop` is always the R denominator; `exit_stop` is where the
    position actually dies — the liquidation price when stops are disabled.
    """
    ref_stop, _ref_risk = strategies.newsevent_stop(direction, entry, extreme)
    cap_pct = _dec(app_settings.majorsbot_newsevent_position_size_pct)
    stop_enabled = bool(getattr(app_settings, "majorsbot_newsevent_stop_enabled", True))

    if stop_enabled:
        qty = strategies.compute_qty(
            paper_equity=eq,
            risk_per_trade_pct=_dec(app_settings.majorsbot_newsevent_risk_per_trade_pct),
            entry_price=entry,
            stop_price=ref_stop,
            max_notional_pct=cap_pct,
        )
    else:
        # No stop distance to normalise against, so the notional cap IS the
        # size: qty = equity x leverage / entry.
        qty = (eq * cap_pct / entry) if entry > 0 else Decimal("0")
    if qty <= 0:
        return None

    leverage = strategies.effective_leverage(entry * qty, eq)
    liq = strategies.liquidation_price(direction, entry, leverage, _mmr_for(symbol))
    # stop_price always means "where this position dies". With the protective
    # stop off that is the liquidation price; if leverage is low enough that
    # liquidation is unreachable, fall back to the reference stop so the
    # NOT NULL column still carries a meaningful number.
    exit_stop = ref_stop if stop_enabled else (liq if liq is not None else ref_stop)
    return qty, ref_stop, exit_stop, leverage, liq


async def _place_retrace_limit(
    db, symbol: str, vol_leg: dict, news_leg: dict, md: data.MarketData,
    *, direction: str, eq: Decimal, lag_s: float, leg_order: str,
) -> bool:
    """Park a limit half-way back into the spike bar instead of chasing it."""
    spike_high = _dec(vol_leg["bar_high"])
    spike_low = _dec(vol_leg["bar_low"])
    limit = strategies.newsevent_limit_price(
        direction, _dec(vol_leg["close"]), spike_high, spike_low
    )
    if limit is None:
        log.info("newsevent_degenerate_spike_bar", symbol=symbol, direction=direction)
        await clear_legs(symbol)
        return False

    extreme = spike_low if direction == "long" else spike_high
    sized = _size_and_levels(symbol, direction, limit, extreme, eq)
    if sized is None:
        return False
    qty, ref_stop, exit_stop, leverage, liq = sized
    tp = strategies.take_profit_price(
        direction, limit, abs(limit - ref_stop), strategies.NEWSEVENT_TP_R
    )

    last_bar = md.bars[-1]
    trade = await executor.place_pending_order(
        db,
        symbol=symbol,
        direction=direction,
        strategy=strategies.NEWSEVENT,
        signal_at=_dt(int(vol_leg["bar_ts"])),
        signal_high=spike_high,
        signal_low=spike_low,
        limit_price=limit,
        stop_price=exit_stop,
        initial_stop_price=ref_stop,
        take_profit_price=tp,
        qty=qty,
        paper_equity=eq,
        # Bars already closed at placement are NOT fillable — see the
        # executor docstring. The news leg can arrive minutes after the spike.
        entry_bar_at=_dt(int(last_bar["t"])),
        expire_at=datetime.now(timezone.utc)
        + timedelta(minutes=strategies.NEWSEVENT_FILL_WINDOW_MIN),
    )

    r = redis_service.get_redis()
    await r.set(
        NEWS_CTX_KEY.format(trade_id=str(trade.id)),
        json.dumps({
            "news_source": news_leg.get("source"),
            "news_title": news_leg.get("title"),
            "leg_order": leg_order,
            "leg_gap_s": lag_s,
        }),
        ex=strategies.NEWSEVENT_FILL_WINDOW_MIN * 60 + 600,
    )
    await r.set(COOLDOWN_KEY.format(symbol=symbol), "1", ex=COOLDOWN_S)
    await clear_legs(symbol)
    log.info(
        "newsevent_limit_placed",
        id=str(trade.id), symbol=symbol, direction=direction,
        limit=float(limit), spike_close=float(_dec(vol_leg["close"])),
        retrace_pct=float((abs(_dec(vol_leg["close"]) - limit) / _dec(vol_leg["close"])) * 100),
        leverage=float(leverage), news_source=news_leg.get("source"),
        leg_order=leg_order, leg_gap_s=lag_s, vol_mult=vol_leg.get("vol_mult"),
    )
    return True


async def _manage_pending(db, trade: MajorsBotTrade, md: data.MarketData) -> str:
    """One pending newsevent limit → waiting | cancelled | filled.

    Replay-safe like _walk_open: eligibility is re-derived every tick from the
    persisted row (fills only on bars strictly after ``entry_bar_at`` and
    strictly before ``expire_at``), so there is no watermark to lose.
    """
    now = datetime.now(timezone.utc)
    direction = trade.direction
    limit = _dec(trade.limit_price)
    placed_ms = int(trade.entry_bar_at.timestamp() * 1000) if trade.entry_bar_at else 0
    expire_ms = int(trade.expire_at.timestamp() * 1000) if trade.expire_at else None

    for bar in md.bars:
        t = int(bar["t"])
        if t <= placed_ms:
            continue
        if expire_ms is not None and t >= expire_ms:
            break  # a bar opening at/after expiry can't fill — the order is gone
        high, low, bar_open = _dec(bar["h"]), _dec(bar["l"]), _dec(bar["o"])
        if not strategies.is_limit_touched(direction, high, low, limit):
            continue

        # Capacity gate at the fill transition — pendings never hold a slot.
        if await count_open(db, statuses=("open",)) >= int(
            app_settings.majorsbot_newsevent_max_concurrent
        ):
            await executor.cancel_pending_order(
                db, trade, reason=strategies.CLOSE_MAX_CONCURRENT
            )
            return "cancelled"

        fill_px = strategies.limit_fill_price(direction, bar_open, limit)
        eq = await equity.get_paper_equity(equity.ledger_for(strategies.NEWSEVENT))
        extreme = _dec(trade.signal_low if direction == "long" else trade.signal_high)
        sized = _size_and_levels(trade.symbol, direction, fill_px, extreme, eq)
        if sized is None:
            await executor.cancel_pending_order(db, trade, reason="degenerate")
            return "cancelled"
        qty, ref_stop, exit_stop, leverage, liq = sized
        stop_enabled = bool(getattr(app_settings, "majorsbot_newsevent_stop_enabled", True))
        extra = {
            "leverage": float(leverage),
            "stop_kind": (
                "liquidation" if (not stop_enabled and liq is not None) else "stop"
            ),
            "retrace_fill": True,
        }
        ctx = await _load_news_ctx(str(trade.id))
        if ctx:
            extra.update(ctx)
        await executor.fill_pending_order(
            db, trade,
            fill_price=fill_px,
            stop_price=exit_stop,
            initial_stop_price=ref_stop,
            take_profit_price=strategies.take_profit_price(
                direction, fill_px, abs(fill_px - ref_stop), strategies.NEWSEVENT_TP_R
            ),
            qty=qty,
            entry_bar_at=_dt(t),
            paper_equity=eq,
            alert_extra=extra,
        )
        return "filled"

    if trade.expire_at is not None and now >= trade.expire_at:
        await executor.cancel_pending_order(db, trade)
        return "cancelled"
    return "waiting"


async def _try_enter(db, symbol: str, vol_leg: dict, news_leg: dict, md: data.MarketData) -> bool:
    """Both legs present — validate the pair, then place a retrace limit (or
    open at market when ``majorsbot_newsevent_retrace_entry`` is off)."""
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

    # Entry at DECISION-TIME price: the latest completed bar's close, with
    # taker slippage. The spike bar's close is up to ~15 min stale when the
    # news leg lands late (RSS lag + poll cadence) — filling at it silently
    # credited the position with every tick of movement since, a lookahead
    # the live version could never have.
    last_bar = md.bars[-1]
    entry_raw = _dec(last_bar["c"])
    extreme = _dec(vol_leg["bar_low"] if direction == "long" else vol_leg["bar_high"])
    eq = await equity.get_paper_equity(equity.ledger_for(strategies.NEWSEVENT))
    stop_enabled = bool(getattr(app_settings, "majorsbot_newsevent_stop_enabled", True))
    lag_s = abs(int(vol_leg["ts_ms"]) - int(news_leg["ts_ms"])) / 1000
    leg_order = "news_first" if news_leg["ts_ms"] <= vol_leg["ts_ms"] else "volume_first"

    if bool(getattr(app_settings, "majorsbot_newsevent_retrace_entry", True)):
        return await _place_retrace_limit(
            db, symbol, vol_leg, news_leg, md,
            direction=direction, eq=eq, lag_s=lag_s, leg_order=leg_order,
        )

    entry = strategies.adverse_slippage_price(
        direction, entry_raw, _dec(app_settings.majorsbot_slippage_pct)
    )
    sized = _size_and_levels(symbol, direction, entry, extreme, eq)
    if sized is None:
        return False
    qty, ref_stop, exit_stop, leverage, liq = sized

    trade = await executor.open_market_trade(
        db,
        symbol=symbol,
        direction=direction,
        strategy=strategies.NEWSEVENT,
        signal_at=_dt(int(vol_leg["ts_ms"])),
        entry_price=entry,
        entry_bar_at=_dt(int(last_bar["t"])),
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

async def _accrued_funding(trade: MajorsBotTrade, md: data.MarketData, exit_bar_ms: int) -> Decimal:
    """Funding transfer over the hold: every event with entry_bar < ts ≤
    exit_bar, priced at the containing 5m bar's open. Full qty until the
    partial bar (inclusive), runner qty after — engine._accrued_funding's
    two-leg split, on 5m bars.

    Not optional realism: a 6h hold crosses an 8h funding event ~75% of the
    time at max hold, and the delisting-short population pays extreme rates
    (Bybit moves volatile small caps to 1h funding intervals).
    """
    if trade.entry_bar_at is None:
        return Decimal("0")
    funding = await data.get_funding(trade.symbol)
    if not funding:
        return Decimal("0")

    entry_ms = int(trade.entry_bar_at.timestamp() * 1000)
    total_qty = _dec(trade.qty)
    partial_qty = _dec(trade.partial_qty) if trade.partial_qty is not None else Decimal("0")
    runner_qty = total_qty - partial_qty
    partial_bar_ms: int | None = None
    if trade.partial_exit_at is not None:
        # stored as bar CLOSE
        partial_bar_ms = int(trade.partial_exit_at.timestamp() * 1000) - strategies.NEWSEVENT_BAR_MS

    total = Decimal("0")
    for ts, rate in funding:
        if ts <= entry_ms or ts > exit_bar_ms:
            continue
        px = None
        for b in md.bars:
            if int(b["t"]) <= ts < int(b["t"]) + strategies.NEWSEVENT_BAR_MS:
                px = _dec(b["o"])
                break
        if px is None:
            px = _dec(trade.entry_price)
        qty = (
            total_qty
            if (partial_bar_ms is None or ts <= partial_bar_ms)
            else runner_qty
        )
        total += strategies.funding_event_pnl(trade.direction, _dec(rate), px, qty)
    return total


async def _walk_open(db, trade: MajorsBotTrade, md: data.MarketData) -> bool:
    """Advance one open newsevent position by REPLAYING all bars since entry.

    Replay, not resume: every tick re-derives the entry-time stop
    deterministically (liquidation price when stops are off, else the initial
    stop) and rebuilds the trail ratchet bar by bar. A previous version
    started each walk from the PERSISTED stop — one tick after the trail
    armed, the first post-entry bar (whose low sits near entry) retroactively
    "hit" the ratcheted level and closed every runner at ~+1R. Ratchet
    updates must only ever apply to LATER bars (strategies.py walk
    semantics); replaying from entry state guarantees that under
    re-execution, with no watermark to lose. trade.stop_price is display
    state only.

    Pessimistic within a bar: liquidation, then stop/trail, then TP. Stop and
    trail fills take the bake-off gap penalty (an open beyond the level fills
    at the open); liquidation books the BANKRUPTCY price — the exchange
    engine's outcome, not the liq trigger. Funding accrues on every close.
    """
    # Strictly after the entry bar. For a market entry that is exact (entry IS
    # that bar's close). For a retrace-limit fill it is mildly optimistic: the
    # remainder of the fill bar is never evaluated, so a liquidation contained
    # entirely within it would be missed. Not corrected here because the bar's
    # extremes may predate the fill, and assuming they didn't would be
    # pessimistic in the opposite direction — 5m OHLC cannot distinguish the
    # two. The following bar is checked normally one tick later.
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
    liq = strategies.liquidation_price(direction, entry, leverage, _mmr_for(trade.symbol))
    bankruptcy = strategies.bankruptcy_price(direction, entry, leverage)

    # Entry-time stop, re-derived — never read from the persisted (possibly
    # ratcheted) trade.stop_price. With stops off there is no protective
    # level; the trail creates one when it arms.
    stop: Decimal | None = initial_stop if stop_enabled else None
    trail_armed = False
    peak = entry
    # Recording-only max-favourable-excursion. Deliberately NOT `peak`: that
    # one drives the trail ratchet and is updated at the BOTTOM of the loop so
    # a ratchet can only bind on later bars. `mfe` updates at the top so the
    # persisted value includes the bar a liquidation or stop happened on.
    mfe = entry
    held = 0

    async def _close(raw_exit: Decimal, reason: str, bar_ms: int) -> None:
        funding_pnl = await _accrued_funding(trade, md, exit_bar_ms=bar_ms)
        # Persist MFE before the close writes the row — without it the DB
        # cannot answer "did this loser ever go green?" after the fact, and
        # every exit-rule question needs the bars refetched from the exchange.
        trade.peak_price = mfe
        await executor.close_trade(
            db, trade, raw_exit_price=raw_exit, reason=reason, funding_pnl=funding_pnl
        )

    for bar in bars:
        held += 1
        bar_ms = int(bar["t"])
        bar_open, high, low = _dec(bar["o"]), _dec(bar["h"]), _dec(bar["l"])
        mfe = max(mfe, high) if direction == "long" else min(mfe, low)

        if liq is not None:
            hit_liq = low <= liq if direction == "long" else high >= liq
            if hit_liq:
                await _close(
                    bankruptcy if bankruptcy is not None else liq,
                    strategies.CLOSE_LIQUIDATION,
                    bar_ms,
                )
                log.warning(
                    "newsevent_liquidated",
                    id=str(trade.id), symbol=trade.symbol,
                    direction=direction, entry=float(entry),
                    liquidation=float(liq), leverage=float(leverage),
                )
                return True

        if stop is not None:
            hit_stop = low <= stop if direction == "long" else high >= stop
            if hit_stop:
                raw = strategies.stop_fill_raw(direction, bar_open, stop, is_entry_bar=False)
                await _close(
                    raw,
                    strategies.CLOSE_TRAIL if trail_armed else strategies.CLOSE_STOP,
                    bar_ms,
                )
                return True

        if not took_partial:
            hit_tp = high >= tp if direction == "long" else low <= tp
            if hit_tp:
                await executor.take_partial_profit(
                    db, trade,
                    exit_price=tp,
                    bar_close_at=_dt(bar_ms + strategies.NEWSEVENT_BAR_MS),
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
            # Ratchet only — a trail never loosens; effective from the NEXT
            # bar, since this bar's checks already ran.
            if stop is None:
                stop = new_stop
            else:
                stop = max(stop, new_stop) if direction == "long" else min(stop, new_stop)
            trail_armed = True

        if held >= strategies.NEWSEVENT_MAX_HOLD_BARS:
            await _close(_dec(bar["c"]), strategies.CLOSE_MAX_HOLD, bar_ms)
            return True

    # Display state only — the next replay re-derives everything above.
    if trail_armed and stop is not None and _dec(trade.stop_price) != stop:
        trade.stop_price = stop
    trade.peak_price = mfe
    await db.commit()
    return False


# ---------- tick ----------

async def run_newsevent_tick() -> dict:
    """One pass: manage open positions, refresh both legs, fire any pairs."""
    totals = {
        "symbols": 0, "opened": 0, "closed": 0, "vol_legs": 0, "news_legs": 0,
        "placed": 0, "filled": 0, "cancelled": 0,
    }
    if not getattr(app_settings, "majorsbot_enabled", False):
        return totals
    if not getattr(app_settings, "majorsbot_newsevent_enabled", False):
        return totals

    window_s = strategies.NEWSEVENT_PAIR_WINDOW_S

    async with AsyncSessionLocal() as db:
        news_legs = await recent_news_legs(db, window_s)
        totals["news_legs"] = len(news_legs)

        live_rows = (
            await db.execute(
                select(MajorsBotTrade)
                .where(MajorsBotTrade.strategy == strategies.NEWSEVENT)
                .where(MajorsBotTrade.status.in_(("pending", "open")))
            )
        ).scalars().all()

        universe = await tradeable_universe(set(news_legs))
        # A live row must be driven even if its symbol left the universe —
        # otherwise a resting limit would never fill or expire.
        for row in live_rows:
            if row.symbol not in universe:
                universe.append(row.symbol)

        open_by_symbol = {r.symbol: r for r in live_rows if r.status == "open"}
        pending_by_symbol = {r.symbol: r for r in live_rows if r.status == "pending"}

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

                pending = pending_by_symbol.get(symbol)
                if pending is not None:
                    outcome = await _manage_pending(db, pending, md)
                    if outcome == "cancelled":
                        totals["cancelled"] += 1
                    elif outcome == "filled":
                        totals["filled"] += 1
                        # Walk the just-filled position in the same tick so a
                        # liquidation or trail inside the fill window is not
                        # deferred a full minute.
                        if await _walk_open(db, pending, md):
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
                    if await _try_enter(db, symbol, vol_leg, news_leg, md):
                        # retrace mode parks a limit; market mode opens now
                        if bool(getattr(app_settings, "majorsbot_newsevent_retrace_entry", True)):
                            totals["placed"] += 1
                        else:
                            totals["opened"] += 1
            except Exception as e:
                log.warning("newsevent_symbol_failed", symbol=symbol, err=str(e))

    if totals["opened"] or totals["closed"] or totals["vol_legs"] or totals["placed"] or totals["filled"]:
        log.info("newsevent_tick", **totals)
    return totals


async def run_newsevent_job() -> None:
    try:
        await run_newsevent_tick()
    except Exception as e:
        log.error("newsevent_tick_failed", error=str(e))
