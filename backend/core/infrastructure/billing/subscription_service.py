"""
Subscription service for LeadBoost SaaS
"""

import os
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, func
from core.domain.models.subscription import Plan
from core.domain.models.billing import Subscription
from core.domain.models.organization import Organization
from core.domain.models.lead import Lead
from core.domain.schemas.subscription import PlanUsage


class SubscriptionService:
    def __init__(self, db: Session):
        self.db = db

    def get_organization_usage(self, organization_id: int) -> PlanUsage:
        """Get the current usage and limits for an organization"""
        # Get the subscription for the organization
        subscription = (
            self.db.query(Subscription)
            .filter(Subscription.organization_id == organization_id)
            .first()
        )

        # Determine plan name from subscription or default to 'free'
        plan_name = subscription.plan_name if subscription else "free"

        # Get plan limits from environment variables based on plan name
        if plan_name == "pro":
            max_leads_per_day = int(os.getenv("PRO_MAX_LEADS_PER_DAY", "500"))
            can_export = os.getenv("CAN_EXPORT_PRO", "false").lower() == "true"
            can_use_ai = os.getenv("CAN_USE_AI_PRO", "false").lower() == "true"
        elif plan_name == "enterprise":
            max_leads_per_day = int(os.getenv("ENTERPRISE_MAX_LEADS_PER_DAY", "10000"))
            can_export = os.getenv("CAN_EXPORT_ENTERPRISE", "false").lower() == "true"
            can_use_ai = os.getenv("CAN_USE_AI_ENTERPRISE", "false").lower() == "true"
        else:  # free plan
            max_leads_per_day = int(os.getenv("FREE_MAX_LEADS_PER_DAY", "10"))
            can_export = os.getenv("CAN_EXPORT_FREE", "false").lower() == "true"
            can_use_ai = os.getenv("CAN_USE_AI_FREE", "false").lower() == "true"

        current_usage = self._get_daily_usage(organization_id)
        remaining_daily_leads = max(0, max_leads_per_day - current_usage)
        can_process_more_today = remaining_daily_leads > 0

        return PlanUsage(
            plan_name=plan_name,
            max_leads_per_day=max_leads_per_day,
            can_export=can_export,
            can_use_ai=can_use_ai,
            current_usage=current_usage,
            remaining_daily_leads=remaining_daily_leads,
            can_process_more_today=can_process_more_today,
        )

    def _get_daily_usage(self, organization_id: int) -> int:
        """Get the number of leads created today for an organization"""
        today = datetime.utcnow().date()
        start_of_day = datetime.combine(today, datetime.min.time())
        end_of_day = datetime.combine(today, datetime.max.time())

        count = (
            self.db.query(Lead)
            .filter(
                and_(
                    Lead.organization_id == organization_id,
                    Lead.created_at >= start_of_day,
                    Lead.created_at <= end_of_day,
                )
            )
            .count()
        )

        return count

    def can_create_lead(self, organization_id: int) -> bool:
        """Check if an organization can create a new lead based on their plan"""
        usage = self.get_organization_usage(organization_id)
        return usage.can_process_more_today

    def can_use_ai_features(self, organization_id: int) -> bool:
        """Check if an organization can use AI features"""
        subscription = (
            self.db.query(Subscription)
            .filter(Subscription.organization_id == organization_id)
            .first()
        )

        plan_name = subscription.plan_name if subscription else "free"

        if plan_name == "pro":
            return os.getenv("CAN_USE_AI_PRO", "false").lower() == "true"
        elif plan_name == "enterprise":
            return os.getenv("CAN_USE_AI_ENTERPRISE", "false").lower() == "true"
        else:  # free plan
            return os.getenv("CAN_USE_AI_FREE", "false").lower() == "true"

    def can_export_data(self, organization_id: int) -> bool:
        """Check if an organization can export data"""
        subscription = (
            self.db.query(Subscription)
            .filter(Subscription.organization_id == organization_id)
            .first()
        )

        plan_name = subscription.plan_name if subscription else "free"

        if plan_name == "pro":
            return os.getenv("CAN_EXPORT_PRO", "false").lower() == "true"
        elif plan_name == "enterprise":
            return os.getenv("CAN_EXPORT_ENTERPRISE", "false").lower() == "true"
        else:  # free plan
            return os.getenv("CAN_EXPORT_FREE", "false").lower() == "true"

    def initialize_plans(self):
        """Initialize default plans in the database"""
        # Check if plans already exist
        existing_plans = self.db.query(Plan).count()
        if existing_plans > 0:
            return

        # Create default plans
        plans_data = [
            {
                "name": "free",
                "max_leads_per_day": int(os.getenv("FREE_MAX_LEADS_PER_DAY", "10")),
                "can_export": os.getenv("CAN_EXPORT_FREE", "false").lower() == "true",
                "can_use_ai": os.getenv("CAN_USE_AI_FREE", "false").lower() == "true",
            },
            {
                "name": "pro",
                "max_leads_per_day": int(os.getenv("PRO_MAX_LEADS_PER_DAY", "500")),
                "can_export": os.getenv("CAN_EXPORT_PRO", "false").lower() == "true",
                "can_use_ai": os.getenv("CAN_USE_AI_PRO", "false").lower() == "true",
            },
            {
                "name": "enterprise",
                "max_leads_per_day": int(
                    os.getenv("ENTERPRISE_MAX_LEADS_PER_DAY", "10000")
                ),
                "can_export": os.getenv("CAN_EXPORT_ENTERPRISE", "false").lower()
                == "true",
                "can_use_ai": os.getenv("CAN_USE_AI_ENTERPRISE", "false").lower()
                == "true",
            },
        ]

        for plan_data in plans_data:
            plan = Plan(**plan_data)
            self.db.add(plan)

        self.db.commit()

    def assign_plan_to_organization(self, organization_id: int, plan_name: str) -> bool:
        """Assign a plan to an organization"""
        # Validate plan name
        valid_plans = ["free", "pro", "enterprise"]
        if plan_name not in valid_plans:
            return False

        # Check if organization already has a subscription
        existing_subscription = (
            self.db.query(Subscription)
            .filter(Subscription.organization_id == organization_id)
            .first()
        )

        if existing_subscription:
            # Update existing subscription
            existing_subscription.plan_name = plan_name
            existing_subscription.status = "active"
        else:
            # Create new subscription
            subscription = Subscription(
                organization_id=organization_id,
                stripe_subscription_id=f"sub_{organization_id}_{plan_name}_{int(datetime.now().timestamp())}",
                plan_name=plan_name,
                status="active",
                current_period_start=datetime.now(),
                current_period_end=None,  # Ongoing subscription
            )
            self.db.add(subscription)

        self.db.commit()
        return True

    def cancel_subscription(
        self, organization_id: int, immediate: bool = False
    ) -> bool:
        """Cancel an organization's subscription"""
        subscription = (
            self.db.query(Subscription)
            .filter(Subscription.organization_id == organization_id)
            .first()
        )

        if not subscription:
            return False

        if immediate:
            # Cancel immediately - set status to canceled
            subscription.status = "canceled"
            subscription.cancel_at_period_end = False
        else:
            # Cancel at period end - set flag to cancel at end of current period
            subscription.cancel_at_period_end = True
            subscription.status = "active"  # Keep active until period end

        self.db.commit()
        return True
