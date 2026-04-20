"""Reusable FastAPI dependencies: db session, current user."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.errors import AppError
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


