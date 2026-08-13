"""
Tests for core.infrastructure.billing.subscription_service.SubscriptionService.
"""

# Ensures the "users" table is registered on Base.metadata even if this
# file is the only one collected in a given pytest run (User isn't
# otherwise imported by anything SubscriptionService touches, but Lead's
# owner_id foreign key still points at it).
from core.domain.models.user import User  # noqa: F401
from core.infrastructure.billing.subscription_service import SubscriptionService


def test_free_plan_default_daily_limit_is_50(db_session, sample_org, monkeypatch):
    """PART 8 of the brief: Free plan should be 50 leads/day, not the
    previous default of 10."""
    monkeypatch.delenv("FREE_MAX_LEADS_PER_DAY", raising=False)

    service = SubscriptionService(db_session)
    usage = service.get_organization_usage(sample_org.id)

    assert usage.plan_name == "free"
    assert usage.max_leads_per_day == 50


def test_pro_plan_default_daily_limit_is_500(db_session, sample_org, monkeypatch):
    monkeypatch.delenv("PRO_MAX_LEADS_PER_DAY", raising=False)
    service = SubscriptionService(db_session)
    service.assign_plan_to_organization(sample_org.id, "pro")

    usage = service.get_organization_usage(sample_org.id)
    assert usage.max_leads_per_day == 500


def test_enterprise_plan_default_daily_limit_is_10000(db_session, sample_org, monkeypatch):
    monkeypatch.delenv("ENTERPRISE_MAX_LEADS_PER_DAY", raising=False)
    service = SubscriptionService(db_session)
    service.assign_plan_to_organization(sample_org.id, "enterprise")

    usage = service.get_organization_usage(sample_org.id)
    assert usage.max_leads_per_day == 10000


def test_canceled_subscription_reverts_to_free_tier_limits(db_session, sample_org, monkeypatch):
    """A canceled Pro/Enterprise subscription must not leave an
    organization permanently on paid-tier limits -- only `status`
    changes on cancellation (plan_name is left alone for billing
    history), so usage/feature checks must treat "canceled" as
    effectively free."""
    monkeypatch.delenv("FREE_MAX_LEADS_PER_DAY", raising=False)
    monkeypatch.setenv("CAN_USE_AI_ENTERPRISE", "true")
    monkeypatch.setenv("CAN_USE_AI_FREE", "false")
    # This test's whole point is asserting can_use_ai_features() is False
    # on the free tier, which the local-dev AI bypass (see
    # SubscriptionService.can_use_ai_features) is explicitly designed to
    # override. conftest.py doesn't otherwise touch this var, so it was
    # silently inheriting whatever ENABLE_AI_FOR_ALL_PLANS happens to be
    # set to in the ambient environment/.env pytest is run from -- a real
    # false positive/negative risk, unrelated to what this test is
    # actually meant to verify. monkeypatch.setenv always wins over both
    # an exported shell var and a `.env`-loaded one, and reverts
    # automatically after the test.
    monkeypatch.setenv("ENABLE_AI_FOR_ALL_PLANS", "false")

    service = SubscriptionService(db_session)
    service.assign_plan_to_organization(sample_org.id, "enterprise")
    assert service.can_use_ai_features(sample_org.id) is True

    service.cancel_subscription(sample_org.id, immediate=True)

    usage = service.get_organization_usage(sample_org.id)
    assert usage.plan_name == "free"
    assert usage.max_leads_per_day == 50
    assert service.can_use_ai_features(sample_org.id) is False


def test_cancel_at_period_end_keeps_plan_active_until_then(db_session, sample_org, monkeypatch):
    """Non-immediate cancellation should NOT instantly drop the org to
    free -- only immediate cancellation does."""
    service = SubscriptionService(db_session)
    service.assign_plan_to_organization(sample_org.id, "pro")

    service.cancel_subscription(sample_org.id, immediate=False)

    usage = service.get_organization_usage(sample_org.id)
    assert usage.plan_name == "pro"


def test_organization_with_no_subscription_defaults_to_free(db_session, sample_org):
    service = SubscriptionService(db_session)
    usage = service.get_organization_usage(sample_org.id)
    assert usage.plan_name == "free"


def test_assign_plan_rejects_invalid_plan_name(db_session, sample_org):
    service = SubscriptionService(db_session)
    result = service.assign_plan_to_organization(sample_org.id, "super-deluxe")
    assert result is False
