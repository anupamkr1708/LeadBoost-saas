"""
Stripe integration for billing and subscription management
"""

import stripe
import os
from typing import Dict, Any, Optional
from datetime import datetime

from core.domain.models.organization import Organization
from core.domain.models.billing import Subscription, UsageRecord
from core.infrastructure.database import get_db
from core.infrastructure.database.crud import (
    get_organization,
    create_subscription,
    create_usage_record,
    get_subscription_by_organization,
)
from core.infrastructure.logging import get_logger

logger = get_logger(__name__)

# Initialize Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")


class StripeService:
    """
    Service class for handling Stripe integration
    """

    def __init__(self):
        self.webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    def create_customer(self, organization: Organization) -> Optional[str]:
        """
        Create a Stripe customer for an organization
        """
        try:
            customer = stripe.Customer.create(
                name=organization.name,
                email=organization.users[0].email if organization.users else None,
                description=f"Organization {organization.id}",
                metadata={"organization_id": str(organization.id)},
            )

            # Update organization with Stripe customer ID
            organization.stripe_customer_id = customer.id

            logger.info(
                f"Created Stripe customer {customer.id} for organization {organization.id}"
            )
            return customer.id

        except Exception as e:
            logger.error(
                f"Failed to create Stripe customer for organization {organization.id}: {str(e)}"
            )
            return None

    def create_subscription(
        self, organization_id: int, plan_id: str
    ) -> Optional[Subscription]:
        """
        Create a subscription for an organization
        """
        db = next(get_db())
        try:
            organization = get_organization(db, organization_id)
            if not organization:
                logger.error(f"Organization {organization_id} not found")
                return None

            # Create customer if not exists
            if not organization.stripe_customer_id:
                customer_id = self.create_customer(organization)
                if not customer_id:
                    logger.error(
                        f"Failed to create customer for organization {organization_id}"
                    )
                    return None

            # Create subscription
            subscription = stripe.Subscription.create(
                customer=organization.stripe_customer_id,
                items=[{"price": plan_id}],
                metadata={"organization_id": str(organization_id)},
            )

            # Create local subscription record
            local_subscription = create_subscription(
                db,
                organization_id=organization_id,
                stripe_subscription_id=subscription.id,
                plan_name=self._get_plan_name_from_id(plan_id),
            )

            logger.info(
                f"Created subscription {subscription.id} for organization {organization_id}"
            )
            return local_subscription

        except Exception as e:
            logger.error(
                f"Failed to create subscription for organization {organization_id}: {str(e)}"
            )
            return None
        finally:
            db.close()

    def update_subscription(self, organization_id: int, new_plan_id: str) -> bool:
        """
        Update an organization's subscription plan
        """
        db = next(get_db())
        try:
            subscription = get_subscription_by_organization(db, organization_id)
            if not subscription:
                logger.error(
                    f"No subscription found for organization {organization_id}"
                )
                return False

            # Get Stripe subscription
            stripe_subscription = stripe.Subscription.retrieve(
                subscription.stripe_subscription_id
            )

            # Update subscription
            updated_subscription = stripe.Subscription.modify(
                stripe_subscription.id,
                items=[
                    {
                        "id": stripe_subscription.items.data[0].id,
                        "price": new_plan_id,
                    }
                ],
            )

            # Update local record
            subscription.plan_name = self._get_plan_name_from_id(new_plan_id)
            subscription.updated_at = datetime.utcnow()
            db.commit()

            logger.info(
                f"Updated subscription {subscription.stripe_subscription_id} for organization {organization_id}"
            )
            return True

        except Exception as e:
            logger.error(
                f"Failed to update subscription for organization {organization_id}: {str(e)}"
            )
            return False
        finally:
            db.close()

    def cancel_subscription(
        self, organization_id: int, immediate: bool = False
    ) -> bool:
        """
        Cancel an organization's subscription
        """
        db = next(get_db())
        try:
            subscription = get_subscription_by_organization(db, organization_id)
            if not subscription:
                logger.error(
                    f"No subscription found for organization {organization_id}"
                )
                return False

            if immediate:
                # Cancel immediately
                stripe.Subscription.delete(subscription.stripe_subscription_id)
            else:
                # Cancel at period end
                stripe.Subscription.modify(
                    subscription.stripe_subscription_id, cancel_at_period_end=True
                )
                subscription.cancel_at_period_end = True
                db.commit()

            logger.info(
                f"Cancelled subscription {subscription.stripe_subscription_id} for organization {organization_id}"
            )
            return True

        except Exception as e:
            logger.error(
                f"Failed to cancel subscription for organization {organization_id}: {str(e)}"
            )
            return False
        finally:
            db.close()

    def handle_webhook(self, payload: str, sig_header: str) -> bool:
        """
        Handle Stripe webhook events
        """
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, self.webhook_secret
            )

            event_type = event["type"]
            event_data = event["data"]["object"]

            if event_type == "customer.subscription.created":
                self._handle_subscription_created(event_data)
            elif event_type == "customer.subscription.updated":
                self._handle_subscription_updated(event_data)
            elif event_type == "customer.subscription.deleted":
                self._handle_subscription_deleted(event_data)
            elif event_type == "invoice.payment_succeeded":
                self._handle_payment_succeeded(event_data)
            elif event_type == "invoice.payment_failed":
                self._handle_payment_failed(event_data)

            logger.info(f"Handled Stripe webhook event: {event_type}")
            return True

        except Exception as e:
            logger.error(f"Failed to handle Stripe webhook: {str(e)}")
            return False

    def _handle_subscription_created(self, subscription_data: Dict[str, Any]):
        """
        Handle subscription created event
        """
        organization_id = int(
            subscription_data.get("metadata", {}).get("organization_id", 0)
        )
        if organization_id:
            db = next(get_db())
            try:
                # Update local subscription record if it exists, or create new one
                local_subscription = get_subscription_by_organization(
                    db, organization_id
                )
                if local_subscription:
                    local_subscription.stripe_subscription_id = subscription_data["id"]
                    local_subscription.plan_name = self._get_plan_name_from_price(
                        subscription_data["items"]["data"][0]["price"]["id"]
                    )
                    local_subscription.status = subscription_data["status"]
                    db.commit()
                else:
                    create_subscription(
                        db,
                        organization_id=organization_id,
                        stripe_subscription_id=subscription_data["id"],
                        plan_name=self._get_plan_name_from_price(
                            subscription_data["items"]["data"][0]["price"]["id"]
                        ),
                    )
            finally:
                db.close()

    def _handle_subscription_updated(self, subscription_data: Dict[str, Any]):
        """
        Handle subscription updated event
        """
        organization_id = int(
            subscription_data.get("metadata", {}).get("organization_id", 0)
        )
        if organization_id:
            db = next(get_db())
            try:
                local_subscription = get_subscription_by_organization(
                    db, organization_id
                )
                if local_subscription:
                    local_subscription.plan_name = self._get_plan_name_from_price(
                        subscription_data["items"]["data"][0]["price"]["id"]
                    )
                    local_subscription.status = subscription_data["status"]
                    local_subscription.cancel_at_period_end = subscription_data.get(
                        "cancel_at_period_end", False
                    )
                    db.commit()
            finally:
                db.close()

    def _handle_subscription_deleted(self, subscription_data: Dict[str, Any]):
        """
        Handle subscription deleted event
        """
        organization_id = int(
            subscription_data.get("metadata", {}).get("organization_id", 0)
        )
        if organization_id:
            db = next(get_db())
            try:
                local_subscription = get_subscription_by_organization(
                    db, organization_id
                )
                if local_subscription:
                    local_subscription.status = "canceled"
                    db.commit()
            finally:
                db.close()

    def _handle_payment_succeeded(self, invoice_data: Dict[str, Any]):
        """
        Handle payment succeeded event
        """
        # Update invoice status in database
        pass

    def _handle_payment_failed(self, invoice_data: Dict[str, Any]):
        """
        Handle payment failed event
        """
        # Update invoice status and notify organization
        pass

    def record_usage(
        self, organization_id: int, action: str, quantity: int = 1
    ) -> bool:
        """
        Record usage for metered billing
        """
        try:
            db = next(get_db())
            try:
                # Create usage record in our database
                create_usage_record(db, organization_id, action, quantity)

                # Get organization to check if they have a subscription
                organization = get_organization(db, organization_id)
                if not organization or not organization.stripe_customer_id:
                    logger.warning(
                        f"No Stripe customer found for organization {organization_id}"
                    )
                    return True  # Still record internally even if Stripe fails

                # In a real implementation, you would record usage with Stripe
                # for metered billing plans
                # stripe.SubscriptionItem.create_usage_record(
                #     subscription_item_id="si_...",
                #     quantity=quantity,
                #     timestamp=int(datetime.now().timestamp()),
                #     action='increment'
                # )

                logger.info(
                    f"Recorded usage for organization {organization_id}: {action} x{quantity}"
                )
                return True
            finally:
                db.close()
        except Exception as e:
            logger.error(
                f"Failed to record usage for organization {organization_id}: {str(e)}"
            )
            return False

    def get_billing_portal_url(self, organization_id: int) -> Optional[str]:
        """
        Generate a billing portal URL for an organization
        """
        db = next(get_db())
        try:
            organization = get_organization(db, organization_id)
            if not organization or not organization.stripe_customer_id:
                logger.error(
                    f"No Stripe customer found for organization {organization_id}"
                )
                return None

            session = stripe.billing_portal.Session.create(
                customer=organization.stripe_customer_id,
                return_url=f"{os.getenv('FRONTEND_URL', 'http://localhost:5173')}/billing",
            )

            return session.url
        except Exception as e:
            logger.error(
                f"Failed to create billing portal session for organization {organization_id}: {str(e)}"
            )
            return None
        finally:
            db.close()

    def _get_plan_name_from_id(self, plan_id: str) -> str:
        """
        Get plan name from Stripe price/plan ID
        """
        plan_mapping = {
            "price_123": "starter",
            "price_456": "pro",
            "price_789": "enterprise",
        }
        return plan_mapping.get(plan_id, "unknown")

    def _get_plan_name_from_price(self, price_id: str) -> str:
        """
        Get plan name from Stripe price ID
        """
        return self._get_plan_name_from_id(price_id)


# Global instance
stripe_service = StripeService()
