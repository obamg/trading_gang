"""Stripe integration: checkout, portal, webhook event handling."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.errors import AppError
from app.logging_config import log
from app.models.billing import Invoice, Plan, Subscription
from app.models.user import User

stripe.api_key = settings.stripe_secret_key


# ---------- price-id <-> plan lookup ----------

def _price_ids_for(plan_name: str) -> dict[str, str]:
    if plan_name == "pro":
        return {
            "monthly": settings.stripe_pro_price_id_monthly,
            "yearly": settings.stripe_pro_price_id_yearly,
        }
    if plan_name == "elite":
        return {
            "monthly": settings.stripe_elite_price_id_monthly,
            "yearly": settings.stripe_elite_price_id_yearly,
        }
    raise AppError(400, f"Unknown plan: {plan_name}", "UNKNOWN_PLAN")


def _all_price_id_to_plan() -> dict[str, tuple[str, str]]:
    """Returns {price_id: (plan_name, cycle)} for webhook lookup."""
    return {
        settings.stripe_pro_price_id_monthly: ("pro", "monthly"),
        settings.stripe_pro_price_id_yearly: ("pro", "yearly"),
        settings.stripe_elite_price_id_monthly: ("elite", "monthly"),
        settings.stripe_elite_price_id_yearly: ("elite", "yearly"),
    }


async def _get_or_create_customer(db: AsyncSession, user: User) -> str:
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == user.id).limit(1)
    )
    sub = result.scalar_one_or_none()
    if sub and sub.stripe_customer_id:
        return sub.stripe_customer_id

    customer = stripe.Customer.create(
        email=user.email,
        name=user.full_name or user.email,
        metadata={"user_id": str(user.id)},
    )
    if sub:
        sub.stripe_customer_id = customer.id
        await db.flush()
    return customer.id


# ---------- checkout + portal ----------

async def create_checkout_session(
    db: AsyncSession,
    user: User,
    plan_name: str,
    billing_cycle: str,
) -> dict:
    prices = _price_ids_for(plan_name)
    price_id = prices[billing_cycle]
    if price_id.endswith("placeholder"):
        raise AppError(503, "Stripe pricing not configured", "STRIPE_NOT_CONFIGURED")

    customer_id = await _get_or_create_customer(db, user)
    await db.commit()

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{settings.frontend_url}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{settings.frontend_url}/billing/cancel",
        metadata={"user_id": str(user.id), "plan": plan_name, "cycle": billing_cycle},
    )
    return {"checkout_url": session.url, "session_id": session.id}


async def create_portal_session(db: AsyncSession, user: User) -> str:
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == user.id).limit(1)
    )
    sub = result.scalar_one_or_none()
    if sub is None or not sub.stripe_customer_id:
        raise AppError(404, "No Stripe customer for this user", "NO_CUSTOMER")

    portal = stripe.billing_portal.Session.create(
        customer=sub.stripe_customer_id,
        return_url=f"{settings.frontend_url}/billing",
    )
    return portal.url


# ---------- webhook ----------

def verify_webhook(payload: bytes, sig_header: str | None) -> stripe.Event:
    if not sig_header:
        raise AppError(400, "Missing Stripe-Signature header", "INVALID_SIGNATURE")
    try:
        return stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret
        )
    except ValueError as e:
        raise AppError(400, "Invalid webhook payload", "INVALID_SIGNATURE") from e
    except stripe.SignatureVerificationError as e:  # type: ignore[attr-defined]
        raise AppError(400, "Invalid webhook signature", "INVALID_SIGNATURE") from e


def _ts_to_dt(ts: int | None) -> datetime | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc)


async def _plan_by_name(db: AsyncSession, name: str) -> Plan | None:
    result = await db.execute(select(Plan).where(Plan.name == name))
    return result.scalar_one_or_none()


async def _find_user_by_customer(db: AsyncSession, customer_id: str) -> UUID | None:
    result = await db.execute(
        select(Subscription).where(Subscription.stripe_customer_id == customer_id).limit(1)
    )
    sub = result.scalar_one_or_none()
    if sub:
        return sub.user_id
    # Fallback to Stripe metadata
    try:
        customer = stripe.Customer.retrieve(customer_id)
        uid = customer.get("metadata", {}).get("user_id")
        return UUID(uid) if uid else None
    except Exception:
        return None


async def _upsert_subscription_from_stripe(
    db: AsyncSession,
    stripe_sub: dict,
) -> None:
    items = stripe_sub.get("items", {}).get("data", [])
    if not items:
        return
    price_id = items[0]["price"]["id"]
    mapping = _all_price_id_to_plan()
    if price_id not in mapping:
        log.warning("unknown_stripe_price", price_id=price_id)
        return
    plan_name, cycle = mapping[price_id]

    plan = await _plan_by_name(db, plan_name)
    if plan is None:
        log.error("plan_not_found", plan=plan_name)
        return

    customer_id = stripe_sub["customer"]
    user_id = await _find_user_by_customer(db, customer_id)
    if user_id is None:
        log.error("no_user_for_customer", customer_id=customer_id)
        return

    result = await db.execute(
        select(Subscription).where(Subscription.stripe_subscription_id == stripe_sub["id"])
    )
    sub = result.scalar_one_or_none()

    if sub is None:
        sub = Subscription(
            user_id=user_id,
            plan_id=plan.id,
            stripe_subscription_id=stripe_sub["id"],
            stripe_customer_id=customer_id,
            status=stripe_sub["status"],
            billing_cycle=cycle,
            current_period_start=_ts_to_dt(stripe_sub.get("current_period_start")),
            current_period_end=_ts_to_dt(stripe_sub.get("current_period_end")),
            trial_end=_ts_to_dt(stripe_sub.get("trial_end")),
        )
        db.add(sub)
    else:
        sub.plan_id = plan.id
        sub.status = stripe_sub["status"]
        sub.billing_cycle = cycle
        sub.current_period_start = _ts_to_dt(stripe_sub.get("current_period_start"))
        sub.current_period_end = _ts_to_dt(stripe_sub.get("current_period_end"))
        sub.trial_end = _ts_to_dt(stripe_sub.get("trial_end"))
        if stripe_sub.get("canceled_at"):
            sub.cancelled_at = _ts_to_dt(stripe_sub["canceled_at"])


async def _handle_subscription_deleted(db: AsyncSession, stripe_sub: dict) -> None:
    result = await db.execute(
        select(Subscription).where(Subscription.stripe_subscription_id == stripe_sub["id"])
    )
    sub = result.scalar_one_or_none()
    if sub is None:
        return
    sub.status = "cancelled"
    sub.cancelled_at = datetime.now(timezone.utc)

    # Downgrade user back to free plan
    free = await _plan_by_name(db, "free")
    if free:
        db.add(Subscription(
            user_id=sub.user_id,
            plan_id=free.id,
            status="active",
            billing_cycle="monthly",
            stripe_customer_id=sub.stripe_customer_id,
        ))


async def _handle_invoice(db: AsyncSession, stripe_invoice: dict, paid: bool) -> None:
    result = await db.execute(
        select(Invoice).where(Invoice.stripe_invoice_id == stripe_invoice["id"])
    )
    if result.scalar_one_or_none() is not None:
        return  # idempotent

    customer_id = stripe_invoice.get("customer")
    user_id = await _find_user_by_customer(db, customer_id) if customer_id else None
    if user_id is None:
        return

    sub_result = await db.execute(
        select(Subscription).where(
            Subscription.stripe_subscription_id == stripe_invoice.get("subscription")
        )
    )
    sub = sub_result.scalar_one_or_none()

    db.add(Invoice(
        user_id=user_id,
        subscription_id=sub.id if sub else None,
        stripe_invoice_id=stripe_invoice["id"],
        amount_usd=Decimal(stripe_invoice.get("amount_paid", 0)) / Decimal(100),
        status="paid" if paid else stripe_invoice.get("status", "open"),
        invoice_pdf_url=stripe_invoice.get("invoice_pdf"),
        period_start=_ts_to_dt(stripe_invoice.get("period_start")),
        period_end=_ts_to_dt(stripe_invoice.get("period_end")),
    ))


async def handle_webhook_event(db: AsyncSession, event: stripe.Event) -> None:
    etype = event["type"]
    data = event["data"]["object"]
    log.info("stripe_webhook", type=etype, id=event.get("id"))

    if etype in ("customer.subscription.created", "customer.subscription.updated"):
        await _upsert_subscription_from_stripe(db, data)
    elif etype == "customer.subscription.deleted":
        await _handle_subscription_deleted(db, data)
    elif etype == "invoice.payment_succeeded":
        await _handle_invoice(db, data, paid=True)
    elif etype == "invoice.payment_failed":
        await _handle_invoice(db, data, paid=False)
    else:
        log.debug("unhandled_stripe_event", type=etype)


# ---------- current subscription lookup ----------

async def get_current_subscription_with_plan(
    db: AsyncSession, user_id: UUID
) -> tuple[Subscription, Plan] | None:
    result = await db.execute(
        select(Subscription, Plan)
        .join(Plan, Plan.id == Subscription.plan_id)
        .where(Subscription.user_id == user_id, Subscription.status == "active")
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    row = result.first()
    if row is None:
        return None
    return row[0], row[1]
