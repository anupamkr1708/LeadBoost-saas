"""
Organization model for the LeadBoost SaaS platform
"""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from core.infrastructure.database import Base


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    plan_tier = Column(String, default="free")  # free, pro, enterprise
    max_users = Column(Integer, default=1)
    max_leads = Column(Integer, default=100)
    usage_count = Column(Integer, default=0)
    stripe_customer_id = Column(String, nullable=True)  # For billing
    stripe_subscription_id = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    users = relationship("User", back_populates="organization")
    leads = relationship("Lead", back_populates="organization")
    api_keys = relationship("APIKey", back_populates="organization")
    subscription = relationship(
        "Subscription", back_populates="organization", uselist=False
    )
