from __future__ import annotations

from fastapi import APIRouter, Query

from app.dependencies import CurrentUser
from app.modules.cmcpulse import collector
from app.services import cmc_client

router = APIRouter(prefix="/cmcpulse", tags=["cmcpulse"])


@router.get("/credits")
async def cmc_credits(_user: CurrentUser):
    """CMC key status + credit spend.

    ``estimated_used`` is our own running counter; ``plan`` is CMC's
    authoritative figure from the free ``/v1/key/info`` endpoint. The point
    is to see the monthly limit approaching rather than discover it as a
    wall of 429s.
    """
    out: dict = {
        "enabled": cmc_client.enabled(),
        "estimated_used_this_month": await cmc_client.credits_used_this_month(),
        "estimated_budget": cmc_client.BASIC_MONTHLY_CREDITS,
        "plan": None,
    }
    info = await cmc_client.key_info()
    if info:
        out["plan"] = info.get("plan")
        out["usage"] = info.get("usage")
    return out


@router.get("/context")
async def current_context(_user: CurrentUser, symbol: str | None = Query(None, max_length=40)):
    """Current Fear & Greed + trending entry for an optional symbol."""
    return await collector.get_context(symbol)
