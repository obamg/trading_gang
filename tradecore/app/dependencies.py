"""Reusable FastAPI dependencies: db session, current user, plan guards."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.errors import AppError
from app.models.billing import Plan, Subscription
from app.models.user import User
from app.services.auth_service import decode_access_token

DBSession = Annotated[AsyncSession, Depends(get_db)]


async def _current_user_from_header(
    authorization: str | None,
    db: AsyncSession,
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AppError(401, "Missing authorization header", "UNAUTHORIZED")
    token = authorization.split(" ", 1)[1].strip()
    user_id = decode_access_token(token)
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise AppError(401, "User no longer exists", "UNAUTHORIZED")
    if not user.is_active:
        raise AppError(403, "Account is disabled", "ACCOUNT_DISABLED")
    return user


async def get_current_user(
    db: DBSession,
    authorization: str | None = Header(default=None),
) -> User:
    return await _current_user_from_header(authorization, db)


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_verified_user(user: CurrentUser) -> User:
    if not user.is_verified:
        raise AppError(403, "Email not verified", "EMAIL_NOT_VERIFIED")
    return user


async def user_has_access(db: AsyncSession, user_id, feature_name: str) -> bool:
    """Returns True if the user's active plan has `feature_name` set to True."""
    result = await db.execute(
        select(Plan)
        .join(Subscription, Subscription.plan_id == Plan.id)
        .where(Subscription.user_id == user_id, Subscription.status == "active")
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    plan = result.scalar_one_or_none()
    if plan is None:
        return False
    return bool(plan.features.get(feature_name, False))


def require_feature(feature_name: str):
    """Dependency factory — guards a route behind a plan feature flag."""
    async def _guard(user: CurrentUser, db: DBSession) -> User:
        if not await user_has_access(db, user.id, feature_name):
            raise AppError(
                403,
                f"Your plan does not include '{feature_name}'",
                "FEATURE_NOT_AVAILABLE",
            )
        return user
    return _guard
