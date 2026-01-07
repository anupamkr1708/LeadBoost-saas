"""
Billing models for the LeadBoost SaaS platform
"""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, ForeignKey
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
