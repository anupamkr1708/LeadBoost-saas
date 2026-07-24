"""
Billing endpoints
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session


from core.infrastructure.database import get_db
from core.infrastructure.auth.security import get_current_user
from core.domain.models.user import User


from core.infrastructure.logging import get_logger
from core.infrastructure.billing.subscription_service import SubscriptionService
from core.domain.schemas.subscription import PlanUsage

logger = get_logger(__name__)

router = APIRouter()


@router.get("/usage")
async def get_usage(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PlanUsage:
    """
    Get current organization's usage and subscription information
    """
    subscription_service = SubscriptionService(db)
    usage = subscription_service.get_organization_usage(current_user.organization_id)
    return usage


@router.post("/upgrade")
async def upgrade_plan(
    plan_name: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Online payments are not live yet, so this endpoint intentionally does
    NOT change the organization's plan. Stripe isn't wired up (see
    core.infrastructure.billing.stripe_service -- it exists but has no
    webhook endpoint registered), so accepting a plan_name here and
    calling assign_plan_to_organization directly would let any
    authenticated user unlock Pro/Enterprise limits and AI/export
    features for free, with no payment ever taking place, just by
    calling this endpoint (or changing frontend state). A real upgrade
    will happen through a verified Stripe Checkout + webhook flow once
    that's wired up; assign_plan_to_organization should only ever be
    called from there (or from signup, for the default free plan).
    """
    valid_plans = {"free", "pro", "enterprise"}
    if plan_name not in valid_plans:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid plan name: {plan_name}",
        )

    logger.info(
        f"Upgrade requested but not activated (online payments not live): "
        f"organization_id={current_user.organization_id} plan={plan_name}"
    )

    return JSONResponse(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        content={
            "message": "Online payments coming soon.",
            "activated": False,
            "requested_plan": plan_name,
        },
    )


@router.get("/plans")
async def get_available_plans(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get all available subscription plans
    """
    from core.domain.models.subscription import Plan

    plans = db.query(Plan).all()
    return plans


@router.post("/cancel")
async def cancel_subscription(
    immediate: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Cancel the organization's subscription
    """
    subscription_service = SubscriptionService(db)
    success = subscription_service.cancel_subscription(
        current_user.organization_id, immediate
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to cancel subscription",
        )

    return {"message": "Subscription cancelled successfully"}
