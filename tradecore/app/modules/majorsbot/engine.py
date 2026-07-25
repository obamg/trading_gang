"""MajorsBot — tick orchestration.

One scheduler job every 5 minutes:

  1. per symbol, load klines + funding (Redis-cached, ~one REST hit/hour);
  2. manage pending limits (fill on touched bars, cancel at expiry) and open
     positions (band exits, stops, partials, trails) against every NEW
     completed 1h bar since the per-trade watermark;
  3. evaluate NEW entries only when a fresh completed bar exists for the
     symbol (majorsbot:last_bar:{symbol} gate) — volevent triggers on the
     latest completed bar, fundingfade on the newest unseen funding event.

Redis keys owned here:
  majorsbot:last_bar:{symbol}   last completed bar ts evaluated for entries
  majorsbot:ff_event:{symbol}   newest funding event ts already evaluated —
                                also bumped on fundingfade closes so events
                                that fired mid-hold are never entered late
  majorsbot:managed:{trade_id}  last bar ts walked for the trade (resume point)

Per-symbol failures are swallowed and logged; one bad symbol can't stall the
universe. Idempotent: watermarks + status guards mean a double-fired tick
cannot fill or close a trade twice.
"""
from __future__ import annotations

from bisect import bisect_left
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select

from app.config import settings as app_settings
from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.majorsbot import MajorsBotTrade
from app.modules.majorsbot import data, equity, executor, strategies
from app.services import redis_service

H1_MS = 3_600_000

LAST_BAR_KEY = "majorsbot:last_bar:{symbol}"
FF_EVENT_KEY = "majorsbot:ff_event:{symbol}"
MANAGED_KEY = "majorsbot:managed:{trade_id}"
_GATE_TTL_S = 7 * 24 * 3600
_MANAGED_TTL_S = 14 * 24 * 3600


def symbol_list() -> list[str]:
    return [
        s.strip().upper()
        for s in str(app_settings.majorsbot_symbols or "").split(",")
        if s.strip()
    ]


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _dec(v) -> Decimal:
    return Decimal(str(v))


# ---------- watermarks ----------


async def _get_watermark(trade_id) -> int | None:
    r = redis_service.get_redis()
    raw = await r.get(MANAGED_KEY.format(trade_id=trade_id))
    if raw is None:
        return None
    try:
        return int(raw.decode() if isinstance(raw, bytes) else raw)
    except (TypeError, ValueError):
        return None


async def _set_watermark(trade_id, ts_ms: int) -> None:
    r = redis_service.get_redis()
    await r.set(MANAGED_KEY.format(trade_id=trade_id), str(ts_ms), ex=_MANAGED_TTL_S)


async def _clear_watermark(trade_id) -> None:
    r = redis_service.get_redis()
    await r.delete(MANAGED_KEY.format(trade_id=trade_id))


# ---------- funding helpers ----------


def _pctile_at_event(
    funding: list[tuple[int, float]], event_ts: int
) -> float | None:
    """Trailing-90-event percentile of the event at ``event_ts`` (inclusive
    window, exactly the bake-off's W=90). None when the event is unknown or
    history is too shallow."""
    ts_list = [ts for ts, _ in funding]
    idx = bisect_left(ts_list, event_ts)
    if idx >= len(ts_list) or ts_list[idx] != event_ts:
        return None
    if idx < strategies.FUNDING_WINDOW_EVENTS - 1:
        return None
    window = [rate for _, rate in funding[idx - strategies.FUNDING_WINDOW_EVENTS + 1 : idx + 1]]
    return strategies.funding_percentile(window)


def _bar_open_at(bars: list[dict], ts: int) -> float | None:
    """Open of the bar at ``ts`` — exact match, else the first bar at/after it
    (clamped to the last bar), mirroring the bake-off's funding pricing."""
    if not bars:
        return None
    ts_list = [b["t"] for b in bars]
    idx = bisect_left(ts_list, ts)
    if idx >= len(bars):
        idx = len(bars) - 1
    return bars[idx]["o"]


def _accrued_funding(trade: MajorsBotTrade, md: data.MarketData, exit_bar_ms: int) -> Decimal:
    """Funding transfer over the hold: every 8h event with
    entry_bar < ts ≤ exit_bar, priced at that bar's open. Full qty until the
    partial bar (inclusive), runner qty after — the bake-off's two-leg split."""
    if not md.funding or trade.entry_bar_at is None:
        return Decimal("0")
    entry_ms = _ms(trade.entry_bar_at)
    total_qty = _dec(trade.qty)
    partial_qty = _dec(trade.partial_qty) if trade.partial_qty is not None else Decimal("0")
    runner_qty = total_qty - partial_qty
    partial_bar_ms: int | None = None
    if trade.partial_exit_at is not None:
        partial_bar_ms = _ms(trade.partial_exit_at) - H1_MS  # stored as bar CLOSE
    total = Decimal("0")
    for ts, rate in md.funding:
        if ts <= entry_ms or ts > exit_bar_ms:
            continue
        px = _bar_open_at(md.bars, ts)
        if px is None:
            continue
        qty = (
            total_qty
            if (partial_bar_ms is None or ts <= partial_bar_ms)
            else runner_qty
        )
        total += strategies.funding_event_pnl(trade.direction, _dec(rate), _dec(px), qty)
    return total


# ---------- DB seams (monkeypatch targets in tests) ----------


async def _get_trades(db, symbol: str) -> list[MajorsBotTrade]:
    rows = (
        await db.execute(
            select(MajorsBotTrade).where(
                MajorsBotTrade.symbol == symbol,
                MajorsBotTrade.status.in_(("pending", "open")),
            )
        )
    ).scalars().all()
    return list(rows)


async def _active_symbols(db) -> list[str]:
    rows = (
        await db.execute(
            select(MajorsBotTrade.symbol)
            .where(MajorsBotTrade.status.in_(("pending", "open")))
            .distinct()
        )
    ).scalars().all()
    return list(rows)


async def _count_open(db) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(MajorsBotTrade)
            .where(MajorsBotTrade.status == "open")
        )
    ).scalar_one()


# ---------- pending management ----------


async def _manage_pending(db, trade: MajorsBotTrade, md: data.MarketData, now: datetime) -> str:
    """One pending volevent limit → waiting | cancelled | filled | filled_closed."""
    direction = trade.direction
    limit = _dec(trade.limit_price)
    wm = await _get_watermark(trade.id)
    if wm is None:
        wm = _ms(trade.signal_at)  # trigger bar open — fills scan bars after it
    expire_ms = _ms(trade.expire_at) if trade.expire_at else None

    for bar in md.bars:
        t = int(bar["t"])
        if t <= wm:
            continue
        if expire_ms is not None and t >= expire_ms:
            break  # a bar opening at/after expiry can't fill — the order is gone
        high, low, bar_open = _dec(bar["h"]), _dec(bar["l"]), _dec(bar["o"])
        if not strategies.is_limit_touched(direction, high, low, limit):
            wm = t
            continue
        # Capacity gate at the fill transition (pendings never hold a slot).
        if await equity.get_concurrent_count() >= int(app_settings.majorsbot_max_concurrent):
            await executor.cancel_pending_order(
                db, trade, reason=strategies.CLOSE_MAX_CONCURRENT
            )
            await _clear_watermark(trade.id)
            return "cancelled"
        fill_px = strategies.limit_fill_price(direction, bar_open, limit)
        ext = _dec(trade.signal_low if direction == "long" else trade.signal_high)
        stop, risk = strategies.volevent_stop(direction, fill_px, ext)
        tp = strategies.take_profit_price(direction, fill_px, risk, strategies.VOLEVENT_TP_R)
        eq = await equity.get_paper_equity()
        qty = strategies.compute_qty(
            paper_equity=eq,
            risk_per_trade_pct=_dec(app_settings.majorsbot_risk_per_trade_pct),
            entry_price=fill_px,
            stop_price=stop,
            max_notional_pct=_dec(app_settings.majorsbot_position_size_pct),
        )
        if qty <= 0:
            await executor.cancel_pending_order(db, trade, reason="degenerate")
            await _clear_watermark(trade.id)
            return "cancelled"
        await executor.fill_pending_order(
            db,
            trade,
            fill_price=fill_px,
            stop_price=stop,
            take_profit_price=tp,
            qty=qty,
            entry_bar_at=_dt(t),
            paper_equity=eq,
        )
        # Walk resumes AT the fill bar (entry-bar stop fills exactly, no gap).
        await _set_watermark(trade.id, t - 1)
        outcome = await _walk_open(db, trade, md, now)
        return "filled_closed" if outcome == "closed" else "filled"

    if trade.expire_at is not None and now >= trade.expire_at:
        await executor.cancel_pending_order(db, trade)
        await _clear_watermark(trade.id)
        return "cancelled"
    await _set_watermark(trade.id, wm)
    return "waiting"


# ---------- open-position management ----------


async def _walk_open(db, trade: MajorsBotTrade, md: data.MarketData, now: datetime) -> str:
    """Advance one open position over every new completed bar → open | closed."""
    direction = trade.direction
    entry = _dec(trade.entry_price)
    initial_stop = _dec(
        trade.initial_stop_price if trade.initial_stop_price is not None else trade.stop_price
    )
    risk = abs(entry - initial_stop)
    entry_bar_ms = _ms(trade.entry_bar_at) if trade.entry_bar_at is not None else None
    wm = await _get_watermark(trade.id)
    if wm is None:
        wm = (entry_bar_ms - 1) if entry_bar_ms is not None else _ms(trade.entry_at)

    state = strategies.PositionState(
        stop=_dec(trade.stop_price),
        peak=_dec(trade.peak_price) if trade.peak_price is not None else None,
        partial_taken=trade.partial_exit_at is not None,
    )
    is_ff = trade.strategy == strategies.FUNDINGFADE
    tp = (
        _dec(trade.take_profit_price)
        if (not is_ff and trade.take_profit_price is not None)
        else None
    )
    trail_dist = strategies.trail_distance_for(trade.strategy, risk) if risk > 0 else None
    arm_r = strategies.FF_TRAIL_ARM_R if is_ff else strategies.VOLEVENT_TRAIL_ARM_R
    funding_ts = {ts for ts, _ in md.funding} if (is_ff and md.funding) else set()

    for bar in md.bars:
        t = int(bar["t"])
        if t <= wm:
            continue
        bar_open, high, low = _dec(bar["o"]), _dec(bar["h"]), _dec(bar["l"])
        is_entry_bar = entry_bar_ms is not None and t == entry_bar_ms

        # Band renormalization exits at the bar open, BEFORE the stop check and
        # never on the entry bar — bake-off ordering (k > j, band first).
        if is_ff and not is_entry_bar and t in funding_ts:
            pct = _pctile_at_event(md.funding, t)
            if pct is not None and strategies.in_normal_band(pct):
                await executor.close_trade(
                    db,
                    trade,
                    raw_exit_price=bar_open,
                    reason=strategies.CLOSE_FUNDING_NORM,
                    funding_pnl=_accrued_funding(trade, md, exit_bar_ms=t),
                )
                await _clear_watermark(trade.id)
                return "closed"

        state, events = strategies.step_position_bar(
            direction,
            entry,
            initial_stop,
            state,
            bar_open,
            high,
            low,
            is_entry_bar=is_entry_bar,
            tp_price=tp,
            trail_arm_r=arm_r,
            trail_distance=trail_dist,
        )
        for ev in events:
            if ev[0] == "partial":
                await executor.take_partial_profit(
                    db, trade, exit_price=ev[1], bar_close_at=_dt(t + H1_MS)
                )
            elif ev[0] == "close":
                await executor.close_trade(
                    db,
                    trade,
                    raw_exit_price=ev[1],
                    reason=ev[2],
                    funding_pnl=_accrued_funding(trade, md, exit_bar_ms=t),
                )
                await _clear_watermark(trade.id)
                return "closed"
        wm = t

    # Still open — persist ratchet state + resume point for the next tick.
    changed = False
    if state.stop != _dec(trade.stop_price):
        trade.stop_price = state.stop
        changed = True
    if state.peak is not None and (
        trade.peak_price is None or state.peak != _dec(trade.peak_price)
    ):
        trade.peak_price = state.peak
        changed = True
    if changed:
        await db.commit()
    await _set_watermark(trade.id, wm)

    # Safety net: force-close positions held past majorsbot_max_hold_hours.
    max_hold = int(app_settings.majorsbot_max_hold_hours or 0)
    if max_hold > 0:
        age_hours = (now - trade.entry_at).total_seconds() / 3600.0
        if age_hours >= max_hold and md.bars:
            last = md.bars[-1]
            await executor.close_trade(
                db,
                trade,
                raw_exit_price=_dec(last["c"]),
                reason=strategies.CLOSE_MAX_HOLD,
                funding_pnl=_accrued_funding(trade, md, exit_bar_ms=int(last["t"])),
            )
            await _clear_watermark(trade.id)
            return "closed"
    return "open"


# ---------- entry evaluation ----------


async def _evaluate_entries(
    db, symbol: str, md: data.MarketData, now: datetime, unavailable: set[str]
) -> dict:
    """Once per new completed bar: volevent trigger on the latest bar,
    fundingfade on the newest unseen funding event. ``unavailable`` holds
    strategies that already have a live row for the symbol or closed one this
    tick (the bake-off never re-enters on the exit bar)."""
    out = {"placed": 0, "opened": 0}
    r = redis_service.get_redis()
    max_concurrent = int(app_settings.majorsbot_max_concurrent)
    risk_pct = _dec(app_settings.majorsbot_risk_per_trade_pct)
    cap_pct = _dec(app_settings.majorsbot_position_size_pct)

    if (
        getattr(app_settings, "majorsbot_volevent_enabled", True)
        and strategies.VOLEVENT not in unavailable
    ):
        sig = strategies.volevent_signal(md.bars)
        if sig is not None:
            if await equity.get_concurrent_count() >= max_concurrent:
                log.info(
                    "majorsbot_signal_skipped",
                    symbol=symbol,
                    strategy=strategies.VOLEVENT,
                    reason="max_concurrent",
                )
            else:
                direction = sig["direction"]
                limit = _dec(sig["limit_price"])
                ext = _dec(
                    sig["trigger_low"] if direction == "long" else sig["trigger_high"]
                )
                stop_est, risk_est = strategies.volevent_stop(direction, limit, ext)
                eq = await equity.get_paper_equity()
                qty = strategies.compute_qty(
                    paper_equity=eq,
                    risk_per_trade_pct=risk_pct,
                    entry_price=limit,
                    stop_price=stop_est,
                    max_notional_pct=cap_pct,
                )
                if qty > 0:
                    trigger_ts = int(sig["trigger_ts"])
                    # Order works bars trigger+1h … trigger+6h; gone at +7h.
                    expire_at = _dt(
                        trigger_ts + (1 + strategies.VOLEVENT_FILL_WINDOW_HOURS) * H1_MS
                    )
                    trade = await executor.place_pending_order(
                        db,
                        symbol=symbol,
                        direction=direction,
                        strategy=strategies.VOLEVENT,
                        signal_at=_dt(trigger_ts),
                        signal_high=_dec(sig["trigger_high"]),
                        signal_low=_dec(sig["trigger_low"]),
                        limit_price=limit,
                        stop_price=stop_est,
                        take_profit_price=strategies.take_profit_price(
                            direction, limit, risk_est, strategies.VOLEVENT_TP_R
                        ),
                        qty=qty,
                        paper_equity=eq,
                        expire_at=expire_at,
                    )
                    await _set_watermark(trade.id, trigger_ts)
                    out["placed"] += 1

    if (
        getattr(app_settings, "majorsbot_fundingfade_enabled", True)
        and md.funding
        and strategies.FUNDINGFADE not in unavailable
    ):
        ev_ts, ev_rate = md.funding[-1]
        raw = await r.get(FF_EVENT_KEY.format(symbol=symbol))
        try:
            seen = int(raw.decode() if isinstance(raw, bytes) else raw) if raw else 0
        except (TypeError, ValueError):
            seen = 0
        if ev_ts > seen:
            await r.set(FF_EVENT_KEY.format(symbol=symbol), str(ev_ts), ex=_GATE_TTL_S)
            now_ms = _ms(now)
            pct = _pctile_at_event(md.funding, ev_ts)
            direction = (
                strategies.fundingfade_direction(pct) if pct is not None else None
            )
            if now_ms - ev_ts > strategies.FF_MAX_ENTRY_LAG_MS:
                if direction:
                    log.info(
                        "majorsbot_signal_skipped",
                        symbol=symbol,
                        strategy=strategies.FUNDINGFADE,
                        reason="stale_event",
                    )
            elif direction is not None:
                if await equity.get_concurrent_count() >= max_concurrent:
                    log.info(
                        "majorsbot_signal_skipped",
                        symbol=symbol,
                        strategy=strategies.FUNDINGFADE,
                        reason="max_concurrent",
                    )
                else:
                    entry_open = _event_bar_open(md, ev_ts)
                    atr = _atr_before(md.bars, ev_ts)
                    if entry_open is None or atr is None or atr <= 0:
                        log.warning(
                            "majorsbot_ff_entry_unpriceable",
                            symbol=symbol,
                            event_ts=ev_ts,
                        )
                    else:
                        entry = _dec(entry_open)
                        stop, _risk = strategies.fundingfade_stop(
                            direction, entry, _dec(atr)
                        )
                        eq = await equity.get_paper_equity()
                        qty = strategies.compute_qty(
                            paper_equity=eq,
                            risk_per_trade_pct=risk_pct,
                            entry_price=entry,
                            stop_price=stop,
                            max_notional_pct=cap_pct,
                        )
                        if qty > 0:
                            trade = await executor.open_market_trade(
                                db,
                                symbol=symbol,
                                direction=direction,
                                strategy=strategies.FUNDINGFADE,
                                signal_at=_dt(ev_ts),
                                entry_price=entry,
                                entry_bar_at=_dt(ev_ts),
                                stop_price=stop,
                                qty=qty,
                                paper_equity=eq,
                                funding_rate=_dec(ev_rate),
                                funding_pctile=_dec(round(pct, 4)),
                            )
                            # Walk starts AT the event bar (entry bar, exact-stop).
                            await _set_watermark(trade.id, ev_ts - 1)
                            out["opened"] += 1
    return out


def _event_bar_open(md: data.MarketData, event_ts: int) -> float | None:
    """Open of the 1h bar starting at the funding event — completed bar first,
    else the live (forming) bar when the event just landed."""
    for bar in reversed(md.bars):
        if bar["t"] == event_ts:
            return bar["o"]
        if bar["t"] < event_ts:
            break
    if md.live_bar and int(md.live_bar.get("t", -1)) == event_ts:
        return md.live_bar.get("o")
    return None


def _atr_before(bars: list[dict], event_ts: int) -> float | None:
    """ATR(24) of the window ending at the last bar BEFORE the event bar —
    the bake-off's atr24[j−1]."""
    ts_list = [b["t"] for b in bars]
    idx = bisect_left(ts_list, event_ts) - 1
    if idx < 0:
        return None
    return data.atr_at(bars, idx, strategies.FF_ATR_WINDOW)


# ---------- per-symbol + tick ----------


async def _process_symbol(db, symbol: str, now: datetime) -> dict:
    md = await data.get_market_data(symbol)
    if md is None or not md.bars:
        log.warning("majorsbot_no_data", symbol=symbol)
        return {"errors": 1}

    rows = await _get_trades(db, symbol)
    out = {"pending": 0, "filled": 0, "cancelled": 0, "closed": 0, "placed": 0, "opened": 0}
    closed_strategies: set[str] = set()

    for trade in [t for t in rows if t.status == "pending"]:
        out["pending"] += 1
        outcome = await _manage_pending(db, trade, md, now)
        if outcome in ("filled", "filled_closed"):
            out["filled"] += 1
        if outcome == "cancelled":
            out["cancelled"] += 1
        if outcome == "filled_closed":
            out["closed"] += 1
            closed_strategies.add(trade.strategy)

    for trade in [t for t in rows if t.status == "open"]:
        outcome = await _walk_open(db, trade, md, now)
        if outcome == "closed":
            out["closed"] += 1
            closed_strategies.add(trade.strategy)

    r = redis_service.get_redis()
    # A fundingfade close consumes every event up to now — events that fired
    # mid-hold must not be entered late (bake-off skips events during a hold).
    if strategies.FUNDINGFADE in closed_strategies and md.funding:
        await r.set(
            FF_EVENT_KEY.format(symbol=symbol), str(md.funding[-1][0]), ex=_GATE_TTL_S
        )

    latest_ts = int(md.bars[-1]["t"])
    raw = await r.get(LAST_BAR_KEY.format(symbol=symbol))
    try:
        last_eval = int(raw.decode() if isinstance(raw, bytes) else raw) if raw else None
    except (TypeError, ValueError):
        last_eval = None
    if last_eval != latest_ts:
        live = {t.strategy for t in rows if t.status in ("pending", "open")}
        res = await _evaluate_entries(db, symbol, md, now, live | closed_strategies)
        out["placed"] += res["placed"]
        out["opened"] += res["opened"]
        await r.set(LAST_BAR_KEY.format(symbol=symbol), str(latest_ts), ex=_GATE_TTL_S)
    return out


async def run_majorsbot_tick() -> dict:
    """Scheduler entry — swallows its own exceptions (scheduler convention)."""
    if not getattr(app_settings, "majorsbot_enabled", False):
        return {"skipped": "disabled"}
    now = datetime.now(timezone.utc)
    totals = {
        "symbols": 0,
        "pending": 0,
        "filled": 0,
        "cancelled": 0,
        "closed": 0,
        "placed": 0,
        "opened": 0,
        "errors": 0,
    }
    try:
        async with AsyncSessionLocal() as db:
            symbols = symbol_list()
            # Keep managing live rows whose symbol left the config list —
            # otherwise they orphan until max_hold.
            for extra in await _active_symbols(db):
                if extra not in symbols:
                    symbols.append(extra)
            for symbol in symbols:
                totals["symbols"] += 1
                try:
                    res = await _process_symbol(db, symbol, now)
                    for k, v in res.items():
                        totals[k] = totals.get(k, 0) + v
                except Exception as e:
                    totals["errors"] += 1
                    log.warning("majorsbot_symbol_failed", symbol=symbol, err=str(e))
            open_now = await _count_open(db)
        drift = await equity.reconcile_concurrent(int(open_now))
        if drift != 0:
            log.warning("majorsbot_concurrent_reconciled", drift=drift, actual=int(open_now))
        log.info("majorsbot_tick", **totals)
        return totals
    except Exception as e:
        log.error("majorsbot_tick_failed", err=str(e))
        return {"error": str(e)}
