"""Awakening REST endpoints — read-only view over the recent Redis log."""
from __future__ import annotations

import json

from fastapi import APIRouter, Query

from app.dependencies import CurrentUser
from app.modules.awakening.detector import RECENT_LIST
from app.services import redis_service

router = APIRouter(prefix="/awakening", tags=["awakening"])


@router.get("/recent")
async def list_recent(
    _user: CurrentUser,
    limit: int = Query(50, ge=1, le=200),
):
    r = redis_service.get_redis()
    raw = await r.lrange(RECENT_LIST, 0, limit - 1) or []
    items = []
    for entry in raw:
        try:
            items.append(json.loads(entry))
        except (TypeError, ValueError):
            continue
    return {"items": items}
