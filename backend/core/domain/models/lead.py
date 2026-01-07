"""
Lead model for the LeadBoost SaaS platform
"""

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Boolean,
    Float,
    ForeignKey,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from core.infrastructure.database import Base


class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Core company information
    company_name = Column(String, nullable=True)
    website = Column(String, nullable=False)
    industry = Column(String, nullable=True)
    about_text = Column(Text, nullable=True)

    # Contact information
    contact_name = Column(String, nullable=True)
    contact_title = Column(String, nullable=True)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    address = Column(String, nullable=True)

    # Social profiles
    linkedin_url = Column(String, nullable=True)
    twitter_url = Column(String, nullable=True)
    facebook_url = Column(String, nullable=True)

    # Company metrics
    employees = Column(String, nullable=True)  # e.g., "1-10", "11-50", etc.
    revenue_band = Column(String, nullable=True)  # e.g., "$0-1M", "$1M-10M", etc.
    founded_year = Column(Integer, nullable=True)

    # Lead scoring
    score = Column(Float, default=0.0)
    qualification_label = Column(String, default="Low Priority")

    # Data quality metrics
    scrape_confidence = Column(Float, default=0.0)
    email_confidence = Column(Float, default=0.0)
    enrichment_confidence = Column(Float, default=0.0)

    # Sources and metadata
    enrichment_source = Column(String, default="none")  # heuristic, llm, external_api
    email_source = Column(String, default="none")
    scrape_source = Column(String, default="none")

    # Outreach
    outreach_message = Column(Text, nullable=True)
    outreach_sent = Column(Boolean, default=False)
    outreach_sent_at = Column(DateTime(timezone=True), nullable=True)

    # Status and tracking
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    organization = relationship("Organization", back_populates="leads")
    owner = relationship("User", back_populates="leads")
    enrichment_logs = relationship("LeadEnrichmentLog", back_populates="lead")
    scraping_logs = relationship("ScrapingLog", back_populates="lead")


class LeadEnrichmentLog(Base):
    __tablename__ = "lead_enrichment_logs"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    enrichment_type = Column(String, nullable=False)  # heuristic, llm, external_api
    enrichment_data = Column(Text)  # JSON string of enrichment results
    confidence_score = Column(Float, default=0.0)
    processing_time_ms = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationship
    lead = relationship("Lead", back_populates="enrichment_logs")


class ScrapingLog(Base):
    __tablename__ = "scraping_logs"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    scraping_method = Column(
        String, nullable=False
    )  # json_ld, structured_data, playwright, etc.
    success = Column(Boolean, default=False)
    error_message = Column(Text, nullable=True)
    confidence_score = Column(Float, default=0.0)
    processing_time_ms = Column(Integer)
    scraped_data = Column(Text)  # JSON string of scraped data
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationship
    lead = relationship("Lead", back_populates="scraping_logs")
