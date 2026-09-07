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

    # P1.2 (Company Profile): minimal extension of the existing Organization
    # row rather than a separate CompanyProfile table -- name/description
    # already lived here, and these two fields are the only additional
    # profile data actually justified by current product behavior (no
    # agent/prompt in this codebase consumes an organization-defined ICP
    # today; see application/agents/company_intelligence_agent.py and
    # application/prompts/templates/company_intelligence_v1.yaml, where
    # icp_alignment_score is evidence-completeness, not a comparison
    # against org-provided text). These are profile/configuration data
    # only -- reading them into a prompt or scoring formula is explicitly
    # out of scope for P1.2.
    industry = Column(String, nullable=True)
    icp_description = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    users = relationship("User", back_populates="organization")
    leads = relationship("Lead", back_populates="organization")
    api_keys = relationship("APIKey", back_populates="organization")
    subscription = relationship(
        "Subscription", back_populates="organization", uselist=False
    )
    # P1.2: organization-scoped qualification policy (see
    # core/domain/models/qualification_settings.py). 1:1, same pattern as
    # `subscription` above.
    qualification_settings = relationship(
        "OrganizationQualificationSettings", back_populates="organization", uselist=False
    )
