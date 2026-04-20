"""Auth business logic: registration, login, token rotation, email verification."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.errors import AppError
from app.models.settings import UserSettings, Watchlist
from app.models.user import EmailVerification, PasswordReset, Session, User
from app.services import email_service

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------- password hashing ----------

def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ---------- token helpers ----------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_access_token(user_id: UUID) -> tuple[str, int]:
    ttl_seconds = settings.jwt_access_ttl_minutes * 60
    payload = {
        "sub": str(user_id),
        "type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        "iat": datetime.now(timezone.utc),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, ttl_seconds


def decode_access_token(token: str) -> UUID:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != "access":
            raise AppError(401, "Invalid token type", "INVALID_TOKEN")
        return UUID(payload["sub"])
    except (JWTError, KeyError, ValueError) as e:
        raise AppError(401, "Invalid or expired token", "INVALID_TOKEN") from e


def create_refresh_token() -> str:
    """Opaque random token (not JWT) — stored hashed in `sessions`."""
    return secrets.token_urlsafe(48)


# ---------- session management ----------

async def _store_session(
    db: AsyncSession,
    user_id: UUID,
    refresh_token: str,
    ip: str | None,
    user_agent: str | None,
) -> Session:
    session = Session(
        user_id=user_id,
        token_hash=_hash_token(refresh_token),
        ip_address=ip,
        user_agent=user_agent,
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_ttl_days),
    )
    db.add(session)
    await db.flush()
    return session


async def issue_tokens(
    db: AsyncSession,
    user_id: UUID,
    ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[str, str, int]:
    access, ttl = create_access_token(user_id)
    refresh = create_refresh_token()
    await _store_session(db, user_id, refresh, ip, user_agent)
    return access, refresh, ttl


async def rotate_refresh_token(
    db: AsyncSession,
    refresh_token: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[str, str, int]:
    token_hash = _hash_token(refresh_token)
    result = await db.execute(select(Session).where(Session.token_hash == token_hash))
    session = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if session is None or session.expires_at < now:
        raise AppError(401, "Invalid or expired refresh token", "INVALID_REFRESH_TOKEN")
    user_id = session.user_id
    await db.delete(session)
    await db.flush()
    return await issue_tokens(db, user_id, ip, user_agent)


async def invalidate_refresh_token(db: AsyncSession, refresh_token: str) -> None:
    token_hash = _hash_token(refresh_token)
    result = await db.execute(select(Session).where(Session.token_hash == token_hash))
    session = result.scalar_one_or_none()
    if session:
        await db.delete(session)


# ---------- registration / login ----------

async def register_user(
    db: AsyncSession,
    email: str,
    password: str,
    full_name: str | None,
) -> User:
    result = await db.execute(select(User).where(User.email == email))
    if result.scalar_one_or_none() is not None:
        raise AppError(409, "Email is already registered", "EMAIL_EXISTS")

    user = User(
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        auth_provider="email",
    )
    db.add(user)
    await db.flush()

    # Create default user settings row
    db.add(UserSettings(user_id=user.id))
    db.add(Watchlist(user_id=user.id, name="Default", symbols=[], is_default=True))

    # Issue email verification token
    raw_token = secrets.token_urlsafe(32)
    db.add(EmailVerification(
        user_id=user.id,
        token_hash=_hash_token(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    ))
    await db.commit()
    await db.refresh(user)

    email_service.send_verification_email(user.email, raw_token)
    return user


async def authenticate(db: AsyncSession, email: str, password: str) -> User:
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None or not user.password_hash or not verify_password(password, user.password_hash):
        raise AppError(401, "Invalid email or password", "INVALID_CREDENTIALS")
    if not user.is_active:
        raise AppError(403, "Account is disabled", "ACCOUNT_DISABLED")
    return user


# ---------- email verification ----------

async def verify_email(db: AsyncSession, token: str) -> User:
    token_hash = _hash_token(token)
    result = await db.execute(
        select(EmailVerification).where(EmailVerification.token_hash == token_hash)
    )
    record = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if record is None or record.used_at is not None or record.expires_at < now:
        raise AppError(400, "Invalid or expired verification token", "INVALID_VERIFICATION_TOKEN")
    record.used_at = now

    user_result = await db.execute(select(User).where(User.id == record.user_id))
    user = user_result.scalar_one()
    user.is_verified = True
    await db.commit()
    await db.refresh(user)
    return user


# ---------- password reset ----------

async def request_password_reset(db: AsyncSession, email: str) -> None:
    """Always succeeds silently, to prevent email enumeration."""
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        return
    raw_token = secrets.token_urlsafe(32)
    db.add(PasswordReset(
        user_id=user.id,
        token_hash=_hash_token(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ))
    await db.commit()
    email_service.send_password_reset_email(user.email, raw_token)


async def reset_password(db: AsyncSession, token: str, new_password: str) -> None:
    token_hash = _hash_token(token)
    result = await db.execute(
        select(PasswordReset).where(PasswordReset.token_hash == token_hash)
    )
    record = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if record is None or record.used_at is not None or record.expires_at < now:
        raise AppError(400, "Invalid or expired reset token", "INVALID_RESET_TOKEN")
    record.used_at = now

    user_result = await db.execute(select(User).where(User.id == record.user_id))
    user = user_result.scalar_one()
    user.password_hash = hash_password(new_password)

    # Invalidate all existing sessions on password change
    existing = await db.execute(select(Session).where(Session.user_id == user.id))
    for s in existing.scalars().all():
        await db.delete(s)
    await db.commit()
