"""DayTrade scanner — one tick a minute.

Pulls 5m klines for a short list of liquid majors, evaluates each session's
opening range (playbook.evaluate), writes a paper plan the moment a setup
triggers, and resolves open plans against the bars that followed.

Nothing here places an order. TradeCore has no order routing (see CLAUDE.md);
a plan is an alert plus a row in `daytrade_plans` whose outcome is simulated.

Redis:
    daytrade:board   JSON string, TTL 600s — {"at", "exchange", "setups": [...]}
                     the live state of every (symbol, session) for the desk UI
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import desc, func, or_, select

from app.config import settings
from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.daytrade import DayTradePlan
from app.models.macro import EconomicEvent
from app.models.news import NewsArticle
from app.models.oracle import OracleSignal
from app.modules.daytrade import ai
from app.modules.daytrade.playbook import Playbook, build_plan, evaluate, gate, resolve
from app.modules.riskcalc.calculator import RiskInput, calculate_position, funding_settlements
from app.services import redis_service

BOARD_KEY = "daytrade:board"
BOARD_TTL = 600
KLINE_LIMIT = 600  # 50h of 5m bars: covers the 20h trend EMA plus a full session
PLAYBOOK = Playbook()
CLOSED = ("target", "stop", "time")


def symbols() -> list[str]:
    return [s.strip().upper() for s in settings.daytrade_symbols.split(",") if s.strip()]


def exchange() -> str:
    return "binance" if settings.market_data_source == "binance" else "bybit"


async def fetch_klines(client: httpx.AsyncClient, ex: str, symbol: str) -> list[dict]:
    """5m bars, oldest first, as {t, o, h, l, c, v}. Public endpoints, no key."""
    if ex == "binance":
        r = await client.get(
            f"{settings.binance_rest_url}/fapi/v1/klines",
            params={"symbol": symbol, "interval": "5m", "limit": KLINE_LIMIT},
            timeout=15,
        )
        r.raise_for_status()
        raw = r.json() or []
    else:
        r = await client.get(
            f"{settings.bybit_rest_url}/v5/market/kline",
            params={"category": "linear", "symbol": symbol, "interval": "5", "limit": KLINE_LIMIT},
            timeout=15,
        )
        r.raise_for_status()
        raw = ((r.json() or {}).get("result") or {}).get("list") or []
    rows = [
        {"t": int(k[0]), "o": float(k[1]), "h": float(k[2]), "l": float(k[3]), "c": float(k[4]), "v": float(k[5])}
        for k in raw
    ]
    rows.sort(key=lambda x: x["t"])  # Bybit returns newest first
    return rows


def day_stats(plans: list[DayTradePlan], now: datetime, session_at: datetime | None = None) -> dict:
    """Today's (UTC) usage of the risk budget, for playbook.gate. *session_at*
    adds the count of plans already taken in that session."""
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today = [p for p in plans if p.status != "blocked" and p.triggered_at >= start]
    closed = sorted((p for p in today if p.status in CLOSED), key=lambda p: p.closed_at)
    streak = 0
    for p in reversed(closed):
        if float(p.pnl_r) >= 0:
            break
        streak += 1
    return {
        "trades": len(today),
        "session_trades": sum(1 for p in plans if p.status != "blocked" and p.session_at == session_at),
        "loss_r": sum(-float(p.pnl_r) for p in closed if float(p.pnl_r) < 0),
        "open_symbols": [p.symbol for p in plans if p.status == "open"],
        "streak": streak,
    }


async def _context(db, symbol: str, rows: list[dict], now: datetime) -> dict:
    """What the AI reviewer sees beyond the plan itself. Three small queries."""
    base = symbol.removesuffix("USDT")
    news = (
        await db.execute(
            select(NewsArticle.title, NewsArticle.sentiment, NewsArticle.coins, NewsArticle.published_at)
            .where(NewsArticle.published_at >= now - timedelta(hours=6))
            .order_by(desc(NewsArticle.published_at))
            .limit(200)
        )
    ).all()
    # Exact ticker match on the comma list — a substring test would attribute
    # every "ETHFI" headline to ETH.
    headlines = [
        {"title": n.title, "sentiment": n.sentiment, "minutes_ago": int((now - n.published_at).total_seconds() // 60)}
        for n in news
        if base in {c.strip().upper() for c in (n.coins or "").split(",")}
    ][:8]
    sig = (
        await db.execute(
            select(OracleSignal.score, OracleSignal.recommendation, OracleSignal.confluence_count)
            .where(OracleSignal.symbol == symbol, OracleSignal.signal_at >= now - timedelta(hours=2))
            .order_by(desc(OracleSignal.signal_at))
            .limit(1)
        )
    ).first()
    events = (
        await db.execute(
            select(EconomicEvent.name, EconomicEvent.scheduled_at)
            .where(
                func.lower(EconomicEvent.impact) == "high",
                EconomicEvent.scheduled_at >= now,
                EconomicEvent.scheduled_at <= now + timedelta(hours=PLAYBOOK.expire_hours),
            )
            .order_by(EconomicEvent.scheduled_at)
            .limit(5)
        )
    ).all()
    day_ago = rows[-289]["c"] if len(rows) > 289 else None
    return {
        "change_24h_pct": round((rows[-1]["c"] / day_ago - 1) * 100, 2) if day_ago else None,
        "recent_news": headlines,
        "oracle": {"score": sig.score, "recommendation": sig.recommendation, "confluence": sig.confluence_count}
        if sig else None,
        "high_impact_events_ahead": [
            {"name": e.name, "in_minutes": int((e.scheduled_at - now).total_seconds() // 60)} for e in events
        ],
    }


async def _open_plan(db, ex: str, st: dict, rows: list[dict], plans: list[DayTradePlan], now: datetime):
    pb = PLAYBOOK
    symbol = st["symbol"]
    plan, reasons = build_plan(st["side"], st["entry"], st["range_high"], st["range_low"], pb)
    if not plan:
        log.warning("daytrade_plan_invalid", symbol=symbol, reasons=reasons)
        return None
    exit_by = datetime.fromisoformat(st["expires"])
    funding = await redis_service.get_funding_rate(symbol)
    sizing: dict = {}
    try:
        sizing = calculate_position(RiskInput(
            account_balance_usd=settings.daytrade_account_usd,
            risk_pct=pb.risk_pct,
            entry_price=plan["entry"],
            stop_loss_price=plan["stop"],
            take_profit_price=plan["target"],
            max_leverage=pb.max_leverage,
            side=plan["side"].lower(),
            taker_fee_pct=pb.taker_fee_pct,
            stop_slippage_pct=pb.stop_slippage_pct,
            funding_rate=funding,
            funding_settlements=funding_settlements(now, (exit_by - now).total_seconds() / 3600),
        ))
        reasons += [w.removeprefix("BLOCKED: ") for w in sizing["warnings"] if w.startswith("BLOCKED")]
    except ValueError as e:
        reasons.append(str(e))
    session_at = datetime.fromisoformat(st["anchor"])
    reasons += gate(symbol, day_stats(plans, now, session_at), pb)

    row = DayTradePlan(
        exchange=ex, symbol=symbol, session_at=session_at, playbook=pb.version,
        side=plan["side"], status="blocked" if reasons else "open",
        entry=plan["entry"], stop=plan["stop"], target=plan["target"],
        range_high=st["range_high"], range_low=st["range_low"],
        stop_pct=plan["stop_pct"], vol_x=st["vol_x"], funding_rate=funding,
        checks={"reasons": reasons, "sizing": sizing},
        triggered_at=datetime.fromtimestamp(st["trigger_close_ms"] / 1000, tz=timezone.utc),
        exit_by=exit_by,
    )
    if not reasons:
        try:
            ctx = await _context(db, symbol, rows, now)
            verdict = await ai.get_verdict({
                "symbol": symbol, "exchange": ex, "session_utc": st["session"],
                "plan": {**plan, "reward_risk": pb.target_r, "exit_by": st["expires"]},
                "range": {"high": st["range_high"], "low": st["range_low"]},
                "trigger_volume_x": st["vol_x"],
                "funding_rate_per_8h": funding,
                **ctx,
            })
            if verdict:
                row.ai_verdict, row.ai_reason = verdict.verdict, verdict.reason
        except Exception as e:  # the verdict is optional; the plan is not
            log.warning("daytrade_ai_failed", symbol=symbol, err=str(e))
    db.add(row)
    log.info("daytrade_plan", symbol=symbol, side=row.side, status=row.status, reasons=reasons, ai=row.ai_verdict)
    if not reasons:
        await redis_service.publish_alert("daytrade", {
            "type": "plan_open", "exchange": ex, "symbol": symbol, "side": row.side,
            "entry": plan["entry"], "stop": plan["stop"], "target": plan["target"],
            "stop_pct": round(plan["stop_pct"], 2), "vol_x": st["vol_x"], "session": st["session"].replace("@", " "),
            "exit_by": st["expires"], "size_usd": sizing.get("position_size_usd"),
            "leverage": sizing.get("leverage"), "account_usd": settings.daytrade_account_usd,
            "ai_verdict": row.ai_verdict, "ai_reason": row.ai_reason,
        })
    return row


async def run_daytrade_tick() -> dict[str, int]:
    """One pass: resolve open plans, evaluate every setup, publish the board."""
    if not settings.daytrade_enabled:
        return {"skipped": 1}
    try:
        return await _tick()
    except Exception as e:  # never take the scheduler down
        log.error("daytrade_tick_failed", err=str(e))
        return {"failed": 1}


async def _tick() -> dict[str, int]:
    now = datetime.now(timezone.utc)
    ex, syms = exchange(), symbols()
    async with httpx.AsyncClient() as client:
        fetched = await asyncio.gather(*(fetch_klines(client, ex, s) for s in syms), return_exceptions=True)
    klines: dict[str, list[dict]] = {}
    for s, res in zip(syms, fetched):
        if isinstance(res, Exception):
            log.warning("daytrade_klines_failed", symbol=s, err=str(res))
        elif res:
            klines[s] = res

    opened = closed = 0
    board: list[dict] = []
    async with AsyncSessionLocal() as db:
        plans = list((
            # Open plans are loaded whatever their age: after a long outage a
            # stale open plan must still be resolved and still count as risk.
            await db.execute(select(DayTradePlan).where(or_(
                DayTradePlan.triggered_at >= now - timedelta(days=2), DayTradePlan.status == "open",
            )))
        ).scalars().all())

        for p in plans:
            if p.status != "open" or p.symbol not in klines:
                continue
            out = resolve(
                p.side, float(p.entry), float(p.stop), float(p.target),
                int(p.triggered_at.timestamp() * 1000), p.exit_by, klines[p.symbol], now, PLAYBOOK,
            )
            if out is None:
                continue
            p.status, p.exit_price, p.pnl_r, p.closed_at = out[0], out[1], round(out[2], 3), now
            closed += 1
            await redis_service.publish_alert("daytrade", {
                "type": "plan_closed", "exchange": p.exchange, "symbol": p.symbol, "side": p.side,
                "result": out[0], "exit_price": out[1], "pnl_r": round(out[2], 2),
            })

        by_session = {(p.symbol, p.session_at): p for p in plans}
        for sym in syms:
            rows = klines.get(sym)
            if not rows:
                continue
            for hhmm in PLAYBOOK.sessions:
                st = evaluate(rows, now, hhmm, PLAYBOOK)
                st["symbol"] = sym
                existing = by_session.get((sym, datetime.fromisoformat(st["anchor"])))
                if st["status"] == "triggered" and existing is None:
                    existing = await _open_plan(db, ex, st, rows, plans, now)
                    if existing is not None:
                        plans.append(existing)
                        by_session[(sym, existing.session_at)] = existing
                        opened += 1
                if existing is not None:
                    # A plan on record outranks the freshness window: the
                    # setup did trigger, the board should keep saying so.
                    st["status"], st["plan_status"] = "triggered", existing.status
                board.append(st)
        await db.commit()

    r = redis_service.get_redis()
    await r.set(BOARD_KEY, json.dumps({"at": now.isoformat(), "exchange": ex, "setups": board}), ex=BOARD_TTL)
    log.info("daytrade_tick", symbols=len(klines), setups=len(board), opened=opened, closed=closed)
    return {"setups": len(board), "opened": opened, "closed": closed}
