"""User settings — notification preferences, Telegram linking, thresholds."""
from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter
from sqlalchemy import select

from app.dependencies import CurrentUser, DBSession
from app.models.settings import UserSettings
from app.services.telegram_service import service as telegram_service

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsResponse(BaseModel):
    telegram_enabled: bool = False
    telegram_linked: bool = False
    radarx_zscore_threshold: float = 3.0
    radarx_ratio_threshold: float = 4.0
    radarx_min_volume_usd: float = 10_000_000
    radarx_cooldown_minutes: int = 30
    whaleradar_min_trade_usd: float = 300_000
    whaleradar_min_onchain_usd: float = 500_000
    gemradar_min_mcap_usd: float = 1_000_000
    gemradar_max_mcap_usd: float = 100_000_000
    oracle_min_score: int = 65
    oracle_min_confluence: int = 4


class SettingsUpdate(BaseModel):
    telegram_enabled: bool | None = None
    radarx_zscore_threshold: float | None = None
    radarx_ratio_threshold: float | None = None
    radarx_min_volume_usd: float | None = None
    radarx_cooldown_minutes: int | None = None
    whaleradar_min_trade_usd: float | None = None
    whaleradar_min_onchain_usd: float | None = None
    gemradar_min_mcap_usd: float | None = None
    gemradar_max_mcap_usd: float | None = None
    oracle_min_score: int | None = None
    oracle_min_confluence: int | None = None


async def _get_or_create(db: DBSession, user: CurrentUser) -> UserSettings:
    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == user.id)
    )
    us = result.scalar_one_or_none()
    if us is None:
        us = UserSettings(user_id=user.id)
        db.add(us)
        await db.commit()
        await db.refresh(us)
    return us


@router.get("", response_model=SettingsResponse)
async def get_settings(user: CurrentUser, db: DBSession):
    us = await _get_or_create(db, user)
    return SettingsResponse(
        telegram_enabled=us.telegram_enabled,
        telegram_linked=bool(us.telegram_chat_id),
        radarx_zscore_threshold=float(us.radarx_zscore_threshold),
        radarx_ratio_threshold=float(us.radarx_ratio_threshold),
        radarx_min_volume_usd=float(us.radarx_min_volume_usd),
        radarx_cooldown_minutes=us.radarx_cooldown_minutes,
        whaleradar_min_trade_usd=float(us.whaleradar_min_trade_usd),
        whaleradar_min_onchain_usd=float(us.whaleradar_min_onchain_usd),
        gemradar_min_mcap_usd=float(us.gemradar_min_mcap_usd),
        gemradar_max_mcap_usd=float(us.gemradar_max_mcap_usd),
        oracle_min_score=us.oracle_min_score,
        oracle_min_confluence=us.oracle_min_confluence,
    )


@router.patch("", response_model=SettingsResponse)
async def update_settings(body: SettingsUpdate, user: CurrentUser, db: DBSession):
    us = await _get_or_create(db, user)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(us, field, value)
    await db.commit()
    await db.refresh(us)
    return await get_settings(user, db)


@router.post("/telegram/link-token")
async def create_telegram_link_token(user: CurrentUser, db: DBSession):
    await _get_or_create(db, user)
    token = await telegram_service.create_link_token(user.id)
    return {"token": token}


@router.delete("/telegram")
async def unlink_telegram(user: CurrentUser, db: DBSession):
    us = await _get_or_create(db, user)
    us.telegram_chat_id = None
    us.telegram_enabled = False
    await db.commit()
    return {"ok": True}
