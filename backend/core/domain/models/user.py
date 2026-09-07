"""
User model for the LeadBoost SaaS platform
"""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from core.infrastructure.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)

    # P1.2 (Sender Profile): minimal extension of the existing User row.
    # Sender identity belongs to the user, not the organization -- the
    # existing outreach path (application/context/context_builder.py's
    # `sender_org`, core/infrastructure/messaging/messenger.py) already
    # signs messages off with only the organization's name because no
    # per-person sender identity existed anywhere in this codebase; these
    # two fields are the minimal identity data actually named in the P1.2
    # brief (title/signature). No new table: User already carries the
    # user's personal identity fields (first_name/last_name/email) that
    # these extend, exactly like Organization already carrying
    # name/description before industry/icp_description were added to it.
    # Wiring these into the Messaging Agent/outreach templates is left for
    # a later phase -- P1.2 only adds the storage + profile UI.
    job_title = Column(String, nullable=True)
    signature = Column(String, nullable=True)

    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    organization = relationship("Organization", back_populates="users")
    leads = relationship("Lead", back_populates="owner")
    api_keys = relationship("APIKey", back_populates="user")
