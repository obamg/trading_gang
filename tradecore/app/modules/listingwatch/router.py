"""ListingWatch REST endpoints — read-only views over new_listing_events
and listing_signals.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import desc, select

from app.dependencies import CurrentUser, DBSession
from app.models.listing import ListingSignal, NewListingEvent

router = APIRouter(prefix="/listings", tags=["listingwatch"])


def _serialize_event(e: NewListingEvent) -> dict:
    return {
        "id": str(e.id),
        "exchange": e.exchange,
        "market_type": e.market_type,
        "symbol": e.symbol,
        "base_asset": e.base_asset,
        "quote_asset": e.quote_asset,
        "is_cross_listing": e.is_cross_listing,
        "other_exchanges": e.other_exchanges,
        "detected_at": e.detected_at.isoformat() if e.detected_at else None,
        "listed_at": e.listed_at.isoformat() if e.listed_at else None,
        "watcher_ends_at": e.watcher_ends_at.isoformat() if e.watcher_ends_at else None,
        "t0_price": float(e.t0_price) if e.t0_price is not None else None,
        "last_price": float(e.last_price) if e.last_price is not None else None,
        "high_15m": float(e.high_15m) if e.high_15m is not None else None,
        "low_15m": float(e.low_15m) if e.low_15m is not None else None,
        "high_1h": float(e.high_1h) if e.high_1h is not None else None,
        "low_1h": float(e.low_1h) if e.low_1h is not None else None,
        "last_funding_pct": float(e.last_funding_pct) if e.last_funding_pct is not None else None,
        "signal_count": e.signal_count,
        "status": e.status,
    }


def _serialize_signal(s: ListingSignal) -> dict:
    return {
        "id": str(s.id),
        "listing_id": str(s.listing_id),
        "signal_type": s.signal_type,
        "direction": s.direction,
        "conviction": float(s.conviction),
        "price_at_emit": float(s.price_at_emit) if s.price_at_emit is not None else None,
        "seconds_since_t0": s.seconds_since_t0,
        "context": s.context,
        "emitted_at": s.emitted_at.isoformat() if s.emitted_at else None,
    }


@router.get("/active")
async def list_active(_user: CurrentUser, db: DBSession):
    """All listings currently being watched."""
    rows = (
        await db.execute(
            select(NewListingEvent)
            .where(NewListingEvent.status == "watching")
            .order_by(desc(NewListingEvent.detected_at))
        )
    ).scalars().all()
    return {"items": [_serialize_event(e) for e in rows]}


@router.get("/recent")
async def list_recent(
    _user: CurrentUser,
    db: DBSession,
    days: int = Query(7, ge=1, le=30),
):
    """Listings detected in the last N days, ended or still active."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        await db.execute(
            select(NewListingEvent)
            .where(NewListingEvent.detected_at >= cutoff)
            .order_by(desc(NewListingEvent.detected_at))
            .limit(200)
        )
    ).scalars().all()
    return {"items": [_serialize_event(e) for e in rows]}


@router.get("/{listing_id}")
async def listing_detail(listing_id: UUID, _user: CurrentUser, db: DBSession):
    """A single listing + its signal history."""
    event = (
        await db.execute(select(NewListingEvent).where(NewListingEvent.id == listing_id))
    ).scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    signals = (
        await db.execute(
            select(ListingSignal)
            .where(ListingSignal.listing_id == listing_id)
            .order_by(ListingSignal.emitted_at)
        )
    ).scalars().all()
    return {
        "event": _serialize_event(event),
        "signals": [_serialize_signal(s) for s in signals],
    }
