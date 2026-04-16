"""Billing endpoints — checkout, portal, webhook, current subscription."""
from fastapi import APIRouter, Header, Request, status
from fastapi.responses import JSONResponse

from app.dependencies import CurrentUser, DBSession
from app.errors import AppError
from app.schemas.billing import (
    CheckoutRequest,
    CheckoutResponse,
    PlanResponse,
    PortalResponse,
    SubscriptionResponse,
)
from app.services import billing_service

router = APIRouter(prefix="/billing", tags=["billing"])


@router.post("/create-checkout", response_model=CheckoutResponse)
async def create_checkout(
    payload: CheckoutRequest,
    user: CurrentUser,
    db: DBSession,
):
    data = await billing_service.create_checkout_session(
        db, user, payload.plan, payload.billing_cycle
    )
    await db.commit()
    return CheckoutResponse(**data)


@router.post("/portal", response_model=PortalResponse)
async def open_portal(user: CurrentUser, db: DBSession):
    url = await billing_service.create_portal_session(db, user)
    return PortalResponse(portal_url=url)


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def stripe_webhook(
    request: Request,
    db: DBSession,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
):
    payload = await request.body()
    event = billing_service.verify_webhook(payload, stripe_signature)
    try:
        await billing_service.handle_webhook_event(db, event)
        await db.commit()
    except AppError:
        raise
    except Exception:
        await db.rollback()
        # Return 500 so Stripe retries
        return JSONResponse(status_code=500, content={"error": "handler_failed", "code": "WEBHOOK_HANDLER_FAILED"})
    return {"received": True}


@router.get("/subscription", response_model=SubscriptionResponse)
async def get_subscription(user: CurrentUser, db: DBSession):
    result = await billing_service.get_current_subscription_with_plan(db, user.id)
    if result is None:
        raise AppError(404, "No active subscription", "NO_SUBSCRIPTION")
    sub, plan = result
    return SubscriptionResponse(
        id=sub.id,
        plan=PlanResponse.model_validate(plan),
        status=sub.status,
        billing_cycle=sub.billing_cycle,
        current_period_start=sub.current_period_start,
        current_period_end=sub.current_period_end,
        trial_end=sub.trial_end,
        cancelled_at=sub.cancelled_at,
    )
