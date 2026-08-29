"""
Billing models for the LeadBoost SaaS platform
"""

from sqlalchemy import Column, Integer, String, Boolean, Date, DateTime, Float, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from core.infrastructure.database import Base


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    stripe_subscription_id = Column(String, unique=True, nullable=False)
    plan_name = Column(String, nullable=False)  # e.g., "pro", "enterprise"
    status = Column(String, default="active")  # active, canceled, past_due, unpaid
    current_period_start = Column(DateTime(timezone=True))
    current_period_end = Column(DateTime(timezone=True))
    cancel_at_period_end = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    organization = relationship("Organization", back_populates="subscription")


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    action = Column(
        String, nullable=False
    )  # e.g., "lead_scraped", "enrichment_performed"
    quantity = Column(Integer, default=1)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    organization = relationship("Organization")


class DailyLeadQuotaUsage(Base):
    """Atomic, per-organization-per-day lead-creation counter.

    Additive-only table (production-robustness hardening, Phase A5).

    This is NOT a replacement for SubscriptionService's existing
    `_get_daily_usage()` (a `COUNT(*)` over `Lead` rows) -- that remains
    the single source of truth for *display* purposes (remaining-quota
    figures shown to the user, plan-usage reporting) and is completely
    unchanged by this table.

    This table exists purely as a concurrency-safety primitive: it lets
    lead-creation call sites (api/endpoints/leads.py,
    application/discovery/discovery_service.py) atomically *reserve*
    quota with a single conditional `UPDATE ... WHERE leads_reserved +
    :n <= :max_per_day` statement (see
    core.infrastructure.billing.quota_guard.reserve_daily_lead_quota).
    A single UPDATE's WHERE-clause evaluation and SET are applied
    atomically by the database engine itself -- this holds identically
    on SQLite and PostgreSQL without relying on `SELECT ... FOR UPDATE`
    dialect differences, closing the check-then-act race where two
    concurrent requests could both read "quota available" from a stale
    COUNT(*) before either commits its new Lead row.
    """

    __tablename__ = "daily_lead_quota_usage"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    usage_date = Column(Date, nullable=False, index=True)
    leads_reserved = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("organization_id", "usage_date", name="uq_daily_lead_quota_org_date"),
    )


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    stripe_invoice_id = Column(String, unique=True, nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String, default="usd")
    status = Column(String, default="draft")  # draft, open, paid, void, uncollectible
    invoice_pdf = Column(String, nullable=True)  # URL to the PDF invoice
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    due_date = Column(DateTime(timezone=True))

    # Relationships
    organization = relationship("Organization")
