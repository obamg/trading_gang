"""Connected-exchange API.

  GET    /exchanges/supported            — registered adapters + form metadata
  GET    /exchanges/credentials          — list user's stored credentials
  POST   /exchanges/credentials          — validate & store a new key
  DELETE /exchanges/credentials/{id}     — remove a stored key
  POST   /exchanges/credentials/{id}/sync — trigger sync now

Plaintext secrets only enter the validation step; on success they're
encrypted via Fernet before persistence. The list/get responses never
include any secret material.
"""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.dependencies import CurrentUser, DBSession
from app.errors import AppError
from app.rate_limit import limiter
from app.services.exchanges import REGISTRY  # noqa: F401  - ensure adapters import
from app.services.exchanges import binance, bybit  # noqa: F401  - register adapters
from app.services.exchanges.base import get_adapter, supported_exchanges
from app.services.exchanges.credentials import (
    create_credential,
    delete_credential,
    get_credential,
    list_credentials,
    serialize,
)
from app.services.exchanges.sync import sync_credential

router = APIRouter(prefix="/exchanges", tags=["exchanges"])


class CredentialCreate(BaseModel):
    exchange: str = Field(min_length=2, max_length=30)
    api_key: str = Field(min_length=8, max_length=200)
    api_secret: str = Field(min_length=8, max_length=400)
    passphrase: str | None = Field(default=None, max_length=200)
    label: str = Field(default="default", min_length=1, max_length=50)


@router.get("/supported")
async def supported(_user: CurrentUser):
    """List registered adapters. Frontend renders the connect form from this."""
    return {"items": supported_exchanges()}


@router.get("/credentials")
async def list_creds(user: CurrentUser, db: DBSession):
    rows = await list_credentials(db, user.id)
    return {"items": [serialize(r) for r in rows]}


@router.post("/credentials")
@limiter.limit("10/hour")
async def create_creds(
    request: Request, payload: CredentialCreate, user: CurrentUser, db: DBSession
):
    try:
        adapter = get_adapter(payload.exchange)
    except ValueError:
        raise AppError(400, f"Unsupported exchange: {payload.exchange}", "UNSUPPORTED_EXCHANGE")

    if adapter.requires_passphrase and not payload.passphrase:
        raise AppError(400, f"{adapter.display_name} requires a passphrase", "PASSPHRASE_REQUIRED")

    # Validate live before persisting. Any permission/auth error propagates
    # back to the user as a 400 — we never store an unverified key.
    from app.services.exchanges.base import Credentials
    creds = Credentials(
        api_key=payload.api_key,
        api_secret=payload.api_secret,
        passphrase=payload.passphrase,
    )
    try:
        permissions = await adapter.validate(creds)
    except Exception as exc:
        raise AppError(400, f"Key validation failed: {exc}", "KEY_VALIDATION_FAILED")

    row = await create_credential(
        db,
        user.id,
        exchange=payload.exchange,
        api_key=payload.api_key,
        api_secret=payload.api_secret,
        passphrase=payload.passphrase,
        label=payload.label,
        permissions=permissions,
        validated_at=datetime.now(timezone.utc),
    )
    return serialize(row)


@router.delete("/credentials/{cred_id}")
async def remove_creds(cred_id: UUID, user: CurrentUser, db: DBSession):
    ok = await delete_credential(db, user.id, cred_id)
    if not ok:
        raise AppError(404, "Credential not found", "NOT_FOUND")
    return {"deleted": True}


@router.post("/credentials/{cred_id}/sync")
@limiter.limit("12/hour")
async def sync_now(
    request: Request, cred_id: UUID, user: CurrentUser, db: DBSession
):
    cred = await get_credential(db, user.id, cred_id)
    if cred is None:
        raise AppError(404, "Credential not found", "NOT_FOUND")
    result = await sync_credential(db, user.id, cred_id)
    if not result.get("ok"):
        raise AppError(502, result.get("reason", "sync_failed"), "SYNC_FAILED")
    return result
