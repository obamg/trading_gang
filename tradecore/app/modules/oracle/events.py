"""Incoming market-moving events for Oracle's Top Picks.

Warn-only: nothing here changes a score. Events are stored with their time,
so a later eval can join them to ``oracle_signals.signal_at`` directly —
an event earns a weight the same way any input does (ORACLE_SIGNAL_HARNESS).
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.macro import EconomicEvent

EVENTS_HORIZON_HOURS = 48
PICK_WINDOW_HOURS = 4          # Oracle calls are judged at 4h
DERIBIT_EXPIRY = time(8, 0, tzinfo=timezone.utc)


def _last_friday(year: int, month: int) -> date:
    last = date(year, month, calendar.monthrange(year, month)[1])
    return last - timedelta(days=(last.weekday() - 4) % 7)


def options_expiries(now: datetime, hours: int) -> list[dict]:
    """Deribit monthly BTC/ETH expiries (last Friday, 08:00 UTC) in [now, now+hours]."""
    out = []
    y, m = now.year, now.month
    for _ in range(2):  # this month and next cover any horizon < 4 weeks
        at = datetime.combine(_last_friday(y, m), DERIBIT_EXPIRY)
        if now <= at <= now + timedelta(hours=hours):
            quarterly = m in (3, 6, 9, 12)
            out.append({
                "name": f"{'Quarterly' if quarterly else 'Monthly'} BTC/ETH options expiry",
                "kind": "options",
                "impact": "high" if quarterly else "medium",
                "scheduled_at": at,
            })
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


async def upcoming_events(db: AsyncSession, now: datetime, hours: int = EVENTS_HORIZON_HOURS) -> list[dict]:
    rows = (await db.execute(
        select(EconomicEvent)
        .where(EconomicEvent.scheduled_at.between(now, now + timedelta(hours=hours)))
        .where(EconomicEvent.impact.in_(("high", "medium")))
    )).scalars().all()
    events = [
        {"name": r.name, "kind": "macro", "impact": r.impact, "scheduled_at": r.scheduled_at}
        for r in rows
    ] + options_expiries(now, hours)
    events.sort(key=lambda e: e["scheduled_at"])
    return events


def in_window(events: list[dict], now: datetime, hours: int = PICK_WINDOW_HOURS) -> list[dict]:
    """Events landing inside a pick's holding window. All are market-wide today."""
    end = now + timedelta(hours=hours)
    return [e for e in events if now <= e["scheduled_at"] <= end]


def serialize(e: dict) -> dict:
    return {**e, "scheduled_at": e["scheduled_at"].isoformat()}
