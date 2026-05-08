"""CRUD for ExchangeCredential — encrypts on write, decrypts on read.

Plaintext secrets are only materialized inside `load_credentials()` and never
returned by the listing endpoint.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.exchange import ExchangeCredential
from app.services.encryption import decrypt, encrypt
from app.services.exchanges.base import Credentials


async def create_credential(
    db: AsyncSession,
    user_id: UUID,
    *,
    exchange: str,
    api_key: str,
    api_secret: str,
    passphrase: str | None = None,
    label: str = "default",
    permissions: dict | None = None,
    validated_at: datetime | None = None,
) -> ExchangeCredential:
    row = ExchangeCredential(
        user_id=user_id,
        exchange=exchange,
        label=label,
        api_key_enc=encrypt(api_key),
        api_secret_enc=encrypt(api_secret),
        passphrase_enc=encrypt(passphrase) if passphrase else None,
        permissions=permissions,
        validated_at=validated_at or datetime.now(timezone.utc),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def list_credentials(db: AsyncSession, user_id: UUID) -> list[ExchangeCredential]:
    rows = (
        await db.execute(
            select(ExchangeCredential)
            .where(ExchangeCredential.user_id == user_id)
            .order_by(ExchangeCredential.created_at.desc())
        )
    ).scalars().all()
    return list(rows)


async def get_credential(
    db: AsyncSession, user_id: UUID, cred_id: UUID
) -> ExchangeCredential | None:
    return (
        await db.execute(
            select(ExchangeCredential).where(
                ExchangeCredential.id == cred_id,
                ExchangeCredential.user_id == user_id,
            )
        )
    ).scalar_one_or_none()


async def delete_credential(db: AsyncSession, user_id: UUID, cred_id: UUID) -> bool:
    row = await get_credential(db, user_id, cred_id)
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    return True


def load_credentials(row: ExchangeCredential) -> Credentials:
    """Decrypt the stored ciphertext into a Credentials value object.

    Caller is responsible for not logging or persisting the result.
    """
    return Credentials(
        api_key=decrypt(row.api_key_enc),
        api_secret=decrypt(row.api_secret_enc),
        passphrase=decrypt(row.passphrase_enc) if row.passphrase_enc else None,
    )


def serialize(row: ExchangeCredential) -> dict:
    """Public representation. Never includes secrets."""
    return {
        "id": str(row.id),
        "exchange": row.exchange,
        "label": row.label,
        "is_active": row.is_active,
        "permissions": row.permissions,
        "validated_at": row.validated_at.isoformat() if row.validated_at else None,
        "last_synced_at": row.last_synced_at.isoformat() if row.last_synced_at else None,
        "last_sync_error": row.last_sync_error,
        "created_at": row.created_at.isoformat(),
    }


__all__ = [
    "create_credential",
    "list_credentials",
    "get_credential",
    "delete_credential",
    "load_credentials",
    "serialize",
]
