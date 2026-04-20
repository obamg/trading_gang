"""Google OAuth2 — authorization code flow."""
from __future__ import annotations

from urllib.parse import urlencode
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.errors import AppError
from app.models.settings import UserSettings, Watchlist
from app.models.user import User

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def build_authorization_url(state: str) -> str:
    if not settings.google_client_id:
        raise AppError(503, "Google OAuth is not configured", "OAUTH_NOT_CONFIGURED")
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_code_for_userinfo(code: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        token_resp = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_redirect_uri,
            "grant_type": "authorization_code",
        })
        if token_resp.status_code != 200:
            raise AppError(400, "Google token exchange failed", "OAUTH_EXCHANGE_FAILED")
        access = token_resp.json().get("access_token")
        if not access:
            raise AppError(400, "No access token from Google", "OAUTH_EXCHANGE_FAILED")

        info_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access}"},
        )
        if info_resp.status_code != 200:
            raise AppError(400, "Failed to fetch Google profile", "OAUTH_EXCHANGE_FAILED")
        return info_resp.json()


async def find_or_create_google_user(db: AsyncSession, userinfo: dict) -> User:
    email = userinfo.get("email")
    google_sub = userinfo.get("sub")
    if not email or not google_sub:
        raise AppError(400, "Google profile missing email", "OAUTH_INVALID_PROFILE")

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is not None:
        if not user.provider_id:
            user.provider_id = google_sub
            user.auth_provider = "google"
        return user

    user = User(
        email=email,
        full_name=userinfo.get("name"),
        avatar_url=userinfo.get("picture"),
        is_verified=bool(userinfo.get("email_verified", True)),
        auth_provider="google",
        provider_id=google_sub,
    )
    db.add(user)
    await db.flush()
    db.add(UserSettings(user_id=user.id))
    db.add(Watchlist(user_id=user.id, name="Default", symbols=[], is_default=True))
    await db.flush()
    return user
