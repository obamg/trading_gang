"""User, Session, EmailVerification, PasswordReset."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    auth_provider: Mapped[str] = mapped_column(String(50), default="email", server_default="email", nullable=False)
    provider_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    sessions: Mapped[list["Session"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()

    user: Mapped["User"] = relationship(back_populates="sessions")


class EmailVerification(Base):
    __tablename__ = "email_verifications"

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_col()


class PasswordReset(Base):
    __tablename__ = "password_resets"

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_col()
