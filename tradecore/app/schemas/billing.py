"""Billing schemas."""
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class CheckoutRequest(BaseModel):
    plan: Literal["pro", "elite"]
    billing_cycle: Literal["monthly", "yearly"] = "monthly"


class CheckoutResponse(BaseModel):
    checkout_url: str
    session_id: str


class PortalResponse(BaseModel):
    portal_url: str


class PlanResponse(BaseModel):
    id: UUID
    name: str
    display_name: str
    price_monthly_usd: Decimal
    price_yearly_usd: Decimal
    features: dict
    max_watchlist_size: int
    alert_delay_seconds: int

    model_config = {"from_attributes": True}


class SubscriptionResponse(BaseModel):
    id: UUID
    plan: PlanResponse
    status: str
    billing_cycle: str
    current_period_start: datetime | None
    current_period_end: datetime | None
    trial_end: datetime | None
    cancelled_at: datetime | None

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    message: str = Field(...)
