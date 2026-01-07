"""
Billing endpoints
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
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
    Upgrade the organization's subscription plan
    """
    subscription_service = SubscriptionService(db)
    success = subscription_service.assign_plan_to_organization(
        current_user.organization_id, plan_name
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid plan name: {plan_name}",
        )

    return {"message": f"Subscription upgraded to {plan_name} successfully"}


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
