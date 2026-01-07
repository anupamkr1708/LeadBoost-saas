"""
API Key model for the LeadBoost SaaS platform
"""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from core.infrastructure.database import Base
import secrets


class APIKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, index=True)
    key_hash = Column(String, unique=True, nullable=False)  # Hash of the API key
    name = Column(String, nullable=False)  # Name/description of the key
    key_prefix = Column(
        String, nullable=False
    )  # First few characters for identification
    organization_id = Column(Integer, ForeignKey("organizations.id"))
    user_id = Column(Integer, ForeignKey("users.id"))
    is_active = Column(Boolean, default=True)
    is_revoked = Column(Boolean, default=False)
    rate_limit = Column(Integer, default=100)  # Requests per minute
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    organization = relationship("Organization", back_populates="api_keys")
    user = relationship("User", back_populates="api_keys")

    def generate_key(self) -> str:
        """Generate a new API key"""
        key = f"lb_{secrets.token_urlsafe(32)}"
        self.key_prefix = key[:8]  # Store first 8 chars for identification
        return key
